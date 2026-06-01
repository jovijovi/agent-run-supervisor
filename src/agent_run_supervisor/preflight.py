"""Environment preflight probes for `doctor`.

All probes are **read-only**: they only run `--version`-style checks and pure
local computation. They never launch a real AGENT, never run `acpx exec`, never
send a session prompt, and never trigger an `npx` package fetch. Probes return
structured dicts (never raise) so the CLI serializes them deterministically even
when a binary is missing.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent_run_supervisor import policy as _policy
from agent_run_supervisor import redaction as _redaction
from agent_run_supervisor import workspace as _workspace
from agent_run_supervisor.event_store import (
    DIR_MODE,
    FILE_MODE,
    exclusive_create_bytes,
    secure_mkdir,
)
from agent_run_supervisor.session import SESSION_JSON, SessionStore
from agent_run_supervisor.workspace import WorkspaceValidationResult

NODE_MIN_VERSION = "22.12.0"
ACPX_EXPECTED_VERSION = "0.10.0"
_PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ProbeRun:
    """Outcome of a single `--version` probe invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


ProbeRunner = Callable[[list[str]], ProbeRun]


def _default_runner(argv: list[str]) -> ProbeRun:
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    return ProbeRun(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def probe_node(
    *,
    binary: str | None = None,
    runner: ProbeRunner | None = None,
) -> dict:
    resolved_binary = binary or "node"
    return _run_version_probe(
        binary=resolved_binary,
        runner=runner,
        version_parser=_parse_node_version,
        ok_predicate=lambda version: _meets_minimum(version, NODE_MIN_VERSION),
        base_payload={
            "binary": "node",
            "requirement_minimum": NODE_MIN_VERSION,
        },
        mismatch_detail=lambda version: (
            f"node {version} is below required minimum {NODE_MIN_VERSION}"
        ),
        parse_failed_detail="could not parse node --version output",
    )


def probe_acpx(
    *,
    binary: str | None = None,
    runner: ProbeRunner | None = None,
) -> dict:
    resolved_binary = binary or "acpx"
    return _run_version_probe(
        binary=resolved_binary,
        runner=runner,
        version_parser=_parse_acpx_version,
        ok_predicate=lambda version: version == ACPX_EXPECTED_VERSION,
        base_payload={
            "binary": resolved_binary,
            "expected_version": ACPX_EXPECTED_VERSION,
        },
        mismatch_detail=lambda version: (
            f"acpx {version} does not match expected {ACPX_EXPECTED_VERSION}"
        ),
        parse_failed_detail="could not parse acpx --version output",
    )


def _run_version_probe(
    *,
    binary: str,
    runner: ProbeRunner | None,
    version_parser: Callable[[str], str | None],
    ok_predicate: Callable[[str], bool],
    base_payload: dict,
    mismatch_detail: Callable[[str], str],
    parse_failed_detail: str,
) -> dict:
    payload = {
        **base_payload,
        "available": False,
        "version": None,
        "ok": False,
        "error_detail": None,
    }

    runner = runner or _default_runner
    argv = [binary, "--version"]

    try:
        outcome = runner(argv)
    except FileNotFoundError:
        payload["error_detail"] = f"binary not found on PATH: {binary}"
        return payload
    except subprocess.TimeoutExpired:
        payload["error_detail"] = f"version probe timed out for {binary}"
        return payload
    except OSError as exc:
        payload["error_detail"] = f"could not invoke {binary}: {exc.__class__.__name__}"
        return payload

    if outcome.returncode != 0:
        payload["error_detail"] = (
            f"{binary} --version exited with code {outcome.returncode}"
        )
        return payload

    payload["available"] = True
    version = version_parser(outcome.stdout)
    if version is None:
        payload["error_detail"] = parse_failed_detail
        return payload

    payload["version"] = version
    if not ok_predicate(version):
        payload["error_detail"] = mismatch_detail(version)
        return payload

    payload["ok"] = True
    return payload


def _parse_node_version(stdout: str) -> str | None:
    token = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
    if not token:
        return None
    if token.startswith("v") or token.startswith("V"):
        token = token[1:]
    if not token or not token[0].isdigit():
        return None
    parts = token.split(".")
    if len(parts) < 1:
        return None
    for part in parts[:3]:
        digits = part.split("-", 1)[0]
        if not digits.isdigit():
            return None
    return token


def _parse_acpx_version(stdout: str) -> str | None:
    token = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
    if not token:
        return None
    if token.lower().startswith("acpx"):
        token = token[4:].strip()
    if token.startswith("v"):
        token = token[1:]
    if not token or not token[0].isdigit():
        return None
    if not all(part.split("-", 1)[0].isdigit() for part in token.split(".")):
        return None
    return token


def _meets_minimum(version: str, minimum: str) -> bool:
    return _version_tuple(version) >= _version_tuple(minimum)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = version.split("-", 1)[0].split(".")
    out: list[int] = []
    for part in parts:
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def discover_node_binary() -> str | None:
    return shutil.which("node")


def discover_acpx_binary() -> str | None:
    return shutil.which("acpx")


# --- W1 read-only doctor probes ------------------------------------------
#
# Each probe returns a structured dict and never raises for ordinary missing
# binaries or invalid roles: a doctor probe fails closed (``ok False`` with an
# ``error_detail``) instead of propagating. None of these launch a real AGENT,
# run ``acpx exec``, send a session prompt, or trigger an ``npx`` package fetch.


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _which_or_path(binary: str) -> str | None:
    """Resolve a binary read-only: an absolute path must exist; else PATH lookup."""
    if os.path.isabs(binary):
        return binary if os.path.exists(binary) else None
    return shutil.which(binary)


def probe_policy(role) -> dict:
    """Compile the role's permission policy locally and confirm default-deny.

    Pure/local: never spawns a subprocess and never launches acpx. Fails closed
    (``ok False`` with ``error_detail``) if compilation raises.
    """
    payload = {
        "ok": False,
        "parseable": False,
        "default_action": None,
        "auto_approve_count": None,
        "auto_deny_count": None,
        "policy_hash": None,
        "error_detail": None,
    }
    try:
        compiled = _policy.compile_permission_policy(role)
        json.dumps(compiled)  # confirm it serializes deterministically
        default_action = compiled.get("defaultAction")
        auto_approve = compiled.get("autoApprove", [])
        auto_deny = compiled.get("autoDeny", [])
        hashed = _policy.policy_hash(role)
    except Exception as exc:  # fail closed: a doctor probe never raises
        payload["error_detail"] = f"{exc.__class__.__name__}: {exc}"
        return payload
    payload["parseable"] = True
    payload["default_action"] = default_action
    payload["auto_approve_count"] = len(auto_approve)
    payload["auto_deny_count"] = len(auto_deny)
    payload["policy_hash"] = hashed
    payload["ok"] = default_action == "deny"
    return payload


def probe_workspace(role, *, cwd: str | None = None) -> dict:
    """Validate the effective cwd against allowed_roots. Pure/local.

    Always reports the not-a-sandbox disclaimer and never claims a security
    boundary. ``ok False`` with ``error_detail`` when the cwd is outside roots.
    """
    payload = {
        "ok": False,
        "effective_cwd": None,
        "matched_root": None,
        "allowed_roots_security_boundary": False,
        "disclaimer": _workspace.ALLOWED_ROOTS_DISCLAIMER,
        "error_detail": None,
    }
    try:
        result = _workspace.validate_effective_cwd(role, cwd)
    except _workspace.WorkspaceValidationError as exc:
        payload["error_detail"] = str(exc)
        return payload
    payload["ok"] = True
    payload["effective_cwd"] = str(result.effective_cwd)
    payload["matched_root"] = (
        str(result.matched_root) if result.matched_root is not None else None
    )
    payload["allowed_roots_security_boundary"] = result.allowed_roots_security_boundary
    payload["disclaimer"] = result.disclaimer
    return payload


# Synthetic, non-secret, pattern-shaped fakes — never real credentials. Each is
# crafted to match a redaction pattern so re-running redaction over it proves the
# pattern fires. See `redaction._PATTERNS`.
_REDACTION_SAMPLES = {
    "openai_api_key": "sk-" + "A" * 24,
    "bearer_token": "Authorization: Bearer FAKE-" + "B" * 12,
    "jwt": "eyJ" + "C" * 8 + "." + "D" * 8 + "." + "E" * 8,
    "pem_private_key": "-----BEGIN RSA PRIVATE KEY-----",
}


def probe_redaction() -> dict:
    """Run synthetic, non-secret pattern-shaped samples through redaction.

    Pure/local: the samples are fabricated fakes (never real credentials). Each
    is pushed through ``redact_text``/``redact_env``/``redact_argv`` and must not
    survive. ``leaked`` is empty on success.
    """
    payload = {
        "ok": False,
        "patterns_exercised": sorted(_REDACTION_SAMPLES.keys()),
        "leaked": [],
        "error_detail": None,
    }
    leaked: list[str] = []
    try:
        for name, sample in _REDACTION_SAMPLES.items():
            redacted_text, _ = _redaction.redact_text(sample)
            if sample in redacted_text:
                leaked.append(f"text:{name}")
            redacted_env, _ = _redaction.redact_env({"PROBE_VALUE": sample})
            if sample in json.dumps(redacted_env):
                leaked.append(f"env:{name}")
            redacted_argv, _ = _redaction.redact_argv([sample])
            if any(sample in token for token in redacted_argv):
                leaked.append(f"argv:{name}")
    except Exception as exc:  # fail closed
        payload["error_detail"] = f"{exc.__class__.__name__}: {exc}"
        return payload
    payload["leaked"] = sorted(leaked)
    payload["ok"] = payload["leaked"] == []
    return payload


def probe_npx(role, *, runner: ProbeRunner | None = None) -> dict:
    """Report runtime acpx fetch risk read-only. Never fetches/execs acpx.

    With an explicit ``acpx_binary`` there is no ``npx`` fetch path
    (``fetch_risk False``) and npx is never consulted. Otherwise the compiler
    resolves ``npx -y acpx@<version>`` at run time (``fetch_risk True``); this
    probe only runs ``npx --version`` (read-only) and never ``npx acpx``.
    """
    acpx_binary = role.runner.acpx_binary
    payload = {
        "ok": False,
        "fetch_risk": acpx_binary is None,
        "npx_available": None,
        "pinned_spec": f"acpx@{role.runner.acpx_version}",
        "acpx_binary": acpx_binary,
        "error_detail": None,
    }
    if acpx_binary is not None:
        resolved = _which_or_path(acpx_binary)
        payload["ok"] = resolved is not None
        if resolved is None:
            payload["error_detail"] = f"acpx_binary not found: {acpx_binary}"
        return payload

    runner = runner or _default_runner
    argv = ["npx", "--version"]
    try:
        outcome = runner(argv)
    except FileNotFoundError:
        payload["npx_available"] = False
        payload["error_detail"] = "binary not found on PATH: npx"
        return payload
    except subprocess.TimeoutExpired:
        payload["npx_available"] = False
        payload["error_detail"] = "version probe timed out for npx"
        return payload
    except OSError as exc:
        payload["npx_available"] = False
        payload["error_detail"] = f"could not invoke npx: {exc.__class__.__name__}"
        return payload
    payload["npx_available"] = outcome.returncode == 0
    if outcome.returncode != 0:
        payload["error_detail"] = f"npx --version exited with code {outcome.returncode}"
    payload["ok"] = payload["npx_available"]
    return payload


def probe_adapter(
    role,
    *,
    acpx_runner: ProbeRunner | None = None,
    npx_runner: ProbeRunner | None = None,
) -> dict:
    """Report adapter availability as *declared + hostable*. Read-only.

    acpx hosts the adapter and no fixture-proven read-only adapter enumeration
    command exists, so this never launches the adapter/agent. "Hostable" means
    the hosting acpx resolves read-only (via the same acpx-binary/npx resolution
    as ``probe_acpx``/``probe_npx``).
    """
    adapter_agent = role.runner.adapter_agent
    declared = bool(adapter_agent)
    detail = (
        "adapter availability is declared + hostable only: the adapter process "
        "is NOT launched and no acpx exec / adapter subcommand runs. acpx hosts "
        "the adapter; hostability checks that the acpx host resolves read-only."
    )
    if role.runner.acpx_binary:
        host = probe_acpx(binary=role.runner.acpx_binary, runner=acpx_runner)
        hostable = bool(host["available"])
    else:
        host = probe_npx(role, runner=npx_runner)
        hostable = bool(host["npx_available"])
    return {
        "ok": declared and hostable,
        "adapter_agent": adapter_agent or None,
        "declared": declared,
        "hostable": hostable,
        "detail": detail,
    }


def probe_session_readiness(
    role=None,
    *,
    sessions_dir: Path | None = None,
    now: _dt.datetime | None = None,
) -> dict:
    """Verify the session store can create 0700 dirs / 0600 files. Read-only.

    Mode checks always run against a throwaway ``tempfile`` store (never the real
    sessions root). When a persistent role is supplied the lease length is
    reported. When ``sessions_dir`` is given, the W4 stale-lock detector reports
    lock/lease/temp-debris state read-only (no lock taken, nothing removed, no
    acpx launch).
    """
    payload = {
        "ok": False,
        "dir_mode_ok": False,
        "file_mode_ok": False,
        "lease_seconds": None,
        "stale_locks": [],
        "error_detail": None,
    }
    if role is not None and role.session.strategy == "persistent":
        payload["lease_seconds"] = role.session.lease_seconds
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dir_ok, file_ok = _session_mode_probe(Path(tmp), role)
        payload["dir_mode_ok"] = dir_ok
        payload["file_mode_ok"] = file_ok
        if sessions_dir is not None:
            store = SessionStore(base_dir=Path(sessions_dir))
            payload["stale_locks"] = store.detect_stale_locks(now=now)
    except Exception as exc:  # fail closed
        payload["error_detail"] = f"{exc.__class__.__name__}: {exc}"
        return payload
    payload["ok"] = payload["dir_mode_ok"] and payload["file_mode_ok"]
    return payload


def _session_mode_probe(tmp_dir: Path, role) -> tuple[bool, bool]:
    """Create a synthetic session store under ``tmp_dir`` and check artifact modes.

    A persistent role exercises the real ``SessionStore.create_session`` path; any
    other case (role-less or exec role) just asserts the sessions root + a probe
    file get the secure 0700/0600 modes. Synthetic only — never the live root.
    """
    sessions_root = tmp_dir / "sessions"
    if role is not None and role.session.strategy == "persistent":
        store = SessionStore(base_dir=sessions_root)
        workspace_result = _workspace.validate_effective_cwd(role, None)
        store.create_session(
            session_id="probe-session",
            role=role,
            workspace_result=workspace_result,
        )
        session_dir = sessions_root / "probe-session"
        return (
            _mode(session_dir) == DIR_MODE,
            _mode(session_dir / SESSION_JSON) == FILE_MODE,
        )
    secure_mkdir(sessions_root)
    probe_file = sessions_root / "probe.json"
    exclusive_create_bytes(probe_file, b"{}")
    return _mode(sessions_root) == DIR_MODE, _mode(probe_file) == FILE_MODE

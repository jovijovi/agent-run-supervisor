from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor import policy as policy_module
from agent_run_supervisor import preflight as preflight_module
from agent_run_supervisor.preflight import (
    ProbeRun,
    discover_acpx_binary,
    discover_node_binary,
    probe_acpx,
    probe_adapter,
    probe_node,
    probe_npx,
    probe_policy,
    probe_redaction,
    probe_session_readiness,
    probe_workspace,
)
from agent_run_supervisor.role import PERMISSION_KINDS, load_role
from agent_run_supervisor.session import SessionStore

UTC = timezone.utc
T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _runner(returncode: int, stdout: str = "", stderr: str = ""):
    def _fake(argv):
        assert isinstance(argv, list)
        return ProbeRun(returncode=returncode, stdout=stdout, stderr=stderr)

    return _fake


def _raising_runner(exc: Exception):
    def _fake(argv):
        raise exc

    return _fake


def _recording_runner(returncode: int = 0, stdout: str = "", stderr: str = ""):
    seen: list[list[str]] = []

    def _fake(argv):
        seen.append(list(argv))
        return ProbeRun(returncode=returncode, stdout=stdout, stderr=stderr)

    return _fake, seen


def _role(valid_role_dict: dict[str, Any], work: Path | None = None, **overrides: Any):
    payload = {**valid_role_dict}
    payload["runner"] = {**valid_role_dict["runner"]}
    payload["workspace"] = {**valid_role_dict["workspace"]}
    if work is not None:
        payload["workspace"]["default_cwd"] = str(work)
        payload["workspace"]["allowed_roots"] = [str(work)]
    for key, value in overrides.items():
        payload[key] = value
    return load_role(payload)


def test_probe_node_reports_version_when_runner_succeeds() -> None:
    result = probe_node(runner=_runner(0, stdout="v22.13.0\n"))

    assert result["binary"] == "node"
    assert result["available"] is True
    assert result["version"] == "22.13.0"
    assert result["requirement_minimum"] == "22.12.0"
    assert result["ok"] is True
    assert result["error_detail"] is None


def test_probe_node_below_minimum_is_not_ok() -> None:
    result = probe_node(runner=_runner(0, stdout="v18.0.0\n"))

    assert result["available"] is True
    assert result["version"] == "18.0.0"
    assert result["ok"] is False
    assert result["error_detail"]


def test_probe_node_when_binary_missing_is_unavailable_not_raising() -> None:
    result = probe_node(runner=_raising_runner(FileNotFoundError("node")))

    assert result["available"] is False
    assert result["version"] is None
    assert result["ok"] is False
    assert "not found" in result["error_detail"].lower()


def test_probe_node_with_unparseable_output_marks_not_ok() -> None:
    result = probe_node(runner=_runner(0, stdout="garbage\n"))

    assert result["available"] is True
    assert result["version"] is None
    assert result["ok"] is False
    assert result["error_detail"]


def test_probe_node_nonzero_exit_is_unavailable() -> None:
    result = probe_node(runner=_runner(127, stdout="", stderr="not found"))

    assert result["available"] is False
    assert result["ok"] is False
    assert result["version"] is None


def test_probe_acpx_reports_version_when_runner_succeeds() -> None:
    result = probe_acpx(runner=_runner(0, stdout="0.12.0\n"))

    assert result["binary"] == "acpx"
    assert result["available"] is True
    assert result["version"] == "0.12.0"
    assert result["expected_version"] == "0.12.0"
    assert result["ok"] is True
    assert result["error_detail"] is None


def test_probe_acpx_missing_binary_is_unavailable_not_ok() -> None:
    result = probe_acpx(runner=_raising_runner(FileNotFoundError("acpx")))

    assert result["available"] is False
    assert result["ok"] is False
    assert result["version"] is None
    assert "not found" in result["error_detail"].lower()


def test_probe_acpx_version_mismatch_fails_ok() -> None:
    result = probe_acpx(runner=_runner(0, stdout="0.9.5\n"))

    assert result["available"] is True
    assert result["version"] == "0.9.5"
    assert result["expected_version"] == "0.12.0"
    assert result["ok"] is False
    assert result["error_detail"]


def test_probe_acpx_does_not_launch_acpx_exec() -> None:
    seen: list[list[str]] = []

    def _record(argv):
        seen.append(list(argv))
        return ProbeRun(returncode=0, stdout="0.12.0\n", stderr="")

    probe_acpx(runner=_record)

    assert seen, "runner not invoked"
    for argv in seen:
        assert "exec" not in argv
        assert argv[-1] == "--version"


def test_probe_acpx_honors_explicit_binary_override() -> None:
    seen: list[list[str]] = []

    def _record(argv):
        seen.append(list(argv))
        return ProbeRun(returncode=0, stdout="0.12.0\n", stderr="")

    probe_acpx(binary="/opt/acpx/bin/acpx", runner=_record)

    assert seen[0][0] == "/opt/acpx/bin/acpx"


def test_probe_node_timeout_is_unavailable_not_raising() -> None:
    result = probe_node(
        runner=_raising_runner(subprocess.TimeoutExpired(cmd="node", timeout=1.0)),
    )

    assert result["available"] is False
    assert result["ok"] is False
    assert result["version"] is None
    assert result["error_detail"]


# --- W1 probe_policy (pure-local; never launches acpx) --------------------


def test_probe_policy_exec_role_is_ok_with_default_deny(valid_role_dict) -> None:
    result = probe_policy(_role(valid_role_dict))

    assert result["ok"] is True
    assert result["parseable"] is True
    assert result["default_action"] == "deny"
    # The 9 acpx-mapped permission kinds (terminal is a flag, not a policy kind).
    assert result["auto_approve_count"] + result["auto_deny_count"] == len(PERMISSION_KINDS) - 1
    assert result["policy_hash"].startswith("sha256:")
    assert result["error_detail"] is None


def test_probe_policy_persistent_role_is_ok(valid_role_dict) -> None:
    result = probe_policy(_role(valid_role_dict, session={"strategy": "persistent"}))

    assert result["ok"] is True
    assert result["default_action"] == "deny"
    assert result["policy_hash"].startswith("sha256:")


def test_probe_policy_fails_closed_when_compile_raises(valid_role_dict, monkeypatch) -> None:
    def _boom(_role_arg):
        raise RuntimeError("synthetic compile failure")

    monkeypatch.setattr(policy_module, "compile_permission_policy", _boom)

    result = probe_policy(_role(valid_role_dict))

    assert result["ok"] is False
    assert result["error_detail"]
    assert result["policy_hash"] is None


# --- W1 probe_workspace (pure-local; reports the not-a-sandbox disclaimer) -


def test_probe_workspace_ok_when_cwd_inside_root(valid_role_dict, tmp_path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    result = probe_workspace(_role(valid_role_dict, work))

    assert result["ok"] is True
    assert result["matched_root"] == str(work)
    assert result["effective_cwd"] == str(work)
    assert result["allowed_roots_security_boundary"] is False
    assert "sandbox" in result["disclaimer"].lower()
    assert result["error_detail"] is None


def test_probe_workspace_fails_when_cwd_outside_roots(valid_role_dict, tmp_path) -> None:
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    result = probe_workspace(_role(valid_role_dict, work), cwd=str(outside))

    assert result["ok"] is False
    assert result["error_detail"]
    assert "sandbox" in result["error_detail"].lower()


# --- W1 probe_redaction (synthetic samples; nothing must survive) ----------


def test_probe_redaction_reports_no_leaks() -> None:
    result = probe_redaction()

    assert result["ok"] is True
    assert result["leaked"] == []
    assert result["patterns_exercised"]
    assert result["error_detail"] is None


def test_probe_redaction_uses_only_synthetic_fakes() -> None:
    # Guard: the probe must not embed a real-looking credential; its samples are
    # pattern-shaped fakes, so re-running redaction over them never leaks.
    first = probe_redaction()
    second = probe_redaction()
    assert first["leaked"] == second["leaked"] == []


# --- W1 probe_npx (read-only; never fetches acpx) --------------------------


def test_probe_npx_explicit_binary_has_no_fetch_risk(valid_role_dict) -> None:
    runner, seen = _recording_runner(0, stdout="10.0.0\n")
    role = _role(
        valid_role_dict,
        runner={**valid_role_dict["runner"], "acpx_binary": "/opt/acpx/bin/acpx"},
    )

    result = probe_npx(role, runner=runner)

    assert result["fetch_risk"] is False
    assert result["acpx_binary"] == "/opt/acpx/bin/acpx"
    # With a pinned explicit binary, npx is never consulted.
    assert seen == []


def test_probe_npx_no_binary_reports_fetch_risk_and_pinned_spec(valid_role_dict) -> None:
    runner, seen = _recording_runner(0, stdout="10.0.0\n")
    role = _role(valid_role_dict)  # acpx_binary is None in the fixture
    assert role.runner.acpx_binary is None

    result = probe_npx(role, runner=runner)

    assert result["fetch_risk"] is True
    assert result["pinned_spec"] == "acpx@0.12.0"
    assert result["npx_available"] is True
    # Read-only: only `npx --version`, never a fetch/exec of acpx itself.
    assert seen == [["npx", "--version"]]
    for argv in seen:
        assert not any("acpx@" in token for token in argv)
        assert "exec" not in argv


def test_probe_npx_missing_npx_is_not_available(valid_role_dict) -> None:
    role = _role(valid_role_dict)
    result = probe_npx(role, runner=_raising_runner(FileNotFoundError("npx")))

    assert result["fetch_risk"] is True
    assert result["npx_available"] is False
    assert result["error_detail"]


# --- W1 probe_adapter (declared + hostable; never launches the adapter) ----


def test_probe_adapter_declared_and_hostable(valid_role_dict) -> None:
    runner, seen = _recording_runner(0, stdout="0.12.0\n")
    role = _role(
        valid_role_dict,
        runner={**valid_role_dict["runner"], "acpx_binary": "/opt/acpx/bin/acpx"},
    )

    result = probe_adapter(role, acpx_runner=runner)

    assert result["declared"] is True
    assert result["adapter_agent"] == "codex"
    assert result["hostable"] is True
    assert result["ok"] is True
    assert "not launched" in result["detail"].lower() or "not launch" in result["detail"].lower()
    # Read-only: only a --version host check, never an adapter/exec subcommand.
    for argv in seen:
        assert "exec" not in argv
        assert argv[-1] == "--version"


def test_probe_adapter_not_hostable_when_host_unresolvable(valid_role_dict) -> None:
    role = _role(valid_role_dict)  # no explicit binary -> resolves via npx
    result = probe_adapter(role, npx_runner=_raising_runner(FileNotFoundError("npx")))

    assert result["declared"] is True
    assert result["hostable"] is False
    assert result["ok"] is False


# --- W1 probe_session_readiness (temp store modes + W4 stale-lock surface) --


def test_probe_session_readiness_reports_dir_and_file_modes(valid_role_dict) -> None:
    result = probe_session_readiness()

    assert result["dir_mode_ok"] is True
    assert result["file_mode_ok"] is True
    assert result["ok"] is True
    assert result["stale_locks"] == []
    assert result["error_detail"] is None


def test_probe_session_readiness_reports_lease_for_persistent_role(valid_role_dict, tmp_path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _role(valid_role_dict, work, session={"strategy": "persistent", "lease_seconds": 1234})

    result = probe_session_readiness(role)

    assert result["ok"] is True
    assert result["lease_seconds"] == 1234


def test_probe_session_readiness_surfaces_injected_expired_lock(
    valid_role_dict, tmp_path, request
) -> None:
    # A real sessions dir with a provably expired lock is surfaced read-only.
    work = tmp_path / "work"
    work.mkdir()
    role = _role(valid_role_dict, work, session={"strategy": "persistent"})
    sessions_dir = tmp_path / "sessions"
    store = SessionStore(base_dir=sessions_dir)
    from agent_run_supervisor.workspace import validate_effective_cwd

    workspace = validate_effective_cwd(role, override=None)
    store.create_session(session_id="sess-x", role=role, workspace_result=workspace, now=T0)
    store.acquire_lock("sess-x", owner="w", now=T0, lease_seconds=10)

    result = probe_session_readiness(role, sessions_dir=sessions_dir, now=T0 + timedelta(seconds=100))

    rows = {row["session_id"]: row for row in result["stale_locks"]}
    assert rows["sess-x"]["lease_expired"] is True
    # The probe is read-only: the injected lock is still present afterwards.
    assert (sessions_dir / "sess-x" / "lock.json").exists()


def test_default_runner_invokes_subprocess(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "v1.0.0\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(preflight_module.subprocess, "run", fake_run)

    outcome = preflight_module._default_runner(["node", "--version"])

    assert captured["argv"] == ["node", "--version"]
    assert outcome.returncode == 0
    assert outcome.stdout == "v1.0.0\n"


def test_discover_binaries_use_shutil_which(monkeypatch) -> None:
    monkeypatch.setattr(preflight_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert discover_node_binary() == "/usr/bin/node"
    assert discover_acpx_binary() == "/usr/bin/acpx"


def test_version_tuple_treats_non_numeric_parts_as_zero() -> None:
    assert preflight_module._version_tuple("22.a.0") == (22, 0, 0)


def test_parse_node_version_rejects_empty_and_non_numeric_segments() -> None:
    assert preflight_module._parse_node_version("") is None
    assert preflight_module._parse_node_version("   \n") is None
    assert preflight_module._parse_node_version("22.x.0\n") is None


def test_parse_acpx_version_handles_prefix_and_malformed_tokens() -> None:
    assert preflight_module._parse_acpx_version("") is None
    assert preflight_module._parse_acpx_version("acpx v0.12.0\n") == "0.12.0"
    assert preflight_module._parse_acpx_version("acpx not-a-version\n") is None
    assert preflight_module._parse_acpx_version("0.12.x\n") is None


def test_probe_node_oserror_is_unavailable_not_raising() -> None:
    result = probe_node(runner=_raising_runner(OSError("permission denied")))

    assert result["available"] is False
    assert result["ok"] is False
    assert "OSError" in result["error_detail"]


def test_probe_acpx_oserror_and_nonzero_exit() -> None:
    os_err = probe_acpx(runner=_raising_runner(OSError("fail")))
    assert os_err["available"] is False
    assert "OSError" in os_err["error_detail"]

    nonzero = probe_acpx(runner=_runner(2, stdout="", stderr="broken"))
    assert nonzero["available"] is False
    assert "exited with code 2" in nonzero["error_detail"]


def test_probe_npx_explicit_binary_missing_path(valid_role_dict) -> None:
    role = _role(
        valid_role_dict,
        runner={**valid_role_dict["runner"], "acpx_binary": "/definitely/missing/acpx"},
    )

    result = probe_npx(role)

    assert result["fetch_risk"] is False
    assert result["ok"] is False
    assert "not found" in result["error_detail"].lower()


def test_probe_npx_timeout_oserror_and_nonzero(valid_role_dict) -> None:
    role = _role(valid_role_dict)

    timeout = probe_npx(
        role,
        runner=_raising_runner(subprocess.TimeoutExpired(cmd="npx", timeout=1.0)),
    )
    assert timeout["npx_available"] is False
    assert "timed out" in timeout["error_detail"].lower()

    os_err = probe_npx(role, runner=_raising_runner(OSError("npx blocked")))
    assert os_err["npx_available"] is False
    assert "OSError" in os_err["error_detail"]

    nonzero = probe_npx(role, runner=_runner(127, stderr="missing"))
    assert nonzero["npx_available"] is False
    assert nonzero["ok"] is False
    assert "exited with code 127" in nonzero["error_detail"]


def test_probe_redaction_fail_closed_on_internal_error(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("redaction probe failed")

    monkeypatch.setattr("agent_run_supervisor.preflight._redaction.redact_text", _boom)

    result = probe_redaction()

    assert result["ok"] is False
    assert "RuntimeError" in result["error_detail"]


def test_probe_redaction_reports_leaked_samples(monkeypatch) -> None:
    def _noop(text):
        return text, []

    monkeypatch.setattr("agent_run_supervisor.preflight._redaction.redact_text", _noop)
    monkeypatch.setattr("agent_run_supervisor.preflight._redaction.redact_env", lambda env: (env, []))
    monkeypatch.setattr(
        "agent_run_supervisor.preflight._redaction.redact_argv",
        lambda argv: (argv, []),
    )

    result = probe_redaction()

    assert result["ok"] is False
    assert result["leaked"]


def test_probe_session_readiness_fail_closed_on_store_error(tmp_path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    def _boom(self, **_kwargs):
        raise RuntimeError("stale lock scan failed")

    monkeypatch.setattr(SessionStore, "detect_stale_locks", _boom)

    result = probe_session_readiness(sessions_dir=sessions_dir)

    assert result["ok"] is False
    assert "RuntimeError" in result["error_detail"]


def test_probe_npx_explicit_binary_resolves_existing_path(valid_role_dict, tmp_path) -> None:
    acpx_path = tmp_path / "acpx"
    acpx_path.write_text("#!/bin/sh\n", encoding="utf-8")
    role = _role(
        valid_role_dict,
        runner={**valid_role_dict["runner"], "acpx_binary": str(acpx_path)},
    )

    result = probe_npx(role)

    assert result["fetch_risk"] is False
    assert result["ok"] is True
    assert result["error_detail"] is None

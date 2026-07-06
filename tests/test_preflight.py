from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor import policy as policy_module
from agent_run_supervisor.preflight import (
    ProbeRun,
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

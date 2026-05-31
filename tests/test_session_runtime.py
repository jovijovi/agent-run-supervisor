"""S1c persistent-session runtime MVP.

These tests pin the local session *runtime* slice: create/open a role-bound
session through fixture-shaped acpx management commands, send a single prompt
turn under a lease lock with redacted turn artifacts, and query status/show.
They drive a **fake executor** — no real acpx, no network, no AGENT launch —
and reuse the S1a fixtures as the proven command/stream contract.
"""
from __future__ import annotations

import copy
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from agent_run_supervisor.policy import ExecStrategyError
from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import SubprocessOutcome
from agent_run_supervisor.session import (
    SessionBindingError,
    SessionExistsError,
    SessionNotFoundError,
)
from agent_run_supervisor.session_runtime import (
    SessionRuntime,
    SessionRuntimeError,
)
from agent_run_supervisor.workspace import WorkspaceValidationError

UTC = timezone.utc
T0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 5, 31, 12, 5, 0, tzinfo=UTC)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "acpx-0.10.0"


# --- helpers --------------------------------------------------------------


def _persistent_role(valid_role_dict: dict[str, Any], work_dir: Path, **overrides: Any):
    payload = copy.deepcopy(valid_role_dict)
    payload["workspace"]["default_cwd"] = str(work_dir)
    payload["workspace"]["allowed_roots"] = [str(work_dir)]
    payload["runner"]["acpx_binary"] = None
    payload["session"] = {"strategy": "persistent"}
    for key, value in overrides.items():
        payload[key] = value
    return load_role(payload)


def _exec_role(valid_role_dict: dict[str, Any], work_dir: Path):
    payload = copy.deepcopy(valid_role_dict)
    payload["workspace"]["default_cwd"] = str(work_dir)
    payload["workspace"]["allowed_roots"] = [str(work_dir)]
    payload["session"] = {"strategy": "exec"}
    return load_role(payload)


def _fixture(name: str, filename: str) -> bytes:
    return (FIXTURES / name / filename).read_bytes()


def _new_named() -> bytes:
    return _fixture("session-new-named", "stdout.json")


def _turn1() -> bytes:
    return _fixture("session-prompt-turn1", "stdout.ndjson")


def _show_open() -> bytes:
    return _fixture("session-show-open", "stdout.json")


def _status_alive() -> bytes:
    return _fixture("session-status-after-turns", "stdout.json")


def _status_no_session() -> bytes:
    return _fixture("management-status-no-session-exit0", "stdout.json")


def _outcome(stdout: bytes = b"", *, exit_code: int = 0, stderr: bytes = b"") -> SubprocessOutcome:
    return SubprocessOutcome(exit_code=exit_code, signal=None, stdout=stdout, stderr=stderr)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class FakeExecutor:
    """Records calls and replays queued outcomes (or raises a queued error)."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        grace_ms: int,
    ) -> SubprocessOutcome:
        self.calls.append(
            {
                "argv": argv,
                "cwd": cwd,
                "env": dict(env),
                "timeout_seconds": timeout_seconds,
                "grace_ms": grace_ms,
            }
        )
        if not self.steps:
            raise AssertionError(f"unexpected executor call #{len(self.calls)}: {argv!r}")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture()
def sessions_dir(tmp_path: Path) -> Path:
    return tmp_path / "sessions"


# --- create ---------------------------------------------------------------


def test_create_invokes_fixture_management_argv_and_persists_record(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    outcome = runtime.create_session(
        role=role, session_id="sess-a", session_name="nightly", now=T0
    )

    assert len(fake.calls) == 1
    argv = fake.calls[0]["argv"]
    assert argv[-5:] == ["codex", "sessions", "new", "--name", "nightly"]
    assert all(isinstance(part, str) for part in argv)
    assert fake.calls[0]["cwd"] == work_dir

    session_json = sessions_dir / "sess-a" / "session.json"
    assert session_json.exists()
    data = json.loads(session_json.read_text(encoding="utf-8"))
    assert data["acpx_session_id"] == "019e7940-35e8-79b1-8af2-229e4e41ad4b"
    assert data["session_name"] == "nightly"
    assert data["state"] == "open"
    assert outcome.record.acpx_session_id == "019e7940-35e8-79b1-8af2-229e4e41ad4b"
    assert outcome.result["acpx_session_id"] == "019e7940-35e8-79b1-8af2-229e4e41ad4b"
    assert outcome.result["business_verdict"] is None
    assert outcome.summary["kind"] == "session_ensured"


def test_create_defaults_session_name_to_session_id(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    runtime.create_session(role=role, session_id="sess-b", now=T0)

    assert fake.calls[0]["argv"][-5:] == ["codex", "sessions", "new", "--name", "sess-b"]
    data = json.loads((sessions_dir / "sess-b" / "session.json").read_text(encoding="utf-8"))
    assert data["session_name"] == "sess-b"


def test_create_refuses_exec_role_before_executor_and_artifacts(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _exec_role(valid_role_dict, work_dir)
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(ExecStrategyError):
        runtime.create_session(role=role, session_id="sess-x", now=T0)

    assert fake.calls == []
    assert not (sessions_dir / "sess-x").exists()


def test_create_refuses_cwd_outside_allowed_roots_before_executor(
    valid_role_dict, work_dir, sessions_dir, tmp_path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(WorkspaceValidationError):
        runtime.create_session(role=role, session_id="sess-x", cwd=str(outside), now=T0)

    assert fake.calls == []
    assert not (sessions_dir / "sess-x").exists()


def test_create_fails_closed_on_management_nonzero_exit_without_persisting(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    error_stdout = _fixture("management-no-session-exit4", "stdout.ndjson")
    fake = FakeExecutor([_outcome(error_stdout, exit_code=4)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(SessionRuntimeError):
        runtime.create_session(role=role, session_id="sess-x", now=T0)

    assert not (sessions_dir / "sess-x" / "session.json").exists()


def test_create_fails_closed_on_unparseable_management_output(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(b"not json", exit_code=0)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(SessionRuntimeError):
        runtime.create_session(role=role, session_id="sess-x", now=T0)

    assert not (sessions_dir / "sess-x" / "session.json").exists()


def test_create_fails_closed_on_wrong_management_kind_without_persisting(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_status_alive(), exit_code=0)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(SessionRuntimeError):
        runtime.create_session(role=role, session_id="sess-x", now=T0)

    assert not (sessions_dir / "sess-x" / "session.json").exists()


def test_create_fails_closed_when_create_output_lacks_acpx_session_id(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(b'{"action":"session_ensured","created":true}')])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(SessionRuntimeError):
        runtime.create_session(role=role, session_id="sess-x", now=T0)

    assert not (sessions_dir / "sess-x" / "session.json").exists()


def test_create_refuses_existing_local_record_without_running_executor(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    runtime.create_session(role=role, session_id="sess-a", session_name="nightly", now=T0)

    with pytest.raises(SessionExistsError):
        runtime.create_session(role=role, session_id="sess-a", session_name="nightly", now=T0)

    # The second create must fail before spawning acpx again.
    assert len(fake.calls) == 1


# --- send -----------------------------------------------------------------


def _create_and(runtime: SessionRuntime, role, *, session_id="sess-a", session_name="nightly"):
    return runtime.create_session(
        role=role, session_id=session_id, session_name=session_name, now=T0
    )


def test_send_runs_turn_persists_redacted_artifacts_and_releases_lease(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.send(
        role=role, session_id="sess-a", prompt="contract turn 1", now=T1
    )

    send_argv = fake.calls[1]["argv"]
    assert send_argv[-5:] == ["codex", "prompt", "-s", "nightly", "contract turn 1"]
    assert outcome.result["status"] == "completed"
    assert outcome.result["final_message"] == "S1A_SESSION_TURN_1_OK"
    assert outcome.result["business_verdict"] is None
    assert outcome.result["session_id"] == "sess-a"
    assert outcome.result["turn_id"] == outcome.turn_id

    turn_dir = outcome.turn_dir
    assert turn_dir.parent == sessions_dir / "sess-a" / "turns"
    for name in (
        "prompt.txt",
        "acpx-stdout.ndjson",
        "normalized-events.jsonl",
        "result.json",
        "redaction-report.json",
    ):
        assert (turn_dir / name).exists(), name
    assert _mode(turn_dir) == 0o700
    assert _mode(turn_dir / "result.json") == 0o600

    # Lease released after a successful turn.
    assert not (sessions_dir / "sess-a" / "lock.json").exists()


def test_send_releases_lease_when_executor_raises(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), RuntimeError("boom")])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    with pytest.raises(RuntimeError):
        runtime.send(role=role, session_id="sess-a", prompt="turn", now=T1)

    assert not (sessions_dir / "sess-a" / "lock.json").exists()


def test_send_releases_lease_on_protocol_error_output(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    malformed = b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\nGARBAGE\n'
    fake = FakeExecutor([_outcome(_new_named()), _outcome(malformed)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.send(role=role, session_id="sess-a", prompt="turn", now=T1)

    assert outcome.result["status"] == "protocol_error"
    assert not (sessions_dir / "sess-a" / "lock.json").exists()
    assert (outcome.turn_dir / "result.json").exists()


def test_send_refuses_binding_mismatch_before_executor_and_artifacts(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    drifted = _persistent_role(valid_role_dict, work_dir, description="totally different role")

    with pytest.raises(SessionBindingError):
        runtime.send(role=drifted, session_id="sess-a", prompt="turn", now=T1)

    # No prompt turn executor call, no turn artifacts, no dangling lock.
    assert len(fake.calls) == 1
    assert not (sessions_dir / "sess-a" / "turns").exists()
    assert not (sessions_dir / "sess-a" / "lock.json").exists()


def test_send_refuses_missing_session_without_executor(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(SessionNotFoundError):
        runtime.send(role=role, session_id="ghost", prompt="turn", now=T1)

    assert fake.calls == []


def test_send_refuses_exec_role(valid_role_dict, work_dir, sessions_dir) -> None:
    role = _exec_role(valid_role_dict, work_dir)
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(ExecStrategyError):
        runtime.send(role=role, session_id="sess-a", prompt="turn", now=T1)

    assert fake.calls == []


def test_send_redacts_prompt_and_stderr_in_turn_artifacts(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    stderr_secret = b"warning Authorization: Bearer abcdef123456 leaked"
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1(), stderr=stderr_secret)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.send(
        role=role,
        session_id="sess-a",
        prompt="use sk-ABCDEFGH12345678 then reply",
        now=T1,
    )

    prompt_txt = (outcome.turn_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "sk-ABCDEFGH12345678" not in prompt_txt
    assert "[REDACTED]" in prompt_txt

    stderr_log = (outcome.turn_dir / "stderr.log").read_text(encoding="utf-8")
    assert "Bearer abcdef123456" not in stderr_log

    report = json.loads((outcome.turn_dir / "redaction-report.json").read_text(encoding="utf-8"))
    assert report["matches"]


# --- status ---------------------------------------------------------------


def test_status_summarizes_management_json_safely_without_lock(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_status_alive())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.status(role=role, session_id="sess-a")

    status_argv = fake.calls[1]["argv"]
    assert status_argv[-4:] == ["codex", "status", "-s", "nightly"]
    assert outcome.ok is True
    assert outcome.summary["kind"] == "status_snapshot"
    assert outcome.summary["status"] == "alive"
    assert outcome.summary["acpx_session_id"] == "019e7940-4726-73e2-ab90-10b8bd7714f7"
    # Safe summary: no bulk/untrusted model catalog leaks into the summary.
    assert "gpt-5.5" not in json.dumps(outcome.summary)
    # Read-only query takes no lease.
    assert not (sessions_dir / "sess-a" / "lock.json").exists()
    # Management response persisted as redacted evidence.
    assert (sessions_dir / "sess-a" / "management" / "status.json").exists()


def test_status_no_session_snapshot_returns_not_ok(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_status_no_session())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.status(role=role, session_id="sess-a")

    assert outcome.ok is False
    assert outcome.result["ok"] is False
    assert outcome.summary["kind"] == "status_snapshot"
    assert outcome.summary["status"] == "no-session"


def test_status_refuses_binding_mismatch_before_executor(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    drifted = _persistent_role(valid_role_dict, work_dir, description="totally different role")

    with pytest.raises(SessionBindingError):
        runtime.status(role=drifted, session_id="sess-a")

    assert len(fake.calls) == 1


def test_status_refuses_exec_role(valid_role_dict, work_dir, sessions_dir) -> None:
    role = _exec_role(valid_role_dict, work_dir)
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(ExecStrategyError):
        runtime.status(role=role, session_id="sess-a")

    assert fake.calls == []

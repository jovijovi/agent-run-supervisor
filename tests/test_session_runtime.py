"""Persistent-session runtime tests.

These tests pin the local session runtime: create/open a role-bound session
through fixture-shaped acpx management commands, send a single prompt turn under
a lease lock with redacted turn artifacts, query status/show, close/cancel the
local lifecycle, and list supervisor-owned session records. They drive a **fake
executor** — no real acpx, no network, no AGENT launch — and reuse the S1a
fixtures as the proven command/stream contract.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from agent_run_supervisor.mcp_config import McpConfigError
from agent_run_supervisor.policy import ExecStrategyError
from agent_run_supervisor.process_liveness import LivenessProbe, ProcessIdentity
from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import SubprocessOutcome
from agent_run_supervisor.session import (
    SessionBindingError,
    SessionClosedError,
    SessionExistsError,
    SessionLockError,
    SessionNotFoundError,
    SessionStore,
)
from agent_run_supervisor.session_runtime import (
    SessionRuntime,
    SessionRuntimeError,
)
from agent_run_supervisor.workspace import WorkspaceValidationError

UTC = timezone.utc
T0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 5, 31, 12, 5, 0, tzinfo=UTC)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "acpx-0.12.0"


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


def _turn2() -> bytes:
    return _fixture("session-prompt-turn2", "stdout.ndjson")


def _show_open() -> bytes:
    return _fixture("session-show-open", "stdout.json")


def _status_alive() -> bytes:
    return _fixture("session-status-after-turns", "stdout.json")


def _status_no_session() -> bytes:
    return _fixture("management-status-no-session-exit0", "stdout.json")


def _close_named() -> bytes:
    return _fixture("session-close-named", "stdout.json")


def _cancel_no_active() -> bytes:
    return _fixture("session-cancel-no-active", "stdout.json")


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
        on_spawn: Callable[[int], None] | None = None,
    ) -> SubprocessOutcome:
        # The fake replays canned outcomes and never spawns a real child, so
        # there is no genuine pid to hand ``on_spawn``; record it for contract
        # fidelity with ``SubprocessExecutor`` without inventing a spawn event.
        self.calls.append(
            {
                "argv": argv,
                "cwd": cwd,
                "env": dict(env),
                "timeout_seconds": timeout_seconds,
                "grace_ms": grace_ms,
                "on_spawn": on_spawn,
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
    assert isinstance(data["acpx_session_id"], str) and data["acpx_session_id"]
    assert data["session_name"] == "nightly"
    assert data["state"] == "open"
    assert outcome.record.acpx_session_id == data["acpx_session_id"]
    assert outcome.result["acpx_session_id"] == data["acpx_session_id"]
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


# --- S2 no-op fail-closed turns ---------------------------------------------


def _noop_turn_stream() -> bytes:
    """Exit-0-shaped prompt-turn stream with zero observed effect.

    Mirrors the observed `/goal …` incident: protocol-clean bookkeeping
    (initialize, prompt accepted, command/usage updates, ``stopReason``) but
    no agent message text and no tool events.
    """
    return b"\n".join(
        [
            b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}',
            b'{"jsonrpc":"2.0","id":1,"method":"session/prompt","params":{}}',
            b'{"jsonrpc":"2.0","method":"session/update","params":{"update":{"sessionUpdate":"available_commands_update","availableCommands":[]}}}',
            b'{"jsonrpc":"2.0","method":"session/update","params":{"update":{"sessionUpdate":"usage_update","totalTokens":7}}}',
            b'{"jsonrpc":"2.0","id":1,"result":{"stopReason":"end_turn"}}',
        ]
    ) + b"\n"


def test_send_slash_goal_noop_turn_fails_closed_as_no_op(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    # S2 regression (incident): a `/goal …` slash prompt turn that exits 0
    # without producing any output or tool activity must classify as the
    # fail-closed ``no_op``, never as ``completed``.
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_noop_turn_stream())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.send(
        role=role, session_id="sess-a", prompt="/goal ship the report", now=T1
    )

    assert outcome.result["status"] == "no_op"
    assert outcome.result["error_code"] == "NO_OP"
    assert outcome.result["retryable"] is False
    assert outcome.result["observed_effect"] is False
    # The slash prompt still travelled as one untouched argv element.
    assert fake.calls[1]["argv"][-1] == "/goal ship the report"
    # Turn artifacts persist and the lease is released like any other turn.
    assert (outcome.turn_dir / "result.json").exists()
    assert not (sessions_dir / "sess-a" / "lock.json").exists()


def test_send_records_slash_prompt_kind_in_turn_result(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.send(
        role=role, session_id="sess-a", prompt="/goal ship it", now=T1
    )

    assert outcome.result["prompt_kind"] == "slash_command"
    # A turn with real agent output reports observed_effect=True.
    assert outcome.result["observed_effect"] is True
    persisted = json.loads(
        (outcome.turn_dir / "result.json").read_text(encoding="utf-8")
    )
    assert persisted["prompt_kind"] == "slash_command"


def test_send_records_text_prompt_kind_in_turn_result(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.send(
        role=role, session_id="sess-a", prompt="plain contract turn", now=T1
    )

    assert outcome.result["prompt_kind"] == "text"


def test_send_writes_generated_policy_artifact_and_policy_mode(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    # S2 audit symmetry (solution §2.1-4): the exec path persists
    # generated-policy.json per run; session turns must carry the same
    # per-turn evidence plus the compiled permission mode in the result.
    role = _persistent_role(valid_role_dict, work_dir)  # read+search granted
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.send(
        role=role, session_id="sess-a", prompt="contract turn 1", now=T1
    )

    policy_artifact = json.loads(
        (outcome.turn_dir / "generated-policy.json").read_text(encoding="utf-8")
    )
    assert policy_artifact["defaultAction"] == "deny"
    assert "read" in policy_artifact["autoApprove"]
    assert outcome.result["prompt_permission_mode"] == "policy"


def test_send_all_deny_role_reports_deny_all_permission_mode(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    all_deny = {kind: False for kind in valid_role_dict["permissions"]}
    role = _persistent_role(valid_role_dict, work_dir, permissions=all_deny)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.send(
        role=role, session_id="sess-a", prompt="contract turn 1", now=T1
    )

    assert outcome.result["prompt_permission_mode"] == "deny_all"
    policy_artifact = json.loads(
        (outcome.turn_dir / "generated-policy.json").read_text(encoding="utf-8")
    )
    assert policy_artifact["autoApprove"] == []


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
    stderr_secret = b"warning Authorization: " + b"Bearer " + b"abcdef123456 leaked"
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1(), stderr=stderr_secret)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    prompt_secret = "sk-" + "ABCDEFGH12345678"
    outcome = runtime.send(
        role=role,
        session_id="sess-a",
        prompt=f"use {prompt_secret} then reply",
        now=T1,
    )

    prompt_txt = (outcome.turn_dir / "prompt.txt").read_text(encoding="utf-8")
    assert prompt_secret not in prompt_txt
    assert "[REDACTED]" in prompt_txt

    stderr_log = (outcome.turn_dir / "stderr.log").read_text(encoding="utf-8")
    assert "Bearer " + "abcdef123456" not in stderr_log

    report = json.loads((outcome.turn_dir / "redaction-report.json").read_text(encoding="utf-8"))
    assert report["matches"]


# --- multi-turn continuity (S1 closure) -----------------------------------


def test_two_sequential_sends_reuse_record_persist_distinct_turns_and_release_lease(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    """S1 closure: multi-turn continuity through ``SessionRuntime``.

    Two sequential ``send`` calls against the same local session id must reuse
    the same local record (no second create), drive the SAME acpx session name,
    persist two distinct redacted turn directories, release the lease after each
    turn, and parse the S1a fixture markers ``S1A_SESSION_TURN_1_OK`` /
    ``S1A_SESSION_TURN_2_OK`` (turn2 reuses the same ACP session id, skipping
    ``initialize``/``session/new``).
    """
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1()), _outcome(_turn2())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    created = _create_and(runtime, role)
    # Exactly one management call so far: the single create. No extra create
    # is allowed for the second turn.
    assert len(fake.calls) == 1

    first = runtime.send(role=role, session_id="sess-a", prompt="contract turn 1", now=T1)
    # Lease released after the first turn so the second turn can acquire it.
    assert not (sessions_dir / "sess-a" / "lock.json").exists()

    second = runtime.send(role=role, session_id="sess-a", prompt="contract turn 2", now=T1)
    # Lease released after the second turn too.
    assert not (sessions_dir / "sess-a" / "lock.json").exists()

    # Same local record reused: only the two prompt turns were added, so the
    # second turn did NOT trigger another create.
    assert len(fake.calls) == 3
    assert created.session_id == "sess-a"
    assert fake.calls[1]["argv"][-5:] == ["codex", "prompt", "-s", "nightly", "contract turn 1"]
    assert fake.calls[2]["argv"][-5:] == ["codex", "prompt", "-s", "nightly", "contract turn 2"]

    # Two distinct redacted turn directories persisted under the one session.
    assert first.turn_id != second.turn_id
    assert first.turn_dir != second.turn_dir
    turns_root = sessions_dir / "sess-a" / "turns"
    turn_dirs = sorted(p.name for p in turns_root.iterdir() if p.is_dir())
    assert len(turn_dirs) == 2
    assert {first.turn_id, second.turn_id} == set(turn_dirs)

    # Multi-turn continuity markers parsed from the fixtures.
    assert first.result["final_message"] == "S1A_SESSION_TURN_1_OK"
    assert second.result["final_message"] == "S1A_SESSION_TURN_2_OK"
    assert first.result["status"] == "completed"
    assert second.result["status"] == "completed"
    assert first.result["session_id"] == "sess-a"
    assert second.result["session_id"] == "sess-a"
    assert first.result["business_verdict"] is None
    assert second.result["business_verdict"] is None

    # The single local session record stays open across both turns.
    assert runtime.store.open_session("sess-a").state == "open"


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
    assert isinstance(outcome.summary["acpx_session_id"], str) and outcome.summary["acpx_session_id"]
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


# --- close (S1d) ----------------------------------------------------------


def test_close_runs_fixture_argv_marks_record_closed_and_returns_json(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_close_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.close(role=role, session_id="sess-a", now=T1)

    close_argv = fake.calls[1]["argv"]
    assert close_argv[-4:] == ["codex", "sessions", "close", "nightly"]
    assert outcome.result["state"] == "closed"
    assert outcome.result["closed"] is True
    assert outcome.result["kind"] == "session_closed"
    assert outcome.result["business_verdict"] is None
    assert outcome.result["session_id"] == "sess-a"

    # Record atomically transitioned to closed and observable on a fresh open.
    assert runtime.store.open_session("sess-a").state == "closed"
    # Redacted management evidence persisted (allow-listed summary only).
    mgmt = sessions_dir / "sess-a" / "management" / "close.json"
    assert mgmt.exists()
    saved = json.loads(mgmt.read_text(encoding="utf-8"))
    assert saved["kind"] == "session_closed"
    assert "gpt-5.5" not in json.dumps(saved)


def test_close_refuses_already_closed_before_executor(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_close_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)
    runtime.close(role=role, session_id="sess-a", now=T1)

    with pytest.raises(SessionClosedError):
        runtime.close(role=role, session_id="sess-a", now=T1)

    # The second close must not spawn acpx again (create + 1 close only).
    assert len(fake.calls) == 2


def test_close_fails_closed_on_unexpected_kind_without_marking_closed(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_status_alive())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    with pytest.raises(SessionRuntimeError):
        runtime.close(role=role, session_id="sess-a", now=T1)

    # Fail closed: record stays open, no close evidence written.
    assert runtime.store.open_session("sess-a").state == "open"
    assert not (sessions_dir / "sess-a" / "management" / "close.json").exists()


def test_close_fails_closed_on_nonzero_exit_without_marking_closed(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    error_stdout = _fixture("management-no-session-exit4", "stdout.ndjson")
    fake = FakeExecutor([_outcome(_new_named()), _outcome(error_stdout, exit_code=4)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    with pytest.raises(SessionRuntimeError):
        runtime.close(role=role, session_id="sess-a", now=T1)

    assert runtime.store.open_session("sess-a").state == "open"


def test_close_refuses_binding_mismatch_before_executor(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    drifted = _persistent_role(valid_role_dict, work_dir, description="totally different role")

    with pytest.raises(SessionBindingError):
        runtime.close(role=drifted, session_id="sess-a", now=T1)

    assert len(fake.calls) == 1
    assert runtime.store.open_session("sess-a").state == "open"
    assert not (sessions_dir / "sess-a" / "management" / "close.json").exists()


def test_close_refuses_active_lease_before_executor_and_artifacts(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)
    runtime.store.acquire_lock("sess-a", owner="active-turn", now=T1, lease_seconds=60)

    with pytest.raises(SessionLockError):
        runtime.close(role=role, session_id="sess-a", now=T1)

    # Close must not race an active turn: no close executor call, no evidence, state stays open.
    assert len(fake.calls) == 1
    assert runtime.store.open_session("sess-a").state == "open"
    assert not (sessions_dir / "sess-a" / "management" / "close.json").exists()


def test_close_rechecks_open_state_under_lifecycle_guard_before_executor(
    valid_role_dict, work_dir, sessions_dir, monkeypatch
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)
    original_guard = runtime.store.lifecycle_guard

    @contextmanager
    def close_then_guard(session_id: str):
        # Simulate another close completing after close's first open-state check
        # but before this close enters its serialized lifecycle section.
        runtime.store.mark_closed(session_id, now=T1)
        with original_guard(session_id):
            yield

    monkeypatch.setattr(runtime.store, "lifecycle_guard", close_then_guard)

    with pytest.raises(SessionClosedError):
        runtime.close(role=role, session_id="sess-a", now=T1)

    assert len(fake.calls) == 1
    assert not (sessions_dir / "sess-a" / "management" / "close.json").exists()


def test_close_refuses_exec_role(valid_role_dict, work_dir, sessions_dir) -> None:
    role = _exec_role(valid_role_dict, work_dir)
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(ExecStrategyError):
        runtime.close(role=role, session_id="sess-a", now=T1)

    assert fake.calls == []


# --- abort / cancel (S1d) -------------------------------------------------


def test_abort_runs_cancel_argv_reports_cancelled_false_and_keeps_open(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_cancel_no_active())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.abort(role=role, session_id="sess-a")

    abort_argv = fake.calls[1]["argv"]
    assert abort_argv[-4:] == ["codex", "cancel", "-s", "nightly"]
    # No active request: cancelled=false is honest, NOT a business verdict.
    assert outcome.result["cancelled"] is False
    assert outcome.result["kind"] == "cancel_result"
    assert outcome.result["business_verdict"] is None
    assert outcome.cancelled is False
    # Cancel is not close: the record stays open.
    assert runtime.store.open_session("sess-a").state == "open"
    assert (sessions_dir / "sess-a" / "management" / "abort.json").exists()


def test_abort_reports_cancelled_true_when_acpx_cancels_active_work(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    cancelled_true = b'{"action":"cancel_result","acpxRecordId":"r","cancelled":true}'
    fake = FakeExecutor([_outcome(_new_named()), _outcome(cancelled_true)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    outcome = runtime.abort(role=role, session_id="sess-a")

    assert outcome.result["cancelled"] is True
    assert outcome.cancelled is True


def test_abort_refuses_closed_session_before_executor(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_close_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)
    runtime.close(role=role, session_id="sess-a", now=T1)

    with pytest.raises(SessionClosedError):
        runtime.abort(role=role, session_id="sess-a")

    # No cancel executor call after the close (create + close only).
    assert len(fake.calls) == 2


def test_abort_fails_closed_on_unexpected_kind(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_close_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    with pytest.raises(SessionRuntimeError):
        runtime.abort(role=role, session_id="sess-a")

    assert runtime.store.open_session("sess-a").state == "open"


@pytest.mark.parametrize(
    "cancel_payload",
    [
        pytest.param(b'{"action":"cancel_result","acpxRecordId":"r"}', id="missing"),
        pytest.param(
            b'{"action":"cancel_result","acpxRecordId":"r","cancelled":null}', id="null"
        ),
        pytest.param(
            b'{"action":"cancel_result","acpxRecordId":"r","cancelled":"true"}', id="string"
        ),
        pytest.param(
            b'{"action":"cancel_result","acpxRecordId":"r","cancelled":1}', id="int"
        ),
    ],
)
def test_abort_fails_closed_when_cancelled_not_boolean(
    valid_role_dict, work_dir, sessions_dir, cancel_payload
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(cancel_payload)])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    # A cancel_result whose `cancelled` is missing/non-boolean is malformed
    # management output, NOT an honest cancelled=false.
    with pytest.raises(SessionRuntimeError):
        runtime.abort(role=role, session_id="sess-a")

    # Fail closed: no abort.json dressing malformed output as a clean verdict.
    assert not (sessions_dir / "sess-a" / "management" / "abort.json").exists()
    # Cancel never flips state; a fail-closed abort leaves the record open.
    assert runtime.store.open_session("sess-a").state == "open"


def test_abort_refuses_binding_mismatch_before_executor(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    drifted = _persistent_role(valid_role_dict, work_dir, description="totally different role")

    with pytest.raises(SessionBindingError):
        runtime.abort(role=drifted, session_id="sess-a")

    assert len(fake.calls) == 1


def test_abort_rechecks_open_state_under_lifecycle_guard_before_executor(
    valid_role_dict, work_dir, sessions_dir, monkeypatch
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)
    original_guard = runtime.store.lifecycle_guard

    @contextmanager
    def close_then_guard(session_id: str):
        # Simulate close completing after abort's first open-state check but
        # before abort enters its serialized lifecycle section.
        runtime.store.mark_closed(session_id, now=T1)
        with original_guard(session_id):
            yield

    monkeypatch.setattr(runtime.store, "lifecycle_guard", close_then_guard)

    with pytest.raises(SessionClosedError):
        runtime.abort(role=role, session_id="sess-a")

    assert len(fake.calls) == 1
    assert not (sessions_dir / "sess-a" / "management" / "abort.json").exists()


def test_abort_refuses_exec_role(valid_role_dict, work_dir, sessions_dir) -> None:
    role = _exec_role(valid_role_dict, work_dir)
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(ExecStrategyError):
        runtime.abort(role=role, session_id="sess-a")

    assert fake.calls == []


# --- send guard on closed sessions (S1d) ----------------------------------


def test_send_refuses_closed_session_before_executor_and_artifacts(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_close_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)
    runtime.close(role=role, session_id="sess-a", now=T1)

    with pytest.raises(SessionClosedError):
        runtime.send(role=role, session_id="sess-a", prompt="turn", now=T1)

    # No turn executor call (create + close only), no turn dir, no lock.
    assert len(fake.calls) == 2
    assert not (sessions_dir / "sess-a" / "turns").exists()
    assert not (sessions_dir / "sess-a" / "lock.json").exists()


def test_send_rechecks_closed_state_under_lease_before_executor_and_artifacts(
    valid_role_dict, work_dir, sessions_dir, monkeypatch
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)
    original_acquire = runtime.store.acquire_lock

    def close_then_acquire(*args, **kwargs):
        # Simulate close completing after send's first ensure_open/binding check
        # but before send acquires its lease. Send must re-read state under the
        # acquired lease and refuse before launching the prompt turn.
        runtime.store.mark_closed("sess-a", now=T1)
        return original_acquire(*args, **kwargs)

    monkeypatch.setattr(runtime.store, "acquire_lock", close_then_acquire)

    with pytest.raises(SessionClosedError):
        runtime.send(role=role, session_id="sess-a", prompt="turn", now=T1)

    assert len(fake.calls) == 1
    assert not (sessions_dir / "sess-a" / "turns").exists()
    assert not (sessions_dir / "sess-a" / "lock.json").exists()


# --- list (S1d, local read-only) ------------------------------------------


def test_list_sessions_returns_minimal_records_without_launching_executor(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role, session_id="sess-a", session_name="alpha")
    _create_and(runtime, role, session_id="sess-b", session_name="beta")
    calls_after_create = len(fake.calls)

    outcome = runtime.list_sessions(role=role)

    # Local read-only: list must not launch acpx/AGENT work.
    assert len(fake.calls) == calls_after_create
    ids = [entry["session_id"] for entry in outcome.result["sessions"]]
    assert ids == ["sess-a", "sess-b"]
    assert outcome.result["count"] == 2
    assert outcome.result["business_verdict"] is None
    first = outcome.result["sessions"][0]
    assert first["state"] == "open"
    assert first["session_name"] == "alpha"


def test_list_sessions_reflects_closed_state(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_close_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)
    runtime.close(role=role, session_id="sess-a", now=T1)

    outcome = runtime.list_sessions()

    assert outcome.result["sessions"][0]["state"] == "closed"


def test_list_sessions_filters_by_role_when_provided(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    _create_and(runtime, role)

    # A role that does not own the record (different role_hash) sees nothing.
    drifted = _persistent_role(valid_role_dict, work_dir, description="totally different role")
    filtered = runtime.list_sessions(role=drifted)
    assert filtered.result["sessions"] == []
    assert filtered.result["count"] == 0

    # The owning role sees its record.
    owned = runtime.list_sessions(role=role)
    assert [e["session_id"] for e in owned.result["sessions"]] == ["sess-a"]


def test_list_sessions_empty_when_no_records(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    outcome = runtime.list_sessions()

    assert outcome.result["sessions"] == []
    assert outcome.result["count"] == 0
    assert fake.calls == []


# --- K1 crash recovery: reclaim provably-crashed leases -------------------


def _identity(*, host="host-1", pid=111, start="s1", boot="boot-A") -> ProcessIdentity:
    return ProcessIdentity(pid=pid, process_start=start, boot_id=boot, host=host)


def _make_probe(identity: ProcessIdentity, *, running=None, starts=None) -> LivenessProbe:
    running = running or {}
    starts = starts or {}
    return LivenessProbe(
        is_running=lambda pid: running.get(pid, False),
        read_start=lambda pid: starts.get(pid),
        current=lambda: identity,
    )


def _seed_stale_lease(sessions_dir: Path, holder: ProcessIdentity, *, lease_seconds=600) -> None:
    """Leave a within-TTL lease owned by ``holder`` (a crashed/other process)."""
    SessionStore(sessions_dir, liveness_probe=_make_probe(holder)).acquire_lock(
        "sess-a", owner="prior-turn", now=T1, lease_seconds=lease_seconds
    )


def test_send_reclaims_within_ttl_lease_of_crashed_prior_turn(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1())])
    # The reviver proves the crashed holder PID 111 is no longer running.
    reviver = _make_probe(_identity(host="h", pid=222, boot="b"), running={111: False})
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake, liveness_probe=reviver)
    _create_and(runtime, role)

    # A prior send crashed mid-turn, leaving a within-TTL lease behind.
    _seed_stale_lease(sessions_dir, _identity(host="h", pid=111, start="s1", boot="b"))
    assert (sessions_dir / "sess-a" / "lock.json").exists()

    outcome = runtime.send(role=role, session_id="sess-a", prompt="resume turn", now=T1)

    # The crashed lease was safely reclaimed and the turn ran.
    assert outcome.result["status"] == "completed"
    assert outcome.result["final_message"] == "S1A_SESSION_TURN_1_OK"
    assert fake.calls[1]["argv"][-5:] == ["codex", "prompt", "-s", "nightly", "resume turn"]
    # Lease released after the recovered turn.
    assert not (sessions_dir / "sess-a" / "lock.json").exists()


def test_send_refuses_within_ttl_lease_of_live_holder(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    # The probe proves holder PID 111 is genuinely alive (running + start match).
    reviver = _make_probe(
        _identity(host="h", pid=222, boot="b"), running={111: True}, starts={111: "s1"}
    )
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake, liveness_probe=reviver)
    _create_and(runtime, role)
    _seed_stale_lease(sessions_dir, _identity(host="h", pid=111, start="s1", boot="b"))

    with pytest.raises(SessionLockError):
        runtime.send(role=role, session_id="sess-a", prompt="turn", now=T1)

    # No prompt turn ran; no turn artifacts; the live lease is untouched.
    assert len(fake.calls) == 1
    assert not (sessions_dir / "sess-a" / "turns").exists()
    assert (sessions_dir / "sess-a" / "lock.json").exists()


def test_send_refuses_within_ttl_lease_of_unknown_holder(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    # A different host cannot validate the holder PID -> UNKNOWN -> fail safe.
    reviver = _make_probe(_identity(host="other-host", pid=222), running={111: False})
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake, liveness_probe=reviver)
    _create_and(runtime, role)
    _seed_stale_lease(sessions_dir, _identity(host="h", pid=111, start="s1", boot="b"))

    with pytest.raises(SessionLockError):
        runtime.send(role=role, session_id="sess-a", prompt="turn", now=T1)

    assert len(fake.calls) == 1
    assert not (sessions_dir / "sess-a" / "turns").exists()


def test_close_reclaims_within_ttl_lease_of_crashed_prior_turn(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_close_named())])
    reviver = _make_probe(_identity(host="h", pid=222, boot="b"), running={111: False})
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake, liveness_probe=reviver)
    _create_and(runtime, role)
    _seed_stale_lease(sessions_dir, _identity(host="h", pid=111, start="s1", boot="b"))

    outcome = runtime.close(role=role, session_id="sess-a", now=T1)

    assert outcome.result["state"] == "closed"
    assert runtime.store.open_session("sess-a").state == "closed"
    assert not (sessions_dir / "sess-a" / "lock.json").exists()


def test_reclaim_crashed_disabled_restores_ttl_only_blocking(
    valid_role_dict, work_dir, sessions_dir
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    fake = FakeExecutor([_outcome(_new_named())])
    reviver = _make_probe(_identity(host="h", pid=222, boot="b"), running={111: False})
    runtime = SessionRuntime(
        sessions_dir=sessions_dir, executor=fake, liveness_probe=reviver, reclaim_crashed=False
    )
    _create_and(runtime, role)
    _seed_stale_lease(sessions_dir, _identity(host="h", pid=111, start="s1", boot="b"))

    # With recovery disabled, even a provably-crashed holder blocks until TTL.
    with pytest.raises(SessionLockError):
        runtime.send(role=role, session_id="sess-a", prompt="turn", now=T1)

    assert len(fake.calls) == 1
    assert not (sessions_dir / "sess-a" / "turns").exists()


# --- mcp_config role binding (native --mcp-config) --------------------------


def _write_mcp(path: Path, servers: list) -> None:
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _mcp_persistent_role(valid_role_dict, work_dir: Path, config: Path):
    return _persistent_role(
        valid_role_dict,
        work_dir,
        runner={
            **valid_role_dict["runner"],
            "acpx_binary": None,
            "mcp_config": str(config),
        },
    )


def test_create_session_binds_mcp_config_and_flags_management_argv(
    valid_role_dict, work_dir, sessions_dir, tmp_path
) -> None:
    config = tmp_path / "mcp.json"
    _write_mcp(config, [])
    role = _mcp_persistent_role(valid_role_dict, work_dir, config)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    runtime.create_session(role=role, session_id="sess-mcp", now=T0)

    argv = fake.calls[0]["argv"]
    assert argv[:3] == ["npx", "-y", "acpx@0.12.0"]
    assert argv[3:5] == ["--mcp-config", str(config)]

    data = json.loads(
        (sessions_dir / "sess-mcp" / "session.json").read_text(encoding="utf-8")
    )
    assert data["mcp_config_path"] == os.path.realpath(config)
    assert (
        data["mcp_config_sha256"]
        == "sha256:" + hashlib.sha256(config.read_bytes()).hexdigest()
    )


def test_create_session_fails_closed_before_spawn_on_bad_mcp_config(
    valid_role_dict, work_dir, sessions_dir, tmp_path
) -> None:
    role = _mcp_persistent_role(valid_role_dict, work_dir, tmp_path / "absent.json")
    fake = FakeExecutor([])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    with pytest.raises(McpConfigError):
        runtime.create_session(role=role, session_id="sess-mcp", now=T0)

    assert fake.calls == []
    assert not (sessions_dir / "sess-mcp").exists()


def test_send_carries_mcp_config_flag_on_prompt_turn(
    valid_role_dict, work_dir, sessions_dir, tmp_path
) -> None:
    config = tmp_path / "mcp.json"
    _write_mcp(config, [])
    role = _mcp_persistent_role(valid_role_dict, work_dir, config)
    fake = FakeExecutor([_outcome(_new_named()), _outcome(_turn1())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    runtime.create_session(
        role=role, session_id="sess-mcp", session_name="nightly", now=T0
    )

    turn = runtime.send(role=role, session_id="sess-mcp", prompt="hello", now=T1)

    assert turn.result["status"] == "completed"
    argv = fake.calls[1]["argv"]
    assert argv[:3] == ["npx", "-y", "acpx@0.12.0"]
    assert argv[3:5] == ["--mcp-config", str(config)]


def test_send_rechecks_mcp_content_drift_before_spawn(
    valid_role_dict, work_dir, sessions_dir, tmp_path
) -> None:
    config = tmp_path / "mcp.json"
    _write_mcp(config, [])
    role = _mcp_persistent_role(valid_role_dict, work_dir, config)
    fake = FakeExecutor([_outcome(_new_named())])
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)
    runtime.create_session(role=role, session_id="sess-mcp", now=T0)

    # Same path, new content: the pre-spawn recheck must fail closed on the
    # content SHA before any prompt subprocess is launched.
    _write_mcp(config, [{"name": "drifted"}])

    with pytest.raises(SessionBindingError) as excinfo:
        runtime.send(role=role, session_id="sess-mcp", prompt="hello", now=T1)

    assert "mcp_config_sha256" in str(excinfo.value)
    assert len(fake.calls) == 1  # the create management call only; no turn spawn

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import SubprocessOutcome, SupervisorRunner
from agent_run_supervisor.session_runtime import SessionRuntime

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "acpx-0.10.0"

INIT = b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n'
MSG = (
    b'{"jsonrpc":"2.0","method":"session/update","params":'
    b'{"sessionId":"abc","update":{"sessionUpdate":"agent_message_chunk",'
    b'"content":{"type":"text","text":"LIVE_OK"}}}}\n'
)
DONE = b'{"jsonrpc":"2.0","id":1,"result":{"stopReason":"end_turn","usage":{"inputTokens":1}}}\n'
STREAM = INIT + MSG + DONE


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _exec_role(
    valid_role_dict: dict[str, Any],
    work_dir: Path,
    *,
    max_output_bytes: int | None = None,
):
    payload = copy.deepcopy(valid_role_dict)
    payload["workspace"]["default_cwd"] = str(work_dir)
    payload["workspace"]["allowed_roots"] = [str(work_dir)]
    payload["runner"]["acpx_binary"] = str(work_dir / "fake-acpx")
    payload["session"] = {"strategy": "exec"}
    if max_output_bytes is not None:
        payload["limits"] = dict(payload["limits"])
        payload["limits"]["max_output_bytes"] = max_output_bytes
    return load_role(payload)


def _persistent_role(
    valid_role_dict: dict[str, Any],
    work_dir: Path,
    *,
    max_output_bytes: int | None = None,
):
    payload = copy.deepcopy(valid_role_dict)
    payload["workspace"]["default_cwd"] = str(work_dir)
    payload["workspace"]["allowed_roots"] = [str(work_dir)]
    payload["runner"]["acpx_binary"] = None
    payload["session"] = {"strategy": "persistent"}
    if max_output_bytes is not None:
        payload["limits"] = dict(payload["limits"])
        payload["limits"]["max_output_bytes"] = max_output_bytes
    return load_role(payload)


def _new_named() -> bytes:
    return (FIXTURES / "session-new-named" / "stdout.json").read_bytes()


class LiveExecExecutor:
    def __init__(self, runs_dir: Path, *, outcome: SubprocessOutcome | None = None) -> None:
        self.runs_dir = runs_dir
        self.outcome = outcome or SubprocessOutcome(exit_code=0, signal=None, stdout=STREAM, stderr=b"")
        self.observed_mid_run = False

    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        grace_ms: int,
        stdout_sink=None,
        **_: Any,
    ) -> SubprocessOutcome:
        assert stdout_sink is not None
        stdout_sink(INIT)
        run_dirs = list(self.runs_dir.iterdir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        assert not (run_dir / "result.json").exists()
        progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
        events = _read_events(run_dir / "normalized-events.jsonl")
        assert progress["state"] == "running"
        assert progress["last_seq"] == 1
        assert progress["event_count"] == 1
        assert [event["seq"] for event in events] == [1]
        assert events[0]["type"] == "run_started"
        self.observed_mid_run = True
        stdout_sink(MSG)
        stdout_sink(DONE)
        return self.outcome


def test_exec_run_persists_live_events_before_result_json(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    runs_dir = tmp_path / "runs"
    executor = LiveExecExecutor(runs_dir)
    runner = SupervisorRunner(runs_dir=runs_dir, executor=executor)

    outcome = runner.run(
        role=_exec_role(valid_role_dict, work_dir),
        prompt="live stream test",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert executor.observed_mid_run is True
    assert outcome.result["status"] == "completed"
    assert outcome.result["final_message"] == "LIVE_OK"
    events = _read_events(outcome.run_dir / "normalized-events.jsonl")
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert [event["type"] for event in events] == [
        "run_started",
        "agent_message_delta",
        "run_completed",
    ]
    progress = json.loads((outcome.run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "completed"
    assert progress["last_seq"] == 3
    assert progress["event_count"] == 3


def test_exec_run_timeout_keeps_live_events_and_progress(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    runs_dir = tmp_path / "runs"
    executor = LiveExecExecutor(
        runs_dir,
        outcome=SubprocessOutcome(
            exit_code=3,
            signal=15,
            stdout=INIT,
            stderr=b"",
            supervisor_killed=True,
            supervisor_timed_out=True,
            kill_reason="watchdog_timeout",
            kill_signal="SIGTERM",
            grace_ms=250,
            process_group_used=True,
            stdout_closed=True,
            stderr_closed=True,
        ),
    )
    runner = SupervisorRunner(runs_dir=runs_dir, executor=executor, watchdog_grace_ms=250)

    outcome = runner.run(
        role=_exec_role(valid_role_dict, work_dir),
        prompt="live timeout test",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert outcome.result["status"] == "timed_out"
    events = _read_events(outcome.run_dir / "normalized-events.jsonl")
    assert events[0]["type"] == "run_started"
    progress = json.loads((outcome.run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "timed_out"
    assert progress["event_count"] >= 1


def test_exec_live_path_preserves_max_output_bytes_fail_closed(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    runs_dir = tmp_path / "runs"
    executor = LiveExecExecutor(runs_dir)
    runner = SupervisorRunner(runs_dir=runs_dir, executor=executor)

    outcome = runner.run(
        role=_exec_role(valid_role_dict, work_dir, max_output_bytes=len(INIT) + 5),
        prompt="live max bytes test",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert outcome.result["status"] == "protocol_error"
    assert outcome.result["truncated"] is True
    assert outcome.result["truncate_reason"] == "max_output_bytes"
    events = _read_events(outcome.run_dir / "normalized-events.jsonl")
    assert [event["type"] for event in events] == ["run_started"]
    progress = json.loads((outcome.run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "protocol_error"


class SinkFailureExecExecutor:
    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        grace_ms: int,
        stdout_sink=None,
        **_: Any,
    ) -> SubprocessOutcome:
        assert stdout_sink is not None
        stdout_sink(INIT)
        return SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=STREAM,
            stderr=b"",
            stdout_sink_failed=True,
        )


def test_exec_live_sink_failure_falls_back_to_full_stdout_parse(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=SinkFailureExecExecutor())

    outcome = runner.run(
        role=_exec_role(valid_role_dict, work_dir),
        prompt="live sink fallback test",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert outcome.result["status"] == "completed"
    assert outcome.result["final_message"] == "LIVE_OK"
    events = _read_events(outcome.run_dir / "normalized-events.jsonl")
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert [event["type"] for event in events] == [
        "run_started",
        "agent_message_delta",
        "run_completed",
    ]
    progress = json.loads((outcome.run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "completed"
    assert progress["event_count"] == 3


class LiveSessionExecutor:
    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir
        self.calls = 0
        self.observed_mid_turn = False

    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        grace_ms: int,
        stdout_sink=None,
        **_: Any,
    ) -> SubprocessOutcome:
        self.calls += 1
        if self.calls == 1:
            assert stdout_sink is None
            return SubprocessOutcome(exit_code=0, signal=None, stdout=_new_named(), stderr=b"")
        assert stdout_sink is not None
        stdout_sink(INIT)
        turn_dirs = list((self.sessions_dir / "sess-live" / "turns").iterdir())
        assert len(turn_dirs) == 1
        turn_dir = turn_dirs[0]
        assert not (turn_dir / "result.json").exists()
        progress = json.loads((turn_dir / "progress.json").read_text(encoding="utf-8"))
        assert progress["state"] == "running"
        assert progress["last_seq"] == 1
        self.observed_mid_turn = True
        stdout_sink(MSG)
        stdout_sink(DONE)
        return SubprocessOutcome(exit_code=0, signal=None, stdout=STREAM, stderr=b"")


def test_session_send_persists_live_turn_events_before_result_json(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    executor = LiveSessionExecutor(sessions_dir)
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=executor)
    role = _persistent_role(valid_role_dict, work_dir)

    runtime.create_session(role=role, session_id="sess-live", session_name="nightly")
    outcome = runtime.send(role=role, session_id="sess-live", prompt="live turn")

    assert executor.observed_mid_turn is True
    assert outcome.result["status"] == "completed"
    assert outcome.result["final_message"] == "LIVE_OK"
    events = _read_events(outcome.turn_dir / "normalized-events.jsonl")
    assert [event["seq"] for event in events] == [1, 2, 3]
    progress = json.loads((outcome.turn_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "completed"
    assert progress["event_count"] == 3


def test_session_send_live_path_preserves_max_output_bytes_fail_closed(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    executor = LiveSessionExecutor(sessions_dir)
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=executor)
    role = _persistent_role(valid_role_dict, work_dir, max_output_bytes=len(INIT) + 5)

    runtime.create_session(role=role, session_id="sess-live", session_name="nightly")
    outcome = runtime.send(role=role, session_id="sess-live", prompt="live turn")

    assert outcome.result["status"] == "protocol_error"
    assert outcome.result["truncated"] is True
    assert outcome.result["truncate_reason"] == "max_output_bytes"
    events = _read_events(outcome.turn_dir / "normalized-events.jsonl")
    assert [event["type"] for event in events] == ["run_started"]
    progress = json.loads((outcome.turn_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "protocol_error"

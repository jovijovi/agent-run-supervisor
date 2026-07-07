from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from agent_run_supervisor.caller import (
    CallerInvocationError,
    CallerInvocationSpec,
    invoke_caller,
)
from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import SubprocessOutcome, SupervisorRunner
from agent_run_supervisor.session_runtime import SessionRuntime


class RecordingExecutor:
    def __init__(self, outcomes: list[SubprocessOutcome]) -> None:
        self.outcomes = list(outcomes)
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
        if not self.outcomes:
            raise AssertionError(f"unexpected executor call #{len(self.calls)}: {argv!r}")
        return self.outcomes.pop(0)


class NoCallRunner:
    calls = 0

    def dry_run(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("runner.dry_run should not be called")

    def run(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("runner.run should not be called")


class NoCallRuntime:
    calls = 0

    def create_session(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("runtime.create_session should not be called")

    def send(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("runtime.send should not be called")

    def status(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("runtime.status should not be called")

    def close(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("runtime.close should not be called")

    def abort(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("runtime.abort should not be called")

    def list_sessions(self, **_: Any) -> Any:
        self.calls += 1
        raise AssertionError("runtime.list_sessions should not be called")


def _exec_role(valid_role_dict: dict[str, Any], work_dir: Path):
    payload = copy.deepcopy(valid_role_dict)
    payload["workspace"]["default_cwd"] = str(work_dir)
    payload["workspace"]["allowed_roots"] = [str(work_dir)]
    payload["runner"]["acpx_binary"] = str(work_dir / "fake-acpx")
    payload["session"] = {"strategy": "exec"}
    return load_role(payload)


def _persistent_role(valid_role_dict: dict[str, Any], work_dir: Path):
    payload = copy.deepcopy(valid_role_dict)
    payload["workspace"]["default_cwd"] = str(work_dir)
    payload["workspace"]["allowed_roots"] = [str(work_dir)]
    payload["runner"]["acpx_binary"] = str(work_dir / "fake-acpx")
    payload["session"] = {"strategy": "persistent"}
    return load_role(payload)


def _outcome(stdout: bytes, *, exit_code: int = 0) -> SubprocessOutcome:
    return SubprocessOutcome(
        exit_code=exit_code,
        signal=None,
        stdout=stdout,
        stderr=b"",
        stdout_closed=True,
        stderr_closed=True,
    )


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def test_validation_requires_exactly_one_role_source(
    valid_role_dict: dict[str, Any],
    role_file: Path,
    work_dir: Path,
) -> None:
    role = _exec_role(valid_role_dict, work_dir)

    with pytest.raises(CallerInvocationError, match="exactly one"):
        invoke_caller(CallerInvocationSpec(mode="exec", prompt="hello"))

    with pytest.raises(CallerInvocationError, match="exactly one"):
        invoke_caller(
            CallerInvocationSpec(
                mode="exec",
                role=role,
                role_file=role_file,
                prompt="hello",
            )
        )


@pytest.mark.parametrize("mode", ["exec", "exec_dry_run", "session_send"])
def test_validation_requires_prompt_for_prompt_modes(
    mode: str,
    valid_role_dict: dict[str, Any],
    work_dir: Path,
) -> None:
    role = _persistent_role(valid_role_dict, work_dir) if mode.startswith("session_") else _exec_role(valid_role_dict, work_dir)

    with pytest.raises(CallerInvocationError, match="prompt"):
        invoke_caller(
            CallerInvocationSpec(mode=mode, role=role, session_id="sess-a"),
            runner=NoCallRunner(),
            session_runtime=NoCallRuntime(),
        )


@pytest.mark.parametrize(
    "mode",
    ["session_create", "session_send", "session_status", "session_close"],
)
def test_validation_requires_session_id_for_session_modes(
    mode: str,
    valid_role_dict: dict[str, Any],
    work_dir: Path,
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    spec = CallerInvocationSpec(
        mode=mode,
        role=role,
        prompt="turn prompt" if mode == "session_send" else None,
    )

    with pytest.raises(CallerInvocationError, match="session_id"):
        invoke_caller(
            spec,
            runner=NoCallRunner(),
            session_runtime=NoCallRuntime(),
        )


def test_validation_rejects_session_name_outside_create(
    valid_role_dict: dict[str, Any],
    work_dir: Path,
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)

    with pytest.raises(CallerInvocationError, match="session_name"):
        invoke_caller(
            CallerInvocationSpec(
                mode="session_send",
                role=role,
                session_id="sess-a",
                session_name="nightly",
                prompt="turn",
            ),
            session_runtime=NoCallRuntime(),
        )


def test_validation_rejects_invalid_mode_before_runner_or_runtime_call(
    valid_role_dict: dict[str, Any],
    work_dir: Path,
) -> None:
    role = _exec_role(valid_role_dict, work_dir)
    runner = NoCallRunner()
    runtime = NoCallRuntime()

    with pytest.raises(CallerInvocationError, match="unsupported mode"):
        invoke_caller(
            CallerInvocationSpec(mode="deliver", role=role, prompt="hello"),
            runner=runner,
            session_runtime=runtime,
        )

    assert runner.calls == 0
    assert runtime.calls == 0


def test_exec_dry_run_delegates_to_local_runner_and_returns_null_business_verdict(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    work_dir: Path,
) -> None:
    role = _exec_role(valid_role_dict, work_dir)

    result = invoke_caller(
        CallerInvocationSpec(
            mode="exec_dry_run",
            role=role,
            context="caller context",
            prompt="caller prompt",
            runs_dir=tmp_path / "runs",
        )
    )

    assert result.mode == "exec_dry_run"
    assert result.supervisor_status == "dry_run"
    assert result.result["business_verdict"] is None
    assert result.business_verdict is None
    assert result.run_dir == result.result["run_dir"]
    assert result.artifact_dir == result.run_dir
    assert Path(result.run_dir or "").exists()
    prompt_text = (Path(result.run_dir or "") / "prompt.txt").read_text(encoding="utf-8")
    assert "caller context" in prompt_text
    assert "caller prompt" in prompt_text


def test_exec_delegates_to_supervisor_runner_with_fake_subprocess_result(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
    work_dir: Path,
) -> None:
    role = _exec_role(valid_role_dict, work_dir)
    fake = RecordingExecutor(
        [_outcome((fixtures_root / "success-codex-sentinel" / "stdout.ndjson").read_bytes())]
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake)

    result = invoke_caller(
        CallerInvocationSpec(
            mode="exec",
            role=role,
            context="caller context",
            prompt="caller prompt",
        ),
        runner=runner,
    )

    assert len(fake.calls) == 1
    assert fake.calls[0]["argv"][-1] == "caller context\n\ncaller prompt"
    assert fake.calls[0]["cwd"] == work_dir
    assert result.supervisor_status == "completed"
    assert result.result["status"] == "completed"
    assert result.result["final_message"] == "CODEX_ACPX_OK"
    assert result.result["business_verdict"] is None
    assert result.business_verdict is None
    assert result.run_dir == result.result["run_dir"]
    assert result.session_dir is None


def test_session_modes_delegate_to_session_runtime_and_preserve_null_business_verdict(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
    work_dir: Path,
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    sessions_dir = tmp_path / "sessions"
    fake = RecordingExecutor(
        [
            _outcome((fixtures_root / "session-new-named" / "stdout.json").read_bytes()),
            _outcome((fixtures_root / "session-prompt-turn1" / "stdout.ndjson").read_bytes()),
            _outcome((fixtures_root / "session-status-after-turns" / "stdout.json").read_bytes()),
            _outcome((fixtures_root / "session-close-named" / "stdout.json").read_bytes()),
        ]
    )
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    created = invoke_caller(
        CallerInvocationSpec(
            mode="session_create",
            role=role,
            session_id="sess-a",
            session_name="nightly",
            sessions_dir=sessions_dir,
        ),
        session_runtime=runtime,
    )
    sent = invoke_caller(
        CallerInvocationSpec(
            mode="session_send",
            role=role,
            session_id="sess-a",
            context="caller context",
            prompt="turn 1",
            sessions_dir=sessions_dir,
        ),
        session_runtime=runtime,
    )
    status = invoke_caller(
        CallerInvocationSpec(
            mode="session_status",
            role=role,
            session_id="sess-a",
            sessions_dir=sessions_dir,
        ),
        session_runtime=runtime,
    )
    closed = invoke_caller(
        CallerInvocationSpec(
            mode="session_close",
            role=role,
            session_id="sess-a",
            sessions_dir=sessions_dir,
        ),
        session_runtime=runtime,
    )

    assert [call["argv"][-5:] for call in fake.calls[:2]] == [
        ["codex", "sessions", "new", "--name", "nightly"],
        ["codex", "prompt", "-s", "nightly", "caller context\n\nturn 1"],
    ]
    assert fake.calls[2]["argv"][-4:] == ["codex", "status", "-s", "nightly"]
    assert fake.calls[3]["argv"][-4:] == ["codex", "sessions", "close", "nightly"]

    assert created.result["business_verdict"] is None
    assert sent.result["business_verdict"] is None
    assert status.result["business_verdict"] is None
    assert closed.result["business_verdict"] is None
    assert created.business_verdict is None
    assert sent.business_verdict is None
    assert status.business_verdict is None
    assert closed.business_verdict is None

    assert created.session_dir == str(sessions_dir / "sess-a")
    assert created.artifact_dir == created.session_dir
    assert sent.supervisor_status == "completed"
    assert sent.result["final_message"] == "S1A_SESSION_TURN_1_OK"
    assert sent.run_dir == sent.result["run_dir"]
    assert sent.session_dir == str(sessions_dir / "sess-a")
    assert status.result["ok"] is True
    assert status.session_dir == str(sessions_dir / "sess-a")
    assert closed.result["state"] == "closed"
    assert closed.session_dir == str(sessions_dir / "sess-a")


def test_session_abort_and_list_delegate_to_session_runtime(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
    work_dir: Path,
) -> None:
    role = _persistent_role(valid_role_dict, work_dir)
    sessions_dir = tmp_path / "sessions"
    fake = RecordingExecutor(
        [
            _outcome((fixtures_root / "session-new-named" / "stdout.json").read_bytes()),
            _outcome((fixtures_root / "session-cancel-no-active" / "stdout.json").read_bytes()),
        ]
    )
    runtime = SessionRuntime(sessions_dir=sessions_dir, executor=fake)

    invoke_caller(
        CallerInvocationSpec(
            mode="session_create",
            role=role,
            session_id="sess-a",
            session_name="nightly",
            sessions_dir=sessions_dir,
        ),
        session_runtime=runtime,
    )
    aborted = invoke_caller(
        CallerInvocationSpec(
            mode="session_abort",
            role=role,
            session_id="sess-a",
            sessions_dir=sessions_dir,
        ),
        session_runtime=runtime,
    )
    listed = invoke_caller(
        CallerInvocationSpec(
            mode="session_list",
            role=role,
            sessions_dir=sessions_dir,
        ),
        session_runtime=runtime,
    )

    assert fake.calls[1]["argv"][-3:] == ["cancel", "-s", "nightly"]
    assert aborted.result["cancelled"] is False
    assert aborted.result["business_verdict"] is None
    assert aborted.session_dir == str(sessions_dir / "sess-a")
    assert listed.result["count"] == 1
    assert listed.result["sessions"][0]["session_id"] == "sess-a"
    assert listed.artifact_dir == str(sessions_dir)


def test_caller_result_projection_has_only_local_supervisor_fields(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    work_dir: Path,
) -> None:
    role = _exec_role(valid_role_dict, work_dir)

    result = invoke_caller(
        CallerInvocationSpec(
            mode="exec_dry_run",
            role=role,
            prompt="caller prompt",
            runs_dir=tmp_path / "runs",
        )
    )

    projection = result.to_dict()
    assert set(projection) == {
        "mode",
        "supervisor_status",
        "result",
        "artifact_dir",
        "run_dir",
        "session_dir",
        "business_verdict",
    }
    forbidden = {
        "delivery",
        "delivery_state",
        "gateway",
        "platform",
        "webhook",
        "channel_id",
        "message_id",
        "recipient",
        "public_ingress",
        "auto_reply",
    }
    assert forbidden.isdisjoint(projection)
    assert forbidden.isdisjoint(result.result)


def test_caller_module_does_not_parse_raw_acpx_streams(repo_root: Path) -> None:
    source = (repo_root / "src" / "agent_run_supervisor" / "caller.py").read_text(
        encoding="utf-8"
    )

    assert "agent_run_supervisor.parser" not in source
    assert "parse_acpx" not in source
    assert "summarize_management_json" not in source

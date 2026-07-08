from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import (
    SupervisorRunner,
    SubprocessOutcome,
    execute_subprocess,
    execute_with_optional_stdout_sink,
)


class RecordingExecutor:
    def __init__(self, outcome: SubprocessOutcome) -> None:
        self.outcome = outcome
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
        return self.outcome


def _role_with_workspace(valid_role_dict: dict[str, Any], work_dir: Path) -> Any:
    payload = dict(valid_role_dict)
    payload["workspace"] = dict(valid_role_dict["workspace"])
    payload["workspace"]["default_cwd"] = str(work_dir)
    payload["workspace"]["allowed_roots"] = [str(work_dir)]
    payload["runner"] = dict(valid_role_dict["runner"])
    payload["runner"]["acpx_binary"] = str(work_dir / "fake-acpx")
    return load_role(payload)


def _success_stdout(fixtures_root: Path) -> bytes:
    return (fixtures_root / "success-codex-sentinel" / "stdout.ndjson").read_bytes()


def test_exec_run_uses_argv_list_without_shell(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    fake = RecordingExecutor(
        SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=_success_stdout(fixtures_root),
            stderr=b"",
        )
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake, watchdog_grace_ms=250)

    outcome = runner.run(
        role=role,
        prompt="Connectivity smoke only.",
        cwd=None,
        env={"PATH": "/usr/bin", "TOKEN_VALUE": "secret-token-value"},
    )

    assert outcome.result["status"] == "completed"
    assert outcome.result["final_message"] == "CODEX_ACPX_OK"
    assert outcome.result["business_verdict"] is None
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert isinstance(call["argv"], list)
    assert all(isinstance(part, str) for part in call["argv"])
    assert call["argv"][0] == str(work_dir / "fake-acpx")
    assert call["cwd"] == work_dir
    assert call["env"]["PATH"] == "/usr/bin"
    assert call["timeout_seconds"] > role.limits.timeout_seconds
    assert "shell" not in call


def test_exec_run_uses_same_compiled_command_as_dry_run(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    prompt = "same prompt"
    dry_runner = SupervisorRunner(runs_dir=tmp_path / "dry-runs")
    dry = dry_runner.dry_run(role=role, prompt=prompt, cwd=None, env={"PATH": "/usr/bin"})
    fake = RecordingExecutor(
        SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=_success_stdout(fixtures_root),
            stderr=b"",
        )
    )
    exec_runner = SupervisorRunner(runs_dir=tmp_path / "exec-runs", executor=fake)

    executed = exec_runner.run(role=role, prompt=prompt, cwd=None, env={"PATH": "/usr/bin"})

    dry_argv = json.loads((dry.run_dir / "command.argv.json").read_text(encoding="utf-8"))
    exec_argv = json.loads((executed.run_dir / "command.argv.json").read_text(encoding="utf-8"))
    assert exec_argv == dry_argv


def test_exec_run_classifies_nonzero_exit_and_protocol_error(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    permission_runner = SupervisorRunner(
        runs_dir=tmp_path / "permission-runs",
        executor=RecordingExecutor(SubprocessOutcome(exit_code=5, signal=None, stdout=b"", stderr=b"")),
    )
    malformed_runner = SupervisorRunner(
        runs_dir=tmp_path / "malformed-runs",
        executor=RecordingExecutor(SubprocessOutcome(exit_code=0, signal=None, stdout=b"not json\n", stderr=b"")),
    )

    permission = permission_runner.run(role=role, prompt="hello", cwd=None, env={"PATH": "/usr/bin"})
    malformed = malformed_runner.run(role=role, prompt="hello", cwd=None, env={"PATH": "/usr/bin"})

    assert permission.result["status"] == "permission_denied"
    assert permission.result["acpx_exit_code"] == 5
    assert malformed.result["status"] == "protocol_error"
    assert malformed.result["error_code"] == "PROTOCOL_ERROR"


def test_exec_run_exit_zero_without_observed_effect_classifies_no_op(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
) -> None:
    # S2 regression: exit 0 with a protocol-clean stream but no agent output
    # and no tool events must classify as the fail-closed no_op, never as
    # completed.
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    silent = b"\n".join(
        [
            b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}',
            b'{"jsonrpc":"2.0","id":1,"result":{"stopReason":"end_turn"}}',
        ]
    ) + b"\n"
    fake = RecordingExecutor(
        SubprocessOutcome(exit_code=0, signal=None, stdout=silent, stderr=b"")
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake, watchdog_grace_ms=250)

    outcome = runner.run(
        role=role, prompt="do nothing", cwd=None, env={"PATH": "/usr/bin"}
    )

    assert outcome.result["status"] == "no_op"
    assert outcome.result["error_code"] == "NO_OP"
    assert outcome.result["retryable"] is False
    assert outcome.result["observed_effect"] is False


def test_exec_run_classifies_interrupted_exit(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    interrupted_runner = SupervisorRunner(
        runs_dir=tmp_path / "interrupted-runs",
        executor=RecordingExecutor(SubprocessOutcome(exit_code=130, signal=2, stdout=b"", stderr=b"")),
    )

    interrupted = interrupted_runner.run(role=role, prompt="hello", cwd=None, env={"PATH": "/usr/bin"})

    assert interrupted.result["status"] == "interrupted"
    assert interrupted.result["error_code"] == "INTERRUPTED"
    assert interrupted.result["acpx_exit_code"] == 130
    assert interrupted.result["signal"] == 2


def test_exec_run_redacts_stderr_secret(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    sensitive_value = "sk-" + "runnerstderrsecret1234567890"
    fake = RecordingExecutor(
        SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=_success_stdout(fixtures_root),
            stderr=f"stderr leaked {sensitive_value}\n".encode("utf-8"),
        )
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake)

    outcome = runner.run(role=role, prompt="hello", cwd=None, env={"PATH": "/usr/bin"})

    stderr_text = (outcome.run_dir / "stderr.log").read_text(encoding="utf-8")
    report = json.loads((outcome.run_dir / "redaction-report.json").read_text(encoding="utf-8"))
    assert sensitive_value not in stderr_text
    assert "[REDACTED]" in stderr_text
    assert report["matches"]


def test_exec_run_reports_stdout_redactions(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    sensitive_value = "sk-" + "runnerstdoutsecret1234567890"
    fake = RecordingExecutor(
        SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=f"not json {sensitive_value}\n".encode("utf-8"),
            stderr=b"",
        )
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake)

    outcome = runner.run(role=role, prompt="hello", cwd=None, env={"PATH": "/usr/bin"})

    stdout_text = (outcome.run_dir / "acpx-stdout.ndjson").read_text(encoding="utf-8")
    report = json.loads((outcome.run_dir / "redaction-report.json").read_text(encoding="utf-8"))
    assert sensitive_value not in stdout_text
    assert "[REDACTED]" in stdout_text
    assert report["matches"]


def test_exec_run_records_fake_watchdog_timeout_metadata(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    fake = RecordingExecutor(
        SubprocessOutcome(
            exit_code=3,
            signal=15,
            stdout=b"",
            stderr=b"",
            supervisor_killed=True,
            supervisor_timed_out=True,
            kill_reason="watchdog_timeout",
            kill_signal="SIGTERM",
            grace_ms=250,
            process_group_used=True,
            stdout_closed=True,
            stderr_closed=True,
        )
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake, watchdog_grace_ms=250)

    outcome = runner.run(role=role, prompt="hello", cwd=None, env={"PATH": "/usr/bin"})

    assert outcome.result["status"] == "timed_out"
    assert outcome.result["origin"] == "supervisor"
    assert outcome.result["kill_reason"] == "watchdog_timeout"
    assert outcome.result["kill_signal"] == "SIGTERM"
    assert outcome.result["grace_ms"] == 250
    assert outcome.result["process_group_used"] is True


def test_execute_subprocess_timeout_does_not_duplicate_partial_output(tmp_path: Path) -> None:
    child = "import sys,time; sys.stdout.write('A'); sys.stdout.flush(); time.sleep(5)"

    outcome = execute_subprocess(
        argv=[sys.executable, "-c", child],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=1,
        grace_ms=50,
    )

    assert outcome.supervisor_timed_out is True
    assert outcome.supervisor_killed is True
    assert outcome.kill_reason in {"watchdog_timeout", "watchdog_timeout_force_kill"}
    assert outcome.stdout == b"A"
    assert outcome.stdout_closed is True
    assert outcome.stderr_closed is True


def test_execute_subprocess_pipe_holder_descendant_still_hits_watchdog(tmp_path: Path) -> None:
    child = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'], "
        "stdout=sys.stdout, stderr=sys.stderr); "
        "print('parent done', flush=True)"
    )

    outcome = execute_subprocess(
        argv=[sys.executable, "-c", child],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=1,
        grace_ms=50,
    )

    assert outcome.supervisor_timed_out is True
    assert outcome.supervisor_killed is True
    assert outcome.kill_reason in {
        "watchdog_timeout_pipe_drain",
        "watchdog_timeout_pipe_drain_force_kill",
    }
    assert b"parent done" in outcome.stdout
    assert outcome.stdout_closed is True
    assert outcome.stderr_closed is True


def test_execute_subprocess_slow_stdout_sink_after_success_does_not_timeout(tmp_path: Path) -> None:
    def slow_sink(_: bytes) -> None:
        time.sleep(0.2)

    outcome = execute_subprocess(
        argv=[sys.executable, "-c", "print('done', flush=True)"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        grace_ms=10,
        stdout_sink=slow_sink,
    )

    assert outcome.exit_code == 0
    assert outcome.supervisor_timed_out is False
    assert outcome.kill_reason is None
    assert outcome.stdout == b"done\n"
    assert outcome.stdout_closed is True
    assert outcome.stderr_closed is True


def test_execute_subprocess_high_output_slow_sink_does_not_backpressure_child(
    tmp_path: Path,
) -> None:
    def slow_sink(_: bytes) -> None:
        time.sleep(0.001)

    child = "import sys\nfor i in range(2000): print('line', flush=False)\nsys.stdout.flush()"
    outcome = execute_subprocess(
        argv=[sys.executable, "-c", child],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=1,
        grace_ms=50,
        stdout_sink=slow_sink,
    )

    assert outcome.exit_code == 0
    assert outcome.supervisor_timed_out is False
    assert outcome.kill_reason is None
    assert len(outcome.stdout.splitlines()) == 2000
    assert outcome.stdout_sink_failed is True
    assert outcome.stdout_closed is True
    assert outcome.stderr_closed is True


def test_execute_subprocess_stalled_stdout_sink_is_bounded_after_child_exit(
    tmp_path: Path,
) -> None:
    def stalled_sink(_: bytes) -> None:
        time.sleep(5)

    start = time.monotonic()
    outcome = execute_subprocess(
        argv=[sys.executable, "-c", "print('done', flush=True)"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=1,
        grace_ms=50,
        stdout_sink=stalled_sink,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 2
    assert outcome.exit_code == 0
    assert outcome.supervisor_timed_out is False
    assert outcome.stdout == b"done\n"
    assert outcome.stdout_sink_failed is True


def test_execute_subprocess_missing_binary_returns_127(tmp_path: Path) -> None:
    outcome = execute_subprocess(
        argv=[str(tmp_path / "definitely-missing-acpx-binary")],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=100,
    )

    assert outcome.exit_code == 127
    assert outcome.stdout_closed is True
    assert outcome.stderr_closed is True
    assert b"No such file" in outcome.stderr or b"definitely-missing" in outcome.stderr


def test_execute_subprocess_oserror_on_spawn(tmp_path: Path, monkeypatch) -> None:
    def raise_oserror(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("agent_run_supervisor.runner.subprocess.Popen", raise_oserror)

    outcome = execute_subprocess(
        argv=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=100,
    )

    assert outcome.exit_code == 1
    assert b"permission denied" in outcome.stderr


def test_execute_subprocess_spawn_callback_failure(tmp_path: Path) -> None:
    helper = tmp_path / "exit0.py"
    helper.write_text("import sys\nsys.exit(0)\n")

    def failing_spawn(_pid: int) -> None:
        raise RuntimeError("spawn tracking failed")

    outcome = execute_subprocess(
        argv=[sys.executable, str(helper)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=100,
        on_spawn=failing_spawn,
    )

    assert outcome.exit_code == 1
    assert outcome.supervisor_killed is True
    assert outcome.kill_reason == "spawn_callback_failed"
    assert b"spawn callback failed" in outcome.stderr


class LegacyExecutorWithoutStdoutSink:
    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        grace_ms: int,
    ) -> SubprocessOutcome:
        return SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=b"",
            stderr=b"",
        )


def test_execute_with_optional_stdout_sink_skips_sink_for_legacy_executor(
    tmp_path: Path,
) -> None:
    sink_calls: list[bytes] = []

    outcome = execute_with_optional_stdout_sink(
        LegacyExecutorWithoutStdoutSink(),
        argv=["ignored"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=100,
        stdout_sink=lambda chunk: sink_calls.append(chunk),
    )

    assert outcome.exit_code == 0
    assert sink_calls == []


def test_execute_with_optional_stdout_sink_passes_sink_to_builtin_executor(
    tmp_path: Path,
) -> None:
    sink_calls: list[bytes] = []

    outcome = execute_with_optional_stdout_sink(
        execute_subprocess,
        argv=[sys.executable, "-c", "print('live-line')"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=100,
        stdout_sink=lambda chunk: sink_calls.append(chunk),
    )

    assert outcome.exit_code == 0
    assert any(b"live-line" in chunk for chunk in sink_calls)


class SinkFailedAfterPartialLive:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout
        self.saw_sink = False

    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        grace_ms: int,
        stdout_sink=None,
    ) -> SubprocessOutcome:
        if stdout_sink is not None:
            stdout_sink(b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n')
            self.saw_sink = True
        return SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=self.stdout,
            stderr=b"",
            stdout_sink_failed=True,
            stdout_closed=True,
        )


def test_exec_run_finalizes_via_batch_when_stdout_sink_failed(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    stdout = _success_stdout(fixtures_root)
    executor = SinkFailedAfterPartialLive(stdout)
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=executor)

    outcome = runner.run(
        role=role,
        prompt="Batch fallback after sink failure.",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert executor.saw_sink is True
    assert outcome.result["status"] == "completed"
    assert (Path(outcome.run_dir) / "progress.json").exists()
    events = (Path(outcome.run_dir) / "normalized-events.jsonl").read_text(encoding="utf-8")
    assert events.strip()


def test_exec_run_records_acpx_error_metadata(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    error_stdout = (fixtures_root / "usage-error-invalid-flag" / "stdout.ndjson").read_bytes()
    fake = RecordingExecutor(
        SubprocessOutcome(
            exit_code=1,
            signal=None,
            stdout=error_stdout,
            stderr=b"",
        )
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake)

    outcome = runner.run(
        role=role,
        prompt="Expect usage error classification.",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert outcome.result["status"] == "invalid_invocation"
    assert outcome.result.get("origin") == "cli"
    assert outcome.result["error_code"] == "INVALID_INVOCATION"


def test_exec_run_classifies_signal_termination(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    fake = RecordingExecutor(
        SubprocessOutcome(
            exit_code=137,
            signal=9,
            stdout=b"",
            stderr=b"killed",
        )
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake)

    outcome = runner.run(
        role=role,
        prompt="Interrupted run.",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert outcome.result["acpx_exit_code"] == 137
    assert outcome.result["signal"] == 9
    assert outcome.result["status"] == "infrastructure_error"


class VarKeywordLegacyExecutor:
    def __call__(self, **kwargs: Any) -> SubprocessOutcome:
        assert kwargs["stdout_sink"] is not None
        kwargs["stdout_sink"](b"ignored\n")
        return SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=b"",
            stderr=b"",
        )


def test_execute_with_optional_stdout_sink_supports_var_keyword_executor(
    tmp_path: Path,
) -> None:
    outcome = execute_with_optional_stdout_sink(
        VarKeywordLegacyExecutor(),
        argv=["ignored"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=100,
        stdout_sink=lambda _chunk: None,
    )

    assert outcome.exit_code == 0


def test_execute_subprocess_watchdog_timeout_force_kill(tmp_path: Path) -> None:
    sleeper = tmp_path / "sleep.py"
    sleeper.write_text("import time\ntime.sleep(30)\n")

    outcome = execute_subprocess(
        argv=[sys.executable, str(sleeper)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=1,
        grace_ms=50,
    )

    assert outcome.supervisor_timed_out is True
    assert outcome.supervisor_killed is True
    assert outcome.kill_reason in {
        "watchdog_timeout",
        "watchdog_timeout_force_kill",
    }


def test_execute_subprocess_spawn_callback_failure_force_kill(tmp_path: Path) -> None:
    sleeper = tmp_path / "ignore_term.py"
    sleeper.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(30)\n"
    )

    def failing_spawn(_pid: int) -> None:
        raise RuntimeError("spawn tracking failed")

    outcome = execute_subprocess(
        argv=[sys.executable, str(sleeper)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=50,
        on_spawn=failing_spawn,
    )

    assert outcome.exit_code == 1
    assert outcome.kill_reason in {
        "spawn_callback_failed",
        "spawn_callback_failed_force_kill",
    }


def test_execute_subprocess_stdout_sink_exception_still_drains(tmp_path: Path) -> None:
    def exploding_sink(_chunk: bytes) -> None:
        raise RuntimeError("sink exploded")

    outcome = execute_subprocess(
        argv=[sys.executable, "-c", "print('line-one'); print('line-two')"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=100,
        stdout_sink=exploding_sink,
    )

    assert outcome.exit_code == 0
    assert outcome.stdout_sink_failed is True
    assert b"line-one" in outcome.stdout
    assert b"line-two" in outcome.stdout


class DisableRaisingSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def disable(self) -> None:
        raise RuntimeError("disable failed")


def test_execute_subprocess_timeout_tolerates_disable_failure(tmp_path: Path) -> None:
    sleeper = tmp_path / "sleep.py"
    sleeper.write_text("import time\ntime.sleep(30)\n")
    sink = DisableRaisingSink()

    outcome = execute_subprocess(
        argv=[sys.executable, str(sleeper)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=1,
        grace_ms=50,
        stdout_sink=sink.feed,
    )

    assert outcome.supervisor_timed_out is True


def test_exec_run_extracts_stop_reason_from_completed_stream(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    fixtures_root: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    fake = RecordingExecutor(
        SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=_success_stdout(fixtures_root),
            stderr=b"",
        )
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake)

    outcome = runner.run(
        role=role,
        prompt="Completed with stop reason.",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert outcome.result["status"] == "completed"
    assert outcome.result["stop_reason"] == "end_turn"


def test_exec_run_tolerates_acpx_error_data_without_dict(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    role = _role_with_workspace(valid_role_dict, work_dir)
    stdout = (
        b'{"jsonrpc":"2.0","id":null,"error":{"code":-1,"message":"bad",'
        b'"data":"not-a-dict"}}\n'
    )
    fake = RecordingExecutor(
        SubprocessOutcome(exit_code=1, signal=None, stdout=stdout, stderr=b"")
    )
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake)

    outcome = runner.run(
        role=role,
        prompt="Non-dict acpx error data.",
        cwd=None,
        env={"PATH": "/usr/bin"},
    )

    assert outcome.result["status"] == "runner_error"


def test_execute_with_optional_stdout_sink_non_inspectable_executor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class CallableWithoutSignature:
        def __call__(
            self,
            *,
            argv: list[str],
            cwd: Path,
            env: Mapping[str, str],
            timeout_seconds: int,
            grace_ms: int,
        ) -> SubprocessOutcome:
            return SubprocessOutcome(exit_code=0, signal=None, stdout=b"", stderr=b"")

    def broken_signature(_obj):
        raise TypeError("no signature")

    monkeypatch.setattr(
        "agent_run_supervisor.runner.inspect.signature",
        broken_signature,
    )

    outcome = execute_with_optional_stdout_sink(
        CallableWithoutSignature(),
        argv=["ignored"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        grace_ms=100,
        stdout_sink=lambda _chunk: None,
    )

    assert outcome.exit_code == 0

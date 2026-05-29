from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import SupervisorRunner, SubprocessOutcome, execute_subprocess


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

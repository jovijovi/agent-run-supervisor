from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import SupervisorRunner, SubprocessOutcome


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
    runner = SupervisorRunner(runs_dir=tmp_path / "runs", executor=fake)

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

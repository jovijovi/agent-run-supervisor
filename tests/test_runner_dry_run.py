from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import (
    DryRunResult,
    SupervisorRunner,
    SubprocessOutcome,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_dry_run_writes_run_dir_with_safe_artifacts(tmp_path: Path, valid_role_dict: dict[str, Any], prompt_file: Path) -> None:
    role = load_role(valid_role_dict)
    runner = SupervisorRunner(runs_dir=tmp_path)

    outcome = runner.dry_run(role=role, prompt=prompt_file.read_text(), cwd=None)

    assert isinstance(outcome, DryRunResult)
    assert outcome.run_dir.exists()
    assert _mode(outcome.run_dir) == 0o700
    metadata = json.loads((outcome.run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["role_id"] == role.role_id
    assert metadata["allowed_roots_security_boundary"] is False
    assert metadata["dry_run"] is True
    argv = json.loads((outcome.run_dir / "command.argv.json").read_text(encoding="utf-8"))
    assert "--format" in argv and "--json-strict" in argv
    policy = json.loads((outcome.run_dir / "generated-policy.json").read_text(encoding="utf-8"))
    assert policy["defaultAction"] == "deny"
    result = json.loads((outcome.run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "dry_run"
    assert result["business_verdict"] is None
    assert _mode(outcome.run_dir / "result.json") == 0o600


def test_dry_run_redacts_sensitive_env_in_metadata(tmp_path: Path, valid_role_dict: dict[str, Any]) -> None:
    role = load_role(valid_role_dict)
    runner = SupervisorRunner(runs_dir=tmp_path)

    sensitive_env = {
        "OPENAI_API_KEY": "sk-" + "ABC" + "1234567890",
        "PATH": "/usr/bin",
    }
    outcome = runner.dry_run(
        role=role,
        prompt="hello",
        cwd=None,
        env=sensitive_env,
    )

    env_redacted = json.loads(
        (outcome.run_dir / "env.redacted.json").read_text(encoding="utf-8"),
    )
    assert env_redacted["OPENAI_API_KEY"] == "[REDACTED]"
    assert env_redacted["PATH"] == "/usr/bin"


def test_runner_classify_uses_exit_classifier(tmp_path: Path, valid_role_dict: dict[str, Any]) -> None:
    role = load_role(valid_role_dict)
    runner = SupervisorRunner(runs_dir=tmp_path)

    outcome = runner.finalize_outcome(
        role=role,
        prompt="hello",
        cwd=None,
        subprocess_outcome=SubprocessOutcome(
            exit_code=5,
            signal=None,
            stdout=b"",
            stderr=b"",
        ),
    )

    assert outcome.result["status"] == "permission_denied"
    assert outcome.result["acpx_exit_code"] == 5
    assert outcome.result["business_verdict"] is None


def test_runner_finalize_classifies_management_stdout_as_protocol_error(tmp_path: Path, valid_role_dict: dict[str, Any]) -> None:
    role = load_role(valid_role_dict)
    runner = SupervisorRunner(runs_dir=tmp_path)
    payload = b'{"action":"status_snapshot","status":"no-session","summary":"no active session"}\n'

    outcome = runner.finalize_outcome(
        role=role,
        prompt="hello",
        cwd=None,
        subprocess_outcome=SubprocessOutcome(
            exit_code=0,
            signal=None,
            stdout=payload,
            stderr=b"",
        ),
    )

    assert outcome.result["status"] == "protocol_error"


def test_runner_redacts_prompt_in_persisted_artifacts(tmp_path: Path, valid_role_dict: dict[str, Any]) -> None:
    role = load_role(valid_role_dict)
    runner = SupervisorRunner(runs_dir=tmp_path)
    leaky_prompt = "leaked key " + "sk-" + "test_should_not_be_here in prompt"

    outcome = runner.dry_run(role=role, prompt=leaky_prompt, cwd=None)

    persisted_prompt = (outcome.run_dir / "prompt.txt").read_text(encoding="utf-8")
    assert ("sk-" + "test_should_not_be_here") not in persisted_prompt
    assert "[REDACTED]" in persisted_prompt

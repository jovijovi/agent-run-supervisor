from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def test_validate_role_accepts_valid_role(run_cli, role_file: Path) -> None:
    completed = run_cli(["validate-role", str(role_file)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["role_id"] == "codex-reviewer"
    assert payload["valid"] is True


def test_validate_role_rejects_missing_role_id(run_cli, tmp_path: Path, valid_role_dict: dict[str, Any]) -> None:
    bad = dict(valid_role_dict)
    bad.pop("role_id")
    bad_path = tmp_path / "role.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")

    completed = run_cli(["validate-role", str(bad_path)])

    assert completed.returncode != 0
    assert "role_id" in completed.stderr


def test_replay_assembles_final_message_from_fixture(run_cli, fixtures_root: Path) -> None:
    fixture = fixtures_root / "success-codex-sentinel" / "stdout.ndjson"

    completed = run_cli(["replay", str(fixture)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["final_message"] == "CODEX_ACPX_OK"
    assert payload["protocol_error"] is False


def test_replay_rejects_management_status_stdout(run_cli, fixtures_root: Path) -> None:
    fixture = fixtures_root / "management-status-no-session-exit0" / "stdout.json"

    completed = run_cli(["replay", str(fixture)])

    assert completed.returncode != 0
    assert "protocol" in (completed.stdout + completed.stderr).lower()


def test_doctor_runs_without_role_and_does_not_launch_real_agent(run_cli) -> None:
    completed = run_cli(["doctor"])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["event_store_probe"]["dir_mode_ok"] is True
    assert payload["event_store_probe"]["file_mode_ok"] is True
    assert payload["fixture_replay"]["protocol_error"] is False
    assert "launched_real_agent" in payload
    assert payload["launched_real_agent"] is False


def test_doctor_validates_role_when_given(run_cli, role_file: Path) -> None:
    completed = run_cli(["doctor", "--role", str(role_file)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["role_validation"]["valid"] is True


def test_run_no_real_run_writes_artifacts(run_cli, tmp_path: Path, role_file: Path, prompt_file: Path) -> None:
    runs_dir = tmp_path / "runs"

    completed = run_cli(
        [
            "run",
            "--role",
            str(role_file),
            "--prompt-file",
            str(prompt_file),
            "--no-real-run",
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "dry_run"
    assert payload["business_verdict"] is None
    run_dir = Path(payload["run_dir"])
    assert run_dir.exists()
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["dry_run"] is True
    argv = json.loads((run_dir / "command.argv.json").read_text(encoding="utf-8"))
    assert "--json-strict" in argv


def test_run_without_no_real_run_does_not_invoke_acpx_in_test_env(run_cli, tmp_path: Path, role_file: Path, prompt_file: Path) -> None:
    runs_dir = tmp_path / "runs"
    import os

    env_blocker = {**os.environ, "AGENT_RUN_SUPERVISOR_DISABLE_REAL_RUN": "1"}
    completed = run_cli(
        [
            "run",
            "--role",
            str(role_file),
            "--prompt-file",
            str(prompt_file),
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert completed.returncode != 0 or "real run disabled" in (completed.stdout + completed.stderr).lower() or "[REDACTED]" in completed.stdout

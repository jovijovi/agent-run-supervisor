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


def test_doctor_emits_structured_node_and_acpx_probes(run_cli) -> None:
    completed = run_cli(["doctor"])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)

    node_probe = payload["node_probe"]
    assert node_probe["binary"] == "node"
    assert set(node_probe.keys()) >= {
        "binary",
        "available",
        "version",
        "requirement_minimum",
        "ok",
        "error_detail",
    }
    assert node_probe["requirement_minimum"] == "22.12.0"

    acpx_probe = payload["acpx_probe"]
    assert acpx_probe["binary"] == "acpx"
    assert set(acpx_probe.keys()) >= {
        "binary",
        "available",
        "version",
        "expected_version",
        "ok",
        "error_detail",
    }
    assert acpx_probe["expected_version"] == "0.10.0"

    assert payload["launched_real_agent"] is False


def test_doctor_validates_role_when_given(run_cli, role_file: Path) -> None:
    completed = run_cli(["doctor", "--role", str(role_file)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["role_validation"]["valid"] is True


def test_doctor_uses_role_acpx_binary_for_version_probe(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    fake_acpx = tmp_path / "fake-acpx"
    fake_acpx.write_text("#!/bin/sh\nprintf '0.10.0\\n'\n", encoding="utf-8")
    fake_acpx.chmod(0o700)
    role_payload = dict(valid_role_dict)
    role_payload["runner"] = dict(valid_role_dict["runner"])
    role_payload["runner"]["acpx_binary"] = str(fake_acpx)
    role_path = tmp_path / "role.json"
    role_path.write_text(json.dumps(role_payload), encoding="utf-8")

    completed = run_cli(["doctor", "--role", str(role_path)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["acpx_probe"]["binary"] == str(fake_acpx)
    assert payload["acpx_probe"]["available"] is True
    assert payload["acpx_probe"]["version"] == "0.10.0"
    assert payload["acpx_probe"]["ok"] is True


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


def test_run_without_no_real_run_emits_stable_refusal_and_creates_no_artifacts(
    run_cli, tmp_path: Path, role_file: Path, prompt_file: Path
) -> None:
    runs_dir = tmp_path / "runs"

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

    assert completed.returncode == 2, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "refused"
    assert payload["error_code"] == "REAL_RUN_DISABLED"
    assert payload["launched_real_agent"] is False
    assert payload["allowed_roots_security_boundary"] is False
    assert "disclaimer" in payload
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []


def test_run_refusal_happens_before_reading_prompt(
    run_cli, tmp_path: Path, role_file: Path
) -> None:
    runs_dir = tmp_path / "runs"
    missing_prompt = tmp_path / "does-not-exist.txt"

    completed = run_cli(
        [
            "run",
            "--role",
            str(role_file),
            "--prompt-file",
            str(missing_prompt),
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert completed.returncode == 2, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "refused"
    assert payload["error_code"] == "REAL_RUN_DISABLED"
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []


def test_run_no_real_run_refuses_when_cwd_outside_allowed_roots(
    run_cli, tmp_path: Path, prompt_file: Path, valid_role_dict
) -> None:
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    role_payload = dict(valid_role_dict)
    role_payload["workspace"] = dict(valid_role_dict["workspace"])
    role_payload["workspace"]["default_cwd"] = str(work)
    role_payload["workspace"]["allowed_roots"] = [str(work)]
    role_payload["workspace"]["allowed_roots_security_boundary"] = False
    role_path = tmp_path / "role.json"
    role_path.write_text(json.dumps(role_payload), encoding="utf-8")
    runs_dir = tmp_path / "runs"

    completed = run_cli(
        [
            "run",
            "--role",
            str(role_path),
            "--prompt-file",
            str(prompt_file),
            "--no-real-run",
            "--cwd",
            str(outside),
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert completed.returncode == 1, completed.stderr
    combined = completed.stdout + completed.stderr
    assert "allowed_roots" in combined.lower()
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []

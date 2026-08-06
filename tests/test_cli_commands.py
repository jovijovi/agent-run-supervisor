from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any

from agent_run_supervisor.session import (
    QUARANTINE_DISPATCH_OBSERVATION_LOST,
    SessionStore,
)


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


def test_default_doctor_fixture_dir_points_to_packaged_success_fixture() -> None:
    from agent_run_supervisor.commands import _default_fixture_dir

    replay = _default_fixture_dir() / "success-codex-sentinel" / "stdout.ndjson"

    assert replay.is_file()
    assert "CODEX_ACPX_OK" in replay.read_text(encoding="utf-8")


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
    assert acpx_probe["expected_version"] == "0.12.0"

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
    fake_acpx.write_text("#!/bin/sh\nprintf '0.12.0\\n'\n", encoding="utf-8")
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
    assert payload["acpx_probe"]["version"] == "0.12.0"
    assert payload["acpx_probe"]["ok"] is True


def test_doctor_no_role_includes_readonly_probes_and_is_ci_safe(run_cli) -> None:
    completed = run_cli(["doctor"])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    # The always-on, pure-local read-only probes are present and pass.
    assert payload["redaction_probe"]["ok"] is True
    assert payload["redaction_probe"]["leaked"] == []
    assert payload["session_probe"]["dir_mode_ok"] is True
    assert payload["session_probe"]["file_mode_ok"] is True
    # Role-only probes are absent (null) without --role.
    assert payload["policy_probe"] is None
    assert payload["workspace_probe"] is None
    assert payload["npx_probe"] is None
    assert payload["adapter_probe"] is None
    # External-binary absence (node/acpx/npx) never flips ok: clean env -> exit 0.
    assert payload["ok"] is True
    assert payload["launched_real_agent"] is False


def test_doctor_role_runs_readonly_role_probes(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_payload = dict(valid_role_dict)
    role_payload["workspace"] = dict(valid_role_dict["workspace"])
    role_payload["workspace"]["default_cwd"] = str(work)
    role_payload["workspace"]["allowed_roots"] = [str(work)]
    role_path = tmp_path / "role.json"
    role_path.write_text(json.dumps(role_payload), encoding="utf-8")

    completed = run_cli(["doctor", "--role", str(role_path)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["policy_probe"]["ok"] is True
    assert payload["policy_probe"]["default_action"] == "deny"
    assert payload["workspace_probe"]["ok"] is True
    assert payload["workspace_probe"]["allowed_roots_security_boundary"] is False
    # npx/adapter probes are present but informational (never gate ok).
    assert payload["npx_probe"] is not None
    assert payload["adapter_probe"]["declared"] is True
    assert "not launch" in payload["adapter_probe"]["detail"].lower()
    assert payload["launched_real_agent"] is False


def test_doctor_role_with_cwd_outside_roots_makes_ok_false(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    role_payload = dict(valid_role_dict)
    role_payload["workspace"] = dict(valid_role_dict["workspace"])
    # default_cwd outside the configured allowed_roots -> workspace probe fails.
    role_payload["workspace"]["default_cwd"] = str(outside)
    role_payload["workspace"]["allowed_roots"] = [str(work)]
    role_path = tmp_path / "role.json"
    role_path.write_text(json.dumps(role_payload), encoding="utf-8")

    completed = run_cli(["doctor", "--role", str(role_path)])

    assert completed.returncode == 1, completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["workspace_probe"]["ok"] is False
    assert payload["workspace_probe"]["error_detail"]
    # A failed role probe must not imply a launched agent.
    assert payload["launched_real_agent"] is False


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


def test_run_without_no_real_run_launches_local_exec_and_writes_artifacts(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], prompt_file: Path, fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    fake_acpx = tmp_path / "fake-acpx"
    fixture_stdout = fixtures_root / "success-codex-sentinel" / "stdout.ndjson"
    fake_acpx.write_text(f"#!/bin/sh\ncat {fixture_stdout!s}\n", encoding="utf-8")
    fake_acpx.chmod(0o700)
    role_payload = dict(valid_role_dict)
    role_payload["workspace"] = dict(valid_role_dict["workspace"])
    role_payload["workspace"]["default_cwd"] = str(work)
    role_payload["workspace"]["allowed_roots"] = [str(work)]
    role_payload["runner"] = dict(valid_role_dict["runner"])
    role_payload["runner"]["acpx_binary"] = str(fake_acpx)
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
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "completed"
    assert payload["final_message"] == "CODEX_ACPX_OK"
    assert payload["business_verdict"] is None
    run_dir = Path(payload["run_dir"])
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["dry_run"] is False
    argv = json.loads((run_dir / "command.argv.json").read_text(encoding="utf-8"))
    assert argv[0] == str(fake_acpx)


def test_run_real_exec_missing_prompt_creates_no_artifacts(
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

    assert completed.returncode == 1
    assert "run input error" in completed.stderr
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []


def test_validate_role_accepts_persistent_role(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    role_payload = dict(valid_role_dict)
    role_payload["session"] = {"lease_seconds": 900}
    role_path = tmp_path / "role.json"
    role_path.write_text(json.dumps(role_payload), encoding="utf-8")

    completed = run_cli(["validate-role", str(role_path)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["valid"] is True


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


# --- S1c session lifecycle CLI (create / send / status) -------------------
#
# These drive the real argparse + handler path against a fake acpx binary that
# dispatches per management/turn subcommand from the S1a fixtures. No real acpx,
# no network, no AGENT launch.


def _fake_acpx(
    tmp_path: Path,
    fixtures_root: Path,
    *,
    status_fixture: str = "session-status-after-turns",
) -> Path:
    new_json = fixtures_root / "session-new-named" / "stdout.json"
    show_json = fixtures_root / "session-show-open" / "stdout.json"
    status_json = fixtures_root / status_fixture / "stdout.json"
    turn_ndjson = fixtures_root / "session-prompt-turn1" / "stdout.ndjson"
    cancel_json = fixtures_root / "session-cancel-no-active" / "stdout.json"
    script = tmp_path / "fake-acpx"
    script.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"sessions new"*) cat "{new_json}" ;;\n'
        f'  *"sessions ensure"*) cat "{new_json}" ;;\n'
        f'  *"sessions show"*) cat "{show_json}" ;;\n'
        f'  *"cancel -s"*) cat "{cancel_json}" ;;\n'
        f'  *"status -s"*) cat "{status_json}" ;;\n'
        f'  *"prompt -s"*) cat "{turn_ndjson}" ;;\n'
        "  *) printf '{\"action\":\"unknown\"}\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return script


def _persistent_role_file(
    tmp_path: Path,
    valid_role_dict: dict[str, Any],
    work: Path,
    acpx_binary: Path | None,
) -> Path:
    payload = copy.deepcopy(valid_role_dict)
    payload["workspace"]["default_cwd"] = str(work)
    payload["workspace"]["allowed_roots"] = [str(work)]
    payload["runner"]["acpx_binary"] = str(acpx_binary) if acpx_binary else None
    payload["session"] = {"lease_seconds": 900}
    path = tmp_path / "session-role.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_session_create_cli_writes_record_and_json(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"

    completed = run_cli(
        [
            "session",
            "create",
            "--role",
            str(role_path),
            "--session-id",
            "sess-a",
            "--session-name",
            "nightly",
            "--sessions-dir",
            str(sessions_dir),
        ]
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["session_id"] == "sess-a"
    assert isinstance(payload["acpx_session_id"], str) and payload["acpx_session_id"]
    assert payload["business_verdict"] is None
    assert (sessions_dir / "sess-a" / "session.json").exists()


def test_session_send_cli_persists_turn_and_returns_final_message(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("contract turn 1", encoding="utf-8")

    created = run_cli(
        ["session", "create", "--role", str(role_path), "--session-id", "sess-a",
         "--session-name", "nightly", "--sessions-dir", str(sessions_dir)]
    )
    assert created.returncode == 0, created.stderr

    completed = run_cli(
        ["session", "send", "--role", str(role_path), "--session-id", "sess-a",
         "--prompt-file", str(prompt_path), "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "completed"
    assert payload["final_message"] == "S1A_SESSION_TURN_1_OK"
    assert payload["business_verdict"] is None
    assert payload["session_id"] == "sess-a"
    turns_dir = sessions_dir / "sess-a" / "turns"
    assert turns_dir.exists()
    assert list(turns_dir.iterdir()), "expected a persisted turn directory"


def test_session_send_goal_file_compiles_goal_contract_for_non_native_adapter(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    # Regression (2026-07-09 codex retest): `--goal-file` used to compose a
    # literal `/goal <text>` slash turn, which the codex ACP surface answers
    # with `Unknown command "/goal"` — a transport-completed no-op. The CLI
    # must compile the goal through `compile_goal_prompt`, which renders the
    # goal-contract/v1 text template for every adapter not registered as
    # natively supporting a `/goal` command (all of them today).
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    goal_path = tmp_path / "goal.txt"
    goal_path.write_text("ship the S2 report", encoding="utf-8")

    created = run_cli(
        ["session", "create", "--role", str(role_path), "--session-id", "sess-a",
         "--session-name", "nightly", "--sessions-dir", str(sessions_dir)]
    )
    assert created.returncode == 0, created.stderr

    completed = run_cli(
        ["session", "send", "--role", str(role_path), "--session-id", "sess-a",
         "--goal-file", str(goal_path), "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    # The compiled goal turn is plain text for non-native adapters …
    assert payload["prompt_kind"] == "text"
    # … and the permission shape stays role-governed, untouched by --goal-file.
    assert payload["prompt_permission_mode"] == "policy"
    turn_dirs = list((sessions_dir / "sess-a" / "turns").iterdir())
    assert len(turn_dirs) == 1
    prompt_text = (turn_dirs[0] / "prompt.txt").read_text(encoding="utf-8")
    assert prompt_text.startswith("[goal-contract/v1]")
    assert not prompt_text.startswith("/goal")
    assert "ship the S2 report" in prompt_text
    assert "GOAL_STATUS: DONE" in prompt_text


def test_session_send_rejects_prompt_file_and_goal_file_together(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("hello", encoding="utf-8")
    goal_path = tmp_path / "goal.txt"
    goal_path.write_text("ship it", encoding="utf-8")

    completed = run_cli(
        ["session", "send", "--role", str(role_path), "--session-id", "sess-a",
         "--prompt-file", str(prompt_path), "--goal-file", str(goal_path),
         "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 2
    assert "not allowed with" in completed.stderr


def test_session_send_goal_file_rejects_nested_slash_goal_text(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    goal_path = tmp_path / "goal.txt"
    goal_path.write_text("/clear", encoding="utf-8")

    created = run_cli(
        ["session", "create", "--role", str(role_path), "--session-id", "sess-a",
         "--session-name", "nightly", "--sessions-dir", str(sessions_dir)]
    )
    assert created.returncode == 0, created.stderr

    completed = run_cli(
        ["session", "send", "--role", str(role_path), "--session-id", "sess-a",
         "--goal-file", str(goal_path), "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 1
    assert "goal" in completed.stderr.lower()
    # Fail closed before any acpx launch: no turn artifacts were created.
    assert not (sessions_dir / "sess-a" / "turns").exists()


def test_session_status_cli_returns_safe_summary(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"

    created = run_cli(
        ["session", "create", "--role", str(role_path), "--session-id", "sess-a",
         "--session-name", "nightly", "--sessions-dir", str(sessions_dir)]
    )
    assert created.returncode == 0, created.stderr

    completed = run_cli(
        ["session", "status", "--role", str(role_path), "--session-id", "sess-a",
         "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["summary"]["kind"] == "status_snapshot"
    assert payload["summary"]["status"] == "alive"
    # Safe summary: the bulk model catalog must not leak through the CLI.
    assert "gpt-5.5" not in completed.stdout


def test_session_status_cli_no_session_snapshot_returns_nonzero(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(
        tmp_path,
        valid_role_dict,
        work,
        _fake_acpx(
            tmp_path,
            fixtures_root,
            status_fixture="management-status-no-session-exit0",
        ),
    )
    sessions_dir = tmp_path / "sessions"

    created = run_cli(
        ["session", "create", "--role", str(role_path), "--session-id", "sess-a",
         "--session-name", "nightly", "--sessions-dir", str(sessions_dir)]
    )
    assert created.returncode == 0, created.stderr

    completed = run_cli(
        ["session", "status", "--role", str(role_path), "--session-id", "sess-a",
         "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["summary"]["status"] == "no-session"


def test_session_without_subcommand_errors(run_cli) -> None:
    completed = run_cli(["session"])

    assert completed.returncode == 2
    assert "session" in (completed.stdout + completed.stderr).lower()


def test_session_send_cli_missing_prompt_file_errors(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    missing_prompt = tmp_path / "nope.txt"

    completed = run_cli(
        ["session", "send", "--role", str(role_path), "--session-id", "sess-a",
         "--prompt-file", str(missing_prompt), "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 1
    assert "error" in (completed.stdout + completed.stderr).lower()


# --- S1d session lifecycle CLI (close / abort / list) ---------------------


def _create_session_via_cli(run_cli, role_path: Path, sessions_dir: Path, session_id="sess-a"):
    created = run_cli(
        ["session", "create", "--role", str(role_path), "--session-id", session_id,
         "--session-name", "nightly", "--sessions-dir", str(sessions_dir)]
    )
    assert created.returncode == 0, created.stderr
    return created


def test_session_abort_cli_reports_cancelled_flag(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    _create_session_via_cli(run_cli, role_path, sessions_dir)

    completed = run_cli(
        ["session", "abort", "--role", str(role_path), "--session-id", "sess-a",
         "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["cancelled"] is False
    assert payload["kind"] == "cancel_result"
    assert payload["business_verdict"] is None
    # Cancelling affects the current work, never the Session: it is still
    # there, still carries no lifecycle state, and is still unquarantined.
    data = json.loads((sessions_dir / "sess-a" / "session.json").read_text(encoding="utf-8"))
    assert "state" not in data
    assert "quarantine" not in data
    assert (sessions_dir / "sess-a" / "management" / "abort.json").exists()


def test_session_send_cli_refuses_quarantined_session(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    """The only refusal left: quarantine, recorded out of band by a finalizer."""
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("contract turn 1", encoding="utf-8")
    _create_session_via_cli(run_cli, role_path, sessions_dir)

    SessionStore(base_dir=sessions_dir).mark_quarantined(
        "sess-a",
        reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST,
        run_id="run-prior",
    )

    completed = run_cli(
        ["session", "send", "--role", str(role_path), "--session-id", "sess-a",
         "--prompt-file", str(prompt_path), "--sessions-dir", str(sessions_dir)]
    )

    assert completed.returncode == 1
    assert "quarantin" in (completed.stdout + completed.stderr).lower()
    # Fail closed: no turn artifacts produced for the quarantined session.
    assert not (sessions_dir / "sess-a" / "turns").exists()


def test_session_list_cli_returns_records(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    _create_session_via_cli(run_cli, role_path, sessions_dir, session_id="sess-a")
    _create_session_via_cli(run_cli, role_path, sessions_dir, session_id="sess-b")

    completed = run_cli(
        ["session", "list", "--sessions-dir", str(sessions_dir), "--role", str(role_path)]
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    ids = [entry["session_id"] for entry in payload["sessions"]]
    assert ids == ["sess-a", "sess-b"]
    assert payload["count"] == 2
    assert payload["business_verdict"] is None
    # Local read-only summary: bulk acpx payload never appears.
    assert "gpt-5.5" not in completed.stdout


def test_session_list_cli_works_without_role(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], fixtures_root: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_path = _persistent_role_file(tmp_path, valid_role_dict, work, _fake_acpx(tmp_path, fixtures_root))
    sessions_dir = tmp_path / "sessions"
    _create_session_via_cli(run_cli, role_path, sessions_dir)

    completed = run_cli(["session", "list", "--sessions-dir", str(sessions_dir)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert [e["session_id"] for e in payload["sessions"]] == ["sess-a"]


def test_session_list_cli_empty_when_no_sessions(run_cli, tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"

    completed = run_cli(["session", "list", "--sessions-dir", str(sessions_dir)])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["sessions"] == []
    assert payload["count"] == 0


# --- H1 W2 cleanup CLI (dry-run default / confined apply) ------------------


def _artifact_runs_sessions(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / ".agent-run-supervisor"
    runs = base / "runs"
    sessions = base / "sessions"
    runs.mkdir(parents=True)
    sessions.mkdir(parents=True)
    return runs, sessions


def _aged_run(runs_dir: Path, run_id: str, *, age_days: float) -> Path:
    """A terminated Run carrying bulk evidence beside its spine."""
    run_dir = runs_dir / run_id
    run_dir.mkdir()
    bulk = run_dir / "events.jsonl"
    bulk.write_text('{"seq":1}\n', encoding="utf-8")
    marker = run_dir / "result.json"
    # A terminal the production reader trusts: retention prunes on nothing less.
    from agent_run_supervisor.exit_classifier import AgentRunStatus
    from agent_run_supervisor.result import build_result_payload

    marker.write_text(
        json.dumps(
            build_result_payload(
                run_id=run_id,
                status=AgentRunStatus.COMPLETED,
                origin="acp",
                detail_code=None,
                retryable=False,
                exit_code=0,
                signal=None,
                stop_reason="end_turn",
                usage=None,
                final_message="done",
                truncated=False,
                truncate_reason=None,
                run_dir=None,
            )
        ),
        encoding="utf-8",
    )
    ts = time.time() - age_days * 86400
    for path in (bulk, marker, run_dir):
        os.utime(path, (ts, ts))
    return run_dir


def _aged_session(sessions_dir: Path, session_id: str, *, age_days: float) -> Path:
    sess_dir = sessions_dir / session_id
    sess_dir.mkdir()
    record = sess_dir / "session.json"
    record.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
    ts = time.time() - age_days * 86400
    os.utime(record, (ts, ts))
    os.utime(sess_dir, (ts, ts))
    return sess_dir


def test_cleanup_cli_dry_run_then_apply(run_cli, tmp_path: Path) -> None:
    runs, sessions = _artifact_runs_sessions(tmp_path)
    old = _aged_run(runs, "run-old", age_days=10)
    fresh = _aged_run(runs, "run-fresh", age_days=1)

    dry = run_cli(
        ["cleanup", "--runs-dir", str(runs), "--sessions-dir", str(sessions),
         "--max-age-days", "5"]
    )

    assert dry.returncode == 0, dry.stderr
    plan = json.loads(dry.stdout)
    assert plan["applied"] is False
    prune_ids = {c["id"] for c in plan["plan"]["prune"]}
    skip_ids = {c["id"] for c in plan["plan"]["skip"]}
    assert prune_ids == {"run-old"}
    assert "run-fresh" in skip_ids
    # Dry-run removes nothing.
    assert (old / "events.jsonl").exists()
    assert (fresh / "events.jsonl").exists()

    applied = run_cli(
        ["cleanup", "--runs-dir", str(runs), "--sessions-dir", str(sessions),
         "--max-age-days", "5", "--apply"]
    )

    assert applied.returncode == 0, applied.stderr
    result = json.loads(applied.stdout)
    assert result["applied"] is True
    assert result["pruned"] == ["run-old"]
    assert result["failed"] == []
    # Bulk evidence is gone; the Run and its idempotency spine remain.
    assert not (old / "events.jsonl").exists()
    assert (old / "result.json").exists()
    assert (fresh / "events.jsonl").exists()


def test_cleanup_cli_refuses_unconfined_root(run_cli, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    sessions = tmp_path / "sessions"
    runs.mkdir()
    sessions.mkdir()

    completed = run_cli(
        ["cleanup", "--runs-dir", str(runs), "--sessions-dir", str(sessions),
         "--max-age-days", "1"]
    )

    assert completed.returncode == 1
    assert "retention error" in completed.stderr.lower()


def test_cleanup_cli_never_removes_a_session(run_cli, tmp_path: Path) -> None:
    runs, sessions = _artifact_runs_sessions(tmp_path)
    durable = _aged_session(sessions, "sess-durable", age_days=4000)

    applied = run_cli(
        ["cleanup", "--runs-dir", str(runs), "--sessions-dir", str(sessions),
         "--max-age-days", "1", "--apply"]
    )

    assert applied.returncode == 0, applied.stderr
    result = json.loads(applied.stdout)
    assert result["applied"] is True
    # A Session is unconditionally durable: never removed, forced to skip.
    assert "sess-durable" not in result["pruned"]
    skip = {c["id"]: c for c in result["plan"]["skip"]}
    assert skip["sess-durable"]["reason"] == "session_durable"
    assert (durable / "session.json").exists()


def test_cleanup_cli_rejects_removed_include_open_flag(run_cli, tmp_path: Path) -> None:
    runs, sessions = _artifact_runs_sessions(tmp_path)

    completed = run_cli(
        ["cleanup", "--runs-dir", str(runs), "--sessions-dir", str(sessions),
         "--max-age-days", "1", "--include-open"]
    )

    # The flag was removed for H1; argparse refuses it as an unknown option.
    assert completed.returncode == 2
    assert "include-open" in (completed.stdout + completed.stderr).lower()


def test_run_refuses_bad_mcp_config_before_launch(
    run_cli, tmp_path: Path, valid_role_dict: dict[str, Any], prompt_file: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role_payload = copy.deepcopy(valid_role_dict)
    role_payload["workspace"]["default_cwd"] = str(work)
    role_payload["workspace"]["allowed_roots"] = [str(work)]
    role_payload["runner"]["mcp_config"] = str(tmp_path / "absent-mcp.json")
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
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert completed.returncode == 1
    assert "mcp config error" in completed.stderr
    # Fails closed before launch: no run artifacts are created.
    assert not runs_dir.exists() or not any(runs_dir.iterdir())

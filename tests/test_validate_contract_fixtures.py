from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts.validate_contract_fixtures import (
    find_secret_like_values,
    parse_json_lines,
    validate_command_argv,
    validate_manifest,
    validate_session_command_argv,
    validate_session_contract,
)

REPO_FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "acpx-0.10.0"


def _session_prompt_argv(prompt: str, *, scratch: str | None = None) -> list[str]:
    cwd = scratch or "/repo/.tmp/acpx-session-contract-scratch/persistent-session"
    return [
        "npx",
        "-y",
        "acpx@0.10.0",
        "--format",
        "json",
        "--json-strict",
        "--suppress-reads",
        "--timeout",
        "180",
        "--max-turns",
        "1",
        "--cwd",
        cwd,
        "--deny-all",
        "--non-interactive-permissions",
        "fail",
        "--no-terminal",
        "--model",
        "gpt-5.5[low]",
        "codex",
        "prompt",
        "-s",
        "s1a-session-contract",
        prompt,
    ]


def test_parse_json_lines_rejects_malformed_line(tmp_path: Path) -> None:
    fixture = tmp_path / "stdout.ndjson"
    fixture.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")

    try:
        parse_json_lines(fixture)
    except ValueError as exc:
        assert "line 2" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("malformed NDJSON should fail closed")


def test_secret_scan_detects_common_token_shapes(tmp_path: Path) -> None:
    fixture = tmp_path / "stderr.log"
    fixture.write_text("Authorization: " + "Bearer " + "sk-" + "test_should_not_be_here", encoding="utf-8")

    findings = find_secret_like_values(tmp_path)

    assert findings
    assert findings[0].path == fixture
    assert "Bearer" in findings[0].pattern_name


def test_validate_manifest_fails_closed_without_core_contract_fields(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "runner_flags_family": ["--format", "json"],
                "fixtures": [],
            }
        ),
        encoding="utf-8",
    )

    errors = validate_manifest(tmp_path)

    assert any("runner_flags_family" in error for error in errors)
    assert any("schema_summary" in error for error in errors)
    assert any("permission_policy_fixture" in error for error in errors)
    assert any("path_enforcement_conclusion" in error for error in errors)
    assert any("manifest.fixtures" in error for error in errors)


def test_validate_command_argv_rejects_misordered_exec_flags() -> None:
    errors = validate_command_argv(
        "success-codex-sentinel",
        [
            "npx",
            "-y",
            "acpx@0.10.0",
            "--json-strict",
            "--format",
            "json",
            "--suppress-reads",
            "--timeout",
            "180",
            "--max-turns",
            "1",
            "--cwd",
            "/tmp/.tmp/acpx-contract-scratch/success-codex-sentinel",
            "--deny-all",
            "--non-interactive-permissions",
            "fail",
            "--no-terminal",
            "--model",
            "gpt-5.5[low]",
            "codex",
            "exec",
            "hello",
        ],
    )

    assert any("ordered JSON/timeout/max-turns/cwd grammar" in error for error in errors)


def test_validate_manifest_checks_management_status_no_session(tmp_path: Path) -> None:
    root = tmp_path
    fixture_names = [
        "success-codex-sentinel",
        "permission-policy-deny-all-sentinel",
        "usage-error-invalid-flag",
        "timeout-hanging-agent",
        "runtime-error-agent",
        "permission-denied-codex-read",
        "management-no-session-exit4",
        "management-status-no-session-exit0",
    ]
    manifest = {
        "runner_flags_family": ["--format", "json", "--json-strict", "--suppress-reads", "--timeout", "1", "--max-turns", "1"],
        "schema_summary": {"jsonrpc_present": True, "methods": ["initialize", "session/new", "session/prompt"]},
        "permission_policy_fixture": {"fixture": "permission-policy-deny-all-sentinel"},
        "path_enforcement_conclusion": {"v0_1a_rule": "Treat allowed_roots as cwd/config validation only."},
        "fixtures": [{"name": name, "expected_exit": 0} for name in fixture_names],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in fixture_names:
        fixture = root / name
        fixture.mkdir()
        (fixture / "metadata.json").write_text(json.dumps({"stdout_file": "stdout.ndjson"}), encoding="utf-8")
        (fixture / "result.json").write_text(json.dumps({"exit_code": 0}), encoding="utf-8")
        (fixture / "stdout.ndjson").write_text('{"jsonrpc":"2.0"}\n', encoding="utf-8")
        if name in {
            "success-codex-sentinel",
            "permission-policy-deny-all-sentinel",
            "timeout-hanging-agent",
            "runtime-error-agent",
            "permission-denied-codex-read",
        }:
            argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--suppress-reads", "--timeout", "1", "--max-turns", "1", "--cwd", f"/tmp/.tmp/acpx-contract-scratch/{name}", "exec", "hello"]
            if name == "success-codex-sentinel":
                argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--suppress-reads", "--timeout", "180", "--max-turns", "1", "--cwd", f"/tmp/.tmp/acpx-contract-scratch/{name}", "--deny-all", "--non-interactive-permissions", "fail", "--no-terminal", "--model", "gpt-5.5[low]", "codex", "exec", "hello"]
            elif name == "permission-denied-codex-read":
                argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--suppress-reads", "--timeout", "180", "--max-turns", "3", "--cwd", f"/tmp/.tmp/acpx-contract-scratch/{name}", "--deny-all", "--non-interactive-permissions", "fail", "--no-terminal", "--model", "gpt-5.5[low]", "codex", "exec", "hello"]
            elif name == "runtime-error-agent":
                argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--suppress-reads", "--timeout", "10", "--max-turns", "1", "--cwd", f"/tmp/.tmp/acpx-contract-scratch/{name}", "--agent", "node /tmp/tools/fake-agents/exit-before-initialize.mjs", "exec", "hello"]
            elif name == "timeout-hanging-agent":
                argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--suppress-reads", "--timeout", "1", "--max-turns", "1", "--cwd", f"/tmp/.tmp/acpx-contract-scratch/{name}", "--agent", "node /tmp/tools/fake-agents/hang-agent.mjs", "exec", "hello"]
            elif name == "permission-policy-deny-all-sentinel":
                argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--suppress-reads", "--timeout", "180", "--max-turns", "1", "--cwd", f"/tmp/.tmp/acpx-contract-scratch/{name}", "--permission-policy", json.dumps({"defaultAction": "deny", "autoDeny": ["read"]}), "--non-interactive-permissions", "fail", "--no-terminal", "--model", "gpt-5.5[low]", "codex", "exec", "hello"]
        elif name == "usage-error-invalid-flag":
            argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--bad-flag"]
        elif name == "management-no-session-exit4":
            argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--cwd", "/tmp/.tmp/acpx-contract-scratch/management-no-session", "codex", "-s", "definitely-missing-session", "--no-wait", "hello"]
        else:
            argv = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--cwd", "/tmp/.tmp/acpx-contract-scratch/management-no-session", "codex", "-s", "definitely-missing-session", "status"]
        (fixture / "command.argv.json").write_text(json.dumps(argv), encoding="utf-8")
    status_dir = root / "management-status-no-session-exit0"
    (status_dir / "metadata.json").write_text(json.dumps({"stdout_file": "stdout.json"}), encoding="utf-8")
    (status_dir / "stdout.json").write_text(json.dumps({"status": "active"}), encoding="utf-8")

    errors = validate_manifest(root)

    assert "management-status-no-session-exit0 must report status=no-session" in errors


# --- S1a persistent-session contract spike --------------------------------


def test_validate_session_command_argv_accepts_management_grammar() -> None:
    argv = [
        "npx",
        "-y",
        "acpx@0.10.0",
        "--format",
        "json",
        "--json-strict",
        "--cwd",
        "/repo/.tmp/acpx-session-contract-scratch/persistent-session",
        "codex",
        "sessions",
        "new",
        "--name",
        "s1a-session-contract",
    ]

    assert validate_session_command_argv("session-new-named", argv) == []


def test_validate_session_command_argv_accepts_prompt_grammar() -> None:
    argv = _session_prompt_argv("Persistent session contract turn 1. Reply exactly: S1A_SESSION_TURN_1_OK")

    assert validate_session_command_argv("session-prompt-turn1", argv) == []


def test_validate_session_command_argv_rejects_wrong_session_name() -> None:
    argv = [
        "npx",
        "-y",
        "acpx@0.10.0",
        "--format",
        "json",
        "--json-strict",
        "--cwd",
        "/repo/.tmp/acpx-session-contract-scratch/persistent-session",
        "codex",
        "sessions",
        "close",
        "some-other-session",
    ]

    errors = validate_session_command_argv("session-close-named", argv)

    assert any("session management command tail" in error for error in errors)


def test_validate_session_command_argv_rejects_misordered_prompt_flags() -> None:
    argv = _session_prompt_argv("hello")
    # Swap --format/--json-strict ordering to break the strict grammar.
    argv[3], argv[5] = argv[5], argv[3]

    errors = validate_session_command_argv("session-prompt-turn1", argv)

    assert any("ordered" in error for error in errors)


def test_validate_session_command_argv_rejects_extra_prompt_argument() -> None:
    argv = _session_prompt_argv("hello")
    argv.append("unexpected-second-prompt")

    errors = validate_session_command_argv("session-prompt-turn1", argv)

    assert any("exactly one prompt argument" in error for error in errors)


def test_validate_session_contract_passes_on_captured_fixtures() -> None:
    errors = validate_session_contract(REPO_FIXTURES_ROOT)

    assert errors == []


def test_validate_session_contract_detects_turn2_setup_pollution(tmp_path: Path) -> None:
    root = tmp_path / "acpx-0.10.0"
    shutil.copytree(REPO_FIXTURES_ROOT, root)

    turn2 = root / "session-prompt-turn2" / "stdout.ndjson"
    polluted = turn2.read_text(encoding="utf-8")
    polluted += json.dumps({"jsonrpc": "2.0", "id": 99, "method": "initialize", "params": {}}) + "\n"
    turn2.write_text(polluted, encoding="utf-8")

    # Keep the declared summary in sync so the ONLY failing check is the
    # follow-up-stream rule, not the summary cross-check.
    summary_path = root / "session-contract-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["schema"]["turn2"]["methods"] = sorted(
        set(summary["schema"]["turn2"]["methods"]) | {"initialize"}
    )
    summary["fixtures"]["session-prompt-turn2"]["stdout_lines"] += 1
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    errors = validate_session_contract(root)

    assert any("session-prompt-turn2" in error and "follow-up" in error for error in errors)


def test_validate_session_contract_detects_flipped_created_flag(tmp_path: Path) -> None:
    root = tmp_path / "acpx-0.10.0"
    shutil.copytree(REPO_FIXTURES_ROOT, root)

    new_stdout = root / "session-new-named" / "stdout.json"
    payload = json.loads(new_stdout.read_text(encoding="utf-8"))
    payload["created"] = False
    new_stdout.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_session_contract(root)

    assert any("session-new-named" in error and "created" in error for error in errors)


def test_validate_session_contract_detects_summary_text_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "acpx-0.10.0"
    shutil.copytree(REPO_FIXTURES_ROOT, root)

    summary_path = root / "session-contract-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["schema"]["turn1"]["joined_agent_text"] = "WRONG_SENTINEL"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    errors = validate_session_contract(root)

    assert any("summary" in error and "turn1" in error for error in errors)


def test_validate_session_contract_detects_exit_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "acpx-0.10.0"
    shutil.copytree(REPO_FIXTURES_ROOT, root)

    result_path = root / "session-close-named" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["exit_code"] = 1
    result_path.write_text(json.dumps(result), encoding="utf-8")

    errors = validate_session_contract(root)

    assert any("session-close-named" in error and "exit_code" in error for error in errors)

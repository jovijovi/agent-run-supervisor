from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_contract_fixtures import (
    find_secret_like_values,
    parse_json_lines,
    validate_command_argv,
    validate_manifest,
)


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

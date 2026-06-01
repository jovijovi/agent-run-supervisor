from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Bearer token", re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[^\s]+")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    ("PEM private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

TEXT_SUFFIXES = {".json", ".jsonl", ".ndjson", ".log", ".md", ".txt"}


@dataclass(frozen=True)
class SecretFinding:
    path: Path
    pattern_name: str
    line_number: int


def validate_command_argv(name: str, argv: list[str]) -> list[str]:
    errors: list[str] = []
    acpx_prefix = ["npx", "-y", "acpx@0.10.0"]
    if argv[:3] != acpx_prefix:
        errors.append(f"{name}: command argv must start with npx -y acpx@0.10.0")
        return errors

    if name == "usage-error-invalid-flag":
        expected = acpx_prefix + ["--format", "json", "--json-strict", "--bad-flag"]
        if argv != expected:
            errors.append(f"{name}: command argv must exactly match invalid-flag usage fixture grammar")
        return errors

    if name in {"management-no-session-exit4", "management-status-no-session-exit0"}:
        expected_tail = ["codex", "-s", "definitely-missing-session"]
        expected_end = ["--no-wait", "hello"] if name == "management-no-session-exit4" else ["status"]
        expected_head = acpx_prefix + ["--format", "json", "--json-strict", "--cwd"]
        if argv[:7] != expected_head:
            errors.append(f"{name}: management command argv has wrong prefix/format/cwd grammar")
            return errors
        if not argv[7].endswith("/.tmp/acpx-contract-scratch/management-no-session"):
            errors.append(f"{name}: management --cwd must point at management-no-session scratch dir")
        if argv[8:] != expected_tail + expected_end:
            errors.append(f"{name}: management command tail must exactly match expected session command")
        return errors

    expected_exec = {
        "success-codex-sentinel": ("180", "1", "success-codex-sentinel"),
        "permission-policy-deny-all-sentinel": ("180", "1", "permission-policy-deny-all-sentinel"),
        "timeout-hanging-agent": ("1", "1", "timeout-hanging-agent"),
        "runtime-error-agent": ("10", "1", "runtime-error-agent"),
        "permission-denied-codex-read": ("180", "3", "permission-denied-codex-read"),
    }
    if name not in expected_exec:
        errors.append(f"{name}: unknown fixture command grammar")
        return errors

    timeout_value, max_turns_value, scratch_name = expected_exec[name]
    expected_head = acpx_prefix + [
        "--format",
        "json",
        "--json-strict",
        "--suppress-reads",
        "--timeout",
        timeout_value,
        "--max-turns",
        max_turns_value,
        "--cwd",
    ]
    if argv[:12] != expected_head:
        errors.append(f"{name}: exec command argv has wrong ordered JSON/timeout/max-turns/cwd grammar")
        return errors
    if not argv[12].endswith(f"/.tmp/acpx-contract-scratch/{scratch_name}"):
        errors.append(f"{name}: exec --cwd must point at {scratch_name} scratch dir")
    tail = argv[13:]

    if name == "permission-policy-deny-all-sentinel":
        expected_tail_prefix = ["--permission-policy"]
        if tail[:1] != expected_tail_prefix:
            errors.append(f"{name}: permission-policy fixture must pass --permission-policy immediately after --cwd")
        else:
            try:
                policy = json.loads(tail[1])
            except (IndexError, json.JSONDecodeError) as exc:
                errors.append(f"{name}: --permission-policy value must be JSON: {exc}")
                return errors
            if policy.get("defaultAction") != "deny" or "read" not in policy.get("autoDeny", []):
                errors.append(f"{name}: --permission-policy JSON must deny by default and include read in autoDeny")
            tail = tail[2:]
        expected_codex_tail = ["--non-interactive-permissions", "fail", "--no-terminal", "--model", "gpt-5.5[low]", "codex", "exec"]
        if tail[:7] != expected_codex_tail or len(tail) != 8:
            errors.append(f"{name}: permission-policy codex exec tail must exactly match expected grammar")
        return errors

    if name in {"success-codex-sentinel", "permission-denied-codex-read"}:
        expected_codex_tail = ["--deny-all", "--non-interactive-permissions", "fail", "--no-terminal", "--model", "gpt-5.5[low]", "codex", "exec"]
        if tail[:8] != expected_codex_tail or len(tail) != 9:
            errors.append(f"{name}: codex exec tail must exactly match expected deny-all grammar")
        return errors

    if name == "timeout-hanging-agent":
        expected_agent = "node "
        if tail[:1] != ["--agent"] or len(tail) != 4 or not tail[1].startswith(expected_agent) or not tail[1].endswith("/tools/fake-agents/hang-agent.mjs") or tail[2:] != ["exec", "hello"]:
            errors.append(f"{name}: hanging custom-agent command tail must exactly match expected grammar")
        return errors

    if name == "runtime-error-agent":
        expected_agent = "node "
        if tail[:1] != ["--agent"] or len(tail) != 4 or not tail[1].startswith(expected_agent) or not tail[1].endswith("/tools/fake-agents/exit-before-initialize.mjs") or tail[2:] != ["exec", "hello"]:
            errors.append(f"{name}: runtime-error custom-agent command tail must exactly match expected grammar")
        return errors

    return errors


def parse_json_lines(path: Path) -> list[Any]:
    records: list[Any] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON on line {line_number}: {exc.msg}") from exc
    return records


def find_secret_like_values(root: Path) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for pattern_name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(SecretFinding(path=path, pattern_name=pattern_name, line_number=line_number))
    return findings


def validate_manifest(root: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return [f"missing {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    flags = manifest.get("runner_flags_family")
    required_flags = ["--format", "json", "--json-strict", "--suppress-reads", "--timeout", "--max-turns"]
    if not isinstance(flags, list) or any(flag not in flags for flag in required_flags):
        errors.append("manifest.runner_flags_family must include exact V0.1a JSON/timeout/max-turns flags")

    schema_summary = manifest.get("schema_summary")
    if not isinstance(schema_summary, dict):
        errors.append("manifest.schema_summary must be present")
    else:
        if schema_summary.get("jsonrpc_present") is not True:
            errors.append("schema_summary.jsonrpc_present must be true for observed acpx JSON stdout")
        methods = set(schema_summary.get("methods") or [])
        for required_method in {"initialize", "session/new", "session/prompt"}:
            if required_method not in methods:
                errors.append(f"schema_summary.methods missing {required_method}")

    permission_policy = manifest.get("permission_policy_fixture")
    if not isinstance(permission_policy, dict) or permission_policy.get("fixture") != "permission-policy-deny-all-sentinel":
        errors.append("manifest.permission_policy_fixture must reference permission-policy-deny-all-sentinel")

    path_finding = manifest.get("path_enforcement_conclusion")
    if not isinstance(path_finding, dict) or "allowed_roots" not in str(path_finding.get("v0_1a_rule", "")):
        errors.append("manifest.path_enforcement_conclusion must state the V0.1a allowed_roots boundary")

    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        errors.append("manifest.fixtures must be a non-empty list")
        return errors
    names = {fixture.get("name") for fixture in fixtures if isinstance(fixture, dict)}
    required_names = {
        "success-codex-sentinel",
        "permission-policy-deny-all-sentinel",
        "usage-error-invalid-flag",
        "timeout-hanging-agent",
        "runtime-error-agent",
        "permission-denied-codex-read",
        "management-no-session-exit4",
        "management-status-no-session-exit0",
    }
    for name in sorted(required_names - names):
        errors.append(f"manifest.fixtures missing {name}")

    for fixture in fixtures:
        if not isinstance(fixture, dict):
            errors.append("manifest fixture entry must be object")
            continue
        name = fixture.get("name")
        expected_exit = fixture.get("expected_exit")
        if not isinstance(name, str) or not name:
            errors.append("fixture entry missing name")
            continue
        result_path = root / name / "result.json"
        if not result_path.exists():
            errors.append(f"{name}: missing result.json")
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        actual_exit = result.get("exit_code")
        if actual_exit != expected_exit:
            errors.append(f"{name}: exit_code {actual_exit!r} != expected {expected_exit!r}")

        command_path = root / name / "command.argv.json"
        if not command_path.exists():
            errors.append(f"{name}: missing command.argv.json")
            continue
        argv = json.loads(command_path.read_text(encoding="utf-8"))
        if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
            errors.append(f"{name}: command.argv.json must be a string array")
            continue

        errors.extend(validate_command_argv(name, argv))

        stdout_name = json.loads((root / name / "metadata.json").read_text(encoding="utf-8")).get("stdout_file", "stdout.ndjson")
        stdout_path = root / name / stdout_name
        if stdout_path.exists() and stdout_path.suffix in {".ndjson", ".json"}:
            if stdout_path.suffix == ".ndjson":
                try:
                    parse_json_lines(stdout_path)
                except ValueError as exc:
                    errors.append(str(exc))
            else:
                try:
                    json.loads(stdout_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    errors.append(f"{stdout_path}: invalid JSON: {exc.msg}")

    status_path = root / "management-status-no-session-exit0" / "stdout.json"
    if status_path.exists():
        status_payload = json.loads(status_path.read_text(encoding="utf-8"))
        if status_payload.get("status") != "no-session":
            errors.append("management-status-no-session-exit0 must report status=no-session")
    return errors


# --- S1a persistent-session contract spike --------------------------------
#
# These fixtures prove the observed acpx@0.10.0 *persistent-session* command
# grammar and stdout schemas. They are contract evidence only; S1 session
# support is not implemented. Management commands emit a single summarized JSON
# object, while prompt turns emit a raw newline-delimited ACP/JSON-RPC stream,
# so the two schema families are validated separately and never conflated.

SESSION_NAME = "s1a-session-contract"
SESSION_SCRATCH_SUFFIX = "/.tmp/acpx-session-contract-scratch/persistent-session"

# Exact ordered command tail (everything after `--cwd <scratch>`) per management
# fixture. Note `status`/`cancel` are top-level codex verbs (`-s <name>`), not
# `sessions <verb>`.
SESSION_MANAGEMENT_TAILS: dict[str, list[str]] = {
    "session-new-named": ["codex", "sessions", "new", "--name", SESSION_NAME],
    "session-ensure-existing": ["codex", "sessions", "ensure", "--name", SESSION_NAME],
    "session-show-open": ["codex", "sessions", "show", SESSION_NAME],
    "session-show-after-turns": ["codex", "sessions", "show", SESSION_NAME],
    "session-show-closed": ["codex", "sessions", "show", SESSION_NAME],
    "session-history-after-turns": ["codex", "sessions", "history", "--limit", "8", SESSION_NAME],
    "session-read-tail-after-turns": ["codex", "sessions", "read", "--tail", "8", SESSION_NAME],
    "session-status-after-turns": ["codex", "status", "-s", SESSION_NAME],
    "session-cancel-no-active": ["codex", "cancel", "-s", SESSION_NAME],
    "session-close-named": ["codex", "sessions", "close", SESSION_NAME],
}

SESSION_PROMPT_FIXTURES: tuple[str, ...] = ("session-prompt-turn1", "session-prompt-turn2")

# Observed prompt-stream facts. turn2 is a *follow-up*: it reuses the existing
# ACP session and must NOT re-run initialize/session-new setup.
SESSION_PROMPT_FACTS: dict[str, dict[str, Any]] = {
    "session-prompt-turn1": {
        "required_methods": {"initialize", "session/new", "session/set_model", "session/prompt", "session/update"},
        "forbidden_methods": set(),
        "update_types": {"agent_message_chunk", "available_commands_update", "usage_update"},
        "joined_text": "S1A_SESSION_TURN_1_OK",
    },
    "session-prompt-turn2": {
        "required_methods": {"session/prompt", "session/update"},
        "forbidden_methods": {"initialize", "session/new"},
        "update_types": {"agent_message_chunk", "usage_update"},
        "joined_text": "S1A_SESSION_TURN_2_OK",
    },
}

SESSION_PROMPT_HEAD = ["npx", "-y", "acpx@0.10.0"] + [
    "--format",
    "json",
    "--json-strict",
    "--suppress-reads",
    "--timeout",
    "180",
    "--max-turns",
    "1",
    "--cwd",
]
SESSION_PROMPT_TAIL = [
    "--deny-all",
    "--non-interactive-permissions",
    "fail",
    "--no-terminal",
    "--model",
    "gpt-5.5[low]",
    "codex",
    "prompt",
    "-s",
    SESSION_NAME,
]
SESSION_MANAGEMENT_HEAD = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict", "--cwd"]


def validate_session_command_argv(name: str, argv: list[str]) -> list[str]:
    """Validate the exact ordered argv grammar for an S1a session fixture."""
    errors: list[str] = []
    if argv[:3] != ["npx", "-y", "acpx@0.10.0"]:
        errors.append(f"{name}: session command argv must start with npx -y acpx@0.10.0")
        return errors

    if name in SESSION_PROMPT_FIXTURES:
        if argv[: len(SESSION_PROMPT_HEAD)] != SESSION_PROMPT_HEAD:
            errors.append(f"{name}: session prompt argv has wrong ordered JSON/timeout/max-turns/cwd grammar")
            return errors
        scratch = argv[len(SESSION_PROMPT_HEAD)]
        if not scratch.endswith(SESSION_SCRATCH_SUFFIX):
            errors.append(f"{name}: session prompt --cwd must point at the persistent-session scratch dir")
        tail = argv[len(SESSION_PROMPT_HEAD) + 1 :]
        if tail[: len(SESSION_PROMPT_TAIL)] != SESSION_PROMPT_TAIL:
            errors.append(f"{name}: session prompt tail must exactly match deny-all/model/codex prompt -s grammar")
            return errors
        if len(tail) != len(SESSION_PROMPT_TAIL) + 1:
            errors.append(f"{name}: session prompt must end with exactly one prompt argument")
        return errors

    if name in SESSION_MANAGEMENT_TAILS:
        if argv[: len(SESSION_MANAGEMENT_HEAD)] != SESSION_MANAGEMENT_HEAD:
            errors.append(f"{name}: session management argv has wrong prefix/format/cwd grammar")
            return errors
        scratch = argv[len(SESSION_MANAGEMENT_HEAD)]
        if not scratch.endswith(SESSION_SCRATCH_SUFFIX):
            errors.append(f"{name}: session management --cwd must point at the persistent-session scratch dir")
        expected_tail = SESSION_MANAGEMENT_TAILS[name]
        if argv[len(SESSION_MANAGEMENT_HEAD) + 1 :] != expected_tail:
            errors.append(
                f"{name}: session management command tail must exactly match {' '.join(expected_tail)}"
            )
        return errors

    errors.append(f"{name}: unknown session fixture command grammar")
    return errors


def summarize_prompt_stream(records: list[Any]) -> tuple[set[str], set[str], set[str], str]:
    """Derive (methods, update types, session ids, joined agent text) from a stream."""
    methods: set[str] = set()
    update_types: set[str] = set()
    session_ids: set[str] = set()
    texts: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        method = record.get("method")
        if isinstance(method, str):
            methods.add(method)
        params = record.get("params")
        if not isinstance(params, dict):
            continue
        session_id = params.get("sessionId")
        if isinstance(session_id, str):
            session_ids.add(session_id)
        update = params.get("update")
        if not isinstance(update, dict):
            continue
        update_type = update.get("sessionUpdate")
        if isinstance(update_type, str):
            update_types.add(update_type)
        if update_type == "agent_message_chunk":
            content = update.get("content")
            if isinstance(content, dict) and content.get("type") == "text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
    return methods, update_types, session_ids, "".join(texts)


def _validate_prompt_facts(name: str, records: list[Any]) -> list[str]:
    errors: list[str] = []
    facts = SESSION_PROMPT_FACTS[name]
    methods, update_types, session_ids, joined = summarize_prompt_stream(records)

    missing_methods = facts["required_methods"] - methods
    if missing_methods:
        errors.append(f"{name}: prompt stream missing required methods {sorted(missing_methods)}")
    present_forbidden = facts["forbidden_methods"] & methods
    if present_forbidden:
        errors.append(
            f"{name}: follow-up prompt stream must not contain setup methods {sorted(present_forbidden)}"
        )
    missing_updates = facts["update_types"] - update_types
    if missing_updates:
        errors.append(f"{name}: prompt stream missing update types {sorted(missing_updates)}")
    if joined != facts["joined_text"]:
        errors.append(f"{name}: joined agent text {joined!r} != expected {facts['joined_text']!r}")
    if len(session_ids) != 1:
        errors.append(f"{name}: prompt stream must use exactly one ACP session id, found {len(session_ids)}")
    return errors


def _validate_management_semantics(name: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if name == "session-new-named":
        if payload.get("action") != "session_ensured":
            errors.append(f"{name}: management action must be session_ensured")
        if payload.get("created") is not True:
            errors.append(f"{name}: a fresh named session must report created=true")
    elif name == "session-ensure-existing":
        if payload.get("action") != "session_ensured":
            errors.append(f"{name}: management action must be session_ensured")
        if payload.get("created") is not False:
            errors.append(f"{name}: ensuring an existing session must report created=false")
    elif name == "session-show-open":
        if payload.get("schema") != "acpx.session.v1":
            errors.append(f"{name}: session show must use schema acpx.session.v1")
        if payload.get("closed") is not False:
            errors.append(f"{name}: an open session must report closed=false")
    elif name == "session-show-closed":
        if payload.get("schema") != "acpx.session.v1":
            errors.append(f"{name}: session show must use schema acpx.session.v1")
        if payload.get("closed") is not True:
            errors.append(f"{name}: a closed session must report closed=true")
        else:
            closed_at = payload.get("closedAt")
            if not isinstance(closed_at, str) or not closed_at:
                errors.append(f"{name}: a closed session must record a non-empty closedAt timestamp")
    elif name == "session-show-after-turns":
        if payload.get("schema") != "acpx.session.v1":
            errors.append(f"{name}: session show must use schema acpx.session.v1")
        messages = payload.get("messages")
        if not isinstance(messages, list) or len(messages) == 0:
            errors.append(f"{name}: session show after turns must record a nonzero message count")
        last_seq = payload.get("lastSeq")
        if not isinstance(last_seq, int) or isinstance(last_seq, bool) or last_seq <= 0:
            errors.append(f"{name}: session show after turns must record a nonzero lastSeq")
    elif name == "session-status-after-turns":
        if payload.get("action") != "status_snapshot":
            errors.append(f"{name}: status command must report action=status_snapshot")
        if payload.get("status") != "alive":
            errors.append(f"{name}: status after turns must report an alive queue owner")
    elif name == "session-cancel-no-active":
        if payload.get("action") != "cancel_result":
            errors.append(f"{name}: cancel command must report action=cancel_result")
        if payload.get("cancelled") is not False:
            errors.append(f"{name}: idle cancel must report cancelled=false (cancel is not close)")
    elif name == "session-close-named":
        if payload.get("action") != "session_closed":
            errors.append(f"{name}: close command must report action=session_closed")
    elif name in {"session-history-after-turns", "session-read-tail-after-turns"}:
        entries = payload.get("entries")
        if not isinstance(entries, list) or not entries:
            errors.append(f"{name}: history/read must return a nonempty entries list")
        count = payload.get("count")
        if not isinstance(count, int) or count <= 0:
            errors.append(f"{name}: history/read must report a positive count")
        if not isinstance(payload.get("sessionId"), str) or not payload.get("sessionId"):
            errors.append(f"{name}: history/read must reference the persistent sessionId")
    return errors


def _nonempty_line_count(path: Path) -> int:
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def _validate_session_summary(root: Path) -> list[str]:
    """Cross-check session-contract-summary.json against the captured streams."""
    errors: list[str] = []
    summary_path = root / "session-contract-summary.json"
    if not summary_path.exists():
        return [f"missing {summary_path}"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    schema = summary.get("schema")
    if not isinstance(schema, dict):
        errors.append("session-contract-summary.json must contain a schema object")
        return errors

    observed_ids: list[set[str]] = []
    for name in SESSION_PROMPT_FIXTURES:
        turn_key = "turn1" if name.endswith("turn1") else "turn2"
        declared = schema.get(turn_key)
        if not isinstance(declared, dict):
            errors.append(f"session-contract-summary.json schema.{turn_key} must be an object")
            continue
        stdout_path = root / name / "stdout.ndjson"
        if not stdout_path.exists():
            errors.append(f"summary cross-check ({turn_key}): {name} stdout.ndjson missing")
            continue
        try:
            records = parse_json_lines(stdout_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        methods, update_types, session_ids, joined = summarize_prompt_stream(records)
        observed_ids.append(session_ids)
        if declared.get("joined_agent_text") != joined:
            errors.append(
                f"summary cross-check ({turn_key}): joined_agent_text {declared.get('joined_agent_text')!r} "
                f"!= observed {joined!r}"
            )
        if set(declared.get("methods") or []) != methods:
            errors.append(f"summary cross-check ({turn_key}): methods do not match observed prompt stream")
        if set(declared.get("session_update_types") or []) != update_types:
            errors.append(
                f"summary cross-check ({turn_key}): session_update_types do not match observed prompt stream"
            )
        if declared.get("unique_session_id_count") != len(session_ids):
            errors.append(
                f"summary cross-check ({turn_key}): unique_session_id_count "
                f"{declared.get('unique_session_id_count')!r} != observed {len(session_ids)}"
            )

    # Continuity: the two prompt turns must reuse the SAME single ACP session id.
    if len(observed_ids) == 2 and not (observed_ids[0] and observed_ids[0] == observed_ids[1]):
        errors.append("summary cross-check: turn1 and turn2 must reuse the same ACP session id (continuity)")

    fixtures = summary.get("fixtures")
    if not isinstance(fixtures, dict):
        errors.append("session-contract-summary.json must contain a fixtures object")
        return errors
    for name, declared in fixtures.items():
        if not isinstance(declared, dict):
            errors.append(f"summary cross-check: fixtures.{name} must be an object")
            continue
        result_path = root / name / "result.json"
        if not result_path.exists():
            errors.append(f"summary cross-check: {name} result.json missing")
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if declared.get("exit_code") != result.get("exit_code"):
            errors.append(f"summary cross-check ({name}): exit_code disagrees with result.json")
        stdout_file = declared.get("stdout_file")
        if stdout_file:
            stdout_path = root / name / stdout_file
            if not stdout_path.exists():
                errors.append(f"summary cross-check ({name}): declared stdout_file {stdout_file} missing")
            elif declared.get("stdout_lines") != _nonempty_line_count(stdout_path):
                errors.append(f"summary cross-check ({name}): stdout_lines disagrees with captured stream")
    return errors


def validate_session_contract(root: Path) -> list[str]:
    """Fail-closed validation of the S1a persistent-session contract fixtures."""
    errors: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return [f"missing {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    contract = manifest.get("session_contract")
    if not isinstance(contract, dict):
        return ["manifest.session_contract section must be present for the S1a contract spike"]

    # S1a is contract evidence only. Product/runtime persistent-session support
    # has since been implemented elsewhere; this fixture set must still avoid
    # claiming that the fixtures themselves implement runtime behavior.
    if contract.get("contract_evidence_only") is not True:
        errors.append(
            "manifest.session_contract.contract_evidence_only must be true (S1a is evidence only)"
        )
    if "implemented" in contract:
        errors.append(
            "manifest.session_contract.implemented is a stale legacy key; use "
            "runtime_implementation_in_fixture_set instead"
        )
    if contract.get("runtime_implementation_in_fixture_set") is not False:
        errors.append(
            "manifest.session_contract.runtime_implementation_in_fixture_set must be false "
            "(S1a fixtures are evidence only; runtime support lives in source code)"
        )
    if contract.get("session_name") != SESSION_NAME:
        errors.append(f"manifest.session_contract.session_name must be {SESSION_NAME!r}")
    if contract.get("summary_file") != "session-contract-summary.json":
        errors.append("manifest.session_contract.summary_file must reference session-contract-summary.json")

    declared = contract.get("fixtures")
    if not isinstance(declared, list) or not declared:
        errors.append("manifest.session_contract.fixtures must be a non-empty list")
        return errors

    declared_by_name: dict[str, dict[str, Any]] = {}
    for entry in declared:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            errors.append("manifest.session_contract.fixtures entry must be an object with a name")
            continue
        declared_by_name[entry["name"]] = entry

    expected_names = set(SESSION_MANAGEMENT_TAILS) | set(SESSION_PROMPT_FIXTURES)
    for missing in sorted(expected_names - set(declared_by_name)):
        errors.append(f"manifest.session_contract.fixtures missing {missing}")
    for unexpected in sorted(set(declared_by_name) - expected_names):
        errors.append(f"manifest.session_contract.fixtures has unknown fixture {unexpected}")

    for name in sorted(expected_names):
        entry = declared_by_name.get(name)
        if entry is None:
            continue
        fixture_dir = root / name
        if not fixture_dir.is_dir():
            errors.append(f"{name}: missing session fixture directory")
            continue
        expected_exit = entry.get("expected_exit")

        result_path = fixture_dir / "result.json"
        command_path = fixture_dir / "command.argv.json"
        metadata_path = fixture_dir / "metadata.json"
        if not metadata_path.exists():
            errors.append(f"{name}: missing metadata.json")

        if not result_path.exists():
            errors.append(f"{name}: missing result.json")
        else:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("exit_code") != expected_exit:
                errors.append(f"{name}: exit_code {result.get('exit_code')!r} != expected {expected_exit!r}")

        if not command_path.exists():
            errors.append(f"{name}: missing command.argv.json")
        else:
            argv = json.loads(command_path.read_text(encoding="utf-8"))
            if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
                errors.append(f"{name}: command.argv.json must be a string array")
            else:
                errors.extend(validate_session_command_argv(name, argv))

        if name in SESSION_PROMPT_FIXTURES:
            stdout_path = fixture_dir / "stdout.ndjson"
            if not stdout_path.exists():
                errors.append(f"{name}: missing stdout.ndjson prompt stream")
            else:
                try:
                    records = parse_json_lines(stdout_path)
                except ValueError as exc:
                    errors.append(str(exc))
                else:
                    errors.extend(_validate_prompt_facts(name, records))
        else:
            stdout_path = fixture_dir / "stdout.json"
            if not stdout_path.exists():
                errors.append(f"{name}: missing stdout.json management summary")
            else:
                try:
                    payload = json.loads(stdout_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    errors.append(f"{stdout_path}: invalid JSON: {exc.msg}")
                else:
                    if isinstance(payload, dict):
                        errors.extend(_validate_management_semantics(name, payload))
                    else:
                        errors.append(f"{name}: management stdout.json must be a JSON object")

    errors.extend(_validate_session_summary(root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate acpx contract fixtures")
    parser.add_argument("root", type=Path, help="Fixture root, e.g. fixtures/acpx-0.10.0")
    args = parser.parse_args()

    errors = validate_manifest(args.root)
    errors.extend(validate_session_contract(args.root))
    secret_findings = find_secret_like_values(args.root)
    for finding in secret_findings:
        errors.append(f"secret-like value: {finding.path}:{finding.line_number} {finding.pattern_name}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

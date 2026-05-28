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


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate acpx contract fixtures")
    parser.add_argument("root", type=Path, help="Fixture root, e.g. fixtures/acpx-0.10.0")
    args = parser.parse_args()

    errors = validate_manifest(args.root)
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

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
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        errors.append("manifest.fixtures must be a non-empty list")
        return errors
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

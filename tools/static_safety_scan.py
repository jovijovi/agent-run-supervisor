#!/usr/bin/env python3
"""Static safety scan for release/acceptance gates.

The scan is intentionally conservative and repo-specific:

- secret-shaped values across tracked text files, with documented synthetic
  redaction samples allow-listed only when nearby text marks them as fake;
- dangerous runtime calls/imports in ``src/`` via Python AST;
- stale acceptance phrases that previously survived green functional tests.

It is not a replacement for focused review; it is a CI backstop for the exact
classes of drift this repository has already hit.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".ndjson",
    ".svg",
    ".txt",
}
TEXT_NAMES = {"LICENSE", "AGENTS.md", "GOAL.md", "CLAUDE.md"}
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}

FORBIDDEN_IMPORT_ROOTS = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "feishu",
    "lark",
    "sachima",
    "gateway",
    "temporalio",
}

SYNTHETIC_SECRET_PATHS = {
    Path("src/agent_run_supervisor/preflight.py"),
    Path("docs/plans/2026-06-01-h1-operational-hardening.md"),
}

SECRET_PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    "github_pat": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "bearer_long": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{30,}\b"),
}

STALE_PATTERNS = {
    "ready_for_pr": re.compile(r"ready[- ]for[- ]PR", re.I),
    "implementation_candidate": re.compile(r"implementation candidate", re.I),
    "closure_requires_pr": re.compile(r"closure requires PR", re.I),
    "pr_ci_pending": re.compile(r"PR/CI pending|pending PR review|waiting for CI", re.I),
    "session_unimplemented_tail": re.compile(
        r"S1\s+(?:remain|remains)\s+Planned|"
        r"S1 session support .*unimplemented|"
        r"support remains Planned|"
        r"persistent-session runtime is not implemented",
        re.I,
    ),
    "red_expectation_tail": re.compile(r"RED\s+expectation", re.I),
    "old_test_count_455": re.compile(r"\b455\s+(?:passed|tests|collected)\b", re.I),
}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str
    snippet: str


def _line_number(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _snippet(text: str, line: int) -> str:
    lines = text.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:180]
    return ""


def iter_text_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES or path.name.startswith(".env"):
            yield path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _is_allowlisted_synthetic_secret(root: Path, path: Path, text: str, pos: int) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if rel not in SYNTHETIC_SECRET_PATHS:
        return False
    nearby = text[max(0, pos - 900) : pos].lower()
    return any(marker in nearby for marker in ("synthetic", "non-secret", "fabricated", "fake"))


def scan_secrets(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_text_files(root):
        text = _read(path)
        for name, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                if _is_allowlisted_synthetic_secret(root, path, text, match.start()):
                    continue
                line = _line_number(text, match.start())
                findings.append(Finding(str(path.relative_to(root)), line, f"secret:{name}", _snippet(text, line)))
    return findings


def _import_roots(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name.split(".")[0]
    elif isinstance(node, ast.ImportFrom):
        yield (node.module or "").split(".")[0]


def scan_source_ast(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in (root / "src").rglob("*.py"):
        rel = path.relative_to(root)
        text = _read(path)
        try:
            tree = ast.parse(text, filename=str(rel))
        except SyntaxError as exc:
            findings.append(Finding(str(rel), exc.lineno or 1, "syntax_error", exc.msg))
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _import_roots(node):
                    if module in FORBIDDEN_IMPORT_ROOTS:
                        findings.append(
                            Finding(str(rel), getattr(node, "lineno", 1), f"forbidden_import:{module}", _snippet(text, getattr(node, "lineno", 1)))
                        )
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    obj = func.value.id if isinstance(func.value, ast.Name) else None
                    if obj == "os" and func.attr == "system":
                        findings.append(Finding(str(rel), node.lineno, "dangerous_call:os.system", _snippet(text, node.lineno)))
                    if obj == "pickle" and func.attr in {"load", "loads"}:
                        findings.append(Finding(str(rel), node.lineno, f"dangerous_call:pickle.{func.attr}", _snippet(text, node.lineno)))
                elif isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
                    findings.append(Finding(str(rel), node.lineno, f"dangerous_call:{func.id}", _snippet(text, node.lineno)))
                for keyword in node.keywords:
                    if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                        findings.append(Finding(str(rel), node.lineno, "dangerous_call:shell=True", _snippet(text, node.lineno)))
    return findings


def scan_stale_phrases(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_text_files(root):
        rel = path.relative_to(root)
        if rel == Path("tools/static_safety_scan.py"):
            # This file defines the stale patterns; scan call sites and docs, not
            # the literal pattern declarations themselves.
            continue
        text = _read(path)
        for name, pattern in STALE_PATTERNS.items():
            for match in pattern.finditer(text):
                line = _line_number(text, match.start())
                snippet = _snippet(text, line)
                lowered = snippet.lower()
                if any(token in lowered for token in ("backlog", "not approved", "non-approval", "requires separate approval")):
                    continue
                findings.append(Finding(str(path.relative_to(root)), line, f"stale:{name}", snippet))
    return findings


def run_scan(root: Path) -> dict[str, object]:
    root = root.resolve()
    secret_findings = scan_secrets(root)
    ast_findings = scan_source_ast(root)
    stale_findings = scan_stale_phrases(root)
    findings = [*secret_findings, *ast_findings, *stale_findings]
    return {
        "ok": not findings,
        "counts": {
            "secret": len(secret_findings),
            "source_ast": len(ast_findings),
            "stale": len(stale_findings),
            "total": len(findings),
        },
        "findings": [asdict(finding) for finding in findings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repo-specific static safety scans.")
    parser.add_argument("root", nargs="?", default=".", help="Repository root to scan")
    args = parser.parse_args(argv)
    report = run_scan(Path(args.root))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

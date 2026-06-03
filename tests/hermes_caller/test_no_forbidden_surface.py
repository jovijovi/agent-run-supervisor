"""T8 — static forbidden-surface guard (plan §10-T8, §12).

A local ``ast``-based scanner proves the caller-side package introduces
no platform/live surface: no networking/SDK import, no supervisor-internal
import, and no delivery/platform token used as a real field, call, or function.

The scanner is validated against a deliberately-seeded scratch fixture
(``fixtures/static_violation.py``) so the guard is not vacuously green. It then
guards the real ``src/agent_run_supervisor/hermes_caller/`` tree, skipping only
when that package directory is absent from the checkout.

This module imports no ``hermes_caller`` symbol, so the scanner stays
collectable and exercises the seeded fixture independently of the package.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PACKAGE_DIR = REPO_ROOT / "src" / "agent_run_supervisor" / "hermes_caller"

# Networking / SDK import roots that may never be imported.
FORBIDDEN_IMPORT_ROOTS = {
    "socket",
    "http",
    "urllib",
    "requests",
    "feishu",
    "lark",
    "sdk",
}

# Supervisor internals the caller layer must never reach into directly.
FORBIDDEN_SUPERVISOR_INTERNALS = {
    "runner",
    "session_runtime",
    "parser",
    "policy",
    "session",
    "commands",
}

# Delivery/platform tokens that may appear only in comment/prose, never as a
# real AST name, attribute, or function.
FORBIDDEN_TOKENS = {
    "delivery",
    "webhook",
    "gateway",
    "recipient",
    "channel_id",
    "message_id",
    "send_card",
    "post_message",
    "public_ingress",
    "auto_reply",
}

SUPERVISOR_PKG = "agent_run_supervisor"


def _import_violations(node: ast.AST) -> list[str]:
    found: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] in FORBIDDEN_IMPORT_ROOTS:
                found.append(f"import {alias.name}")
            if parts[0] == SUPERVISOR_PKG and len(parts) > 1 and parts[1] in FORBIDDEN_SUPERVISOR_INTERNALS:
                found.append(f"import {alias.name}")
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        parts = module.split(".")
        if parts[0] in FORBIDDEN_IMPORT_ROOTS:
            found.append(f"from {module} import ...")
        if module == SUPERVISOR_PKG:
            for alias in node.names:
                if alias.name in FORBIDDEN_SUPERVISOR_INTERNALS:
                    found.append(f"from {module} import {alias.name}")
        elif parts[0] == SUPERVISOR_PKG and len(parts) > 1 and parts[1] in FORBIDDEN_SUPERVISOR_INTERNALS:
            found.append(f"from {module} import ...")
    return found


def _token_violations(node: ast.AST) -> list[str]:
    found: list[str] = []
    if isinstance(node, ast.Name) and node.id in FORBIDDEN_TOKENS:
        found.append(f"name {node.id}")
    elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_TOKENS:
        found.append(f"attribute .{node.attr}")
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FORBIDDEN_TOKENS:
        found.append(f"def {node.name}")
    elif isinstance(node, ast.arg) and node.arg in FORBIDDEN_TOKENS:
        found.append(f"arg {node.arg}")
    elif isinstance(node, ast.keyword) and node.arg in FORBIDDEN_TOKENS:
        found.append(f"keyword {node.arg}")
    return found


def scan_forbidden_surface(path: Path) -> list[str]:
    """Return a list of forbidden-surface violations found in a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        violations.extend(_import_violations(node))
        violations.extend(_token_violations(node))
    return violations


def test_scanner_catches_seeded_violation() -> None:
    violations = scan_forbidden_surface(FIXTURES_DIR / "static_violation.py")

    # The scratch fixture seeds every category the guard must reject.
    assert any("socket" in v for v in violations)
    assert any("urllib" in v for v in violations)
    assert any("session_runtime" in v for v in violations)
    assert any("send_card" in v for v in violations)
    assert any("post_message" in v for v in violations)
    assert any("webhook" in v for v in violations)


def test_real_package_is_clean() -> None:
    if not PACKAGE_DIR.is_dir():
        pytest.skip("hermes_caller package directory not present in this checkout")

    offenders: dict[str, list[str]] = {}
    for module_path in sorted(PACKAGE_DIR.glob("*.py")):
        violations = scan_forbidden_surface(module_path)
        if violations:
            offenders[module_path.name] = violations

    assert not offenders, f"forbidden surface found in package: {offenders}"

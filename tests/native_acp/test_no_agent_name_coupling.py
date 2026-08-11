"""G7 — the generic runtime path names no agent.

The ``AgentInstance`` seam is only real if nothing downstream of it branches on
*which* agent is running. This walks every ``Constant`` node under
``native_acp/`` and ``arsd/`` and refuses an agent-name literal anywhere except
the closed registry data in ``profile.py`` — which exists precisely to hold
source-owned, agent-keyed constants and is module-level assignment data, never
executable logic.

Two deliberate exclusions. Docstrings, because the gate is about code, not
prose: a docstring explaining why OpenCode needs a mediation binding is
documentation, while ``if executable_key == "opencode"`` on the launch path is
the coupling this refuses. And word boundaries, because ``alphanumeric`` is not
an agent name — a scan that cannot tell those apart is a scan nobody keeps.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_run_supervisor"
SCANNED_PACKAGES = (SRC / "native_acp", SRC / "arsd")

# Real agent names plus the two fabricated ones: a fixture identity must never
# leak into shipped source either.
AGENT_NAME_RE = re.compile(
    r"(\bopencode\b|\bcursor\b|\breasonix\b|\boh-my-pi\b|\bomp\b|"
    r"fake-alpha|fake-beta)",
    re.IGNORECASE,
)

# The one module allowed to carry agent-named data at all, and only as
# module-level constants — the closed registry itself.
REGISTRY_MODULE = "profile.py"


def _modules() -> list[Path]:
    paths: list[Path] = []
    for package in SCANNED_PACKAGES:
        assert package.is_dir(), package
        paths.extend(sorted(package.glob("*.py")))
    return paths


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _module_level_assignment_ids(tree: ast.Module) -> set[int]:
    """Constants inside a top-level ``NAME = <value>`` — registry data only.

    Anything nested in a function or class body is excluded by construction, so
    a comparison against a registry constant cannot hide here.
    """
    ids: set[int] = set()
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        if statement.value is None:
            continue
        for child in ast.walk(statement.value):
            if isinstance(child, ast.Constant):
                ids.add(id(child))
    return ids


@pytest.mark.parametrize("module_path", _modules(), ids=lambda p: p.name)
def test_no_agent_name_literal_reaches_the_generic_runtime_path(
    module_path: Path,
) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    exempt = _docstring_ids(tree)
    if module_path.name == REGISTRY_MODULE:
        exempt |= _module_level_assignment_ids(tree)
    offenders = [
        f"line {node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in exempt
        and AGENT_NAME_RE.search(node.value)
    ]
    assert not offenders, f"{module_path.name} names an agent in code: {offenders}"


def test_only_the_registry_module_carries_agent_named_data_at_all() -> None:
    """Every other module is agent-blind even in its module-level constants."""
    for module_path in _modules():
        if module_path.name == REGISTRY_MODULE:
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        exempt = _docstring_ids(tree)
        named = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in exempt
            and AGENT_NAME_RE.search(node.value)
        ]
        assert not named, f"{module_path.name} carries agent-named data: {named}"


def test_the_scan_actually_covers_the_modules_this_change_touches() -> None:
    names = {path.name for path in _modules()}
    assert {
        "profile.py",
        "agent_registry.py",
        "agent_registration.py",
        "observation.py",
        "spec.py",
        "run_task.py",
        "admission.py",
        "handlers.py",
        "protocol.py",
    } <= names

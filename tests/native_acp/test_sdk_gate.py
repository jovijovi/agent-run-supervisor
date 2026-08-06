"""The optional-SDK gate: only the SDK-facing modules may import ``acp``.

This module used to also pin that ``native_acp`` never imported the retired
runtime's modules. That ban now lives in ``tools/static_safety_scan.py``, which
applies the same shape rule to the whole repository instead of to one package,
and runs inside ``make verify`` — so what is left here is the gate that is
genuinely about the SDK: the package must stay importable without the ``native``
extra installed, and only the two modules that actually speak the protocol may
depend on it.

The AST scan walks every module in the package, so modules added by later slices
are covered automatically as they land.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "agent_run_supervisor" / "native_acp"
)

# Only the SDK-facing modules may import the official ACP SDK; everything else
# must stay stdlib-importable without the native extra.
SDK_GATED_MODULES = {"driver.py", "client.py"}


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = "agent_run_supervisor.native_acp"
                if node.level == 2:
                    base = "agent_run_supervisor"
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            names.add(module)
    return names


def _package_modules() -> list[Path]:
    assert PACKAGE_DIR.is_dir(), PACKAGE_DIR
    return sorted(PACKAGE_DIR.glob("*.py"))


def test_only_sdk_gated_modules_import_the_sdk() -> None:
    for module_path in _package_modules():
        if module_path.name in SDK_GATED_MODULES:
            continue
        imports = _module_imports(module_path)
        sdk_imports = {
            name for name in imports if name == "acp" or name.startswith("acp.")
        }
        assert not sdk_imports, f"{module_path.name} imports {sorted(sdk_imports)}"


def test_package_imports_without_sdk_and_gate_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_run_supervisor.native_acp as native_acp

    assert callable(native_acp.require_sdk)
    # Simulate the base install (no native extra): the package import above
    # already succeeded; only SDK use raises the typed error.
    monkeypatch.setitem(sys.modules, "acp", None)
    with pytest.raises(native_acp.NativeSdkUnavailableError):
        native_acp.require_sdk()


def test_sdk_gate_returns_module_when_present() -> None:
    import agent_run_supervisor.native_acp as native_acp

    module = native_acp.require_sdk()
    assert module.__name__ == "acp"

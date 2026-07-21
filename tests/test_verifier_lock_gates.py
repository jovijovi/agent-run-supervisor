"""Static pins for the C1 lock-enforcement gates (CI, Makefile, verifier).

The behavioral half runs for free: CI and ``make verify`` execute
``scripts/verify_local.sh``, so its ``uv lock --check`` gate runs on every
ladder invocation once present.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_install_lines_are_locked_with_native_extra() -> None:
    workflow = (ROOT / ".github" / "workflows" / "verify.yml").read_text(
        encoding="utf-8"
    )
    install_lines = [
        line.strip() for line in workflow.splitlines() if "uv sync" in line
    ]
    assert len(install_lines) == 2, install_lines
    for line in install_lines:
        assert "--locked" in line, line
        assert "--extra native" in line, line


def test_verifier_contains_uv_lock_check_gate() -> None:
    verifier = (ROOT / "scripts" / "verify_local.sh").read_text(encoding="utf-8")
    assert "uv lock --check" in verifier


def test_makefile_sync_recipe_is_locked_with_native_extra() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^sync:\n((?:\t.+\n)+)", makefile, flags=re.MULTILINE)
    assert match is not None
    recipe = match.group(1)
    assert "uv sync" in recipe
    assert "--locked" in recipe, recipe
    assert "--extra native" in recipe, recipe

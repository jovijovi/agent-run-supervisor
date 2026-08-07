"""Regression tests for the public documentation-site content gate."""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_docs_site", ROOT / "tools" / "check_docs_site.py"
)
assert SPEC and SPEC.loader
check_docs_site = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_docs_site
SPEC.loader.exec_module(check_docs_site)


def _copy_candidate(tmp_path: Path) -> Path:
    for name in ("website", "src", ".github"):
        source = ROOT / name
        if source.exists():
            shutil.copytree(source, tmp_path / name)
    for name in ("mkdocs.yml",):
        shutil.copy2(ROOT / name, tmp_path / name)
    return tmp_path


def _kinds(root: Path) -> set[str]:
    return {finding["kind"] for finding in check_docs_site.run_scan(root)["findings"]}


def test_current_site_passes_all_content_checks() -> None:
    report = check_docs_site.run_scan(ROOT)
    assert report["ok"], report["findings"]


@pytest.mark.parametrize(
    ("relative_path", "addition", "expected_kind"),
    [
        ("website/docs/index.md", "\nSee docs/roadmap/current-status.md\n", "content:internal_docs_tree"),
        ("website/docs/index.md", "\n![remote](https://example.test/image.png)\n", "assets:markdown_image"),
        ("website/docs/index.md", "\n[missing](missing-page.md)\n", "links:unresolved"),
        (".github/workflows/docs.yml", "\n  pages: write\n", "publication:pages_permission"),
    ],
)
def test_gate_rejects_forbidden_candidate_changes(
    tmp_path: Path, relative_path: str, addition: str, expected_kind: str
) -> None:
    root = _copy_candidate(tmp_path)
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text((target.read_text() if target.exists() else "") + addition)
    assert expected_kind in _kinds(root)


def test_gate_rejects_an_unallowlisted_public_page(tmp_path: Path) -> None:
    root = _copy_candidate(tmp_path)
    (root / "website/docs/accidental.md").write_text("# Accidental\n")
    assert "nav:page_not_allowlisted" in _kinds(root)

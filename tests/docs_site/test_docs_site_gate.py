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
        (".github/workflows/verify.yml", "\n      - uses: actions/deploy-pages@v4\n", "publication:deploy_pages_action"),
        (".github/workflows/pages-publish.yml", "\non:\n  push:\n    branches: [main]\n", "publication:not_manual_only"),
        (".github/workflows/pages-publish.yml", "\non:\n  push: {branches: [main]}\n", "publication:not_manual_only"),
        (".github/workflows/pages-publish.yml", "\npermissions:\n  contents: write\n", "publication:broadened_permission"),
        (".github/workflows/pages-publish.yml", "\npermissions: { contents: write }\n", "publication:broadened_permission"),
        (".github/workflows/pages-publish.yml", "\n      - uses: peaceiris/actions-gh-pages@v4\n", "publication:gh_pages_action"),
        (".github/workflows/pages-publish.yml", "\nname: &workflow_dispatch push\non: *workflow_dispatch\n", "publication:yaml_indirection"),
        (".github/workflows/pages-publish.yml", '\npermissions:\n  "actions": write\n', "publication:broadened_permission"),
        (".github/workflows/pages-publish.yml", "\n    permissions:\n      contents: write\n", "publication:permissions_not_pinned"),
        (".github/workflows/pages-publish.yml", "\n      - uses: actions/deploy-pages@v4\n", "publication:canonical_fragment"),
        (".github/workflows/verify.yml", "\n      - uses: actions/configure-pages@v5\n        with:\n          enablement: true\n", "publication:configure_pages_action"),
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


def test_publication_workflow_declares_exactly_workflow_dispatch() -> None:
    workflow = ROOT / ".github" / "workflows" / check_docs_site.PUBLICATION_WORKFLOW
    text = workflow.read_text(encoding="utf-8")
    assert check_docs_site._workflow_triggers(text) == {"workflow_dispatch"}


@pytest.mark.parametrize(
    ("old", "new", "expected_kind"),
    [
        ("actions/deploy-pages@v4", "actions/deploy-pages@v5", "publication:canonical_fragment"),
        ("          enablement: false", "          enablement: true", "publication:pages_enablement"),
        ("    needs: build\n", "", "publication:canonical_fragment"),
        ("      name: github-pages\n", "", "publication:canonical_fragment"),
        ("          path: site", "          path: docs", "publication:canonical_fragment"),
        ("  contents: read\n", "", "publication:permissions_not_pinned"),
    ],
)
def test_gate_rejects_publication_workflow_mutations(
    tmp_path: Path, old: str, new: str, expected_kind: str
) -> None:
    root = _copy_candidate(tmp_path)
    workflow = root / ".github" / "workflows" / check_docs_site.PUBLICATION_WORKFLOW
    text = workflow.read_text(encoding="utf-8")
    assert old in text
    workflow.write_text(text.replace(old, new), encoding="utf-8")
    assert expected_kind in _kinds(root)


def test_gate_rejects_extra_input_on_reviewed_pages_action(tmp_path: Path) -> None:
    root = _copy_candidate(tmp_path)
    workflow = root / ".github" / "workflows" / check_docs_site.PUBLICATION_WORKFLOW
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        text.replace(
            "          enablement: false\n",
            "          enablement: false\n          debug: true\n",
        ),
        encoding="utf-8",
    )
    assert "publication:workflow_digest" in _kinds(root)


def test_gate_rejects_moving_reviewed_pages_step_between_jobs(tmp_path: Path) -> None:
    root = _copy_candidate(tmp_path)
    workflow = root / ".github" / "workflows" / check_docs_site.PUBLICATION_WORKFLOW
    text = workflow.read_text(encoding="utf-8")
    step = (
        "\n      - name: Configure GitHub Pages\n"
        "        uses: actions/configure-pages@v5\n"
        "        with:\n"
        "          enablement: false\n"
    )
    assert text.count(step) == 1
    text = text.replace(step, "")
    text = text.replace(
        "    steps:\n      - name: Deploy to GitHub Pages\n",
        f"    steps:{step}\n      - name: Deploy to GitHub Pages\n",
    )
    workflow.write_text(text, encoding="utf-8")
    assert "publication:workflow_digest" in _kinds(root)


def test_gate_rejects_unicode_escape_in_other_active_workflow(tmp_path: Path) -> None:
    root = _copy_candidate(tmp_path)
    workflow = root / ".github" / "workflows" / "verify.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8")
        + '\n      - uses: "actions/conf\\u0069gure-pages@v5"\n',
        encoding="utf-8",
    )
    assert "publication:yaml_escape" in _kinds(root)


def test_gate_hashes_the_publication_workflow_raw_bytes(tmp_path: Path) -> None:
    root = _copy_candidate(tmp_path)
    workflow = root / ".github" / "workflows" / check_docs_site.PUBLICATION_WORKFLOW
    workflow.write_bytes(workflow.read_bytes().replace(b"\n", b"\r\n"))
    assert "publication:workflow_digest" in _kinds(root)


def test_gate_requires_the_publication_workflow_to_exist(tmp_path: Path) -> None:
    root = _copy_candidate(tmp_path)
    workflow = root / ".github" / "workflows" / check_docs_site.PUBLICATION_WORKFLOW
    workflow.unlink()
    assert "publication:workflow_missing" in _kinds(root)


def test_gate_rejects_a_renamed_publication_workflow(tmp_path: Path) -> None:
    root = _copy_candidate(tmp_path)
    workflow = root / ".github" / "workflows" / check_docs_site.PUBLICATION_WORKFLOW
    workflow.rename(workflow.with_name("pages-publish-manual.yml"))
    kinds = _kinds(root)
    assert "publication:workflow_missing" in kinds
    assert "publication:deploy_pages_action" in kinds

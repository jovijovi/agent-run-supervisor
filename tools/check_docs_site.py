#!/usr/bin/env python3
"""Content gate for the public documentation site under ``website/``.

Standard library only, and deliberately so: this runs inside
``scripts/verify_local.sh``, where the ``docs`` extra is **not** installed. It
therefore cannot import ``yaml``, ``mkdocs``, or ``mkdocstrings``, and it parses
the small, self-owned parts of ``mkdocs.yml`` it needs by line scanning rather
than by building a YAML model.

It exists because the repository's own gates do not cover ``website/``:

* ``tools/static_safety_scan.py`` walks the repository root, so it already scans
  ``website/**/*.md`` for secrets and stale phrases — but its ``TEXT_SUFFIXES``
  excludes ``.css``, ``.js``, and ``.html``, so the theme layer is unscanned, and
  its prose-claim and removed-CLI rules are scoped to authority-document roots
  that do not include ``website/``. This module closes both gaps by importing
  that module and reusing its rule functions. It never edits it.
* Nothing else checks that the public navigation allowlist, the published file
  tree, and ``mkdocs.yml`` agree; that internal links resolve; that every
  documented API symbol exists; that no external asset is loaded; or that
  publication stays dormant.

Exit status is ``0`` with no findings and ``1`` otherwise. The report is JSON on
stdout, in the same shape ``static_safety_scan`` uses.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import static_safety_scan as sss  # noqa: E402

# -- layout ------------------------------------------------------------------

SITE_ROOT = Path("website")
DOCS_DIR = SITE_ROOT / "docs"
OVERRIDES_DIR = SITE_ROOT / "overrides"
MKDOCS_CONFIG = Path("mkdocs.yml")
NAV_ALLOWLIST = SITE_ROOT / "nav-allowlist.txt"
API_ALLOWLIST = SITE_ROOT / "api-allowlist.txt"
CONTRACT_ASSERTIONS = SITE_ROOT / "contract-assertions.toml"
ASSET_PROVENANCE = SITE_ROOT / "ASSET-PROVENANCE.md"
SRC_ROOT = Path("src")
WORKFLOWS_DIR = Path(".github/workflows")

#: Files that become the site. The control files in ``website/`` itself are
#: excluded: ``contract-assertions.toml`` declares the forbidden patterns, so
#: scanning it would make every pattern match its own declaration.
PUBLISHED_ROOTS = (DOCS_DIR, OVERRIDES_DIR)
PUBLISHED_SUFFIXES = {".md", ".css", ".js", ".html", ".svg", ".yml", ".yaml", ".json"}

#: The theme layer, which ``static_safety_scan`` does not read.
THEME_SUFFIXES = {".css", ".js", ".html"}

#: Vendored third-party files. Each must be named in ASSET-PROVENANCE.md.
VENDORED_ASSETS = (
    DOCS_DIR / "assets/fonts/ibm-plex-sans-400.woff2",
    DOCS_DIR / "assets/fonts/ibm-plex-sans-400-italic.woff2",
    DOCS_DIR / "assets/fonts/ibm-plex-sans-600.woff2",
    DOCS_DIR / "assets/fonts/ibm-plex-sans-700.woff2",
    DOCS_DIR / "assets/fonts/ibm-plex-mono-400.woff2",
    DOCS_DIR / "assets/fonts/ibm-plex-mono-600.woff2",
    DOCS_DIR / "assets/fonts/OFL.txt",
    DOCS_DIR / "assets/javascript/mermaid.min.js",
)

MERMAID_ASSET = DOCS_DIR / "assets/javascript/mermaid.min.js"
STYLESHEET = DOCS_DIR / "assets/stylesheets/ars.css"

# -- rule vocabularies -------------------------------------------------------

#: Internal governance surfaces. A public page that links to one either leaks it
#: or produces a dead link, and both are failures.
INTERNAL_SURFACE_PATTERNS = {
    "internal_docs_tree": re.compile(
        r"\bdocs/(?:roadmap|plans|archive|lessons|practices|product|design)\b"
    ),
    "internal_docs_file": re.compile(
        r"\bdocs/(?:INDEX|AI_FLOW)\.md\b|\bGOAL\.md\b|\b_drift_report\b"
    ),
    "governance_doc": re.compile(
        r"\bnon-approvals\b|\bcurrent-status\b|\bsession-reuse-acceptance\b"
    ),
    "roadmap_status": re.compile(
        r"(?i)\bphase\s+[A-Z]\d\b|\bPR\s*#\d+|\bnot yet approved\b|\bunapproved\b"
    ),
}

#: Host-specific and private runtime facts. Placeholder forms stay legal: the
#: repository's own rule is repository-relative paths or an angle-bracket
#: placeholder, so ``/home/<service-user>/...`` is exactly right and
#: ``/home/realname/...`` is exactly wrong.
PRIVATE_PATH_PATTERNS = {
    "home_directory": re.compile(r"/(?:home|Users)/(?!<)[A-Za-z0-9._-]+"),
    "worktree_path": re.compile(r"\bworktrees?/[A-Za-z0-9._-]+"),
    "root_home": re.compile(r"(?<![A-Za-z0-9_-])/root/[A-Za-z0-9._-]+"),
}

#: An external asset *load*. Ordinary hyperlinks to external documentation are
#: legitimate and are not matched here — only things a browser fetches.
EXTERNAL_ASSET_PATTERNS = {
    "html_asset": re.compile(
        r"<(?:script|link|img|source|iframe)\b[^>]*\b(?:src|href)\s*=\s*[\"']https?://",
        re.I,
    ),
    "css_url": re.compile(r"url\(\s*[\"']?https?://", re.I),
    "css_import": re.compile(r"@import\s+(?:url\()?\s*[\"']?https?://", re.I),
    "markdown_image": re.compile(r"!\[[^\]]*\]\(\s*https?://"),
    "js_asset_fetch": re.compile(
        r"(?:fetch|importScripts|XMLHttpRequest)\s*\(\s*[\"']https?://", re.I
    ),
}

#: Remote font delivery, in any spelling.
REMOTE_FONT_PATTERNS = {
    "google_fonts": re.compile(r"fonts\.(?:googleapis|gstatic)\.com", re.I),
    "remote_font_face": re.compile(r"@font-face[^}]*?url\(\s*[\"']?https?://", re.I | re.S),
    "font_cdn": re.compile(r"(?:use\.typekit|cdn\.jsdelivr\.net/npm/@fontsource)", re.I),
}

#: Publication markers. Present in an active workflow, each one means the
#: repository can deploy the site, which is not authorized.
#:
#: Every marker is Pages-specific. ``id-token: write`` is deliberately *not*
#: one: the package release workflow needs it for PyPI Trusted Publishing, so
#: matching it here would fail the gate on a legitimate, pre-existing, unrelated
#: workflow. `pages: write` is the permission that actually grants a site
#: deploy, and it is matched.
PUBLICATION_MARKERS = {
    "deploy_pages_action": re.compile(r"actions/deploy-pages", re.I),
    "upload_pages_artifact": re.compile(r"actions/upload-pages-artifact", re.I),
    "gh_pages_action": re.compile(r"peaceiris/actions-gh-pages", re.I),
    "gh_deploy_command": re.compile(r"mkdocs\s+gh-deploy", re.I),
    "pages_environment": re.compile(r"environment:\s*\n?\s*(?:name:\s*)?github-pages", re.I),
    "pages_permission": re.compile(r"^\s*pages:\s*write\s*$", re.I | re.M),
}

_INLINE_MARKUP = re.compile(r"[`*_~]")
_WHITESPACE = re.compile(r"\s+")
_MKDOCSTRINGS = re.compile(r"^:::\s+(\S+)\s*$", re.M)
_MD_LINK = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)")
_HTML_HREF = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", re.I)
_FONT_FACE_SRC = re.compile(r"@font-face\s*\{[^}]*?src:\s*url\(\s*[\"']?([^\"')]+)", re.S)
_MERMAID_FENCE = re.compile(r"^```mermaid\s*$", re.M)


# -- findings ----------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One rule violation, addressed to whoever has to fix it."""

    file: str
    line: int
    kind: str
    snippet: str


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _snippet(text: str, line: int) -> str:
    lines = text.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:180]
    return ""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize(text: str) -> str:
    """Strip Markdown inline markup and collapse whitespace.

    A required literal has to survive re-wrapping a paragraph or emphasising a
    word inside it; it must not survive deleting or negating the claim.
    """
    return _WHITESPACE.sub(" ", _INLINE_MARKUP.sub("", text)).strip()


def _published_files(root: Path, suffixes: Iterable[str] | None = None) -> list[Path]:
    wanted = set(suffixes) if suffixes is not None else PUBLISHED_SUFFIXES
    found: list[Path] = []
    for base in PUBLISHED_ROOTS:
        base_path = root / base
        if not base_path.is_dir():
            continue
        for path in sorted(base_path.rglob("*")):
            if path.is_file() and path.suffix in wanted:
                found.append(path)
    return found


def _rel(root: Path, path: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


# -- mkdocs.yml, read without a YAML parser ----------------------------------


def _config_block(text: str, key: str) -> list[str]:
    """The indented lines belonging to a top-level ``key:`` mapping."""
    lines = text.splitlines()
    block: list[str] = []
    inside = False
    for line in lines:
        if not inside:
            if re.match(rf"^{re.escape(key)}\s*:", line):
                inside = True
            continue
        if line.strip() and not line[:1].isspace():
            break
        block.append(line)
    return block


def nav_pages(text: str) -> list[str]:
    """Every Markdown path reachable from ``nav:``, in navigation order."""
    pages: list[str] = []
    for line in _config_block(text, "nav"):
        match = re.search(r"(\S+\.md)\s*$", line)
        if match:
            pages.append(match.group(1))
    return pages


def config_list(text: str, key: str) -> list[str]:
    """A top-level list of scalars, e.g. ``extra_css:``."""
    values: list[str] = []
    for line in _config_block(text, key):
        match = re.match(r"^\s*-\s*(\S+)\s*$", line)
        if match:
            values.append(match.group(1))
    return values


def _allowlist_entries(path: Path) -> list[str]:
    entries: list[str] = []
    for raw in _read(path).splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


# -- checks ------------------------------------------------------------------


def check_layout(root: Path) -> list[Finding]:
    """Every control file and vendored asset the rest of the gate assumes."""
    findings: list[Finding] = []
    required = (
        MKDOCS_CONFIG,
        NAV_ALLOWLIST,
        API_ALLOWLIST,
        CONTRACT_ASSERTIONS,
        ASSET_PROVENANCE,
        DOCS_DIR,
        OVERRIDES_DIR,
        STYLESHEET,
        MERMAID_ASSET,
    )
    for rel in required:
        if not (root / rel).exists():
            findings.append(Finding(str(rel), 0, "site:missing_required_path", ""))

    config_path = root / MKDOCS_CONFIG
    if config_path.is_file():
        text = _read(config_path)
        if not re.search(r"^docs_dir:\s*website/docs\s*$", text, re.M):
            findings.append(
                Finding(
                    str(MKDOCS_CONFIG),
                    0,
                    "site:docs_dir_not_isolated",
                    "docs_dir must be website/docs so the governed docs/ tree is never copied",
                )
            )
        if re.search(r"^docs_dir:\s*docs\s*$", text, re.M):
            findings.append(
                Finding(str(MKDOCS_CONFIG), 0, "site:docs_dir_is_governed_tree", "")
            )

    # ASSET-PROVENANCE.md must be outside docs_dir, or the build publishes it.
    if (root / DOCS_DIR / "ASSET-PROVENANCE.md").exists():
        findings.append(
            Finding(
                str(DOCS_DIR / "ASSET-PROVENANCE.md"),
                0,
                "site:provenance_inside_docs_dir",
                "the provenance record is a repository file, not a public page",
            )
        )
    return findings


def check_nav_allowlist(root: Path) -> list[Finding]:
    """Three-way agreement: ``nav:``, the allowlist, and the file tree.

    Order matters as well as membership. Keeping the allowlist in navigation
    order is what makes the two files diffable against each other by eye.
    """
    findings: list[Finding] = []
    config_path = root / MKDOCS_CONFIG
    allowlist_path = root / NAV_ALLOWLIST
    if not (config_path.is_file() and allowlist_path.is_file()):
        return findings

    nav = nav_pages(_read(config_path))
    allowed = _allowlist_entries(allowlist_path)

    for page in nav:
        if page not in allowed:
            findings.append(
                Finding(str(NAV_ALLOWLIST), 0, "nav:in_nav_not_allowlisted", page)
            )
    for page in allowed:
        if page not in nav:
            findings.append(
                Finding(str(MKDOCS_CONFIG), 0, "nav:allowlisted_not_in_nav", page)
            )
    if nav != allowed and not findings:
        findings.append(
            Finding(
                str(NAV_ALLOWLIST),
                0,
                "nav:order_mismatch",
                "same pages, different order — keep the allowlist in navigation order",
            )
        )

    docs_root = root / DOCS_DIR
    if docs_root.is_dir():
        on_disk = sorted(
            p.relative_to(docs_root).as_posix()
            for p in docs_root.rglob("*.md")
            if p.is_file()
        )
        allowed_set = set(allowed)
        for page in on_disk:
            if page not in allowed_set:
                findings.append(
                    Finding(
                        (DOCS_DIR / page).as_posix(),
                        0,
                        "nav:page_not_allowlisted",
                        "a Markdown file in docs_dir that no allowlist entry publishes",
                    )
                )
        on_disk_set = set(on_disk)
        for page in allowed:
            if page not in on_disk_set:
                findings.append(
                    Finding(str(NAV_ALLOWLIST), 0, "nav:allowlisted_page_missing", page)
                )
    return findings


def check_internal_surfaces(root: Path) -> list[Finding]:
    """No internal governance surface, host path, or private runtime fact."""
    findings: list[Finding] = []
    rules = {**INTERNAL_SURFACE_PATTERNS, **PRIVATE_PATH_PATTERNS}
    for path in _published_files(root):
        text = _read(path)
        rel = _rel(root, path)
        for name, pattern in rules.items():
            for match in pattern.finditer(text):
                line = _line_of(text, match.start())
                findings.append(
                    Finding(rel.as_posix(), line, f"content:{name}", _snippet(text, line))
                )
    return findings


def check_links(root: Path) -> list[Finding]:
    """Every relative link resolves to a file that exists under ``docs_dir``."""
    findings: list[Finding] = []
    docs_root = root / DOCS_DIR
    if not docs_root.is_dir():
        return findings

    for path in sorted(docs_root.rglob("*.md")):
        text = _read(path)
        rel = _rel(root, path)
        targets = [(m.group(1), m.start()) for m in _MD_LINK.finditer(text)]
        targets += [(m.group(1), m.start()) for m in _HTML_HREF.finditer(text)]
        for target, pos in targets:
            if re.match(r"^(?:https?:|mailto:|#|data:)", target):
                continue
            bare = target.split("#", 1)[0].split("?", 1)[0]
            if not bare:
                continue
            line = _line_of(text, pos)
            if bare.startswith("/"):
                findings.append(
                    Finding(rel.as_posix(), line, "links:absolute_internal", target)
                )
                continue
            resolved = (path.parent / bare).resolve()
            # A pretty URL ("quickstart/") resolves to the page it renders from.
            candidates = [resolved, resolved.with_suffix(".md"), resolved / "index.md"]
            if bare.endswith("/"):
                candidates.append(resolved.parent.with_suffix(".md"))
            if any(c.exists() for c in candidates):
                try:
                    resolved.relative_to(docs_root.resolve())
                except ValueError:
                    findings.append(
                        Finding(rel.as_posix(), line, "links:escapes_docs_dir", target)
                    )
                continue
            findings.append(Finding(rel.as_posix(), line, "links:unresolved", target))
    return findings


def _module_attributes(path: Path) -> set[str]:
    """Top-level names a module defines, without importing it."""
    names: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return names
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def check_api_symbols(root: Path) -> list[Finding]:
    """Documented symbols are allowlisted, and every allowlisted one exists."""
    findings: list[Finding] = []
    allowlist_path = root / API_ALLOWLIST
    if not allowlist_path.is_file():
        return findings
    allowed = set(_allowlist_entries(allowlist_path))

    docs_root = root / DOCS_DIR
    if docs_root.is_dir():
        for path in sorted(docs_root.rglob("*.md")):
            text = _read(path)
            rel = _rel(root, path)
            for match in _MKDOCSTRINGS.finditer(text):
                identifier = match.group(1)
                if identifier not in allowed:
                    findings.append(
                        Finding(
                            rel.as_posix(),
                            _line_of(text, match.start()),
                            "api:identifier_not_allowlisted",
                            identifier,
                        )
                    )

    for identifier in sorted(allowed):
        module_parts = identifier.split(".")
        attribute = module_parts.pop()
        module_path = root / SRC_ROOT / Path(*module_parts).with_suffix(".py")
        if not module_path.is_file():
            findings.append(
                Finding(str(API_ALLOWLIST), 0, "api:module_missing", identifier)
            )
            continue
        if attribute not in _module_attributes(module_path):
            findings.append(
                Finding(str(API_ALLOWLIST), 0, "api:attribute_missing", identifier)
            )
    return findings


def check_mermaid(root: Path) -> list[Finding]:
    """Mermaid is configured, used, vendored, and loaded before the theme."""
    findings: list[Finding] = []
    config_path = root / MKDOCS_CONFIG
    if config_path.is_file():
        config = _read(config_path)
        if "custom_fences" not in config or not re.search(
            r"-\s*name:\s*mermaid\b", config
        ):
            findings.append(
                Finding(str(MKDOCS_CONFIG), 0, "mermaid:fence_not_configured", "")
            )

    docs_root = root / DOCS_DIR
    used = False
    if docs_root.is_dir():
        for path in docs_root.rglob("*.md"):
            if _MERMAID_FENCE.search(_read(path)):
                used = True
                break
    if not used:
        findings.append(
            Finding(
                str(DOCS_DIR),
                0,
                "mermaid:no_diagram",
                "the site declares Mermaid support and must actually use it",
            )
        )

    main_template = root / OVERRIDES_DIR / "main.html"
    if not main_template.is_file():
        findings.append(
            Finding(str(OVERRIDES_DIR / "main.html"), 0, "mermaid:loader_missing", "")
        )
    elif "assets/javascript/mermaid.min.js" not in _read(main_template):
        findings.append(
            Finding(
                str(OVERRIDES_DIR / "main.html"),
                0,
                "mermaid:vendored_bundle_not_loaded",
                "the theme falls back to a CDN when window.mermaid is undefined",
            )
        )
    return findings


def check_fonts(root: Path) -> list[Finding]:
    """Fonts are self-hosted: no remote face anywhere, every local face present."""
    findings: list[Finding] = []
    config_path = root / MKDOCS_CONFIG
    if config_path.is_file() and not re.search(r"^\s*font:\s*false\s*$", _read(config_path), re.M):
        findings.append(
            Finding(
                str(MKDOCS_CONFIG),
                0,
                "fonts:theme_font_not_disabled",
                "theme.font must be false or the theme emits a Google Fonts link",
            )
        )

    for path in _published_files(root):
        text = _read(path)
        rel = _rel(root, path)
        for name, pattern in REMOTE_FONT_PATTERNS.items():
            for match in pattern.finditer(text):
                line = _line_of(text, match.start())
                findings.append(
                    Finding(rel.as_posix(), line, f"fonts:{name}", _snippet(text, line))
                )

    stylesheet = root / STYLESHEET
    if stylesheet.is_file():
        text = _read(stylesheet)
        sources = _FONT_FACE_SRC.findall(text)
        if not sources:
            findings.append(
                Finding(str(STYLESHEET), 0, "fonts:no_local_font_face", "")
            )
        for src in sources:
            if re.match(r"^(?:https?:)?//", src):
                findings.append(Finding(str(STYLESHEET), 0, "fonts:remote_src", src))
                continue
            if not (stylesheet.parent / src).resolve().is_file():
                findings.append(Finding(str(STYLESHEET), 0, "fonts:src_missing", src))
    return findings


def check_external_assets(root: Path) -> list[Finding]:
    """No asset is fetched from a third-party origin at page load."""
    findings: list[Finding] = []
    for path in _published_files(root):
        text = _read(path)
        rel = _rel(root, path)
        for name, pattern in EXTERNAL_ASSET_PATTERNS.items():
            for match in pattern.finditer(text):
                line = _line_of(text, match.start())
                findings.append(
                    Finding(rel.as_posix(), line, f"assets:{name}", _snippet(text, line))
                )

    config_path = root / MKDOCS_CONFIG
    if config_path.is_file():
        config = _read(config_path)
        for key in ("extra_css", "extra_javascript"):
            for value in config_list(config, key):
                if re.match(r"^(?:https?:)?//", value):
                    findings.append(
                        Finding(str(MKDOCS_CONFIG), 0, f"assets:remote_{key}", value)
                    )

    provenance = root / ASSET_PROVENANCE
    if provenance.is_file():
        recorded = _read(provenance)
        for asset in VENDORED_ASSETS:
            if not (root / asset).is_file():
                findings.append(
                    Finding(asset.as_posix(), 0, "assets:vendored_file_missing", "")
                )
            elif asset.name not in recorded:
                findings.append(
                    Finding(
                        str(ASSET_PROVENANCE),
                        0,
                        "assets:provenance_not_recorded",
                        asset.name,
                    )
                )
    return findings


def check_publication_dormant(root: Path) -> list[Finding]:
    """No enabled workflow can publish the site.

    A ``.yml.disabled`` artifact may contain the steps — that is the point of
    keeping one — but nothing a runner loads may.
    """
    findings: list[Finding] = []
    workflows = root / WORKFLOWS_DIR
    if not workflows.is_dir():
        return findings

    for path in sorted(workflows.iterdir()):
        if not path.is_file() or path.suffix not in {".yml", ".yaml"}:
            continue
        text = _read(path)
        rel = _rel(root, path)
        for name, pattern in PUBLICATION_MARKERS.items():
            for match in pattern.finditer(text):
                line = _line_of(text, match.start())
                findings.append(
                    Finding(
                        rel.as_posix(),
                        line,
                        f"publication:{name}",
                        _snippet(text, line),
                    )
                )
    return findings


def check_contract_assertions(root: Path) -> list[Finding]:
    """Required product statements are present; forbidden claims are absent."""
    findings: list[Finding] = []
    manifest_path = root / CONTRACT_ASSERTIONS
    if not manifest_path.is_file():
        return findings
    manifest = tomllib.loads(_read(manifest_path))

    for entry in manifest.get("required", []):
        page = entry["page"]
        literal = entry["literal"]
        page_path = root / DOCS_DIR / page
        if not page_path.is_file():
            findings.append(
                Finding(str(CONTRACT_ASSERTIONS), 0, "contract:page_missing", page)
            )
            continue
        if _normalize(literal) not in _normalize(_read(page_path)):
            findings.append(
                Finding(
                    (DOCS_DIR / page).as_posix(),
                    0,
                    "contract:required_literal_absent",
                    literal[:180],
                )
            )

    compiled = [
        (entry["id"], re.compile(entry["pattern"]))
        for entry in manifest.get("forbidden", [])
    ]
    for path in _published_files(root):
        text = _read(path)
        rel = _rel(root, path)
        for rule_id, pattern in compiled:
            for match in pattern.finditer(text):
                line = _line_of(text, match.start())
                findings.append(
                    Finding(
                        rel.as_posix(),
                        line,
                        f"contract:forbidden:{rule_id}",
                        _snippet(text, line),
                    )
                )
    return findings


def check_reused_repo_rules(root: Path) -> list[Finding]:
    """Reuse ``static_safety_scan``'s rules on the surfaces it does not reach.

    Two gaps: the theme file types are outside its ``TEXT_SUFFIXES``, and its
    prose-claim and removed-CLI rules are scoped to authority-document roots that
    exclude ``website/``. The rules themselves are not redefined here.
    """
    findings: list[Finding] = []

    # Secrets and stale phrases, in .css/.js/.html.
    for path in _published_files(root, THEME_SUFFIXES):
        text = _read(path)
        rel = _rel(root, path)
        for name, pattern in {**sss.SECRET_PATTERNS, **sss.STALE_PATTERNS}.items():
            for match in pattern.finditer(text):
                line = _line_of(text, match.start())
                findings.append(
                    Finding(rel.as_posix(), line, f"theme:{name}", _snippet(text, line))
                )

    # Prose-claim and removed-CLI rules, over published Markdown.
    for path in _published_files(root, {".md"}):
        rel = _rel(root, path)
        text = _read(path)
        for finding in sss._scan_prose_claims(rel, text):
            findings.append(
                Finding(finding.file, finding.line, finding.kind, finding.snippet)
            )
        for finding in sss._scan_removed_cli_leaves(rel, text):
            findings.append(
                Finding(finding.file, finding.line, finding.kind, finding.snippet)
            )
    return findings


CHECKS = (
    check_layout,
    check_nav_allowlist,
    check_internal_surfaces,
    check_links,
    check_api_symbols,
    check_mermaid,
    check_fonts,
    check_external_assets,
    check_publication_dormant,
    check_contract_assertions,
    check_reused_repo_rules,
)


def run_scan(root: Path) -> dict:
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(root))
    return {
        "ok": not findings,
        "root": str(root),
        "checks": [check.__name__ for check in CHECKS],
        "findings": [asdict(finding) for finding in findings],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Content gate for the public documentation site under website/."
    )
    parser.add_argument("root", nargs="?", default=".", help="Repository root to scan")
    args = parser.parse_args(argv)
    report = run_scan(Path(args.root))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

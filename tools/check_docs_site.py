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
  publication stays confined to the one reviewed workflow.

Exit status is ``0`` with no findings and ``1`` otherwise. The report is JSON on
stdout, in the same shape ``static_safety_scan`` uses.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import unquote, urlsplit

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

#: Publication markers. Present in an active workflow, each one means that
#: workflow can deploy the site. Exactly one workflow may carry them — the
#: reviewed publication workflow named by ``PUBLICATION_WORKFLOW`` —
#: and only the artifact-based markers in ``REVIEWED_PUBLICATION_MARKERS``.
#: Every other active workflow must stay free of every marker, and
#: ``check_publication_boundary`` additionally requires the reviewed workflow
#: to exist and holds it to its reviewed canonical shape: push-to-main plus
#: manual-dispatch triggering, both jobs guarded to refs/heads/main, a pinned
#: narrow token, exact deploy topology, and no YAML indirection.
#:
#: Every marker is Pages-specific. ``id-token: write`` is deliberately *not*
#: one: the package release workflow needs it for PyPI Trusted Publishing, so
#: matching it here would fail the gate on a legitimate, pre-existing, unrelated
#: workflow. `pages: write` is the permission that actually grants a site
#: deploy, and it is matched. ``actions/configure-pages`` is the enablement
#: surface: forbidden in every other workflow, allowed exactly once in the
#: reviewed one, and ``enablement`` may never carry any value but ``false``
#: anywhere.
PUBLICATION_MARKERS = {
    "deploy_pages_action": re.compile(r"actions/deploy-pages", re.I),
    "upload_pages_artifact": re.compile(r"actions/upload-pages-artifact", re.I),
    "gh_pages_action": re.compile(r"peaceiris/actions-gh-pages", re.I),
    "gh_deploy_command": re.compile(r"mkdocs\s+gh-deploy", re.I),
    "pages_environment": re.compile(r"environment:\s*\n?\s*(?:name:\s*)?github-pages", re.I),
    "pages_permission": re.compile(r"^\s*pages:\s*write\s*$", re.I | re.M),
    "configure_pages_action": re.compile(r"actions/configure-pages", re.I),
    "pages_enablement": re.compile(r"^\s*enablement:(?!\s*false\s*$).*$", re.I | re.M),
}

#: The one reviewed publication workflow, by file name under
#: ``WORKFLOWS_DIR``. Deleting or renaming it re-forbids every marker here.
PUBLICATION_WORKFLOW = "pages-publish.yml"

#: SHA-256 of the complete, reviewed publication workflow bytes. The workflow
#: is intentionally tiny and rarely changed; any trigger, permission, action,
#: input, job placement, or comment edit must update this reviewed digest.
PUBLICATION_WORKFLOW_SHA256 = (
    "418ecb878724957e9080ee7aa044b958f8bd4a74f11cd0fd347f700e5e0c53bc"
)

#: Markers that *are* the reviewed workflow's mechanism — the official
#: artifact-based Pages pattern, including its ``configure-pages`` step,
#: whose canonical fragment below pins ``enablement: false``. The
#: ``gh-pages``-branch mechanisms (``peaceiris/actions-gh-pages``,
#: ``mkdocs gh-deploy``) and any non-``false`` ``enablement`` stay forbidden
#: everywhere, including in the reviewed workflow.
REVIEWED_PUBLICATION_MARKERS = frozenset(
    {
        "deploy_pages_action",
        "upload_pages_artifact",
        "pages_environment",
        "pages_permission",
        "configure_pages_action",
    }
)

#: The only triggers the reviewed workflow may declare: automatic publication
#: when main advances, plus manual re-publication by dispatch. The canonical
#: byte fragments below pin the push branch and both job guards exactly.
PUBLICATION_TRIGGERS = frozenset({"push", "workflow_dispatch"})

#: The only ``write`` grants the reviewed workflow may hold — the two
#: ``actions/deploy-pages`` itself requires. Everything else stays read-only.
PUBLICATION_WRITE_GRANTS = frozenset({"pages", "id-token"})

#: The root permission map, byte-exact. The reviewed workflow must carry this
#: block exactly once, and no other ``permissions`` mapping may exist at any
#: level, so a job cannot re-widen what the root pinned. Without it, jobs
#: would inherit the repository's default ``GITHUB_TOKEN`` grants, which this
#: gate cannot see.
PUBLICATION_ROOT_PERMISSIONS = (
    "\npermissions:\n  contents: read\n  pages: write\n  id-token: write\n"
)

#: The reviewed workflow's critical topology, newline-anchored, byte-exact,
#: and each fragment exactly once: the push-to-main plus manual-dispatch trigger
#: block, both jobs' refs/heads/main guards, the three official Pages steps with
#: their pinned inputs, and ``deploy``'s dependency and environment.
PUBLICATION_CANONICAL_FRAGMENTS = (
    (
        "trigger_block",
        "\non:\n  push:\n    branches:\n      - main\n  workflow_dispatch:\n",
    ),
    (
        "build_job_main_guard",
        "\njobs:\n  build:\n"
        "    if: github.ref == 'refs/heads/main'\n"
        "    runs-on: ubuntu-latest\n",
    ),
    (
        "configure_pages_step",
        "\n      - name: Configure GitHub Pages\n"
        "        uses: actions/configure-pages@v5\n"
        "        with:\n"
        "          enablement: false\n",
    ),
    (
        "upload_artifact_step",
        "\n      - name: Upload Pages artifact\n"
        "        uses: actions/upload-pages-artifact@v4\n"
        "        with:\n"
        "          path: site\n",
    ),
    (
        "deploy_step",
        "\n      - name: Deploy to GitHub Pages\n"
        "        id: deployment\n"
        "        uses: actions/deploy-pages@v4\n",
    ),
    (
        "deploy_job_topology",
        "\n  deploy:\n"
        "    needs: build\n"
        "    if: github.ref == 'refs/heads/main'\n"
        "    runs-on: ubuntu-latest\n"
        "    environment:\n"
        "      name: github-pages\n",
    ),
)

#: Deploy surfaces counted over the whole file: exactly one of each, so a
#: duplicate step, a second ``on:``/``jobs:`` mapping, or a second Pages
#: surface cannot ride along beside the canonical ones.
PUBLICATION_UNIQUE_SURFACES = (
    ("on_key", "\non:\n"),
    ("jobs_key", "\njobs:\n"),
    ("configure_pages_action", "actions/configure-pages"),
    ("upload_pages_artifact_action", "actions/upload-pages-artifact"),
    ("deploy_pages_action", "actions/deploy-pages"),
    ("pages_environment_name", "github-pages"),
)

_ON_LINE = re.compile(r"^(?:on|[\"']on[\"'])\s*:(.*)$")
_TRIGGER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INLINE_TRIGGERS = re.compile(r"[A-Za-z0-9_,\s\[\]]+")
_BLOCK_KEY = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*:(?:\s|$)")
_BLOCK_ITEM = re.compile(r"^(\s+)-\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")
_WRITE_GRANT = re.compile(r"[\"']?([A-Za-z-]+)[\"']?\s*:\s*[\"']?write(?:-all)?\b", re.I)
_PERMISSIONS_KEY = re.compile(r"^\s*[\"']?permissions[\"']?\s*:", re.M)
_YAML_INDIRECTION = re.compile(r"[&*%!]|<<|^---\s*$|^\.\.\.\s*$", re.M)
_YAML_ESCAPE = re.compile(r"\\(?:x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})")

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


class _BuiltLinkParser(HTMLParser):
    """Collect link destinations from rendered HTML without third-party code."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value is not None:
                self.links.append((value, self.getpos()[0]))


def check_built_site_links(
    site_dir: Path, pages_base_path: str = "/"
) -> list[Finding]:
    """Reject broken internal URLs in MkDocs' final HTML output.

    Paths are checked after query strings and fragments are removed. This keeps
    fragments from corrupting path resolution without duplicating MkDocs' anchor
    validation for Markdown-authored links.
    """
    findings: list[Finding] = []
    if not site_dir.is_dir():
        return [Finding(str(site_dir), 0, "built_links:site_dir_missing", "")]

    base_root = "/" + pages_base_path.strip("/")
    base_prefix = base_root if base_root == "/" else base_root + "/"
    site_root = site_dir.resolve()

    for page in sorted(site_dir.rglob("*.html")):
        parser = _BuiltLinkParser()
        parser.feed(_read(page))
        rel = page.relative_to(site_dir).as_posix()
        for target, line in parser.links:
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            url_path = unquote(parsed.path)
            if url_path.lower().endswith(".md"):
                findings.append(Finding(rel, line, "built_links:markdown_url", target))
                continue

            if url_path.startswith("/"):
                if base_root != "/" and url_path == base_root:
                    relative_url = ""
                elif url_path.startswith(base_prefix):
                    relative_url = url_path[len(base_prefix):]
                else:
                    findings.append(
                        Finding(rel, line, "built_links:outside_pages_base", target)
                    )
                    continue
                resolved = site_root / relative_url
            else:
                resolved = page.parent / url_path
            resolved = resolved.resolve()
            try:
                resolved.relative_to(site_root)
            except ValueError:
                findings.append(Finding(rel, line, "built_links:escapes_site", target))
                continue

            candidates = [resolved]
            if url_path.endswith("/") or resolved.is_dir():
                candidates.append(resolved / "index.html")
            elif not resolved.suffix:
                candidates.extend((resolved.with_suffix(".html"), resolved / "index.html"))
            if not any(candidate.is_file() for candidate in candidates):
                findings.append(Finding(rel, line, "built_links:target_missing", target))
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


def _workflow_triggers(text: str) -> set[str]:
    """Every trigger name a workflow file declares, in any accepted spelling.

    GitHub accepts ``on: push``, ``on: [push]``, and a nested mapping, and
    raw text can repeat ``on:``, so every occurrence is read and merged.
    Like the ``mkdocs.yml`` helpers above, this scans lines rather than
    importing a YAML parser. Anything it cannot read — a flow value, a
    quoted item, a folded scalar, an anchor or alias — contributes an
    ``<unreadable>`` token instead of being skipped, so no spelling can slip
    past the caller's exact trigger-set comparison. The reviewed
    workflow additionally refuses YAML indirection outright, so an alias can
    never stand in for a trigger name there.
    """
    triggers: set[str] = set()
    lines = text.splitlines()
    for number, line in enumerate(lines):
        match = _ON_LINE.match(line)
        if not match:
            continue
        inline = match.group(1).split("#", 1)[0].strip()
        if inline:
            if _INLINE_TRIGGERS.fullmatch(inline):
                triggers.update(_TRIGGER_NAME.findall(inline))
            else:
                triggers.add("<unreadable>")
            continue
        entries: list[tuple[int, str]] = []
        for nested in lines[number + 1:]:
            stripped = nested.strip()
            if stripped and not nested[:1].isspace():
                break
            if not stripped or stripped.startswith("#"):
                continue
            entry = _BLOCK_KEY.match(nested) or _BLOCK_ITEM.match(nested)
            if entry is None:
                triggers.add("<unreadable>")
                continue
            entries.append((len(entry.group(1)), entry.group(2)))
        if entries:
            top = min(indent for indent, _ in entries)
            triggers.update(name for indent, name in entries if indent == top)
    return triggers


def _check_publication_workflow(
    rel: Path, text: str, raw_bytes: bytes
) -> list[Finding]:
    """The reviewed publication workflow keeps its reviewed canonical shape.

    This is deliberately a byte-exact contract, not YAML semantics: the file
    is small and reviewed, so its complete SHA-256 is pinned. Any edit must
    update the reviewed digest. The older focused checks remain to produce
    useful findings for common mistakes.
    """
    findings: list[Finding] = []
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != PUBLICATION_WORKFLOW_SHA256:
        findings.append(
            Finding(
                rel.as_posix(),
                0,
                "publication:workflow_digest",
                f"expected {PUBLICATION_WORKFLOW_SHA256}, found {digest}",
            )
        )
    for match in _YAML_INDIRECTION.finditer(text):
        line = _line_of(text, match.start())
        findings.append(
            Finding(
                rel.as_posix(),
                line,
                "publication:yaml_indirection",
                _snippet(text, line),
            )
        )
    triggers = _workflow_triggers(text)
    if triggers != PUBLICATION_TRIGGERS:
        findings.append(
            Finding(
                rel.as_posix(),
                0,
                "publication:unreviewed_triggers",
                "on: must declare exactly push (branches: main) and "
                "workflow_dispatch; found "
                + (", ".join(sorted(triggers)) or "no recognizable trigger"),
            )
        )
    for match in _WRITE_GRANT.finditer(text):
        if match.group(1).lower() not in PUBLICATION_WRITE_GRANTS:
            line = _line_of(text, match.start())
            findings.append(
                Finding(
                    rel.as_posix(),
                    line,
                    "publication:broadened_permission",
                    _snippet(text, line),
                )
            )
    if len(_PERMISSIONS_KEY.findall(text)) != 1:
        findings.append(
            Finding(
                rel.as_posix(),
                0,
                "publication:permissions_not_pinned",
                "exactly one permissions mapping: the root token pin",
            )
        )
    if text.count(PUBLICATION_ROOT_PERMISSIONS) != 1:
        findings.append(
            Finding(
                rel.as_posix(),
                0,
                "publication:permissions_not_pinned",
                "root permission map must be exactly contents: read, "
                "pages: write, id-token: write",
            )
        )
    for label, fragment in PUBLICATION_CANONICAL_FRAGMENTS:
        count = text.count(fragment)
        if count != 1:
            findings.append(
                Finding(
                    rel.as_posix(),
                    0,
                    "publication:canonical_fragment",
                    f"{label}: expected exactly once, found {count}",
                )
            )
    for label, token in PUBLICATION_UNIQUE_SURFACES:
        count = text.count(token)
        if count != 1:
            findings.append(
                Finding(
                    rel.as_posix(),
                    0,
                    "publication:canonical_fragment",
                    f"{label}: expected exactly once, found {count}",
                )
            )
    return findings


def check_publication_boundary(root: Path) -> list[Finding]:
    """Publication stays confined to the one reviewed workflow.

    Exactly one reviewed workflow — ``PUBLICATION_WORKFLOW`` — must exist
    and carry the official artifact-based Pages pattern in its reviewed
    canonical shape: push to main plus ``workflow_dispatch`` as the only
    triggers, both jobs guarded to refs/heads/main, the exact root token pin,
    the exact deploy topology, and no YAML indirection. Every other active
    workflow must stay free of every publication marker.
    """
    findings: list[Finding] = []
    reviewed_rel = WORKFLOWS_DIR / PUBLICATION_WORKFLOW
    missing = Finding(
        reviewed_rel.as_posix(),
        0,
        "publication:workflow_missing",
        "the reviewed publication workflow must exist",
    )
    workflows = root / WORKFLOWS_DIR
    if not workflows.is_dir():
        return [missing]

    reviewed_seen = False
    for path in sorted(workflows.iterdir()):
        if not path.is_file() or path.suffix not in {".yml", ".yaml"}:
            continue
        raw_bytes = path.read_bytes()
        text = raw_bytes.decode("utf-8", errors="replace")
        rel = _rel(root, path)
        reviewed = path.name == PUBLICATION_WORKFLOW
        if reviewed:
            reviewed_seen = True
        for match in _YAML_ESCAPE.finditer(text):
            line = _line_of(text, match.start())
            findings.append(
                Finding(
                    rel.as_posix(),
                    line,
                    "publication:yaml_escape",
                    _snippet(text, line),
                )
            )
        for name, pattern in PUBLICATION_MARKERS.items():
            if reviewed and name in REVIEWED_PUBLICATION_MARKERS:
                continue
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
        if reviewed:
            findings.extend(_check_publication_workflow(rel, text, raw_bytes))
    if not reviewed_seen:
        findings.append(missing)
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
    check_publication_boundary,
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
    parser.add_argument(
        "--built-site",
        type=Path,
        help="Scan internal links in an already-built MkDocs site instead",
    )
    parser.add_argument(
        "--pages-base-path",
        default="/",
        help="URL path prefix used by root-relative links in the built site",
    )
    args = parser.parse_args(argv)
    if args.built_site is None:
        report = run_scan(Path(args.root))
    else:
        findings = check_built_site_links(args.built_site, args.pages_base_path)
        report = {
            "ok": not findings,
            "root": str(args.built_site),
            "checks": ["check_built_site_links"],
            "findings": [asdict(finding) for finding in findings],
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

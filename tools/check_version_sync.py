#!/usr/bin/env python3
"""Verify project version consistency across pyproject, __init__, and uv.lock."""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
INIT_PY = PROJECT_ROOT / "src/agent_run_supervisor/__init__.py"
UV_LOCK = PROJECT_ROOT / "uv.lock"

WORKSPACE_LOCK_RE = re.compile(
    r'\[\[package\]\]\s*\n'
    r'name = "agent-run-supervisor"\s*\n'
    r'version = "([^"]+)"\s*\n'
    r'source = \{ editable = "\." \}',
    re.MULTILINE,
)
INIT_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def read_pyproject_version(root: Path = PROJECT_ROOT) -> str:
    path = root / "pyproject.toml"
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


def read_init_version(root: Path = PROJECT_ROOT) -> str:
    path = root / "src/agent_run_supervisor/__init__.py"
    text = path.read_text(encoding="utf-8")
    match = INIT_VERSION_RE.search(text)
    if not match:
        raise ValueError(f"could not parse __version__ from {path}")
    return match.group(1)


def read_lock_version(root: Path = PROJECT_ROOT) -> str:
    path = root / "uv.lock"
    text = path.read_text(encoding="utf-8")
    match = WORKSPACE_LOCK_RE.search(text)
    if not match:
        raise ValueError(
            f"could not find editable agent-run-supervisor package in {path}"
        )
    return match.group(1)


def collect_versions(root: Path = PROJECT_ROOT) -> dict[str, str]:
    return {
        "pyproject.toml": read_pyproject_version(root),
        "__init__.py": read_init_version(root),
        "uv.lock": read_lock_version(root),
    }


def check_version_sync(root: Path = PROJECT_ROOT) -> tuple[bool, list[str]]:
    errors: list[str] = []
    versions = collect_versions(root)
    unique = set(versions.values())
    if len(unique) != 1:
        for label, value in versions.items():
            errors.append(f"{label}: {value}")
        errors.insert(
            0,
            "version mismatch across pyproject.toml, __init__.py, and uv.lock",
        )
        return False, errors
    return True, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check version sync across pyproject.toml, __init__.py, and uv.lock"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="Project root (default: repository root)",
    )
    args = parser.parse_args(argv)

    try:
        ok, errors = check_version_sync(args.root)
    except (OSError, ValueError, tomllib.TOMLDecodeError, KeyError) as exc:
        print(f"version sync check FAILED: {exc}", file=sys.stderr)
        return 1

    if not ok:
        print("version sync check FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "\nRemediation: run `make bump VERSION=X.Y.Z` or "
            "`uv run python tools/bump_version.py X.Y.Z` before release.",
            file=sys.stderr,
        )
        return 1

    versions = collect_versions(args.root)
    version = next(iter(set(versions.values())))
    print(f"version sync check OK ({version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

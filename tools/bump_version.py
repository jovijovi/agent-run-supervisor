#!/usr/bin/env python3
"""Bump release version across pyproject.toml, __init__.py, uv.lock, and CHANGELOG."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from tools.check_version_sync import PROJECT_ROOT, read_pyproject_version

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
PYPROJECT_VERSION_RE = re.compile(r'^(version\s*=\s*")[^"]+(")', re.MULTILINE)
INIT_VERSION_RE = re.compile(r'^(__version__\s*=\s*")[^"]+(")', re.MULTILINE)
VERSION_SECTION_RE = re.compile(r"^## \[\d+\.\d+\.\d+\]", re.MULTILINE)

CHANGELOG_STUB = """## [{version}] - {release_date}

### Added

### Changed

### Fixed

### Notes

"""


def parse_version(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def validate_new_version(current: str, new: str) -> None:
    if not SEMVER_RE.fullmatch(new):
        raise ValueError(f"invalid semver (expected X.Y.Z): {new}")
    if parse_version(new) <= parse_version(current):
        raise ValueError(
            f"new version must be greater than current {current}: got {new}"
        )


def update_pyproject_version(path: Path, new_version: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = PYPROJECT_VERSION_RE.subn(
        rf'\g<1>{new_version}\g<2>',
        text,
        count=1,
    )
    if count != 1:
        raise ValueError(f"could not update version in {path}")
    path.write_text(updated, encoding="utf-8")


def update_init_version(path: Path, new_version: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = INIT_VERSION_RE.subn(
        rf'\g<1>{new_version}\g<2>',
        text,
        count=1,
    )
    if count != 1:
        raise ValueError(f"could not update __version__ in {path}")
    path.write_text(updated, encoding="utf-8")


def insert_changelog_stub(path: Path, new_version: str, release_date: str) -> None:
    text = path.read_text(encoding="utf-8")
    if f"## [{new_version}]" in text:
        raise ValueError(f"CHANGELOG already contains section for {new_version}")

    stub = CHANGELOG_STUB.format(version=new_version, release_date=release_date)
    match = VERSION_SECTION_RE.search(text)
    if match:
        idx = match.start()
        updated = text[:idx] + stub + text[idx:]
    else:
        updated = text.rstrip() + "\n\n" + stub

    path.write_text(updated, encoding="utf-8")


def run_uv_lock(root: Path) -> None:
    result = subprocess.run(
        ["uv", "lock"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise RuntimeError("uv lock failed")


def bump_version(
    new_version: str,
    *,
    root: Path = PROJECT_ROOT,
    release_date: str | None = None,
    skip_lock: bool = False,
) -> str:
    current = read_pyproject_version(root)
    validate_new_version(current, new_version)

    update_pyproject_version(root / "pyproject.toml", new_version)
    update_init_version(root / "src/agent_run_supervisor/__init__.py", new_version)
    insert_changelog_stub(
        root / "CHANGELOG.md",
        new_version,
        release_date or date.today().isoformat(),
    )
    if not skip_lock:
        run_uv_lock(root)

    return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bump release version across project metadata files"
    )
    parser.add_argument("version", help="New semver version (X.Y.Z)")
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="Project root (default: repository root)",
    )
    parser.add_argument(
        "--release-date",
        help="CHANGELOG release date (default: today, ISO YYYY-MM-DD)",
    )
    parser.add_argument(
        "--skip-lock",
        action="store_true",
        help="Skip uv lock (for tests only)",
    )
    args = parser.parse_args(argv)

    try:
        current = bump_version(
            args.version,
            root=args.root,
            release_date=args.release_date,
            skip_lock=args.skip_lock,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"bump_version FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"Bumped version: {current} -> {args.version}")
    print("Updated: pyproject.toml, src/agent_run_supervisor/__init__.py, CHANGELOG.md")
    if not args.skip_lock:
        print("Updated: uv.lock (via uv lock)")
    print("")
    print("Next steps:")
    print("  1. Edit CHANGELOG.md section content")
    print("  2. make verify")
    print("  3. Merge to main, then: make release-tag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

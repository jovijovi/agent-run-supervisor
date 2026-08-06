#!/usr/bin/env python3
"""Assert what a built wheel and sdist actually contain.

The distribution boundary is a *set*, not an absence. A gate written as
``unzip -l dist/*.whl | grep -c acpx`` observes a count and inverts its exit
status exactly when that count is zero, so it reports success for the same
reason a broken gate would. This compares the real manifest to a committed
allowlist as an exact set, in both directions, for both artifacts.

Two layers, because they fail differently:

* **set equality** catches anything that appears or disappears — including a
  file nobody thought to write a rule about;
* **negative rules** catch a careless *regeneration* of the allowlist, which
  set equality would accept without complaint.

There is deliberately no ``--write`` mode. Regenerating an allowlist from
whatever the build happened to produce is the failure this gate exists to
prevent; a manifest change is an edit somebody makes on purpose.
"""
from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path

DIST_NAME = "agent_run_supervisor"
DIST_INFO_PLACEHOLDER = "<dist-info>"

_WHEEL_DIST_INFO = re.compile(rf"^{re.escape(DIST_NAME)}-[^/]+\.dist-info/")
_SDIST_ROOT = re.compile(rf"^{re.escape(DIST_NAME)}-[^/]+/")

ALLOWLISTS = {
    "wheel": Path("tools/dist_manifest_wheel.txt"),
    "sdist": Path("tools/dist_manifest_sdist.txt"),
}

#: Package modules the acpx removal deleted. None may ship, under any name.
REMOVED_MODULES = (
    "caller",
    "goal",
    "hermes_caller",
    "live_stream",
    "mcp_config",
    "parser",
    "policy",
    "preflight",
    "retention",
    "role",
    "runner",
    "session_inspect",
    "session_runtime",
    "workspace",
)


def normalize_wheel_entry(name: str) -> str:
    """Drop the version so a bump is not a manifest diff."""
    return _WHEEL_DIST_INFO.sub(f"{DIST_INFO_PLACEHOLDER}/", name)


def normalize_sdist_entry(name: str) -> str:
    return _SDIST_ROOT.sub("", name)


def wheel_manifest(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return {
            normalize_wheel_entry(info.filename)
            for info in archive.infolist()
            if not info.is_dir()
        }


def sdist_manifest(path: Path) -> set[str]:
    with tarfile.open(path) as archive:
        return {
            normalize_sdist_entry(member.name)
            for member in archive.getmembers()
            if member.isfile()
        }


def load_expected(root: Path, kind: str) -> set[str]:
    text = (root / ALLOWLISTS[kind]).read_text(encoding="utf-8")
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def forbidden_paths(names: set[str]) -> list[str]:
    """The rules that hold whatever the allowlist says."""
    problems: list[str] = []
    for name in sorted(names):
        tail = name.split("/", 1)[-1] if name.startswith("src/") else name
        if "acpx" in name:
            problems.append(f"names the removed runtime: {name}")
            continue
        parts = tail.split("/")
        if f"{DIST_NAME}/fixtures" in tail:
            problems.append(f"ships packaged fixture data: {name}")
            continue
        for index, part in enumerate(parts):
            if part != DIST_NAME or index + 1 >= len(parts):
                continue
            leaf = parts[index + 1].removesuffix(".py")
            if leaf in REMOVED_MODULES:
                problems.append(f"ships a removed module: {name}")
                break
    return problems


def check_manifest(actual: set[str], expected: set[str], *, kind: str) -> list[str]:
    problems: list[str] = []
    for name in sorted(actual - expected):
        problems.append(f"{kind}: unexpected file in the artifact: {name}")
    for name in sorted(expected - actual):
        problems.append(f"{kind}: allowlisted file missing from the artifact: {name}")
    for problem in forbidden_paths(actual):
        problems.append(f"{kind}: {problem}")
    return problems


def _one(root: Path, pattern: str) -> Path:
    matches = sorted((root / "dist").glob(pattern))
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one dist/{pattern}, found {len(matches)}: "
            f"{[p.name for p in matches]}"
        )
    return matches[0]


def run(root: Path) -> list[str]:
    problems: list[str] = []
    problems.extend(
        check_manifest(
            wheel_manifest(_one(root, "*.whl")),
            load_expected(root, "wheel"),
            kind="wheel",
        )
    )
    problems.extend(
        check_manifest(
            sdist_manifest(_one(root, "*.tar.gz")),
            load_expected(root, "sdist"),
            kind="sdist",
        )
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="Repository root")
    args = parser.parse_args(argv)
    problems = run(Path(args.root).resolve())
    if problems:
        print("Distribution manifest gate FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nIf this change is intended, edit the allowlist in tools/ on purpose.",
            file=sys.stderr,
        )
        return 1
    print("Distribution manifest gate passed (wheel and sdist match exactly).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

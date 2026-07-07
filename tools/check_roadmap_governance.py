#!/usr/bin/env python3
"""Roadmap documentation governance checks.

Enforces the living-board / active-plans layout introduced in the 2026-07
roadmap governance restructure.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOARD = PROJECT_ROOT / "docs/roadmap/current-status.md"
FEATURES = PROJECT_ROOT / "docs/roadmap/features.md"
PLANS_ROOT = PROJECT_ROOT / "docs/plans"
PLANS_ACTIVE = PLANS_ROOT / "active"
PLANS_ARCHIVE = PLANS_ROOT / "archive"
PHASE_ARCHIVE = PROJECT_ROOT / "docs/roadmap/archive/phases"

DEFAULT_MAX_BOARD_LINES = 180
DEFAULT_MAX_ACTIVE_PLANS = 3
DEFAULT_MAX_EVIDENCE_CHARS = 120
DEFAULT_MAX_BOARD_PR_REFS = 5


def _fail(errors: list[str]) -> int:
    print("roadmap governance check FAILED:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    print(
        "\nRemediation: keep merge history in git/PR; detailed evidence in "
        "docs/roadmap/archive/phases/; completed plans in docs/plans/archive/.",
        file=sys.stderr,
    )
    return 1


def _board_line_count(max_lines: int, errors: list[str]) -> None:
    if not BOARD.is_file():
        errors.append(f"missing board file: {BOARD}")
        return
    lines = BOARD.read_text(encoding="utf-8").splitlines()
    if len(lines) > max_lines:
        errors.append(
            f"{BOARD.relative_to(PROJECT_ROOT)} has {len(lines)} lines (max {max_lines})"
        )


def _board_pr_and_sha(errors: list[str], max_pr: int) -> None:
    text = BOARD.read_text(encoding="utf-8")
    pr_count = len(re.findall(r"PR\s+#\d+", text))
    if pr_count > max_pr:
        errors.append(f"board has {pr_count} PR references (max {max_pr})")
    if re.search(r"\b[0-9a-f]{7,40}\b", text):
        errors.append("board contains git SHA-like tokens")


def _plans_root_discipline(errors: list[str]) -> None:
    for path in PLANS_ROOT.glob("*.md"):
        if path.name != "README.md":
            errors.append(
                f"plan file must not live in docs/plans/ root: {path.name} "
                "(use active/ or archive/)"
            )


def _active_plan_count(max_active: int, errors: list[str]) -> None:
    if not PLANS_ACTIVE.is_dir():
        errors.append("missing docs/plans/active/")
        return
    active_plans = [
        p
        for p in PLANS_ACTIVE.glob("*.md")
        if p.name != "README.md"
    ]
    if len(active_plans) > max_active:
        errors.append(
            f"docs/plans/active/ has {len(active_plans)} plans (max {max_active}); "
            "archive completed work"
        )


def _features_evidence(max_chars: int, errors: list[str]) -> None:
    if not FEATURES.is_file():
        errors.append(f"missing features file: {FEATURES}")
        return
    in_table = False
    for line in FEATURES.read_text(encoding="utf-8").splitlines():
        if line.startswith("| ID |"):
            in_table = True
            continue
        if in_table and line.startswith("|") and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 5:
                evidence = cells[4]
                if len(evidence) > max_chars:
                    errors.append(
                        f"features evidence too long ({len(evidence)} chars) for row {cells[0]} "
                        f"(max {max_chars})"
                    )
        if in_table and line.strip() == "":
            break


def _archive_plan_frontmatter(errors: list[str]) -> None:
    for path in PLANS_ARCHIVE.glob("202*.md"):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            errors.append(f"archive plan missing frontmatter: {path.name}")
            continue
        block = text.split("---", 2)[1]
        if "status: archived" not in block:
            errors.append(f"archive plan missing status: archived: {path.name}")


def _phase_archive_status(errors: list[str]) -> None:
    if not PHASE_ARCHIVE.is_dir():
        errors.append("missing docs/roadmap/archive/phases/")
        return
    for path in PHASE_ARCHIVE.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if (
            "Status: **Closed" not in text
            and "Status: **Merged" not in text
            and "Status: **Complete" not in text
            and "Status: **Done" not in text
        ):
            if "Closed on" not in text and "Closed for" not in text and "Complete on" not in text:
                errors.append(
                    f"phase archive missing closed status marker: {path.name}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check roadmap doc governance rules")
    parser.add_argument(
        "--max-board-lines",
        type=int,
        default=DEFAULT_MAX_BOARD_LINES,
    )
    parser.add_argument(
        "--max-active-plans",
        type=int,
        default=DEFAULT_MAX_ACTIVE_PLANS,
    )
    parser.add_argument(
        "--max-evidence-chars",
        type=int,
        default=DEFAULT_MAX_EVIDENCE_CHARS,
    )
    parser.add_argument(
        "--max-board-pr-refs",
        type=int,
        default=DEFAULT_MAX_BOARD_PR_REFS,
    )
    args = parser.parse_args(argv)

    errors: list[str] = []
    _board_line_count(args.max_board_lines, errors)
    _board_pr_and_sha(errors, args.max_board_pr_refs)
    _plans_root_discipline(errors)
    _active_plan_count(args.max_active_plans, errors)
    _features_evidence(args.max_evidence_chars, errors)
    _archive_plan_frontmatter(errors)
    _phase_archive_status(errors)

    if errors:
        return _fail(errors)
    print("roadmap governance check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

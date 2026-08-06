"""The active-tree containment scan for the Session no-close model.

One scan, run by default, over exactly the surfaces that can *do* something:
tracked source, scripts, fixtures, tests, and maintained public docs. Cold
archives and historical Changelog facts are deliberately out of scope — history
is allowed to record what was once true.

The scan distinguishes two things that look alike in a grep and are not alike at
all:

* **Session termination** — anything that ends an external or ARS Session:
  ``sessions close``, ``session_close``, ``mark_closed``, a lifecycle ``state``.
  None of it may exist on an active surface, in source *or* in a script.
* **Ordinary resource cleanup** — closing a file descriptor, a socket, a
  stream, an ACP connection, a subscription, or reaping a process. All of it is
  legitimate and must survive untouched, so the scan may not simply ban the
  word "close".

Negative tombstone tests — the ones that assert a retired name is *absent* —
are also legitimate, and are recognized rather than flagged.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Cold history: never loaded by default, never a source of active contract.
_ARCHIVE_PREFIXES = (
    "docs/archive/",
    "docs/plans/archive/",
    "docs/roadmap/archive/",
)
_HISTORY_FILES = ("CHANGELOG.md",)

#: This file is the scan itself, and must name what it looks for.
_SELF = "tests/test_no_close_containment_scan.py"


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [
        path
        for path in out
        if not path.startswith(_ARCHIVE_PREFIXES)
        and path not in _HISTORY_FILES
        and path != _SELF
    ]


def _is_tombstone(line: str) -> bool:
    """A line that asserts a retired thing is gone is not the retired thing."""
    stripped = line.strip()
    return (
        "not hasattr" in stripped
        or "not in " in stripped
        or stripped.startswith("#")
        or "is refused" in stripped
        or "UNKNOWN_OP" in stripped
    )


# The executable shapes that terminate a Session. Each is a call, not a word.
_TERMINATION_PATTERNS = (
    # An external agent Session, ended through a management command.
    re.compile(r'"sessions"\s*,\s*"close"'),
    re.compile(r"'sessions'\s*,\s*'close'"),
    re.compile(r"\bsessions\s+close\b"),
    # An ARS Session, ended through the store or the wire.
    re.compile(r"\bmark_closed\s*\("),
    re.compile(r"\bsession_close\s*\("),
    re.compile(r"\bclose_session\s*\("),
    re.compile(r"\bbest_effort_close_session\b"),
    re.compile(r"\bteardown_real_acpx_session\b"),
)

_TEXT_SUFFIXES = (".py", ".md", ".json", ".toml", ".sh", ".ndjson", ".yml", ".yaml")


def _scan(patterns) -> list[str]:
    hits: list[str] = []
    for relative in _tracked_files():
        path = REPO_ROOT / relative
        if path.suffix not in _TEXT_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _is_tombstone(line):
                continue
            for pattern in patterns:
                if pattern.search(line):
                    hits.append(f"{relative}:{number}: {line.strip()}")
                    break
    return hits


def test_no_active_surface_terminates_a_session() -> None:
    """Source, scripts, fixtures, tests, and public docs — all of them."""
    hits = _scan(_TERMINATION_PATTERNS)
    assert hits == [], "active Session-termination mechanism(s):\n" + "\n".join(hits)


def test_ordinary_resource_cleanup_is_untouched() -> None:
    """The scan must not have been satisfied by deleting real cleanup.

    If these disappear, something removed genuine teardown while chasing a
    grep — which is the specific failure mode the plan warns about.
    """
    client = (REPO_ROOT / "src/agent_run_supervisor/arsd/client.py").read_text(
        encoding="utf-8"
    )
    assert "def close(self)" in client, "ArsdClient lost its socket cleanup"

    run_task = (
        REPO_ROOT / "src/agent_run_supervisor/native_acp/run_task.py"
    ).read_text(encoding="utf-8")
    assert "aclose()" in run_task or "close()" in run_task, (
        "RunTask lost its stream/connection teardown"
    )

    managed = (REPO_ROOT / "src/agent_run_supervisor/managed_process.py").read_text(
        encoding="utf-8"
    )
    assert "terminate_group" in managed and "kill_group" in managed, (
        "process teardown was removed"
    )


def test_no_active_surface_claims_a_session_lifecycle_state() -> None:
    """No active text may still teach `state: open|closed` on a Session."""
    patterns = (
        re.compile(r'"state"\s*:\s*"(open|closed)"'),
        re.compile(r"'state'\s*:\s*'(open|closed)'"),
        re.compile(r"\bsession\.state\b"),
        re.compile(r"\brecord\.state\b"),
    )
    hits = _scan(patterns)
    assert hits == [], "active Session-lifecycle claim(s):\n" + "\n".join(hits)

"""Caller-side read-only view over persisted normalized events (evidence).

The view exposes STRUCTURAL signals only (family, kind, status, text_length,
summary). It reads already-persisted, already-redacted ``normalized-events.jsonl``
artifacts and never re-parses raw acpx streams or surfaces bulk agent content —
even if a (synthetic, adversarial) event carries a verbatim text body.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EVENTS_FILE_NAME = "normalized-events.jsonl"


@dataclass(frozen=True)
class NormalizedEventView:
    """Read-only structural projection of one persisted normalized event."""

    family: str             # e.g. run_started, tool_started, run_completed, ...
    kind: str | None        # tool kind, when present
    status: str | None      # tool/run status, when present
    text_length: int | None  # length only — never the text body
    summary: str | None     # forward-compatible key_summary for unknown_update


def _project(event: dict) -> NormalizedEventView:
    text_length = event.get("text_length")
    return NormalizedEventView(
        family=str(event.get("type", "")),
        kind=event.get("kind"),
        status=event.get("status"),
        text_length=text_length if isinstance(text_length, int) else None,
        summary=event.get("key_summary"),
    )


def load_events(artifact_dir: str | Path) -> list[NormalizedEventView]:
    """Load persisted normalized events for a run/turn as read-only evidence.

    Local read of already-redacted artifacts. Returns an empty list when the
    artifact has no event stream (e.g. a dry-run preview dir).
    """
    events_path = Path(artifact_dir) / EVENTS_FILE_NAME
    if not events_path.is_file():
        return []
    views: list[NormalizedEventView] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        views.append(_project(json.loads(line)))
    return views

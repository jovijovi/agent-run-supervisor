"""Caller-side read-only view over persisted normalized events (evidence).

The view exposes STRUCTURAL signals only (family, kind, status, text_length,
summary). It reads already-persisted, already-redacted ``normalized-events.jsonl``
artifacts and never re-parses raw acpx streams or surfaces bulk agent content —
even if a (synthetic, adversarial) event carries a verbatim text body.

On top of the whole-stream ``load_events`` projection, this module also exposes a
small, stable cursor/read API for callers (future Sachima/Hermes) that poll a
run/turn artifact directory for live progress:

* ``read_event_page(artifact_dir, after_seq=..., limit=...)`` returns a bounded,
  cursor-paged window of structural :class:`EventRecord` projections. It honours
  the persisted integer ``seq`` when present and falls back to stable 1-based
  line cursors for legacy streams that predate ``seq``.
* ``load_progress(artifact_dir)`` returns the :class:`ProgressSnapshot` from
  ``progress.json`` when present (or ``None``), carrying only the structural
  ``schema_version``/``state``/``last_seq``/``event_count``/``updated_at`` fields.

Both work for one-shot run dirs and persistent-session turn dirs because both are
artifact directories with the same live files. Neither surfaces raw AGENT text,
platform state, or a business verdict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EVENTS_FILE_NAME = "normalized-events.jsonl"
PROGRESS_FILE_NAME = "progress.json"

# Bounded paging: a caller asks for at most ``MAX_PAGE_LIMIT`` records per read.
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 1000


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


# --------------------------------------------------------------------------- #
# Cursor-paged event records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EventRecord:
    """A cursor-addressable structural projection of one persisted event.

    Carries the stream ``seq`` cursor plus the same allow-listed structural
    fields as :class:`NormalizedEventView` — never raw text/body/content/message.
    """

    seq: int                # persisted seq, or 1-based line cursor for legacy streams
    family: str
    kind: str | None
    status: str | None
    text_length: int | None
    summary: str | None


@dataclass(frozen=True)
class EventPage:
    """A bounded, cursor-paged window over a normalized event stream."""

    records: tuple[EventRecord, ...]
    next_cursor: int | None  # pass back as ``after_seq`` to resume; unchanged when empty
    has_more: bool           # True when more records remain past this window


def _effective_seq(event: dict, position: int) -> int:
    """Persisted integer ``seq`` when present, else the 1-based line cursor.

    Live paths persist a monotonic ``seq`` starting at 1; legacy streams predate
    it, so we fall back to the record's 1-based position for a stable cursor.
    """
    seq = event.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        return position
    return seq


def _record(event: dict, seq: int) -> EventRecord:
    view = _project(event)
    return EventRecord(
        seq=seq,
        family=view.family,
        kind=view.kind,
        status=view.status,
        text_length=view.text_length,
        summary=view.summary,
    )


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(f"limit must be an int, got {type(limit).__name__}")
    if limit < 1 or limit > MAX_PAGE_LIMIT:
        raise ValueError(f"limit must be in 1..{MAX_PAGE_LIMIT}, got {limit}")
    return limit


def _validate_after_seq(after_seq: int | None) -> int | None:
    if after_seq is None:
        return None
    if isinstance(after_seq, bool) or not isinstance(after_seq, int):
        raise ValueError(
            f"after_seq must be an int or None, got {type(after_seq).__name__}"
        )
    if after_seq < 0:
        raise ValueError(f"after_seq must be >= 0, got {after_seq}")
    return after_seq


def read_event_page(
    artifact_dir: str | Path,
    *,
    after_seq: int | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
) -> EventPage:
    """Read one cursor-paged window of structural event records.

    Local read of the already-redacted ``normalized-events.jsonl`` artifact.

    * ``after_seq`` is an **exclusive** cursor: only records with a strictly
      greater cursor are returned. ``None`` starts at the beginning.
    * ``limit`` bounds the window (validated to ``1..MAX_PAGE_LIMIT``).
    * The cursor is the persisted integer ``seq`` when present, else a stable
      1-based line cursor for legacy streams.

    A missing event stream (e.g. a dry-run preview dir) yields an empty page with
    the cursor left unchanged. Never surfaces raw agent text.
    """
    after = _validate_after_seq(after_seq)
    bound = _validate_limit(limit)

    events_path = Path(artifact_dir) / EVENTS_FILE_NAME
    if not events_path.is_file():
        return EventPage(records=(), next_cursor=after, has_more=False)

    matched: list[EventRecord] = []
    position = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        position += 1
        event = json.loads(line)
        seq = _effective_seq(event, position)
        if after is not None and seq <= after:
            continue
        matched.append(_record(event, seq))

    has_more = len(matched) > bound
    page = matched[:bound]
    next_cursor = page[-1].seq if page else after
    return EventPage(records=tuple(page), next_cursor=next_cursor, has_more=has_more)


# --------------------------------------------------------------------------- #
# Progress snapshot
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProgressSnapshot:
    """Structural projection of ``progress.json`` (never raw text/verdict)."""

    schema_version: int
    state: str
    last_seq: int
    event_count: int
    updated_at: str


def _require_int(data: dict, key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"progress.json field {key!r} must be an integer, got {value!r}"
        )
    return value


def _require_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(
            f"progress.json field {key!r} must be a string, got {value!r}"
        )
    return value


def load_progress(artifact_dir: str | Path) -> ProgressSnapshot | None:
    """Read the live ``progress.json`` snapshot for a run/turn, if present.

    Returns ``None`` when no progress artifact exists (not an error). Invalid
    required integer fields fail closed with a clear :class:`ValueError`. Exposes
    only the structural progress fields — never raw AGENT text, platform state,
    or a business verdict.
    """
    progress_path = Path(artifact_dir) / PROGRESS_FILE_NAME
    if not progress_path.is_file():
        return None
    data = json.loads(progress_path.read_text(encoding="utf-8"))
    return ProgressSnapshot(
        schema_version=_require_int(data, "schema_version"),
        state=_require_str(data, "state"),
        last_seq=_require_int(data, "last_seq"),
        event_count=_require_int(data, "event_count"),
        updated_at=_require_str(data, "updated_at"),
    )

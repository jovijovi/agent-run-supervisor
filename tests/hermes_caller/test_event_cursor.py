"""PR2 — caller-side cursor/read API over persisted safe artifacts.

These tests exercise the read-only cursor paging and progress-snapshot surface
callers (future Sachima/Hermes) use to poll a run/turn artifact directory
without re-parsing raw acpx streams or surfacing bulk agent text. Everything is
local/offline: synthetic ``normalized-events.jsonl`` / ``progress.json`` written
into a tmp artifact dir. The same surface serves one-shot run dirs and
persistent-session turn dirs because both are artifact dirs with the same files.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from agent_run_supervisor.hermes_caller.events import (
    EventPage,
    EventRecord,
    ProgressSnapshot,
    load_progress,
    read_event_page,
)

BULK_MARKER = "BULK_AGENT_CONTENT_THAT_MUST_NOT_LEAK"
EVENTS_FILE_NAME = "normalized-events.jsonl"
PROGRESS_FILE_NAME = "progress.json"


# --------------------------------------------------------------------------- #
# Local artifact-dir helpers (self-contained; no conftest coupling)
# --------------------------------------------------------------------------- #
def _write_stream(artifact_dir: Path, events: list[dict]) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(event) for event in events)
    (artifact_dir / EVENTS_FILE_NAME).write_text(lines + "\n", encoding="utf-8")
    return artifact_dir


def _write_progress(artifact_dir: Path, payload: dict) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / PROGRESS_FILE_NAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return artifact_dir


def _seqed_stream() -> list[dict]:
    """A live-style stream carrying persisted, 1-based, monotonic ``seq``."""
    return [
        {"type": "run_started", "seq": 1, "method": "initialize", "id": 1},
        {"type": "tool_started", "seq": 2, "tool_call_id": "[REDACTED]", "kind": "read"},
        {"type": "tool_completed", "seq": 3, "tool_call_id": "[REDACTED]", "status": "completed"},
        {"type": "agent_message_delta", "seq": 4, "text_length": 128},
        {"type": "run_completed", "seq": 5, "stop_reason": "end_turn"},
    ]


def _legacy_stream() -> list[dict]:
    """A pre-PR1 stream with NO ``seq`` (must use 1-based line cursors)."""
    return [
        {"type": "run_started", "method": "initialize", "id": 1},
        {"type": "tool_started", "tool_call_id": "[REDACTED]", "kind": "read"},
        {"type": "tool_completed", "tool_call_id": "[REDACTED]", "status": "completed"},
        {"type": "agent_message_delta", "text_length": 128},
        {"type": "run_completed", "stop_reason": "end_turn"},
    ]


# --------------------------------------------------------------------------- #
# 1. Cursor page uses persisted seq and excludes after_seq (exclusive).
# --------------------------------------------------------------------------- #
def test_cursor_uses_persisted_seq_and_excludes_after_seq(tmp_path: Path) -> None:
    run_dir = _write_stream(tmp_path / "run", _seqed_stream())

    page = read_event_page(run_dir, after_seq=2)

    assert isinstance(page, EventPage)
    assert [r.seq for r in page.records] == [3, 4, 5]
    # after_seq is exclusive: seq 2 (and everything below) is dropped.
    assert all(r.seq > 2 for r in page.records)
    assert page.records[0].family == "tool_completed"
    assert page.next_cursor == 5
    assert page.has_more is False


# --------------------------------------------------------------------------- #
# 2. Legacy streams without seq use stable 1-based line-number cursors.
# --------------------------------------------------------------------------- #
def test_cursor_falls_back_to_line_numbers_for_legacy_stream(tmp_path: Path) -> None:
    run_dir = _write_stream(tmp_path / "legacy", _legacy_stream())

    full = read_event_page(run_dir)
    assert [r.seq for r in full.records] == [1, 2, 3, 4, 5]

    tail = read_event_page(run_dir, after_seq=3)
    assert [r.seq for r in tail.records] == [4, 5]
    assert tail.records[0].family == "agent_message_delta"
    assert tail.next_cursor == 5


# --------------------------------------------------------------------------- #
# 3. Page limit + has_more + next_cursor walk the whole stream.
# --------------------------------------------------------------------------- #
def test_limit_has_more_and_next_cursor_paging(tmp_path: Path) -> None:
    run_dir = _write_stream(tmp_path / "run", _seqed_stream())

    first = read_event_page(run_dir, limit=2)
    assert [r.seq for r in first.records] == [1, 2]
    assert first.has_more is True
    assert first.next_cursor == 2

    second = read_event_page(run_dir, after_seq=first.next_cursor, limit=2)
    assert [r.seq for r in second.records] == [3, 4]
    assert second.has_more is True
    assert second.next_cursor == 4

    third = read_event_page(run_dir, after_seq=second.next_cursor, limit=2)
    assert [r.seq for r in third.records] == [5]
    assert third.has_more is False
    assert third.next_cursor == 5


# --------------------------------------------------------------------------- #
# 4. Missing normalized-events.jsonl → empty page, cursor unchanged; progress
#    still readable when present.
# --------------------------------------------------------------------------- #
def test_missing_events_file_returns_empty_page_with_unchanged_cursor(
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "no-events"
    empty_dir.mkdir()

    page = read_event_page(empty_dir, after_seq=7)
    assert page.records == ()
    assert page.next_cursor == 7  # unchanged
    assert page.has_more is False

    # No progress artifact either → None, not an error.
    assert load_progress(empty_dir) is None

    # A progress artifact next to an absent event stream is still readable.
    _write_progress(
        empty_dir,
        {
            "schema_version": 1,
            "state": "running",
            "last_seq": 0,
            "event_count": 0,
            "updated_at": "2026-07-05T00:00:00+00:00",
        },
    )
    still_empty = read_event_page(empty_dir, after_seq=7)
    assert still_empty.records == ()
    assert load_progress(empty_dir) is not None


# --------------------------------------------------------------------------- #
# 5. load_progress reads progress.json fields.
# --------------------------------------------------------------------------- #
def test_load_progress_reads_fields(tmp_path: Path) -> None:
    run_dir = _write_progress(
        tmp_path / "run",
        {
            "schema_version": 1,
            "state": "running",
            "last_seq": 4,
            "event_count": 4,
            "updated_at": "2026-07-05T12:34:56+00:00",
        },
    )

    snap = load_progress(run_dir)
    assert isinstance(snap, ProgressSnapshot)
    assert snap.schema_version == 1
    assert snap.state == "running"
    assert snap.last_seq == 4
    assert snap.event_count == 4
    assert snap.updated_at == "2026-07-05T12:34:56+00:00"


# --------------------------------------------------------------------------- #
# 6. Missing progress.json → None (not an error).
# --------------------------------------------------------------------------- #
def test_missing_progress_returns_none(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert load_progress(run_dir) is None


# --------------------------------------------------------------------------- #
# 7. Invalid required integer fields fail closed with a clear ValueError.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_field", ["schema_version", "last_seq", "event_count"])
def test_invalid_progress_integer_field_raises(tmp_path: Path, bad_field: str) -> None:
    payload = {
        "schema_version": 1,
        "state": "running",
        "last_seq": 4,
        "event_count": 4,
        "updated_at": "2026-07-05T00:00:00+00:00",
    }
    payload[bad_field] = "not-an-int"
    run_dir = _write_progress(tmp_path / "run", payload)

    with pytest.raises(ValueError) as excinfo:
        load_progress(run_dir)
    assert bad_field in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 8. Cursor API never surfaces raw bulk agent text, even with adversarial
#    text/content/message/body fields on the persisted record.
# --------------------------------------------------------------------------- #
def test_cursor_page_does_not_leak_bulk_content(tmp_path: Path) -> None:
    adversarial = _seqed_stream()
    adversarial[3] = {
        "type": "agent_message_delta",
        "seq": 4,
        "text_length": 128,
        "text": f"{BULK_MARKER} verbatim model output body",
        "content": BULK_MARKER,
        "message": BULK_MARKER,
        "body": BULK_MARKER,
    }
    run_dir = _write_stream(tmp_path / "run", adversarial)

    page = read_event_page(run_dir)

    for record in page.records:
        assert isinstance(record, EventRecord)
        for value in dataclasses.asdict(record).values():
            if isinstance(value, str):
                assert BULK_MARKER not in value
        for attr in ("text", "content", "message", "body"):
            assert not hasattr(record, attr)

    delta = next(r for r in page.records if r.family == "agent_message_delta")
    assert delta.text_length == 128  # length only — never the body


# --------------------------------------------------------------------------- #
# 9. Bounded limit / after_seq validation.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_limit", [0, -1, 10_000])
def test_invalid_limit_raises(tmp_path: Path, bad_limit: int) -> None:
    run_dir = _write_stream(tmp_path / "run", _seqed_stream())
    with pytest.raises(ValueError):
        read_event_page(run_dir, limit=bad_limit)


def test_negative_after_seq_raises(tmp_path: Path) -> None:
    run_dir = _write_stream(tmp_path / "run", _seqed_stream())
    with pytest.raises(ValueError):
        read_event_page(run_dir, after_seq=-1)


# --------------------------------------------------------------------------- #
# 10. Structural projection carries the same allow-listed fields as load_events.
# --------------------------------------------------------------------------- #
def test_records_are_structural_projections(tmp_path: Path) -> None:
    run_dir = _write_stream(tmp_path / "run", _seqed_stream())
    page = read_event_page(run_dir)

    field_names = {f.name for f in dataclasses.fields(EventRecord)}
    assert field_names == {"seq", "family", "kind", "status", "text_length", "summary"}

    started = next(r for r in page.records if r.family == "tool_started")
    assert started.kind == "read"
    completed = next(r for r in page.records if r.family == "tool_completed")
    assert completed.status == "completed"

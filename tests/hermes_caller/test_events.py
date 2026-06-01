"""T3 — read-only normalized-event evidence view (plan §6.4, §10-T3).

The caller-side events view exposes STRUCTURAL signals only (family, kind,
status, text_length, key_summary) over already-persisted, already-redacted
events. It never re-parses raw acpx streams and never surfaces bulk agent
content — even if a (synthetic, adversarial) event carries a verbatim text body.

RED expectation: the ``hermes_caller.events`` module does not exist yet.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from agent_run_supervisor.hermes_caller.events import (
    NormalizedEventView,
    load_events,
)

STRUCTURAL_FIELDS = {"family", "kind", "status", "text_length", "summary"}
BULK_MARKER = "BULK_AGENT_CONTENT_THAT_MUST_NOT_LEAK"


def _by_family(views: list[NormalizedEventView]) -> dict[str, NormalizedEventView]:
    # Fixture has at most one event per family, so last-wins indexing is fine.
    return {v.family: v for v in views}


def test_view_exposes_only_structural_fields() -> None:
    field_names = {f.name for f in dataclasses.fields(NormalizedEventView)}
    assert field_names == STRUCTURAL_FIELDS


def test_load_events_returns_structural_views(events_run_dir: Path) -> None:
    views = load_events(events_run_dir)
    assert views and all(isinstance(v, NormalizedEventView) for v in views)

    indexed = _by_family(views)
    assert "run_started" in indexed
    assert "run_completed" in indexed

    assert indexed["tool_started"].kind == "read"
    assert indexed["tool_completed"].status == "completed"
    assert indexed["agent_message_delta"].text_length == 128
    assert indexed["unknown_update"].summary == "alpha:str,beta:int"


def test_load_events_does_not_leak_bulk_content(events_run_dir: Path) -> None:
    views = load_events(events_run_dir)

    # No structural field on any view may carry verbatim agent body text.
    for view in views:
        for value in dataclasses.asdict(view).values():
            if isinstance(value, str):
                assert BULK_MARKER not in value
    # And the projection has no content-bearing attribute at all.
    delta = _by_family(views)["agent_message_delta"]
    for attr in ("text", "content", "message", "body"):
        assert not hasattr(delta, attr)


def test_load_events_handles_failed_stream(failed_events_run_dir: Path) -> None:
    indexed = _by_family(load_events(failed_events_run_dir))

    assert "run_failed" in indexed
    assert indexed["tool_completed"].status == "failed"
    assert "permission_requested" in indexed

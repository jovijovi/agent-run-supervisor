---
title: "Live event streaming"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: live-event-streaming
---

# Live event streaming

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## Live event streaming (PR1 + PR2) — Closed

Closure record only — **not** a new product phase. Implements local live supervision artifacts
and a read-only cursor API; does **not** approve Sachima/platform live progress delivery (PR3).

- **PR1 (Done):** incremental stdout parsing during exec and session-send; live
  `acpx-stdout.ndjson`, `normalized-events.jsonl`, and `progress.json`; `result.json` unchanged.
  Evidence: `src/agent_run_supervisor/live_stream.py`, `tests/test_live_event_stream.py`,
  `docs/design/result-event-schema.md` §4.1; plan archived at
  `docs/plans/archive/2026-07-05-live-event-streaming.md`.
- **PR2 (Done):** local seq-cursored event fetch and progress polling via
  `hermes_caller.events` (`read_event_page`, `load_progress`); evidence:
  `tests/hermes_caller/test_event_cursor.py`, schema §4.2.
- **PR3 (Not approved):** Sachima live progress integration — requires separate explicit product
  approval; §5 non-approvals unchanged.

Status: **Closed on `main` pre-`v0.1.0`.** No Gateway/IM/delivery behavior implied.

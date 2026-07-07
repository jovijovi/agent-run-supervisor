---
title: "Live event streaming core (PR1)"
status: archived
created_at: 2026-07-05
last_validated_at: 2026-07-07T11:30:00+0800
archived_at: 2026-07-07
---
> **Archived plan（冷区）：** 非 active 上下文。Roadmap 章节迁移见
> [`docs/roadmap/MIGRATION.md`](../roadmap/MIGRATION.md)。
> 验收摘要见对应 [`docs/roadmap/archive/phases/`](../roadmap/archive/phases/) 条目。

# Live event streaming core — PR1

## Completion note

**PR1 and PR2 are closed on `main` (pre-`v0.1.0`).** PR1 live stream core and PR2 local cursor/progress
API (`hermes_caller.events`) are implemented and evidenced in tests/schema. **PR3 (Sachima live progress
integration) is not approved** and remains out of scope; §5 non-approvals unchanged.

Concrete, task-level implementation plan. Derives from `docs/product/prd.md`,
`docs/design/architecture.md`, `docs/design/technical-solution.md`,
`docs/design/result-event-schema.md`, and the local-supervisor scope tracked in
`docs/roadmap/current-status.md`. It does **not** redefine product goals, expand
product scope, or imply any new live/runtime approval.

## 1. Context and exact target

The supervisor currently captures the acpx/Codex NDJSON stream **only after the
child exits**:

- `runner.execute_subprocess` reads stdout with `process.communicate()`.
- `SupervisorRunner._finalize_prepared_outcome` writes `acpx-stdout.ndjson` and
  then calls `parse_acpx_stdout_bytes(...)` on the whole buffer.
- `session_runtime.SessionRuntime._run_turn` does the same after `_run(...)`.

This is post-hoc replay, not live supervision. A supervisor cannot observe a run
until it is already over.

**PR1 target:** add a **live event stream core** so the supervisor consumes
stdout NDJSON incrementally while the child is still running, persists
raw/redacted stdout lines and normalized safe events as they arrive, exposes a
safe `progress.json`, and still produces the current final `result.json`
unchanged.

## 2. Architecture: event-first, result-last

- **Incremental parser.** `parser.py` gains `IncrementalParseState` plus
  `consume_acpx_line(...)`/`feed(...)`/`finish(...)`. They reuse the exact
  per-record logic (`_consume_parsed_line` → `_consume_record`) that the batch
  `parse_acpx_stdout_bytes(...)` uses, so batch parse and live parse agree. Only
  complete newline-terminated records are parsed; a partial line at the reader
  boundary is buffered until its newline (or stream EOF) completes it.
- **Live stdout sink.** `execute_subprocess` accepts an optional
  `stdout_sink: Callable[[bytes], None]`. A reader thread calls the sink with
  each complete line as it arrives, while the whole stdout buffer is still
  collected for `SubprocessOutcome` compatibility. stderr is drained the same
  way. Timeout/kill semantics and all metadata
  (`supervisor_killed`, `supervisor_timed_out`, `kill_reason`, `kill_signal`,
  `process_group_used`, `stdout_closed`, `stderr_closed`) are preserved. No
  `shell=True`.
- **Safe projection.** `live_stream.LiveEventSink` is the single live writer. Per
  line it: appends the **redacted** raw line to `acpx-stdout.ndjson`; feeds the
  **raw** bytes to the incremental parser; appends each newly normalized **safe**
  event (structural only — `text_length`, never text) to
  `normalized-events.jsonl` with a monotonically increasing `seq`; and rewrites a
  small `progress.json` (`schema_version`, `state`, `last_seq`, `event_count`,
  `updated_at`) that carries **no raw agent text**.
- **Raw artifacts remain evidence.** The redacted raw NDJSON is still persisted;
  the normalized safe stream and `progress.json` are the caller-facing live
  projection.
- **Result-last compatibility.** `result.json` is still written at finalization
  with the same shape. When events were appended live they are **not** re-appended
  (the sink is the sole writer); when no live streaming happened (e.g. a caller
  that hands a finished `SubprocessOutcome` to `finalize_outcome`), finalization
  falls back to the existing batch persistence so current behavior is byte-stable.

## 3. Scope and non-goals

In scope (PR1):

- Incremental parser core and live sink for the one-shot exec runner.
- Live turn streaming for `SessionRuntime._run_turn` (session *send*).
- `progress.json` during the run; live `acpx-stdout.ndjson` and
  `normalized-events.jsonl`; monotonic `seq`.

Explicit non-goals / non-approvals (unchanged from
`docs/roadmap/non-approvals.md`, all still in force):

- No Gateway lifecycle, Feishu/IM delivery, Sachima behavior, public ingress,
  production config writes, real automatic replies, live/default-on behavior,
  `@all`, or agent-to-agent routing.
- No real Codex/acpx live smoke in PR1 — fake subprocess/executor tests only.
- No cursor/subscription API and no long-poll/websocket surface (that is PR2).
- Session management commands (`create`/`status`/`close`/`abort`) remain
  non-streaming management JSON calls in PR1.
- No new CLI surface, no secret/raw-env/log dumps in artifacts or docs.

## 4. Three-PR map

- **PR1 (this plan) — live event stream core.** Incremental parser, live stdout
  sink, live `acpx-stdout.ndjson` / `normalized-events.jsonl` / `progress.json`,
  result-last compatibility, one-shot + session-send live persistence.
- **PR2 — cursor / events API.** A read API over the persisted live stream
  (seq-cursored event fetch, progress polling) for local callers. Still local,
  still no delivery.
- **PR3 — Sachima live progress integration.** Wire a concrete caller to the live
  stream for progress reporting. Requires a separate explicit product approval;
  out of scope here and not approved by authoring this plan.

## 5. Implementation checklist

- [x] `parser.py`: `IncrementalParseState`, `consume_acpx_line`, `feed`,
  `finish`; refactor batch parse to share `_consume_parsed_line`; keep
  `parse_acpx_stdout_bytes` backward compatible.
- [x] `event_store.py`: `RunHandle.append_text` for appending redacted raw NDJSON
  lines at `0600`.
- [x] `live_stream.py`: `LiveEventSink` (redact+append raw line, incremental
  parse, seq-stamped normalized events, `progress.json`).
- [x] `runner.py`: `execute_subprocess` reader-thread live sink + full buffer;
  `SubprocessExecutor` Protocol gains `stdout_sink`; `run()` wires a sink;
  `_finalize_prepared_outcome` dedupes live vs batch and finalizes progress.
- [x] `session_runtime.py`: `_run` gains `stdout_sink`; `_run_turn` wires a live
  sink and dedupes finalization.
- [x] Tests: RED-first live tests (see §7).
- [x] PR2: `hermes_caller/events.py` cursor/progress read API + tests (local-only).

## 6. Files likely to change

- `src/agent_run_supervisor/parser.py`
- `src/agent_run_supervisor/event_store.py`
- `src/agent_run_supervisor/live_stream.py` (new)
- `src/agent_run_supervisor/runner.py`
- `src/agent_run_supervisor/session_runtime.py`
- `tests/test_live_event_stream.py` (new)
- `tests/test_runner_exec.py`, `tests/test_session_runtime.py` (fake-executor
  signature extension for the new `stdout_sink` kwarg)
- `docs/design/result-event-schema.md` (document `progress.json` + `seq`,
  additive-only)

## 7. Acceptance gates

RED-first (must fail on current code, captured before implementation):

- One-shot run: a fake subprocess emits ≥2 NDJSON records with an observation
  point between them; `normalized-events.jsonl` and `progress.json` are visible
  **while the child is still running, before `result.json` exists**.
- Session send: same live persistence for a session turn.
- Partial-line boundary: no normalized event is emitted until the newline
  completes the JSON record.
- Timeout/kill: already-emitted live events and `progress.json` survive a
  supervisor kill, and `result.json` status is `timed_out`.

GREEN gates:

```bash
uv run --no-project --with pytest python -m pytest -q tests/test_runner_exec.py tests/test_session_runtime.py tests/test_live_event_stream.py
uv run --no-project --with pytest python -m pytest -q
python3 -m compileall -q src scripts tests
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

## 8. Risks and open questions

- **Threaded reader.** stdout/stderr are drained on daemon threads so the outer
  watchdog can still time out and kill. Risk: a sink exception must not leak the
  child or block the pipe — the pump captures the first sink error, stops
  sinking, and keeps draining so stdout stays complete and finalization can fall
  back to the full buffer.
- **Batch/live agreement.** Both paths route each record through the same
  `_consume_parsed_line`/`_consume_record`. `max_output_bytes` truncation is
  enforced in the live path as chunks arrive and in the batch fallback path on
  the full buffer; exec and session live-truncation tests cover fail-closed
  `protocol_error` behavior.
- **`seq` addition.** `seq` is additive; existing consumers read event fields via
  `.get(...)` and ignore unknown keys (`hermes_caller/events.py`), so this does
  not break projections or the schema contract (additive-only).

## 9. Rollback

Single-branch, additive change. Rollback = revert the branch: `parser.py` retains
`parse_acpx_stdout_bytes`; `execute_subprocess`'s `stdout_sink` is optional and
defaults to `None`; `LiveEventSink`/`live_stream.py` are new and unreferenced once
the runner/session wiring is reverted. No data migration, no config, no schema
break; `result.json` shape is unchanged so callers are unaffected.

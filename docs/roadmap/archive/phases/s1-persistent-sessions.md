---
title: "S1 — Persistent session support"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: s1-persistent-sessions
---

# S1 — Persistent session support

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## S1 — Persistent session support

Goal: implement controlled persistent ACP/acpx session lifecycle as a first-class product requirement.

Checklist:

- [x] Capture fresh acpx session command fixtures and observed event shapes. *(S1a contract spike — command/schema evidence only.)*
- [x] Extend `AgentRoleSpec` session config for persistent sessions, including bounded lease settings.
- [x] Add session store layout with role/workspace/acpx/policy hashes. *(S1b foundation; no real acpx launch.)*
- [x] Add locks/leases to prevent unsafe concurrent local session mutation. *(S1b foundation.)*
- [x] Add deterministic stale-lock recovery for expired leases. *(S1b foundation; K1 later adds process-liveness crash/runtime recovery beyond expired-lease replacement.)*
- [x] Refuse cross-role, cross-workspace, stale-policy, acpx-version, or adapter-mismatched session reuse before mutation. *(S1b foundation.)*
- [x] Implement real session create/open runtime against fixture-proven acpx session commands. *(S1c MVP: `SessionRuntime.create_session`; fake-executor/fixture acceptance; S1 closure acceptance adds reproducible real-acpx smoke.)*
- [x] Implement session send/continue runtime. *(S1c: lease-locked single `prompt -s` turn with redacted artifacts; S1 closure acceptance proves two-turn continuity.)*
- [x] Implement session status/list where needed. *(S1c: `status -s` query with safe management JSON summary; S1d adds local read-only `list_sessions` over store records, no acpx launch.)*
- [x] Implement session close/abort semantics. *(S1d: fixture-proven `sessions close`/`cancel -s` management commands, redacted close/abort artifacts, atomic `closed` state transition, and closed-session refusal for `send`/`close`/`abort`; fake-executor/fixture acceptance.)*
- [x] Add session parser/event coverage and redaction tests for prompt-turn and management-command schemas. *(S1c: prompt-turn NDJSON parse + `summarize_management_json`; redaction tests in `tests/test_session_runtime.py`.)*
- [x] Add CLI/library session surface. *(S1c MVP: `session create|send|status` + `SessionRuntime` library; S1d adds `session close|abort|list`.)*
- [x] Update feature tracker and roadmap evidence as each remaining S1 slice lands. *(S1c and S1d evidence recorded in `docs/roadmap/features.md` and below.)*

S1a contract-spike evidence (command/schema only, not implementation):

- Plan: `docs/plans/archive/2026-05-30-s1a-session-contract-spike.md`.
- Prompt-turn fixtures (raw `stdout.ndjson`): `fixtures/acpx-0.12.0/session-prompt-turn1/`, `fixtures/acpx-0.12.0/session-prompt-turn2/` (turn2 reuses the same ACP session id and skips `initialize`/`session/new`).
- Management-command fixtures (single-object `stdout.json`): `fixtures/acpx-0.12.0/session-new-named/`, `session-ensure-existing/`, `session-show-open/`, `session-show-after-turns/`, `session-show-closed/`, `session-history-after-turns/`, `session-read-tail-after-turns/`, `session-status-after-turns/`, `session-cancel-no-active/`, `session-close-named/`.
- Cross-checked summary `fixtures/acpx-0.12.0/session-contract-summary.json`, manifest section `session_contract` in `fixtures/acpx-0.12.0/manifest.json`, fixtures README `fixtures/acpx-0.12.0/README.md`, validator `scripts/validate_contract_fixtures.py`, and tests `tests/test_validate_contract_fixtures.py`.

S1b foundation evidence (local store/binding/lock only, not acpx runtime):

- Plan: `docs/plans/archive/2026-05-30-s1b-session-store-locks.md`.
- Role/session config and exec fail-closed guard: `src/agent_run_supervisor/role.py`, `policy.py`, `runner.py`, `commands.py`; tests `tests/test_role.py`, `tests/test_session_strategy_guard.py`, `tests/test_cli_commands.py`.
- Session store, workspace binding, and lease locks: `src/agent_run_supervisor/session.py`, `workspace.py`, `event_store.py`; tests `tests/test_session_store.py`, `tests/test_workspace_gate.py`, `tests/test_event_store.py`.
- This evidence proves local session artifacts, hash binding, mismatch refusal, lock contention, token release, and expired-lock replacement. It does **not** prove real acpx persistent-session runtime, parser/event handling, CLI lifecycle commands, close/abort runtime semantics, or full crash recovery.

S1c runtime MVP evidence (local create/send/status runtime; fake-executor/fixture acceptance, no real acpx launch):

- Plan: `docs/plans/archive/2026-05-31-s1c-session-runtime-mvp.md`.
- Session command compilers (`compile_session_create/ensure/show/status/prompt_command`) and the `ensure_persistent_strategy` guard: `src/agent_run_supervisor/policy.py`; tests `tests/test_policy.py`.
- Management-command JSON summarizer (`summarize_management_json`, `ManagementParseError`) that keeps prompt-turn NDJSON and management JSON on separate parsers: `src/agent_run_supervisor/parser.py`; tests `tests/test_parser.py`.
- `SessionRuntime` (`create_session`/`send`/`status`) over the S1b store: role/cwd/binding revalidation before mutation, lease acquire/release on every send (released on success **and** failure), redacted turn artifacts under `sessions/<session_id>/turns/<turn_id>/` and redacted `management/` evidence, prompt-turn NDJSON parsing with `business_verdict: null`: `src/agent_run_supervisor/session_runtime.py`; tests `tests/test_session_runtime.py`.
- CLI `session create|send|status` with JSON stdout and 0/nonzero exit codes: `src/agent_run_supervisor/cli.py`, `src/agent_run_supervisor/commands.py`; tests `tests/test_cli_commands.py`.
- This evidence proves the local create/send/status runtime slice, lease release on success and failure, binding-mismatch refusal before subprocess/artifact mutation, no-shell argv, and redaction of prompt/stdout/stderr/final-message. It does **not** prove real acpx launch, close/abort runtime/semantics, session list, multi-turn resume, crash/interruption recovery, or retention/cleanup.

S1d lifecycle completion evidence (local close/abort/list + closed-session refusal; fake-executor/fixture acceptance, no real acpx launch):

- Plan: `docs/plans/archive/2026-05-31-s1d-session-lifecycle-completion.md`.
- Close/cancel command compilers (`compile_session_close_command` → `sessions close <name>`, `compile_session_cancel_command` → `cancel -s <name>`) pinned to S1a fixtures `session-close-named`/`session-cancel-no-active`, management-only flags, no shell, persistent-strategy guarded: `src/agent_run_supervisor/policy.py`; tests `tests/test_policy.py`.
- Atomic lifecycle state in the store: `lifecycle_guard` (separate local close/abort serialization), `mark_closed` (atomic 0600 rewrite, refuses double-close under the per-session guard), `ensure_open` (fail-closed gate), and read-only `list_records`: `src/agent_run_supervisor/session.py`; tests `tests/test_session_store.py` and `tests/test_session_runtime.py`.
- `SessionRuntime.close`/`abort`/`list_sessions`: binding revalidation before mutation, lifecycle-guarded close/abort rechecks before subprocess/artifact work, redacted `management/close.json`/`management/abort.json`, atomic `closed` transition on close, honest `cancelled: true|false` (never a business verdict) on abort, and local read-only list with minimal redacted record summaries; `send` also re-checks closed state under the acquired lease before subprocess/artifact mutation: `src/agent_run_supervisor/session_runtime.py`; tests `tests/test_session_runtime.py`.
- CLI `session close|abort|list` with JSON stdout and 0/nonzero exit codes (`list` is role-optional and read-only): `src/agent_run_supervisor/cli.py`, `src/agent_run_supervisor/commands.py`; tests `tests/test_cli_commands.py`.
- The management-command summarizer already allow-lists `session_closed`/`cancel_result` (`closed`/`cancelled` fields), so no parser change was required: `src/agent_run_supervisor/parser.py`; tests `tests/test_parser.py`.
- This evidence proves the local close/abort/list slice, closed-session fail-closed refusal before subprocess/artifact mutation, atomic state transition, honest cancel semantics, and redacted management evidence. It does **not** prove real acpx launch, multi-turn resume, crash/interruption recovery, or retention/cleanup.

S1 closure-acceptance evidence (real-acpx smoke + multi-turn continuity; closes S1 for the local persistent-session lifecycle, no new S1 subphase):

- Plan: `docs/plans/archive/2026-05-31-s1-closure-acceptance.md`.
- Reproducible local real-acpx smoke: `scripts/smoke_persistent_session.py` (stdlib-only) drives the existing CLI surface (`validate-role` → `session create` → `send` turn 1 → `send` turn 2 → `status` → `list` → `close`) against a persistent reviewer role with `acpx_binary: null`, so the compiler resolves the pinned `npx -y acpx@0.12.0` prefix and the smoke requires `npx` explicitly. It generates a unique real acpx session name per run, verifies both turn markers, status ok, list count 1, close state `closed`, two distinct redacted turn dirs, and `business_verdict: null` on every lifecycle result; it cleans its temp scratch/sessions artifacts by default (`--keep-artifacts` retains them), best-effort closes after post-create smoke failures, and never commits raw local smoke output.
- Multi-turn continuity regression: `tests/test_session_runtime.py::test_two_sequential_sends_reuse_record_persist_distinct_turns_and_release_lease` proves two sequential `SessionRuntime.send(...)` calls against the same local session reuse the same record (no second create), drive the same acpx session name, persist two distinct turn dirs, release the lease after each turn, keep the record open, and parse the S1a fixtures `session-prompt-turn1`/`session-prompt-turn2` (`S1A_SESSION_TURN_1_OK`/`S1A_SESSION_TURN_2_OK`). `tests/test_smoke_persistent_session.py` also covers the npx precondition and best-effort close after a post-create smoke failure so failed smokes do not leak real persistent sessions.
- Recorded manual real-acpx proof: the current CLI ran a full local persistent-session lifecycle via `npx -y acpx@0.12.0` (create, two sends, status, list, close all succeeded) with observed turn markers `S1_CLOSURE_TURN_1_OK` and `S1_CLOSURE_TURN_2_OK`; raw artifacts remain local-only and outside git.
- This closes S1 for the local persistent-session lifecycle (create/send/multi-turn-resume/status/list/close, binding-mismatch refusal, lease handling, expired-lease recovery, redacted artifacts). It deliberately does **not** claim full crash/interruption recovery beyond deterministic expired-lease replacement, artifact retention/cleanup, or any caller integration — those are H1 / I1 carry-overs below.

Acceptance:

- Fixture validator or session-specific fixture validator passes.
- Session lifecycle tests cover create, send, multi-turn resume, status, list, close, stale (expired) lock recovery, and mismatch refusal; a reproducible local real-acpx smoke plus recorded manual real-acpx evidence exercise the same lifecycle end to end.
- Full crash/interruption recovery beyond deterministic expired-lease replacement was carried out of S1 into operational hardening and later closed by K1 (`ARS-CRASH-RECOVERY` via PR #22), not an S1 blocker.
- Session artifacts are redacted and local-only.
- No public ingress, real delivery, Gateway lifecycle, or agent-to-agent auto-routing is introduced.

Status: **Closed for the local persistent-session lifecycle after S1a + S1b + S1c + S1d + closure acceptance. S1a captured persistent-session command grammar and stdout-schema evidence; S1b implemented the local role/session-config, store, binding, and lease-lock foundation; S1c implements the local create/send/status runtime MVP with fake-executor/fixture acceptance, safe management summaries, redacted turn artifacts, and CLI/library `session create|send|status`; S1d adds local lifecycle completion — fixture-proven close and abort/cancel, atomic `closed` state transition with closed-session refusal, local read-only `list`, redacted management evidence, and CLI/library `session close|abort|list`; closure acceptance adds multi-turn continuity regression coverage and a reproducible local real-acpx persistent-session smoke (`scripts/smoke_persistent_session.py`) plus recorded manual real-acpx lifecycle evidence. S1 remains exactly S1a/S1b/S1c/S1d; closure introduces no new subphase. Carried out of S1 as non-blockers: H1 delivered artifact retention/cleanup and detection-first crash hygiene; K1 later closed full process-liveness crash recovery. Caller integration stays parked at I1.**

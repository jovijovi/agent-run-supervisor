---
title: "agent-run-supervisor Roadmap Current Status"
status: active
created_at: 2026-05-28
last_validated_at: 2026-07-06T16:00:00+0800
---
# agent-run-supervisor Roadmap Current Status

> Living roadmap, phase tracker, and implementation-stage acceptance register. This file replaces the deleted standalone implementation plan.

```text
last_updated: 2026-07-06
base_branch: main
product_role: independent local Python library + dev CLI for supervising ACP/acpx AGENT runs and sessions with redacted audit evidence
source_of_truth: GOAL.md, docs/product/prd.md, docs/design/architecture.md, docs/design/technical-solution.md, docs/roadmap/features.md, docs/roadmap/current-status.md, docs/AI_FLOW.md
current_mainline: E1 local one-shot exec runner is merged and closed on main via PR #8 (21b3393); F-EXEC-001 is Done; S1a session contract evidence is merged via PR #14 (99637c6); S1b adds the local session store/lock foundation; S1c adds the local create/send/status runtime MVP; S1d adds local lifecycle completion (close/abort/list + closed-session refusal); S1 closure acceptance adds multi-turn continuity regression coverage and a reproducible local real-acpx persistent-session smoke (scripts/smoke_persistent_session.py), closing S1 for the local persistent-session lifecycle (S1a-S1d + closure evidence); H1 operational hardening is merged on main via PR #19 at 484ae23 — the full read-only doctor probe set (ARS-DOCTOR-COMPLETE), confined dry-run-first run/session retention/cleanup (ARS-RETENTION-CLEANUP, F-RETENTION-001), the caller-stable result/event schema doc (docs/design/result-event-schema.md), and a narrow detection-first crash tail (read-only stale-lock/.tmp-* detection + provably-expired lock hygiene); K1 process-liveness crash recovery is merged on main via PR #22 at 0ad531e, closing ARS-CRASH-RECOVERY with conservative holder-set classification and safe composite supervisor+child reclaim semantics, and the I1 generic local caller boundary work is merged on main via PR #20 at 83d9cb2 with all standing non-approvals still in force; L1 concrete-caller integration design is merged and closed on main via PR #24 at 5e34f5c as a design-only phase (docs/plans/2026-06-01-l1-concrete-caller-integration-design.md — Hermes caller on the I1 boundary, Feishu document-check as presentation target only), and L2 local/offline Hermes caller + offline Feishu view-model implementation is merged and closed on main via PR #27 at eb7912e (`src/agent_run_supervisor/hermes_caller/`, `tests/hermes_caller/`), with live/Sachima/Feishu/Gateway behavior still parked
```

## 1. How to read this roadmap

The document hierarchy is:

```text
PRD -> technical/design docs -> roadmap/current-status + feature tracker -> approved phase implementation plan (docs/plans/)
```

- `GOAL.md` defines stable product positioning and points to source-of-truth docs.
- `docs/product/prd.md` defines product requirements.
- `docs/design/architecture.md` gives the system-level architecture (diagrams and boundaries).
- `docs/design/technical-solution.md` defines the module-level technical solution.
- `docs/roadmap/features.md` tracks feature/capability completion.
- This file tracks engineering phases, status, tails, and acceptance criteria. It does not hold task-level execution plans.
- Per-phase implementation plans are created only for newly approved implementation work and live under `docs/plans/` named `YYYY-MM-DD-<task-slug>.md` (see `docs/plans/README.md`); the pre-realignment `docs/plans/` and `docs/dev_log/` artifacts were retired and remain non-authoritative.

## 2. Current decision

```text
Documentation authority realignment is complete on main via PR #6 (`7dcbe4f`).
The product requirement includes both one-shot exec and persistent sessions.
Engineering sequence may implement exec first, then persistent sessions.
Exec-first sequencing belongs in roadmap/phase planning only, not in PRD, GOAL, or product-level design as a reduced product scope.
Current implementation: E1 local one-shot exec runner is merged and closed on main via PR #8 (`21b3393`); F-EXEC-001 is Done. S1a persistent-session contract evidence is merged via PR #14 (`99637c6`). S1b adds the local session store/lock foundation. S1c adds the local create/send/status runtime MVP. S1d adds the local lifecycle completion slice (close, abort/cancel, local read-only list, and closed-session refusal). S1 closure acceptance adds multi-turn continuity regression coverage and a reproducible local real-acpx persistent-session smoke, closing S1 for the **local persistent-session lifecycle** (S1a–S1d plus closure evidence). S1 remains exactly the four approved slices (S1a/S1b/S1c/S1d); closure acceptance introduces no new S1 subphase. H1 operational hardening is merged on `main` via PR #19 at `484ae23`: the full read-only doctor probe set, confined dry-run-first run/session retention/cleanup, the caller-stable result/event schema doc, and a narrow detection-first crash tail. I1 is merged on `main` via PR #20 at `83d9cb2` as a generic local caller boundary implemented as a library surface. K1 is merged on `main` via PR #22 at `0ad531e`, closing `ARS-CRASH-RECOVERY` with process-liveness crash/interruption recovery beyond deterministic expired-lease replacement, conservative holder-set classification, and safe composite supervisor+child reclaim semantics. L1 concrete-caller integration design is merged and closed on `main` via PR #24 at `5e34f5c` as a **design-only** phase (Hermes caller built on the I1 boundary; Feishu document-check as a presentation target only). L2 local/offline Hermes caller + offline Feishu view-model implementation is merged and closed on `main` via PR #27 at `eb7912e`, adding `src/agent_run_supervisor/hermes_caller/` and `tests/hermes_caller/` while keeping real Sachima/Feishu/Gateway behavior parked and unapproved. All standing non-approvals (§5) remain in force.
```

## 3. Phase roadmap

### R0 — Documentation authority realignment

Goal: make documentation the product authority, delete obsolete mixed/stale docs, and prevent old plan/dev-log artifacts from driving future work.

Checklist:

- [x] Simplify `GOAL.md` into stable product positioning and source-of-truth index.
- [x] Move product requirements into `docs/product/prd.md`.
- [x] Move technical/architecture/session/runner design into `docs/design/technical-solution.md`.
- [x] Add feature completion management at `docs/roadmap/features.md`.
- [x] Merge standalone implementation-plan content into this roadmap/status document.
- [x] Remove the retired mixed V0.1a design file.
- [x] Remove the stale V0.1c manual-approval design file.
- [x] Clear obsolete `docs/dev_log/` files.
- [x] Clear obsolete `docs/plans/` files.
- [x] Rebuild `docs/INDEX.md` and `docs/lessons/_drift_report.md`.
- [x] Merge documentation authority realignment via PR #6 (`7dcbe4f`) and verify main CI.

Acceptance:

- Docs index/drift gates pass.
- No active doc names deleted design/dev-log/plan files as source-of-truth.
- PRD/DESIGN/GOAL do not reduce the product to exec-only.
- Roadmap clearly sequences exec before persistent sessions as engineering order only.

Status: **Complete on main via PR #6 (`7dcbe4f`); main `Verify` CI passed and post-merge local gates passed.**

### C0 — acpx contract fixtures and validator

Goal: preserve the observed acpx contract that implementation and tests rely on.

Checklist:

- [x] Capture real `acpx@0.12.0` fixtures.
- [x] Capture command grammar and observed stdout schema for current fixture family.
- [x] Validate fixture naming, schema markers, exit semantics, and secret-shaped content.
- [x] Record that `allowed_roots` is cwd/config evidence only, not a sandbox.

Acceptance:

- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0` passes.

Status: **Complete**.

### F0 — Role/policy/parser/store foundation

Goal: provide the reusable core supervisor foundation without relying on a true subprocess/session launch.

Checklist:

- [x] `AgentRoleSpec` model and validation.
- [x] role hash.
- [x] permission policy compiler.
- [x] acpx argv compiler for current run shape.
- [x] cwd/allowed-roots gate.
- [x] exit classifier.
- [x] observed stdout parser.
- [x] EventStore permissions/atomic writes.
- [x] redaction helpers.
- [x] CLI `validate-role`, `replay`, `doctor` baseline.
- [x] CLI `run --no-real-run` artifact compilation.
- [x] Pre-E1 real-run refusal stayed stable until the E1 runner replaced it.

Acceptance:

- pytest passes.
- compileall passes.
- CLI doctor/replay smoke passes.
- Pre-E1 real-run path refused without launching a process until E1 replaced that refusal with supervised local exec.

Status: **Complete as foundation; not product-complete**.

### E1 — One-shot exec runner completion

Goal: implement real local acpx exec supervision under role-bound authorization.

Checklist:

- [x] Add subprocess execution abstraction accepting compiled argv, effective cwd, timeout, and environment snapshot.
- [x] Prove no shell interpolation.
- [x] Capture stdout/stderr from subprocess into EventStore.
- [x] Parse stdout with fixture-proven parser.
- [x] Add fake subprocess tests for success, nonzero exits, malformed stdout, permission denied, stderr redaction, interruption, timeout, and kill paths.
- [x] Implement outer watchdog with grace and process-group handling where supported.
- [x] Record kill metadata: `kill_reason`, `kill_signal`, `grace_ms`, `process_group_used`, stdout/stderr truncation/closure state.
- [x] Connect CLI/library `run` without `--no-real-run` to the exec runner after tests are green.
- [x] Preserve `business_verdict: null` and caller-owned business interpretation.
- [x] Run a minimal local acpx smoke in a scratch repo after fake-runner gates pass.
- [x] Update `docs/roadmap/features.md` and this file with evidence.

Acceptance:

- Existing tests continue to pass.
- New fake runner/watchdog tests pass.
- `python3 -m compileall -q src scripts tests` passes.
- Fixture validator passes.
- Doctor/replay smoke passes.
- Secret/static scans show no unsafe runner patterns.
- Local smoke evidence is redacted and does not use production/Gateway/Sachima integration.

Status: **Done — merged and closed on main via PR #8 (`21b3393`); `docs/roadmap/features.md` records F-EXEC-001 as Done. Evidence includes `tests/test_runner_exec.py`, full local gates, and local smoke `/tmp/agent-run-supervisor-e1-smoke/result.json` with `final_message=AGENT_RUN_SUPERVISOR_E1_OK`. Persistent sessions remain S1.**

### S1 — Persistent session support

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

- Plan: `docs/plans/2026-05-30-s1a-session-contract-spike.md`.
- Prompt-turn fixtures (raw `stdout.ndjson`): `fixtures/acpx-0.12.0/session-prompt-turn1/`, `fixtures/acpx-0.12.0/session-prompt-turn2/` (turn2 reuses the same ACP session id and skips `initialize`/`session/new`).
- Management-command fixtures (single-object `stdout.json`): `fixtures/acpx-0.12.0/session-new-named/`, `session-ensure-existing/`, `session-show-open/`, `session-show-after-turns/`, `session-show-closed/`, `session-history-after-turns/`, `session-read-tail-after-turns/`, `session-status-after-turns/`, `session-cancel-no-active/`, `session-close-named/`.
- Cross-checked summary `fixtures/acpx-0.12.0/session-contract-summary.json`, manifest section `session_contract` in `fixtures/acpx-0.12.0/manifest.json`, fixtures README `fixtures/acpx-0.12.0/README.md`, validator `scripts/validate_contract_fixtures.py`, and tests `tests/test_validate_contract_fixtures.py`.

S1b foundation evidence (local store/binding/lock only, not acpx runtime):

- Plan: `docs/plans/2026-05-30-s1b-session-store-locks.md`.
- Role/session config and exec fail-closed guard: `src/agent_run_supervisor/role.py`, `policy.py`, `runner.py`, `commands.py`; tests `tests/test_role.py`, `tests/test_session_strategy_guard.py`, `tests/test_cli_commands.py`.
- Session store, workspace binding, and lease locks: `src/agent_run_supervisor/session.py`, `workspace.py`, `event_store.py`; tests `tests/test_session_store.py`, `tests/test_workspace_gate.py`, `tests/test_event_store.py`.
- This evidence proves local session artifacts, hash binding, mismatch refusal, lock contention, token release, and expired-lock replacement. It does **not** prove real acpx persistent-session runtime, parser/event handling, CLI lifecycle commands, close/abort runtime semantics, or full crash recovery.

S1c runtime MVP evidence (local create/send/status runtime; fake-executor/fixture acceptance, no real acpx launch):

- Plan: `docs/plans/2026-05-31-s1c-session-runtime-mvp.md`.
- Session command compilers (`compile_session_create/ensure/show/status/prompt_command`) and the `ensure_persistent_strategy` guard: `src/agent_run_supervisor/policy.py`; tests `tests/test_policy.py`.
- Management-command JSON summarizer (`summarize_management_json`, `ManagementParseError`) that keeps prompt-turn NDJSON and management JSON on separate parsers: `src/agent_run_supervisor/parser.py`; tests `tests/test_parser.py`.
- `SessionRuntime` (`create_session`/`send`/`status`) over the S1b store: role/cwd/binding revalidation before mutation, lease acquire/release on every send (released on success **and** failure), redacted turn artifacts under `sessions/<session_id>/turns/<turn_id>/` and redacted `management/` evidence, prompt-turn NDJSON parsing with `business_verdict: null`: `src/agent_run_supervisor/session_runtime.py`; tests `tests/test_session_runtime.py`.
- CLI `session create|send|status` with JSON stdout and 0/nonzero exit codes: `src/agent_run_supervisor/cli.py`, `src/agent_run_supervisor/commands.py`; tests `tests/test_cli_commands.py`.
- This evidence proves the local create/send/status runtime slice, lease release on success and failure, binding-mismatch refusal before subprocess/artifact mutation, no-shell argv, and redaction of prompt/stdout/stderr/final-message. It does **not** prove real acpx launch, close/abort runtime/semantics, session list, multi-turn resume, crash/interruption recovery, or retention/cleanup.

S1d lifecycle completion evidence (local close/abort/list + closed-session refusal; fake-executor/fixture acceptance, no real acpx launch):

- Plan: `docs/plans/2026-05-31-s1d-session-lifecycle-completion.md`.
- Close/cancel command compilers (`compile_session_close_command` → `sessions close <name>`, `compile_session_cancel_command` → `cancel -s <name>`) pinned to S1a fixtures `session-close-named`/`session-cancel-no-active`, management-only flags, no shell, persistent-strategy guarded: `src/agent_run_supervisor/policy.py`; tests `tests/test_policy.py`.
- Atomic lifecycle state in the store: `lifecycle_guard` (separate local close/abort serialization), `mark_closed` (atomic 0600 rewrite, refuses double-close under the per-session guard), `ensure_open` (fail-closed gate), and read-only `list_records`: `src/agent_run_supervisor/session.py`; tests `tests/test_session_store.py` and `tests/test_session_runtime.py`.
- `SessionRuntime.close`/`abort`/`list_sessions`: binding revalidation before mutation, lifecycle-guarded close/abort rechecks before subprocess/artifact work, redacted `management/close.json`/`management/abort.json`, atomic `closed` transition on close, honest `cancelled: true|false` (never a business verdict) on abort, and local read-only list with minimal redacted record summaries; `send` also re-checks closed state under the acquired lease before subprocess/artifact mutation: `src/agent_run_supervisor/session_runtime.py`; tests `tests/test_session_runtime.py`.
- CLI `session close|abort|list` with JSON stdout and 0/nonzero exit codes (`list` is role-optional and read-only): `src/agent_run_supervisor/cli.py`, `src/agent_run_supervisor/commands.py`; tests `tests/test_cli_commands.py`.
- The management-command summarizer already allow-lists `session_closed`/`cancel_result` (`closed`/`cancelled` fields), so no parser change was required: `src/agent_run_supervisor/parser.py`; tests `tests/test_parser.py`.
- This evidence proves the local close/abort/list slice, closed-session fail-closed refusal before subprocess/artifact mutation, atomic state transition, honest cancel semantics, and redacted management evidence. It does **not** prove real acpx launch, multi-turn resume, crash/interruption recovery, or retention/cleanup.

S1 closure-acceptance evidence (real-acpx smoke + multi-turn continuity; closes S1 for the local persistent-session lifecycle, no new S1 subphase):

- Plan: `docs/plans/2026-05-31-s1-closure-acceptance.md`.
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

### H1 — Operational hardening

Goal: close long-lived-use tails.

Checklist:

- [x] Doctor probes adapter availability without launching AGENT work. *(W1: `preflight.probe_adapter` — declared + hostable only, never launches the adapter/agent.)*
- [x] Doctor detects runtime `npx` fetch risk. *(W1: `preflight.probe_npx` — read-only `npx --version`; `fetch_risk` true only when no explicit `acpx_binary`; never runs `npx acpx`.)*
- [x] Doctor checks policy parseability safely. *(W1: `preflight.probe_policy` — pure-local permission-policy compile, asserts `default_action == "deny"`, no subprocess.)*
- [x] Doctor reports role cwd/allowed-roots validation. *(W1: `preflight.probe_workspace` — reuses the workspace intent gate; always reports the not-a-sandbox disclaimer.)*
- [x] Doctor reports redaction probe. *(W1: `preflight.probe_redaction` — synthetic pattern-shaped samples only, asserts `leaked == []`.)*
- [x] Doctor reports session readiness after S1. *(W1/W4: `preflight.probe_session_readiness` — temp-store `0700`/`0600` mode probe + read-only stale-lock detection; no acpx launch.)*
- [x] Retention/cleanup knobs exist for run/session artifacts. *(W2: `retention.plan_cleanup`/`apply_cleanup` + `agent-run-supervisor cleanup` CLI — confined, dry-run-first.)*
- [x] Result/event schema is documented for caller stability. *(W3: `docs/design/result-event-schema.md`, pinned by `tests/test_result_event_schema.py`.)*

Acceptance:

- Doctor tests cover success/failure for each probe. *(`tests/test_preflight.py`, `tests/test_cli_commands.py`.)*
- Retention tests prove safe deletion/listing boundaries. *(`tests/test_retention.py`, `tests/test_cli_commands.py`.)*
- Feature tracker marks hardening tails complete. *(`docs/roadmap/features.md`: `F-CLI-003` Done, `F-RETENTION-001` Done.)*

H1 evidence (merged on `main` via PR #19 at `484ae23`; four workstreams):

- **W1 — Doctor completion (`ARS-DOCTOR-COMPLETE`).** Full read-only probe set in
  `src/agent_run_supervisor/preflight.py` (`probe_policy`, `probe_workspace`,
  `probe_redaction`, `probe_npx`, `probe_adapter`, `probe_session_readiness`) wired into
  `cmd_doctor` (`src/agent_run_supervisor/commands.py`). Every probe is read-only:
  `launched_real_agent` stays `false`; no probe runs `acpx exec`, the adapter, a session
  prompt, or an `npx` fetch. `ok` gates only on pure-local deterministic probes so the no-role
  CI `doctor` still exits `0`; role-dependent probes run only with `--role`. Tests:
  `tests/test_preflight.py`, `tests/test_cli_commands.py`.
- **W2 — Retention/cleanup (`ARS-RETENTION-CLEANUP`, `F-RETENTION-001`).** New
  `src/agent_run_supervisor/retention.py` (`plan_cleanup`/`apply_cleanup`,
  `RetentionPolicy`, `CleanupCandidate`/`CleanupPlan`/`CleanupResult`, `RetentionError`) plus
  the `agent-run-supervisor cleanup` command (`cli.py`, `commands.py`). Dry-run is the default;
  `--apply` deletes **only** planned entries. Hard artifact-root confinement to a resolved
  `.agent-run-supervisor` root, symlink-escape refusal, open/live-locked-session protection,
  and TOCTOU re-checks at apply time. Tests: `tests/test_retention.py`, `tests/test_cli_commands.py`.
- **W3 — Caller-stable schema doc.** `docs/design/result-event-schema.md` documents the
  `result.json` payload, session result projections, statuses/error codes, normalized event
  families, `doctor` output, and the cleanup plan/result shape, with an additive-only
  stability contract (`business_verdict` always `null`). Fidelity test
  `tests/test_result_event_schema.py` pins the documented top-level `result.json` key set
  against `result.build_result_payload`.
- **W4 — Narrow detection-first crash tail.** Read-only
  `SessionStore.detect_stale_locks` (`src/agent_run_supervisor/session.py`) reports expired
  leases and `.tmp-*` debris (takes no lock, removes nothing, signals no process); it is
  surfaced by `probe_session_readiness` and consumed by the cleanup planner, and provably
  expired `lock.json` files are removed only as part of an eligible session dir. Tests:
  `tests/test_session_store.py`. **Carry-over:** full process-liveness crash/interruption
  recovery beyond deterministic expired-lease replacement (no PID inspection, signals, or
  live-session takeover) was deliberately deferred at H1 close; K1 later closed
  `ARS-CRASH-RECOVERY` on `main` via PR #22 (`0ad531e`) with safe
  process-liveness recovery.

H1 introduces no non-approval from §5: no Sachima/Gateway/IM/public-ingress/production-config/
real-delivery/service-restart/live/default-on/automatic-reply behavior; `doctor` never launches
an AGENT and `cleanup` never deletes outside a resolved `.agent-run-supervisor` root.

Status: **Merged on `main` via PR #19 at `484ae23`; user-provided status says main CI passed.**
W1–W4 are complete; `ARS-DOCTOR-COMPLETE` and `ARS-RETENTION-CLEANUP` are closed (see §4).
Full process-liveness crash recovery was later closed by K1 on `main` via PR #22 (`0ad531e`); H1 itself remains the detection-first slice.

### I1 — Thin caller integration

Goal: add a generic, local-only library caller boundary while keeping the supervisor independent.

Checklist before start:

- [x] E1 exec support merged and verified.
- [x] S1 session support merged and verified for local persistent-session lifecycle.
- [x] H1 operational hardening merged on `main` via PR #19 at `484ae23`.
- [x] Generic caller responsibility split is documented: caller chooses role/prompt/context/cwd/mode/artifact dirs and owns verdict/rendering.
- [x] No public ingress / real delivery / Gateway lifecycle operation is implied.
- [x] Caller owns business verdict and rendering.

Acceptance:

- `caller.py` remains a local library-only wrapper over `SupervisorRunner` and `SessionRuntime`.
- `CallerResult` wraps existing supervisor payload/projection and keeps `business_verdict: null`.
- Tests use fake executors and dry-run only; no real external service/platform smoke is required.
- No new CLI, public ingress, real delivery, Gateway lifecycle, automatic reply, live/default-on behavior, `@all`, or agent-to-agent routing.

Status: **Merged on `main` via PR #20 at `83d9cb2` as a generic
local library boundary; main `Verify` CI passed.** Evidence: `src/agent_run_supervisor/caller.py`,
`tests/test_caller.py`, and design/schema docs. Concrete Sachima/Gateway/IM/public-ingress/
delivery/auto-reply behavior remains unapproved and separate; the standing non-approvals (§5)
all hold.

### L1 — Concrete caller integration design (design-only)

Goal: design — **without implementing** — how a concrete local caller (Hermes) drives the
supervisor through the generic I1 boundary for a document-check scenario, in both `exec` and
`persistent session` modes.

- **Type:** design-only. Implements no runtime code; changes no runtime module, CLI, config,
  or `caller.py`; grants no live/runtime approval.
- **Scope:** named concrete caller (Hermes); Feishu document-check scenario as a
  **presentation target only**; exec + persistent-session flows; input/output contracts
  reused unchanged from I1; normalized-event → caller-owned view-model mapping; ownership
  matrix; defined-but-unapproved Sachima seam.
- **Invariants preserved:** supervisor stays generic (no platform field in
  `CallerInvocationSpec`/`CallerResult`); `business_verdict` stays `null` and caller-owned;
  Feishu rich cards stay a caller-owned view-model/presentation adapter with **no delivery**;
  agent output stays untrusted (no trusted Markdown/HTML rendering).
- **Plan:** `docs/plans/2026-06-01-l1-concrete-caller-integration-design.md`.
- **Parked / unapproved (unchanged):** real Feishu/IM delivery, platform ingress, Sachima
  behavior, Gateway lifecycle, automatic replies, live/default-on. Connecting Hermes to
  Sachima for real debugging/testing is a separate later approved phase; L1 only defines the
  seam.

Status: **Closed on `main` via PR #24 (`5e34f5c`).** PR #24 passed CI, Codex primary post-PR review, Mermaid render checks, docs index/drift gates, full local gates, and post-merge verification.
No implementation, live behavior, Sachima/Feishu/Gateway integration, or scope change was
introduced; all standing non-approvals (§5) remain in force.

### L2 — Hermes caller + offline Feishu view-model implementation (local/offline)

Goal: implement the approved concrete Hermes caller and offline Feishu rich-card view-model
adapter above the generic I1 boundary, covering both one-shot `exec` and persistent-session
document-check flows while keeping the supervisor generic.

Checklist:

- [x] Add the caller-side package `src/agent_run_supervisor/hermes_caller/` with `task`,
  `intake`, caller-owned `verdict`, normalized-event evidence projection, view-model,
  offline Feishu payload adapter, and `HermesDocCheckCaller` orchestration.
- [x] Add `tests/hermes_caller/` RED/GREEN coverage for intake, verdict derivation,
  normalized-event projections, progress/result view-models, escaped offline card payloads,
  exec flow, persistent-session flow, and static forbidden-surface guards.
- [x] Preserve the I1 generic contract: no platform fields in `CallerInvocationSpec` /
  `CallerResult`, supervisor `business_verdict` remains `null`, and caller-owned verdicts live
  only in `hermes_caller.verdict` / view-models.
- [x] Keep everything stdlib-only and fake/local/offline: no Feishu SDK/API, no IM delivery,
  no public ingress, no Gateway/Sachima behavior, no automatic replies, no live/default-on
  behavior, and no trusted Markdown/HTML rendering.

Acceptance evidence for this branch:

- `python3 -m pytest -q tests/hermes_caller` → 39 passed.
- `python3 -m pytest -q` → full suite passed.
- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0` → passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src scripts tests` → passed.
- `PYTHONPATH=src python3 -m agent_run_supervisor doctor` → `ok: true`, `launched_real_agent: false`.
- `PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson` → `final_message: CODEX_ACPX_OK`, `business_verdict: null`.

Status: **Closed on `main` via PR #27 (`eb7912e`).** PR #27 passed CI, Codex primary
post-PR review, full local post-merge gates, docs index/drift checks, and post-merge verification.

### P3 — Engineering basics (uv + verify + PyPI)

Goal: reproducible local dev with uv, CI/local gate alignment, and tag-triggered PyPI Trusted
Publishing for the first public release — without changing supervisor runtime behavior or introducing
runtime dependencies.

Checklist:

- [x] Track `uv.lock` for reproducible dev tooling (`dev` + `release` extras only; runtime stays
  stdlib-only).
- [x] Add `scripts/verify_local.sh` as the single local gate entry mirroring §6 and CI.
- [x] Add `scripts/smoke_installed_wheel.sh` for reusable installed-wheel smoke.
- [x] Migrate `.github/workflows/verify.yml` to uv (`astral-sh/setup-uv@v5`, Python 3.11/3.12 matrix,
  `concurrency` cancel-in-progress).
- [x] Bump `pyproject.toml` to `0.1.0`; add `CHANGELOG.md`.
- [x] Add `.github/workflows/release.yml` (tag `v*`, OIDC Trusted Publishing, environment `pypi`).
- [x] Update README Development + Publishing sections; governance docs and plan
  `docs/plans/2026-07-06-p3-engineering-basics.md`.

Acceptance:

- `uv sync --extra dev --extra release` + `./scripts/verify_local.sh` pass locally.
- CI `Verify` workflow uses uv and `./scripts/verify_local.sh` on Python 3.11 and 3.12.
- `release.yml` ready for maintainer Trusted Publisher setup; no secrets in repo.
- First live `pip install agent-run-supervisor==0.1.0` awaits operator tag push (`v0.1.0`).

Status: **Implementation complete; first PyPI publish pending operator Trusted Publisher config and
tag push.** All §5 non-approvals remain in force.

### Phase B — ARS evidence hardening (support for the external Sachima controlled-local-execution PRD)

Evidence/test-only; **not** a new ARS product phase. This is *Phase B — `agent-run-supervisor`
role support hardening* from the external *Sachima × agent-run-supervisor Controlled Local Agent
Execution* PRD gate and the Claude architect design packet (rev. 2). It adds a **local
static/compiler-evidence gate** — parametrized `compile_command` golden tests in
`tests/test_policy.py` pinning the pinned-local `runner.acpx_binary` prefix (no `npx`) and
`<adapter> exec <prompt>` for `adapter_agent in {codex, claude}`, with default-deny permission
policy and argv-list/no-shell behavior preserved — plus the plan
`docs/plans/2026-06-12-phase-b-ars-evidence-hardening.md`. It makes **no schema or runtime
behavior change**. This host currently has **no local `acpx` binary**, so a truthful
strict-offline real Claude `acpx` fixture capture is **blocked** and carried forward; Phase B
captures **no** new live fixture and runs no `npx`/`acpx`. It approves **no** live behavior and
**no** Sachima integration; all §5 non-approvals remain in force.

## 4. Tail register

| ID | Class | Description | Blocks code work? | Required before | Acceptance method | Status |
|---|---|---|---:|---|---|---|
| ARS-SANDBOX-BOUNDARY | PARKED | Any claim that `allowed_roots` is an OS/filesystem sandbox remains parked. | No | Separate sandbox phase | OS sandbox proof + negative probes | Parked |
| ARS-CALLER-INTEGRATION | PARTIAL | Generic local library caller boundary is implemented in I1; the concrete caller (Hermes) is captured **design-only** in L1 (`docs/plans/2026-06-01-l1-concrete-caller-integration-design.md`, exec + persistent-session document-check, Feishu card as caller-owned view-model only), merged via PR #24 at `5e34f5c`. L2 local/offline Hermes caller + offline Feishu view-model implementation is merged via PR #27 at `eb7912e` under `src/agent_run_supervisor/hermes_caller/` with `tests/hermes_caller/`. Concrete Sachima/Gateway/IM/Feishu-delivery/live behavior remains separate and unapproved. | No | Separate later approval for any live platform seam | PR #27 evidence for local/offline caller package; separate product approval for real Feishu/Sachima/Gateway/live surfaces | Generic boundary done; concrete-caller design closed (L1, design-only); L2 local/offline implementation closed on main; all live/platform behavior parked |
| ARS-NPX-STRICT-OFFLINE | PARKED | Default role compilation may resolve `npx -y acpx@0.12.0` when no explicit `acpx_binary` is configured; this is documented as fetch risk, not strict offline mode. | No | A future strict-offline hardening phase | Add role/runtime checks that require a local `acpx_binary` when strict offline is enabled; prove no `npx` fetch path with tests | P3 backlog only; not implemented in this docs/packaging cleanup |
| ARS-REDACTION-DLP-HARDENING | PARKED | Current redaction covers common token/key/JWT/PEM/env/argv shapes; stronger DLP/caller allowlist projection is needed before broader real user/platform data scenarios. | No | Any real-user/platform-data rollout | Add allowlist projection and expanded redaction probes for caller-visible surfaces | P3 backlog only |
| ARS-LOCK-RELEASE-AUDIT | PARKED | `_release_quietly()` intentionally avoids masking original session errors, but lock-release failures are not yet surfaced as structured audit evidence. | No | A future lifecycle observability hardening phase | Add regression tests and redacted management/audit evidence for release failures without breaking fail-closed behavior | P3 backlog only |

### K1 post-merge tail-closure evidence

| ID | Branch evidence | Result |
|---|---|---|
| ARS-CRASH-RECOVERY | PR #22 (`0ad531e`): `src/agent_run_supervisor/process_liveness.py`, K1 changes in `session.py`/`session_runtime.py`, `tests/test_process_liveness.py`, K1 additions in `tests/test_session_store.py` and `tests/test_session_runtime.py`, and plan `docs/plans/2026-06-01-k1-crash-recovery-hardening.md` | Closed on `main`; evidence passed local gates, PR CI, Codex review, and post-merge verification. |

### Recently closed tails

| ID | Closed by | Evidence | Result |
|---|---|---|---|
| ARS-CRASH-RECOVERY | PR #22 (`0ad531e`) | Process-liveness crash/interruption recovery beyond deterministic expired-lease replacement: supervisor-owned lease metadata, additive child-subprocess metadata, conservative `alive`/`crashed`/`unknown` holder-set classification, additive read-only detector fields, and opt-in reclamation only for provably-crashed, reclaimable holder sets; supervisor+child locks require both identities to be provably crashed; pending `reclaimable: false` holders remain TTL-only. | Closed; no terminating signal to recorded holders, no prior-holder kill, no live/unknown-holder takeover |
| ARS-DOCTOR-COMPLETE | PR #19 (`484ae23`) | Full read-only doctor probe set in `src/agent_run_supervisor/preflight.py` (`probe_policy/workspace/redaction/npx/adapter/session_readiness`) wired into `cmd_doctor`; `tests/test_preflight.py`, `tests/test_cli_commands.py`; doctor output documented in `docs/design/result-event-schema.md` §5 | Closed; `launched_real_agent: false` preserved |
| ARS-RETENTION-CLEANUP | PR #19 (`484ae23`) | `src/agent_run_supervisor/retention.py` (`plan_cleanup`/`apply_cleanup`, confined dry-run-first), `cleanup` CLI in `cli.py`/`commands.py`, read-only `SessionStore.detect_stale_locks`; `tests/test_retention.py`, `tests/test_cli_commands.py`, `tests/test_session_store.py`; plan/result shape in `docs/design/result-event-schema.md` §7 | Closed; `F-RETENTION-001` Done; full crash recovery was later closed by K1 via PR #22 (`0ad531e`) |
| ARS-EXEC-RUNNER | PR #8 (`21b3393`) | `tests/test_runner_exec.py`, local gates, local smoke `result.json` (`final_message=AGENT_RUN_SUPERVISOR_E1_OK`), `docs/roadmap/features.md` F-EXEC-001 Done | Closed |
| ARS-SESSIONS | S1a-S1d + closure acceptance branch | S1a fixtures, S1b store/lock foundation, S1c create/send/status runtime, S1d close/abort/list lifecycle, `tests/test_session_runtime.py::test_two_sequential_sends_reuse_record_persist_distinct_turns_and_release_lease`, `tests/test_smoke_persistent_session.py`, and `scripts/smoke_persistent_session.py` real local acpx lifecycle smoke | Closed for the local persistent-session lifecycle; H1 carried retention/cleanup and detection-first crash hygiene, and K1 later closed full process-liveness crash recovery |
| ARS-DOC-AUTHORITY | PR #6 (`7dcbe4f`) | docs index/drift, CI `Verify`, post-merge gates | Closed |
| ARS-LEGACY-DOCS | PR #6 (`7dcbe4f`) | retired mixed/stale docs deleted, old plan/dev-log artifacts cleared, stale-reference scan | Closed |

## 5. Current explicit non-approvals

Current docs/code work does not approve:

- Sachima behavior integration;
- real AGENT automatic replies;
- public ingress;
- real IM delivery;
- Gateway restart/reload/replace;
- production config writes;
- live/default-on behavior;
- worker auto-routing;
- participant persistence or management UI;
- `@all` fanout;
- agent-to-agent automatic routing;
- trusted Markdown/HTML rendering;
- treating `allowed_roots` as an OS/filesystem sandbox;
- per-run human approval as the default authorization model.

Persistent sessions are **not** a non-goal; they are a product requirement now closed for the local lifecycle, originally sequenced after the exec runner implementation phase.

## 6. Verification gates for implementation PRs

Primary local entry (after `uv sync --extra dev --extra release`):

```bash
./scripts/verify_local.sh
```

Step-by-step equivalent:

```bash
uv run python scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0
uv run pytest -q
uv run python -m compileall -q src scripts tests
uv run agent-run-supervisor doctor
uv run agent-run-supervisor replay fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson
uv run python tools/build_docs_index.py --check
uv run python tools/docs_drift_signal.py --check
uv run python tools/static_safety_scan.py
uv run python -m build
uv run python -m twine check dist/*
./scripts/smoke_installed_wheel.sh
git diff --check
```

**pip fallback** (without uv): replace `uv run …` with `PYTHONPATH=src python3 -m …` or installed
console scripts after `pip install -e '.[dev,release]'`.

For source changes, also run secret-shaped and static dangerous-pattern scans over added lines.

## 7. Review requirements

- Claude Code: main worker for implementation/debugging/design work unless explicitly deviating.
- Codex CLI: primary reviewer.
- Hermes: scope control, verification, gates, and evidence arbitration.
- Reviewers must check PRD/design/feature/roadmap alignment, not only whether tests pass.

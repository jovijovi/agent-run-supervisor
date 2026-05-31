---
title: "agent-run-supervisor Roadmap Current Status"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-30T00:00:00+0800
---
# agent-run-supervisor Roadmap Current Status

> Living roadmap, phase tracker, and implementation-stage acceptance register. This file replaces the deleted standalone implementation plan.

```text
last_updated: 2026-05-31
base_branch: main
product_role: independent local Python library + dev CLI for supervising ACP/acpx AGENT runs and sessions with redacted audit evidence
source_of_truth: GOAL.md, docs/product/prd.md, docs/design/architecture.md, docs/design/technical-solution.md, docs/roadmap/features.md, docs/roadmap/current-status.md, docs/AI_FLOW.md
current_mainline: E1 local one-shot exec runner is merged and closed on main via PR #8 (21b3393); F-EXEC-001 is Done; S1a session contract evidence is merged via PR #14 (99637c6); S1b adds the local session store/lock foundation; S1c adds the local create/send/status runtime MVP while close/abort/list, real-acpx smoke, crash recovery, and retention remain open
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
Current implementation: E1 local one-shot exec runner is merged and closed on main via PR #8 (`21b3393`); F-EXEC-001 is Done. S1a persistent-session contract evidence is merged via PR #14 (`99637c6`). S1b adds the local session store/lock foundation. S1c adds the local create/send/status runtime MVP; full S1 remains Partial because close/abort/list, real-acpx smoke, crash recovery, and retention remain open.
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

- [x] Capture real `acpx@0.10.0` fixtures.
- [x] Capture command grammar and observed stdout schema for current fixture family.
- [x] Validate fixture naming, schema markers, exit semantics, and secret-shaped content.
- [x] Record that `allowed_roots` is cwd/config evidence only, not a sandbox.

Acceptance:

- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0` passes.

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
- [x] Add deterministic stale-lock recovery for expired leases. *(S1b foundation; crash/runtime recovery remains open.)*
- [x] Refuse cross-role, cross-workspace, stale-policy, acpx-version, or adapter-mismatched session reuse before mutation. *(S1b foundation.)*
- [x] Implement real session create/open runtime against fixture-proven acpx session commands. *(S1c MVP: `SessionRuntime.create_session`; fake-executor/fixture acceptance, real-acpx smoke deferred.)*
- [x] Implement session send/continue runtime. *(S1c: lease-locked single `prompt -s` turn with redacted artifacts; multi-turn/continue resume remains.)*
- [~] Implement session status/list where needed. *(S1c: `status -s` query with safe management JSON summary; `list` remains a later slice.)*
- [ ] Implement session close/abort semantics.
- [x] Add session parser/event coverage and redaction tests for prompt-turn and management-command schemas. *(S1c: prompt-turn NDJSON parse + `summarize_management_json`; redaction tests in `tests/test_session_runtime.py`.)*
- [x] Add CLI/library session surface. *(S1c MVP: `session create|send|status` + `SessionRuntime` library; close/abort/list pending.)*
- [x] Update feature tracker and roadmap evidence as each remaining S1 slice lands. *(S1c evidence recorded in `docs/roadmap/features.md` and below.)*

S1a contract-spike evidence (command/schema only, not implementation):

- Plan: `docs/plans/2026-05-30-s1a-session-contract-spike.md`.
- Prompt-turn fixtures (raw `stdout.ndjson`): `fixtures/acpx-0.10.0/session-prompt-turn1/`, `fixtures/acpx-0.10.0/session-prompt-turn2/` (turn2 reuses the same ACP session id and skips `initialize`/`session/new`).
- Management-command fixtures (single-object `stdout.json`): `fixtures/acpx-0.10.0/session-new-named/`, `session-ensure-existing/`, `session-show-open/`, `session-show-after-turns/`, `session-show-closed/`, `session-history-after-turns/`, `session-read-tail-after-turns/`, `session-status-after-turns/`, `session-cancel-no-active/`, `session-close-named/`.
- Cross-checked summary `fixtures/acpx-0.10.0/session-contract-summary.json`, manifest section `session_contract` in `fixtures/acpx-0.10.0/manifest.json`, fixtures README `fixtures/acpx-0.10.0/README.md`, validator `scripts/validate_contract_fixtures.py`, and tests `tests/test_validate_contract_fixtures.py`.

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

Acceptance:

- Fixture validator or session-specific fixture validator passes.
- Session lifecycle tests cover create, send, resume, close, stale lock, mismatch refusal, and crash recovery before S1 is complete.
- Session artifacts are redacted and local-only.
- No public ingress, real delivery, Gateway lifecycle, or agent-to-agent auto-routing is introduced.

Status: **Partial after S1a + S1b + S1c. S1a captured persistent-session command grammar and stdout-schema evidence; S1b implemented the local role/session-config, store, binding, and lease-lock foundation; S1c implements the local create/send/status runtime MVP with fake-executor/fixture acceptance, safe management summaries, redacted turn artifacts, and CLI/library `session create|send|status`. Remaining open tails: real-acpx smoke, close/abort semantics, session `list`, multi-turn resume, full crash/interruption recovery, retention/cleanup, and any caller integration.**

### H1 — Operational hardening

Goal: close long-lived-use tails.

Checklist:

- [ ] Doctor probes adapter availability without launching AGENT work.
- [ ] Doctor detects runtime `npx` fetch risk.
- [ ] Doctor checks policy parseability safely.
- [ ] Doctor reports role cwd/allowed-roots validation.
- [ ] Doctor reports redaction probe.
- [ ] Doctor reports session readiness after S1.
- [ ] Retention/cleanup knobs exist for run/session artifacts.
- [ ] Result/event schema is documented for caller stability.

Acceptance:

- Doctor tests cover success/failure for each probe.
- Retention tests prove safe deletion/listing boundaries.
- Feature tracker marks hardening tails complete.

Status: **Planned**.

### I1 — Thin caller integration

Goal: only after explicit approval, integrate with a caller such as Hermes/Sachima while keeping the supervisor independent.

Checklist before start:

- [ ] E1 exec support merged and verified.
- [ ] S1 session support merged and verified if the caller needs persistent sessions.
- [ ] Caller integration target and responsibility split are documented.
- [ ] No public ingress / real delivery / Gateway lifecycle operation is implied.
- [ ] Caller owns business verdict and rendering.

Acceptance:

- Separate integration PRD/plan names exact caller behavior and non-goals.
- Supervisor remains local library/CLI.

Status: **Parked pending separate approval**.

## 4. Tail register

| ID | Class | Description | Blocks code work? | Required before | Acceptance method | Status |
|---|---|---|---:|---|---|---|
| ARS-SESSIONS | NEXT_PHASE | Persistent session support is product-required and partially implemented: S1a captured command/schema contract evidence; S1b added local session config/store/binding/lease-lock foundation; S1c adds local create/send/status runtime, management summaries, redacted turn artifacts, and CLI surface. Real-acpx smoke, close/abort/list, multi-turn resume, crash recovery, and cleanup remain open. | Yes for product-complete | S1 | Session fixtures + lifecycle tests | Open |
| ARS-DOCTOR-COMPLETE | NEXT_PHASE | Doctor is missing adapter/npx/policy/cwd/redaction/session probes. | No | H1 | Structured doctor tests | Open |
| ARS-RETENTION-CLEANUP | NEXT_PHASE | Run/session artifact retention cleanup knobs are missing. | No | H1 / long-lived use | Cleanup tests and docs | Open |
| ARS-SANDBOX-BOUNDARY | PARKED | Any claim that `allowed_roots` is an OS/filesystem sandbox remains parked. | No | Separate sandbox phase | OS sandbox proof + negative probes | Parked |
| ARS-CALLER-INTEGRATION | PARKED | Sachima/Hermes behavior integration remains separate. | No | I1 approval | Explicit integration PRD/plan | Parked |

### Recently closed tails

| ID | Closed by | Evidence | Result |
|---|---|---|---|
| ARS-EXEC-RUNNER | PR #8 (`21b3393`) | `tests/test_runner_exec.py`, local gates, local smoke `result.json` (`final_message=AGENT_RUN_SUPERVISOR_E1_OK`), `docs/roadmap/features.md` F-EXEC-001 Done | Closed |
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

Persistent sessions are **not** a non-goal; they are a product requirement scheduled after the exec runner implementation phase.

## 6. Verification gates for implementation PRs

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

For source changes, also run secret-shaped and static dangerous-pattern scans over added lines.

## 7. Review requirements

- Claude Code: main worker for implementation/debugging/design work unless explicitly deviating.
- Codex CLI: primary reviewer.
- Hermes: scope control, verification, gates, and evidence arbitration.
- Reviewers must check PRD/design/feature/roadmap alignment, not only whether tests pass.

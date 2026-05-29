---
title: "agent-run-supervisor Roadmap Current Status"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-29T13:44:07+0800
---
# agent-run-supervisor Roadmap Current Status

> Living roadmap, phase tracker, and implementation-stage acceptance register. This file replaces the deleted standalone implementation plan.

```text
last_updated: 2026-05-29
base_branch: main
product_role: independent local Python library + dev CLI for supervising ACP/acpx AGENT runs and sessions with redacted audit evidence
source_of_truth: GOAL.md, docs/product/prd.md, docs/design/technical-solution.md, docs/roadmap/features.md, docs/roadmap/current-status.md, docs/AI_FLOW.md
current_mainline: E1 local one-shot exec runner implementation candidate is complete on branch ai/e1-one-shot-exec-runner-2026-05-29; close ARS-EXEC-RUNNER on main only after PR merge, CI, and post-merge verification; next product mode remains S1 persistent-session support
```

## 1. How to read this roadmap

The document hierarchy is:

```text
PRD -> technical/design docs -> roadmap/current-status + feature tracker -> approved phase implementation plan
```

- `GOAL.md` defines stable product positioning and points to source-of-truth docs.
- `docs/product/prd.md` defines product requirements.
- `docs/design/technical-solution.md` defines the technical solution.
- `docs/roadmap/features.md` tracks feature/capability completion.
- This file tracks engineering phases, status, tails, and acceptance criteria.
- Per-phase implementation plans are created only for newly approved implementation work; all old `docs/plans/` and `docs/dev_log/` artifacts were retired.

## 2. Current decision

```text
Documentation authority realignment is complete on main via PR #6 (`7dcbe4f`).
The product requirement includes both one-shot exec and persistent sessions.
Engineering sequence may implement exec first, then persistent sessions.
Exec-first sequencing belongs in roadmap/phase planning only, not in PRD, GOAL, or product-level design as a reduced product scope.
Current approved implementation: E1 local one-shot exec runner. Candidate evidence exists on branch `ai/e1-one-shot-exec-runner-2026-05-29`; E1 closes on main only after PR merge and post-merge gates.
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
- [x] Stable real-run refusal while launch path is incomplete.

Acceptance:

- pytest passes.
- compileall passes.
- CLI doctor/replay smoke passes.
- Current real-run path refuses without launching a process.

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

Status: **Implementation candidate complete on branch `ai/e1-one-shot-exec-runner-2026-05-29`; close on main only after PR merge, main CI, and clean post-merge verification. Evidence includes `tests/test_runner_exec.py`, full local gates, and local smoke `/tmp/agent-run-supervisor-e1-smoke/result.json` with `final_message=AGENT_RUN_SUPERVISOR_E1_OK`.**

### S1 — Persistent session support

Goal: implement controlled persistent ACP/acpx session lifecycle as a first-class product requirement.

Checklist:

- [ ] Capture fresh acpx session command fixtures and observed event shapes.
- [ ] Extend `AgentRoleSpec` session config for persistent sessions.
- [ ] Add session store layout with role/workspace/acpx/policy hashes.
- [ ] Implement session create/open.
- [ ] Implement session send/continue.
- [ ] Implement session status/list where needed.
- [ ] Implement session close/abort semantics.
- [ ] Add locks/leases to prevent unsafe concurrent use.
- [ ] Add stale-lock recovery and crash/interruption handling.
- [ ] Refuse cross-role, cross-workspace, stale-policy, or mismatched-session reuse.
- [ ] Add session parser/event coverage and redaction tests.
- [ ] Add CLI/library session surface.
- [ ] Update feature tracker and roadmap evidence.

Acceptance:

- Fixture validator or session-specific fixture validator passes.
- Session lifecycle tests cover create, send, resume, close, stale lock, mismatch refusal, and crash recovery.
- Session artifacts are redacted and local-only.
- No public ingress, real delivery, Gateway lifecycle, or agent-to-agent auto-routing is introduced.

Status: **Planned after E1**.

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
| ARS-EXEC-RUNNER | NEXT_PHASE | Real one-shot acpx exec runner is implemented on the E1 branch but not closed on main until PR merge/post-merge gates. | Yes | Product execution mode E1 | Fake subprocess tests + local smoke + PR/CI/post-merge verification | In review |
| ARS-SESSIONS | NEXT_PHASE | Persistent session support is product-required but unimplemented. | Yes for product-complete | S1 | Session fixtures + lifecycle tests | Open |
| ARS-DOCTOR-COMPLETE | NEXT_PHASE | Doctor is missing adapter/npx/policy/cwd/redaction/session probes. | No | H1 | Structured doctor tests | Open |
| ARS-RETENTION-CLEANUP | NEXT_PHASE | Run/session artifact retention cleanup knobs are missing. | No | H1 / long-lived use | Cleanup tests and docs | Open |
| ARS-SANDBOX-BOUNDARY | PARKED | Any claim that `allowed_roots` is an OS/filesystem sandbox remains parked. | No | Separate sandbox phase | OS sandbox proof + negative probes | Parked |
| ARS-CALLER-INTEGRATION | PARKED | Sachima/Hermes behavior integration remains separate. | No | I1 approval | Explicit integration PRD/plan | Parked |

### Recently closed tails

| ID | Closed by | Evidence | Result |
|---|---|---|---|
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

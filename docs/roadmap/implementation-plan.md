---
title: "agent-run-supervisor Implementation Plan"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T11:09:05+0800
---
# agent-run-supervisor Implementation Plan

## 1. Planning rule

This plan is goal-first. The final product target is fixed by `docs/product/prd.md` and `docs/design/technical-solution.md`; phases are slices toward that target, not opportunities to redefine the target.

## 2. Final target

A caller can select a long-lived `AgentRoleSpec`, provide a prompt and cwd, and ask `agent-run-supervisor` to supervise exactly one local `acpx@0.10.0 exec` run. The supervisor validates role/workspace, compiles policy/argv, runs acpx, parses observed stdout, classifies result, writes redacted artifacts, and returns a stable result object. The caller remains responsible for business verdict and user-facing behavior.

## 3. Overall checklist

- [x] G0 governance / AI_FLOW exists.
- [x] Phase -1 acpx contract fixtures exist.
- [x] V0.1a role/policy/parser/store foundations exist.
- [x] V0.1b preflight/cwd/refusal hardening exists.
- [x] V0.1c manual approval drift is identified as deprecated direction.
- [x] Final PRD / technical solution / implementation plan / conformance matrix are written.
- [ ] V0.1a real exec-only runner is implemented.
- [ ] V0.1a runner gaps are closed against `docs/roadmap/v0.1a-design-conformance.md`.
- [ ] Retention/cleanup knobs exist before long-lived usage.
- [ ] Optional future phases are re-evaluated only after V0.1a completion.

## 4. Phase plan

### Phase G0 — Governance and source-of-truth repair

Goal: ensure future work starts from a complete target and cannot follow the manual-approval drift branch.

Checklist:

- [x] Root `GOAL.md` exists.
- [x] `docs/AI_FLOW.md` exists.
- [x] `docs/roadmap/current-status.md` exists.
- [x] Generated docs index/drift gates exist.
- [x] Final PRD is written: `docs/product/prd.md`.
- [x] Final technical solution is written: `docs/design/technical-solution.md`.
- [x] V0.1a conformance matrix is written: `docs/roadmap/v0.1a-design-conformance.md`.
- [x] V0.1c manual approval design is marked deprecated/historical.
- [x] Current-status next phase points to V0.1a completion, not manual approval artifacts.

Acceptance:

- docs index/drift pass;
- roadmap names `docs/design/v0.1a-design.md` as authoritative;
- no active current-status row recommends per-run manual approval as next implementation.

Status: this documentation PR.

### Phase -1 — acpx contract spike

Goal: prove the real acpx contract before implementation.

Checklist:

- [x] Capture real `acpx@0.10.0` fixtures.
- [x] Capture command grammar and observed stdout schema.
- [x] Capture success/failure/management fixtures where reachable.
- [x] Validate fixture naming, schema markers, exit semantics, and secret-shaped content.
- [x] Record `allowed_roots` boundary honestly.

Acceptance:

- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0` passes.

Status: complete.

### Phase V0.1a-Foundation — role/policy/parser/store vertical slice

Goal: build the supervisor foundation without relying on a real subprocess launch.

Checklist:

- [x] `AgentRoleSpec` model and validation.
- [x] role hash.
- [x] permission policy compiler.
- [x] acpx argv compiler.
- [x] exit classifier.
- [x] observed stdout parser.
- [x] EventStore permissions/atomic writes.
- [x] redaction helpers.
- [x] CLI `validate-role`, `replay`, `doctor` baseline.
- [x] CLI `run --no-real-run` artifact compilation.

Acceptance:

- pytest passes;
- compileall passes;
- CLI doctor/replay smoke passes;
- no real agent is launched.

Status: complete as a foundation slice, but not complete against the full V0.1a real exec-only runner target.

### Phase V0.1a-Completion — exec-only runner alignment

Goal: finish the V0.1a design by connecting CLI `run` to a true one-shot acpx exec subprocess runner.

Scope:

- source changes under `src/agent_run_supervisor/`;
- tests under `tests/`;
- docs/dev log/current status updates;
- no Sachima/Gateway/IM integration.

Checklist:

- [ ] Add a subprocess execution abstraction that accepts compiled argv, effective cwd, timeout, and env.
- [ ] Add fake subprocess tests for success, nonzero exits, malformed stdout, stderr redaction, timeout, and signal/interruption.
- [ ] Implement outer watchdog with grace and process-group handling where supported.
- [ ] Record kill metadata: `kill_reason`, `kill_signal`, `grace_ms`, `process_group_used`, stdout/stderr truncation/closure state.
- [ ] Persist stdout/stderr from real subprocess into EventStore.
- [ ] Ensure `run` without `--no-real-run` uses the true exec-only runner once the phase explicitly approves local one-shot launch.
- [ ] Keep `run --no-real-run` available as compile-only artifact generation if retained.
- [ ] Add CLI tests proving no shell interpolation and no hidden Gateway/Sachima behavior.
- [ ] Run one minimal local acpx smoke only after fake-runner gates pass and explicit local one-shot launch scope is confirmed.

Acceptance:

- all existing 123 tests still pass;
- new runner tests pass;
- `python3 -m compileall -q src scripts tests` passes;
- fixture validator passes;
- doctor/replay smoke passes;
- secret/static scans show no new unsafe patterns;
- current-status/conformance matrix mark V0.1a real runner requirements complete.

Status: next implementation phase.

### Phase V0.1a-Hardening — doctor, retention, and API polish

Goal: close V0.1a operational tails before long-lived use.

Checklist:

- [ ] Doctor probes adapter availability without launch.
- [ ] Doctor detects whether runtime `npx` fetch would occur.
- [ ] Doctor validates policy parseability/dry-run without launching an agent.
- [ ] Doctor reports role cwd/allowed_roots validation when a role is provided.
- [ ] Doctor reports explicit redaction probe result.
- [ ] Retention/cleanup knobs exist for run artifacts.
- [ ] Result/event schema is documented for caller stability.

Acceptance:

- doctor tests cover success/failure for each probe;
- retention tests prove safe deletion/listing boundaries;
- docs and conformance matrix updated.

Status: planned after V0.1a-Completion.

### Phase V0.1b+ — persistent/session phase

Goal: only after explicit approval, design and implement persistent ACP sessions.

Checklist before starting:

- [ ] V0.1a completion is merged and verified.
- [ ] User explicitly approves persistent/session phase.
- [ ] Session command shapes are proven with fresh fixtures.
- [ ] Plan includes session isolation, role/workspace hash, locking, stale-lock recovery, crash recovery, close failure semantics, and leakage tests.

Acceptance:

- separate design gate and implementation plan;
- no default-on/live behavior.

Status: parked.

### Phase Integration — Sachima/Hermes caller integration

Goal: only after explicit approval, make a thin caller integration.

Checklist before starting:

- [ ] V0.1a local exec-only supervisor is complete.
- [ ] Integration target and caller ownership are defined.
- [ ] No public ingress / real delivery / Gateway lifecycle operation is implied.
- [ ] Caller owns business verdict and rendering.

Acceptance:

- integration plan names exact caller behavior and non-goals;
- supervisor remains independent library/CLI.

Status: parked.

## 5. Tail register

| ID | Class | Description | Blocks current docs phase? | Blocks next implementation? | Required before | Acceptance method | Status |
|---|---|---|---:|---:|---|---|---|
| ARS-DOCS-GOAL-FIRST | BLOCKER | Final PRD/technical solution/implementation plan/conformance matrix must exist before more feature work. | Yes | Yes | Any further implementation | docs gates and review | In this PR |
| ARS-MANUAL-APPROVAL-DRIFT | BLOCKER | V0.1c manual approval branch conflicts with role-bound authorization. | Yes | Yes | V0.1a completion | mark deprecated and remove from next-mainline wording | In this PR |
| ARS-V01A-REAL-RUNNER | NEXT_PHASE | CLI `run` must connect to one-shot exec-only subprocess runner. | No | Yes | V0.1a completion | fake subprocess tests + local smoke if approved | Open |
| ARS-WATCHDOG-METADATA | NEXT_PHASE | Outer watchdog and kill metadata must match v0.1a design. | No | Yes | V0.1a completion | tests for timeout/kill paths | Open |
| ARS-DOCTOR-COMPLETE | NEXT_PHASE | Doctor is still missing adapter/npx/policy/cwd/redaction probes. | No | No | V0.1a hardening | structured doctor tests | Open |
| ARS-RETENTION-CLEANUP | NEXT_PHASE | Retention/cleanup knobs missing before long-lived use. | No | No | V0.1a hardening | tests and docs | Open |
| ARS-PERSISTENT-SESSIONS | PARKED | Persistent sessions remain outside current roadmap. | No | No | separate approval | design + tests | Parked |
| ARS-SACHIMA-INTEGRATION | PARKED | Sachima/Gateway/IM behavior integration remains outside current roadmap. | No | No | separate approval | explicit integration plan | Parked |

## 6. Verification gates for every implementation PR

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

- Claude Code should be used as main worker for implementation/design work unless explicitly deviating.
- Codex CLI should provide primary review for PRs.
- Hermes controls scope, verifies gates, and arbitrates with evidence.
- Reviewers must check conformance to `docs/design/v0.1a-design.md`, not only whether tests pass.

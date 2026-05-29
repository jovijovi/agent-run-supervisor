---
title: "agent-run-supervisor Roadmap Current Status"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-29T11:09:05+0800
---
# agent-run-supervisor Roadmap Current Status

> Living dashboard. This file is the first stop for roadmap, next-phase, and drift-control work.

```text
last_updated: 2026-05-29
base_branch: main
repo_role: independent local Python library + dev CLI for supervising acpx/ACP exec-only AGENT runs and recording redacted audit evidence
source_of_truth: docs/design/v0.1a-design.md, docs/product/prd.md, docs/design/technical-solution.md, docs/roadmap/v0.1a-design-conformance.md, docs/roadmap/implementation-plan.md
current_mainline: Phase -1 fixtures, V0.1a foundation, V0.1b preflight hardening, and goal-first final docs are aligned around role-bound AgentRoleSpec authorization
selected_epic: V0.1a completion / exec-only runner alignment; persistent sessions, Sachima integration, real delivery, and per-run manual approval remain out of mainline scope
```

## One-screen roadmap checklist

### G0 — Project governance / AI_FLOW

- [x] Root `GOAL.md` defines project role, principles, and non-approvals.
- [x] `docs/AI_FLOW.md` defines AI-assisted branch, plan, verification, review, and PR workflow.
- [x] `AGENTS.md` gives local agent instructions and role split.
- [x] `docs/roadmap/current-status.md` is the living status dashboard.
- [x] `tools/build_docs_index.py` and `tools/docs_drift_signal.py` provide docs index/drift gates.
- [x] GitHub Actions `Verify` mirrors portable local gates.
- [x] Goal-first final docs exist: PRD, technical solution, implementation plan, and V0.1a conformance matrix.

**Evidence:** `GOAL.md`, `AGENTS.md`, `docs/AI_FLOW.md`, `docs/product/prd.md`, `docs/design/technical-solution.md`, `docs/roadmap/implementation-plan.md`, `docs/roadmap/v0.1a-design-conformance.md`, `.github/workflows/verify.yml`.

### Phase -1 — acpx@0.10.0 contract spike

- [x] Capture real `acpx@0.10.0` command grammar and stdout schema.
- [x] Check in fixtures under `fixtures/acpx-0.10.0/`.
- [x] Validate fixture naming, command argv JSON, schema markers, exit semantics, and secret-shaped content.
- [x] Record that `allowed_roots` is cwd/config evidence only, not a security sandbox.

**Evidence:** `fixtures/acpx-0.10.0/`, `scripts/capture_acpx_contract.py`, `scripts/validate_contract_fixtures.py`, `docs/plans/2026-05-28-phase-minus-1-acpx-contract-spike.md`, `docs/dev_log/2026-05-28-phase-minus-1-acpx-contract-spike.md`.

### V0.1a foundation — role/policy/parser/store vertical slice

- [x] `AgentRoleSpec` validation.
- [x] acpx argv/policy compiler.
- [x] exit classifier for `0/1/2/3/4/5/130/unknown`.
- [x] observed stdout parser/replay for Phase -1 fixtures.
- [x] EventStore with restrictive permissions.
- [x] redaction helpers.
- [x] CLI commands: `validate-role`, `replay`, `doctor`, and `run --no-real-run`.
- [x] pytest, compileall, fixture validation, CLI smoke, secret/static scan, and Codex review evidence.
- [ ] final CLI `run` exec-only subprocess launch path.
- [ ] outer watchdog/process-group lifecycle and detailed kill metadata.
- [ ] retention/cleanup knobs before long-lived use.

**Evidence:** `src/agent_run_supervisor/`, `tests/`, `docs/plans/2026-05-28-v0.1a-exec-only-vertical-slice.md`, `docs/dev_log/2026-05-28-v0.1a-exec-only-vertical-slice.md`, `docs/roadmap/v0.1a-design-conformance.md`.

### V0.1b — preflight and safe refusal hardening

- [x] `doctor` emits structured Node and acpx version probes without launching agents.
- [x] role-specific `runner.acpx_binary` is honored by the acpx version probe.
- [x] cwd-vs-allowed-roots validation fails closed before artifact creation.
- [x] `run --no-real-run` records effective cwd metadata and the allowed-roots disclaimer.
- [x] `run` without `--no-real-run` returns a stable refusal payload and creates no run artifacts.
- [x] tests cover probe success/failure, cwd in/out-of-root, and real-run refusal.

**Evidence:** `src/agent_run_supervisor/preflight.py`, `src/agent_run_supervisor/workspace.py`, `tests/test_preflight.py`, `tests/test_workspace_gate.py`, `docs/plans/2026-05-29-v0.1b-real-run-preflight-hardening.md`, `docs/dev_log/2026-05-29-v0.1b-real-run-preflight-hardening.md`.

### V0.1c — deprecated HITL/manual approval design branch

- [x] Historical design branch merged as docs-only PR #3.
- [x] Current review found it conflicts with the role-bound authorization decision.
- [x] `docs/design/v0.1c-hitl-manual-real-run-design.md` is marked deprecated/historical.
- [x] Current mainline no longer points next work to per-run manual approval artifacts.

**Evidence:** `docs/design/v0.1c-hitl-manual-real-run-design.md`, this current-status dashboard, `docs/product/prd.md`, `docs/design/technical-solution.md`.

### V0.1a completion — exec-only runner alignment

- [ ] Add a real one-shot subprocess execution path for `run` using compiled role argv/policy.
- [ ] Capture stdout/stderr from the real subprocess into EventStore.
- [ ] Add fake subprocess tests for success, failure, timeout, malformed stdout, permission denied, and stderr redaction.
- [ ] Implement outer watchdog with grace and process-group termination where supported.
- [ ] Record kill metadata: `kill_reason`, `kill_signal`, `grace_ms`, `process_group_used`, stdout/stderr truncation/closure state.
- [ ] Preserve `business_verdict: null` and caller-owned business interpretation.
- [ ] Keep persistent sessions, Sachima integration, Gateway operations, and IM delivery out of scope.

**Evidence target:** future V0.1a completion plan/dev log/PR plus updates to `docs/roadmap/v0.1a-design-conformance.md`.

## Current decision

```text
G0 governance / AI_FLOW: supported.
Phase -1: complete.
V0.1a foundation: complete as role/policy/parser/store foundation, but incomplete against final exec-only runner design.
V0.1b: complete as preflight and safe refusal hardening.
V0.1c: deprecated as product direction; retained only as historical drift context.
Next allowed request: V0.1a completion / exec-only runner alignment. This may implement the one-shot local acpx exec runner under role-bound AgentRoleSpec authorization, with fake subprocess tests first and no persistent sessions or Sachima/Gateway integration.
```

## Explicit non-approvals

The current repo state does not approve:

- persistent sessions;
- session registry, locking, stale-lock recovery, or multi-turn context retention;
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

## Tail register

| ID | Class | Description | Blocks current docs phase? | Blocks next implementation? | Required before | Acceptance method | Status |
|---|---|---|---:|---:|---|---|---|
| ARS-DOCS-GOAL-FIRST | BLOCKER | Final PRD/technical solution/implementation plan/conformance matrix must exist before more feature work. | Yes | Yes | Any further implementation | docs gates and review | In this PR |
| ARS-MANUAL-APPROVAL-DRIFT | BLOCKER | V0.1c manual approval branch conflicts with role-bound authorization. | Yes | Yes | V0.1a completion | deprecate design and remove next-mainline wording | In this PR |
| ARS-V01A-REAL-RUNNER | NEXT_PHASE | CLI `run` must connect to one-shot exec-only subprocess runner. | No | Yes | V0.1a completion | fake subprocess tests + local smoke if approved | Open |
| ARS-WATCHDOG-METADATA | NEXT_PHASE | Outer watchdog and kill metadata must match v0.1a design. | No | Yes | V0.1a completion | tests for timeout/kill paths | Open |
| ARS-DOCTOR-COMPLETE | NEXT_PHASE | Doctor is missing adapter/npx/policy/cwd/redaction probes. | No | No | V0.1a hardening | structured doctor tests | Open |
| ARS-RETENTION-CLEANUP | NEXT_PHASE | Retention/cleanup knobs are missing before long-lived use. | No | No | V0.1a hardening | tests and docs | Open |
| ARS-SANDBOX-BOUNDARY | PARKED | Any claim that `allowed_roots` provides OS/filesystem sandbox isolation remains parked. | No | No | Separate sandbox phase approval | OS-level sandbox proof and negative filesystem-access probes | Parked |
| ARS-PERSISTENT-SESSIONS | PARKED | Persistent sessions, session registry, locking, stale-lock recovery, and multi-turn context retention remain outside this roadmap line. | No | No | Separate persistent-session phase approval | session isolation/locking/recovery design and tests | Parked |
| ARS-SACHIMA-INTEGRATION | PARKED | Sachima behavior integration, auto-replies, delivery, ingress, participant UI, and `@all` remain out of scope. | No | No | Separate integration approval | explicit integration plan and product boundary review | Parked |

## Canonical references

- North star: `GOAL.md`
- PRD: `docs/product/prd.md`
- Design authority: `docs/design/v0.1a-design.md`
- Technical solution: `docs/design/technical-solution.md`
- V0.1a conformance matrix: `docs/roadmap/v0.1a-design-conformance.md`
- Implementation plan: `docs/roadmap/implementation-plan.md`
- AI flow: `docs/AI_FLOW.md`
- Roadmap rules: `docs/roadmap/README.md`
- Phase -1 plan: `docs/plans/2026-05-28-phase-minus-1-acpx-contract-spike.md`
- Phase -1 dev log: `docs/dev_log/2026-05-28-phase-minus-1-acpx-contract-spike.md`
- V0.1a plan: `docs/plans/2026-05-28-v0.1a-exec-only-vertical-slice.md`
- V0.1a dev log: `docs/dev_log/2026-05-28-v0.1a-exec-only-vertical-slice.md`
- V0.1b plan: `docs/plans/2026-05-29-v0.1b-real-run-preflight-hardening.md`
- V0.1b dev log: `docs/dev_log/2026-05-29-v0.1b-real-run-preflight-hardening.md`
- Deprecated V0.1c design: `docs/design/v0.1c-hitl-manual-real-run-design.md`
- Goal-first final docs plan: `docs/plans/2026-05-29-goal-first-final-docs.md`
- Goal-first final docs dev log: `docs/dev_log/2026-05-29-goal-first-final-docs.md`

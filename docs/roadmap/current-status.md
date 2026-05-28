---
title: "agent-run-supervisor Roadmap Current Status"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-29T01:36:11+0800
---
# agent-run-supervisor Roadmap Current Status

> Living dashboard. This file is the first stop for roadmap, next-phase, and drift-control work.

```text
last_updated: 2026-05-29
base_branch: main
repo_role: independent local Python library + dev CLI for supervising acpx/ACP exec-only AGENT runs and recording redacted audit evidence
current_mainline: Phase -1 fixtures, V0.1a exec-only vertical slice, and V0.1b real-run preflight hardening are repository-supported
selected_epic: V0.1b preflight hardening; persistent sessions and real integrations remain deferred
```

## One-screen roadmap checklist

### G0 — Project governance / AI_FLOW

- [x] Root `GOAL.md` defines project role, principles, and non-approvals.
- [x] `docs/AI_FLOW.md` defines AI-assisted branch, plan, verification, review, and PR workflow.
- [x] `AGENTS.md` gives local agent instructions and role split.
- [x] `docs/roadmap/current-status.md` is the living status dashboard.
- [x] `tools/build_docs_index.py` and `tools/docs_drift_signal.py` provide docs index/drift gates.
- [x] GitHub Actions `Verify` mirrors portable local gates.

**Evidence:** `GOAL.md`, `AGENTS.md`, `docs/AI_FLOW.md`, `docs/roadmap/current-status.md`, `.github/workflows/verify.yml`.

### Phase -1 — acpx@0.10.0 contract spike

- [x] Capture real `acpx@0.10.0` command grammar and stdout schema.
- [x] Check in fixtures under `fixtures/acpx-0.10.0/`.
- [x] Validate fixture naming, command argv JSON, schema markers, exit semantics, and secret-shaped content.
- [x] Record that `allowed_roots` is cwd/config evidence only, not a security sandbox.

**Evidence:** `fixtures/acpx-0.10.0/`, `scripts/capture_acpx_contract.py`, `scripts/validate_contract_fixtures.py`, `docs/plans/2026-05-28-phase-minus-1-acpx-contract-spike.md`, `docs/dev_log/2026-05-28-phase-minus-1-acpx-contract-spike.md`.

### V0.1a — exec-only vertical slice

- [x] `AgentRoleSpec` validation.
- [x] acpx argv/policy compiler.
- [x] exit classifier for `0/1/2/3/4/5/130/unknown`.
- [x] observed stdout parser/replay for Phase -1 fixtures.
- [x] EventStore with restrictive permissions.
- [x] redaction helpers.
- [x] CLI commands: `validate-role`, `replay`, `doctor`, and `run --no-real-run`.
- [x] pytest, compileall, fixture validation, CLI smoke, secret/static scan, and Codex review evidence.

**Evidence:** `src/agent_run_supervisor/`, `tests/`, `docs/plans/2026-05-28-v0.1a-exec-only-vertical-slice.md`, `docs/dev_log/2026-05-28-v0.1a-exec-only-vertical-slice.md`.

### V0.1b — real-run preflight hardening

- [x] `doctor` emits structured Node and acpx version probes without launching agents.
- [x] role-specific `runner.acpx_binary` is honored by the acpx version probe.
- [x] cwd-vs-allowed-roots validation fails closed before artifact creation.
- [x] `run --no-real-run` records effective cwd metadata and the allowed-roots disclaimer.
- [x] `run` without `--no-real-run` returns a stable refusal payload and creates no run artifacts.
- [x] tests cover probe success/failure, cwd in/out-of-root, and real-run refusal.

**Evidence:** `src/agent_run_supervisor/preflight.py`, `src/agent_run_supervisor/workspace.py`, `tests/test_preflight.py`, `tests/test_workspace_gate.py`, `docs/plans/2026-05-29-v0.1b-real-run-preflight-hardening.md`, `docs/dev_log/2026-05-29-v0.1b-real-run-preflight-hardening.md`.

## Current decision

```text
G0 governance / AI_FLOW: supported from this branch onward.
Phase -1: complete.
V0.1a: complete as exec-only vertical slice.
V0.1b: complete as real-run preflight hardening; actual real AGENT launch remains unapproved.
Next allowed request: plan a HITL/manual real-run design gate only if the user explicitly approves the next boundary; it must stay single-agent, manual, budget/timeout/max-turn bounded, and no auto-routing by default.
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
- treating `allowed_roots` as an OS/filesystem sandbox.

## Tail register

| ID | Class | Description | Blocks current phase? | Blocks next phase? | Required before | Acceptance method | Status |
|---|---|---|---:|---:|---|---|---|
| ARS-V01B-NODE-ACPX-DOCTOR | NEXT_PHASE | `doctor` emits structured Node/acpx probes, honors role `runner.acpx_binary`, and does not launch agents. | No | No | V0.1b/live-run preflight | tests plus doctor output proving detected versions without launching real agents | Closed in V0.1b |
| ARS-ALLOWED-ROOTS-BOUNDARY | NEXT_PHASE | `allowed_roots` is implemented as cwd/config validation only; cwd outside configured roots fails before artifacts. | No | No | V0.1b preflight | negative tests and docs that distinguish config validation from sandbox enforcement | Closed for cwd/config gate in V0.1b |
| ARS-SANDBOX-BOUNDARY | PARKED | Any claim that `allowed_roots` provides OS/filesystem sandbox isolation remains parked. | No | No | Separate sandbox phase approval | OS-level sandbox proof and negative filesystem-access probes | Parked |
| ARS-REAL-RUN-GATE | PARKED | Real agent launch beyond `run --no-real-run` remains parked; V0.1b only adds stable refusal. | No | No | Separate user approval | phase plan with HITL/manual gate, timeout/max-turns/budget caps, no auto-routing | Parked |

## Canonical references

- North star: `GOAL.md`
- AI flow: `docs/AI_FLOW.md`
- Roadmap rules: `docs/roadmap/README.md`
- Design: `docs/design/v0.1a-design.md`
- Phase -1 plan: `docs/plans/2026-05-28-phase-minus-1-acpx-contract-spike.md`
- Phase -1 dev log: `docs/dev_log/2026-05-28-phase-minus-1-acpx-contract-spike.md`
- V0.1a plan: `docs/plans/2026-05-28-v0.1a-exec-only-vertical-slice.md`
- V0.1a dev log: `docs/dev_log/2026-05-28-v0.1a-exec-only-vertical-slice.md`
- V0.1b plan: `docs/plans/2026-05-29-v0.1b-real-run-preflight-hardening.md`
- V0.1b dev log: `docs/dev_log/2026-05-29-v0.1b-real-run-preflight-hardening.md`

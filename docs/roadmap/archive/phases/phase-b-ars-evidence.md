---
title: "Phase B — ARS evidence hardening"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: phase-b-ars-evidence
---

# Phase B — ARS evidence hardening

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## Phase B — ARS evidence hardening (support for the external Sachima controlled-local-execution PRD)

Evidence/test-only; **not** a new ARS product phase. This is *Phase B — `agent-run-supervisor`
role support hardening* from the external *Sachima × agent-run-supervisor Controlled Local Agent
Execution* PRD gate and the Claude architect design packet (rev. 2). It adds a **local
static/compiler-evidence gate** — parametrized `compile_command` golden tests in
`tests/test_policy.py` pinning the pinned-local `runner.acpx_binary` prefix (no `npx`) and
`<adapter> exec <prompt>` for `adapter_agent in {codex, claude}`, with default-deny permission
policy and argv-list/no-shell behavior preserved — plus the plan
`docs/plans/archive/2026-06-12-phase-b-ars-evidence-hardening.md`. It makes **no schema or runtime
behavior change**. This host currently has **no local `acpx` binary**, so a truthful
strict-offline real Claude `acpx` fixture capture is **blocked** and carried forward; Phase B
captures **no** new live fixture and runs no `npx`/`acpx`. It approves **no** live behavior and
**no** Sachima integration; all §5 non-approvals remain in force.

Status: **Closed** — evidence gate merged; no live approval.

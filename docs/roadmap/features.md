---
title: "ARS vNext Feature and Capability Tracker"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-22
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/features.md"
---
# ARS vNext Feature and Capability Tracker

Only vNext direction and compatibility obligations live here. Detailed v0.1.7 feature closure is retained
in the cold archive and Git history; it is not default development context.

Status legend: **Done** · **Planned** · **Parked** · **Non-goal**

| ID | Capability | Product | Impl | Evidence / authority | Remaining |
|---|---|---|---|---|---|
| F-LEGACY-COMPAT-001 | v0.1.7 acpx compatibility baseline | Compatibility | Done | released code; archived authority snapshot; result/event schema | maintenance only; no vNext direction |
| F-VNEXT-ADMISSION-001 | AgentProfile → ResolvedLaunchSpec → immutable AgentRunSpec | Required | Done | PRD R1; active plan C1/C4; freeze-order + spec-hash suites | integration/merge pending |
| F-VNEXT-PROCESS-001 | ManagedProcess live stdio supervision | Required | Done | PRD R2; active plan C3; live-wire/group-kill/reap suite | integration/merge pending |
| F-NATIVE-ACP-001 | Native ACP exact-config core through ars-core | Required | Done | PRD R2–R3; active plan C1–C10; real B-grade acceptance incl. registered-second-model switch | integration/merge pending; production claim only via Stage 2 |
| F-VNEXT-SESSION-001 | process-per-Run, session/load continuity, cross-Run switching | Required | Done | PRD R4; active plan C6/C9/C10; real nonce continuity + exact switch/rollback | integration/merge pending |
| F-VNEXT-STATE-001 | unknown/quarantined/retryable=false, markers, no replay | Required | Done | PRD R5; active plan C2/C8; terminal-table + write-once + cancellation suites | integration/merge pending |
| F-VNEXT-PERMISSION-001 | frozen grant, default-deny mediation, real canary | Required | Planned | PRD R7; active plan C7; Stage-1 default-deny bridge done at L1/L2 | Stage 2 real denied-action canary |
| F-VNEXT-EVIDENCE-001 | isolated Native stores and bounded runtime ledger | Required | Done | PRD R8–R9; active plan C6–C8; poisoned-legacy isolation + bounded-writer suites | integration/merge pending |
| F-ARSD-001 | local UDS production ingress, ownership, reconciliation, cgroup containment | Required | Planned | PRD R6/R10; architecture §§1/6; technical §§1.3/9 | Stage 2 authorization, G12, S1–S5 |
| F-SACHIMA-ARSD-001 | Sachima socket backend | Later integration | Parked | GOAL/PRD stage boundary | only after ARS production acceptance |
| F-NONGOAL-001 | public/root/TCP/multi-tenant/business-orchestration surfaces | Non-goal | Non-goal | GOAL; PRD §6; non-approvals | separate product decision only |

## Completion roll-up

| Area | Done | Planned | Parked | Non-goal |
|---|---:|---:|---:|---:|
| Legacy compatibility baseline | 1 | 0 | 0 | 0 |
| vNext Stage 0/1 | 6 | 1 | 0 | 0 |
| vNext Stage 2 | 0 | 1 | 0 | 0 |
| Later integration | 0 | 0 | 1 | 0 |
| Explicit exclusions | 0 | 0 | 0 | 1 |

Update this tracker only when requirements, implementation state, or acceptance evidence changes. Keep
evidence cells short; details belong in active plans or cold phase archives.

---
title: "ARS vNext Feature and Capability Tracker"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-23
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/features.md"
---
# ARS vNext Feature and Capability Tracker

Only vNext direction and compatibility obligations live here. Detailed v0.1.7 feature closure is retained
in the cold archive and Git history; it is not default development context.

Status legend: **Done** · **Planned** · **Parked** · **Non-goal**

| ID | Capability | Product | Impl | Evidence / authority | Remaining |
|---|---|---|---|---|---|
| F-LEGACY-COMPAT-001 | v0.1.7 acpx compatibility baseline | Compatibility | Done | released code; archived authority snapshot; result/event schema | maintenance only; no vNext direction |
| F-VNEXT-ADMISSION-001 | AgentProfile → ResolvedLaunchSpec → immutable AgentRunSpec | Required | Done | PRD R1; archived plan C1/C4; freeze-order + spec-hash suites | merged; in the 0.2.0 source line |
| F-VNEXT-PROCESS-001 | ManagedProcess live stdio supervision | Required | Done | PRD R2; archived plan C3; live-wire/group-kill/reap suite | merged; in the 0.2.0 source line |
| F-NATIVE-ACP-001 | Native ACP exact-config core through ars-core | Required | Done | PRD R2–R3; archived plan C1–C10; real B-grade acceptance incl. registered-second-model switch | merged; production claim only via Stage 2 |
| F-VNEXT-SESSION-001 | process-per-Run, session/load continuity, cross-Run switching | Required | Done | PRD R4; archived plan C6/C9/C10; real nonce continuity + exact switch/rollback | merged; in the 0.2.0 source line |
| F-VNEXT-STATE-001 | unknown/quarantined/retryable=false, markers, no replay | Required | Done | PRD R5; archived plan C2/C8; terminal-table + write-once + cancellation suites | merged; in the 0.2.0 source line |
| F-VNEXT-PERMISSION-001 | frozen grant, default-deny mediation, real canary | Required | Planned | PRD R7; archived plan C7; Stage-1 default-deny bridge done at L1/L2 | A4 real denied-action canary evidence |
| F-VNEXT-EVIDENCE-001 | isolated Native stores and bounded runtime ledger | Required | Done | PRD R8–R9; archived plan C6–C8; poisoned-legacy isolation + bounded-writer suites | merged; in the 0.2.0 source line |
| F-ARSD-001 | local UDS production ingress, ownership, reconciliation, cgroup containment | Required | Planned | PRD R6/R10; A1 source/default-closed foundation (Slices 1–5 and 6a) merged in `main` | A2 G12 policy owner + exact real UID mappings; A3 user-service/cgroup activation/execution; A4 real external-AGENT S1–S5; A5 production/default-on enablement |
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

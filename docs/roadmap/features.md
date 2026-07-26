---
title: "ARS vNext Feature and Capability Tracker"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-26
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/features.md"
---
# ARS vNext Feature and Capability Tracker

Only vNext direction and compatibility obligations live here. Detailed v0.1.7 feature closure is retained
in the cold archive and Git history; it is not default development context.

Status legend: **Done** · **Planned** · **Parked** · **Non-goal**

| ID | Capability | Product | Impl | Evidence / authority | Remaining |
|---|---|---|---|---|---|
| F-LEGACY-COMPAT-001 | v0.1.7 acpx compatibility baseline | Compatibility | Done | released code; archived authority snapshot; result/event schema | maintenance only; no vNext direction |
| F-VNEXT-ADMISSION-001 | AgentProfile → ResolvedLaunchSpec → immutable AgentRunSpec | Required | Done | PRD R1; archived plan C1/C4; freeze-order + spec-hash suites | merged; first released in 0.2.0 |
| F-VNEXT-PROCESS-001 | ManagedProcess live stdio supervision | Required | Done | PRD R2; archived plan C3; live-wire/group-kill/reap suite | merged; first released in 0.2.0 |
| F-NATIVE-ACP-001 | Native ACP exact-config core through ars-core | Required | Done | PRD R2–R3; archived plan C1–C10; real B-grade acceptance incl. registered-second-model switch | merged; production ingress closed by Stage 2 |
| F-VNEXT-SESSION-001 | process-per-Run, session/load continuity, cross-Run switching | Required | Done | PRD R4; archived plan C6/C9/C10; real nonce continuity + exact switch/rollback | merged; first released in 0.2.0 |
| F-VNEXT-STATE-001 | unknown/quarantined/retryable=false, markers, no replay | Required | Done | PRD R5; archived plan C2/C8; terminal-table + write-once + cancellation suites | merged; first released in 0.2.0 |
| F-VNEXT-PERMISSION-001 | frozen grant, default-deny mediation, real canary | Required | Done | PRD R7; L1/L2 bridge; A4 S2 real denied-action canary PASS (operator-held C-grade evidence) | none — production default-on is tracked by F-ARSD-001 |
| F-VNEXT-EVIDENCE-001 | isolated Native stores and bounded runtime ledger | Required | Done | PRD R8–R9; archived plan C6–C8; poisoned-legacy isolation + bounded-writer suites | merged; first released in 0.2.0 |
| F-ARSD-001 | local UDS production ingress, ownership, reconciliation, cgroup containment | Required | Done | PRD R6/R10; A1–A5 closed; default-on enabled 2026-07-23 on CPython 3.12.3 (operator-held evidence) | none — operate on the CPython 3.12.3 runtime invariant; Sachima stays parked (F-SACHIMA-ARSD-001) |
| F-NATIVE-ADAPTER-CODEX-001 | Codex official ACP adapter closed profile: frozen launch env, spawn-boundary attestation, credential-ref binding | Required | Done | PRD R1/R3/R12; [plan](../plans/archive/2026-07-25-codex-official-adapter-admission.md); local acceptance | none — publication/deployment/enablement remain separately approved |
| F-NATIVE-ADAPTER-CLAUDE-001 | Claude official ACP adapter closed profile: frozen runtime identity, enforced `default` permission mode, frozen session metadata on new+load | Required | Done | PRD R1/R3/R7/R12; [plan](../plans/archive/2026-07-25-claude-official-adapter-b3-b5-closure.md); local acceptance | none — publication/deployment/enablement remain separately approved |
| F-RUNTIME-BINDING-001 | operator-owned Runtime Binding, sealed per-Run runtime provenance, session compatibility epoch | Required | Planned | PRD R13; [plan](../plans/active/2026-07-26-runtime-binding-refactor.md) | PR-B vertical source/test/docs implementation; promotion, rollout, and real-provider evidence stay separate operator decisions |
| F-SACHIMA-ARSD-001 | Sachima socket backend | Later integration | Parked | GOAL/PRD stage boundary | ARS production acceptance closed; integration still requires its own separate approval |
| F-NONGOAL-001 | public/root/TCP/multi-tenant/business-orchestration surfaces | Non-goal | Non-goal | GOAL; PRD §6; non-approvals | separate product decision only |

## Completion roll-up

| Area | Done | Planned | Parked | Non-goal |
|---|---:|---:|---:|---:|
| Legacy compatibility baseline | 1 | 0 | 0 | 0 |
| vNext Stage 0/1 | 7 | 0 | 0 | 0 |
| vNext Stage 2 | 1 | 0 | 0 | 0 |
| Registered official adapters | 2 | 0 | 0 | 0 |
| Runtime Binding refactor | 0 | 1 | 0 | 0 |
| Later integration | 0 | 0 | 1 | 0 |
| Explicit exclusions | 0 | 0 | 0 | 1 |

**Planned boundary.** `Planned` means accepted design with no source on `main`. The historical `Done`
counts above are unchanged by the Runtime Binding row: nothing previously closed is reopened, and the
new row claims no implementation, acceptance, deployment, or publication.

**Publication boundary.** `Done` means implemented in the current `main` source line, whose package
metadata is 0.5.0 and covers the Stage 0/1 Native ACP core, `arsd` (F-ARSD-001), and the three
registered closed profiles. The live GitHub Releases and PyPI listings are authoritative for which
versions are published; neither publication nor local acceptance is a deployment, enablement, or
Sachima-integration approval.

Update this tracker only when requirements, implementation state, or acceptance evidence changes. Keep
evidence cells short; details belong in active plans or cold phase archives.

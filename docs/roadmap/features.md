---
title: "ARS vNext Feature and Capability Tracker"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-30
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/features.md"
---
# ARS vNext Feature and Capability Tracker

Only vNext direction and the authority-versus-source delta live here. Detailed v0.1.7 feature closure is
retained in the cold archive and Git history; it is not default development context.

Status legend: **Done** · **In review** · **Planned** · **Superseded** · **Retired** · **Parked** ·
**Non-goal**

**Superseded** and **Retired** describe *authority*, not source. A row that reads
"source retirement pending" means the tracked architecture no longer targets that capability while the
code that implements it is still on `main`. The board carries the exact authority-versus-source delta.

| ID | Capability | Product | Impl | Evidence / authority | Remaining |
|---|---|---|---|---|---|
| F-LEGACY-COMPAT-001 | legacy v0.1.7 acpx line: no product, runtime, or compatibility authority | Not a product | Retired | GOAL acpx removal direction; board; archived authority snapshot | authority retired, source not deleted: the acpx code and its emitted fields stay on `main` until separately authorized removal, maintenance is separately approved, and only bounded differential/comparison fixtures remain as reference |
| F-VNEXT-ADMISSION-001 | structured admission → immutable AgentRunSpec sealed before spawn | Required | Done | PRD R1; archived plan C1/C4; freeze-order + spec-hash suites | merged; the resolution inputs change with F-AGENT-REGISTRY-001 |
| F-VNEXT-PROCESS-001 | ManagedProcess live stdio supervision | Required | Done | PRD R2; archived plan C3; live-wire/group-kill/reap suite | merged; first released in 0.2.0 |
| F-NATIVE-ACP-001 | Native ACP exact-config core through ars-core | Required | Done | PRD R2–R3; archived plan C1–C10; real B-grade acceptance | merged; live option domains land with F-BOUNDARY-RESET-001 |
| F-VNEXT-SESSION-001 | process-per-Run, session/load continuity, cross-Run switching | Required | Done | PRD R4; archived plan C6/C9/C10; real continuity + exact switch/rollback | merged; the fail-closed reuse gate is pending in F-BOUNDARY-RESET-001 |
| F-VNEXT-STATE-001 | unknown/quarantined/retryable=false, markers, no replay | Required | Done | PRD R5; archived plan C2/C8; terminal-table + write-once + cancellation suites | merged; first released in 0.2.0 |
| F-VNEXT-PERMISSION-001 | frozen grant, default-deny mediation, real canary | Required | Done | PRD R7; L1/L2 bridge; real denied-action canary PASS (operator-held) | none — the canary stays mandatory per registered agent |
| F-VNEXT-EVIDENCE-001 | isolated Native stores and bounded runtime ledger | Required | Done | PRD R8–R9; archived plan C6–C8; poisoned-legacy isolation + bounded-writer suites | merged; value-blind evidence lands with F-ENV-EVIDENCE-001 |
| F-ARSD-001 | local UDS production ingress, ownership, reconciliation, cgroup containment | Required | Done | PRD R6/R10; A1–A5 closed; default-on enabled 2026-07-23 (operator-held) | none — operate on the recorded CPython runtime invariant |
| F-NATIVE-ADAPTER-CODEX-001 | Codex official ACP adapter closed profile | Required | Superseded | PRD R12; [plan](../plans/archive/2026-07-25-codex-official-adapter-admission.md); local acceptance | superseded by an operator-registered adapter command; source retirement pending, and its execution needs its own confirmation |
| F-NATIVE-ADAPTER-CLAUDE-001 | Claude official ACP adapter closed profile | Required | Superseded | PRD R12; [plan](../plans/archive/2026-07-25-claude-official-adapter-b3-b5-closure.md); ACP discovery | its one cited ACP deviation survives as an evidenced compat profile; the artifact half is retired; source retirement pending |
| F-STANDARD-NATIVE-ACP-001 | versioned conformance profile + typed operator-owned agent identity | Required | Superseded | PRD R12; [plan](../plans/archive/2026-07-29-standard-native-acp-v1.md) | merged on the v0.5.3 line; superseded by the operator registry, which delivers the same idea without an artifact anchor |
| F-RUNTIME-BINDING-001 | operator-owned Runtime Binding + sealed runtime provenance | Required | Retired | [history](../archive/binding-era-2026-07/README.md); [plan](../plans/archive/2026-07-26-runtime-binding-refactor.md) | retired as target architecture; source deletion is Stage 3 and needs its own confirmation |
| F-RUNTIME-BINDING-002 | complete wrapped-adapter package closure in frozen artifact identity | Required | Retired | [history](../archive/binding-era-2026-07/README.md); [rationale](../archive/binding-era-2026-07/retirement-rationale.md) | retired: ARS makes no artifact-integrity claim; source deletion pending Stage 3 |
| F-RUNTIME-BINDING-003 | profile-scoped active selection, one generation per profile | Required | Retired | [plan](../plans/archive/2026-07-29-multi-profile-runtime-binding.md); binding-era archive | released on v0.5.2; retired with the Binding layer, and its live roots are untouched |
| F-AGENT-REGISTRY-001 | one operator-owned TOML agent registry, read once at daemon startup | Required | Planned | PRD R13; GOAL contract 9; [registry contract](../design/agent-registry.md) | Stage 3 source work; needs source-implementation approval, then a restart to take effect at all |
| F-BOUNDARY-RESET-001 | external AGENT boundary reset: two profiles, preserved command semantics, fail-closed load-only reuse | Required | Planned | PRD R4/R12/R13; GOAL contracts 1, 3, 9; architecture §3–§4 | Stages 1 and 3; profile deletion in source needs a separate confirmation on top of source approval |
| F-OBSERVED-EVIDENCE-001 | observed runtime facts are evidence or a policy warning, never a gate | Required | Planned | PRD R14; architecture §3.3; result/event schema §9.5 | Stage 3 source work; retires the observed-identity gates without losing the records |
| F-ENV-EVIDENCE-001 | environment-value non-persistence across every ARS-owned sink | Required | Planned | PRD R15; technical solution §7; result/event schema §9.2 | Stage 2 source work; erases substantial Run evidence by design, and that tradeoff is documented |
| F-RECONCILE-ORDERED-001 | total ordered fail-closed startup reconciliation, absent ≠ corrupt | Required | Planned | PRD R10; architecture §6.1–§6.2; technical solution §9 | Stage 1 source work; strictly more refusals than the tolerant reader it replaces |
| F-ARSD-API-002 | api_version 2 with the eight-operation drain matrix | Required | Planned | PRD R11; architecture §1 two-protocol note | Stage 3 source work; no caller cutover is approved, and the drain window exists to drain |
| F-SACHIMA-ARSD-001 | Sachima socket backend | Later integration | Parked | GOAL/PRD stage boundary | ARS production acceptance closed; integration still requires its own separate approval |
| F-NONGOAL-001 | public/root/TCP/multi-tenant/business-orchestration surfaces | Non-goal | Non-goal | GOAL; PRD §6; non-approvals | separate product decision only |

## Completion roll-up

| Area | Done | In review | Planned | Superseded | Retired | Parked | Non-goal |
|---|---:|---:|---:|---:|---:|---:|---:|
| Legacy acpx line — authority retired | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| vNext Stage 0/1 | 7 | 0 | 0 | 0 | 0 | 0 | 0 |
| vNext Stage 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Registered per-agent profiles | 0 | 0 | 0 | 3 | 0 | 0 | 0 |
| Runtime Binding era | 0 | 0 | 0 | 0 | 3 | 0 | 0 |
| Boundary reset | 0 | 0 | 6 | 0 | 0 | 0 | 0 |
| Later integration | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| Explicit exclusions | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

**What Retired and Superseded mean here.** Both are **documentation-authority** states recorded by the
boundary reset. `Retired` means the tracked architecture no longer targets the capability at all;
`Superseded` means a narrower capability replaces it. In both cases the implementing source is **still
merged on `main`** and still runs in production: no source file was deleted, no profile was unregistered,
no acpx module was removed, no Binding root or artifact tree was touched, and no operator storage was
migrated or removed. The rows say what the project is now *aiming at*, and the board says what the code
currently *is*.

**What Planned means here.** Documentation authority exists and is complete; source does not. Each Planned
row needs a distinct source-implementation approval recorded after the authority alignment merges, and the
row for profile retirement needs one further confirmation before any profile is deleted from source. A
Planned row is never evidence that the capability is reachable, testable, or deployable today.

**Publication boundary.** `Done` means implemented in the source line whose package metadata is 0.5.3 and
which covers the Stage 0/1 Native ACP core, `arsd`, and the retired Binding framework. The live GitHub
Releases and PyPI listings are authoritative for which versions are published; neither publication nor
local acceptance is a deployment, enablement, cutover, or integration approval.

Update this tracker only when requirements, implementation state, or acceptance evidence changes. Keep
evidence cells short; details belong in active plans or cold archives.

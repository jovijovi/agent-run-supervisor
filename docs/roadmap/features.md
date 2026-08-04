---
title: "ARS vNext Feature and Capability Tracker"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-03
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/features.md"
---
# ARS vNext Feature and Capability Tracker

Only vNext direction and the authority-versus-source delta live here. Detailed v0.1.7 feature closure is
retained in the cold archive and Git history; it is not default development context.

Status legend: **Done** · **Implemented** · **In review** · **Planned** · **Superseded** · **Retired** ·
**Parked** · **Non-goal**

`Done` means merged on `main`; `Implemented` means the source exists on a task branch and is not merged.
Every V4 boundary-reset row is `Done`. **Superseded** and
**Retired** describe *authority*; the Remaining cell says what happened to the source, because for the
boundary reset the two moved together.

| ID | Capability | Product | Impl | Evidence / authority | Remaining |
|---|---|---|---|---|---|
| F-LEGACY-COMPAT-001 | legacy v0.1.7 acpx line: no product, runtime, or compatibility authority | Not a product | Retired | GOAL acpx removal direction; board; archived authority snapshot | authority retired, source not deleted: the acpx code and its emitted fields stay on `main` until separately authorized removal, maintenance is separately approved, and only bounded differential/comparison fixtures remain as reference |
| F-VNEXT-ADMISSION-001 | structured admission → immutable AgentRunSpec sealed before spawn | Required | Done | PRD R1; archived plan C1/C4; freeze-order + spec-hash suites | merged; the resolution inputs change with F-AGENT-REGISTRY-001 |
| F-VNEXT-PROCESS-001 | ManagedProcess live stdio supervision | Required | Done | PRD R2; archived plan C3; live-wire/group-kill/reap suite | merged; first released in 0.2.0 |
| F-NATIVE-ACP-001 | Native ACP exact-config core through ars-core | Required | Done | PRD R2–R3; archived plan C1–C10; real B-grade acceptance | merged; live option domains land with F-BOUNDARY-RESET-001 |
| F-VNEXT-SESSION-001 | process-per-Run, session/load continuity, cross-Run switching | Required | Done | PRD R4; archived plan C6/C9/C10; real continuity + exact switch/rollback | merged, including the fail-closed reuse gate that rides F-BOUNDARY-RESET-001 |
| F-VNEXT-STATE-001 | unknown/quarantined/retryable=false, markers, no replay | Required | Done | PRD R5; archived plan C2/C8; terminal-table + write-once + cancellation suites | merged; first released in 0.2.0 |
| F-VNEXT-PERMISSION-001 | frozen grant, default-deny mediation, real canary | Required | Done | PRD R7; L1/L2 bridge; real denied-action canary PASS (operator-held) | none — the canary stays mandatory per registered agent |
| F-VNEXT-EVIDENCE-001 | isolated Native stores and bounded runtime ledger | Required | Done | PRD R8–R9; archived plan C6–C8; poisoned-legacy isolation + bounded-writer suites | merged; value-blind evidence lands with F-ENV-EVIDENCE-001 |
| F-ARSD-001 | local UDS production ingress, ownership, reconciliation, cgroup containment | Required | Done | PRD R6/R10; A1–A5 closed; default-on enabled 2026-07-23 (operator-held) | none — operate on the recorded CPython runtime invariant |
| F-NATIVE-ADAPTER-CODEX-001 | Codex official ACP adapter closed profile | Required | Retired | PRD R12; [plan](../plans/archive/2026-07-25-codex-official-adapter-admission.md); local acceptance | deleted from `main`; the adapter is now an operator-registered command |
| F-NATIVE-ADAPTER-CLAUDE-001 | Claude official ACP adapter closed profile | Required | Retired | PRD R12; [plan](../plans/archive/2026-07-25-claude-official-adapter-b3-b5-closure.md); ACP discovery | deleted from `main`; its one cited ACP deviation survives as claude-agent-acp-compat-v1 |
| F-STANDARD-NATIVE-ACP-001 | versioned conformance profile + typed operator-owned agent identity | Required | Superseded | PRD R12; [plan](../plans/archive/2026-07-29-standard-native-acp-v1.md) | the profile survives as standard-native-acp-v1; its registration layer is replaced by the registry entry |
| F-RUNTIME-BINDING-001 | operator-owned Runtime Binding + sealed runtime provenance | Required | Retired | [history](../archive/binding-era-2026-07/README.md); [plan](../plans/archive/2026-07-26-runtime-binding-refactor.md) | runtime_binding.py and attestation.py deleted from `main`; live roots untouched |
| F-RUNTIME-BINDING-002 | complete wrapped-adapter package closure in frozen artifact identity | Required | Retired | [history](../archive/binding-era-2026-07/README.md); [rationale](../archive/binding-era-2026-07/retirement-rationale.md) | deleted from `main`: ARS makes no artifact-integrity claim, and a scan proves no digest survives |
| F-RUNTIME-BINDING-003 | profile-scoped active selection, one generation per profile | Required | Retired | [plan](../plans/archive/2026-07-29-multi-profile-runtime-binding.md); binding-era archive | released on v0.5.2; retired with the Binding layer, and its live roots are untouched |
| F-AGENT-REGISTRY-001 | one operator-owned TOML agent registry, read once at daemon startup | Required | Done | PRD R13; GOAL contract 9; [registry contract](../design/agent-registry.md) | merged; a registry edit takes effect at the next daemon start. Not published or deployed |
| F-BOUNDARY-RESET-001 | external AGENT boundary reset: a small closed profile set, preserved command semantics, fail-closed load-only reuse | Required | Done | PRD R4/R12/R13; GOAL contracts 1, 3, 9; architecture §3–§4 | merged; the three per-agent profiles are deleted from source, not aliased or disabled |
| F-OBSERVED-EVIDENCE-001 | observed runtime facts are evidence or a policy warning, never a gate | Required | Done | PRD R14; architecture §3.3; result/event schema §9.5 | merged; exactly five observation-based refusals remain, and agentInfo is not one |
| F-ENV-EVIDENCE-001 | environment values never enter sealed material, hash input, or the rendered carrier | Required | Implemented | PRD R15; technical solution §7; result/event schema §9.2 | the value-blind half is merged; the dynamic per-Run literal guard over free-form Run text is **removed** on a task branch, with the retention consequence stated in PRD R15. Unmerged, unpublished |
| F-MODEL-ONLY-FIDELITY-001 | declared configuration-fidelity modes + `cursor-native-acp-v1` | Required | Implemented | PRD R3/R12; technical solution §5; [registry contract](../design/agent-registry.md) | model-only stops at the exact model readback, dispatches no effort RPC, and reports the shared `N/A` sentinel. Existing profiles keep separate selectors and their `profile_hash`. Unmerged, unpublished |
| F-LAUNCH-PERMISSION-001 | profile-selected per-Run launch permission material | Required | Implemented | PRD R7/R12; technical solution §1.2/§5; [registry contract](../design/agent-registry.md) §7 | one closed read-only policy, compiled from the frozen grant, materialized privately under the Run directory before spawn and removed after proven reap; `launch.json` binds it by content digest. An unenforceable grant refuses before spawn. **No registered profile selects it**: the one backend's key names an agent's whole configuration root, and per-Run material there breaks cross-Run `session/load` continuity. Unmerged, unpublished |
| F-ACP-SDK-012-001 | optional `native` extra pinned to `agent-client-protocol==0.12.0` (ACP schema v1.19) | Required | Implemented | PRD R12; technical solution §0/§11 | sender hook, prompt causal boundary, update ordinal domain, delivery barrier, and SDK root-log containment re-verified against 0.12.0; the SDK `http` extra stays uninstalled. Unmerged, unpublished |
| F-RECONCILE-ORDERED-001 | total ordered fail-closed startup reconciliation, absent ≠ corrupt | Required | Done | PRD R10; architecture §6.1–§6.2; technical solution §9 | merged; strictly more refusals than the tolerant reader it replaced |
| F-ARSD-API-002 | api_version 2 with the eight-operation drain matrix | Required | Done | PRD R11; architecture §1 two-protocol note | merged; submit refused at v1, the other seven accepted. No caller cutover is approved |
| F-SACHIMA-ARSD-001 | Sachima socket backend | Later integration | Parked | GOAL/PRD stage boundary | ARS production acceptance closed; integration still requires its own separate approval |
| F-NONGOAL-001 | public/root/TCP/multi-tenant/business-orchestration surfaces | Non-goal | Non-goal | GOAL; PRD §6; non-approvals | separate product decision only |

## Completion roll-up

| Area | Done | Implemented | In review | Planned | Superseded | Retired | Parked | Non-goal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Legacy acpx line — authority retired | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| vNext Stage 0/1 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Configuration fidelity, SDK pin, launch permission | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| vNext Stage 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Registered per-agent profiles | 0 | 0 | 0 | 0 | 1 | 2 | 0 | 0 |
| Runtime Binding era | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 |
| Boundary reset | 5 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Later integration | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| Explicit exclusions | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

Four rows are `Implemented`: the SDK pin, the fidelity modes, profile-selected launch permission, and the
narrowed environment-evidence guarantee all exist in source on a task branch and are not merged. The board still tracks planned work —
acpx removal — that has no feature row here.

**What Retired and Superseded mean here.** Both are **documentation-authority** states recorded by the
boundary reset: `Retired` means the tracked architecture no longer targets the capability at all, and
`Superseded` means a narrower capability replaces it. Where the Remaining cell says *deleted from `main`*,
the deletion is real and merged. Nothing outside the repository moved — no Binding root or artifact tree
was touched, no acpx module was removed, no `/opt` path was deleted, and no operator storage was migrated
or removed.

**Publication boundary.** `Done` means merged on `main` — nothing more. Whether a given `Done` row is in a
published artifact, and which version is running, are volatile facts the board
([`current-status.md`](current-status.md)) owns; the live GitHub Releases and PyPI listings are
authoritative for what is published. Neither merge, publication, nor a green verification is a deployment,
enablement, cutover, or integration approval, and each of those remains its own separate decision — as does
the mandatory real-agent denied-action canary per registered agent.

Update this tracker only when requirements, implementation state, or acceptance evidence changes. Keep
evidence cells short; details belong in active plans or cold archives.

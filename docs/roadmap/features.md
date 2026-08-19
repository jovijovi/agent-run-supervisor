---
title: "ARS vNext Feature and Capability Tracker"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-11
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/features.md"
---
# ARS vNext Feature and Capability Tracker

Only vNext direction and the authority-versus-source delta live here. Detailed v0.1.7 feature closure is
retained in the cold archive and Git history; it is not default development context.

Status legend: **Done** · **Implemented** · **In review** · **Planned** · **Superseded** · **Retired** ·
**Removed** · **Parked** · **Non-goal**

`Done` means merged on `main`; `Implemented` means the source exists on a task branch and is not merged.
Every V4 boundary-reset row is `Done`. **Superseded** and **Retired** describe *authority*, and the
Remaining cell says what happened to the source, because for the boundary reset the two moved together.
**Removed** is the stronger claim: the authority was retired *and* the source is deleted.

| ID | Capability | Product | Impl | Evidence / authority | Remaining |
|---|---|---|---|---|---|
| F-LEGACY-COMPAT-001 | the legacy acpx line | Not a product | Removed | GOAL boundary section; containment scanner; wheel/sdist manifest gates | merged; source deleted from `main`: runtime, CLI leaves, fixtures, and the process-exit result field. Audited keep set was empty, so no fixture remains |
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
| F-AGENT-REGISTRY-001 | one operator-owned TOML agent registry, read once at daemon startup | Required | Done | PRD R13; GOAL contract 9; [registry contract](../design/agent-registry.md) | merged; a registry edit takes effect at the next daemon start. This tracker records source state only — publication and deployment truth belong to live release and operator sources |
| F-BOUNDARY-RESET-001 | external AGENT boundary reset: a small closed profile set, preserved command semantics, fail-closed load-only reuse | Required | Done | PRD R4/R12/R13; GOAL contracts 1, 3, 9; architecture §3–§4 | merged; the three per-agent profiles are deleted from source, not aliased or disabled |
| F-OBSERVED-EVIDENCE-001 | observed runtime facts are evidence or a policy warning, never a gate | Required | Done | PRD R14; architecture §3.3; result/event schema §9.5 | merged; exactly five observation-based refusals remain, and agentInfo is not one |
| F-ENV-EVIDENCE-001 | environment values never enter sealed material, hash input, or the rendered carrier | Required | Done | PRD R15; technical solution §7; result/event schema §9.2 | merged; the dynamic per-Run literal guard is removed, and agent-authored free-form text may retain projected values as stated in PRD R15 |
| F-MODEL-ONLY-FIDELITY-001 | declared configuration-fidelity modes + `cursor-native-acp-v1` | Required | Done | PRD R3/R12; technical solution §5; [registry contract](../design/agent-registry.md) | merged; model-only stops at the exact model readback, dispatches no effort RPC, and reports the shared `N/A` sentinel. Existing profiles keep separate selectors and their `profile_hash` |
| F-CURSOR-GRANT-MODE-001 | grant-driven Cursor permission mode: `ask` for read-only grants, `agent` otherwise | Required | Done | PRD R7/R12; [archived plan](../plans/archive/2026-08-07-cursor-grant-mode.md); hermetic grant-mode suite | merged on `main`; `cursor-native-acp-v1` revision 3 moves only Cursor's `profile_hash`; a cooperative mode mitigation, not a permission/sandbox guarantee; mediation and the completion backstop unchanged |
| F-LAUNCH-PERMISSION-001 | profile-selected per-Run launch permission material | Required | Done | PRD R7/R12; technical solution §1.2/§5; [registry contract](../design/agent-registry.md) §7 | merged; one closed read-only policy is available, but no registered profile selects it because the affected backend's configuration-root key would break cross-Run `session/load` continuity |
| F-ACP-SDK-012-001 | optional `native` extra pinned to `agent-client-protocol==0.12.1` (ACP schema v1.19) | Required | Implemented | PRD R12; technical solution §0/§11 | task-branch candidate, not on `main`. 0.12.1 removed the `sender_factory` seam, so the pre-write tap now rides the message-level `Transport` injection point around the SDK's own sender/NDJSON transport; prompt causal boundary, update ordinal domain, delivery barrier, and SDK root-log containment are re-verified against 0.12.1; the SDK `http` extra stays uninstalled |
| F-RECONCILE-ORDERED-001 | total ordered fail-closed startup reconciliation, absent ≠ corrupt | Required | Done | PRD R10; architecture §6.1–§6.2; technical solution §9 | merged; strictly more refusals than the tolerant reader it replaced |
| F-ARSD-API-002 | api_version 2 with the eight-operation drain matrix | Required | Superseded | PRD R11; superseded by F-SESSION-NOCLOSE-001 | replaced by single-version `api_version` 3 admission; the drain matrix is deleted, not disabled, because no client population exists |
| F-SESSION-NOCLOSE-001 | one durable resumable Session kind: Runs terminate, Sessions do not close | Required | Done | PRD R4/R5/R9/R11; GOAL contract 3; [plan](../plans/archive/2026-08-06-session-no-close-model.md) | merged: optional `session_id`, deterministic prospective identity, one fully bound record before the dispatch marker, quarantine as independent evidence, `api_version` 3. Session durability and indefinite resumability are the product feature, not debt. This tracker records source state only — publication and runtime truth belong to live release and operator sources |
| F-LONG-RUN-TIMEOUT-001 | 6-hour default and 7-day maximum for the per-Run hard turn timeout | Required | Done | PRD R2; technical solution §4; [archived plan](../plans/archive/2026-08-09-long-run-timeout-limits.md) | merged on `main`; no wire/schema or Session lifecycle change, and no release or deployment claim |
| F-SESSION-REPLAY-BACKPRESSURE-001 | Session reuse under AGENT history replay: replay separated from the current Run; bounded serial evidence ledger with exact durable accounting and FIFO absolute deadlines | Required | Done | PRD R4/R9; GOAL contract 3; result/event schema §5.6; hermetic ledger/replay/backpressure + Socket v3 reuse suites | merged on `main`; this tracker records source state only, and neither publication nor deployment truth. Replay is identity-validated then aggregated into one bounded `session_replay_summary` and kept out of per-event evidence, mediation accounting, tool-call closure and `final_message`. The ledger freezes actual-sequence canonical bytes at acceptance, charges in-flight work through durable acknowledgement, grows 1024→8192 events / 8→64 MiB only after persistence progress, isolates observers from caller cancellation, ranks failures by original ordinal, and certifies every accepted ticket at close. A stalled or failed sink still reaches bounded `EVIDENCE_PIPELINE`; the locked SDK supplies no transport backpressure |
| F-RUN-EVENT-BUDGET-CONFIG-001 | operator-configurable per-Run event-ledger admission budget, default 4 GiB | Required | In review | PRD R9; technical solution §4; [active plan](../plans/active/2026-08-11-configurable-run-event-budget.md) | feature-branch candidate pending pre-integration review and merge — not on `main`, not released or deployed. `arsd --max-run-event-budget-bytes` is the admission ceiling for every Run that daemon accepts, reported as `server_info.limits.max_run_event_budget_bytes`, judged by one injected `EventBudgetPolicy` separate from the unchanged individual hard limits and itself bounded by the structural maximum those limits imply. The effective ceiling is also persisted in each accepted Run's write-once `submission.json` and strictly validated, so a historical Run stays auditable across a reconfigure or restart; that record alone moves `SUBMISSION_SCHEMA_VERSION` to 4. A theoretical per-Run event-ledger ceiling only — not preallocated memory, a Run-directory disk quota, or a daemon-wide aggregate. No wire, request-digest, Spec, or launch schema version moves |
| F-OMP-REASONIX-SOURCE-001 | minimal operator-registry source support for OMP and Reasonix, with Reasonix static approval fidelity and canonical workspace regression coverage | Required | Done | PRD R3/R4/R7/R12/R13; [archived plan](../plans/archive/2026-08-11-omp-reasonix-source-support.md); isolated canaries | merged on `main`; OMP remains fail-closed for mutations under the observed 17.2.12 behavior, with no permissive fallback or unproven write-family mapping. This tracker records source state only; publication, deployment, and live activation remain separate facts |
| F-SACHIMA-ARSD-001 | Sachima socket backend | Later integration | Parked | GOAL/PRD stage boundary | ARS production acceptance closed; integration still requires its own separate approval |
| F-NONGOAL-001 | public/root/TCP/multi-tenant/business-orchestration surfaces | Non-goal | Non-goal | GOAL; PRD §6; non-approvals | separate product decision only |

## Completion roll-up

| Area | Done | Implemented | In review | Planned | Superseded | Retired | Removed | Parked | Non-goal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| The legacy acpx line | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| Session no-close model | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Long-Run timeout limits | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Session reuse under history replay | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Configurable per-Run event budget | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| vNext Stage 0/1 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Configuration fidelity, SDK pin, launch permission | 3 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| OMP and Reasonix source support | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vNext Stage 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Registered per-agent profiles | 0 | 0 | 0 | 0 | 1 | 2 | 0 | 0 | 0 |
| Runtime Binding era | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 |
| Boundary reset | 5 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| Later integration | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| Explicit exclusions | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

One row is `Implemented`: F-ACP-SDK-012-001 moved to the 0.12.1 SDK pin on a task branch, so that pin is
not on `main`, released, or deployed. One row is `In review`: F-RUN-EVENT-BUDGET-CONFIG-001 is a
feature-branch candidate in pre-integration review, so it is not on `main`, and nothing about it is
released or deployed. F-OMP-REASONIX-SOURCE-001,
F-SESSION-REPLAY-BACKPRESSURE-001,
F-LONG-RUN-TIMEOUT-001, and F-CURSOR-GRANT-MODE-001 are each merged on `main`.
The acpx removal is merged on `main`. F-LEGACY-COMPAT-001 records it as
`Removed` in source terms only.

**What Retired and Superseded mean here.** Both are **documentation-authority** states recorded by the
boundary reset: `Retired` means the tracked architecture no longer targets the capability at all, and
`Superseded` means a narrower capability replaces it. Where the Remaining cell says *deleted from `main`*,
the deletion is real and merged. Nothing outside the repository moved — no Binding root or artifact tree
was touched, no `/opt` path was deleted, and no operator storage was migrated or removed.
The removal of the retired runtime was a separate, later decision recorded by F-LEGACY-COMPAT-001.

**Publication boundary.** `Done` means merged on `main` — nothing more. Published package/release facts
come from live GitHub Releases and PyPI; deployed/running facts come from operator-held runtime/live checks.
Neither merge, publication, nor a green verification is a deployment, enablement, cutover, or integration
approval, and each of those remains its own separate decision — as does the mandatory real-agent
denied-action canary per registered agent.

Update this tracker only when requirements, implementation state, or acceptance evidence changes. Keep
evidence cells short; details belong in active plans or cold archives.

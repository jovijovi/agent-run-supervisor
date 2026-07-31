---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-30
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Living vNext dashboard. It records current scope and gates, not implementation history.

```text
base_branch: main
active_plan: ../plans/active/2026-07-30-ars-v4-boundary-reset.md
```

## Current position

- ARS vNext remains the local supervision path: `trusted caller → arsd UDS → ars-core/Native ACP → one local process running the registered command`.
- **Tracked authority is now the V4 external-AGENT boundary reset**: one operator-owned agent registry read once at daemon startup, the four-way boundary, the environment-value sink boundary, total ordered reconciliation, and fail-closed load-only Session reuse. Operator contract: [`agent-registry.md`](../design/agent-registry.md).
- The artifact/Binding-era authority is retired into [`docs/archive/binding-era-2026-07/`](../archive/binding-era-2026-07/README.md) as cold history. It is not an alternative source of truth.
- **Decision 1 is recorded as option (a) on 2026-07-30**: policy-level retirement of the three per-agent profiles. That is a policy decision about authority. It is **not** approval to delete anything from source.
- Stage 0/1 Native ACP core is closed on `main`. Stage 2 `arsd` is closed and the previously accepted local user service is enabled. Neither implies release, Sachima, Gateway, public ingress, or further deployment approval.
- Source pins the optional Python ACP SDK as `agent-client-protocol==0.11.1`. It is distinct from any adapter's own bundled JavaScript ACP SDK.
- Sachima integration is Parked and separately approvable. Remaining acpx product, runtime, and compatibility content is planned for separately authorized removal; bounded differential fixtures remain only as reference.

## Authority versus source — the Stage 1→3 window

**These differ right now, deliberately and visibly.** The documents describe the reset; the code does not
implement it yet. In plain words: **the authority chain and both READMEs describe the operator agent
registry, while source still implements the artifact/Binding architecture** — and that stays true until the
three source stages land. The window opens when this documentation gate merges and closes when Stage 3
merges.

| | Authority says | Source on `main` still is |
|---|---|---|
| agent selection | one operator TOML registry, read once at startup, resolved in memory | a Binding root read per Run, with promoted generations |
| profiles registered | two: one standard, one evidenced compat | four, including three per-agent profiles |
| daemon operand | an agents file, required in daemon mode and for unit rendering | a Binding root, required in the same two places |
| operator CLI | `agents validate`, `agents doctor`, `run inspect` | the `runtime-binding` command group |
| artifact identity | none: no digest, closure, promotion, or attestation | frozen artifact identity, package closures, and spawn-boundary attestation |
| environment values | never persisted, hashed, logged, or returned | serialized into launch records and re-hashed by legacy inspection |
| reuse on an absent Session record | refused before the lease | can still fall through to a new external Session |
| reconciliation | absent ≠ corrupt, one exhaustive first-match outcome | absent and corrupt collapse to the same result |
| caller wire | `api_version` 2 with a per-operation drain matrix | `api_version` 1, version rejected at the envelope |

Consequences an operator must not mis-read while the window is open:

- **Production is untouched.** It runs v0.5.3 with three profile-scoped Bindings promoted under a live root. This documentation change deployed nothing, restarted nothing, migrated nothing, and removed no `/opt` or Binding-root path.
- **The registry surface is not runnable.** No agents file is read by any released code, and authoring one against a live deployment changes nothing and is not approved.
- **Both public READMEs describe the reset**, each carrying the same delta note, so neither language over-claims. They document the target operator surface, not the shipped `v0.5.x` one.
- The window closes only when the staged source work lands, in order: **Stage 1** fail-closed reuse and total reconciliation, **Stage 2** the environment-value guard, **Stage 3** the boundary reset. Local development of all three is authorized as of 2026-07-30 and is dependency-gated in that order; landing each still needs its own PR and merge decision, and Stage 3's profile deletion needs one more approval on top.

## Phase board

| Area | State | Current scope |
|---|---|---|
| Stage 0/1 — Native ACP core | Closed on `main` | [closed phase archive](archive/phases/vnext-stage01-native-acp.md) |
| Stage 2 — `arsd` local production ingress | Closed; previously accepted local user service enabled | [closed phase archive](archive/phases/vnext-stage2-arsd-production-ingress.md) |
| Runtime Binding era | Retired as target architecture; implementing source still merged and running | [binding-era archive](../archive/binding-era-2026-07/README.md) · [feature tracker](features.md) |
| Per-agent profiles | Superseded as authority; still registered in source | [features](features.md) F-NATIVE-ADAPTER-*, F-STANDARD-NATIVE-ACP-001 |
| Standard Native ACP (v1) + agent identity | Merged on the v0.5.3 line; plan archived as completed | [archived plan](../plans/archive/2026-07-29-standard-native-acp-v1.md) |
| V4 authority alignment (Stage 0) | Merged on `main`; documentation only | [active plan](../plans/active/2026-07-30-ars-v4-boundary-reset.md) |
| V4 Stage 1 — fail-closed reuse + total reconciliation | Local source work authorized 2026-07-30 and in flight on branch `feat/v4-failclosed-hardening` | [active plan](../plans/active/2026-07-30-ars-v4-boundary-reset.md) · [features](features.md) F-RECONCILE-ORDERED-001 |
| V4 Stages 2–3 | Local source work authorized; not started — Stage 2 waits on Stage 1 merging, Stage 3 on Stage 2 | [active plan](../plans/active/2026-07-30-ars-v4-boundary-reset.md) · [features](features.md) F-AGENT-REGISTRY-001, F-BOUNDARY-RESET-001, F-ENV-EVIDENCE-001, F-OBSERVED-EVIDENCE-001, F-ARSD-API-002 |
| Sachima `ArsdBackend` integration | Parked | separately approvable later integration |
| acpx product/runtime/compatibility removal | Planned | separately authorized source work; bounded comparison fixtures remain |

## Open gates / next decisions

None of these is approved here. Each is separate and non-transitive.

**Recorded, and bounded:**

- **Decision 1 — profile-retirement policy: option (a), recorded 2026-07-30.** It authorized this authority retirement and nothing else.
- **Source implementation for V4 Stages 1–3 — recorded 2026-07-30, after the authority alignment merged.** It covers **local** source, test, and status work only, taken serially at the stage gates, plus per-stage commit and push once a stage candidate has passed verification, independent review, and acceptance. It carries **no** PR, merge, release, deployment, restart, migration, canary, or production authority, and it does not reach the profile deletion below.

**Open human decisions:**

- **Profile-retirement execution in source** — deleting the three registered per-agent profiles (Stage 3 WP3.3). Introducing a retirement capability and using one were always two decisions; the second is **not taken**, and it is required before that source work runs.
- **PR creation and merge for each V4 stage** — separate controller decisions; the source-implementation approval above does not imply either.
- **Decision 2 — cutover and the one-time legacy-Session load refusal.** Every live Session at cutover ends; continuing that work means a new Session with caller-owned context handoff. Open, and it does not block source work.
- **Decision 3 — lifetime of the legacy `v0.5.x` line.** Open, and it governs release and branch policy, which is separately approved anyway.

**Operator actions, unchanged by the reset:**

- Run the mandatory denied-action mediation canary per agent before that agent's use.
- Removal of the `/opt` artifact trees and the Binding roots. They simply stop being referenced by the target architecture; nothing deletes them.
- Deployment, service restart, unit re-render, migration, release, and publication.

## Boundaries

See [`non-approvals.md`](non-approvals.md) for the full current boundary. This board records status only;
it does not authorize operational changes, release/publication, integrations, or acpx removal work.

## Cold history

- [Binding-era authority archive](../archive/binding-era-2026-07/README.md)
- [Runtime Binding source/package-closure archive](archive/phases/vnext-runtime-binding-source-closure.md)
- [Closed phase archive](archive/README.md)
- [Archived implementation plans](../plans/archive/README.md)
- [Former authority snapshot](../archive/pre-vnext-reset-2026-07-21/README.md)

## Verification

See [`verification.md`](verification.md) and [`docs/AI_FLOW.md`](../AI_FLOW.md).

---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-02
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Living vNext dashboard. It records current scope and gates, not implementation history.

```text
base_branch: main
active_plan: none — no plan is active
```

## Current position

- ARS vNext is the local supervision path: `trusted caller → arsd UDS → ars-core/Native ACP → one local process running the registered command`.
- **The V4 external-AGENT boundary reset is merged on `main`**: one operator-owned agent registry read once at daemon startup, the four-way boundary, the environment-value sink boundary, total ordered reconciliation, and fail-closed load-only Session reuse. Operator contract: [`agent-registry.md`](../design/agent-registry.md).
- **Authority and source are aligned.** The tracked authority chain describes merged source, so no authority-versus-source delta remains to track. The artifact/Binding-era authority and its retired implementation are cold history in [`docs/archive/binding-era-2026-07/`](../archive/binding-era-2026-07/README.md).
- Two source profiles are registered: `standard-native-acp-v1` and `claude-agent-acp-compat-v1`. The three per-agent profiles are deleted from source, not aliased or disabled.
- **Package metadata is prepared as `0.6.0`.** That is a version number and a CHANGELOG section, nothing more: no tag, GitHub Release, PyPI upload, deployment, service restart, or cutover has happened.
- Source pins the optional Python ACP SDK as `agent-client-protocol==0.11.1`. It is distinct from any adapter's own bundled JavaScript ACP SDK.
- Sachima integration is Parked and separately approvable. Remaining acpx product, runtime, and compatibility content is planned for separately authorized removal; bounded differential fixtures remain only as reference.

## `main` versus the released `0.5.x` line

The published artifacts predate the reset, so an operator must not read merged source as deployed
behavior. What differs:

| | `main`, prepared as `0.6.0` | Released `0.5.x` still is |
|---|---|---|
| agent selection | one operator TOML registry, read once at startup, resolved in memory | a Binding root read per Run, with promoted generations |
| profiles registered | two: one standard, one evidenced compat | four, including three per-agent profiles |
| daemon operand | `--agents-file`, required in daemon mode and for unit rendering | `--binding-root`, required in the same two places |
| operator CLI | `agents validate`, `agents doctor`, `run inspect` | the `runtime-binding` command group |
| artifact identity | none: no digest, closure, promotion, or attestation | frozen artifact identity, package closures, and spawn-boundary attestation |
| environment values | never persisted, hashed, logged, or returned | serialized into launch records and re-hashed by legacy inspection |
| reuse on an absent Session record | refused before the lease | can still fall through to a new external Session |
| reconciliation | absent ≠ corrupt, one exhaustive first-match outcome | absent and corrupt collapse to the same result |
| caller wire | `api_version` 2 with a per-operation drain matrix | `api_version` 1, version rejected at the envelope |

Consequences that must not be mis-read:

- **Production is untouched.** It runs `0.5.3` with three profile-scoped Bindings promoted under a live root. Nothing here deployed, restarted, migrated, or removed anything, and no `/opt` or Binding-root path was touched.
- **The registry surface is not released.** No published artifact reads an agents file, so authoring one against a live deployment changes nothing and is not approved.
- **Sessions do not carry across the two lines.** A legacy record is refused for `session/load` with a stable code while staying owner-scoped `status`/`list`/`close`-readable. That is deliberate, and it is Decision 2 below.

## Phase board

| Area | State | Current scope |
|---|---|---|
| Stage 0/1 — Native ACP core | Closed on `main` | [closed phase archive](archive/phases/vnext-stage01-native-acp.md) |
| Stage 2 — `arsd` local production ingress | Closed; previously accepted local user service enabled | [closed phase archive](archive/phases/vnext-stage2-arsd-production-ingress.md) |
| V4 boundary reset — Stages 0–3 | Closed on `main` | [archived plan](../plans/archive/2026-07-30-ars-v4-boundary-reset.md) · [features](features.md) F-AGENT-REGISTRY-001, F-BOUNDARY-RESET-001, F-OBSERVED-EVIDENCE-001, F-ENV-EVIDENCE-001, F-RECONCILE-ORDERED-001, F-ARSD-API-002 |
| Runtime Binding era | Retired; the implementing source is deleted from `main` | [binding-era archive](../archive/binding-era-2026-07/README.md) · [feature tracker](features.md) |
| Per-agent profiles | Retired; deleted from `main` | [features](features.md) F-NATIVE-ADAPTER-*, F-STANDARD-NATIVE-ACP-001 |
| `0.6.0` release preparation | Version metadata and CHANGELOG prepared; unpublished | [CHANGELOG](../../CHANGELOG.md) |
| Sachima `ArsdBackend` integration | Parked | separately approvable later integration |
| acpx product/runtime/compatibility removal | Planned | separately authorized source work; bounded comparison fixtures remain |

## Open gates / next decisions

None of these is approved here. Each is separate and non-transitive.

- **Release and publication of the `0.6.0` line** — tag, GitHub Release, and PyPI upload are untaken. The prepared version metadata is not one of them and does not imply them.
- **Decision 2 — cutover and the one-time legacy-Session load refusal.** Every live Session at cutover ends; continuing that work means a new Session with caller-owned context handoff. Open.
- **Decision 3 — lifetime of the legacy `0.5.x` line.** Open, and it governs release and branch policy.
- **Sachima `ArsdBackend` integration.** Parked; needs its own approval after its own evidence.
- **acpx product, runtime, and compatibility removal.** Planned; separately authorized source and docs work.

**Operator actions, unchanged by the reset:**

- Run the mandatory denied-action mediation canary per registered agent before that agent's use.
- Removal of the `/opt` artifact trees and the Binding roots. They simply stopped being referenced; nothing deletes them.
- Deployment, service restart, unit re-render, and migration.

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

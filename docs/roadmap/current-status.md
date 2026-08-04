---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-03
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Living vNext dashboard. It records current scope and gates, not implementation history.

```text
base_branch: main
active_plan: docs/plans/active/2026-08-03-cursor-cross-run-session-resume.md (defect repair, unmerged)
```

## Current position

- ARS vNext is the local supervision path: `trusted caller → arsd UDS → ars-core/Native ACP → one local process running the registered command`.
- **The V4 external-AGENT boundary reset is merged on `main`**: one operator-owned agent registry read once at daemon startup, the four-way boundary, the environment-value sink boundary, total ordered reconciliation, and fail-closed load-only Session reuse. Operator contract: [`agent-registry.md`](../design/agent-registry.md).
- **Authority and source are aligned.** The tracked authority chain describes merged source, so no authority-versus-source delta remains to track. The artifact/Binding-era authority and its retired implementation are cold history in [`docs/archive/binding-era-2026-07/`](../archive/binding-era-2026-07/README.md).
- Three source profiles are registered: `standard-native-acp-v1`, `claude-agent-acp-compat-v1`, and `cursor-native-acp-v1` (model-only configuration fidelity, and that alone). The three per-agent profiles are deleted from source, not aliased or disabled.
- **No registered profile selects a launch-permission policy.** `cursor-native-acp-v1` revision 2 removed the selection revision 1 carried: that backend's environment key names an agent's whole configuration root, so per-Run material relocated and then deleted the agent's own Session state and broke cross-Run `session/load` continuity. The mechanism stays registered and selectable; enforcement stays with ACP mediation, the completion backstop, and the mandatory per-agent canary. Repair is on a task branch, unmerged.
- **The per-Run environment-value literal guard is removed** from source: `RunTextGuard`, `SafeText`, the withholding markers, `arsd/safe_logging.py`, the external-Session-id sensitive-collision refusal, and `ResolvedEnvironment.sensitive_values()` are all deleted. Structured launch material stays value-blind and the static shape/key redactor stays; free-form Run text an AGENT authored is no longer scanned for projected values. The stated consequence is in [PRD R15](../product/prd.md).
- **Release and runtime, as of 2026-08-03.** GitHub's latest release is `v0.6.1`, published 2026-08-02; PyPI serves `0.6.1`; the enabled and active local `arsd` reports `0.6.1` and `arsd` API v2. The reset line is therefore published and running. This is the only place that fact is recorded.
- Source pins the optional Python ACP SDK as `agent-client-protocol==0.12.0` (ACP schema v1.19). It is distinct from any adapter's own bundled JavaScript ACP SDK. The SDK's `http` extra stays uninstalled: ARS is stdio ACP only and adds no HTTP/WS transport.
- Sachima integration is Parked and separately approvable. Remaining acpx product, runtime, and compatibility content is planned for separately authorized removal; bounded differential fixtures remain only as reference.

## What cutover changed, for readers of old Runs

Cutover is done, so the running line is the reset line and the pre-reset comparison is history. Two
consequences still matter when reading durable state:

- **Pre-reset Run records are immutable and are read value-blind.** Nothing rewrote, migrated, re-hashed, or
  deleted them, and no launch-hash recomputation runs over a value-bearing record.
- **Pre-reset Sessions carrying the retired ARS-derived identity hashes are refused for `session/load`** with
  a stable code, while staying owner-scoped `status`/`list`/`close`-readable. That was the deliberate
  one-time continuity loss, and it is now taken.

## Phase board

| Area | State | Current scope |
|---|---|---|
| Stage 0/1 — Native ACP core | Closed on `main` | [closed phase archive](archive/phases/vnext-stage01-native-acp.md) |
| Stage 2 — `arsd` local production ingress | Closed; previously accepted local user service enabled | [closed phase archive](archive/phases/vnext-stage2-arsd-production-ingress.md) |
| V4 boundary reset — Stages 0–3 | Closed on `main` | [archived plan](../plans/archive/2026-07-30-ars-v4-boundary-reset.md) · [features](features.md) F-AGENT-REGISTRY-001, F-BOUNDARY-RESET-001, F-OBSERVED-EVIDENCE-001, F-ENV-EVIDENCE-001, F-RECONCILE-ORDERED-001, F-ARSD-API-002 |
| Runtime Binding era | Retired; the implementing source is deleted from `main` | [binding-era archive](../archive/binding-era-2026-07/README.md) · [feature tracker](features.md) |
| Per-agent profiles | Retired; deleted from `main` | [features](features.md) F-NATIVE-ADAPTER-*, F-STANDARD-NATIVE-ACP-001 |
| Release line | `0.6.1` published and running (see *Current position*) | [CHANGELOG](../../CHANGELOG.md) |
| ACP SDK pin `0.12.0`, guard removal, `cursor-native-acp-v1`, launch permissions | Implemented on a task branch; unmerged, unpublished, undeployed | [features](features.md) F-ENV-EVIDENCE-001, F-MODEL-ONLY-FIDELITY-001, F-ACP-SDK-012-001, F-LAUNCH-PERMISSION-001 |
| Sachima `ArsdBackend` integration | Parked | separately approvable later integration |
| acpx product/runtime/compatibility removal | Planned | separately authorized source work; bounded comparison fixtures remain |

## Open gates / next decisions

None of these is approved here. Each is separate and non-transitive.

- **Any next release act** — a further version bump, tag, GitHub Release, or PyPI upload. `0.6.1` shipping approves none of them.
- **Decision 3 — lifetime of the pre-reset line.** Open, and it governs release and branch policy.
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

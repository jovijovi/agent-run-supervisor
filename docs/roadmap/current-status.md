---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-29
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Living vNext dashboard. It records current scope and gates, not implementation history.

```text
base_branch: main
active_plan: ../plans/active/2026-07-29-multi-profile-runtime-binding.md
```

## Current position

- ARS vNext remains the local supervision path: `trusted caller → arsd UDS → ars-core/Native ACP → registered external AGENT`.
- Stage 0/1 Native ACP core is closed on `main`.
- Stage 2 `arsd` is closed and the previously accepted local user service is enabled. That local status does not imply release, Sachima, Gateway, public ingress, or further deployment approval.
- Registered profiles are `opencode-native-acp` r3, `codex-acp-1.1.7` r3, and `claude-agent-acp-0.63.0` r4; all speak ACP Protocol v1.
- Source pins the optional Python ACP SDK as `agent-client-protocol==0.11.1`. It is distinct from the Claude adapter's bundled JavaScript ACP SDK.
- The Runtime Binding framework and wrapped-adapter package closure are complete in `main` source. Operator activation remains open.
- In review on the active plan: the Binding root's active-selection namespace becomes profile-scoped, so one configured root serves all three registered profiles. The pre-0.5.2 root-level `active.json` layout is rejected with a stable refusal and needs per-profile re-promotion.
- Sachima integration is Parked and separately approvable. Remaining acpx product, runtime, and compatibility content is planned for separately authorized removal; bounded differential/comparison tests and fixtures remain only as reference.

## Phase board

| Area | State | Current scope |
|---|---|---|
| Stage 0/1 — Native ACP core | Closed on `main` | [closed phase archive](archive/phases/vnext-stage01-native-acp.md) |
| Stage 2 — `arsd` local production ingress | Closed; previously accepted local user service enabled | [closed phase archive](archive/phases/vnext-stage2-arsd-production-ingress.md) |
| Runtime Binding and wrapped-adapter package closure | Source closure complete on `main`; operator activation open | [phase archive](archive/phases/vnext-runtime-binding-source-closure.md) · [feature tracker](features.md) |
| Profile-scoped Binding activation | In review on branch | [active plan](../plans/active/2026-07-29-multi-profile-runtime-binding.md) |
| Sachima `ArsdBackend` integration | Parked | separately approvable later integration |
| acpx product/runtime/compatibility removal | Planned | separately authorized source work; bounded comparison fixtures remain |

## Open gates / next operator decisions

The Runtime Binding source closure did not perform or approve any of these separate operator actions:

- Materialize the immutable, root-owned artifact closure.
- Author and promote Binding generations valid for the current contracts, one per profile under `profiles/<profile_id>/`.
- Re-accept all three registered profiles at their current revisions.
- Run the Claude R12 permission canary.

No source closure implies Binding promotion, deployment, service restart, rollout, publication, or a provider run.

## Boundaries

See [`non-approvals.md`](non-approvals.md) for the full current boundary. This board records status only;
it does not authorize operational changes, release/publication, integrations, or acpx removal work.

## Cold history

- [Runtime Binding source/package-closure archive](archive/phases/vnext-runtime-binding-source-closure.md)
- [Closed phase archive](archive/README.md)
- [Archived implementation plans](../plans/archive/README.md)
- [Former authority snapshot](../archive/pre-vnext-reset-2026-07-21/README.md)

## Verification

See [`verification.md`](verification.md) and [`docs/AI_FLOW.md`](../AI_FLOW.md).

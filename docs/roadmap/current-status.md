---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-23
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Living vNext board, not delivery history. Cold history is excluded from default agent context.

```text
base_branch: main
active_plan: docs/plans/active/2026-07-22-vnext-stage2-arsd-production-ingress.md
```

## Authority chain

```text
GOAL → vNext PRD → vNext architecture → vNext technical solution
     → features + this board → active implementation plan → code
```

The pre-reset mixed authority, v0.1.7 feature/phase ledger, completed plans, and old delivery
instructions are archived. They may be read only for audit, compatibility, or user-cited disputes
and cannot direct new work.

## Snapshot

- **Product target:** one local supervision plane: `trusted caller → arsd UDS → ars-core/Native ACP → registered external AGENT`.
- **Released baseline:** v0.1.7 acpx behavior remains compatibility-only; it is not the new-development architecture.
- **Stage 0/1:** the Native ACP core is closed and remains the compatibility baseline for the vNext supervision architecture.
- **Stage 2 — `arsd`:** the A1 source/default-closed foundation is merged into `main`. This establishes source-level, fail-closed ingress foundations only; it does not enable production or default-on operation.
- **Open Stage 2 approvals:** A2/G12 caller UID policy owner and exact values, A3 user-service/cgroup harness, A4 real S1–S5 external-AGENT acceptance, and A5 production/default-on enablement remain separately unapproved.
- **Release/publication:** v0.2.0 predates A1. A follow-on release or publication is not approved.
- **Later integration:** Sachima `ArsdBackend` remains parked until ARS production acceptance and separate approval.

## Phase board

| Phase | State | Active authority | Authorization / exit |
|---|---|---|---|
| vNext authority reset | Documentation complete | GOAL/PRD/design/roadmap + cold archive | no implementation authority |
| Stage 0/1 — Native ACP core | Closed | [phase archive](archive/phases/vnext-stage01-native-acp.md) | production claims require Stage 2 acceptance |
| Stage 2 A1 — `arsd` source/default-closed foundation | Merged into `main`; not production-enabled | [active plan](../plans/active/2026-07-22-vnext-stage2-arsd-production-ingress.md) · PRD/architecture/technical solution | A1 does not approve A2–A5 or default-on operation |
| Stage 2 A2/G12 — caller policy | Unapproved | active plan · PRD/architecture/technical solution | approved policy owner and exact UID→principal/owner-namespace mapping values |
| Stage 2 A3 — service/cgroup harness | Unapproved | active plan · PRD/architecture/technical solution | separate authorization and acceptance |
| Stage 2 A4 — real S1–S5 | Unapproved | active plan · PRD/architecture/technical solution | real external-AGENT acceptance after its prerequisite approvals |
| Stage 2 A5 — production/default-on | Unapproved | active plan · PRD/architecture/technical solution | separate production-enable decision after A2–A4 |
| Sachima integration | Parked | boundary only | after ARS production acceptance and separate approval |

## Open gates

| Gate | Blocks | Required fact |
|---|---|---|
| G9/G10/G11 | Stage 2 production | cgroup containment, real denied-action canary, and robustness; Stage 2 re-proves real credential/model usability inside S1–S5 |
| G12 caller UID/ownership policy | A4 real external-AGENT acceptance and A5 production/default-on | approved policy owner and exact UID→principal/owner-namespace mapping values (A2) |

G12 is a required gate, not optional DLP backlog. The A1 source/default-closed foundation permits
only the explicitly scoped source behavior; it does not supply G12 policy values or authorize A4,
A5, or production use. Optional DLP enhancements remain future work.

## Cold history

- Former mixed authority snapshot: [`docs/archive/pre-vnext-reset-2026-07-21/`](../archive/pre-vnext-reset-2026-07-21/README.md)
- Closed plans: [`docs/plans/archive/`](../plans/archive/README.md)
- Closed phases/tails: [`docs/roadmap/archive/`](archive/README.md)

## Explicit non-approvals

See [`non-approvals.md`](non-approvals.md). Nothing in this board authorizes any separately
unapproved Stage 2 gate, production/default-on enablement, a follow-on release or publication, or
external integration.

## Verification

See [`verification.md`](verification.md) and [`docs/AI_FLOW.md`](../AI_FLOW.md).

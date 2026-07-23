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
- **Stage 2 — `arsd`:** the A1 source/default-closed foundation and the follow-on Native permission-mediation repair are merged into `main`. Source alone does not enable production or default-on operation.
- **Closed Stage 2 gates:** A2/G12 caller policy is closed by a recorded operator policy decision (exact values controller-only); A3 is closed for user-service/restart readiness; A4 real S1–S5 external-AGENT socket-path acceptance is closed with sanitized operator-held C-grade evidence. Closure detail lives in the active plan.
- **Stage 2 A5:** production/default-on enablement is operator-approved and in progress under a controller-held enablement runbook. It is not yet enabled: no production unit, socket, or default-on behavior exists until the runbook's canary and independent review gates pass.
- **A5 runtime invariant:** the production `arsd` interpreter is CPython 3.12.3 — the runtime that carried A4 acceptance and whose build provides the pidfd APIs the crash-containment harness requires. Standalone Python 3.11.15 lacks those APIs and is not an equivalent runtime.
- **Release/publication:** v0.2.0 predates A1. A follow-on release or publication is not approved.
- **Later integration:** Sachima `ArsdBackend` remains parked until A5 closes production acceptance and a separate approval is recorded.

## Phase board

| Phase | State | Active authority | Authorization / exit |
|---|---|---|---|
| vNext authority reset | Documentation complete | GOAL/PRD/design/roadmap + cold archive | no implementation authority |
| Stage 0/1 — Native ACP core | Closed | [phase archive](archive/phases/vnext-stage01-native-acp.md) | production claims require Stage 2 acceptance |
| Stage 2 A1 — `arsd` source/default-closed foundation | Merged into `main`; not production-enabled | [active plan](../plans/active/2026-07-22-vnext-stage2-arsd-production-ingress.md) · PRD/architecture/technical solution | A1 does not approve A2–A5 or default-on operation |
| Stage 2 A2/G12 — caller policy | Closed | active plan §3 · PRD/architecture/technical solution | closed by recorded operator policy decision; exact UID→principal/owner/namespace values are controller-only and never enter the repository |
| Stage 2 A3 — service/cgroup harness | Closed (restart readiness) | active plan §3 · PRD/architecture/technical solution | user-service/restart readiness accepted; no real Run was in A3 scope |
| Stage 2 A4 — real S1–S5 | Closed | active plan §3/§11 · PRD/architecture/technical solution | real OpenCode S1–S5 socket-path acceptance passed on CPython 3.12.3; sanitized C-grade evidence operator-held |
| Stage 2 A5 — production/default-on | Approved; in progress | active plan · controller-held A5 enablement runbook | not yet enabled; closes only after production canary, independent blocker review, and verified default-on state |
| Sachima integration | Parked | boundary only | after ARS production acceptance and separate approval |

## Gates

| Gate | State | Fact |
|---|---|---|
| G9/G10/G11 | Closed by A4 | real S1–S5 socket-path acceptance: cgroup crash containment, real denied-action canary, robustness, and re-proven real credential/model usability; sanitized evidence operator-held |
| G12 caller UID/ownership policy | Closed by A2 | recorded operator policy decision; exact mapping values controller-only, delivered to the daemon only as `--caller-mapping` arguments in the mode-0600 user unit |
| A5 live enablement | Open | exact-main wheel, commit-versioned CPython 3.12.3 runtime, disabled unit install, manual-start production canary, and independent blocker review must all pass before `enable --now` |

G12 closure is a recorded operator decision; the repository intentionally records no production
mapping value. The A1 source/default-closed foundation still permits only the explicitly scoped
source behavior. Optional DLP enhancements remain future work.

## Cold history

- Former mixed authority snapshot: [`docs/archive/pre-vnext-reset-2026-07-21/`](../archive/pre-vnext-reset-2026-07-21/README.md)
- Closed plans: [`docs/plans/archive/`](../plans/archive/README.md)
- Closed phases/tails: [`docs/roadmap/archive/`](archive/README.md)

## Explicit non-approvals

See [`non-approvals.md`](non-approvals.md). This board authorizes nothing by itself: A2–A4
closure and the A5 approval are recorded operator decisions carried by the active plan. A
follow-on release or publication, Sachima/Gateway integration, and public ingress remain
separately unapproved.

## Verification

See [`verification.md`](verification.md) and [`docs/AI_FLOW.md`](../AI_FLOW.md).

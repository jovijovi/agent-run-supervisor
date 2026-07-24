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
active_plan: docs/plans/active/2026-07-24-official-adapter-run-boundaries.md
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
- **Stage 2 — `arsd` (closed 2026-07-23):** A1–A5 are closed. `arsd` is production/default-on enabled as a local user service for trusted local callers under the closed A2 caller policy. This is an enabled local supervision service only — not a release/publication, Sachima, Gateway/IM, or public-ingress approval. Closure detail lives in the Stage 2 phase archive and the archived execution plan.
- **Runtime invariant:** production `arsd` runs on CPython 3.12.3 — the interpreter that carried A4/A5 acceptance and whose build provides the pidfd APIs the crash-containment harness requires. Standalone Python 3.11.15 lacks those APIs and is not an equivalent runtime.
- **Release/publication:** v0.2.0 predates A1. A follow-on release or publication is not approved.
- **Later integration:** Sachima `ArsdBackend` remains parked; ARS production acceptance is closed, and the integration still requires its own separate approval.

## Phase board

| Phase | State | Active authority | Authorization / exit |
|---|---|---|---|
| vNext authority reset | Documentation complete | GOAL/PRD/design/roadmap + cold archive | no implementation authority |
| Stage 0/1 — Native ACP core | Closed | [phase archive](archive/phases/vnext-stage01-native-acp.md) | production claims require Stage 2 acceptance |
| Stage 2 — `arsd` production ingress (A1–A5) | Closed; production/default-on enabled 2026-07-23 | [phase archive](archive/phases/vnext-stage2-arsd-production-ingress.md) · [archived plan](../plans/archive/2026-07-22-vnext-stage2-arsd-production-ingress.md) | enabled local supervision service under the closed A2 caller policy; release/publication and external integration remain separately unapproved |
| Sachima integration | Parked | boundary only | after ARS production acceptance and separate approval |

## Gates

| Gate | State | Fact |
|---|---|---|
| G9/G10/G11 | Closed by A4 | real S1–S5 socket-path acceptance: cgroup crash containment, real denied-action canary, robustness, and re-proven real credential/model usability; sanitized evidence operator-held |
| G12 caller UID/ownership policy | Closed by A2 | recorded operator policy decision; exact mapping values controller-only, delivered to the daemon only as `--caller-mapping` arguments in the mode-0600 user unit |
| A5 live enablement | Closed 2026-07-23 | enabled+active user unit after exact-main runtime install, production canary, and independent blocker review PASS; sanitized closure evidence operator-held (phase archive) |

G12 closure is a recorded operator decision; the repository intentionally records no production
mapping value. The A1 source/default-closed foundation still permits only the explicitly scoped
source behavior. Optional DLP enhancements remain future work.

## Cold history

- Former mixed authority snapshot: [`docs/archive/pre-vnext-reset-2026-07-21/`](../archive/pre-vnext-reset-2026-07-21/README.md)
- Closed plans: [`docs/plans/archive/`](../plans/archive/README.md)
- Closed phases/tails: [`docs/roadmap/archive/`](archive/README.md)

## Explicit non-approvals

See [`non-approvals.md`](non-approvals.md). This board authorizes nothing by itself: the
A1–A5 closures are recorded operator decisions carried by the Stage 2 archives. A follow-on
release or publication, Sachima/Gateway integration, and public ingress remain separately
unapproved.

## Verification

See [`verification.md`](verification.md) and [`docs/AI_FLOW.md`](../AI_FLOW.md).

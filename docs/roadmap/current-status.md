---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-22
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Living vNext board, not merge history. Cold history is excluded from default agent context.

```text
base_branch: main
active_plan: docs/plans/active/2026-07-21-vnext-stage01-native-acp-implementation.md
```

## Authority chain

```text
GOAL → vNext PRD → vNext architecture → vNext technical solution
     → features + this board → active implementation plan → code
```

The pre-reset mixed authority, v0.1.7 feature/phase ledger, completed plans, and old branch/PR instructions
are archived. They may be read only for audit, compatibility, or user-cited disputes and cannot direct
new work.

## Snapshot

- **Product target:** one local supervision plane: `trusted caller → arsd UDS → ars-core/Native ACP → registered external AGENT`.
- **Released baseline:** v0.1.7 acpx behavior remains compatibility-only; it is not the new-development architecture.
- **Stage 0/1 implementation:** complete as a locally verified candidate on the implementation branch.
  C1–C10 landed with the full L1/L2 suites and real OpenCode 1.18.4 B-grade acceptance: exact
  literal K3/max, real `session/load` historical-token continuity across process-per-Run, and the
  chair-approved exact model-switch closure through the registered second model
  `deepseek/deepseek-v4-pro` (profile revision 2 closed model pair). No Stage 0/1 implementation
  blocker remains; sanitized acceptance evidence lives in the operator-held out-of-Git C10 records.
- **Integration state:** the implemented Stage 0/1 work is complete locally; integration remains
  pending under its separate approvals. Live pull-request/CI/merge truth belongs to GitHub, never
  to this board.
- **Production acceptance:** Stage 2 `arsd` plus G12 and real S1–S5; unimplemented and separately approved.
- **Later integration:** Sachima `ArsdBackend`; parked until ARS production acceptance.

## Phase board

| Phase | State | Active authority | Authorization / exit |
|---|---|---|---|
| vNext authority reset | Documentation complete | GOAL/PRD/design/roadmap + cold archive | no implementation authority |
| Stage 0/1 — Native ACP core | Implementation complete; integration pending | [active plan](../plans/active/2026-07-21-vnext-stage01-native-acp-implementation.md) | exit = separately approved push, pull-request review, and merge; the plan archives at merge |
| Stage 2 — `arsd` production ingress | Planned; no active plan | PRD/architecture/technical solution | separate source + G12 + service/harness approval; S1–S5 |
| Sachima integration | Parked | boundary only | after ARS production acceptance and separate approval |

## Open gates

Stage 0/1 gates G1 and G3–G8 are closed for this candidate: explicit operator authorization,
fresh-baseline start, the additive status-consumer decision with per-consumer pins, live wire and
state proofs at L1/L2, and real B-grade acceptance (exact configuration, `loadSession` plus
historical-token continuity, exact model/effort switching with rollback/quarantine). They re-open
only if the candidate materially changes before merge.

| Gate | Blocks | Required fact |
|---|---|---|
| G9/G10/G11 | Stage 2 production | cgroup containment, real denied-action canary, robustness (Stage 2 re-proves real credential/model usability inside S1–S5) |
| G12 caller UID/ownership policy | Stage 2 production enablement | approved policy owner and exact allowed UID values |

G12 is a required production gate, not optional DLP backlog. Optional DLP enhancements remain future work.

## Cold history

- Former mixed authority snapshot: [`docs/archive/pre-vnext-reset-2026-07-21/`](../archive/pre-vnext-reset-2026-07-21/README.md)
- Closed plans: [`docs/plans/archive/`](../plans/archive/README.md)
- Closed phases/tails: [`docs/roadmap/archive/`](archive/README.md)

## Explicit non-approvals

See [`non-approvals.md`](non-approvals.md). Nothing in this board authorizes source, dependency, push/PR,
merge, service, deployment, release, or external integration work.

## Verification

See [`verification.md`](verification.md), [`docs/AI_FLOW.md`](../AI_FLOW.md), and
`./scripts/verify_local.sh`.

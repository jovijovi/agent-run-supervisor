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
active_plan: none
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
- **Stage 0/1 — merged.** The Native ACP core (C1–C10) is merged into `main` and versioned in the
  0.2.0 source candidate (CHANGELOG 0.2.0 section, synced version metadata). The L1/L2 suites and
  real OpenCode 1.18.4 B-grade acceptance closed the stage: exact literal K3/max, real
  `session/load` historical-token continuity across process-per-Run, and exact switching through
  the registered second model (profile revision 2 closed model pair). Sanitized acceptance
  evidence stays in the operator-held out-of-Git C10 records; closure detail:
  [phase archive](archive/phases/vnext-stage01-native-acp.md).
- **Production acceptance:** Stage 2 `arsd` plus G12 and real S1–S5; unimplemented and separately
  approved. The 0.2.0 source line ships no `arsd`, Native service, or Native CLI production entry.
- **Release/publication:** tag, GitHub Release, and PyPI publication of 0.2.0 are separately
  approved operator actions and have not occurred.
- **Later integration:** Sachima `ArsdBackend`; parked until ARS production acceptance.

## Phase board

| Phase | State | Active authority | Authorization / exit |
|---|---|---|---|
| vNext authority reset | Documentation complete | GOAL/PRD/design/roadmap + cold archive | no implementation authority |
| Stage 0/1 — Native ACP core | Merged into `main`; closed | [archived plan](../plans/archive/2026-07-21-vnext-stage01-native-acp-implementation.md) · [phase archive](archive/phases/vnext-stage01-native-acp.md) | closed at merge; production claims only via Stage 2 |
| Stage 2 — `arsd` production ingress | Planned; no active plan | PRD/architecture/technical solution | separate source + G12 + service/harness approval; S1–S5 |
| Sachima integration | Parked | boundary only | after ARS production acceptance and separate approval |

## Open gates

Stage 0/1 gates (G1, G3–G8) closed with the merged core; closure evidence is summarized in the
[phase archive](archive/phases/vnext-stage01-native-acp.md). Stage 2 gates remain open:

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

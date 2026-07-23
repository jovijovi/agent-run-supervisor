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
active_plan: docs/plans/active/2026-07-22-vnext-stage2-arsd-production-ingress.md
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
  released 0.2.0 line (CHANGELOG 0.2.0 section, synced version metadata). The L1/L2 suites and
  real OpenCode 1.18.4 B-grade acceptance closed the stage: exact literal K3/max, real
  `session/load` historical-token continuity across process-per-Run, and exact switching through
  the registered second model (profile revision 2 closed model pair). Sanitized acceptance
  evidence stays in the operator-held out-of-Git C10 records; closure detail:
  [phase archive](archive/phases/vnext-stage01-native-acp.md).
- **Production acceptance:** Stage 2 `arsd` plus G12 and real S1–S5. A source-grounded active
  plan is registered (see `active_plan`), the T1 sequencing ruling is recorded in it (§3,
  2026-07-22), and A1 source implementation (Slices 1–5 and 6a) is authorized and in progress
  against a zero-default fail-closed caller-policy seam. `arsd` is not yet in `main`; G12
  caller UID policy owner/values (A2), the user-service/cgroup harness (A3), real
  external-AGENT acceptance (A4), production enablement (A5),
  push/PR/merge/release/deployment, and Sachima integration each remain separately approved
  and are not approved today. The released 0.2.0 line ships no `arsd`, Native service, or
  Native CLI production entry.
- **Release/publication:** v0.2.0 is tagged, released on GitHub, and published to PyPI
  (operator-verified 2026-07-22). Any further release/publication remains a separately
  approved operator action.
- **Later integration:** Sachima `ArsdBackend`; parked until ARS production acceptance.

## Phase board

| Phase | State | Active authority | Authorization / exit |
|---|---|---|---|
| vNext authority reset | Documentation complete | GOAL/PRD/design/roadmap + cold archive | no implementation authority |
| Stage 0/1 — Native ACP core | Merged into `main`; closed | [archived plan](../plans/archive/2026-07-21-vnext-stage01-native-acp-implementation.md) · [phase archive](archive/phases/vnext-stage01-native-acp.md) | closed at merge; production claims only via Stage 2 |
| Stage 2 — `arsd` production ingress | A1 source implementation in progress (T1 sequencing ruling recorded 2026-07-22) | [active plan](../plans/active/2026-07-22-vnext-stage2-arsd-production-ingress.md) · PRD/architecture/technical solution | A1 granted; A2/G12 policy owner+values, A3 service/cgroup harness, A4 real S1–S5 acceptance, and A5 production enablement remain separate unapproved gates; no push/PR/merge/release/deployment or Sachima approval |
| Sachima integration | Parked | boundary only | after ARS production acceptance and separate approval |

## Open gates

Stage 0/1 gates (G1, G3–G8) closed with the merged core; closure evidence is summarized in the
[phase archive](archive/phases/vnext-stage01-native-acp.md). Stage 2 gates remain open:

| Gate | Blocks | Required fact |
|---|---|---|
| G9/G10/G11 | Stage 2 production | cgroup containment, real denied-action canary, robustness (Stage 2 re-proves real credential/model usability inside S1–S5) |
| G12 caller UID/ownership policy | real external-AGENT acceptance (A4) and production enablement (A5); per the recorded T1 sequencing ruling (active plan §3, 2026-07-22) it no longer blocks A1 source start | approved policy owner and exact UID→principal/owner-namespace mapping values (A2 — still open) |

G12 is a required gate, not optional DLP backlog. The recorded T1 sequencing ruling (active plan §3,
2026-07-22) lets A1 proceed only against a zero-default fail-closed caller-policy seam with explicit
synthetic test-scoped mappings in hermetic tests; the G12 policy owner/mapping values (A2) still gate
A4 and A5. Optional DLP enhancements remain future work.

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

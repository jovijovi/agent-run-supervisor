---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-21
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
- **Authority reset:** complete in this tree, documentation-only; no source/dependency/runtime capability was implemented.
- **Next separately approvable scope:** Stage 0/1 C1–C10 from the active plan, only after explicit approval and a fresh branch/worktree from live `origin/main`.
- **Production acceptance:** Stage 2 `arsd` plus G12 and real S1–S5; unimplemented and separately approved.
- **Later integration:** Sachima `ArsdBackend`; parked until ARS production acceptance.

## Phase board

| Phase | State | Active authority | Authorization / exit |
|---|---|---|---|
| vNext authority reset | Documentation complete | GOAL/PRD/design/roadmap + cold archive | no implementation authority |
| Stage 0/1 — Native ACP core | Planned | [active plan](../plans/active/2026-07-21-vnext-stage01-native-acp-implementation.md) | C1–C10 require explicit local-implementation approval |
| Stage 2 — `arsd` production ingress | Planned; no active plan | PRD/architecture/technical solution | separate source + G12 + service/harness approval; S1–S5 |
| Sachima integration | Parked | boundary only | after ARS production acceptance and separate approval |

## Open gates

| Gate | Blocks | Required fact |
|---|---|---|
| G1 implementation authorization | C1 onward | explicit scope for local source/dependency work |
| G3 real profile prerequisites | C10/production evidence | usable Kimi Code credentials and required model access |
| G4 fresh baseline | C1 | new branch/worktree at live `origin/main`; source/API/CodeGraph/tests rechecked |
| G5 status/result consumer audit | C2 | safe `unknown` carrier selected without legacy regression |
| G6 real session continuity | Stage 1 completion | same external ID + `session/load` + historical-token proof |
| G7/G8 process/state evidence | Stage 1 completion | live wire ownership, markers, terminal table, isolation, no replay |
| G9/G10/G11 | Stage 2 production | cgroup containment, real denied-action canary, robustness |
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

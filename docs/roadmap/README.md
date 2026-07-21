---
title: "ARS vNext Roadmap Governance"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-21
---
# ARS vNext Roadmap Governance

## Living documents — default context

| Document | Role |
|---|---|
| [`current-status.md`](current-status.md) | vNext phase snapshot, active plan, open gates |
| [`features.md`](features.md) | vNext capability completion plus one legacy-compatibility row |
| [`non-approvals.md`](non-approvals.md) | explicit current non-approvals |
| [`verification.md`](verification.md) | deterministic implementation/PR gates |
| [`../plans/active/`](../plans/active/) | board-linked current executable plan |

## Cold archive — never default context

| Location | Role |
|---|---|
| [`../archive/pre-vnext-reset-2026-07-21/`](../archive/pre-vnext-reset-2026-07-21/README.md) | former mixed authority snapshot |
| [`archive/phases/`](archive/phases/) | closed v0.1.x phase acceptance |
| [`archive/tails.md`](archive/tails.md) | closed historical tail register |
| [`archive/path-migration-2026-07.md`](archive/path-migration-2026-07.md) | historical path migration |
| [`../plans/archive/`](../plans/archive/) | completed/superseded plans |

Archived material is retained for audit, compatibility, and disputes only. It cannot define current
scope, modules, branches, PRs, gates, acceptance, or authorization.

Use living roadmap documents to answer:

- which vNext capability/stage is current;
- what the next separately approvable implementation scope is;
- which proof/governance gates remain;
- which non-approvals remain in force.

Roadmap documents sequence implementation but never shrink GOAL/PRD/design. Task-level plans live only in
`docs/plans/active/`. No roadmap/status wording grants source, runtime, live, publication, or integration
authority.

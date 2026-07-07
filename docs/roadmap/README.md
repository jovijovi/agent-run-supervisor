---
title: "Roadmap governance"
status: active
created_at: 2026-05-28
last_validated_at: 2026-07-07T15:30:00+0800
---
# Roadmap governance

## Living documents (default context)

| Document | Role |
|---|---|
| [`current-status.md`](current-status.md) | Living phase board — snapshot, phase index, open tails |
| [`features.md`](features.md) | Feature/capability completion |
| [`non-approvals.md`](non-approvals.md) | Explicit non-approvals |
| [`verification.md`](verification.md) | Implementation PR verification gates |
| [`../plans/active/`](../plans/active/) | In-progress implementation plans |

## Cold archive (not default context)

| Document | Role |
|---|---|
| [`archive/phases/`](archive/phases/) | Closed phase acceptance records |
| [`archive/tails.md`](archive/tails.md) | Closed tail register |
| [`../plans/archive/`](../plans/archive/) | Completed implementation plans |

Use roadmap documents to answer:

- which features are Done/Partial;
- what engineering phase is current;
- what the next allowed implementation request is;
- what tails remain open;
- which non-approvals remain in force.

Roadmap documents may sequence implementation; they must not shrink PRD/design requirements.

Task-level execution plans belong in `docs/plans/active/` (see [`docs/plans/README.md`](../plans/README.md)).
Do not use roadmap docs to imply live/runtime approval.

Path migration: [`MIGRATION.md`](MIGRATION.md).

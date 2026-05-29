---
title: "Implementation plans directory"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T13:44:07+0800
---
# Implementation plans directory

`docs/plans/` holds **concrete, task- or phase-level implementation plans** for
`agent-run-supervisor`. A plan here is an execution artifact: it says how an
already-approved phase will be built, tested, and verified. It is not a place to
define or change product scope.

## What lives here vs. in `docs/roadmap/`

| Concern | Home |
|---|---|
| Product requirements | `docs/product/prd.md` |
| Technical design | `docs/design/technical-solution.md` |
| Feature/capability completion tracking | `docs/roadmap/features.md` |
| Roadmap, phase status, phase acceptance criteria | `docs/roadmap/current-status.md` |
| Concrete task/phase implementation plans | `docs/plans/` (this directory) |

`docs/roadmap/` owns roadmap, status, and feature tracking. It does **not** own
task-level execution plans. Keep step-by-step build/test plans out of
`docs/roadmap/` and in `docs/plans/`.

## Naming convention

Every plan file is named:

```text
docs/plans/YYYY-MM-DD-<task-slug>.md
```

- `YYYY-MM-DD` — the date the plan was created/approved.
- `<task-slug>` — a short kebab-case task identifier.

Example: `docs/plans/2026-05-29-e1-one-shot-exec-runner.md`.

## Rules for new plans

- A plan must **derive from** `docs/product/prd.md`, `docs/design/technical-solution.md`,
  and `docs/roadmap/current-status.md`. It traces back to an approved roadmap phase.
- A plan **must not redefine product goals**, expand product scope, or weaken the
  PRD/design. If a plan seems to require a scope change, stop and change the PRD/design
  first.
- A plan **must not imply new live/runtime approvals**. Authoring or moving a plan
  does not approve Sachima behavior integration, real AGENT automatic replies, public
  ingress, real IM delivery, Gateway lifecycle operations, production config writes,
  live/default-on behavior, worker auto-routing, `@all`, agent-to-agent automatic
  routing, trusted Markdown/HTML rendering, treating `allowed_roots` as an OS sandbox,
  or per-run human approval as the default authorization model.
- Per `docs/AI_FLOW.md`, a plan should include: context and exact target from
  PRD/design/roadmap; an implementation-goal checklist; acceptance criteria; files
  likely to change; verification gates; risks/open questions; and a rollback strategy.

## Historical artifacts are non-authoritative

The pre-realignment `docs/plans/` and `docs/dev_log/` artifacts were retired and
cleared during the R0 documentation authority realignment (PR #6, `7dcbe4f`). Do not
resurrect or cite them as source-of-truth. Only fresh plans that trace to the current
authority chain belong here.

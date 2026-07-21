---
title: "Implementation plans directory"
status: active
created_at: 2026-05-29
last_validated_at: 2026-07-21
---
# Implementation plans directory

`docs/plans/` holds **concrete, task- or phase-level implementation plans**.
A plan is an execution artifact: it says how an already-approved phase will be
built, tested, and verified. It does not define or change product scope.

## Layout

```text
docs/plans/
├── README.md       # this file
├── active/         # in-progress plans (default agent context)
└── archive/        # completed plans (cold archive)
```

**Do not** place `*.md` plan files in the `docs/plans/` root (only this README).

## What lives here vs. in `docs/roadmap/`

| Concern | Home |
|---|---|
| Product requirements | `docs/product/prd.md` |
| Technical design | `docs/design/technical-solution.md` |
| Feature/capability completion | `docs/roadmap/features.md` |
| Living phase board, open tails | `docs/roadmap/current-status.md` |
| Closed phase acceptance summaries | `docs/roadmap/archive/phases/` |
| Explicit non-approvals | `docs/roadmap/non-approvals.md` |
| Verification gates | `docs/roadmap/verification.md` |
| **Active** implementation plans | `docs/plans/active/` |
| **Archived** implementation plans | `docs/plans/archive/` |

## Lifecycle

| Stage | Action |
|---|---|
| **Start** | Create `docs/plans/active/YYYY-MM-DD-<slug>.md` with `status: active`; link from board `active_plan:` |
| **In progress** | Keep the plan in `active/`; update checklists with the implementing PR |
| **Merged / closed** | `git mv` to `docs/plans/archive/`; set `status: archived` and `archived_at`; update board and `docs/roadmap/archive/phases/` |

## Naming convention

```text
docs/plans/active/YYYY-MM-DD-<task-slug>.md
docs/plans/archive/YYYY-MM-DD-<task-slug>.md
```

Example: `docs/plans/active/2026-05-29-e1-one-shot-exec-runner.md`.

## Agent context discipline

- Implementation preflight reads **`docs/plans/active/`** only (plus board link).
- **`docs/plans/archive/`** and **`docs/roadmap/archive/`** are historical; read
  only when auditing, disputing evidence, or when the user cites a path.

## Rules for new plans

- Derive from `docs/product/prd.md`, `docs/design/technical-solution.md`, and
  `docs/roadmap/current-status.md`.
- Must **not** redefine product goals, expand scope, or imply new live/runtime
  approvals.
- Include: context/target, checklist, acceptance, files likely to change,
  verification gates, risks, rollback (per `docs/AI_FLOW.md`).

## Historical artifacts

Everything under `docs/plans/archive/` is cold history. It may preserve completed checklists, superseded
branches/PRs, baselines, and acceptance language, but none of those values are current. Archived plans
cannot define new scope, select a branch, approve work, or override the vNext authority chain.

Only the board-linked plan in `docs/plans/active/` is implementation context. It must start from live
`origin/main` and use a new task branch/worktree.

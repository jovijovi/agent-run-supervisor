---
title: "Active implementation plans"
status: active
created_at: 2026-07-07
last_validated_at: 2026-07-21
---
# Active implementation plans

Plans in this directory are **current executable planning context** but never authorization by their
existence. The living board links exactly one current plan. Start implementation from live `origin/main`
on a new task branch/worktree after the plan's explicit approval and Definition of Ready pass.

- Create new plans here: `docs/plans/active/YYYY-MM-DD-<task-slug>.md`
- When work merges, `git mv` the file to `docs/plans/archive/` and set
  `status: archived` with `archived_at`.
- The living board links the current plan via `active_plan:` in
  [`docs/roadmap/current-status.md`](../../roadmap/current-status.md).

Do **not** place completed plans here. See [`../README.md`](../README.md).

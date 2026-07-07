---
title: "R0 — Documentation authority realignment"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: r0-doc-authority
---

# R0 — Documentation authority realignment

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## R0 — Documentation authority realignment

Goal: make documentation the product authority, delete obsolete mixed/stale docs, and prevent old plan/dev-log artifacts from driving future work.

Checklist:

- [x] Simplify `GOAL.md` into stable product positioning and source-of-truth index.
- [x] Move product requirements into `docs/product/prd.md`.
- [x] Move technical/architecture/session/runner design into `docs/design/technical-solution.md`.
- [x] Add feature completion management at `docs/roadmap/features.md`.
- [x] Merge standalone implementation-plan content into this roadmap/status document.
- [x] Remove the retired mixed V0.1a design file.
- [x] Remove the stale V0.1c manual-approval design file.
- [x] Clear obsolete `docs/dev_log/` files.
- [x] Clear obsolete `docs/plans/archive/` files.
- [x] Rebuild `docs/INDEX.md` and `docs/lessons/_drift_report.md`.
- [x] Merge documentation authority realignment via PR #6 (`7dcbe4f`) and verify main CI.

Acceptance:

- Docs index/drift gates pass.
- No active doc names deleted design/dev-log/plan files as source-of-truth.
- PRD/DESIGN/GOAL do not reduce the product to exec-only.
- Roadmap clearly sequences exec before persistent sessions as engineering order only.

Status: **Complete on main via PR #6 (`7dcbe4f`); main `Verify` CI passed and post-merge local gates passed.**

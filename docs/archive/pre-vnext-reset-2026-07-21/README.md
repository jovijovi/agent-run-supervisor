---
title: "Pre-vNext-reset authority archive"
status: archived
created_at: 2026-07-21
archived_at: 2026-07-21
deprecated_reason: "Preserved historical authority; not valid for new development"
---
# Pre-vNext-reset authority archive

This directory preserves the mixed v0.1.7 + vNext authority set that existed immediately after PR #65.
It is **cold history**, not an alternative source of truth.

## Use only for

- audit and provenance;
- compatibility questions about the released v0.1.7 acpx paths;
- dispute resolution when comparing the authority reset with the previous tree.

## Never use for

- new implementation scope, module design, task sequencing, acceptance, authorization, or production claims;
- resurrecting acpx as the Native ACP production driver or fallback;
- bypassing the current active plan or living non-approvals.

Current authority starts at repository-root `GOAL.md`, then PRD → architecture → technical solution →
features/living board → `docs/plans/active/`.

The preserved snapshots are `GOAL.md`, `prd.md`, `architecture.md`, `technical-solution.md`,
`features.md`, `current-status.md`, and `non-approvals.md`.

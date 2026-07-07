---
title: "C0 — acpx contract fixtures and validator"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: c0-acpx-fixtures
---

# C0 — acpx contract fixtures and validator

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## C0 — acpx contract fixtures and validator

Goal: preserve the observed acpx contract that implementation and tests rely on.

Checklist:

- [x] Capture real `acpx@0.12.0` fixtures.
- [x] Capture command grammar and observed stdout schema for current fixture family.
- [x] Validate fixture naming, schema markers, exit semantics, and secret-shaped content.
- [x] Record that `allowed_roots` is cwd/config evidence only, not a sandbox.

Acceptance:

- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0` passes.

Status: **Complete**.

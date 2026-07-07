---
title: "F0 — Role/policy/parser/store foundation"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: f0-foundation
---

# F0 — Role/policy/parser/store foundation

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## F0 — Role/policy/parser/store foundation

Goal: provide the reusable core supervisor foundation without relying on a true subprocess/session launch.

Checklist:

- [x] `AgentRoleSpec` model and validation.
- [x] role hash.
- [x] permission policy compiler.
- [x] acpx argv compiler for current run shape.
- [x] cwd/allowed-roots gate.
- [x] exit classifier.
- [x] observed stdout parser.
- [x] EventStore permissions/atomic writes.
- [x] redaction helpers.
- [x] CLI `validate-role`, `replay`, `doctor` baseline.
- [x] CLI `run --no-real-run` artifact compilation.
- [x] Pre-E1 real-run refusal stayed stable until the E1 runner replaced it.

Acceptance:

- pytest passes.
- compileall passes.
- CLI doctor/replay smoke passes.
- Pre-E1 real-run path refused without launching a process until E1 replaced that refusal with supervised local exec.

Status: **Complete as foundation; not product-complete**.

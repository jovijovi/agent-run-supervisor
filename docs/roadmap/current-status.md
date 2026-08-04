---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-04
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Lean vNext task-status board. It records current scope and open gates, not release, deployment, runtime,
> commit, pull-request, or CI history.

```text
base_branch: main
active_plan: none
```

## Current position

- The V4 external-AGENT boundary reset and its relevant refinements are implemented on `main`; the
  [feature tracker](features.md) records their current capability state.
- Cursor cross-Run Session resume is closed on `main`: `cursor-native-acp-v1` uses model-only fidelity and
  no registered profile selects per-Run launch-permission material, preserving the AGENT-owned Session state
  needed for real `session/load` continuity.
- There is no active implementation plan.
- The legacy acpx source remains without product, runtime, or compatibility authority; its removal is
  separately authorized work.

## Open decisions and gates

None is approved by this board.

- **Decision 3 — lifetime of the pre-reset line.** Open operator decision.
- **Sachima `ArsdBackend` integration.** Parked; requires separate approval and evidence.
- **acpx product, runtime, and compatibility removal.** Planned; requires separate source and documentation authorization.
- **Per-agent and operational gates.** The denied-action canary remains required before a registered agent's
  use; release, publication, deployment, service, migration, and runtime actions each require separate
  authorization.

## Boundaries

See [`non-approvals.md`](non-approvals.md). This board authorizes no operational action, integration, or
acpx-removal work.

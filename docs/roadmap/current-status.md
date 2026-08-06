---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-06
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Lean vNext task-status board. It records current scope and open gates, not release, deployment, runtime,
> commit, pull-request, or CI history.

```text
base_branch: main
active_plan: docs/plans/active/2026-08-06-session-no-close-model.md
```

## Current position

- The V4 external-AGENT boundary reset and its relevant refinements are implemented on `main`; the
  [feature tracker](features.md) records their current capability state.
- Cursor cross-Run Session resume is closed on `main`: `cursor-native-acp-v1` uses model-only fidelity and
  no registered profile selects per-Run launch-permission material, preserving the AGENT-owned Session state
  needed for real `session/load` continuity.
- The [Session no-close model plan](../plans/active/2026-08-06-session-no-close-model.md) is the active
  implementation context. Runs terminate while Sessions remain durable and resumable: there is one Session
  kind, no `session_close`, no one-shot or ephemeral Session, and no normal Session terminal state.
  Quarantine is independent safety evidence, and `api_version` 3 is the sole caller wire.
- Authority and source are being brought onto that model together in one candidate. Source implementation
  authorizes source, tests, and docs only — it authorizes no runtime-data reset, service restart, release,
  deployment, real-agent canary, push/PR/merge, or caller integration.
- The legacy acpx source remains without product, runtime, or compatibility authority; its removal is
  separately authorized work. The no-close change deletes acpx Session-close and Session-lifetime
  classification only, and adds no acpx capability.

## Open decisions and gates

None is approved by this board.

- **Session no-close runtime cutover.** Not approved. Because the change deliberately adds no dual-schema
  compatibility, cutover must be one operator-controlled action: the v3 package, the repository caller, the
  operator-selected development-data reset, and the acceptance procedure together. Archiving or rebuilding
  development Run/Session state, restarting `arsd`, and real Claude/Codex canaries each stay separate.
- **Decision 3 — lifetime of the pre-reset line.** Open operator decision.
- **Sachima `ArsdBackend` integration.** Parked; requires separate approval and evidence.
- **acpx product, runtime, and compatibility removal.** Planned; requires separate source and documentation authorization.
- **Per-agent and operational gates.** The denied-action canary remains required before a registered agent's
  use; release, publication, deployment, service, migration, and runtime actions each require separate
  authorization.

## Boundaries

See [`non-approvals.md`](non-approvals.md). This board authorizes no operational action, integration, or
acpx-removal work.

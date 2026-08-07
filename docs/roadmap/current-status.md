---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-07
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Lean vNext task-status board. It records current scope and open gates, not release, deployment, runtime,
> commit, pull-request, or CI history.

```text
base_branch: main
active_plan: docs/plans/active/2026-08-07-cursor-grant-mode.md
```

## Current position

- The V4 external-AGENT boundary reset and its relevant refinements are implemented on `main`; the
  [feature tracker](features.md) records their current capability state.
- Cursor cross-Run Session resume is closed on `main`: `cursor-native-acp-v1` uses model-only fidelity and
  no registered profile selects per-Run launch-permission material, preserving the AGENT-owned Session state
  needed for real `session/load` continuity.
- Grant-driven Cursor permission mode is implemented on the task branch `fix/cursor-grant-mode`
  (uncommitted; F-CURSOR-GRANT-MODE-001): `cursor-native-acp-v1` revision 3 requires Cursor ACP mode `ask`
  when the Run's frozen grant is exactly a subset of `{read, search}` and `agent` for every other valid
  grant, set and exact-read-back before the model, re-proven after the model set, failing pre-Prompt as
  `CONFIG_FIDELITY` otherwise, recomputed on every Run including real `session/load` reuse. `ask` is a
  cooperative mode mitigation, not a permission/sandbox guarantee; mediation and the completion backstop
  are unchanged. Source, tests, and docs only: commit, push, PR, merge, release, deployment, restart, and
  any real Cursor canary each remain separately unapproved.
- The Session no-close model is closed on `main`: Runs terminate while Sessions remain durable and
  resumable, with one Session kind, no one-shot or ephemeral Session, and no normal Session terminal state.
  Quarantine is independent safety evidence, and `api_version` 3 is the sole caller wire.
- Authority and source are aligned on `main` for that model. Source implementation authorizes source,
  tests, and docs only — it authorizes no runtime-data reset, service restart, release, deployment,
  real-agent canary, push/PR/merge, or caller integration.
- The retired acpx path was removed from source. That removal took the runtime and its package modules, the
  CLI leaves, the fixtures, and the API v3 process-exit result field. The audited keep set was empty, so no
  fixture was retained. One production architecture remains, `arsd` + ars-core + Native ACP, and a
  containment scanner plus exact wheel/sdist manifest gates refuse a second one. API v3 is the only
  contract: a persisted terminal carrying an undefined key is untrusted evidence, and nothing migrates or
  rewrites a stored record. The removal is merged on `main`. Source only: no runtime-data change, service
  action, release, or deployment.

## Open decisions and gates

None is approved by this board.

- **Session no-close runtime cutover.** Not approved. Because the change deliberately adds no dual-schema
  compatibility, cutover must be one operator-controlled action: the v3 package, the repository caller, the
  operator-selected development-data reset, and the acceptance procedure together. Archiving or rebuilding
  development Run/Session state, restarting `arsd`, and real Claude/Codex canaries each stay separate.
- **Decision 3 — lifetime of the pre-reset line.** Open operator decision.
- **Sachima `ArsdBackend` integration.** Parked; requires separate approval and evidence.
- **Removal landing.** Source, tests, and docs are merged on `main`. Release, publication, deployment, and any runtime-data decision each stay separate and unapproved.
- **Per-agent and operational gates.** The denied-action canary remains required before a registered agent's
  use; release, publication, deployment, service, migration, and runtime actions each require separate
  authorization.

## Boundaries

See [`non-approvals.md`](non-approvals.md). This board authorizes no operational action or integration.

---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-09
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Lean vNext task-status board. It records current scope and open gates, not release, deployment, runtime,
> commit, pull-request, or CI history. Publication and runtime truth belong to live release and operator
> sources; any future operational action requires separate authorization.

```text
base_branch: main
active_plan: docs/plans/active/2026-08-09-long-run-timeout-limits.md
```

## Current position

- The long-Run timeout-limit source task remains active and unmerged on `feat/long-run-timeout-limits`:
  the locally implemented candidate changes the omitted per-Run turn timeout to 21,600 seconds and the inclusive ceiling to
  604,800 seconds while preserving hard Run timeout and Session lifecycle semantics. It carries no release,
  publication, deployment, service, migration, cutover, real-provider, or caller-integration claim.
- The V4 external-AGENT boundary reset and its relevant refinements are implemented on `main`; the
  [feature tracker](features.md) records their current capability state.
- Cursor cross-Run Session resume is closed on `main`: `cursor-native-acp-v1` uses model-only fidelity and
  no registered profile selects per-Run launch-permission material, preserving the AGENT-owned Session state
  needed for real `session/load` continuity.
- Grant-driven Cursor permission mode is closed on `main` (F-CURSOR-GRANT-MODE-001):
  `cursor-native-acp-v1` revision 3 requires Cursor ACP mode `ask`
  when the Run's frozen grant is exactly a subset of `{read, search}` and `agent` for every other valid
  grant, set and exact-read-back before the model, re-proven after the model set, failing pre-Prompt as
  `CONFIG_FIDELITY` otherwise, recomputed on every Run including real `session/load` reuse. `ask` is a
  cooperative mode mitigation, not a permission/sandbox guarantee; mediation and the completion backstop
  are unchanged.
- The Session no-close model is closed on `main`: Runs terminate while Sessions remain durable and
  resumable, with one Session kind, no one-shot or ephemeral Session, and no normal Session terminal state.
  Quarantine is independent safety evidence, and `api_version` 3 is the sole caller wire. Session
  durability and indefinite resumability are deliberate product features, not debt.
- No Session no-close source or cutover task remains open on this board, and the pre-reset-line lifetime
  decision is no longer open: historical state and evidence remain preserved, and no old runtime line is
  active. Live publication and runtime truth are external to this board.
- The retired acpx path was removed from source. That removal took the runtime and its package modules, the
  CLI leaves, the fixtures, and the API v3 process-exit result field. The audited keep set was empty, so no
  fixture was retained. One production architecture remains, `arsd` + ars-core + Native ACP, and a
  containment scanner plus exact wheel/sdist manifest gates refuse a second one. API v3 is the only
  contract: a persisted terminal carrying an undefined key is untrusted evidence, and nothing migrates or
  rewrites a stored record. The removal is merged on `main`; no removal-landing task remains open on this
  board.

## Open decisions and gates

None is approved by this board.

- **Sachima `ArsdBackend` integration.** Parked; requires separate approval and evidence.
- **Long-Run timeout candidate.** Local implementation and gates do not close the active task: the
  candidate remains unmerged under its active plan, and merge plus every downstream side effect remain
  separately unapproved.
- **Per-agent and operational gates.** The denied-action canary remains required before a registered agent's
  use; any future release, publication, deployment, service, migration, or runtime action requires separate
  authorization.

## Boundaries

See [`non-approvals.md`](non-approvals.md). This board authorizes no operational action or integration.

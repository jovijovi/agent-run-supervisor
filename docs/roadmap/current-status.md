---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-10
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Lean vNext task-status board. It records current scope and open gates, not release, deployment, runtime,
> commit, pull-request, or CI history. Publication and runtime truth belong to live release and operator
> sources; any future operational action requires separate authorization.

```text
base_branch: main
active_plan: none
```

## Current position

- The long-Run timeout-limit source task is closed on `main`: omitting the per-Run turn timeout now seals
  21,600 seconds, with an inclusive 604,800-second ceiling, while preserving hard Run timeout and Session
  lifecycle semantics. The merged source carries no release, publication, deployment, service, migration,
  cutover, real-provider, or caller-integration claim.
- Session reuse under AGENT history replay is closed on `main`
  (F-SESSION-REPLAY-BACKPRESSURE-001). A third-party adapter's `session/load` conversation replay is
  identity-validated and then separated from the current Run: it produces no per-event execution evidence,
  no permission accounting, no tool-call closure, and no `final_message`, and is retained as one bounded
  `session_replay_summary`. The per-Run evidence path is now one event-loop-owned Bounded Serial Ledger: it
  allocates the actual sequence and canonical NDJSON bytes at acceptance, holds in-flight count/bytes through
  durable `append_text` acknowledgement, separates admission from persistence outcomes, and applies FIFO
  absolute producer deadlines before room or growth. Its policy rungs remain 1024→8192 events and 8→64 MiB,
  expanding only for a live consumer making durable progress. Observer cancellation cannot mutate accepted
  evidence; failure is absorbing and ordinal-ranked; close synchronously cuts off producers, joins the
  consumer, and succeeds only after every accepted event is durably acknowledged. A stalled or failed sink
  still reaches bounded `EVIDENCE_PIPELINE`. This is callback/evidence-layer backpressure only: the locked ACP
  SDK dispatches one task per notification and applies no transport backpressure. No wire, API,
  schema-version, or Session lifecycle change; no release, deployment, or publication claim.
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
- **Per-agent and operational gates.** The denied-action canary remains required before a registered agent's
  use; any future release, publication, deployment, service, migration, or runtime action requires separate
  authorization.

## Boundaries

See [`non-approvals.md`](non-approvals.md). This board authorizes no operational action or integration.

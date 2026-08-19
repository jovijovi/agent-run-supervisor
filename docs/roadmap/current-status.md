---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-11
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Lean vNext task-status board. It records current scope and open gates, not release, deployment, runtime,
> commit, pull-request, or CI history. Publication and runtime truth belong to live release and operator
> sources; any future operational action requires separate authorization.

```text
base_branch: main
active_plan: docs/plans/active/2026-08-19-acp-sdk-0121-upgrade.md
```

## Current position

- OMP and Reasonix minimal source support is closed on `main`
  (F-OMP-REASONIX-SOURCE-001). The merged source adds the Reasonix
  `tool_approval=ask` compatibility profile, operator examples, and canonical
  workspace regression coverage. OMP 17.2.12 remains fail-closed for mutation
  families that supplied no mappable permission request in the isolated
  canaries. This is source and operator-example support only; publication,
  deployment, registry or service mutation, and live activation remain
  separate operator decisions.
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
- The per-Run event-ledger admission budget is operator-configurable in a feature-branch candidate pending
  integration to `main` (F-RUN-EVENT-BUDGET-CONFIG-001); it is not on `main`, released, or deployed.
  `arsd --max-run-event-budget-bytes` sets one ceiling on `max_event_bytes * max_events` for every Run that
  daemon accepts, defaulting to 4 GiB and reported by `server_info`. The effective ceiling is also written
  into each accepted Run's durable admission record and strictly validated there, so which policy admitted a
  historical Run stays auditable after a reconfigure or restart; that record's material change moves
  `SUBMISSION_SCHEMA_VERSION` to 4 alone. Admission policy governs new work only: an already-accepted key
  resolves from strictly validated durable facts whatever the current ceiling is, and never dispatches
  twice. It is a theoretical per-Run event-ledger ceiling, not preallocated memory, a Run-directory disk
  quota, or a daemon-global aggregate. The individual hard limits and every wire, request-digest, Spec, and
  launch schema version are unchanged. Source, tests, and docs only. Its plan stays active in
  [`docs/plans/active/`](../plans/active/2026-08-11-configurable-run-event-budget.md); the board's
  `active_plan` link now names the current task and does not retire that candidate.
- **Current task:** the optional Native ACP SDK pin moves to `agent-client-protocol==0.12.1`, a
  task-branch candidate that is not on `main`, released, or deployed
  ([plan](../plans/active/2026-08-19-acp-sdk-0121-upgrade.md)). Upstream 0.12.1 removed the connection's
  injectable `sender_factory`, so the driver now assembles the SDK's own `MessageSender` and
  `NdjsonTransport` around its existing pre-write tap and hands that transport to the client connection
  through the message-level `Transport` seam. ARS reimplements no framing, and the prompt causal boundary
  keeps its exact instant: it is snapshotted in the sender loop immediately before the `session/prompt`
  bytes reach the real writer. One prompt per driver/connection, replay/current-turn ordinal separation,
  the SDK-dispatch-gated observer count, the pre-response delivery barrier, callback-error surfacing,
  process supervision/reap, and Session load-only semantics are all unchanged. 0.12.1 also creates a
  notification's handler task synchronously in the receive loop, so a deliverable `session/update` batched
  ahead of a response now reaches its callback before that response resolves; ARS's existing fail-closed
  identity rule covers it and gains a regression. No wire, API, schema-version, Session lifecycle, or
  terminal-vocabulary change. Source, tests, and docs only.
- The previous ARS-owned permission boundary fixes are merged on `main`. A `read`/`search`
  `session/request_permission` is allow-eligible only when the frozen grant includes `read` and every
  protocol-declared `locations[].path` is absolute and canonically inside the bound workspace — missing,
  malformed, relative, mixed, traversal, and symlink-escape locations deny fail-closed, and no other field is
  path authority. A tool call that reports `completed` after ARS denied that same call now emits
  `permission_violation` with `violation_class=completed_after_deny` and reuses the existing
  `failed` / `PERMISSION_VIOLATION` / non-retryable finalization with the Session still reusable; a denied
  call that reports `failed` stays healthy. Mediation remains cooperative — this is neither filesystem
  isolation nor a claim that the side effect was prevented — and it repairs no external AGENT or adapter.
  No wire, API, schema-version, Session lifecycle, or terminal-vocabulary change. Source, tests, and docs
  only.

## Open decisions and gates

None is approved by this board.

- **Sachima `ArsdBackend` integration.** Parked; requires separate approval and evidence.
- **Per-agent and operational gates.** The denied-action canary remains required before a registered agent's
  use; any future release, publication, deployment, service, migration, or runtime action requires separate
  authorization.
- **OMP 17.2.12 mutation compatibility.** The required `always-ask` source
  example stays fail-closed, but this installed version did not expose a usable
  once-scoped mutation path to ARS. A future support expansion requires new
  Agent evidence; permissive/yolo operation is not an approved fallback.

## Boundaries

See [`non-approvals.md`](non-approvals.md). This board authorizes no operational action or integration.

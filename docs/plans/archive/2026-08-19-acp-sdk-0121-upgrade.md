---
title: "ACP Python SDK 0.12.1 upgrade"
status: archived
created_at: 2026-08-19
last_validated_at: 2026-08-19
archived_at: 2026-08-19
---
# ACP Python SDK 0.12.1 upgrade

## Context and target

The optional `native` extra pins `agent-client-protocol==0.12.0`. Upstream 0.12.1 is a patch release whose
two source-relevant entries are:

- `refactor(connection): simplify connection handling and remove unused components` (upstream PR 132)
- `fix(connection): preserve notification response ordering` (upstream PR 129)

PR 132 deletes the `queue`, `state_store`, `dispatcher_factory`, and **`sender_factory`** constructor
keywords from `acp.connection.Connection`, and reduces `acp.task` to `MessageSender` + `TaskSupervisor`
(`DefaultMessageDispatcher`, `InMemoryMessageQueue`, `InMemoryMessageStateStore`, `RpcTask`, `RpcTaskKind`,
`SenderFactory`, and the runner protocols are gone). `Connection._process_message` is now synchronous and
creates one handler task per frame directly.

`NativeAcpDriver._sender_factory` injects a pre-write tap through exactly that removed `sender_factory`
seam, so a dependency-only bump raises
`TypeError: Connection.__init__() got an unexpected keyword argument 'sender_factory'` inside
`driver.open()` and fails the contract, ordinal, and delivery suites. The upgrade is therefore a source
task, not a lockfile task.

PR 129 adds `acp.client.connection._SessionUpdateTracker`: `ClientSideConnection.prompt()` now awaits the
session's already-started `session_update` handlers before returning. That is a partial, session-scoped
version of ARS's own pre-response delivery barrier and does not replace it — a notification task the
receive loop created but has not yet stepped is not registered with the tracker when the prompt response
resolves, so only ARS's observed-versus-completed ordinal barrier covers it.

## Non-goals

- No product, wire, API, `SUBMISSION_SCHEMA_VERSION`, Spec, launch-schema, Session-lifecycle, terminal
  vocabulary, result-key, profile, registry, or permission-policy change.
- No NDJSON framing owned by ARS. The upgrade reuses the SDK's own `NdjsonTransport` and `MessageSender`;
  it does not copy, fork, or reimplement byte framing, buffer-limit handling, or receive-timeout semantics.
- No HTTP/WS transport, remote transport, or `Transport` capability beyond the stdio path ARS already has.
  The SDK's `http` extra stays uninstalled and unresolvable.
- No new hardening, no expansion of the permission/mediation surface, and no security scope beyond keeping
  the existing boundaries exactly as they behave on 0.12.0.
- No release, tag, publication, deployment, service action, or Git/GitHub side effect. Source, tests, and
  documentation only.

## Contract

- The `native` extra, the lockfile, and the SDK contract suite pin exactly `agent-client-protocol==0.12.1`,
  resolved from this worktree's `.venv`, with the SDK's `http`/`ws` extra distributions still unresolvable
  and their modules unimported.
- The prompt causal boundary keeps its exact 0.12.0 semantics: `prompt_wire_boundary` is snapshotted
  **synchronously in the sender loop, immediately before the `session/prompt` bytes are handed to the real
  `StreamWriter.write()`, with no await in between**. A `session/update` observed after the prompt send was
  requested but before those bytes are written is pre-prompt and stays at or below the boundary.
- One prompt per driver/connection; the write-once boundary and its fail-closed
  "prompt response arrived but the prompt wire boundary was never snapshotted" guard are unchanged.
- Replay/current-turn ordinal separation, the SDK-dispatch-gated observer count
  (`_update_frame_will_dispatch`), the pre-response delivery barrier over deliverable frames, and
  `UpdateCallbackError` surfacing are unchanged.
- Process supervision/reap and Session load-only semantics are untouched: the driver still never spawns,
  and `close()` still shuts down SDK connection tasks and never the process.
- Dead 0.12.0-only imports and hooks (`_sender_factory`, the `sender_factory` connection keyword, the
  `acp.task` dispatcher/queue/state symbols) are removed rather than shimmed.

## Approach

1. Write this plan; align `docs/roadmap/features.md`, `docs/design/technical-solution.md`,
   `docs/roadmap/current-status.md`, `README*.md`, and `website/docs/overview.md` only where the SDK
   version or the removed mechanism is currently asserted.
2. **RED** — add `tests/native_acp/test_prompt_wire_boundary.py`: two focused causal-ordering pins over a
   real SDK connection on a socketpair wire, asserting the boundary exists and is snapshotted at the
   prompt's pre-write instant. Both fail on 0.12.1 with production code unchanged.
3. Replace the removed seam with 0.12.1's message-level `Transport` injection point: `driver.open()` builds
   the SDK's own `MessageSender` over the existing `_PromptBoundaryWriter` pre-write tap, wraps it in the
   SDK's own `NdjsonTransport`, and hands that transport to `ClientSideConnection` (which accepts a
   `Transport` as `input_stream` when `output_stream` is omitted). The driver owns one `TaskSupervisor` for
   the sender loop and shuts it down on `close()`.
4. Re-pin the SDK contract suite on the 0.12.1 surface: `observers` keyword-only, `sender_factory` proven
   **absent**, `Transport`-accepting `ClientSideConnection`, `NdjsonTransport`/`MessageSender` present, and
   the new `_SessionUpdateTracker` notification-before-response behavior.
5. Rewrite the removed-dispatcher ordinal pin against 0.12.1's receive loop, and re-point the outgoing
   frame capture helper in `test_session_start_plan.py` at the new construction seam.
6. **GREEN** — focused suites, then the complete `make verify` gate, then `codegraph sync` and impact
   review for the changed central symbols.

## Verification

- Focused: `tests/native_acp/test_prompt_wire_boundary.py`, `test_sdk_contract.py`,
  `test_update_delivery_contract.py`, `test_session_switching.py`, `test_session_start_plan.py`,
  `test_sdk_handler_logging_containment.py`, `test_run_task.py`.
- Canonical: `make verify` (the only completion evidence).

## Boundaries

This plan is closed and archived: the source it describes is merged on `main`, and the plan is retained as
cold history only. It is no longer the board-linked planning artifact, and an archived plan authorizes
strictly less than an active one — nothing. It authorizes no further integration, commit, push, pull
request, merge, tag, release, publication, deployment, service action, or registry/runtime mutation.

---
title: "Official-adapter shared blocker repair — B1/B2 run boundaries"
status: active
created_at: 2026-07-24
last_validated_at: 2026-07-24
---
# Official-adapter shared blocker repair — B1/B2 run boundaries

## Context / target

Post-acceptance bugfix to the merged Native ACP/arsd implementation on
`origin/main@01165e2`. The 2026-07-24 official-adapter validation (sanitized
report held by the operator) found two ARS-shared blockers that violate the
existing authority chain; both official TypeScript adapters and the current
fake-agent path share the same seam:

- **B1 — `session/load` history pollutes the current Run `final_message`.**
  Both adapters replay conversation history as `agent_message_chunk`
  session updates before `session/prompt`; `RunTask._update_sink` accumulates
  every chunk from load onward, so the current Run's `final_message` is
  prefixed with historical assistant output. PRD R9 scopes the runtime ledger
  to *this* Run's supervision facts; the replay must stay available as
  historical event evidence but only assistant chunks causally belonging to
  the current prompt/Turn may contribute to the current Run result.
- **B2 — post-dispatch supervisor timeout is exposed as
  `timed_out`/`retryable=true`.** When a dispatched prompt hangs and the
  supervisor must terminate/escalate with no trustworthy ACP terminal, the
  internal state already quarantines the Session, but the public Run result
  says `timed_out` with `retryable=true` — inviting a retry that is then
  refused. GOAL contract 5 / PRD R5 / architecture §5 / design §6 require the
  dispatched-no-trustworthy-terminal row to end
  `Run=unknown`, `Session=quarantined`, `retryable=false`.

Scope is **B1/B2 only** (approved first slice). B3/B4/B5, profile
registration, dependency/lockfile/version changes, acpx integration, and all
release/deployment/Git side effects are out of scope; explicit non-approvals
in `docs/roadmap/non-approvals.md` hold.

## Checklist

- [x] Preflight: GOAL, PRD, architecture, technical solution, features,
      current-status, AI_FLOW, verification read; position stated.
- [x] RED B1: fake-agent `replay_on_load` fixture + load-path regression
      proving replay chunks do not enter `final_message`, replay normalized
      events stay in `events.jsonl`, and post-prompt accumulation stays
      intact/untruncated.
- [x] RED B2: finalization-table row + hermetic vertical proving
      dispatched turn + supervisor timeout + escalated kill + no ACP terminal
      => `unknown`/`quarantined`/`retryable=false`, and same-Session reuse
      after that quarantine is refused before spawn/prompt.
- [x] Implement B1: causal prompt-wire boundary in `NativeAcpDriver`
      (wire-ordered update count snapshotted by a sender-side pre-write tap
      immediately before the `session/prompt` bytes reach the real
      `StreamWriter.write()`) + delivery-ordinal gate in
      `RunTask._update_sink`; normalization/evidence path unchanged.
- [x] Implement B2: `_post_dispatch_finalize` escalated-kill+timeout row
      returns `UNKNOWN` (quarantine unchanged); stable `TURN_TIMEOUT`
      detail code for the unknown-timeout terminal.
- [x] Update the three tests that pinned the pre-fix behavior
      (finalization-table timeout row, evidence-pipeline-over-timeout row,
      hang+overflow+timeout vertical) to the corrected semantics.
- [x] GREEN: focused suites, four-suite baseline, canonical verifier,
      docs index/drift/governance checks, `git diff --check`.
- [x] Docs: this plan; board `active_plan:` pointer; regenerate
      `docs/INDEX.md` and drift evidence via repository tooling.
- [x] RED (focused-review B1-HAPPENS-BEFORE): deterministic forced-race
      regression holding the SDK request coroutine at its post-write
      suspension point (`MessageSender.send` return) until the receive loop
      has processed a current-turn update — the post-send outgoing-observer
      boundary then dropped that chunk from `final_message`.
- [x] Implement happens-before repair: boundary snapshot moved to a
      `sender_factory` writer tap that runs synchronously immediately before
      the real `StreamWriter.write()` of the prompt frame (no await between
      snapshot and write); outgoing observer keeps only prompt-accepted
      hooks; `prompt_once` fails closed on a second prompt and on a resolved
      response without a snapshotted boundary.
- [x] GREEN (repair): forced-race regression keeps the current-turn chunk;
      replay exclusion, budget/truncation, B2 rows, callback-drain, and
      config-fidelity suites stay green; delivery-contract and one-prompt
      pins added.

## Focused-review WATCH dispositions (2026-07-24)

- **Delivery ordinals vs. callback scheduling.** `RunTask._update_sink`
  ordinals are invocation-count-based and stay aligned with the driver's
  wire-order count only because (a) `NativeAcpClient.session_update` runs
  its synchronous sink before any await and (b) the locked SDK 0.11.0
  dispatcher creates one notification task per frame in receive order,
  whose first steps CPython asyncio starts in creation order (documented
  FIFO `call_soon`). No broader protocol ordering guarantee is claimed.
  Both properties are pinned by
  `tests/native_acp/test_update_delivery_contract.py`, including a
  deliberately delayed earlier runner; an SDK/runtime upgrade that breaks
  either fails those pins loudly. Non-blocking while the pins hold.
- **Write-once boundary / connection reuse.** The boundary is deliberately
  write-once: RunTask drives exactly one prompt per driver/connection.
  `prompt_once` now pins that invariant fail-closed (second prompt raises
  `NativeDriverError` before any wire write; regression in
  `tests/native_acp/test_driver_config_fidelity.py`), so multi-prompt
  connection reuse cannot silently inherit the first Turn's boundary.

## Acceptance

1. Load-path Run over a replaying agent finalizes with `final_message`
   exactly the current Turn's assistant text; `truncated` stays `false`;
   replayed chunks still appear as normalized `agent_message_delta` events.
2. Hermetic dispatched-timeout Run (hanging prompt, escalated kill, no ACP
   terminal) persists `status=unknown`, `retryable=false`,
   `origin=supervisor`, quarantines the Session, and a follow-up Run on the
   same Session is refused before any agent spawn.
3. Trustworthy rows are not overcorrected: ACP-terminal-backed timeout stays
   `timed_out`/active; agent-side and supervisor cancel with a trustworthy
   `cancelled` terminal stay `cancelled`/active; pre-existing durable
   `timed_out` terminals remain trusted (result grammar unchanged).
4. Bounded final-message ingestion/truncation semantics are unchanged for
   current-Turn chunks.
5. Four-suite baseline and the canonical verifier pass; no dependency,
   lockfile, version, service, or profile-registry changes.

## Files likely to change

- `src/agent_run_supervisor/native_acp/driver.py` — prompt-wire boundary.
- `src/agent_run_supervisor/native_acp/run_task.py` — sink gate; terminal
  table row; `TURN_TIMEOUT` detail code.
- `tests/native_acp/fake_agent.py` — `replay_on_load` script key.
- `tests/native_acp/test_session_switching.py` — B1 load-replay + forced
  happens-before race regressions.
- `tests/native_acp/test_run_task.py` — B2 vertical; hang+overflow update.
- `tests/native_acp/test_finalization_table.py` — B2 rows.
- `tests/native_acp/test_update_delivery_contract.py` — delivery-ordinal
  contract pins (WATCH disposition).
- `tests/native_acp/test_driver_config_fidelity.py` — one-prompt
  fail-closed pin (WATCH disposition).
- `docs/roadmap/current-status.md` — `active_plan:` pointer.
- `docs/INDEX.md`, `docs/lessons/_drift_report.md` — generated.

## Verification gates

```bash
uv sync --locked --extra dev --extra release --extra native
uv run --locked --extra dev --extra release --extra native pytest -q \
  tests/native_acp/test_run_task.py tests/native_acp/test_finalization_table.py \
  tests/native_acp/test_session_switching.py tests/arsd/test_reconcile.py
./scripts/verify_local.sh
uv run --locked --extra dev --extra release --extra native python tools/build_docs_index.py --check
uv run --locked --extra dev --extra release --extra native python tools/docs_drift_signal.py --check
uv run --locked --extra dev --extra release --extra native python tools/check_roadmap_governance.py
git diff --check
```

## Risks

- **Update-ordering race:** the B1 gate must not rely on callback timing.
  Mitigation: the boundary is snapshotted synchronously immediately before
  the prompt bytes are handed to the real `StreamWriter.write()` (sender-side
  writer tap, no await between snapshot and write). Every current-Turn
  update is necessarily read after that write and lands above the boundary;
  updates counted below it were received before the prompt could have
  reached the agent. The SDK's post-send outgoing observer is deliberately
  not the snapshot point — it runs only after write+drain, where a fast
  agent's current-turn update can already have been processed (2026-07-24
  focused-review blocker). The residual over-inclusion case — an agent
  spontaneously emitting a pre-prompt chunk still in flight at the write
  instant — is accepted: completeness of current-Turn evidence dominates,
  and the known replay class always precedes the `session/load` response,
  which resolves strictly before the prompt is written.
- **Historical terminal invalidation:** tightening the trusted result
  grammar would turn pre-fix durable `timed_out` rows INVALID and brick
  reconciliation. Mitigation: the grammar in `result.py` is intentionally
  unchanged; only the emitter row changes.
- **Semantic ripple:** `unknown` now dominates the evidence-pipeline
  override for the escalated-timeout row (already the pinned rule for
  `unknown`); the three tests pinning the pre-fix shape are updated with the
  fix, all other rows byte-identical.

## Rollback

Revert the branch commits (or discard the uncommitted worktree changes);
no storage schema, wire protocol, dependency, or deployment surface is
touched, so rollback is a pure source revert. Durable artifacts written by
fixed builds remain valid under the unchanged result grammar.

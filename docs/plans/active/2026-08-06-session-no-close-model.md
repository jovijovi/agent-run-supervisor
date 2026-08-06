---
title: "ARS Session No-Close Model Implementation Plan"
status: active
created_at: 2026-08-06
last_validated_at: 2026-08-06
implementation_authorized: false
production_authorized: false
---
# ARS Session No-Close Model Implementation Plan

> **For Hermes:** Use `subagent-driven-development` to execute this plan task-by-task only after explicit source-implementation approval.

**Goal:** Replace ARS's artificial Session closing lifecycle with one durable, resumable Session model: Runs terminate; Sessions do not close.

**Architecture:** A `submit` request without `session_id` atomically creates a durable ARS Session and its first Run; a request with `session_id` strictly reuses that existing Session and never falls back to `session/new`. Session records have no `open`, `active`, `closed`, ephemeral, or persistent lifecycle category. Concurrency remains a lease concern; machine-proven unsafe continuity remains a quarantine concern; storage retention remains a data-governance concern.

**Tech stack:** Python 3.11+, stdlib-only ARS runtime, Unix-domain Socket API, Native ACP, pytest, repository documentation generators and `make verify`.

---

## 1. Context and decision

The current authority and source model Session state as `active | closed | quarantined`, accept `session_close`, and express creation versus reuse through `session_reuse = none | reuse`. A newly created Native Session is treated as ephemeral and automatically marked closed when its first Run finalizes.

That model assumes ARS can identify a normal business event equivalent to “the user is finished with this conversation.” It cannot. A user may continue immediately, return months later, change topics, or never return. Run completion and user silence do not prove Session abandonment. External AGENT context is durable state rather than a resident ARS process, so normal operation needs no close transition.

The approved replacement decision is:

```text
Session: create → reuse → reuse → indefinitely resumable
Run:     create → execute → terminal
```

There is one Session kind. There is no one-shot Session, no normal Session terminal state, and no close/revoke alias.

## 2. Product position and approval boundary

- `main` currently contains the completed arsd + Native ACP supervision plane and Socket API v2.
- The current PRD, architecture, technical solution, feature tracker, public READMEs, code, tests, and fixtures still teach the old Session lifecycle.
- There is no external or production ARS client population to preserve. ARS is being developed and tested on one development machine.
- Therefore this change carries no compatibility window, dual protocol, alias, shim, online legacy reader, or old-client drain requirement.
- API v3 is still used as an unambiguous contract marker; after cutover, the active daemon and repository client accept/use v3 only.
- Planning approval authorizes this document and its board linkage only. It does **not** authorize source edits, tests that launch external AGENTs, runtime data reset, service restart, release, publication, deployment, or Sachima integration.

## 3. Target contract

### 3.1 `submit` input

The Session portion of the v3 request is exactly:

```text
session_id: optional SessionId
```

| Input | Meaning |
|---|---|
| `session_id` absent | create one new durable Session and execute its first Run |
| `session_id` present | existing-only reuse of that Session |
| unknown or invalid `session_id` | stable refusal; never create a replacement |

Remove `session_reuse` and `ars_session_id`. Use `session_id` consistently on the wire, in Spec/submission records, handlers, results, and caller code.

No standalone `session_create` operation is added. Empty Sessions have no current product use, and atomic create-plus-first-Run avoids abandoned two-step creations.

### 3.2 Idempotent first submission and crash convergence

The existing caller-authenticated `request_id` remains the idempotency key. For a create submission, derive the new ARS `session_id` deterministically from the same principal/request identity that determines the Run. Repeating the same request returns the same Run/Session facts and never dispatches the prompt twice.

The durable submission record — not an in-memory lock — is the create reservation. The keyed admission lock only serializes concurrent live attempts. Retention must preserve the minimal immutable submission/Spec/terminal spine needed to recognize the same authenticated request after bulky Run evidence is pruned.

There is no second durable “Session-creation reservation” and no provisional/unbound Session record. Before `session/new`, the deterministic prospective `session_id` exists only in the sealed submission/Spec identity and the live keyed admission lock. After `session/new` succeeds, ARS atomically creates one fully bound Session record containing the exact external AGENT Session ID; it does not first create a record with a missing external ID and bind it later. If creation fails before that atomic commit, `run_status` reports the terminal failed Run while `session_status` for the prospective ID returns the stable unknown/not-found result. This keeps failed creation distinguishable from an existing resumable Session.

Creation ordering must be:

```text
persist submission with deterministic Run/Session identity
→ seal Spec and launch
→ acquire process-local keyed admission lock for the create path
→ spawn and initialize
→ ACP session/new
→ atomically persist one fully bound Session record with external session id
→ exact configuration fidelity
→ create prompt-dispatch-started marker
→ dispatch prompt
```

The implementation must encode this exhaustive crash table in reconciliation and fault-injection tests:

| Last trustworthy fact | Reconciliation outcome |
|---|---|
| submission only, or sealed Spec/launch before `session/new` | pre-dispatch `failed`; no Session record; same request returns that terminal and never reruns |
| `session/new` may have succeeded but the fully bound Session record is absent and no dispatch marker exists | pre-dispatch `failed`; `session_status` returns unknown/not-found for the prospective ID; provider-side orphan context is not guessed or recovered; no prompt replay |
| Session record committed, config not yet proven, no dispatch marker | pre-dispatch `failed`; Session reusable only when no switch occurred or exact rollback is proven, otherwise quarantine |
| Session record committed and config proven, no dispatch marker | pre-dispatch `failed`; Session remains reusable |
| any dispatch marker, no trustworthy terminal | `unknown`, `retryable=false`, Session quarantine; never replay |
| trustworthy terminal persisted but caller response lost | duplicate request returns the same Run/Session terminal facts |
| required attribution artifact is corrupt or Session attribution is non-actionable | refuse daemon startup under the existing ordered reconciliation rules |

A provider context created by `session/new` but not durably bound before a crash may become an unreachable provider-side orphan. ARS must not guess its ID, scan AGENT-owned storage, or convert it into a resumable Session. The no-dispatch ordering makes that failure safe: the prompt was not sent.

### 3.3 Reuse

Reuse remains strict and existing-only:

```text
load Session record
→ validate owner/namespace/workspace/agent/profile/epoch binding
→ reject quarantine
→ acquire lease
→ ACP session/load with stored external id, byte-unchanged
→ exact config fidelity
→ dispatch
```

A reuse path can never call `session/new`, including after missing data, binding drift, load failure, cancellation, timeout, or exception.

### 3.4 Session record

Retain stable identity and minimum continuity evidence:

- `session_id`
- owner and namespace
- `agent_id`
- profile identity
- workspace binding
- optional operator `session_epoch`
- external AGENT session id
- creation and last-use timestamps
- last observed effective model/effort
- optional quarantine evidence

Delete normal lifecycle fields and concepts:

- `state = open | active | closed`
- `closed_at`
- close reason/source
- ephemeral/persistent flag
- reuse mode as Session identity

Quarantine is not a lifecycle state. Represent it as optional safety evidence, for example `quarantine: null | {reason_code, source_run_id, recorded_at}`. A quarantined Session still exists and remains queryable but refuses new Runs. This plan adds no unquarantine operation.

### 3.5 Lease and Run terminal semantics

One Session permits one active Run at a time. Lease acquisition, renewal/recovery, and release remain independent from Session existence.

- `completed`, `failed`, `cancelled`, `timed_out`, and `unknown` are Run terminals only.
- Every trustworthy Run terminal releases the Session lease.
- A pre-dispatch failure leaves an existing reused Session available unless exact configuration rollback cannot be proven.
- Dispatch uncertainty remains `Run=unknown`, `retryable=false`, plus Session quarantine.
- `run_cancel` affects only the current Run and never ends the Session.
- daemon restart performs reconciliation only and never resends a prompt.

### 3.6 Socket API v3 surface

Keep:

```text
server_info
submit
run_status
run_events
run_cancel
session_status
session_list
```

Remove:

```text
session_close
session_reuse
ars_session_id
```

`session_status` and `session_list` project identity, lease/activity facts, last-use observations, and optional quarantine evidence. They expose no synthetic Session lifecycle state.

The distinction between `ArsdClient.close()` and Session closing must remain explicit: closing a local socket/client/follow subscription is resource cleanup and stays unchanged. Only Session-close semantics are removed.

## 4. Storage and development-data policy

Session identity records are small and remain durable by default. Silence, age, Run completion, daemon restart, and caller disconnection never imply Session expiry. Active-source retention must therefore stop treating Session directories as deletion candidates entirely; a live lease and quarantine remain query/admission facts, not deletion eligibility.

Run retention may prune bulky evidence only after a trustworthy terminal exists. It must preserve a minimal immutable idempotency and attribution spine in the Run directory — at least the durable submission, sealed Spec/launch attribution, and terminal result required by duplicate-submit handling and reconciliation. The implementation must define that allowlist once and test that retention cannot remove it. Event streams, bounded stderr, and other non-authority bulk evidence may be pruned according to policy without making an authenticated `request_id` reusable.

Any future destructive Session-data purge or deletion of the minimal Run idempotency spine is a separate administrator/data-governance design and is not introduced here.

Because there are no external/production clients or durable production obligations:

- do not implement online schema migration;
- do not retain v1/v2 protocol handlers;
- do not dual-read or dual-write old Session records;
- do not preserve close-only legacy fixture behavior as a supported surface;
- archive any operator-selected evidence, then rebuild development Run/Session state at cutover.

Archiving or deleting live development data, restarting `arsd`, and validating a deployed daemon remain separately approved operator actions. Source implementation must not perform them automatically.

## 5. Capability-preservation matrix

| Capability | Required after change |
|---|---|
| one process per Run | preserved |
| real `session/new` for first Run | preserved, now creates a durable Session |
| real `session/load` across Runs | preserved |
| strict no-new fallback on reuse | preserved |
| exact model/effort fidelity | preserved |
| owner/namespace/workspace/profile/epoch binding | preserved |
| one active Run per Session | preserved through lease |
| unknown/quarantine/no replay | preserved |
| run cancellation | preserved; Session remains resumable unless quarantined by uncertainty |
| bounded redacted evidence | preserved |
| Socket API v1/v2 compatibility | deliberately removed; no consumers exist |
| Session close / one-shot Session | removed |
| Sachima integration | out of scope |

## 6. Implementation plan

All stages belong to one short-lived task branch and one coherent PR. Intermediate commits are reviewable save points, not independently deployable releases.

### Stage D0 — Reset documentation authority

**Objective:** Make the authority chain describe the approved no-close model before source implementation begins.

**Files:**

- Modify: `GOAL.md`
- Modify: `docs/product/prd.md`
- Modify: `docs/design/architecture.md`
- Modify: `docs/design/technical-solution.md`
- Modify: `docs/design/result-event-schema.md`
- Modify: `docs/roadmap/features.md`
- Modify: `docs/roadmap/current-status.md`
- Modify: `docs/roadmap/non-approvals.md` to name this active plan and preserve every non-approval
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Steps:**

1. Replace `active | closed | quarantined` with durable Session identity plus optional quarantine evidence.
2. Replace the closed create/reuse start-plan language with the v3 absent-versus-present `session_id` contract.
3. Delete the v1/v2 client-drain and close-operation requirements; state that no client compatibility population exists.
4. Add/update the feature-tracker row for the no-close Session reset and keep implementation status accurate.
5. Preserve explicit non-approvals for runtime reset, restart, release, deployment, and Sachima integration.
6. Run docs generators and governance checks.

**Acceptance:** Authority documents agree on one Session kind, no normal terminal, strict reuse, quarantine, and v3-only cutover.

### Stage D1 — Freeze API v3 and Session schema with RED tests

**Objective:** Define the new contract at boundaries before changing runtime behavior.

**Likely files:**

- Modify: `src/agent_run_supervisor/arsd/protocol.py`
- Modify: `src/agent_run_supervisor/native_acp/spec.py`
- Modify: `src/agent_run_supervisor/session.py`
- Modify: `src/agent_run_supervisor/arsd/admission.py`
- Modify: `src/agent_run_supervisor/retention.py`
- Test: `tests/arsd/test_protocol.py`
- Test: `tests/arsd/test_api_version_matrix.py`
- Test: `tests/arsd/test_admission.py`
- Test: `tests/native_acp/test_spec.py`
- Test: `tests/native_acp/test_native_session_record.py`
- Test: `tests/test_session_store.py`
- Test: `tests/test_retention.py`

**RED requirements:**

1. v3 accepts submit with absent `session_id` and rejects unknown request fields including `session_reuse` and `ars_session_id`.
2. v3 accepts valid existing-session intent with `session_id` and validates its grammar before storage access.
3. only v3 is accepted; no per-operation v1/v2 drain matrix remains.
4. Session serialization has no normal lifecycle state or close fields.
5. quarantine evidence is structurally bounded, categorical, and contains no raw exception or untrusted remote text.
6. Session directories are never retention deletion candidates.
7. terminal Run pruning preserves the immutable idempotency/attribution spine, and a repeated authenticated `request_id` remains non-dispatching after pruning.

**Acceptance:** Boundary tests fail for the intended missing implementation and encode the complete v3 request/record/retention shape.

### Stage D2 — Implement atomic durable creation and strict reuse

**Objective:** Make every newly created Session durable and every reuse path existing-only.

**Likely files:**

- Modify: `src/agent_run_supervisor/native_acp/run_task.py`
- Modify: `src/agent_run_supervisor/native_acp/storage.py`
- Modify: `src/agent_run_supervisor/native_acp/driver.py` only if the start-plan interface changes there
- Modify: `src/agent_run_supervisor/arsd/handlers.py`
- Modify: `src/agent_run_supervisor/arsd/admission.py`
- Modify: `src/agent_run_supervisor/arsd/reconcile.py`
- Test: `tests/native_acp/test_session_start_plan.py`
- Test: `tests/native_acp/test_run_task.py`
- Test: `tests/native_acp/test_session_switching.py`
- Test: `tests/arsd/test_handlers_registry.py`
- Test: `tests/arsd/test_reconcile.py`
- Test: `tests/arsd/test_reconcile_oracle.py`

**Steps:**

1. Replace reuse-mode branching with `session_id is None` versus existing-session load.
2. Derive create-session identity deterministically from authenticated submission identity.
3. Remove the current pre-`session/new` unbound Native Session record; after successful `session/new`, atomically commit one fully bound Session record before the dispatch marker. A failed pre-commit creation remains visible only as a terminal Run, while `session_status` returns unknown/not-found for the prospective Session ID.
4. Preserve byte-exact external session ID storage/load and callback identity checks.
5. Remove automatic post-Run `mark_closed`; settle Run, persist last-use observations, and release lease.
6. Update reconciliation attribution and exhaustive outcomes without closed-state branches.
7. Implement the §3.2 crash table as table-driven reconciliation and fault-injection tests, including every write boundary from submission through terminal response loss.
8. Refactor retention to preserve the minimum idempotency/attribution spine and to exclude Session directories from deletion.

**Acceptance:** `create → completed → reuse → completed` works in-process; no terminal Run changes Session existence; reuse never reaches `session/new`; every §3.2 crash row converges deterministically; retention cannot make a prior `request_id` dispatchable again.

### Stage D3 — Remove Session-close mechanisms from every active surface

**Objective:** Delete the concept, not merely hide the Socket operation.

**Likely files:**

- Modify: `src/agent_run_supervisor/arsd/client.py`
- Modify: `src/agent_run_supervisor/arsd/handlers.py`
- Modify: `src/agent_run_supervisor/arsd/protocol.py`
- Modify: `src/agent_run_supervisor/session.py`
- Modify: `src/agent_run_supervisor/session_runtime.py`
- Modify: `src/agent_run_supervisor/policy.py`
- Modify: `src/agent_run_supervisor/caller.py`
- Modify: `src/agent_run_supervisor/session_inspect.py`
- Modify: `src/agent_run_supervisor/retention.py`
- Modify: `src/agent_run_supervisor/hermes_caller/intake.py`
- Modify: `src/agent_run_supervisor/hermes_caller/hermes.py`
- Modify: `src/agent_run_supervisor/hermes_caller/__init__.py`
- Modify: `scripts/smoke_persistent_session.py`
- Modify: `scripts/smoke_codex_acpx.py`
- Modify: `scripts/capture_acpx_contract.py`
- Test: `tests/test_smoke_persistent_session.py`
- Test: `tests/test_session_strategy_guard.py`
- Modify/remove close-only expectations in `fixtures/acpx-0.10.0/`, `fixtures/acpx-0.12.0/`, and `scripts/validate_contract_fixtures.py`
- Update all focused tests named by `git grep` at implementation preflight

**Steps:**

1. Delete `session_close`, `mark_closed`, `SessionClosedError`, close-mode command compilation, close artifacts, and close-only caller projections across both Native/arsd and the still-present acpx source.
2. Delete acpx `exec | persistent` Session-lifetime classification by restructuring `ensure_exec_strategy`, `ensure_persistent_strategy`, their command-compiler/runner callers, and the named smoke/capture/strategy tests so retained acpx comparison/runtime code no longer selects behavior by Session lifetime. Preserve only distinctions that describe an actual Run command shape or adapter capability rather than Session longevity; full removal of unrelated acpx code remains separately approved work.
3. Make `SessionQuarantinedError` independent of the deleted closed error hierarchy.
4. Remove closed-state parser, inspection, retention, race, and fixture assertions.
5. Preserve resource-level `close()` methods for sockets, streams, ACP connections, and files.
6. Treat the user's ARS-wide no-close decision as approval to **plan** targeted removal of close/session-kind concepts from active acpx source, but not as source-implementation approval or authority to remove unrelated acpx runtime.

**Acceptance:** An active-tree scan, including the retired-but-still-present acpx source and fixtures, finds no Session-close API/state/mechanism and no ephemeral/persistent Session-lifetime classification; ordinary resource cleanup and unrelated acpx comparison code remain intact.

### Stage D4 — Complete public contract and acceptance harnesses

**Objective:** Prove the caller-visible v3 contract and cross-Run continuity without deploying it.

**Likely files:**

- Modify: `src/agent_run_supervisor/arsd/client.py`
- Modify: `scripts/arsd_crash_containment_harness.py`
- Modify: `tests/arsd/test_client_daemon.py`
- Modify: `tests/arsd/test_codex_acceptance_contract.py`
- Modify: `tests/arsd/test_codex_socket_acceptance.py`
- Modify: `tests/arsd/test_real_socket_acceptance.py`
- Modify: `tests/native_acp/test_cursor_cross_run_session_resume.py`
- Modify: real-agent smoke tests only within their existing opt-in boundaries

**Acceptance scenarios:**

1. first submit returns stable `run_id` and `session_id`;
2. duplicate first submit with the same authenticated `request_id` returns the same facts and causes no second dispatch;
3. a duplicate first submit arriving while the original request is still in flight is serialized by the keyed admission lock, resolves through the same durable submission, and causes no second `session/new` or prompt dispatch;
4. second and third Runs reuse the same stored external session ID;
5. daemon-process reconstruction test proves reuse after restart/reconciliation without replay;
6. `run_cancel` leaves the Session reusable when terminal evidence is trustworthy;
7. lease conflict blocks concurrent use;
8. unknown Session ID fails without creating anything;
9. post-dispatch uncertainty quarantines the Session and forbids automatic retry;
10. no response, log, or exception leaks raw remote text, offending IDs, or credential-shaped values.

Real Claude Code/Codex canaries, daemon restart, and development-data reset are post-merge/operator gates and require separate approval; tests may prepare opt-in harnesses but must not execute those side effects by default.

### Stage D5 — Final reconciliation, review, and merge-ready candidate

**Objective:** Produce one exact candidate whose authority, source, tests, and public language agree.

**Steps:**

1. Re-run the active-tree inventory for retired terms and inspect every hit; historical Git/Changelog facts may remain, active contract claims may not.
2. Run `python tools/build_docs_index.py --write` and `python tools/docs_drift_signal.py --write`.
3. Run focused Session/arsd/Native ACP tests during repair.
4. Run `make verify` as the only complete repository gate.
5. Run an independent fresh-context blocker review against the exact candidate tree, with read access to the full ARS repository and no mutation authority.
6. Apply at most one focused blocker-fix pass, rerun affected gates, then rerun blocker-only review on the final tree.
7. Create verified atomic commit(s). Push/PR/merge occur only if separately authorized.

**Acceptance:** Exact candidate passes `make verify`, independent blocker review reports no blocker, and source/document scans prove the retired Session-close contract is absent from active surfaces.

## 7. Verification commands

Focused iteration:

```bash
python3 -m pytest -q \
  tests/arsd/test_protocol.py \
  tests/arsd/test_admission.py \
  tests/arsd/test_handlers_registry.py \
  tests/arsd/test_reconcile.py \
  tests/arsd/test_client_daemon.py \
  tests/native_acp/test_native_session_record.py \
  tests/native_acp/test_session_start_plan.py \
  tests/native_acp/test_run_task.py \
  tests/test_session_store.py \
  tests/test_retention.py
```

Document and governance gates:

```bash
python3 tools/build_docs_index.py --write
python3 tools/docs_drift_signal.py --write
python3 tools/build_docs_index.py --check
python3 tools/docs_drift_signal.py --check
python3 tools/check_roadmap_governance.py
git diff --check
```

Complete gate:

```bash
make verify
```

Active-tree retirement scan must cover tracked source, tests, scripts, fixtures, and maintained public docs while excluding cold archives and historical Changelog facts. Hits must be classified, not blindly deleted, because resource-level `close()` remains valid.

## 8. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| first submit creates duplicate Session/Run after response loss | duplicate prompt or split context | deterministic Session identity plus existing request-id idempotency; no replay |
| Session record is absent when a dispatched Run becomes uncertain | reconciliation cannot quarantine safely | persist bound Session record before dispatch marker |
| removing closed state weakens quarantine | unsafe reuse | model quarantine as independent durable evidence and test every admission/reconciliation path |
| source cleanup accidentally removes socket/process cleanup | leaks and stuck resources | targeted Session-semantic scan; preserve `ArsdClient.close`, follow close, ACP/process teardown |
| legacy acpx cleanup expands into unrelated removal | scope drift | remove only close concepts and direct dependencies; full acpx removal stays separately approved |
| public docs and Chinese README disagree | callers learn conflicting contracts | update both maintained READMEs and run generated-doc/governance gates |
| development data is silently mutated during implementation | evidence loss or runtime disruption | no automatic migration/reset/restart; operator gate after merge |

## 9. Rollback

Before merge, rollback is ordinary branch/commit reversion; no runtime state is touched.

After a source merge but before any runtime cutover, revert the source commit and rebuild the development artifact. Because the plan deliberately adds no dual-schema compatibility, runtime cutover must not happen until the v3 package, repository caller, selected development-data reset, and acceptance procedure are ready as one operator-controlled action.

After runtime cutover, rollback requires restoring the pre-cutover package **and** its archived development-state snapshot together. Never point old code at newly written v3 Session records or new code at old records.

## 10. Definition of Ready for source implementation

All must hold:

- [ ] The human explicitly approves ARS source implementation of this plan, including the targeted removal of Session-close and Session-kind logic from the still-present acpx source.
- [ ] The authority-reset scope in D0 is accepted; no alternative Session lifecycle remains unresolved.
- [ ] A clean task branch/worktree starts from fresh `origin/main`.
- [ ] No runtime reset, restart, release, deployment, or Sachima action is bundled into source authorization.
- [ ] The implementer has full read access to the ARS repository and task-sufficient test/tool access.

## 11. Explicit non-goals

- Session close, revoke alias, one-shot/ephemeral Session, or persistent/ephemeral classification;
- standalone empty-Session creation;
- automatic Session expiration based on silence or age;
- destructive Session purge API;
- v1/v2 compatibility, drain window, shim, alias, dual-read, dual-write, or online migration;
- complete acpx removal;
- Sachima changes or integration;
- package release/publication;
- service configuration, restart, deployment, or live cutover.

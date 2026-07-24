---
title: "vNext Stage 2 — arsd production ingress implementation plan"
status: archived
created_at: 2026-07-22
last_validated_at: 2026-07-23
archived_at: 2026-07-23
---
# Implementation plan — vNext Stage 2: `arsd` production ingress

> **Closed and archived (2026-07-23).** A1–A5 are closed; A5 production/default-on enablement
> completed on 2026-07-23. The `arsd` user unit is enabled+active as a local supervision
> service for trusted callers under the closed A2 caller policy, on the versioned CPython
> 3.12.3 runtime (ARS 0.2.0, ACP SDK 0.11.0, pidfd present), built from source commit
> `b7f38eb0553786ec594c78a72ce62f33bdc57ae1` (tree
> `5bc3e13bad9f8ee6a39f9e747d07fd49ed07e5f9`). Sanitized runtime closure digest (SHA-256):
> `78a75a2d0d3c8a967d974598bdbb77d138a9ff94dc6fb9654797993d7a5dd6cb`; pre-enable evidence
> tree: `11723d37a09211f1219d320c10ced647affe32fbc16fe2b8e86d20d320631e91`; post-enable
> evidence tree: `7dcbd7b0ed57ab3c98502fdef8eeb13d0781c451de456a05aba47ba8aa025d7b`. Evidence
> is sanitized and operator-held outside Git; exact CallerPolicy and credential values remain
> controller-only. This closure authorized and performed **no** push/PR/merge, tag/GitHub
> Release/PyPI, Sachima, Gateway/IM, public ingress, or branch cleanup. Everything below —
> including the §3 gate/timing narrative — is retained historical implementation detail, not
> living authority. Living board: `docs/roadmap/current-status.md`; phase record:
> `docs/roadmap/archive/phases/vnext-stage2-arsd-production-ingress.md`.

Archived Stage 2 execution plan. Derived from `GOAL.md`, PRD R6/R10 (plus R1–R5/R7–R9
as already-implemented constraints), `docs/design/architecture.md` §§1–2/5–8, and
`docs/design/technical-solution.md` §§1.3/6/9/11. This plan redefines no product goal, widens
no scope, and approves nothing by its existence.

## 1. Task contract

**Objective.** Implement the Stage 2 production ingress: a thin, unprivileged, local `arsd`
daemon that is the sole production entry — versioned bounded protocol over a Unix domain
socket, `SO_PEERCRED` caller authentication resolved through a closed caller-principal policy
seam, a durable idempotent submission/acceptance handshake (no success acknowledgement before
a persisted admission artifact; at-most-one dispatch per authenticated caller request key),
principal-bound owner-checked Run/Session operations, an in-process
`RunTask` per Run, idempotent startup reconciliation before listen, bounded concurrency and
graceful shutdown, user-level service/cgroup crash containment, and real C-grade S1–S5
socket-path acceptance on OpenCode 1.18.4.

**Done criteria.**

1. **A1 source/default-closed foundation — complete.** New `agent_run_supervisor.arsd` package
   plus the §10-authorized minimal existing-seam edits (Slices 1–5 and 6a) merged with their
   focused suites, the full test suite, and `./scripts/verify_local.sh` green. PR #72 merged
   the reviewed exact A1 head `fca2bfa364113ec4c39c8cdc16724d363cd1d757` as the tree of
   then-current `main` `d102dfde24aef34c9ecfffb466941d72d901ba83`; independent exact-head
   review passed and GitHub CI was green before merge.
2. Real S1–S5 socket-path acceptance executed and recorded as sanitized, operator-held
   C-grade evidence covering G9/G10/G11 (§11).
3. The T1 G12-timing decision that unblocked A1 is recorded (§3); production enablement
   remained unapproved until A5 closed (archive notice above).
4. Post-merge documentation sync records A1 source completion on the board and in
   `features.md` (including the F-VNEXT-PERMISSION-001 canary remainder). The plan remained
   active until A2–A5 and real S1–S5 closed; it is now archived with the Stage 2 phase record.

**Hard constraints.**

- UDS only: `0700` socket directory, `0600` socket; no TCP, no root, no broad RBAC, no
  database, no plugin system, no durable per-Run Worker.
- Runtime stays Python stdlib-only; no new dependencies, no `pyproject.toml` edits (§9 —
  subpackage discovery already ships `arsd`).
- acpx is never a Native driver, fallback, or session store; legacy stores are never read or
  written by Native/`arsd` code paths.
- The exact-config-before-prompt sequence is owned by `RunTask`/`NativeAcpDriver` and is not
  re-implemented or bypassed; `arsd` adds no configuration path.
- `submit` acknowledges success only after the write-once `submission.json` admission
  artifact is durably persisted and the Run task is registered; admission is durably
  idempotent per authenticated `(principal_id, request_id)` — the Run identity is derived
  deterministically from that key, a retransmission recovers the original Run and never
  creates or dispatches a second one, and a same-key/different-content submit fails closed
  (§5); terminal facts come only from `result.json`.
- `unknown → quarantined → retryable=false` is preserved everywhere; no component replays,
  resumes, or auto-retries a possibly-dispatched prompt; a dispatched Run without a
  trustworthy ACP terminal is never finalized `cancelled` (PRD R5).
- Native state flows only through `native_acp.storage` root constructors; write-once artifacts
  only via `storage.write_once_json`.
- Existing-module edits are limited to the closed §10 authorized-seam list; anything further
  stops for approval (§15).
- One primary implementation writer in one fresh worktree; failing-test-first TDD; narrow
  conventional commits.

**Explicit non-approvals restated** (each requires its own operator approval; see §3 and
`docs/roadmap/non-approvals.md`): any Stage 2 source expansion or repair beyond the merged
source line; follow-on push/PR creation/merge; tag, GitHub Release, PyPI publication;
Sachima `ArsdBackend` or pin changes; Gateway/IM/live-traffic expansion; public ingress.
A2/G12, A3, A4, and A5 are closed by the operator records in §3 and the archive notice;
those records authorized the A5 enablement runbook only, nothing else.

## 2. Baseline and authority trace

- **Merged A1 source basis:** `main` at A1 closure was
  `d102dfde24aef34c9ecfffb466941d72d901ba83`. PR #72
  (`feat/arsd-production-ingress`) is merged; its reviewed exact A1 head
  `fca2bfa364113ec4c39c8cdc16724d363cd1d757` is the merge tree. Current `main`, including
  the PR #74 permission-mediation repair, is recorded in §3.
- Published/tagged v0.2.0 predates A1 and contains no `arsd`; no follow-on release or
  publication is approved.
- Stage 0/1 (Native ACP core C1–C10) is closed and merged with real B-grade OpenCode 1.18.4
  direct-drive evidence; the released 0.2.0 line ships no `arsd`, no Native service entry, and
  no UDS/`SO_PEERCRED` code (verified against the base tree).
- This plan is Stage 2 only. Sachima `ArsdBackend` stays parked and out of scope.
- Verified current-source seams this plan builds on (all inspected in the worktree at base):
  - `native_acp/run_task.py` — `RunTask(request=, prompt_text=, run_id=, workspace_root=,
    registry=, supervisor_root=|session_store=+event_store=, submitted_at=, retry_of_run_id=,
    cwd=)`; `RunTask.run() -> NativeRunResult(run_id, status, payload, run_dir, session_id,
    session_state)`; markers `prompt-dispatch-started` / `prompt-accepted`; on
    `asyncio.CancelledError` it finalizes bounded (child wind-down, one terminal fact, session
    disposition, lease release) and re-raises; its docstring already designates `arsd` as the
    Stage-2 wrapper. Two verified gaps this plan closes through the §10-authorized minimal
    seam edits: (a) `RunTask.run()` first exclusively calls `EventStore.create_run` and, when
    creation fails, returns a `failed` `NativeRunResult` with `run_dir=None` — nothing
    durable exists, so a handler cannot pre-create the directory and cannot acknowledge
    before `run()` starts; (b) `finalize_run_state` currently maps the supervisor-cancelled
    escalated-kill row (`dispatch_started`, no ACP stop reason) to `cancelled`/quarantined,
    which PRD R5 does not permit without a trustworthy terminal fact.
  - `native_acp/storage.py` — `native_session_store()`, `native_event_store()`,
    `write_once_json()`, `create_native_session()`, `bind_agent_session()`,
    `NATIVE_RUNS_DIRNAME` / `NATIVE_SESSIONS_DIRNAME`, state bijection `to_native_state` /
    `to_persisted_state` (persisted `open` ⇄ API `active`).
  - `native_acp/spec.py` — `AgentRunRequest` (fields: `owner`, `namespace`, `profile_id`,
    `session_reuse`, `ars_session_id`, `expected_binding_hash`, `input_refs`,
    `requested_model`, `requested_effort`, `grant_ref`, `grant_hash`, `grant_role_hash`,
    `grant_capabilities`, `mcp_snapshot_hashes`, `credential_refs`, `limits: RunLimits`,
    `evidence_policy_hash`, `recovery_policy_hash`, `schema_version`); `RunLimits`
    (startup/turn/cancel-grace timeouts, stderr/event byte caps, `max_events`);
    `NativeSpecError` hierarchy; `resolve_workspace_binding`.
  - `session.py` — `SessionStore(base_dir=, liveness_probe=)`, `acquire_lock(...,
    reclaimable=, holder_kind=, required_state=)`, `release_lock`, `update_lock_holder`,
    `mark_quarantined(session_id, reason=, run_id=)`, `mark_closed`, `open_session`,
    `SESSION_JSON`/`LOCK_JSON`, `STATE_OPEN`/`STATE_CLOSED`/`STATE_QUARANTINED`,
    `SessionLockError`/`SessionQuarantinedError`. Verified lock/quarantine contract:
    `acquire_lock` deterministically replaces an expired lease inside the per-session guard;
    it reclaims a within-TTL lock only on explicit `reclaim_crashed=True` with a provably
    crashed recorded holder (`process_liveness.classify_lock`, composite supervisor+child
    rules); `reclaimable=False` locks recover by TTL expiry only;
    `required_state=STATE_OPEN` refuses quarantined sessions inside the same guarded critical
    section ("no new lease is ever minted" for a quarantined session); `mark_quarantined` is
    idempotent first-fact-wins and never unlinks `lock.json`; `detect_stale_locks` is
    read-only inspection.
  - `event_store.py` — `EventStore.create_run` (exclusive run-dir create; rejects a
    pre-existing dir with `EventStoreError`; run-id regex `_RUN_ID_RE`), `RunHandle`,
    `secure_mkdir` (0700), `exclusive_create_bytes` / `atomic_write_json` (0600).
  - `exit_classifier.py` / `result.py` — `AgentRunStatus` with additive `failed | cancelled |
    unknown`; `_RETRYABLE_DEFAULT[UNKNOWN] is False` (hard); `build_result_payload(...)`
    keeps the compatibility result shape (`docs/design/result-event-schema.md`).
  - `managed_process.py` / `process_liveness.py` — `ManagedProcess.identity`,
    `terminate_group(reason=)`, `kill_group(reason=)`, `wait() -> ManagedExit`;
    `ProcessIdentity(pid, process_start, boot_id, host)`, `classify_lock`, `REAL_PROBE`.
  - `native_acp/profile.py` — `OPENCODE_1_18_4`: `profile_id="opencode-1.18.4"`, revision 2,
    registered closed model pair `kimi-for-coding/k3` + `deepseek/deepseek-v4-pro`, literal
    efforts including `max`, `requires_session_load=True`; `DEFAULT_REGISTRY`.
  - Run-dir artifact set produced by `RunTask`: `spec.json`, `launch.json`, `effective.json`,
    `events.jsonl`, `result.json`, both dispatch markers, `progress.json`, `stderr.log`,
    `redaction-report.json`. Ephemeral sessions are named `<run_id>-ephemeral`.

## 3. Definition of Ready — separate approvals

| # | Approval | Blocks | Notes |
|---|---|---|---|
| A1 | Stage 2 source/default-closed foundation | **Complete:** Slices 1–5 and 6a merged in PR #72 | source lane is closed; any expansion or repair requires separate approval |
| A2 | G12 caller policy: policy owner + exact real UID→principal/owner/namespace mapping | **Closed 2026-07-23** by recorded operator policy decision | exact values are controller-only and never enter docs, code, or tests; closure digest in the §3 closure status below |
| A3 | User-service/cgroup harness | **Closed 2026-07-23** for user-service/restart readiness (no real Run in A3 scope) | rendering the unit was A1 source-lane; keeping it installed/enabled for real callers is A5 |
| A4 | Real external-AGENT acceptance | **Closed 2026-07-23:** real OpenCode S1–S5 socket-path runs passed | accepted runtime CPython 3.12.3; sanitized C-grade evidence operator-held; fakes never substituted |
| A5 | Production enablement | **Closed 2026-07-23: production/default-on enabled** | executed under the controller-held A5 production-enablement runbook; canary + independent review PASS; runtime closure digests in the archive notice |

Future push, PR creation, merge, tag/Release/PyPI each additionally remain Hermes-owned
separate approvals and are not granted by any row above.

**A1 closure status (2026-07-23).** The merged source/default-closed foundation records no
production caller values, service activation, real S1–S5 execution, production enablement,
release, deployment, or Sachima integration. A2–A5 state is recorded below.

**A2–A4 closure and A5 authorization status (2026-07-23).** Recorded operator decisions.
Basis: current `main` `b7f38eb0553786ec594c78a72ce62f33bdc57ae1` (tree
`5bc3e13bad9f8ee6a39f9e747d07fd49ed07e5f9`), which includes PR #74
(`fix(native-acp): mediate OpenCode write permissions`), merged before A4 closure:

- **A2/G12 closed** as an operator-held policy decision. The exact policy owner and
  UID→principal/owner/namespace mapping values are controller-only and never enter the
  repository. Sanitized closure record digest (SHA-256):
  `98d2d7b243d88f814afac5c5b36293917d5043db03778847dcdd681e6f72a04f`. The mapping reaches the
  daemon only as repeatable `--caller-mapping` argv rendered into the mode-0600 user unit; no
  caller-policy config file exists in current source, and zero mappings keep failing closed.
- **A3 closed** for user-service/restart readiness only; no real Run was in A3 scope. Closure
  record digest: `203971ffb12a5e51a9c0ec8f527398375508acb1e83b103cef5811b36e6f242c`.
- **A4 closed:** real OpenCode 1.18.4 S1–S5 socket-path acceptance passed (C-grade), covering
  G9/G10/G11 including the real denied-action canary (S2). Accepted runtime: CPython 3.12.3.
  Closure record digest: `0d1e850361a61c89b3bb947436f09324e821ec14607cf77f60459c80716f7086`;
  sanitized evidence tree digest:
  `4b9a7a741ae1681e0c5242c198f6f36b775309fdfff638caa13e20cdbdce7706` (operator-held, out of
  Git).
- **A5 runtime invariant:** production `arsd` runs only on CPython 3.12.3, the interpreter
  that carried A4. Its build provides `os.pidfd_open`/`signal.pidfd_send_signal`, which the
  S4 crash-containment harness requires for its PID-reuse-safe kill; the standalone Python
  3.11.15 build lacks them and is not an equivalent runtime.
- **A5 closed 2026-07-23 — production/default-on enabled.** Enablement followed the
  controller-held Git-external `A5_PRODUCTION_ENABLEMENT_RUNBOOK.md` (exact-main wheel,
  commit-versioned CPython 3.12.3 runtime, disabled unit install, manual-start production
  canary, independent fresh-context blocker review, then `enable --now`). Runtime closure and
  evidence tree digests are recorded in the archive notice; Stage 2 is closed and this plan
  is archived.

Push/PR/merge, tag/Release/PyPI, Sachima `ArsdBackend`, Gateway/IM, and public ingress remain
separately unapproved.

**G12 timing conflict (T1) — historical sequencing context; fully closed 2026-07-23 (part 1
by the 2026-07-22 ruling below, part 2 by the A2 record above).** At A1 planning time the
authority chain did not state one consistent G12 timing. PRD R6 ("exact UID values and policy
ownership are Stage 2 gate G12; documentation does not choose them"), technical solution §9
("G12 requires explicit approval of caller UID policy and values before production
enablement"), and the then-current board gate table bound G12 to *production enablement*;
technical solution §11 states Stage 2 "adds `arsd` and production acceptance only after
separate approval **and G12 resolution**". Choosing between those readings was
higher-authority arbitration the plan author did not own, so this plan recorded the conflict
and held A1 fail-closed pending the Operator's T1 decision. **The Operator supplied the
sequencing ruling — recorded below — and A1 was unblocked; its source/default-closed
foundation is merged:** §11's "G12 resolution" did not require G12 closure before A1 source
start. The second T1 half — the exact policy owner and UID→principal/owner/namespace mapping
values — was deferred to A2, blocked A4 real acceptance and A5 production enablement while
open, and **closed 2026-07-23 with the A2 operator record above**. Current state: A2 closed,
A3 closed, A4 closed, A5 closed 2026-07-23 — production/default-on enabled.

The recorded ruling required no redesign: the caller policy is injected configuration (§6);
code, tests, and docs never record production values; the daemon has no implicit default
mapping and refuses to serve without an explicit one (standing invariants); and because
acceptance before A2/G12 resolution was not permitted, only Slice 6 timing shifted — real
S1–S5 execution waited behind A2/G12 and the separate A3/A4 approvals, all of which have
since closed (§3 closure status above).

**Recorded T1 sequencing ruling (Operator, 2026-07-22; closure annotations added
2026-07-23).** The Operator instruction `开始执行计划吧`, following the controller's stated
default, authorized the now-merged A1 source implementation. The ruling as recorded at that
time, with current closure state in brackets:

- A1 Slices 1–5 and 6a proceeded before exact G12 production mapping values existed
  [complete/merged].
- Source implements a generic immutable CallerPolicy seam with zero implicit/default
  mappings and fail-closed startup [standing invariant].
- Hermetic tests may inject explicit synthetic/test-scoped mappings only; no production
  UID, owner, namespace or credential value may appear [standing invariant].
- A2 policy owner and exact UID→principal/owner/namespace mapping values were unresolved at
  ruling time [closed 2026-07-23 by the A2 operator record above].
- A4 real external-AGENT acceptance and A5 production enablement were blocked pending
  explicit A2/G12 resolution and separate approval [A4 closed 2026-07-23; A5 closed
  2026-07-23 — production/default-on enabled].
- A3 service/cgroup harness execution was separately blocked [closed 2026-07-23].
- Push/PR/merge/release/deploy/Sachima/Gateway/IM/public ingress were — and still remain —
  separately unapproved.

This resolved T1 part (1): §11's "G12 resolution" did not block A1 source completion but had
to be explicitly resolved and separately approved before real S1–S5 acceptance (A4) and
production enablement (A5); both subsequently closed under the §3 records above. T1 part (2)
— the policy owner and exact mapping values — was carried by A2 and is closed by the A2
record above (§15 T1).

## 4. Minimal architecture slice

```text
trusted local caller (same trust domain)
        │ NDJSON frames over AF_UNIX SOCK_STREAM (0700 dir / 0600 socket)
        ▼
arsd — one unprivileged asyncio process per user
  server.py     accept loop · SO_PEERCRED · CallerPolicy principal resolution · bounds · isolation
  protocol.py   versioned frames · closed op set · stable errors
  admission.py  durable idempotent intake: key-derived Run identity · exclusive create_run
                · write-once submission.json · prepared handoff
  handlers.py   run registry · submit/status/events/cancel · session ops · principal-bound owner checks
  reconcile.py  startup-only idempotent convergence (before listen)
        │ in-process asyncio task per Run
        ▼
native_acp.run_task.RunTask  (§10-authorized minimal seam: accepts a prepared RunHandle and
then never calls create_run; owns spec admission → spawn → exact config → markers → bounded
evidence → finalization → session disposition)
        ▼
registered external ACP AGENT process (process-per-Run)
```

`admission.py` is a file-granularity refinement inside technical solution §1.3's
submit/handler responsibility — one durable admission owner — not a new architectural layer.

Explicitly absent by design: TCP or root mode, RBAC/policy engine, database, runtime plugin
loader, arbitrary command adapter, durable per-Run Worker, acpx fallback, second stdout
consumer, second process/runtime layer. Direct `ars-core` embedding stays the sanctioned
test/dev path; production callers fail closed when `arsd` is unavailable.

## 5. Versioned UDS protocol (`arsd/protocol.py`)

- **Framing.** Newline-delimited canonical-JSON objects (one frame per line) both directions.
  `MAX_FRAME_BYTES` enforced on read (asyncio stream limit) and write; an oversize inbound
  frame yields `FRAME_TOO_LARGE` and connection close. All caps are named constants in
  `protocol.py`.
- **Envelope.** Every request carries `api_version` (int, v1 = `ARSD_API_VERSION = 1`), `op`,
  and a caller-chosen `request_id` in a closed bounded format (non-empty ASCII
  `[A-Za-z0-9._-]`, length-capped by a named `protocol.py` constant; a violation →
  `INVALID_REQUEST`); responses echo `request_id`. On every op `request_id` correlates
  request and response; on `submit` it is additionally the principal-scoped idempotency key
  (§5 submit handshake, §6) — never correlation-only metadata. Missing/unknown
  `api_version` → `UNSUPPORTED_API_VERSION` with the supported list; unknown `op` →
  `UNKNOWN_OP`. The v1 operation and error sets are closed; unknown request fields are
  rejected (`INVALID_REQUEST`), never ignored.
- **Operations (v1).**
  - `server_info` — package version, `api_version`, limits snapshot; usable as ping.
  - `submit` — payload: an `AgentRunRequest`-shaped object mapped field-for-field onto
    `native_acp.spec.AgentRunRequest` (including nested `RunLimits` and `InputRef` items) by a
    parse helper living in `arsd/protocol.py` (the `native_acp.spec` dataclasses stay
    untouched), plus `prompt_text` (≤ `MAX_PROMPT_BYTES`), `workspace_root`, optional `cwd`,
    optional `retry_of_run_id`. The wire never carries executable paths, argv, env values,
    JSON config blobs, or credential values — those surfaces do not exist (PRD R1);
    credential *slot names* come only from the registered profile. `NativeSpecError` from the
    dataclass validators surfaces as `INVALID_REQUEST`.

    **Durable idempotent admission handshake** (one owner, fixed order; §6 defines the
    artifact, §10 the authorized `RunTask` seam). For `submit`, `request_id` is the
    principal-scoped idempotency key. The Run identity is **derived, never generated**:
    `run-<first 32 hex chars of SHA-256(canonical principal_id + separator + request_id)>`,
    conforming to the `EventStore` run-id pattern (inspect `_RUN_ID_RE` while writing the
    test). The canonical encoding must be injective — length-prefixed fields or a separator
    excluded from both validated value alphabets; its exact shape and helper naming are
    Slice 3 mechanics (§15) pinned by stability tests, but the derivation contract is fixed.
    One key can therefore only ever reserve one run dir, and `submission.json` (§6) is the
    durable `(principal_id, request_id) → run_id` binding that makes retransmission safe:

    1. validate the frame and spec mapping (including the closed bounded `request_id`
       format); require the request's `owner`/`namespace` to match the connection
       principal's allowed set exactly (`OWNER_MISMATCH` otherwise);
    2. under the in-process per-`(principal_id, request_id)` admission lock (concurrent
       same-key submits serialize in-process; `EventStore.create_run` exclusive creation
       remains the cross-process filesystem race anchor), resolve the derived run dir
       against durable facts **before any capacity/session check** — a retransmit of an
       already-accepted key is never refused for capacity and never consumes a second
       slot. First matching row, rows mutually exclusive:
       - valid `submission.json` (parses, known submission `schema_version`, records
         exactly this `principal_id` and `request_id`), equal canonical `request_digest`,
         and a registered task **or** a terminal `result.json` → reply the original
         `{run_id, accepted_at}` from the artifact, byte-stable; never create a dir,
         register a second task, or dispatch again for this key — the original Run stays
         queryable via `run_status`/`run_events`;
       - valid `submission.json`, different `request_digest` → `IDEMPOTENCY_CONFLICT`
         (fail closed: one key never binds two request contents);
       - valid `submission.json` with equal digest but no registered task and no terminal
         `result.json` (registration crashed in this daemon life and the step-6 safe
         finalization was itself untrusted) → `SUBMISSION_INDETERMINATE`; never
         auto-dispatch; startup reconciliation (§8) converges the Run to pre-dispatch
         `failed`, after which the same key+digest returns the original accepted identity
         with its terminal queryable;
       - run dir present without a valid `submission.json` for this key (crash between
         `create_run` and the submission write — with or without a later reconciled §8
         terminal — or an artifact recording a foreign principal/key, which is an
         integrity failure, logged, never duplicate-matched) → `SUBMISSION_INDETERMINATE`,
         fail closed, never dispatch; that derived identity is permanently consumed, so a
         new attempt requires a new `request_id`;
    3. first submission for the key only: check global capacity and the per-session slot
       (`CAPACITY_EXHAUSTED` / `SESSION_BUSY`);
    4. `admission.prepare_run`: call `EventStore.create_run` **exactly once** with the
       derived Run ID — the sole `create_run` call site for socket-submitted Runs;
       exclusive directory creation stays the duplicate-prevention/race anchor (an
       unexpected `EventStoreError` here fails closed with nothing acknowledged and is
       re-resolved from durable facts on retransmit, never dispatched blindly);
    5. persist the write-once `submission.json` admission artifact (§6 contents, including
       the idempotency key and `request_digest`) via `storage.write_once_json`;
    6. construct `RunTask` with the prepared `RunHandle` (it must not call `create_run`
       again) and register it in the run registry; on registration failure, attempt the
       existing safe pre-dispatch `failed` finalization (write-once terminal; nothing was
       dispatched) before replying `INTERNAL` — if that finalization is itself untrusted,
       the indeterminate state is preserved fail-closed and the step-2 rows govern every
       retransmit;
    7. reply `{run_id, accepted_at}` — the success acknowledgement exists **only after** the
       durable submission artifact and successful task registration.

    A failure in step 4 replies `INTERNAL` and nothing durable claims acceptance; a failure
    in step 5 replies `INTERNAL` without acknowledgement and leaves a reserved dir without a
    submission binding — that key thereafter fails closed (`SUBMISSION_INDETERMINATE`), and
    the dir converges at the next startup reconciliation as a pre-dispatch terminal failure
    (§8). No acknowledged Run can lack a durable artifact. A success response lost after
    registration or after dispatch is recovered by retransmitting the **same**
    `(principal_id, request_id)`: admission is durably idempotent, the caller re-obtains the
    original Run identity, and at most one dispatch ever occurs per key. The claim is
    durable idempotent admission plus at-most-one dispatch per key under §6's write-once
    artifact integrity model — **not** exactly-once execution. A caller authorizes a new
    attempt only with a new `request_id` (a new Run, optionally linked via
    `retry_of_run_id`); nothing is ever replayed automatically (PRD R5). After
    acknowledgement, admission/config/spawn failures surface as the Run's own terminal
    `failed` result: terminal facts always come from `result.json`, never from
    protocol-layer improvisation.
  - `run_status` — principal-authorized (§6); returns the `progress.json` snapshot plus
    `result.json` when terminal (already-redacted artifacts only). Before the Run's first
    `progress.json` write it returns a minimal accepted-state snapshot derived from
    `submission.json`; it never invents terminal facts.
  - `run_events` — principal-authorized; bounded snapshot page from the Run's `events.jsonl`
    with `from_seq` cursor and `limit` (default/max page constants); optional `follow: true`
    subscribes the connection to subsequent event frames through a bounded per-subscription
    queue. A slow consumer overflows the queue → subscription ends with
    `EVENT_BACKLOG_EXCEEDED`. Read-side only: it never blocks, fails, or bounds the Run or
    its evidence writer.
  - `run_cancel` — principal-authorized; cancels the registry task, which triggers
    `RunTask`'s bounded finalize. Terminal semantics (PRD R5; technical solution §6):
    pre-dispatch cancellation finalizes `failed` with the session reusable; post-dispatch
    cancellation with a trustworthy ACP terminal fact (stop reason `cancelled`) finalizes
    `cancelled` with the session reusable; post-dispatch cancellation **without** a
    trustworthy ACP terminal (cancel grace expired, escalated kill) finalizes `unknown`,
    Session `quarantined`, `retryable=false` — never `cancelled`. §10 authorizes the minimal
    `finalize_run_state` row correction this requires.
  - `session_status` / `session_list` / `session_close` — principal-authorized wrappers over
    the Native `SessionStore` (list is filtered to the connection principal's allowed owners;
    states reported in the API vocabulary via `to_native_state`; close refuses quarantined
    sessions exactly as `mark_closed` does).
- **Stable errors.** Single envelope `{"error": {"code", "message"}}`; closed v1 code set:
  `UNSUPPORTED_API_VERSION`, `UNKNOWN_OP`, `MALFORMED_FRAME`, `FRAME_TOO_LARGE`,
  `INVALID_REQUEST`, `UNAUTHENTICATED_PEER`, `PEER_UID_DENIED`, `OWNER_MISMATCH`,
  `IDEMPOTENCY_CONFLICT`, `SUBMISSION_INDETERMINATE`, `UNKNOWN_RUN`, `UNKNOWN_SESSION`,
  `SESSION_BUSY`, `CAPACITY_EXHAUSTED`, `EVENT_BACKLOG_EXCEEDED`, `SHUTTING_DOWN`,
  `INTERNAL`. Messages are bounded and never carry secrets, raw payloads, or unredacted
  agent text.
- **Disconnect semantics.** An accepted Run is durable by construction (§5 handshake): caller
  disconnect never cancels it, and its terminal result stays re-queryable by `run_id`. When
  the disconnect swallowed the success reply itself, the caller recovers the same Run
  identity by retransmitting the same `(principal_id, request_id)` submit (idempotent
  admission above) — never by minting a new key. Disconnect tears down only that
  connection's subscriptions and in-flight replies. Cancel is always an explicit op.

## 6. Caller authentication and ownership (`arsd/server.py`, `arsd/admission.py`, `arsd/handlers.py`)

- **Socket placement.** `--socket PATH` argument; default `$XDG_RUNTIME_DIR/
  agent-run-supervisor/arsd.sock` when `XDG_RUNTIME_DIR` is set, else
  `<supervisor_root>/arsd/arsd.sock`. The socket directory is created/verified `0700` via the
  existing `secure_mkdir` primitive; the socket file is chmod `0600` before `listen()`. The
  AF_UNIX ~108-byte path limit is validated with a stable startup error. A pre-existing
  socket file is replaced only after a connect probe proves no live daemon owns it.
- **Peer authentication.** Per accepted connection, read `SO_PEERCRED`
  (`struct ucred` → pid/uid/gid) via `socket.getsockopt`. Unreadable/unsupported peer
  credentials → `UNAUTHENTICATED_PEER` + close. Linux-only by design: startup asserts the
  platform capability and fails closed (no fallback authentication). This authenticates the
  local caller; it is not an OS sandbox or hostile-process containment claim.
- **`CallerPolicy` — trusted peer-to-principal mapping (G12 seam).** A closed, immutable
  mapping frozen at daemon start from explicit configuration: peer UID → trusted principal
  (`principal_id` plus the exact, closed set of `owner`/`namespace` pairs that principal may
  act as). Exact class/flag names are the implementer's; the contract is binding. An
  allowlisted UID alone is **not** ownership authentication — ownership always derives from
  the resolved principal binding, never from a repeated caller claim. A UID absent from the
  mapping → `PEER_UID_DENIED`, close, bounded structured log line (no secrets). **No
  implicit default mapping exists — not even same-UID:** with zero configured mappings the
  daemon refuses to listen (stable startup error), so production listen/enablement fails
  closed without the G12-approved mapping (T1). Test/dev configurations inject explicit
  synthetic (typically same-UID) mappings; no production UID value or owner mapping appears
  in code, tests, or docs.
- **Ownership binding at submit.** The request's `owner`/`namespace` must exactly match the
  connection principal's allowed set (`OWNER_MISMATCH` before any Run is created). The
  accepted binding is persisted in `submission.json` **before** acknowledgement.
- **Durable submission artifact** (replaces any handler-written `caller.json` design — that
  idea is racy against `RunTask`'s exclusive `create_run` and is rejected).
  `submission.json`, written once via `storage.write_once_json` inside `prepare_run` (§5),
  is simultaneously the acceptance record and the durable idempotency binding
  `(principal_id, request_id) → run_id` that makes retransmission safe. It contains at
  minimum: submission `schema_version`; the idempotency key (`principal_id`, `request_id`)
  and the derived `run_id`; `retry_of_run_id`; protocol `api_version`; `accepted_at`
  (UTC ISO); the authenticated principal binding (`principal_id`, peer `pid`/`uid`/`gid` at
  accept time); the bound `owner`/`namespace`; the session reference (`session_reuse`,
  `ars_session_id` or the derivable ephemeral naming); `profile_id`; and the canonical
  digest material — `request_digest` (SHA-256 over the canonical-JSON form of every
  behavior-affecting submit input: the mapped request object, `workspace_root`, `cwd`,
  `retry_of_run_id`, and the prompt bound as `prompt_sha256`/`prompt_bytes`;
  transport-only correlation material — `api_version`, `op`, and the `request_id` key
  itself — is excluded) plus `prompt_sha256` and `prompt_bytes`. Duplicate detection
  compares exactly this digest (§5). Explicitly excluded: prompt text, credential values,
  raw env, secrets of any kind. Additive artifact; every existing artifact name and shape
  is unchanged, so `docs/design/result-event-schema.md` compatibility is preserved.
- **Operation authorization.** Every Run/Session operation authorizes the **connection
  principal** against the resource's recorded binding: Runs resolve owner/namespace from
  `submission.json` (authoritative from acceptance and present even when admission never
  produced `spec.json`; once `spec.json` is sealed its `identity.owner`/`identity.namespace`
  must agree — a mismatch is an integrity failure, never an authorization source), with
  `spec.json` identity as fallback only for pre-`arsd` direct-drive runs in the same root;
  Sessions resolve from the session record `owner`/`namespace`. Exact match within the
  principal's allowed set is required (`OWNER_MISMATCH` otherwise); a Run dir with no
  recorded ownership binding is never exposed over the socket. Unknown IDs →
  `UNKNOWN_RUN`/`UNKNOWN_SESSION`.
- **Test rule (G12-safe).** Hermetic tests run over real temp-dir UDS sockets under the test
  UID; allow/deny both directions are proven by injecting explicit synthetic mappings
  (including same-UID principals) into `CallerPolicy`, and the zero-mapping startup refusal
  is proven. No test assumes a second real OS UID and none hard-codes a production UID.

## 7. Daemon lifecycle and bounds (`arsd/handlers.py`, `arsd/server.py`)

- **Async Run registry.** `run_id → {asyncio task, principal, owner/namespace, session_id,
  submitted_at}`. Global `max_concurrent_runs` (default 4, flag-configurable) →
  `CAPACITY_EXHAUSTED`. Idempotent retransmits resolve from durable facts before any
  capacity/session check (§5 submit order), so an already-accepted key is never refused
  `CAPACITY_EXHAUSTED`/`SESSION_BUSY` and never consumes a second slot. Per-Session
  serialization: the registry fast-fails a second distinct-key submit against a session
  with an active tracked Run (`SESSION_BUSY`); the `SessionStore` lease
  (`acquire_lock(required_state=STATE_OPEN)` inside `RunTask`) remains the authoritative
  serializer — the registry check is an optimization, never a substitute.
- **Bounds.** Finite listen backlog (default 16); max concurrent connections (default 32);
  `MAX_FRAME_BYTES` per frame; per-connection in-flight request cap (default 8); bounded
  event-follow queues (default 256 frames). Run-side bounds (event queue/bytes, stderr cap,
  timeouts) remain owned by `RunLimits`/`EventWriter`/`ManagedProcess`, unchanged.
- **Exception isolation.** Each per-connection task and each registry Run task has a top-level
  exception boundary: a connection fault closes that connection (`INTERNAL` where a reply is
  still possible); a Run fault is converted by `RunTask`'s own guard into a controlled
  terminal state. Registry entries always deregister in `finally`. One malformed caller or
  one failing Run can never take down the daemon.
- **Graceful shutdown** (SIGTERM/SIGINT): stop accepting; answer new frames
  `SHUTTING_DOWN`; cancel in-flight registry tasks — `RunTask` performs its bounded finalize
  (ACP wind-down, `terminate_group` → grace → `kill_group`, reap, one terminal fact, session
  disposition, lease release); per §5 cancellation semantics, a dispatched Run that yields no
  trustworthy ACP terminal during wind-down finalizes `unknown`/quarantined/`retryable=false`,
  never `cancelled`; bounded overall shutdown deadline (default 90 s, i.e. the RunTask
  finalize timeout plus margin) then hard-exit; unlink the socket; exit 0. This process-group
  escalation path is *normal* cancellation and is deliberately distinct from crash-time
  cgroup cleanup (§9).
- **Reaping.** Child reaping stays inside `ManagedProcess.wait`/`RunTask`; the daemon adds no
  second reaper and never guesses liveness by PID/name — `ProcessIdentity` rules govern.

## 8. Startup reconciliation (`arsd/reconcile.py`)

Runs to completion strictly before the socket is bound. Scope: only the Native roots obtained
from `native_acp.storage.native_session_store` / `native_event_store`; legacy `runs/`/
`sessions/`/acpx stores are never read or written.

**Convergence derivation (pure, from durable facts only).** For each run dir under
`native-runs/`, derive the target disposition from `result.json`, the dispatch markers,
`submission.json`, and `spec.json` — first matching row:

| Durable facts | Required Session/progress side effects (idempotent, established first) | Terminal fact (written last) |
|---|---|---|
| `result.json` present | if its `status` is `unknown` and `prompt-dispatch-started` exists → ensure the owning Session is quarantined; ensure the terminal `progress.json` disposition | preserved byte-for-byte; never rewritten or reclassified |
| no result; `prompt-dispatch-started` present | quarantine the owning Session (`mark_quarantined(reason=…, run_id=…)`); terminal `progress.json` disposition | write-once via `build_result_payload(status=AgentRunStatus.UNKNOWN, retryable=False, origin="supervisor", detail_code="RECONCILED_UNKNOWN")` |
| no result; no dispatch marker; `submission.json` and/or `spec.json` present | none — the Session record is untouched and stays reusable (architecture §5 first row) unless independently quarantined | write-once `failed`, `detail_code="RECONCILED_PRE_DISPATCH"`, `retryable` per `_RETRYABLE_DEFAULT` |
| no result; no marker; neither `submission.json` nor `spec.json` (death inside `prepare_run` before durability — the caller was never acknowledged) | none | write-once `failed` as above; the Run has no ownership binding and is never exposed over the socket; its reserving idempotency key stays consumed — a same-key retransmit keeps failing closed (`SUBMISSION_INDETERMINATE`, §5) and successor work needs a new `request_id` |

Session resolution uses `submission.json` when present (authoritative from acceptance:
`session_reuse`/`ars_session_id`, else the `RunTask` ephemeral naming `<run_id>-ephemeral`),
falling back to `spec.json` for pre-`arsd` direct-drive runs. `prompt-accepted` alone never
upgrades certainty — it is a local write-completion fact only. A missing session dir is
logged, never fatal.

**Commit order and idempotency.** Side effects are established before the terminal fact, and
the terminal fact is written last, write-once — so no crash window can leave an irreversible
`unknown` result paired with an un-quarantined Session, and the result-present row repairs
the side effects implied by a preserved result instead of skipping them. Every step is
idempotent: `mark_quarantined` is first-fact-wins (already-quarantined returns without a
write — verified source) and never unlinks `lock.json`; `progress.json` is written only when
its recorded disposition differs (single-threaded pre-listen, so check-then-write is
race-free); the terminal uses `storage.write_once_json`. A crash after any durable step
re-derives the same row on the next start and converges: a second full reconciliation
changes zero bytes across `result.json`, session records, lock files, and `progress.json`,
and never replays a prompt. Runs whose admission persisted only `submission.json` (no
`spec.json`) converge through the same table. Reconciliation is also what restores the §5
idempotency contract after an interrupted admission: once a submission-bearing Run carries
its pre-dispatch `failed` terminal, a retransmitted same-key/same-digest submit returns the
original accepted identity with that terminal queryable; a dir that never gained its
submission binding converges to `failed` but stays unexposed, and its key stays consumed
(`SUBMISSION_INDETERMINATE`).

**Session leases (strictly read-only).** Reconciliation performs no lock mutation of any
kind: no unlink, no check-then-delete, no rewrite, no lease minting, and no new
`SessionStore` API (none is load-bearing). `SessionStore.detect_stale_locks` is read-only
inspection and may inform the structured reconciliation log only. Recovery relies entirely on
the existing guarded semantics at the next acquisition: `acquire_lock` deterministically
replaces expired leases inside the per-session guard; Native locks are written
`reclaimable=False` and therefore recover by TTL expiry only; and
`required_state=STATE_OPEN` refuses quarantined sessions inside the same guard — no new
lease is ever minted for a quarantined Session. The bounded cost is accepted fail-closed
posture: after a daemon crash, an open session holding an unexpired stale lease stays busy
(`SESSION_BUSY`/`SessionLockError`) until TTL.

Invariants: reconciliation never sends a prompt, never replays/resumes, never rewrites a
terminal fact, never un-quarantines, never mints or mutates a lease. After reconciliation,
later independent caller-authorized Runs (linked by `retry_of_run_id` when the caller
chooses) proceed normally.

## 9. Service/cgroup containment

- **Shipped artifact (Slice 6a) — typed module export; no packaging change.** The systemd
  **user** unit template lives as a typed constant/renderer in
  `src/agent_run_supervisor/arsd/service_unit.py` and ships inside the wheel by construction
  (setuptools discovery `agent_run_supervisor*` already includes subpackages — verified in
  `pyproject.toml`, which stays untouched).
  `python -m agent_run_supervisor.arsd --print-service-unit [--socket … --supervisor-root …]`
  renders it to stdout; the operator redirects it into a unit file at A3. No repository-only
  `scripts/systemd/` template exists: the module is the single source of truth, so the
  shipped daemon and its service artifact cannot drift apart and no production template is
  left on an unshipped path. Required semantics (PRD R10): a **user** unit (no root
  anywhere); `ExecStart` invoking `python -m agent_run_supervisor.arsd --socket …
  --supervisor-root …`; `Restart=on-failure`; `KillMode=control-group`; conservative
  `TimeoutStopSec`. `arsd` and every AGENT descendant live in one user-managed cgroup; a
  daemon crash kills the entire descendant tree; restart performs reconciliation only and
  never resends a prompt.
- **Equivalence rule.** Any non-systemd manager used for acceptance must prove the same two
  properties (whole-tree kill on crash; restart → reconcile-only) and the evidence records
  which manager was used.
- **Two distinct mechanisms, both proven separately:** normal cancellation/graceful shutdown =
  ACP cancel + `killpg` escalation from inside `arsd` (§7 tests); crash containment = external
  cgroup kill (S4). Neither substitutes for the other.
- **Approval boundary.** Authoring `service_unit.py`, its tests, and the harness script is
  source-lane work. Activating a rendered unit (`systemctl --user`, `loginctl
  enable-linger`) is A3; keeping it installed/enabled for real callers is A5. Nothing in this
  plan installs or enables anything.

## 10. TDD implementation slices

New files across Slices 1–5:

```text
src/agent_run_supervisor/arsd/__init__.py
src/agent_run_supervisor/arsd/protocol.py
src/agent_run_supervisor/arsd/server.py
src/agent_run_supervisor/arsd/admission.py
src/agent_run_supervisor/arsd/handlers.py
src/agent_run_supervisor/arsd/reconcile.py
src/agent_run_supervisor/arsd/client.py
src/agent_run_supervisor/arsd/__main__.py
tests/arsd/test_protocol.py
tests/arsd/test_server_auth.py
tests/arsd/test_admission.py
tests/arsd/test_handlers_registry.py
tests/arsd/test_reconcile.py
tests/arsd/test_client_daemon.py
```

**Authorized existing-seam edits (closed list — this replaces any claim that all touched
files are new; nothing outside this list may be edited without stopping for approval, §15):**

- `src/agent_run_supervisor/native_acp/run_task.py` — exactly two minimal changes: (a) accept
  a precreated `RunHandle` (prepared-run handoff; when provided, `RunTask.run()` must not
  call `EventStore.create_run`; when omitted, behavior is byte-identical to today); (b)
  correct the `finalize_run_state` escalated-kill row so a supervisor-cancelled dispatched
  Run without a trustworthy ACP terminal finalizes `UNKNOWN`/quarantined (`retryable=false`
  via `_RETRYABLE_DEFAULT`), per PRD R5 — `CANCELLED` only via a trustworthy stop reason.
- `tests/native_acp/test_run_task.py`, `tests/native_acp/test_finalization_table.py` — RED
  tests for both changes (including corrected existing expectations for the escalated row).
- `scripts/smoke_installed_wheel.sh` — the §13 installed-wheel assertions (Slice 6a).

Slice 6a additionally adds `src/agent_run_supervisor/arsd/service_unit.py`,
`tests/arsd/test_service_unit.py`, `tests/arsd/test_real_socket_acceptance.py` (env-gated,
skip-by-default like `tests/native_acp/test_real_opencode_smoke.py`), and
`scripts/arsd_crash_containment_harness.py`. If implementation reveals a genuinely required
touch on any other shared seam, stop, record it in §15, and get it approved — never silently
expand scope.

Commands below use the uv forms from `docs/roadmap/verification.md`; the pip fallback
(`PYTHONPATH=src python3 -m pytest -q …`) applies unchanged.

**Checklist**

- [x] Slice 1 — protocol (merged A1 source/default-closed foundation)
- [x] Slice 2 — server host + peer auth (`CallerPolicy`) (merged A1 source/default-closed foundation)
- [x] Slice 3 — native seams + durable admission + registry/handlers (merged A1 source/default-closed foundation)
- [x] Slice 4 — idempotent startup convergence (merged A1 source/default-closed foundation)
- [x] Slice 5 — client + entrypoint (merged A1 source/default-closed foundation)
- [x] Slice 6a — service artifact and env-gated acceptance harness (merged A1 source/default-closed foundation)
- [x] Slice 6b — real S1–S5 execution (closed by A2/A3/A4 — §3 closure status; §11 recorded result)

### Slice 1 — `feat(arsd): add versioned bounded UDS frame protocol`

Failing tests first in `tests/arsd/test_protocol.py`: `api_version` required and
unknown-rejected with the supported list; closed op set (`UNKNOWN_OP`); `request_id`
closed-format validation (accept/reject boundary cases) and correlation echo; frame byte
caps both directions (`FRAME_TOO_LARGE`); `MALFORMED_FRAME` on
non-JSON / non-object lines; the closed error-code set is exactly §5's; submit wire→
`AgentRunRequest`/`RunLimits`/`InputRef` mapping round-trip; unknown-field rejection; bounded
`prompt_text`; `NativeSpecError` surfacing as `INVALID_REQUEST`.

- RED: `uv run pytest -q tests/arsd/test_protocol.py` → collection/import failure (package
  absent), then assertion failures.
- GREEN: same command passes. Regression: `uv run pytest -q tests/arsd`,
  `uv run python -m compileall -q src scripts tests`.

### Slice 2 — `feat(arsd): add UDS server host with SO_PEERCRED auth and caller-principal policy`

Failing tests first in `tests/arsd/test_server_auth.py` (real temp-dir UDS): socket dir `0700`
and socket `0600` verified by mode probe; AF_UNIX path-length validation error; same-UID
`SO_PEERCRED` resolved through an explicitly injected same-UID mapping and allowed; a UID
absent from the injected mapping → `PEER_UID_DENIED` + close; unreadable peercred →
`UNAUTHENTICATED_PEER`; **zero configured mappings → the server refuses to listen (stable
fail-closed startup error)**; the resolved principal (not the raw UID) is what reaches the
handler layer; finite backlog / connection-cap behavior; a poisoned connection (handler
raises) never kills the accept loop; graceful shutdown unlinks the socket and answers
`SHUTTING_DOWN`; stale socket replaced only after a failed connect probe.

- RED/GREEN: `uv run pytest -q tests/arsd/test_server_auth.py`. Regression:
  `uv run pytest -q tests/arsd tests/native_acp`.

### Slice 3 — native seams + durable admission + registry/handlers

Ordered sub-steps, three narrow commits:

**3a — `fix(native-acp): finalize supervisor-cancelled dispatch-uncertainty as unknown`.**
RED first in `tests/native_acp/test_finalization_table.py`: `finalize_run_state` with
`dispatch_started=True`, `escalated_kill_after_dispatch=True`, `supervisor_cancelled=True`,
and no ACP stop reason returns (`UNKNOWN`, quarantined); the trustworthy stop-reason
`cancelled` row is unchanged; the existing expectation of `CANCELLED` for the escalated row
is corrected (PRD R5 authority; `retryable` stays hard-`False` for `UNKNOWN`).

**3b — `feat(native-acp): accept a prepared RunHandle for arsd admission handoff`.**
RED first in `tests/native_acp/test_run_task.py`: with a prepared handle, `RunTask.run()`
uses it and calls `EventStore.create_run` zero times (spy store); without one, behavior is
unchanged (exclusive create, existing create-failure path preserved); write-once artifact
behavior inside a precreated dir is unchanged.

**3c — `feat(arsd): add durable idempotent admission, run registry, and principal-bound handlers`.**
Seam: handlers take an injected run-task factory; the production default constructs
`native_acp.run_task.RunTask` with the daemon's `supervisor_root`-bound stores from
`native_acp.storage` and the prepared handle. Hermetic tests inject deterministic fakes for
fault/concurrency shaping — fakes are never product runtime and never acceptance evidence.

Failing tests first in `tests/arsd/test_admission.py`:

- handshake ordering: the acknowledgement is observable only after `submission.json` is
  durable and the task is registered (ordering spy);
- idempotent admission (per-key contract): concurrent same-key/same-digest submits
  serialize on the per-key lock — exactly one `create_run`, one `submission.json`, one
  registered task, and every reply carries the byte-identical original accepted fact;
  retransmit after a dropped success reply both post-registration and post-dispatch (spy
  factory) → the original `run_id` is returned and no second task is constructed or
  dispatched; same-key/different-digest → `IDEMPOTENCY_CONFLICT`; cross-principal same
  `request_id` → distinct derived Run identities; a retransmit of an accepted key at full
  capacity / against its own busy session is not refused (§5 resolution order);
- fault injection at every window: failure/crash after `create_run` but before the
  submission write → no acknowledgement, the dir carries no submission artifact, and a
  same-key retransmit fails closed `SUBMISSION_INDETERMINATE` with no dispatch; after the
  submission write but before registration → no acknowledgement, the in-handler safe
  pre-dispatch `failed` finalization is attempted, and a same-key retransmit either
  returns the original accepted identity (terminal present) or fails closed
  `SUBMISSION_INDETERMINATE` (finalization suppressed by the test) — never a dispatch;
  acknowledgements for one key are only ever byte-identical replays of the original
  accepted fact, never a second distinct acceptance;
- duplicate creation: a seeded pre-existing run dir without a valid submission binding →
  fail-closed `SUBMISSION_INDETERMINATE` reply, nothing acknowledged, nothing dispatched;
  a seeded artifact bound to a foreign principal/key → fail-closed integrity error, never
  duplicate-matched; an unexpected `EventStoreError` from `create_run` (filesystem race
  anchor) → error reply, nothing acknowledged, no blind dispatch;
- derivation and digest: derived run ids are deterministic per `(principal_id,
  request_id)`, stable across daemon restarts, and match the `EventStore` pattern (inspect
  `_RUN_ID_RE` in the test); `request_digest` canonicalization is stable under JSON
  key re-ordering, excludes transport-only material (`api_version`, `op`, `request_id`),
  and changes for any behavior-affecting field or prompt byte change; encoding-injectivity
  and collision-shape/format guards fail closed;
- `submission.json`: write-once, exact §6 field set including the idempotency key and
  `request_digest`, no prompt text, no secret-shaped values (field scan); byte-identical
  after a duplicate retransmit;
- prepared handoff: the run-task factory receives the prepared `RunHandle`; the daemon
  performs exactly one `create_run` for one `(principal_id, request_id)` across all
  retransmissions.

Failing tests first in `tests/arsd/test_handlers_registry.py`: `max_concurrent_runs` →
`CAPACITY_EXHAUSTED`; second submit on an active session → `SESSION_BUSY` fast-fail, plus one
real-`RunTask` path proving the lease (`SessionLockError` surface) stays authoritative;
principal-bound checks with two injected principals on submit and on
status/events/cancel/session ops (allowed exact match; other principal → `OWNER_MISMATCH`;
unknown ids → `UNKNOWN_RUN`/`UNKNOWN_SESSION`); ownership resolved from `submission.json`
including a Run that has no `spec.json` yet; a Run dir with no ownership binding is never
exposed; `run_events` snapshot paging by `from_seq`/`limit` over a seeded `events.jsonl`;
follow-mode slow consumer → `EVENT_BACKLOG_EXCEEDED` with the Run unaffected; cancel:
pre-dispatch → `failed` + session reusable, dispatched fake with trustworthy `cancelled`
terminal → `cancelled`, dispatched fake without terminal → `unknown` + quarantined +
`retryable=false`, terminal always read from `result.json`, registry deregistered; caller
disconnect mid-Run → Run continues to terminal and stays re-queryable; registry always
deregisters in `finally`, including on handler exception.

- RED/GREEN: `uv run pytest -q tests/native_acp/test_finalization_table.py
  tests/native_acp/test_run_task.py tests/arsd/test_admission.py
  tests/arsd/test_handlers_registry.py`. Regression: full `uv run pytest -q`.

### Slice 4 — `feat(arsd): add idempotent startup convergence before listen`

First a source-inspection task (no code): re-confirm at the implementation base what this
plan verified in the worktree — `mark_quarantined` idempotency, `acquire_lock` guarded
TTL/`reclaim_crashed`/`required_state` semantics, read-only `detect_stale_locks`, and the
`reclaimable=False` Native lock contract — and record any drift in §15 before proceeding.

Failing tests first in `tests/arsd/test_reconcile.py` (seeded native-store fixtures):

- the §8 convergence rows for both a `reuse` session (from `submission.json`/`spec.json`
  `ars_session_id`) and an ephemeral `<run_id>-ephemeral` session;
- submission-only runs (durable `submission.json`, no `spec.json`) → `RECONCILED_PRE_DISPATCH`
  `failed`; bare dirs (neither artifact, no markers) → `failed` and never exposed over the
  socket;
- restart/reconciliation followed by retransmit (§5 idempotency recovery, driven at the
  `arsd` handler seam): a reconciled submission-bearing run answers a same-key/same-digest
  retransmit with the original accepted identity and its queryable
  `RECONCILED_PRE_DISPATCH` terminal — `submission.json` and `result.json` byte-identical,
  no dispatch, no new `create_run`; a reconciled bare dir (no submission binding) keeps
  answering `SUBMISSION_INDETERMINATE`; a run with a preserved dispatched terminal answers
  the retransmit with the original identity and untouched result bytes;
- fault injection after **every** durable step: crash after quarantine before progress, and
  after progress before the terminal write — each resumed pass converges;
- repair path: seeded `result.json` with `unknown` + dispatch marker + still-open session →
  the next pass quarantines the session and updates progress while the result bytes stay
  identical;
- terminal-preserving: every existing `result.json` byte-identical after reconcile;
- a full second pass changes zero bytes (tree comparison over `native-runs/` and
  `native-sessions/`);
- locks: every seeded `lock.json` (held, expired, and quarantined-session) is byte-identical
  after reconcile; structural assertion that `arsd/reconcile.py` contains no lock
  unlink/rewrite call site and uses no `SessionStore` mutation API beyond `mark_quarantined`;
- quarantined session: no lease minted (the existing
  `acquire_lock(required_state=STATE_OPEN)` refusal re-proven at the `arsd` seam);
- poisoned same-ID legacy `runs/`/`sessions/` trees untouched (mirrors
  `tests/native_acp/test_native_store_isolation.py`);
- no prompt/ACP call sites exist in the module (structural assertion).

- RED/GREEN: `uv run pytest -q tests/arsd/test_reconcile.py`. Regression:
  `uv run pytest -q tests/arsd tests/native_acp`, then full suite.

### Slice 5 — `feat(arsd): add typed local client and unprivileged daemon entrypoint`

`arsd/client.py`: a typed local caller (connect/submit/status/events/cancel/session ops,
pinned `api_version`, bounded reads, context-managed socket) for Hermes/trusted CLI and the
acceptance harness. `arsd/__main__.py`: argparse entry
`python -m agent_run_supervisor.arsd --socket … --supervisor-root … <caller-mapping
configuration> [--max-concurrent-runs …] [--max-connections …] [--log-level …]` (the exact
mapping-flag shape is a Slice 5 mechanic; zero mappings → startup refusal, §6); refuses to
start with effective UID 0 (fail-closed "no root service" guard); wires reconcile → listen
ordering; installs signal handlers.

Failing tests first in `tests/arsd/test_client_daemon.py`: end-to-end over a temp UDS with a
deterministic injected run-task factory — daemon start proves reconcile-before-listen
ordering; startup refusal with zero caller mappings; submit/status/events/cancel round-trips
through `client.py`; SIGTERM → `SHUTTING_DOWN` for stragglers, bounded exit, socket unlinked;
root-refusal guard (patched euid); client maps every §5 error code to a typed exception.

- RED/GREEN: `uv run pytest -q tests/arsd/test_client_daemon.py`. Regression: full
  `uv run pytest -q`; `uv run python -m compileall -q src scripts tests`; then the full
  `./scripts/verify_local.sh` for the merge candidate.

### Slice 6 — `feat(arsd): add shipped service-unit export and real socket acceptance harness`

6a (source lane, A1) — **complete/merged**: `arsd/service_unit.py` (typed unit template + renderer) and the
`--print-service-unit` flag in `arsd/__main__.py` (§9). RED first in
`tests/arsd/test_service_unit.py`: the rendered unit contains `Restart=on-failure`,
`KillMode=control-group`, a user-scope `ExecStart` with the exact daemon invocation and
parameterized socket/supervisor-root, conservative `TimeoutStopSec`, and no root-mode
directives. The env-gated real acceptance module `tests/arsd/test_real_socket_acceptance.py`
covers S1/S2/S3/S5 through `arsd/client.py` (skipped by default; opt-in env var, following
the `test_real_opencode_smoke.py` precedent). `scripts/arsd_crash_containment_harness.py`
drives S4 (render the unit via `--print-service-unit` → install/start under A3 → submit →
SIGKILL daemon after `prompt-dispatch-started` → cgroup descendant check → restart →
reconciliation assertions → fresh successful Run) — runnable only under A3. The
`scripts/smoke_installed_wheel.sh` additions land here (§13). Hermetic CI stays green with
the real module skipped.

6b (A3 + A4 execution) is closed: A2/G12 was explicitly resolved, A3/A4 were separately
approved, and §11 ran with sanitized operator-held evidence (§3 closure status). 6b itself
changed no source; the permission-mediation repair (PR #74) was its own separately approved
fix lane. The plan and the phase closed with A5 (archive notice).

## 11. G9–G12 and real S1–S5 acceptance matrix

Common ground rules: real OpenCode 1.18.4 via profile `opencode-1.18.4` r2; literal
`kimi-for-coding/k3` + literal `max` with exact readback (never downgraded); disposable
known-empty bound workspace with direct pre/post directory listings; every scenario flows
through the UDS socket path (direct ars-core drive is B-grade at best, never C); evidence is
sanitized and operator-held out-of-Git (Stage 0/1 precedent); every scenario records the
caller-policy (principal-mapping) configuration in force — an explicitly test-scoped mapping
is permitted for acceptance only as ruled by T1 (§3). Deterministic fake children remain
hermetic-test-only and are never C-grade evidence.

| S | Scenario | Exact evidence expected |
|---|---|---|
| S1 | Real read-only success | `submit` → terminal `completed`; `effective.json` shows exact effective `kimi-for-coding/k3`/`max`; `submission.json`/`spec.json`/`launch.json`/both markers/`result.json` present and write-once; empty-workspace pre/post listings identical; `events.jsonl` monotonic `seq`; result shape compatible with `docs/design/result-event-schema.md` |
| S2 | Denied-action canary (G10) | read-only grant; prompt instructs a sentinel write; ≥1 real ACP permission request observed; recorded deny mediation event in `events.jsonl`; the operation observably failed for the agent; sentinel file absent; pre/post listings unchanged. Zero mediation events = FAIL, not pass |
| S3 | Session continuity + switching | Run A (`session/new`) plants a nonce; Run B `session/load` on the unchanged external ID recalls the nonce (historical-token continuity); Run C switches to the registered second model `deepseek/deepseek-v4-pro` with exact readback under the same Session lease; any silent external-session re-creation = FAIL |
| S4 | Crash containment (G9) | SIGKILL `arsd` between `prompt-dispatch-started` and any terminal; cgroup listing proves every AGENT descendant died; restart under the unit performs reconciliation only → `RECONCILED_UNKNOWN` with `unknown`/`quarantined`/`retryable=false`; marker set and `events.jsonl` prove no second dispatch; afterwards a fresh Session/Run succeeds end-to-end |
| S5 | Malformed/failing isolation + subsequent success (G11) | through the live socket: malformed frame, oversize frame, a peer denied by the explicitly injected test-scoped caller mapping, admission-refused submit (model outside the registered domain), a same-key/different-digest submit answered by stable `IDEMPOTENCY_CONFLICT`, and a real failing Run (e.g. credential slot value absent at spawn) — daemon stays up, bounds hold, each failure isolated to its Run/connection with stable error codes; a duplicate retransmission of an already-accepted submit (same principal/key/digest) returns the original Run identity, with marker and `events.jsonl` uniqueness proving no second dispatch; immediately afterwards a real successful Run re-proves credential/model usability |

Gate mapping: G9 ← S4; G10 ← S2; G11 ← S1+S3+S5 (robustness plus re-proven real
credential/model usability inside the socket path); G12 is an approval artifact (policy owner
+ exact UID→principal/owner/namespace mapping) — never producible by tests, and it was not
test-produced: it closed as the recorded A2 operator decision (§3). At the time, A5 was
blocked until that record existed, and A4 was blocked until A2/G12 was explicitly resolved
and separately approved; the sequencing relative to A1/A4 was settled by the recorded T1
ruling (§3), which did not gate A1 source start. Neither block remains open; the recorded
result below is the current closure authority.

**Recorded result (2026-07-23).** S1–S5 executed and passed through the production socket
path with the A2-closed caller policy in force, on accepted runtime CPython 3.12.3, after
the permission-mediation repair (PR #74) merged. G9/G10/G11 are satisfied per the gate
mapping above; G12 is closed by the A2 record. Sanitized C-grade evidence is operator-held
out of Git; closure digests are recorded in §3. This matrix remains the acceptance authority
for any future re-run.

## 12. Permissions and execution lanes

- **Architect (this document):** plan-only. No source, test, dependency, service, runtime, or
  remote change.
- **A1 source lane — complete/merged:** The A1 Lead Developer completed the authorized
  source-only/default-closed foundation in PR #72: new `arsd/` files and only the
  §10-authorized existing-seam edits, with the prescribed hermetic verification. That completed
  authority does not grant source expansion/repair, service installation/activation or cgroup/unit
  management (A3), real-AGENT credentials (A4), push/PR/merge (Hermes-owned), releases,
  Sachima, production config writes, or sudo/root actions.
- **OS harness lane (A3):** operator (or an explicitly authorized runner) activates the user
  unit and executes the S4 harness; user scope only, no root.
- **Reviewer:** independent, fresh-context, read-only blocker review before merge
  (`docs/AI_FLOW.md` review requirements), checking authority alignment, not only tests.
- **Hermes:** scope control, deterministic verification, evidence arbitration, and all
  push/PR/merge/runtime side-effect authority.

## 13. Verification

- Per slice: the slice's focused suite, then `uv run pytest -q` (full), then
  `uv run python -m compileall -q src scripts tests`.
- Merge gate: `./scripts/verify_local.sh` (mirrors CI Verify): lock check, contract fixtures,
  full tests, compileall, doctor, replay, `tools/build_docs_index.py --check`,
  `tools/docs_drift_signal.py --check`, `tools/static_safety_scan.py`,
  `tools/check_version_sync.py`, `tools/check_roadmap_governance.py`, `python -m build`,
  twine check, `./scripts/smoke_installed_wheel.sh`, `git diff --check`.
- Wheel smoke (via the §10-authorized `scripts/smoke_installed_wheel.sh` edit, Slice 6a) must
  prove from the installed wheel: `import agent_run_supervisor.arsd` succeeds,
  `python -m agent_run_supervisor.arsd --help` exits 0, and
  `python -m agent_run_supervisor.arsd --print-service-unit` emits a unit containing
  `Restart=on-failure` and `KillMode=control-group` — the daemon entry and its service
  artifact both ship with no `pyproject.toml` change (subpackage discovery verified, §9).
- Source-change scans: secret-shaped and static dangerous-pattern scans over added lines.
- Docs gates for the doc-sync commits: run `python tools/build_docs_index.py --write` and
  `python tools/docs_drift_signal.py --write`; never hand-edit `docs/INDEX.md`.
- Mutation/remote guards: no network fetches during implementation; no writes outside the
  worktree and test temp dirs; no git push from the implementation lane; evidence is
  operator-held, uploaded nowhere.

## 14. Rollback and risks

**Rollback.**

- Pre-merge: discard the branch/worktree.
- Post-merge, pre-enablement: revert the `arsd` commits — the package is additive, the two
  `native_acp` seam edits are self-contained (default-path behavior unchanged; the
  finalization row change only makes an uncertain outcome stricter), there are no
  migrations, Native stores remain readable, no terminal fact is rewritten.
- Post-enablement: stop/disable the user service and remove the socket → Native ingress is
  disabled and production callers fail closed. No acpx fallback, no in-process production
  fallback, no automatic replay, no terminal-fact rewrites, quarantined sessions stay
  quarantined; successor work is caller-authorized new Runs.

**Top risks.**

- `SO_PEERCRED` is Linux-only → startup platform assertion, fail closed; CI is Linux.
- AF_UNIX 108-byte socket-path limit in deep temp dirs → validation plus short test paths.
- Admission crash windows (acknowledgement vs durability; success responses lost after
  registration or dispatch) → closed by the §5 durable idempotent per-key handshake
  (at-most-one dispatch per `(principal_id, request_id)`), §8 convergence, and the
  Slice 3/4 fault-injection/retransmit suites.
- Deterministic per-key Run identity: a crash inside the reservation window (after
  `create_run`, before the submission binding) permanently consumes that key → bounded,
  caller-visible fail-closed cost (`SUBMISSION_INDETERMINATE`); recovery is a new
  `request_id`, never an automatic replay. Digest-canonicalization drift would surface as
  spurious `IDEMPOTENCY_CONFLICT` → canonicalization is pinned by the Slice 3 stability
  tests and the recorded submission `schema_version`; 128-bit derived-id truncation
  collisions are negligible and any binding mismatch fails closed as an integrity error.
- Stale within-TTL leases after a daemon crash keep a session busy until TTL
  (`reclaimable=False`; `arsd` never force-clears) → accepted bounded fail-closed cost;
  recovery is the existing guarded acquire path only.
- Event-follow backpressure → bounded queues, read-side-only failure.
- systemd --user availability/linger on the acceptance host → A3 checklist plus the §9
  equivalence rule.
- Real-AGENT flakiness in S1–S5 → reruns permitted, every attempt recorded, no fake
  substitution ever.

## 15. Tail / open-decision register

| # | Decision / closure state | Owner | Blocks | Overturn/closure evidence |
|---|---|---|---|---|
| T1 | G12: (a) sequencing ruling for the §3 authority conflict — **recorded 2026-07-22 (§3)**; (b) policy owner + exact real UID→principal/owner/namespace mapping — **closed 2026-07-23 by the A2 record (§3)** | operator (Hermes controller) | nothing — both halves closed | (a) §3 recorded ruling; (b) §3 A2 closure record; exact values stay controller-only |
| T2 | Acceptance-host service manager facts: systemd --user availability, linger, or documented equivalent | operator | nothing — **closed 2026-07-23 with A3** (§3) | recorded A3 closure with host facts |
| T3 | Real-acceptance credential/window availability for OpenCode 1.18.4 (K3 + registered second model) | operator | nothing — **closed 2026-07-23 with A4** (§3) | recorded A4 closure; credential values stay controller-only |
| T4 | A5 live-enablement blocker review verdict (independent, fresh-context, read-only) | operator (Hermes controller) + independent reviewer | nothing — **closed 2026-07-23**: PASS recorded, enablement completed | recorded PASS verdict bound to the frozen sanitized A5 evidence trees (digests in the archive notice) |

Nothing else is a governance decision. Remaining choices (exact numeric bound defaults,
default socket path, the caller-mapping flag shape, exact seam/class names such as
`prepare_run`/`PreparedRun`/`CallerPolicy`, the injective per-key encoding and
request-digest canonicalization helper shapes, client convenience surface) are ordinary
implementation mechanics decided inside their slices and reviewed in the PR — the contracts
in §§5–8 are binding regardless of final names.

## 16. Explicit implementation handoff

This plan's existence authorizes nothing. The T1 sequencing decision enabled A1; the A1
source/default-closed foundation (Slices 1–5 and 6a) is merged in PR #72; the permission-
mediation repair is merged in PR #74; and A2/G12, A3, A4, and A5 are closed by the operator
records in §3 and the archive notice — A5 production/default-on enablement completed
2026-07-23 under the controller-held runbook and its T4 review gate. Still separately and
explicitly required: every follow-on source change, push, PR, merge, tag, GitHub Release,
PyPI publication, deployment, Sachima `ArsdBackend` change, Gateway/IM wiring, or
live-traffic expansion decision. This plan is archived history and grants no successor
implementation. Hermes retains scope control, deterministic gates, evidence arbitration, and
all side-effect authority.

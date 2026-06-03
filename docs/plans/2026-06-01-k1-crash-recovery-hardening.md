---
title: "K1 Crash Recovery Hardening Plan"
status: archived
created_at: 2026-06-01
last_validated_at: 2026-06-01T00:00:00+0800
archived_at: 2026-06-03T18:14:07+0800
---
# K1 Crash Recovery Hardening Plan

> **Scope banner.** K1 implements the carried roadmap tail `ARS-CRASH-RECOVERY`
> from `docs/roadmap/current-status.md` §4: *full process-liveness
> crash/interruption recovery beyond deterministic expired-lease replacement*,
> with the acceptance method *"Detection + safe-recovery proof without unsafe
> cross-process assumptions."* It is the safe completion of the tail H1's plan
> O-3 explicitly deferred. It stays a **local Python library + dev CLI**,
> stdlib-only. It does **not** approve Sachima/Hermes/Gateway/IM integration,
> public ingress, real IM delivery, production config writes, automatic replies,
> service restart, live/default-on behavior, `@all`, or agent-to-agent routing.
> Authoring this plan grants none of those.

## 1. Goal

Close the gap where a **crashed or interrupted** supervisor process leaves a
persistent-session lease (`lock.json`) that is *not yet TTL-expired*, wedging the
session until the lease window elapses. Today recovery is purely time-based:
`SessionStore.acquire_lock` only reclaims a lock once `now >= expires_at`
(provably expired). A `SessionRuntime.send`/`close`/`abort` killed by SIGKILL,
power loss, or OOM bypasses the `finally` release and leaves a within-TTL lease
behind.

K1 adds **conservative, evidence-based process-liveness recovery**:

1. Record explicit **process-ownership metadata** in the lease that *our own
   runtime* writes (host, PID, process start time, boot id).
2. **Classify** an encountered lease holder as `alive`, `crashed`, or `unknown`
   using safe, read-only liveness signals — never sending a terminating signal to
   that recorded holder, never killing a prior holder.
3. **Reclaim** a within-TTL lease only when the holder is **provably crashed**,
   and only when the caller opts in. `alive` or `unknown` always **refuses**
   (fail-safe — concurrency safety preserved, no live-session takeover).
4. **Detect + report** holder liveness and recoverability read-only so operators
   and diagnostics can see crashed leases without any mutation.

This plan is an execution artifact only. It must not redefine product goals,
expand product scope, or imply new live/runtime approvals
(`docs/plans/README.md`, `docs/AI_FLOW.md`).

## 2. Current product position

Derived from `GOAL.md`, `docs/product/prd.md`, `docs/design/architecture.md`,
`docs/design/technical-solution.md`, `docs/roadmap/features.md`, and
`docs/roadmap/current-status.md`:

- **Product** = an independent, local-first Python library + dev CLI that
  supervises ACP/acpx AGENT runs and persistent sessions, normalizing runner
  output into redacted, auditable evidence. `business_verdict` is always `null`.
- **E1 — one-shot exec runner**: Done (PR #8, `21b3393`).
- **S1 — persistent sessions**: Closed for the local lifecycle (S1a–S1d + closure
  acceptance). `F-SESSION-001` Done for the local lifecycle.
- **H1 — operational hardening**: Merged (PR #19, `484ae23`). Delivered the full
  read-only doctor probe set, confined retention/cleanup, the caller-stable
  result/event schema doc, and a **detection-first** crash tail (read-only
  stale-lock/`.tmp-*` detection + provably-expired lock hygiene). H1's O-3
  explicitly deferred process-liveness recovery as `ARS-CRASH-RECOVERY`.
- **I1 — thin caller integration**: Merged as the generic local library boundary
  (PR #20, `83d9cb2`); concrete platform behavior remains parked/unapproved.

**Base:** `origin/main` at `d112302`. Branch:
`ai/k1-crash-recovery-hardening-2026-06-01`. Worktree:
`/home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/k1-crash-recovery-hardening-2026-06-01`.

## 3. Exact K1 target and why it is allowed by the roadmap

`docs/roadmap/current-status.md` §4 tail register:

| ID | Class | Description | Acceptance method | Status |
|---|---|---|---|---|
| `ARS-CRASH-RECOVERY` | CARRY | Full process-liveness crash/interruption recovery beyond deterministic expired-lease replacement (no PID inspection, signals, or live-session takeover). H1 closed only the read-only stale-lock/`.tmp-*` detection + provably-expired lock hygiene. | Detection + safe-recovery proof without unsafe cross-process assumptions | Carried (deferred from H1) |

`architecture.md` §8.1 lists `ARS-CRASH-RECOVERY` as an open architectural tail;
PRD FR-5 acceptance and `architecture.md` §4.1 control point 3 name "stale-lock
recovery ... handle crash/interruption" as a required S1 control surface carried
to operational hardening.

**Why allowed:** the user approved execution of *K1 — Crash recovery hardening*
as the carried `ARS-CRASH-RECOVERY` tail. The acceptance method explicitly asks
for *detection + safe recovery without unsafe cross-process assumptions* — exactly
this plan. K1 redefines no product scope and introduces no non-goal. The
"no PID inspection, signals, or live-session takeover" clause in the tail
*describes what H1 did not do*; K1's mandate is to add **safe** liveness recovery.
K1's **liveness detector/reclaimer** uses only a read-only `os.kill(pid, 0)`
existence probe (POSIX delivers no signal for signal 0) plus `/proc` reads; it
sends **no terminating signal to a recorded holder**, kills no prior holder, and
never takes over a *live* session (a live or unverifiable holder always refuses).
The existing runner watchdog still owns its own child process; K1 adds one
fail-closed child-stop path only if the just-spawned subprocess cannot be recorded
into the lease, preventing an untracked AGENT from continuing.

### Open tails this plan closes vs. carries

| Tail | Disposition in K1 |
|---|---|
| `ARS-CRASH-RECOVERY` | **Close** — liveness-classified detection + opt-in provably-crashed lease reclamation, fail-safe on uncertainty. |
| `ARS-SANDBOX-BOUNDARY` | **Carry** — parked; untouched. |
| `ARS-CALLER-INTEGRATION` / I1 | **Carry** — generic boundary done; concrete platform parked; untouched. |

## 4. Explicit non-approvals (unchanged by this plan)

From PRD §6 and `current-status.md` §5 — none of these are introduced, implied, or
enabled: Sachima behavior integration; real AGENT automatic replies; public
ingress; real IM delivery; Gateway restart/reload/replace; production config
writes; live/default-on behavior; worker auto-routing; participant
persistence/management UI; `@all` fanout; agent-to-agent automatic routing;
trusted Markdown/HTML rendering; treating `allowed_roots` as an OS/filesystem
sandbox; per-run human approval as the default authorization model.

**Crash-recovery-specific boundaries:**

- **No unsafe liveness signals / no prior-holder kills.** The only syscall K1's
  liveness path issues against a recorded PID is a no-op `os.kill(pid, 0)`
  existence probe (signal 0 delivers nothing). K1 never sends `SIGTERM`/`SIGKILL`
  to a recorded holder and never terminates a prior holder. Separately, the
  existing runner is still allowed to manage its own newly spawned child; K1's
  spawn-callback fail-closed path stops that child if lease-holder recording fails.
- **No live-session takeover.** A lease is reclaimed **only** when its holder is
  *provably crashed*. An `alive` holder, or an `unknown`/unverifiable holder
  (different host, missing identity, unreadable start time), always **refuses**.
- **No cross-host assumptions.** A lease recorded on a different host classifies as
  `unknown` (we cannot validate a PID on another machine) → never reclaimed via
  liveness; only TTL expiry applies.
- **No retention loosening.** `retention.py` deletion stays TTL/live-lock
  conservative (it already refuses `open`/live-locked sessions); K1 does not make
  the *deletion* path trust liveness.
- **Pending subprocess window is fail-safe TTL-only.** While `SessionRuntime` has
  acquired a lock but has not yet recorded the spawned subprocess holder, the lock
  is marked `reclaimable: false`; if the supervisor dies in that narrow window,
  K1 deliberately waits for TTL rather than risk taking over a live unrecorded child.

## 5. Source-of-truth trace

- Product goal: `GOAL.md`.
- Product requirements: `docs/product/prd.md` FR-5 (persistent session
  locks/leases, stale-lock recovery, crash/interruption), FR-8 (artifacts).
- Architecture: `docs/design/architecture.md` §4 session lifecycle + §4.1 control
  point 3 (stale-lock recovery / crash), §6 trust boundaries, §8.1 open tails.
- Technical solution: `docs/design/technical-solution.md` §3.3 workspace, §3.6
  session store/runtime, §3.9 event store.
- Schema doc: `docs/design/result-event-schema.md` §6 stale-lock detector.
- Feature tracker: `docs/roadmap/features.md` `F-SESSION-001`, `F-STORE-001`.
- Roadmap phase: `docs/roadmap/current-status.md` §3 (S1/H1 context) + §4 tail
  register (`ARS-CRASH-RECOVERY`).
- Workflow + plan rules: `docs/AI_FLOW.md`, `docs/plans/README.md`.

## 6. Scope

### In scope

- **C1 Process-liveness module** (`src/agent_run_supervisor/process_liveness.py`,
  new): stdlib-only `ProcessIdentity`, `current_identity()`, read-only liveness
  signals (`pid_is_running` via `os.kill(pid, 0)`, `read_process_start` via
  `/proc/<pid>/stat` field 22, `read_boot_id` via
  `/proc/sys/kernel/random/boot_id`), an injectable `LivenessProbe`, and a pure
  `classify_holder(...) -> {alive|crashed|unknown}` decision that is **fail-safe by
  construction** (never `crashed` unless positively proven).
- **C2 Lease ownership metadata** (`session.py`): `SessionStore.acquire_lock`
  records the current process identity (`host`, `pid`, `process_start`, `boot_id`)
  into `lock.json` alongside the existing `token`/`owner`/`acquired_at`/
  `expires_at`. Additive — old locks without these fields classify as `unknown`.
- **C3 Opt-in safe reclamation** (`session.py`): `acquire_lock(...,
  reclaim_crashed=False)` — default unchanged (TTL-only). When `True`, also reclaim
  a within-TTL lease whose holder set is **provably crashed**. The reclamation happens
  under the existing `flock` per-session guard (atomic read→classify→unlink→create).
  For runtime locks with both supervisor and child subprocess identities, both identities
  must classify `crashed`; a live/unknown identity on either side refuses.
- **C4 Read-only recovery detection** (`session.py`): extend
  `SessionStore.detect_stale_locks` with additive keys `holder_liveness`
  (`none`/`alive`/`crashed`/`unknown`) and `recoverable`
  (`lease_expired or reclaimable holder_liveness == "crashed"`). Stays read-only: no signal, no
  kill, no mutation; the only PID syscall is the no-op existence probe.
- **C5 Runtime opt-in** (`session_runtime.py`): `SessionRuntime(...,
  reclaim_crashed=True)` (default on, disableable) threads `reclaim_crashed` into
  the `send` and `close` lease acquisitions so a crashed prior holder no longer
  wedges the session. `alive`/`unknown` holders still refuse (`SessionLockError`).
- **C6 Injection seam**: `SessionStore(base_dir, *, liveness_probe=None)` and
  `SessionRuntime(..., liveness_probe=None)` accept an injected probe so tests drive
  crashed/alive/unknown classifications deterministically (no real crashed PIDs).
- TDD coverage for every behavior change; authority-doc updates after code/tests are
  green; regenerate `docs/INDEX.md` and `docs/lessons/_drift_report.md` via the tools.

### Out of scope / not approved in K1

- All PRD §6 non-goals listed in §4 above.
- Sending any signal, killing/terminating any process, or interacting with the
  external acpx/AGENT process across a crash. (A crashed supervisor's child acpx
  subprocess is already gone/orphaned; K1 only reclaims the **local lease record**.)
- Reclaiming a lease held by a **live** or **unverifiable** holder.
- Cross-host / networked-filesystem liveness reconciliation, remote session
  takeover, or service restart.
- Loosening `retention.py` deletion to trust liveness (deletion stays TTL/live-lock
  conservative).
- Recovering / repairing partial turn directories (`turns/<turn_id>/` left by a
  crashed `send`): harmless incomplete evidence, already age-cleanable by retention;
  detection of `.tmp-*` debris already exists. Documented, not implemented.
- New non-stdlib runtime dependencies (`AGENTS.md` stdlib-only rule; K1 does not
  approve a dependency).
- Result-schema **changes**: K1 only **adds** keys (`holder_liveness`,
  `recoverable`) to the stale-lock detector rows; it renames/removes nothing and
  keeps `business_verdict` `null`.

## 7. Design

### 7.1 C1 — `process_liveness.py` (stdlib-only, fail-safe)

```python
ALIVE, CRASHED, UNKNOWN = "alive", "crashed", "unknown"

@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    process_start: str | None   # /proc/<pid>/stat field 22, as a string
    boot_id: str | None         # /proc/sys/kernel/random/boot_id
    host: str                   # socket.gethostname()

@dataclass(frozen=True)
class LivenessProbe:             # injection seam for tests
    is_running: Callable[[int], bool | None]
    read_start: Callable[[int], str | None]
    current: Callable[[], ProcessIdentity]

REAL_PROBE = LivenessProbe(pid_is_running, read_process_start, current_identity)
```

`pid_is_running(pid)` uses `os.kill(pid, 0)`:
`ProcessLookupError → False` (absent), `PermissionError → True` (exists, other
user), success `→ True`, any other `OSError → None` (indeterminate). Guards
`pid > 0` (never probes a process group / "all processes").

`classify_holder(holder, *, probe=REAL_PROBE)` — pure decision over the recorded
lock fields and `probe.current()`:

1. Holder `pid` missing/non-positive → `UNKNOWN` (old-format lock).
2. Holder `host` missing or `!= current.host` → `UNKNOWN` (cannot validate a PID on
   another machine).
3. Holder `boot_id` and current `boot_id` both known and **differ** → `CRASHED`
   (the machine rebooted since the lease; the old PID is definitely dead).
4. Probe the PID: absent → `CRASHED`; indeterminate → `UNKNOWN` (fail-safe);
   present → step 5.
5. PID present → defeat PID reuse via start time. Holder + current start both
   readable and **equal** → `ALIVE`; both readable and **differ** → `CRASHED` (PID
   reused by a different process; the original is gone); otherwise (start
   unverifiable) → `UNKNOWN` (assume possibly-alive → refuse).

The classifier never returns `CRASHED` without positive proof, so a live holder is
never misclassified and a session is never wrongly reclaimed.

### 7.2 C2/C3 — `session.py` lease ownership + opt-in reclamation

- `SessionStore.__init__(self, base_dir, *, liveness_probe=None)` stores
  `self._liveness_probe = liveness_probe or process_liveness.REAL_PROBE`.
- `acquire_lock` builds the lock payload with the identity from
  `self._liveness_probe.current()` (host/pid/process_start/boot_id) **plus** the
  existing token/owner/acquired_at/expires_at.
- New `reclaim_crashed: bool = False` parameter. In the existing-lock branch, when
  `now < expires_at` (not TTL-expired), reclaim **only** if
  `reclaim_crashed and classify_holder(existing, probe=self._liveness_probe)
  == CRASHED`; otherwise raise `SessionLockError` exactly as today. The whole
  read→classify→unlink→create sequence stays inside the `flock` guard, so two
  acquirers cannot both reclaim (the loser then sees the fresh live lock and
  refuses).

### 7.3 C4 — `detect_stale_locks` additive recovery report

For each record, in addition to today's `session_id`/`state`/`lock_present`/
`lease_expired`/`tmp_debris`, add:

- `holder_liveness`: `None` when no lock; otherwise
  `classify_lock(existing, probe=...)` (`alive`/`crashed`/`unknown`), or
  `unknown` for an unreadable/garbage lock. A composite supervisor+child lock is
  `crashed` only when both recorded identities are provably crashed.
- `recoverable`: `lease_expired or (reclaimable and holder_liveness == "crashed")`.

Still read-only: no lock taken, nothing removed/rewritten, no signal; the only PID
syscall is the no-op `os.kill(pid, 0)` existence probe. (`doctor` passes no
`sessions_dir`, so `detect_stale_locks` is not called from `doctor` and `doctor`
stays purely local with `stale_locks: []`.)

### 7.4 C5 — `session_runtime.py` opt-in

- `SessionRuntime.__init__(..., reclaim_crashed: bool = True,
  liveness_probe=None)`; pass `liveness_probe` to the `SessionStore`.
- `send` and `close` call `acquire_lock(..., reclaim_crashed=self.reclaim_crashed)`.
  Default-on recovery is safe because reclamation is gated on *provably crashed*;
  `alive`/`unknown` holders refuse, preserving the existing concurrency-safety tests
  (their "held lock" is the live current test process → `ALIVE` → refuse).
  `reclaim_crashed=False` restores strict TTL-only behavior for a caller that wants
  it.

## 8. TDD implementation checklist

TDD is mandatory (`docs/AI_FLOW.md`, user directive): **failing test first → run it
red → minimal code → green → refactor**. Suggested order: C1 → C2/C3 → C4 → C5,
then authority docs.

### C1 — `process_liveness.py` (`tests/test_process_liveness.py`, new)

- [ ] `classify_holder` → `ALIVE` when same host, PID running, start times equal.
- [ ] `classify_holder` → `CRASHED` when PID absent (same host).
- [ ] `classify_holder` → `CRASHED` when PID present but start times differ (PID reuse).
- [ ] `classify_holder` → `CRASHED` when boot ids differ (reboot).
- [ ] `classify_holder` → `UNKNOWN` when host differs.
- [ ] `classify_holder` → `UNKNOWN` when holder has no `pid` (old-format lock).
- [ ] `classify_holder` → `UNKNOWN` when PID present but start time unverifiable.
- [ ] `classify_holder` → `UNKNOWN` when the probe is indeterminate (`is_running` None).
- [ ] `pid_is_running` → `True` for `os.getpid()`, `False` for a guaranteed-free PID
  (real, no injection), and `None`/`False`-safe for `pid <= 0`.
- [ ] `current_identity()` returns a real PID (`os.getpid()`), a non-empty host, and
  a `process_start` consistent with re-reading the current PID where `/proc` exists.

### C2/C3 — lease ownership + reclamation (`tests/test_session_store.py`)

- [ ] `acquire_lock` writes `host`/`pid`/`process_start`/`boot_id` into `lock.json`
  (identity from the injected probe), preserving existing fields.
- [ ] Default `acquire_lock` (no `reclaim_crashed`) still **blocks** a within-TTL
  lock even when the holder is crashed (behavior unchanged) — regression guard.
- [ ] `acquire_lock(reclaim_crashed=True)` **reclaims** a within-TTL lock whose
  holder set classifies `crashed` (injected probe), minting a fresh token.
- [ ] `acquire_lock(reclaim_crashed=True)` **refuses** (`SessionLockError`) a
  within-TTL lock whose holder set classifies `alive`.
- [ ] `acquire_lock(reclaim_crashed=True)` **refuses** a within-TTL lock whose
  holder set classifies `unknown` (fail-safe).
- [ ] Runtime-style composite locks preserve supervisor identity and add child identity;
  child-exited/supervisor-alive and supervisor-exited/child-alive both refuse, while
  both-crashed reclaims.
- [ ] TTL-expired reclamation still works with and without `reclaim_crashed`
  (existing tests stay green).

### C4 — recovery detection (`tests/test_session_store.py`)

- [ ] `detect_stale_locks` reports `holder_liveness == "crashed"` and
  `recoverable is True` for a within-TTL lock held by a crashed holder (injected),
  while `lease_expired` stays `False`.
- [ ] `detect_stale_locks` reports `holder_liveness == "alive"` and
  `recoverable is False` for a live within-TTL lock.
- [ ] `detect_stale_locks` reports `holder_liveness is None` for a record with no
  lock; `recoverable is False`.
- [ ] expired lease → `recoverable is True` regardless of liveness.
- [ ] read-only: lock + record byte-identical after detection (extend existing
  read-only test to cover the liveness path).

### C5 — runtime reclamation (`tests/test_session_runtime.py`)

- [ ] `send` with an injected crashed-holder probe reclaims a within-TTL stale lease
  left by a "crashed" prior turn and runs the turn (lease released afterward).
- [ ] `send` refuses (`SessionLockError`) when the within-TTL lease holder is `alive`
  (no turn executor call, no turn dir).
- [ ] `close` with a crashed-holder probe reclaims a within-TTL stale lease and
  closes; `close` still refuses an `alive`-held lease (existing
  `test_close_refuses_active_lease...` stays green).
- [ ] `SessionRuntime(reclaim_crashed=False)` restores strict TTL-only blocking even
  for a crashed holder.

### Authority-doc + generated updates (after code/tests green)

- [ ] `docs/roadmap/current-status.md`: move `ARS-CRASH-RECOVERY` to "Recently closed
  tails"; note the safe-recovery posture; update the §3 banner/current_mainline line.
- [ ] `docs/roadmap/features.md`: `F-SESSION-001`/`F-STORE-001` evidence for K1
  liveness recovery.
- [ ] `docs/product/prd.md`: FR-5 stale-lock recovery checklist note (crash recovery
  now beyond expired-lease replacement, safe/opt-in).
- [ ] `docs/design/technical-solution.md` §3.6 (store/runtime) + new
  `process_liveness.py` note; `docs/design/architecture.md` §4.1 control point 3 +
  §8.1 tail closure.
- [ ] `docs/design/result-event-schema.md` §6: additive `holder_liveness` /
  `recoverable` keys + precise read-only wording.
- [ ] `python tools/build_docs_index.py --write` and
  `python tools/docs_drift_signal.py --write` (regenerate; never hand-edit
  `docs/INDEX.md`).

## 9. Acceptance criteria

- **Detection:** `detect_stale_locks` classifies each lease holder
  (`alive`/`crashed`/`unknown`/none) and flags `recoverable`, read-only, with no
  terminating signal to recorded holders, no prior-holder kill, and no mutation.
- **Safe recovery:** a within-TTL lease left by a **provably crashed** holder set is
  reclaimable (opt-in); an `alive` or `unknown` holder always refuses; TTL-expired
  reclamation is unchanged. For runtime supervisor+child locks, both identities must
  be provably crashed before liveness recovery can reclaim.
- **Fail-safe:** missing identity, cross-host, unreadable start time, or
  indeterminate probe → `unknown` → never reclaimed via liveness.
- **No unsafe cross-process behavior:** the liveness recovery path sends no terminating
  signal to recorded holders, kills no prior holder, and never takes over a live
  session (asserted via injected probe + argv/no-launch checks); the only PID syscall
  is the no-op existence probe. A pending pre-holder lock (`reclaimable: false`) stays
  TTL-only even if the recorded supervisor PID is gone, preventing takeover of an
  unrecorded child. The runner's own-child fail-closed stop on spawn-callback
  failure is documented separately.
- **Determinism/safety:** `business_verdict` stays `null`; artifacts stay
  `0700`/`0600`, atomic; no secrets (synthetic only); stdlib-only.
- **Existing behavior unchanged:** store/runtime/retention/preflight tests stay green;
  default store `acquire_lock` is TTL-only; doctor stays read-only with
  `stale_locks: []`.
- **Full gates pass** (§10).
- S1 closure, E1, H1, I1 remain intact; `ARS-SANDBOX-BOUNDARY` and concrete caller
  integration stay parked; no non-goal introduced.

## 10. Verification gates

```bash
python3 -m pytest -q tests/test_process_liveness.py tests/test_session_store.py \
  tests/test_session_runtime.py tests/test_retention.py tests/test_preflight.py \
  tests/test_cli_commands.py
python3 -m pytest -q
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

Additional gates:

- Secret-shaped scan over added/changed lines (no real credentials; identity fields
  are host/pid/start/boot only — never secrets).
- Static dangerous-pattern scan over the new liveness surface: confirm `os.kill` is
  only ever called with signal `0` (existence probe), that no `SIGTERM`/`SIGKILL`/
  signal send is introduced in liveness recovery, and that no new network/config-write
  surface is added. Runner signal paths are limited to already-owned child-process
  watchdog/fail-closed handling.

> **Note for the plan step only:** `python tools/build_docs_index.py --check` is
> expected to report this new plan as out-of-date until `docs/INDEX.md` is
> regenerated during implementation; do not regenerate generated files in the plan
> step. `git diff --check` must be clean.

## 11. Files likely to change

Runtime / library:

- `src/agent_run_supervisor/process_liveness.py` — **new** C1 module.
- `src/agent_run_supervisor/session.py` — lease ownership metadata (C2), opt-in
  `reclaim_crashed` (C3), recovery detection keys (C4), `liveness_probe` seam (C6).
- `src/agent_run_supervisor/session_runtime.py` — `reclaim_crashed`/`liveness_probe`
  (C5/C6); pass-through to `acquire_lock`.

Tests:

- `tests/test_process_liveness.py` — **new** C1 classifier/probe tests.
- `tests/test_session_store.py` — C2/C3/C4 lease ownership, reclamation, detection.
- `tests/test_session_runtime.py` — C5 runtime reclamation/refusal.

Docs (authority + generated, updated after code/tests green):

- `docs/design/result-event-schema.md` (§6 additive keys),
  `docs/design/technical-solution.md`, `docs/design/architecture.md`,
  `docs/product/prd.md`, `docs/roadmap/features.md`,
  `docs/roadmap/current-status.md`.
- `docs/INDEX.md`, `docs/lessons/_drift_report.md` — **generated**; via tools only.
- `docs/plans/2026-06-01-k1-crash-recovery-hardening.md` — this file.

## 12. PR / scope strategy

K1 is one cohesive PR (`ai/k1-crash-recovery-hardening-2026-06-01`): a new
`process_liveness.py` module plus additive `session.py`/`session_runtime.py`
integration and additive schema keys. Authority docs are updated in the same PR as
the code they describe; generated indexes are regenerated, not hand-edited. Hermes
controls scope/verification/merge; Codex CLI is primary reviewer.

## 13. Risks / open questions

- **R-1 PID reuse false positive.** Mitigation: `crashed` requires positive proof
  (PID absent, start-time mismatch, or reboot). A present PID with unverifiable start
  time is `unknown` → refuse. So a live holder is never reclaimed.
- **R-2 Platform without `/proc`.** `read_process_start`/`read_boot_id` return
  `None`, so start-time disambiguation is unavailable → a present PID is `unknown` →
  refuse (TTL-only recovery). Liveness recovery degrades safely to today's behavior.
- **R-3 Default-on runtime reclamation.** `SessionRuntime(reclaim_crashed=True)`
  changes the concurrency model for crashed holders only. Mitigation: gated on
  *provably crashed holder sets*; `alive`/`unknown` refuse; composite supervisor+child
  locks require both identities to be crashed; a disable switch
  (`reclaim_crashed=False`) restores strict TTL; existing held-lock tests (live
  holder) stay green; new tests cover all three classifications.
- **R-4 `os.kill(pid, 0)` honesty.** It performs no terminating signal delivery, but
  the H1 detector docstring/schema said "signals no process". Mitigation: reword
  precisely to "delivers no terminating signal to recorded holders and kills no prior
  holder; the only PID syscall is a no-op `os.kill(pid, 0)` existence probe."
- **O-1 Retention liveness-awareness.** A closed session whose `close` crashed could
  hold a within-TTL lease that blocks retention until expiry. K1 deliberately keeps
  retention TTL/live-lock conservative (deletion is higher-stakes than lease
  reclamation). A future, separately-reviewed slice could let retention consult
  liveness; out of K1 scope and documented.

## 14. Rollback strategy

All work lives on `ai/k1-crash-recovery-hardening-2026-06-01` in the isolated
worktree. Pre-merge rollback is branch/worktree discard. Post-merge rollback is
reverting the PR — K1 adds a new `process_liveness.py` module, additive `lock.json`
fields, an opt-in `reclaim_crashed` parameter (default off at the store), and
additive detector keys, so a revert cleanly restores the post-H1 state with no
schema migration (old locks without identity fields already classify `unknown`).
K1 touches no production config, Gateway, Sachima, public ingress, or external
service state; it sends no signal and kills nothing, so there is nothing to roll
back outside the repo.

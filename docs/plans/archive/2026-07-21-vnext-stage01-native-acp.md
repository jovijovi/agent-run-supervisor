---
title: "ARS vNext Stage 0 + Stage 1 development plan — Native ACP vertical (pre-arsd)"
status: archived
created_at: 2026-07-21
last_validated_at: 2026-07-21T20:30:00+0800
archived_at: 2026-07-21
deprecated_reason: "Superseded after PR #65 squash merge and the vNext authority reset"
---
> **Archived; not active development authority.** Retained for audit only. Do not execute this
> plan or use it to select a branch, PR, baseline, scope, or acceptance gate.

# ARS vNext Stage 0 + Stage 1 development plan — Native ACP vertical (pre-arsd)

```text
STATUS: PLAN_READY_FOR_APPROVAL
plan_slug: 2026-07-21-vnext-stage01-native-acp
prepared_by: Claude Code (Architect role; plan-only, read-only inspection)
prepared_at: 2026-07-21
revision: 2 (2026-07-21 focused repair — explicit native store-isolation seam in C6/C8 with L1/L2 regressions and a structural call-site guard; no other decision reopened)
baseline: origin/main 2b099943f15783a058814713e7c2a35acb81a2b6 (tag v0.1.7), clean, no open PR, Verify green
design_authority: Rev3 sha256 a088b208b9494b94a028d912127a373b1ae0831e31476e8695dbf3e26c2e4bc1 (authoritative; this plan does not reopen it)
scope: Stage 0 + Stage 1 only; Stage 2 (arsd) appears only as preserved seams
inspection_method: read-only file inspection of the release-v0.1.7 worktree; no command execution, no edits
```

All `file:line` references below were verified by direct read at commit 2b09994. The implementer re-verifies them at DoR (gate G4) because origin/main may move between plan approval and execution. The two store-constructor anchors added in revision 2 (`SessionStore`, `EventStore` explicit base-dir construction) are pinned by symbol via the added G4 greps rather than by line.

---

## 0. Preflight position statement (CLAUDE.md / AI_FLOW preflight)

- **Product position.** Local one-shot exec and local persistent-session lifecycles are implemented and closed (board snapshot, `docs/roadmap/current-status.md:37`). v0.1.7 is released; runtime is stdlib-only with zero `[project.dependencies]` (`pyproject.toml:26`). Since the 2026-07-21 G2 authority refresh, `GOAL.md`/PRD record that the released product contains no daemon while the settled vNext target (`ars-core` + thin local `arsd` over a Unix domain socket) is an approved documentation target.
- **Phase target.** ARS vNext per Rev3: a Native ACP vertical (SDK `agent-client-protocol==0.11.0`, OpenCode 1.18.4, Kimi K3/`max`) built additively beside the unchanged acpx legacy paths. This plan covers Stage 0 (consuming dependency lock gate) and Stage 1 (Native vertical through ars-core, B-grade evidence). arsd/Stage 2 is out of scope except for preserved seams.
- **Open tails / authority conflicts (planned explicitly, not silently resolved).**
  1. ~~`GOAL.md` "not a daemon" vs the chair-confirmed arsd production ingress~~ — **resolved.** G2 authority alignment is completed by the 2026-07-21 authority-document refresh on this branch: GOAL/PRD/architecture/technical-solution now record the settled arsd/cgroup production form as an approved documentation target while preserving the v0.1.7 implemented reality. Implementation gates remain separate — G2's docs component grants no source, dependency, service, or deployment authorization (those stay with G1/§8 and Stage-2 approvals).
  2. ~~`docs/roadmap/current-status.md` named `2026-07-08-permissioned-session-goal-noop.md` as the active plan although its work was merged and released in v0.1.7~~ — **resolved.** Slice C0 was approved and executed on this branch (2026-07-21): the stale plan is archived, the S2 board state is closed, and the board's `active_plan:` now points at this plan. C0 is a completed docs prerequisite and is not re-executed (§8).
  3. Standing non-approvals (`docs/roadmap/non-approvals.md`) all remain: no Sachima behavior integration, no real automatic replies, no public ingress, no IM delivery, no Gateway lifecycle, no production config writes, no live/default-on behavior, no worker auto-routing, no `@all`, no agent-to-agent routing, no treating `allowed_roots` as a sandbox, no per-run human approval as default authorization.
- **Is the requested task allowed?** Producing this plan was read-only and allowed, and the documentation prerequisites are complete: C0 (board activation) and the 2026-07-21 G2 authority alignment are done on this branch. Executing the implementation is **not yet allowed**: slices C1–C10 — including the `agent-client-protocol` dependency change (G1) — require the explicit §8 item 1 approval. Nothing in this document authorizes edits by itself.

---

## 1. Task contract

### 1.1 Objective

Implement, on a dedicated worktree branch off fresh `origin/main`, the Rev3 Stage 0 + Stage 1 scope:

1. **Stage 0** — add `agent-client-protocol==0.11.0` as a narrow `native` extra with lock/CI sync; pin the actual SDK import origin, symbols, and I/O model with contract tests; audit every `AgentRunStatus` consumer and settle additive-enum vs Native-superset (G5) on evidence, without compatibility theater.
2. **Stage 1** — the Native vertical inside `agent_run_supervisor`: supervised live-process surface (`ManagedProcess`), `native_acp/` (spec/profile/storage/driver/config-fidelity/client/permissions/events/run_task), Run/Session/Turn state with terminal `unknown` (persistent `retryable=false`) and persistent Session `quarantined`, double dispatch markers, same-external-session across process-per-Run via `session/load`, controlled cross-Run model/effort switching with exact readback and rollback/quarantine, default-deny permission mediation, isolated Native store roots (`.agent-run-supervisor/native-runs/`, `.agent-run-supervisor/native-sessions/`) bound through the explicit `native_acp/storage.py` seam, normalized events/evidence/redaction/bounded stderr/queues, full L1/L2 deterministic coverage, and opt-in real OpenCode 1.18.4 B-grade smokes proving context-token continuity and a real model switch plus an effort switch.

### 1.2 Done criteria

**Stage 0 done when:**
- `native` extra + `uv.lock` + `Makefile`/CI sync landed; `uv lock --check` green.
- SDK contract tests green: distribution version `0.11.0`, import origin inside the branch `.venv`, all required symbols pinned (initialize, session new/**load**/close, set-config, prompt, **cancel**, permission/fs client callbacks, config-option response/update types, stop reasons), and the connection's stream/I-O model recorded.
- G5 consumer audit recorded with a decision (additive extension vs Native superset + lossless mapping), with the grep evidence in the commit message.
- Full verify ladder green; base install (no extra) unaffected (wheel smoke green).

**Stage 1 done when:**
- Slices C2–C10 committed on the branch; full suite green (existing suite — count recorded at DoR baseline — plus all new L1/L2 tests); `make verify` green end-to-end.
- Gates G4–G8 satisfied with recorded evidence; G3/G6 satisfied by the real smokes.
- Real B-grade smokes passed against real OpenCode 1.18.4: (a) S1-equivalent read-only run with exact k3/`max` readback and empty-workspace pre/post assertions; (b) nonce-recall continuity across two Runs on one external session via `session/load` (context-token continuity — the zero-prompt cross-process probe proved transport/load only and does **not** satisfy this); (c) at least one real model-ID switch **and** one effort switch with exact readbacks. Missing prerequisites are escalated, never silently downgraded.
- acpx-unchanged proof: `git diff main...HEAD -- src/agent_run_supervisor/runner.py src/agent_run_supervisor/parser.py src/agent_run_supervisor/policy.py src/agent_run_supervisor/session_runtime.py` is empty; legacy tests unmodified and green; byte-identical serialization golden for pre-existing session records passes.
- Store-isolation proof green: the C6 L1 isolation suite (`tests/native_acp/test_native_store_isolation.py`) and the C8 L2 seeded-legacy vertical pass — Native operations bind exclusively to `.agent-run-supervisor/native-runs/` and `.agent-run-supervisor/native-sessions/` through the `native_acp/storage.py` seam; same-ID legacy/Native sessions and runs coexist without collision; pre-seeded legacy `sessions/`/`runs/` bytes and directory listings are unchanged (poisoned legacy records provably unread); the structural call-site guard passes.
- Explicitly **not** part of done: PR creation/merge, release/tag/PyPI, CHANGELOG release sections, any arsd code, any service/deployment change, any Sachima change. (The G2 GOAL/PRD authority alignment was completed separately by the 2026-07-21 docs refresh; it is a docs deliverable, not a C1–C10 code deliverable.)

### 1.3 Hard constraints

- **Additive only.** `execute_subprocess`/`SubprocessOutcome` (`runner.py:462`, `runner.py:55`) stay byte-identical, acpx-only. No acpx parser migration. No changes to `policy.py` compilers. No shared session store between acpx and Native: Native uses new roots `.agent-run-supervisor/native-runs/` and `.agent-run-supervisor/native-sessions/`; existing `runs/`/`sessions/` artifacts are never read, rewritten, or migrated. Store isolation is a coded seam, not a convention: Native stores are constructed **only** through `native_acp/storage.py` (C6), every Native session/run operation receives a store bound to a `native-*` root, and the C6/C8 isolation regressions plus a structural call-site guard prove zero legacy-root reads/writes and same-ID no-collision.
- **No fallback.** Native failure never routes to acpx. Enforced structurally (no imports of `policy`/`parser` from `native_acp/` — pinned by a coupling test).
- **Fail-closed config fidelity.** Exact-or-zero: literal `model=kimi-for-coding/k3`, `effort=max`; no alias/coercion/nearest-option; any fidelity failure ⇒ 0 Turn, no prompt.
- **Terminal-state contract.** `unknown` round-trips end-to-end with persistent `retryable=false`; terminal states are irreversible; `retry_of_run_id` never rewrites the original Run; no auto-retry/replay/resume anywhere.
- **Secrets.** Credential values only via profile env allowlist at spawn; never serialized (`repr`-safe, test-pinned); `[REDACTED]` in docs; static safety scan green.
- **Evidence tiers.** Handoff/reviewer-seat probes are A-grade context only; Stage-1 direct-drive real smokes are B-grade; nothing here claims C-grade production acceptance (that is Stage 2, S1–S5, out of scope).
- **Process discipline.** TDD per slice (write test, run the RED command, observe the stated failure mode, implement minimal GREEN); conventional commits with a `Verification:` block (`docs/AI_FLOW.md` §Commit conventions); no `make bump`; no tag/publish; no edits before DoR completes.
- **Plan-text discipline.** This file, once committed, is scanned by `tools/static_safety_scan.py`; it deliberately avoids the stale-acceptance phrase patterns and contains no secret-shaped strings.

### 1.4 Authority and non-approvals

This plan implements Rev3 decisions; it does not reopen them (process-per-Run, no per-Run Worker, no Native→acpx fallback, single-supervision, arsd-as-production-ingress being settled architecture, K3 literal `max`, evidence tiers). Standing non-approvals in §0 apply unchanged. Authorization is strictly the approval package in §8; each listed approval is separate and non-transitive.

---

## 2. Start procedure after approval (Definition of Ready)

Run from the canonical repo, in order. Any failure ⇒ stop and report; no edits before every box is checked.

```bash
# 1. Fresh remote check
cd /home/ecs-user/workspace/hermes/repo/agent-run-supervisor
git fetch origin --prune
git rev-parse origin/main
#    Expected: 2b099943f15783a058814713e7c2a35acb81a2b6 (v0.1.7).
#    If different: STOP. Re-run the G4 fresh-check against the new HEAD, report the
#    delta (files/symbols this plan touches), and get re-confirmation before proceeding.

# 2. Existing dedicated worktree + branch (AI_FLOW branch model: feat/ prefix)
#    Already created from the v0.1.7 baseline for the completed C0/G2 docs work.
#    Reuse it — do not re-create it or re-run `git worktree add`; if it is missing
#    or not on feat/native-acp-stage01, STOP and report.
cd /home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/feat-native-acp-stage01
git rev-parse --abbrev-ref HEAD       # must print: feat/native-acp-stage01

# 3. Clean guard
git status --porcelain        # must be empty

# 4. Worktree-local CodeGraph
#    Initialize a CodeGraph index rooted in THIS worktree using the operator-standard
#    invocation, then check its status. Acceptance: index root == this worktree path,
#    .venv excluded. Never reuse the existing 74c04fc-era index (different tree).

# 5. Environment baseline (pre-change)
uv sync --extra dev --extra release
uv lock --check
uv run pytest -q                                   # record the exact passing count as DoR baseline
uv run python -m compileall -q src scripts tests

# 6. G4 symbol fresh-check (read-only) — confirm this plan's anchors still hold:
grep -n "class AgentRunStatus" src/agent_run_supervisor/exit_classifier.py
grep -n "def execute_subprocess" src/agent_run_supervisor/runner.py
grep -n "class SessionRecord" src/agent_run_supervisor/session.py
grep -n "class SessionStore" src/agent_run_supervisor/session.py
grep -n "class EventStore" src/agent_run_supervisor/event_store.py
grep -n "_KNOWN_TURN_STATUSES" src/agent_run_supervisor/session_inspect.py
grep -rn "AgentRunStatus" src tests --include='*.py' -l
grep -rn "SessionRecord\|validate_binding" src --include='*.py' -l
```

DoR is complete when: the existing worktree is on `feat/native-acp-stage01` (v0.1.7 baseline plus the completed C0/G2 docs commits) and the step-1 fresh-remote check passed; status clean; CodeGraph worktree-local and healthy; baseline suite/compileall/lock-check green with the baseline count recorded; G4 greps match this plan — including the explicit-base-dir constructor surfaces of `SessionStore` and `EventStore` on which the C6 storage seam relies — (or the delta is reported and re-confirmed).

---

## 3. Branch activation and Stage 0

### Slice C0 — docs-governance: close stale board state, activate this plan

*The approved resolution of authority conflict #2 (§0). Docs-only; no runtime change. **Completed 2026-07-21** on this branch; kept as the historical slice record and not re-executed (§8).*

- **Inspect:** `docs/roadmap/current-status.md` (snapshot `:37-42`, phase index `:61`, `active_plan:` `:15`), `docs/plans/active/2026-07-08-permissioned-session-goal-noop.md` (all acceptance boxes checked), `docs/plans/README.md`, `tools/check_roadmap_governance.py` (read to learn its exact enforcement before editing).
- **Change:**
  - `git mv docs/plans/active/2026-07-08-permissioned-session-goal-noop.md docs/plans/archive/`; set frontmatter `status: archived`.
  - Add `docs/plans/active/2026-07-21-vnext-stage01-native-acp.md` (this document; frontmatter `title`, `status: active`, `created_at: 2026-07-21`).
  - Update `docs/roadmap/current-status.md`: S2 row → Closed (link to the archived plan; if `check_roadmap_governance.py` requires a phase-archive file, add a minimal `docs/roadmap/archive/phases/s2-permissioned-session.md` acceptance summary derived from the archived plan's checked boxes); `active_plan:` → this plan; snapshot rewritten to the §0 position. `features.md` rows are left for post-merge doc sync (noted, not forgotten).
  - `uv run python tools/build_docs_index.py --write` and `uv run python tools/docs_drift_signal.py --write` (never hand-edit `docs/INDEX.md`). If the drift report names knowledge docs cited by this change, process them per the CLAUDE.md validation rule.
- **Verify:** `uv run python tools/build_docs_index.py --check`; `docs_drift_signal.py --check`; `check_roadmap_governance.py`; `static_safety_scan.py`; `uv run pytest -q` (unchanged); `git diff --check`.
- **Commit:** `docs(roadmap): close S2 board state and activate vNext Stage 0/1 native-ACP plan`

### Slice C1 — Stage 0: consuming dependency lock gate + SDK contract + G5 audit

*No product behavior. Gap ⇒ BLOCKED report; acpx untouched.*

- **Inspect:** `pyproject.toml:34-36` (`dev`/`release` extras), `uv.lock`, `Makefile:20-21` (`sync`), `.github/workflows/verify.yml:40-41` (Install step; also the coverage job's install), `scripts/smoke_installed_wheel.sh`.
- **Change:**
  - `pyproject.toml`: add `[project.optional-dependencies] native = ["agent-client-protocol==0.11.0"]` (exact pin, narrow extra; base `dependencies` stays `[]`).
  - `uv lock`; then `uv sync --extra dev --extra release --extra native`.
  - `Makefile` `sync` target and `verify.yml` install steps: append `--extra native` so CI runs the Native L1/L2 suite. If lock resolution fails on part of the 3.11–3.14 matrix (SDK/transitive support), stop and report options (conditional matrix vs escalation) — do not silently shrink the matrix.
- **Create:** `tests/native_acp/__init__.py`; `tests/native_acp/test_sdk_contract.py`:
  - `importlib.metadata.version("agent-client-protocol") == "0.11.0"`.
  - Resolve the distribution's top-level import name via `importlib.metadata` (do not assume `acp`); import it; assert `__file__` resolves inside this worktree's `.venv` (import-origin pin, same evidence class as the prior `sdk-runtime.txt`).
  - Pin symbols/types required by Rev3: client-side connection class (expected `ClientSideConnection`), `Client` callback surface (session update, permission request, fs), initialize / session new / **session load** / session close / set-config / prompt / **cancel** entry points, config-option carriers (expected `NewSessionResponse.config_options`, `SetSessionConfigOptionResponse.config_options`, `ConfigOptionUpdate`), stop-reason vocabulary incl. `end_turn`.
  - Record the connection constructor's stream/I-O model (asyncio streams vs raw pipes) — this pins the C3 stream surface.
  - Where an actual name differs from Rev3's expectation but is semantically equivalent, the test pins the **actual** name and the same commit updates this plan's symbol table with a drift note; a semantic gap (e.g., no session-load API) is a Stage-0 stop (§5).
- **G5 audit (read-only, recorded in this commit's message and the plan checklist):** `grep -rn "AgentRunStatus" src tests`. Consumers verified at 2b09994: `exit_classifier.py:7` (defining enum; `_RETRYABLE_DEFAULT:21` dict), `result.py:19,66` (`build_result_payload`, `_ERROR_CODE_FOR_STATUS.get`), `runner.py:215` (dry-run) + classification passthrough, `session_runtime.py:49-53,132` (turn classification, `SessionTurnOutcome.status`), `session_inspect.py:63` (`_KNOWN_TURN_STATUSES` — derived from the enum, auto-adapts), `caller.py`/`hermes_caller/verdict.py:18` (status as opaque string evidence; `_SUCCESS_STATUSES={"completed"}` treats unknown values as non-success — tolerant), tests. Sachima consumes the released 0.1.7 wheel and never sees Native stores in Stage 0/1. **Decision rule:** if no consumer exhaustively enumerates or misbehaves on new members → choose the **additive extension** (default, matches this evidence); otherwise choose the Native-side superset enum + lossless mapping and record why. The chosen branch is implemented in C2, not here.
- **RED:** `uv run pytest tests/native_acp/test_sdk_contract.py -q` before the pyproject/lock change → fails with `importlib.metadata.PackageNotFoundError: agent-client-protocol` (SDK absent from the venv).
- **GREEN:** after extra+lock+sync the contract test passes; `uv lock --check` green; wheel smoke (base install, no extra) still green.
- **Verify:** focused test; `uv run pytest -q`; `compileall`; `make verify`; `git diff --check`.
- **Commit:** `feat(packaging): add narrow native extra pinning agent-client-protocol==0.11.0 with SDK contract tests`

**Stage 0 exit:** contract tests green, G5 decision recorded, ladder green. Any SDK gap ⇒ BLOCKED report (exact missing symbol/behavior + candidate resolutions), no workaround.

---

## 4. Stage 1 — slices, dependency graph, per-slice contract

### 4.0 Dependency graph and commit order

```text
C0 docs-governance
C1 Stage 0 (SDK lock + contract + G5 decision)
 ├──> C2 terminal-status carrier (G5 implementation)          [independent of C3–C4]
 ├──> C3 ManagedProcess surface (needs C1's I/O-model pin)
 └──> C4 native_acp spec + profile (stdlib-only)
C3 + C4 ──> C5 driver + client + config-fidelity + fake agent (SDK-dependent)
C4 ──> C6 session-layer additive fields + native store seam (storage.py; C2's status strings for tests)
C4 ──> C7 permission bridge + event normalizer + bounded writer
C2,C3,C4,C5,C6,C7 ──> C8 RunTask vertical (new-session path, finalization table, markers, isolated stores)
C8 ──> C9 session/load reuse + cross-Run switching + rollback/quarantine
C9 ──> C10 real OpenCode B-grade smokes (gated by G3/G6)
```

C2/C3/C4 are mutually independent and may be developed in any order after C1; C6 additionally needs only C4's package scaffold (`native_acp/__init__.py`) plus C2's status strings for its tests; the listed order keeps each commit's test suite self-contained. One branch, linear commits, no merge commits.

### 4.1 Slice C2 — terminal-status carrier (G5 implementation; additive branch as decided default)

- **Goal:** carry the Native terminal set `completed | failed | cancelled | timed_out | unknown` (Rev3 CORE_DECISIONS 6) with `unknown` round-tripping and persistent `retryable=false`. `completed`/`timed_out` already exist; the additive branch adds exactly the missing three members. (This member set is the settled Rev3 terminal vocabulary, not a new decision; Rev3 FILES names `UNKNOWN` explicitly and the other two follow from the same clause.)
- **Inspect:** `exit_classifier.py:7-45` (`AgentRunStatus`, `_RETRYABLE_DEFAULT`, `_BASE_STATUS_FOR_EXIT`), `exit_classifier.py:79` (`classify_exit`), `result.py:19-82` (`build_result_payload`, `_ERROR_CODE_FOR_STATUS`), `session_inspect.py:63`, `hermes_caller/verdict.py:18`.
- **Modify (additive branch):** `exit_classifier.py` — add `FAILED = "failed"`, `CANCELLED = "cancelled"`, `UNKNOWN = "unknown"`; extend `_RETRYABLE_DEFAULT` with all three → `False` (UNKNOWN hard-`False` per contract); `classify_exit` logic untouched. `result.py` — `_ERROR_CODE_FOR_STATUS` entries `FAILED→"FAILED"`, `CANCELLED→"CANCELLED"`, `UNKNOWN→"UNKNOWN"`.
  *(Superset fallback if C1's audit flipped the decision: create `native_acp/status.py` with `NativeRunStatus`, a lossless mapping for the four non-unknown values, and a schema-compatible payload writer; same tests reshaped; `exit_classifier.py`/`result.py` untouched.)*
- **Tests:** create `tests/native_acp/test_status_vocabulary.py`:
  - `AgentRunStatus("unknown")` constructs; `.value` round-trips through JSON.
  - `build_result_payload(status=UNKNOWN, retryable=False, ...)` → `status=="unknown"`, `retryable is False`, `error_code=="UNKNOWN"`; same for `failed`/`cancelled`.
  - Zero-coercion guard: a bounded sweep of `ClassifierInput` (exit codes 0–5,130, plus flag combinations) shows `classify_exit` never returns any new member — acpx classification is provably unchanged.
  - `session_inspect._KNOWN_TURN_STATUSES` now contains the new values (documents the deliberate vocabulary growth of the derived frozenset).
  - Existing `tests/test_exit_classifier.py` untouched and green (regression anchor).
- **RED:** `uv run pytest tests/native_acp/test_status_vocabulary.py -q` → `ValueError: 'unknown' is not a valid AgentRunStatus`.
- **GREEN:** members + table entries only; no behavior change elsewhere.
- **Verify:** focused + `uv run pytest -q` + compileall.
- **Commit:** `feat(status): additive terminal-status vocabulary (failed/cancelled/unknown) for native runs`

### 4.2 Slice C3 — supervised live-process surface (`ManagedProcess`)

- **Goal:** the Rev3 process-ownership triangle: supervision layer owns spawn/PID/PGID/identity/timeout/signal/reap; the SDK will own the live stdin/stdout wire; completion-oriented `execute_subprocess` stays acpx-only and untouched.
- **Inspect:** `runner.py:462-757` (`execute_subprocess`, `_terminate_process`, `_kill_process`, `start_new_session`, SIGTERM→grace→SIGKILL escalation, `on_spawn` fail-closed at `:505-526`), `process_liveness.py:37,122` (`ProcessIdentity`, `identity_for_pid`).
- **Create:** `src/agent_run_supervisor/managed_process.py` (new shared module — chosen over editing `runner.py` so the acpx runner diff is provably empty):
  - `async def spawn_managed_process(*, argv, cwd, env, limits, on_spawn=None) -> ManagedProcess` — POSIX-only (`start_new_session=True` mandatory; clear error otherwise); on_spawn failure ⇒ fail-closed group kill (mirrors `runner.py:505` semantics).
  - `class ManagedProcess`: `identity: ProcessIdentity` (via `identity_for_pid`), `pgid`; `stdin`/`stdout` exposed in the stream form pinned by C1's I/O-model test (expected asyncio streams; raw pipe FDs as the pinned alternative) and handed exclusively to the SDK; `stderr` drained by a supervisor task into a bounded buffer (`max_stderr_bytes` + truncation marker — never by the SDK); `async wait() -> ManagedExit` (exit code/signal/kill metadata; never drains stdout; reaps the child); `terminate_group()` / `kill_group()` (killpg SIGTERM → `cancel_grace` → SIGKILL, same escalation semantics as `runner.py:736-757` reimplemented at pgid level without touching runner internals); startup/turn timeouts are owned by the caller (`RunTask`) via `asyncio.wait_for`.
- **Tests:** create `tests/native_acp/test_managed_process.py` (stdlib child processes, no SDK, runs without the extra): live wire (write stdin → read stdout while running — proves non-completion-oriented I/O and single stdout consumer); identity captured and `pgid == pid` (new session); bounded stderr with truncation flag; group termination kills a child-spawned grandchild; escalation on a SIGTERM-ignoring child; `wait()` reaps (no zombie); on_spawn-failure fail-closed.
- **RED:** `uv run pytest tests/native_acp/test_managed_process.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.managed_process'`.
- **GREEN:** minimal implementation passing the suite; `runner.py` diff empty.
- **Verify:** focused + full suite + compileall. Contributes G7 evidence (spawn/ownership half).
- **Commit:** `feat(process): supervised live-process surface (spawn_managed_process/ManagedProcess) for native ACP`

### 4.3 Slice C4 — `native_acp` spec + profile (freeze order)

- **Goal:** the Rev3 admission freeze order: resolve profile revision/snapshot/hash + config-schema hash + grant/role/workspace/MCP/credential-ref hashes → materialize `ResolvedLaunchSpec` → seal `AgentRunSpec`/`spec_hash` → only then spawn; `EffectiveRunState` holds observations only and never writes back.
- **Inspect:** `workspace.py:40,71` (`validate_effective_cwd`, `workspace_hash` — binding-config hash, explicitly **not** content evidence), `role.py:11` (`PERMISSION_KINDS` vocabulary for grant capability naming), `event_store.py:110` (`exclusive_create_bytes` for later spec.json write).
- **Create:** `src/agent_run_supervisor/native_acp/__init__.py` (no eager SDK import; stdlib modules import cleanly without the extra; SDK-needing modules raise a typed `NativeSdkUnavailableError` only on use), `native_acp/spec.py`, `native_acp/profile.py`:
  - `spec.py`: `AgentRunRequest` (wire input), `AgentRunSpec` (frozen dataclass; fields per Rev3: schema_version, input refs+hashes, session{reuse, ars_session_id, expected_binding_hash}, agent{profile_id, profile_revision, profile_snapshot_ref, profile_hash, config_schema_hash}, execution_grant{grant_hash, role_hash, capabilities}, workspace{canonical_root, cwd, workspace_hash}, runtime{model_id, config{effort}, config_fidelity="exact"}, bindings{mcp snapshot hashes, credential_refs}, limits, evidence/recovery policy hashes, launch_spec_hash), `ResolvedLaunchSpec` (executable, fixed argv with only `<effective_cwd>` substitution, env-allowlist slot names + credential refs — **never values**, transport="stdio"), `EffectiveRunState` (observations only: ProcessIdentity, agent_info, protocol/capabilities incl. session-load, external agent_session_id, discovery snapshots, readback model/effort), `spec_hash()` (sha256 over canonical JSON excluding control-plane fields: run_id, namespace, owner, submitted_at, retry_of_run_id).
  - `profile.py`: `AgentProfile` (typed: revision/hash/snapshot, executable ref, argv template, credential env allowlist, selector names `configId=model`/`configId=effort`, capability flags incl. `requires_session_load`), `ProfileRegistry` (code-registered closed set; unknown id ⇒ error), `OPENCODE_1_18_4` (id `opencode-1.18.4`; executable default `/home/linuxbrew/.linuxbrew/bin/opencode`, subcommand `acp`; literal `model=kimi-for-coding/k3`, `effort=max`; `requires_session_load=True`). No command/argv/env/JSON passthrough anywhere.
- **Tests:** create `tests/native_acp/test_spec.py`, `tests/native_acp/test_profile.py`: freeze-order enforcement (spec cannot seal without a resolved profile snapshot + hashes; sealing twice fails; sealed spec immutable); `spec_hash` golden stability + control-plane exclusion (changing run_id doesn't change the hash; changing any input hash does); `ResolvedLaunchSpec` serialization contains slot names only (adversarial env value never appears in `repr`/JSON); argv template substitutes only `<effective_cwd>`; registry closed-set; `OPENCODE_1_18_4` literals pinned. Plus `tests/native_acp/test_no_acpx_coupling.py`: `native_acp` modules import neither `agent_run_supervisor.policy` nor `agent_run_supervisor.parser` (structural no-fallback pin, extended in later slices).
- **RED:** `uv run pytest tests/native_acp/test_spec.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp'`.
- **GREEN:** minimal dataclasses/hashing passing; no SDK import.
- **Commit:** `feat(native-acp): frozen run spec, resolved launch spec, profile registry (OPENCODE_1_18_4)`

### 4.4 Slice C5 — driver + client callbacks + config-fidelity machine + fake-agent L2

- **Goal:** `NativeAcpDriver` owns wire + ACP state machine only (never spawns); single-Run exact sequence per Rev3 CONFIG A (initialize → session new/load → discovery → set model → consume the complete model-dependent set → rediscover effort in the fresh set only → set effort → consume complete set + updates → exact verify → ready-to-prompt); fail-closed everywhere.
- **Inspect:** C1's pinned SDK symbols; `docs/design/result-event-schema.md` (event families, for the client's update hook shapes).
- **Create:** `native_acp/driver.py` (`NativeAcpDriver`: `open(proc: ManagedProcess)`, `initialize()`, `new_session(...)`, `load_session(agent_session_id)`, `set_config_exact(...)`, `prompt_once(...)`, `cancel()`, `close()`), `native_acp/client.py` (SDK `Client` implementation translating callbacks — session updates, permission requests, fs — into internal normalized shapes consumed by C7's bridge/normalizer; keeps SDK types out of the mapper modules), `native_acp/config_fidelity.py` (`ConfigFidelityMachine` — the sequence above as an explicit state machine; any violation ⇒ `ConfigFidelityError` ⇒ 0 Turn; constructed so the post-set-model option set is the only source for effort discovery — skipping rediscovery is structurally impossible).
- **Create (test asset):** `tests/native_acp/fake_agent.py` — an in-repo fake ACP agent **subprocess** speaking real stdio JSON-RPC framing (scripted via argv/env: advertised option sets pre/post model-set, readback values, fault injections: malformed frame, omit-effort, wrong readback, hang, exit-at-phase, silent-new-on-load). It is spawned through `ManagedProcess` (never a driver mock). L2-only; never production evidence.
- **Tests:** create `tests/native_acp/test_driver_config_fidelity.py` (guarded by `pytest.importorskip` on the pinned SDK module so environments without the extra skip cleanly; CI runs it): happy exact sequence reaches ready-to-prompt with recorded discovery snapshots; effort absent from post-model set ⇒ fail-closed, zero `session/prompt` frames observed by the fake; pre/post option sets differ and the machine provably binds the post set; inexact readback ⇒ error, no prompt; malformed frame ⇒ controlled driver error; child killed mid-handshake ⇒ controlled error; `cancel()` path; initialize capability recording (incl. `loadSession`).
- **RED:** `uv run pytest tests/native_acp/test_driver_config_fidelity.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp.driver'`.
- **GREEN:** driver/client/machine minimal implementation over the fake agent.
- **Verify:** focused + full suite. G7 wire-ownership evidence (exclusive SDK wire, single stdout consumer, cancel) at L2.
- **Commit:** `feat(native-acp): SDK driver, client callbacks, exact config-fidelity machine with fake-agent L2 suite`

### 4.5 Slice C6 — session layer: native identity, `quarantined`, `last_effective_*`, isolated `native-sessions/` store seam

- **Goal:** Rev3 DATA session model — stable Session identity separated from per-Run model/effort; persistent `quarantined`; model/effort as mutable observations; acpx records byte-identical — **plus the explicit store-isolation seam:** every Native session operation binds to `.agent-run-supervisor/native-sessions/` through one narrow constructor helper, an implicit default into legacy `sessions/` is structurally impossible (test-pinned), and legacy code is never taught about Native roots.
- **Inspect:** `session.py:95-116` (`SessionRecord`), `:48-49` (`STATE_OPEN/CLOSED`), `:266` (`ensure_open`), `:278` (`mark_closed`), `:299-338` (`validate_binding` — full `role_hash` incl. `runner.model`, confirming current-main cannot express model switching), `:602-612` (`_record_to_dict` omit-when-unset pattern), `:650` (`_record_from_dict` tolerant reads), `:446-595` (lease surface: `acquire_lock` with `reclaimable=False`, `update_lock_holder` child identity, `release_lock`, `detect_stale_locks`), `process_liveness.py:216` (`classify_lock`); additionally the `SessionStore` explicit-base-dir constructor surface in `session.py` and the `EventStore` explicit-base-dir constructor surface in `event_store.py` (both symbol-pinned by the added G4 greps; the seam passes explicit base dirs and requires **no change** to either constructor or to any store default).
- **Create:** `src/agent_run_supervisor/native_acp/storage.py` — the store-isolation binding seam (stdlib-only; no SDK import; importable without the extra; deliberately two root-binding constructors, **not** a storage abstraction):
  - `NATIVE_SESSIONS_DIRNAME = "native-sessions"` and `NATIVE_RUNS_DIRNAME = "native-runs"` — the only place in `src/` where these directory names are spelled.
  - `native_session_store(supervisor_root) -> SessionStore` — returns a `SessionStore` constructed explicitly on `<supervisor_root>/native-sessions`; performs no discovery of its own (the caller passes the already-resolved `.agent-run-supervisor` control root, the same value legacy code resolves for its own stores).
  - `native_event_store(supervisor_root) -> EventStore` — the same contract for `<supervisor_root>/native-runs` (consumed by C8).
  - **Constructor contract (binding seam):** these two helpers are the only sanctioned way Native code obtains stores. Direct `SessionStore(...)`/`EventStore(...)` construction anywhere in `native_acp/` outside `storage.py` is a test failure (structural guard below). No Native call path may rely on any store default root, and `storage.py` itself never references legacy `sessions`/`runs` path segments.
  - All Native create/open/list, lease (`acquire_lock`/`update_lock_holder`/`release_lock`), quarantine (`mark_quarantined`), and `commit_last_effective` operations in this and every later slice execute against a store instance obtained through this seam; the operations themselves stay root-agnostic (below) — isolation comes entirely from construction.
- **Modify (`session.py`, additive; existing functions untouched unless listed):**
  - `SessionRecord` new optional fields, all defaulting `None` and omitted when unset (exact `mcp_config_*` pattern at `:602`): `session_kind` (absent ⇒ legacy acpx; `"native"` for native records), `native_profile_id`, `native_profile_revision`, `native_profile_hash`, `agent_session_id` (external), `owner`, `last_effective_model`, `last_effective_effort`, `quarantine_reason`, `quarantined_by_run_id`.
  - New constant `STATE_QUARANTINED = "quarantined"`; new `SessionQuarantinedError(SessionClosedError)` so existing acpx catch-sites still catch; `ensure_open` raises it when `state == STATE_QUARANTINED` (acpx records never carry that state — no acpx behavior change).
  - New `validate_native_binding(record, *, profile, workspace_result, owner)` — hard fail-closed on profile/agent-type, workspace, owner mismatch, or `quarantined`; **model/effort differences are not a mismatch** (a new Run's frozen Spec is the legitimate switching input). Existing `validate_binding` untouched.
  - New `mark_quarantined(session_id, *, reason, run_id, now=None)` — guard-serialized like `mark_closed:278`, irreversible (no un-quarantine API; v1 contract), idempotent-safe.
  - New `commit_last_effective(session_id, *, model, effort, now=None)` — atomic record update; **commit timing contract:** called only after an exact-readback success inside the owning Run (post-readback, pre-prompt), and after a proven rollback (restoring the prior pair). Never written from agent-side drift; drift observed without a local set is a `config_drift_observed` evidence event (C8/C9), not a record write.
  - **Root-agnosticism note:** the three new operations are store-scoped like the existing lease/close surface — they act on whatever base dir their `SessionStore` was constructed with and contain no root-resolution logic. No acpx call site constructs a `native-sessions/` store and no acpx call site is edited, so the new state/fields remain unreachable from legacy paths; store defaults in `session.py` are not modified.
- **Tests:** create `tests/native_acp/test_native_session_record.py`: byte-identical golden — a 0.1.7-shaped acpx record serializes to the exact pre-change bytes (zero-migration proof); native record round-trip with omit-when-unset; `validate_native_binding` matrix (profile/workspace/owner/quarantined ⇒ fail; model/effort delta ⇒ pass); quarantine irreversibility + `ensure_open` refusal + lease refusal for quarantined; `commit_last_effective` atomicity. Existing session tests (exact filenames enumerated at DoR via `ls tests`) stay green. **Plus create `tests/native_acp/test_native_store_isolation.py` (L1 isolation regression + structural guard):**
  - Seam binding: `native_session_store(root)` yields a store rooted at `root/native-sessions` and `native_event_store(root)` at `root/native-runs` (asserted via the store's actual recorded base-dir attribute, pinned at implementation); creating a Native record materializes files only under `native-sessions/`.
  - Same-ID no-collision (sessions): seed `root/sessions/` with a legacy-shaped record for session id S (golden fixture bytes); create a Native record with the **same id S** through the seam; both coexist; Native open/list return only Native content; the legacy record is never visible through the Native store.
  - Same-ID no-collision (runs): with `root/runs/<id>/` pre-seeded, `native_event_store(root).create_run` with the same id succeeds and creates `native-runs/<id>/` without touching `runs/<id>/`.
  - No-legacy-read proof: the seeded legacy same-ID record is poisoned (structurally invalid content); every Native operation (create/open/list, `acquire_lock`/`update_lock_holder`/`release_lock`, `mark_quarantined`, `commit_last_effective`, and the `validate_native_binding` load path) succeeds against the Native record — any read of the legacy root would surface the poison as a parse error or wrong content.
  - No-legacy-write proof: a byte snapshot (recursive directory listing + file contents) of `root/sessions/` and `root/runs/` taken before the full Native operation matrix equals the snapshot taken after — no new, changed, or deleted entries.
  - Structural call-site guard (future-accident pin): an AST scan of every module under `src/agent_run_supervisor/native_acp/` asserts (a) `SessionStore`/`EventStore` constructor calls appear only in `storage.py`, and (b) no string literal exactly equal to `"sessions"` or `"runs"` appears anywhere in the package (`"native-sessions"`/`"native-runs"` appear only in `storage.py`). The scan walks the package, so modules added by C7–C9 are covered automatically as they land.
- **RED (two focused commands, run before implementing):** `uv run pytest tests/native_acp/test_native_session_record.py -q` → `TypeError: SessionRecord.__init__() got an unexpected keyword argument 'session_kind'`; `uv run pytest tests/native_acp/test_native_store_isolation.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp.storage'`.
- **GREEN:** additive fields + three new operations + the `storage.py` seam; no existing call-site edits; both new suites and the structural guard green.
- **Commit:** `feat(session): additive native session identity, quarantined state, last-effective observations, isolated native-sessions store seam`

### 4.6 Slice C7 — permission bridge + event normalizer + bounded evidence writer

- **Goal:** default-deny mediation (no RBAC/policy engine) and the evidence surfaces, honest about being cooperative-agent policy enforcement, not an OS sandbox.
- **Inspect:** `role.py:11` (permission-kind vocabulary), `live_stream.py:23` + `hermes_caller/events.py:39` + `docs/design/result-event-schema.md` (normalized-event schema: `type`/`kind`/`status`/`text_length`/`key_summary`, integer `seq`; structural fields only), `event_store.py:22-57` (`RunHandle.append_ndjson`), acpx `--non-interactive-permissions fail` semantics in `docs/plans/archive` S1a/S2 references.
- **Create:** `native_acp/permissions.py` (`PermissionBridge`: clientCapabilities declaration built from the frozen `execution_grant` — first-E2E grant: fs read allowed inside the bound workspace, write refused, terminal not provided; mediation mapping table: workspace-internal read → allow; write/create/delete/terminal/execute/fetch and **any unregistered request type** → deny + `MediationEvent{requested_op, decision, reason}` into evidence; unexpected permission request ⇒ deny + fail-turn flag, aligning with acpx fail semantics; default deny throughout; grant snapshot only — never a runtime re-read), `native_acp/events.py` (`NativeAcpEventNormalizer`: ACP session updates → the existing normalized-event schema; unknown update types → `key_summary`, structural fields only; never copies text bodies — `text_length` only), `native_acp/event_writer.py` (per-Run single `EventWriter`: monotonic `seq`, bounded queue with producer timeout ⇒ controlled-run-failure signal, `max_event_bytes` cap writing a truncation marker while preserving lifecycle/permission/error families; writes through the run handle it is given — no store construction).
- **Tests:** create `tests/native_acp/test_permissions.py` (full decision table incl. default-deny for unknown ops; every decision emits a MediationEvent; read outside the bound workspace ⇒ deny), `tests/native_acp/test_events_normalizer.py` (golden mappings; adversarial update carrying a verbatim body yields `text_length` only), `tests/native_acp/test_event_writer.py` (seq monotonicity, queue-full timeout signal, byte-cap truncation with preserved families). These modules operate on internal shapes (C5's client adapts SDK objects), so the tests run without the extra.
- **RED:** `uv run pytest tests/native_acp/test_permissions.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp.permissions'`.
- **Commit:** `feat(native-acp): default-deny permission bridge, event normalizer, bounded evidence writer`

### 4.7 Slice C8 — `RunTask` vertical: admission → spawn → drive → markers → finalization

- **Goal:** the coordinating per-Run object (Stage-2 arsd will wrap it; no arsd code here): admission assembly, ManagedProcess + driver coordination, double markers, finalization per the Rev3 terminal table, lease binding, top-level exception guard, and **explicit Native store wiring** — evidence to `native-runs/`, records to `native-sessions/`, both bound only through the C6 seam. Direct embedding of `RunTask` is the sanctioned test/dev path (Rev3: production will be arsd-only).
- **Inspect:** `event_store.py:59-75` (`EventStore.create_run` — reused **unmodified**; the instance is constructed on the `native-runs/` root via `native_acp.storage.native_event_store`; `EventStore` already accepts an explicit base dir, so no additive parameter and no default change in `event_store.py` is needed or allowed), `event_store.py:110` (`exclusive_create_bytes` for `spec.json` and markers), `redaction.py` (`redact_text` usage as in `runner.py:144,317,355`), `session.py` lease surface (C6), `native_acp/storage.py` (C6 seam), `runner.py:775` (`progress.json` shape).
- **Create:** `native_acp/run_task.py`:
  - Store wiring: a `RunTask` binds, at construction, an explicit Native store pair — `EventStore` on `<supervisor_root>/native-runs/` and `SessionStore` on `<supervisor_root>/native-sessions/` — obtained exclusively through the C6 seam (`native_event_store`/`native_session_store`), either built by `RunTask` from a caller-supplied supervisor root via those helpers or injected pre-built by an embedding that obtained them the same way. `run_task.py` performs no direct `SessionStore`/`EventStore` construction (the C6 structural guard covers it) and no root discovery or defaulting. As a fail-fast belt-and-suspenders, `RunTask` validates at construction — using the `storage.py` dirname constants — that the bound stores' base dirs terminate in `native-runs`/`native-sessions` and refuses anything else. Every artifact write below goes through the Native `EventStore` run handle; every record/lease/quarantine/`commit_last_effective` call goes through the Native `SessionStore`. Legacy store defaults in `event_store.py`/`session.py` are untouched, and legacy code is never taught to read Native roots.
  - Admission: resolve profile → freeze hashes → `ResolvedLaunchSpec` → seal `AgentRunSpec` → write `spec.json` (exclusive create) and `launch.json` (no secrets) into the Run's `native-runs/` directory via the Native run handle, then spawn.
  - Drive: `spawn_managed_process` → driver `initialize` → `new_session` (this slice) → observe `EffectiveRunState` (`effective.json`) → config-fidelity machine → `prompt-dispatch-started` marker (exclusive create, immediately before the wire write) → `prompt_once` → `prompt-accepted` marker after the write succeeds (first turn-level update corroborates; conservative boundary relies on `started` only) → events through writer/normalizer → bounded `stderr.log` (redacted) → `result.json` write-once atomic + `progress.json` — all under `native-runs/<run_id>/`.
  - Session binding: for session-bound Runs, on the Native `SessionStore`: `acquire_lock(reclaimable=False)` → `update_lock_holder` with the ManagedProcess child identity → release on all paths; `session_reuse=none` uses an internal ephemeral record (still in `native-sessions/`) closed at terminal state.
  - Finalization: a pure function `finalize_run_state(observations) -> (run_status, session_disposition)` encoding the Rev3 DATA table verbatim — result-exists ⇒ keep (irreversible); terminal-event-without-result ⇒ rebuild; reliable ACP terminal + expected reap ⇒ completed/cancelled/failed/timed_out; dispatch-started + no reliable terminal + observation interrupted ⇒ **unknown** (+ Session quarantined, `retryable=false` persisted in the payload); supervisor-present + proven abnormal exit of the identity-matched child ⇒ failed + quarantined; supervisor cancel/timeout with ACP terminal ⇒ cancelled/timed_out + active, escalated-kill-after-dispatch ⇒ quarantined; pre-dispatch failure ⇒ failed, 0 Turn, session stays active. Exit-code detail via `classify_exit` is subordinate: it can never produce a completed/cancelled-class terminal for a dispatched Turn without an ACP terminal.
  - Top-level exception guard: any per-Run exception (normalizer/evidence IO/SDK) ⇒ controlled terminal state + evidence, never propagation (arsd robustness seam, provable now).
- **Tests:** create `tests/native_acp/test_finalization_table.py` (L1 pure: every table row; `unknown` payload carries `retryable=false`; irreversibility — existing `result.json` never rewritten; `retry_of_run_id` on a successor never mutates the original), `tests/native_acp/test_run_task.py` (L2 via fake agent: happy vertical produces the full artifact set with both markers in order; marker exclusive-create makes a duplicate dispatch impossible; kill-after-dispatch ⇒ failed + session quarantined; spawn failure and fidelity failure ⇒ failed, 0 Turn, session active; injected normalizer exception ⇒ controlled failure; lease held during and released after; double result write refused; **store-isolation vertical — the L2 half of the C6 regression:** the happy vertical runs against a supervisor root pre-seeded with legacy artifacts — `sessions/` holding a poisoned record whose session id equals the Run's ARS session id, and `runs/<run_id>/` holding a fixture with the same run id; the Run succeeds; every new artifact lands only under `native-runs/<run_id>/` and `native-sessions/`; the pre-seeded `sessions/`/`runs/` byte snapshots and directory listings are unchanged; the Run's result/evidence reflect only Native state; repeated for the kill-after-dispatch branch to prove quarantine state is written into the `native-sessions/` record only; **constructor guard:** `RunTask` constructed with a store rooted at legacy `sessions/`/`runs/` is refused).
- **RED:** `uv run pytest tests/native_acp/test_finalization_table.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp.run_task'` (the run-task and isolation tests share this RED).
- **Commit:** `feat(native-acp): RunTask vertical — admission, double markers, finalization table, isolated store + evidence wiring`
- Core G8 evidence.

### 4.8 Slice C9 — `session/load` continuity + controlled cross-Run switching

- **Goal:** Rev3 CONFIG B: same external session across process-per-Run; per-Run frozen model/effort (mid-Run switching structurally impossible); partial-failure rollback or quarantine; no generic rebind subsystem.
- **Inspect:** C5 driver `load_session`; C6 `validate_native_binding` / `commit_last_effective` / `mark_quarantined`.
- **Modify/extend:** `run_task.py` + `config_fidelity.py` switching branch: precondition (previous Run terminal; session `active`; lease acquired) → new Run freezes the new pair in its Spec → spawn → initialize → **capability check** (`requires_session_load` advertised; else fail + escalate per G6) → `session/load(agent_session_id)` with external-ID-unchanged assertion (any `session/new` on this path ⇒ hard fail — silent re-creation forbidden) → discovery (target model must be advertised) → set model → fresh dependent set → set effort → exact readback → `commit_last_effective` → markers → prompt. Partial failure: no prompt; record observed partial changes as evidence; roll back to the session's `last_effective_*` pair **with exact readback proof**; rollback proven ⇒ Run `failed` (0 Turn, fidelity error) + session re-opened; rollback failed/unprovable ⇒ `mark_quarantined` (Run still `failed`). Cross-agent-type reuse refused by `validate_native_binding` (new Session + Hermes-side context handoff is the only path). All session-record access on this path continues through the C6-seam store already bound by `RunTask`; C9 adds no store construction site (the C6 structural guard automatically covers the new modules/lines).
- **Tests:** extend the fake agent with load/switch scripting; create `tests/native_acp/test_session_switching.py` (L2): load-reuse happy path (external ID unchanged, no new-session event); silent-new detection ⇒ fail; switch happy path (model then effort; `last_effective_*` committed only after readback); each failure branch (set-model rejected; effort missing post-model; inexact readback; rollback-success ⇒ active; rollback-failure ⇒ quarantined); quarantined session refuses lease and new Runs; `retry_of_run_id` linkage leaves the original `unknown`/`failed` record untouched.
- **RED:** `uv run pytest tests/native_acp/test_session_switching.py -q` → `AttributeError` on the missing switching entry point (driver/run_task lack the load/switch surface until implemented).
- **Commit:** `feat(native-acp): session/load continuity and controlled cross-run model/effort switching with rollback/quarantine`
- Completes G8 (switch branches) at L2.

### 4.9 Slice C10 — real OpenCode 1.18.4 B-grade smokes (opt-in)

- **Goal:** Stage-1 GREEN against the real agent. Deterministic RED discipline is carried by C5–C9's L2 suite; this slice's failures are real-world evidence, triaged and reported, never papered over.
- **Preconditions (checked and reported first — G3/G6):** `ARS_NATIVE_SMOKE=1`; real binary (`ARS_OPENCODE_BIN`, default `/home/linuxbrew/.linuxbrew/bin/opencode`) reports version **1.18.4** (mismatch ⇒ stop: `OPENCODE_1_18_4` is a closed profile); Kimi K3 credentials present via the profile env allowlist; a second credentialable model available for the model switch (candidates `kimi-for-coding/{kimi-for-coding, kimi-for-coding-highspeed}`, same provider/credential slot; final pair chosen at execution by credential availability). A missing prerequisite is reported as a named gap — the effort-only switch never substitutes for the model-switch acceptance.
- **Create:** `tests/native_acp/test_real_opencode_smoke.py`, env-gated (skips in CI; operator-executed for Stage-1 exit), each smoke in a disposable empty workspace under a fresh temp dir **outside any tracked worktree**, with direct pre/post directory-listing assertions (both must be empty — the primary no-change evidence; `workspace_hash` is a binding-config hash and `git status` is never used as change evidence):
  1. **S1-equivalent read-only run** (new session): initialize capabilities recorded — **G6 checkpoint: `loadSession` advertised**; exact k3/`max` sequence with both discovery snapshots persisted; exactly one `session/prompt`; `stop_reason=end_turn`; result carries final_message and exact effective pair; normalized events + both markers + `redaction-report.json` with `matches: []`; workspace listings empty; no leftover processes (identity-probe on the recorded pgid/pid).
  2. **Continuity across process-per-Run:** R1 plants a random nonce; R2 on the same ARS session goes through `session/load` (external ID unchanged) and asks for recall; R2's final_message must contain the nonce. This is the **context-token continuity** proof the zero-prompt cross-process probe explicitly did not provide.
  3. **Switch smoke:** across R2/R3, at least one real model-ID switch and one effort switch (e.g., `max→high`), each with exact readback and `last_effective_*` commit verified; frozen Spec vs `EffectiveRunState` equality asserted per Run.
- Evidence handling: run stores (`native-runs/`, `native-sessions/`, bound via the C6 seam from a disposable supervisor root inside the temp dir) live under the temp root; the test prints artifact paths; the operator extracts a redacted summary out-of-band. Nothing is committed; artifacts may contain model output and stay out of git.
- **Verify:** smoke green in the operator environment; full deterministic suite still green; clean-process guard after each smoke.
- **Commit:** `test(native-acp): opt-in real OpenCode 1.18.4 B-grade smokes (readonly, load-continuity, switch)`

---

## 5. Gates G3–G8: order, evidence, stop/escalate

Execution order: **G4 → (Stage-0 SDK contract) → G5 → G7(L2 halves: C3, C5) → G8(L1/L2: C2, C6, C8, C9) → G3 precheck → G6 → G7/G8 real completion (C10)**. G1 (authorization) precedes everything and is satisfied only by §8. G2's documentation component is completed by the 2026-07-21 authority refresh; Stage-2 arsd source work still requires its own approvals (G1/§8 item 4).

| Gate | Where it runs | Pass evidence | On failure |
|---|---|---|---|
| G4 fresh-check | DoR (§2), before any edit | `origin/main == 2b09994` or reported delta re-confirmed; symbol greps match plan | STOP; report delta; no edits |
| Stage-0 SDK contract | C1 | version/import-origin/symbol/I-O-model tests green | BLOCKED report naming exact gap (e.g., no session-load API); no workaround; acpx untouched |
| G5 status-consumer audit | C1 (decision), C2 (implementation) | grep evidence in commit message; decision recorded; C2 tests prove zero acpx coercion | Intolerant consumer found ⇒ switch to superset branch (documented — this is a planned branch, not an escalation) |
| G7 live-ACP ownership | C3 (spawn/identity/group/reap/bounded stderr), C5 (exclusive wire, single stdout consumer, cancel), C10 (real) | listed tests green + smoke 1 clean-process check | Slice stops; defect fixed under TDD; if the SDK's I/O model cannot coexist with supervisor ownership ⇒ BLOCKED report |
| G8 state proofs | C2 (round-trip), C6 (quarantine persistence, store isolation), C8 (finalization table, markers, irreversibility, seeded-legacy isolation), C9 (switch/rollback), C10 (real terminal evidence) | `unknown` round-trip with persistent `retryable=false`; double markers; all table branches; `retry_of_run_id` non-rewrite; zero replay paths exist | Slice stops; any table ambiguity discovered ⇒ report (table is settled design; do not improvise new rows) |
| G3 run prerequisites | precheck at C10 start | K3 creds usable; second credentialable model confirmed; binary 1.18.4 | Report named gap (credential/model/binary); Stage-1 exit blocked on the missing smoke; **never** downgrade (no effort-only substitute, no version drift acceptance) |
| G6 session-load capability + continuity | first real handshake in C10; nonce recall in smoke 2 | `loadSession` advertised **and** nonce recalled across process-per-Run | Escalate to chair: switching requirement unsatisfiable on `OPENCODE_1_18_4` as designed; do **not** unilaterally move to a keep-alive-process design (that reopens settled architecture) |

General stop behavior: a failed gate stops the affected slice; produce a short BLOCKED report (what failed, evidence paths, minimal repro, options) and wait. The prior cross-process load probe counts only as preliminary transport evidence toward G6 — it never substitutes for smoke 2.

---

## 6. Verification ladder

**Per slice (before each commit):**
```bash
uv run pytest tests/native_acp/<slice tests> -q     # focused
uv run pytest -q                                    # full suite
uv run python -m compileall -q src scripts tests
git diff --check
git status --porcelain                              # only intended paths
```

**Stage boundaries (end of C1, end of C9, end of C10) and before handing the branch to review:**
```bash
make verify          # scripts/verify_local.sh: fixture validation, full pytest, compileall,
                     # doctor + replay smokes, docs index --check, drift --check,
                     # static_safety_scan (secrets / forbidden imports / stale phrases),
                     # check_version_sync, build + twine check, installed-wheel smoke,
                     # check_roadmap_governance, git diff --check
uv lock --check
```

Additional gates layered on top:
- **Docs checks with `--write`** run only inside the authorized docs slice C0; everywhere else docs are untouched and `--check` passes trivially.
- **No-leak scans:** `static_safety_scan.py` (secret-shaped values; forbidden network imports — the SDK is not on that list; stale phrases) plus the C4 test pinning that credential values never serialize, plus manual review of added lines per `docs/roadmap/verification.md:41`.
- **Store-isolation regression:** the C6 L1 suite (`test_native_store_isolation.py`) and the C8 L2 seeded-legacy vertical run inside every full-suite/`make verify` invocation from their slices onward; the structural guard (Native store constructors only in `native_acp/storage.py`; no legacy `sessions`/`runs` string literals in the package; `RunTask` refusing non-native-rooted stores) automatically extends to modules added by later slices, so a future accidental legacy-root default is a suite failure, not a review catch.
- **Wheel/base-install smoke:** the built wheel without the `native` extra must import and run `doctor` (already in `make verify`); one L1 test additionally pins that `import agent_run_supervisor.native_acp` succeeds without the SDK and only SDK-needing modules raise `NativeSdkUnavailableError` on use.
- **Clean-process/worktree guard:** after any L2/real run: no surviving child processes (identity-probe the recorded pids/pgids; report a `pgrep -f opencode` check for the operator after smokes), `git status --porcelain` clean, no `.tmp-*` debris in stores.
- **Version-sync:** untouched (no bump in this plan); `check_version_sync.py` must stay green precisely because nothing was bumped.

---

## 7. Rollback and compatibility

- **acpx stays the default and only wired surface.** Stage 1 adds no CLI/commands wiring for Native (`commands.py`/`cli.py` untouched; that is Stage-2 scope per Rev3 FILES). Nothing existing changes behavior: `runner.py`, `parser.py`, `policy.py`, `session_runtime.py` have empty diffs; `exit_classifier.py`/`result.py`/`session.py` diffs are additive with regression pins (C2 zero-coercion sweep; C6 byte-identical serialization golden).
- **No Native→acpx fallback:** no code path constructs acpx invocations from `native_acp/`; structurally pinned by `test_no_acpx_coupling.py`.
- **No schema migration:** Native uses new store roots (`native-runs/`, `native-sessions/`) bound exclusively through the C6 `storage.py` seam; legacy artifacts are never read or rewritten — proven by the C6 poisoned-record/byte-snapshot regressions and the C8 seeded-legacy vertical, not assumed by convention; new `SessionRecord` fields are omit-when-unset so pre-existing `session.json` files round-trip byte-identically; acpx `result.json` payloads are unchanged.
- **Rollback:** before merge — drop the branch/worktree (`git worktree remove`, `git branch -D`); nothing outside the worktree changed. Per-slice — `git revert` of the offending commit(s); slices are ordered so reverting from the tail leaves a coherent tree. Dependency rollback — reverting C1 removes the extra/lock/CI lines; base runtime never depended on the SDK. Store rollback — deleting `.agent-run-supervisor/native-*` directories removes all Native state without touching acpx artifacts.
- **Sachima:** pin `agent-run-supervisor==0.1.7` and the backend Protocol are untouched; no release happens under this plan, so Sachima cannot observe any of this work.

---

## 8. Approval package

Approvals are separate and non-transitive. Exact sentences:

Documentation prerequisites already completed on this branch and **not** to be re-executed: slice **C0** (docs-governance board/plan activation) and the **G2** authority-document alignment (GOAL/PRD/architecture/technical-solution recording the chair-confirmed arsd/Native ACP target as documentation authority). Neither granted implementation authorization.

1. **Stage 0/1 local implementation (C1–C10) — the next user approval sentence:**
   > I approve local implementation of slices C1–C10 of the vNext Stage 0/1 native-ACP plan (`docs/plans/active/2026-07-21-vnext-stage01-native-acp.md`) on the existing `feat/native-acp-stage01` worktree/branch, after the DoR fresh-head preflight in §2 passes. This authorizes: adding the `agent-client-protocol==0.11.0` `native` extra with `uv.lock`/`Makefile`/CI sync (C1), the C2–C9 additive Native ACP source and L1/L2 test suites, and the opt-in real OpenCode 1.18.4 B-grade smokes (C10) run from the operator's environment. C0 and G2 are already completed docs prerequisites and are not to be re-executed. This approval covers **no** push to origin, **no** PR #65 mutation or merge, **no** release/tag/PyPI publication, **no** arsd or Stage-2 code, **no** service/cgroup enablement or deployment, **no** caller-uid allowlist policy activation, and **no** Sachima/Gateway/IM/live behavior.
2. **Push / PR #65 mutation (separate, after Stage-1 done criteria in §1.2 are met):**
   > I approve pushing `feat/native-acp-stage01` to origin and updating PR #65 for review; merge remains a separate approval.
3. **Merge (separate, after review):**
   > I approve merging PR #65 (`feat/native-acp-stage01` → `main`).
4. **Stage 2 and beyond (future, separate):** arsd implementation, caller-uid allowlist policy (G12), service/cgroup harness enablement, release/tag/PyPI publication, and any Sachima/Gateway/IM/live-behavior work each require their own explicit approval; none is implied by items 1–3.

---

## 9. Risks and open decisions

**Risks (with planned handling):**
- **SDK surface drift vs Rev3 expectations** (symbol names such as `ClientSideConnection`/`ConfigOptionUpdate`, set-config entry point, I/O model): C1 pins the actual surface; equivalent renames are recorded, semantic gaps are Stage-0 stops.
- **SDK support across the CI Python matrix (3.11–3.14):** `uv lock` may fail to resolve for newer interpreters; handled in C1 as an explicit stop-and-report, options being a conditional matrix for the native extra or escalation — never a silent matrix cut.
- **`session/load` real-world behavior:** the probe advertised `loadSession=true` and load worked cross-process, but token-level continuity and switch-under-load are unproven until smoke 2/3; failure escalates via G6 rather than triggering design improvisation.
- **Second credentialable model availability (G3):** switch candidates share the provider credential slot, so probability is high, but confirmation is execution-time; missing ⇒ named-gap report.
- **OpenCode binary drift:** brew may have advanced past 1.18.4; the profile is a closed set, so C10 stops on version mismatch (re-baselining to a newer OpenCode is a chair-level profile decision).
- **Store-constructor drift:** the C6 seam relies on `SessionStore`/`EventStore` accepting an explicit base directory (present at 2b09994; re-pinned at DoR by the added G4 greps). If DoR finds a changed constructor surface, C6 stops and reports; widening or changing legacy constructors/defaults is out of scope.
- **`check_roadmap_governance.py` requirements for C0** are read before editing; the slice adapts (possibly adding the minimal S2 phase-archive file) instead of fighting the checker.
- **POSIX-only ManagedProcess:** Native paths require POSIX (process groups); explicit, tested, and documented — no Windows claim.
- **Real-smoke flakiness:** smokes are operator-run and evidence-producing; a red smoke is triaged as evidence (never retried into green silently).

**Open decisions (deliberately small; none reopen settled architecture):**
- G5 final branch (additive default vs superset) — settled by C1 evidence.
- Exact SDK symbol names — settled by C1 pins.
- Native `result.json` detail-key reuse (e.g., `raw_event_path` value, `acpx_exit_code` naming for a native process exit) — settled inside C8 against `docs/design/result-event-schema.md`, keeping consumer-visible keys stable; recorded in the commit.
- Switch-smoke model pair — settled at C10 by credential availability within the closed candidate list.
- Chair-owned (unchanged from Rev3, none blocking this plan): G12 caller-uid allowlist (Stage 2); G1 approval sequencing. (G2 GOAL/PRD revision: completed 2026-07-21 by the authority-document refresh.)

---

## 10. Non-goals (restated for this plan)

No arsd code, sockets, or reconciliation runtime (Stage 2); no Sachima `ArsdBackend` or pin change; no acpx removal/compat/fallback or shared session store (Native store access goes only through the `native_acp/storage.py` seam); no ACP v2; no runtime-pluggable adapters or command/argv/env passthrough; no TCP/root/multi-tenant/public ingress; no per-Run Worker or cross-crash Run survival; no SQLite or new storage engine/abstraction (the C6 seam is two root-binding constructors over the existing stores, not a storage layer); no workspace content-digest service, filesystem watcher, OS sandbox, or hostile-process containment claims; no generic session-rebind subsystem; no cross-agent-type session migration (context handoff stays with Hermes); no quarantine-release tooling (successor work uses a new Session); no auto-resume/replay of interrupted prompts and no automatic retry of `unknown` Runs (`retryable=false` is persistent; successors are independent Runs linked via `retry_of_run_id`); no write-capable first E2E; no fake/test-double presented as production evidence; no release/tag/PyPI publishing under this plan.

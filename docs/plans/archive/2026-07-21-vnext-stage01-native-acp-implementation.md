---
title: "ARS vNext Stage 0/1 implementation — Native ACP core"
status: archived
created_at: 2026-07-21
archived_at: 2026-07-22
last_validated_at: 2026-07-21
supersedes: "docs/plans/archive/2026-07-21-vnext-stage01-native-acp.md"
---
> **Archived 2026-07-22.** The Stage 0/1 work this plan drove is merged into `main` and versioned in
> the 0.2.0 source line. Retained for audit only; embedded branches, baselines, gates, and approval
> sentences are expired (see [`README.md`](README.md)).

# ARS vNext Stage 0/1 implementation — Native ACP core

> **Execution artifact, not authorization.** This plan derives only from the vNext-only GOAL/PRD/design
> authority. Its archived predecessor is historical and must not supply branches, PRs, baselines, scope,
> or gates. Superseded plans and merged documentation work are cold history and are not part of this plan.

## 0. Target and boundary

Build the additive Native ACP core through ars-core using a fresh implementation branch from the live
`origin/main` that contains this authority reset. Deliver Stage 0 dependency/API gates and Stage 1
C1–C10 with B-grade real OpenCode evidence. Do not implement `arsd`, service/cgroup deployment,
caller-UID policy, release/publication, Sachima, Gateway/IM, or any acpx fallback.

The final product target remains `caller → arsd → ars-core/Native ACP → external AGENT`; Stage 1 is an
intermediate implementation/evidence boundary, never production acceptance.

## 1. Done criteria

Stage 0/1 is done only when all of the following are fresh and reproducible:

- `agent-client-protocol==0.11.0` is locked in the approved Native extra and its import origin/version/API
  are verified from the consuming environment.
- Current source consumers determine the safe `unknown` carrier via the C1 G5 consumer inventory
  (a deterministic out-of-Git evidence record, not a grep transcript); no legacy acpx status/result
  behavior changes accidentally.
- `ManagedProcess` provides one live stdin/stdout owner for the SDK plus supervised identity,
  timeout/cancel/group termination, bounded stderr, wait, and reap.
- `AgentProfile → ResolvedLaunchSpec → immutable AgentRunSpec` freeze order is proven; the first profile
  is OpenCode 1.18.4 / literal K3 / literal `max`.
- Native runs/sessions bind only through `native_acp/storage.py`; poisoned same-ID legacy roots are never
  read or changed.
- L1/L2 prove exact config flow, markers, terminal table, `unknown/quarantined/retryable=false`, no replay,
  default-deny mediation, bounded event writing, write-once terminal facts, `0700`/`0600` Native store
  modes, quarantine-atomic lease/continuity, switch rollback, and fault containment.
- Real opt-in OpenCode evidence proves exact K3/max, a real `session/load` on the same external ID,
  historical-token continuity, and an exact between-Run model/effort switch; any missing capability stops
  the work instead of changing architecture.
- Complete repository verification is green; changed-file and no-fallback/no-secret boundaries are clean.
- No `arsd`, service, deployment, release, Sachima, or live platform behavior is present.

## 2. Definition of Ready — fresh branch, worktree, and baseline

Run only after the user approves §8 item 1. Use live facts, never a commit frozen in this document:

```bash
# From the canonical repository
git fetch origin --prune
BASE=$(git rev-parse origin/main)
git status --porcelain                 # canonical checkout must be clean or left untouched

# The implementation branch/worktree must not pre-exist.
git show-ref --verify --quiet refs/heads/feat/native-acp-stage01-implementation && exit 2 || true
git ls-remote --exit-code --heads origin feat/native-acp-stage01-implementation >/dev/null 2>&1 && exit 3 || true
test ! -e /home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/feat-native-acp-stage01-implementation

git worktree add   /home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/feat-native-acp-stage01-implementation   -b feat/native-acp-stage01-implementation origin/main
cd /home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/feat-native-acp-stage01-implementation

test "$(git rev-parse HEAD)" = "$BASE"
test -z "$(git status --porcelain)"
test -z "$(git diff --name-only origin/main...HEAD)"

# Worktree-local code intelligence; do not reuse an older worktree index.
codegraph init
codegraph status

uv sync --locked --extra dev --extra release
uv lock --check
./scripts/verify_local.sh
```

Then fresh-check the exact seams before C1: official SDK import/API, `execute_subprocess`, result/status
consumers, `SessionRecord.validate_binding`, `SessionStore(base_dir)`, `EventStore(base_dir)`,
`workspace_hash`, process liveness, event schema, and all call sites named by C1–C10. Record `BASE`,
versions, baseline tests, and CodeGraph status outside Git. If any changed source/API invalidates this plan,
stop and revise/reapprove the plan; do not improvise a new architecture inside implementation.

**Early G3 readiness snapshot (advisory, non-secret)** — recorded at DoR, before the first source edit,
into the same out-of-Git evidence record as `BASE`. It records only:

- the registered OpenCode executable resolution (per the operator-managed registered installation
  mapping) and its reported version;
- credential-slot **identifiers** and presence/readiness booleans — never values, secret-bearing paths,
  or fragments;
- whether the intended `kimi-for-coding/k3` model ID and at least one candidate second model ID are
  advertised, **only when** catalog inspection is possible without sending any prompt; otherwise
  `unknown`;
- an overall `ready | not_ready | unknown` verdict plus a sanitized reason and timestamp.

Boundaries: this snapshot is advisory discovery only — not an independent G1 blocker and not C10
acceptance evidence. `not_ready`/`unknown` does not stop deterministic C1–C9 but must stay visible in
every stage-boundary report until resolved. C10 re-runs the **full** G3 gate with real credential/model
usability and fails closed: no effort-only substitution, no version-drift acceptance, no guessed or
aliased model ID.

## 3. Stage 0 — dependency and source-contract gate

### Slice C1 — Stage 0: consuming dependency lock gate + SDK contract + G5 audit

*No product behavior. Gap ⇒ BLOCKED report; acpx untouched.*

- **Inspect:** `pyproject.toml:34-36` (`dev`/`release` extras), `uv.lock`, `Makefile:20-21` (`sync`), `.github/workflows/verify.yml:40-41` (Install step; also the coverage job's install), `scripts/smoke_installed_wheel.sh`.
- **Change:**
  - `pyproject.toml`: add `[project.optional-dependencies] native = ["agent-client-protocol==0.11.0"]` (exact pin, narrow extra; base `dependencies` stays `[]`).
  - `uv lock`; then `uv sync --extra dev --extra release --extra native`.
  - Lock-enforcement gates (required C1 implementation content; nothing here is done in the docs-only plan revision):
    - `verify.yml`: **both** install jobs (`verify` `:40-41` and `coverage` `:66-67`) become `uv sync --locked --extra dev --extra release --extra native` once the Native extra exists, so CI installs are lock-enforced and run the Native L1/L2 suite.
    - `Makefile` `sync` (`:20-21`): `uv sync --locked --extra dev --extra release --extra native` — the local sync path consumes the existing lock and never silently re-resolves. `make bump`'s deliberate lock regeneration via `tools/bump_version.py` is out of scope and unaffected.
    - `scripts/verify_local.sh`: add an `uv lock --check` step as an **internal gate of the canonical verifier**, so CI and `make verify` both enforce it on every run; it is not merely an operator command.
  - If lock resolution fails on part of the 3.11–3.14 matrix (SDK/transitive support), stop and report options (conditional matrix vs escalation) — do not silently shrink the matrix.
- **Create:** `tests/test_verifier_lock_gates.py` — focused static verification that the lock gates exist and stay: read `.github/workflows/verify.yml` and assert both install lines carry `--locked` and `--extra native`; read `scripts/verify_local.sh` and assert it contains an `uv lock --check` step; read the `Makefile` `sync` recipe and assert it carries `--locked`. Behavioral coverage follows for free: CI and `make verify` execute the verifier, so the `uv lock --check` gate itself runs on every ladder invocation from C1 onward.
- **Create:** `tests/native_acp/__init__.py`; `tests/native_acp/test_sdk_contract.py`:
  - `importlib.metadata.version("agent-client-protocol") == "0.11.0"`.
  - Resolve the distribution's top-level import name via `importlib.metadata` (do not assume `acp`); import it; assert `__file__` resolves inside this worktree's `.venv` (import-origin pin, same evidence class as the prior `sdk-runtime.txt`).
  - Pin symbols/types required by the current vNext technical solution: client-side connection class (expected `ClientSideConnection`), `Client` callback surface (session update, permission request, fs), initialize / session new / **session load** / session close / set-config / prompt / **cancel** entry points, config-option carriers (expected `NewSessionResponse.config_options`, `SetSessionConfigOptionResponse.config_options`, `ConfigOptionUpdate`), stop-reason vocabulary incl. `end_turn`.
  - Record the connection constructor's stream/I-O model (asyncio streams vs raw pipes) — this pins the C3 stream surface.
  - Where an actual name differs from the current vNext design's expectation but is semantically equivalent, the test pins the **actual** name and the same commit updates this plan's symbol table with a drift note; a semantic gap (e.g., no session-load API) is a Stage-0 stop (§5).
  - **Symbol drift note (C1, verified against installed 0.11.0):** the actual config setter is
    `ClientSideConnection.set_config_option` (design expectation `set_session_config_option`) and the
    actual `Client` callback protocol lives at `acp.interfaces.Client` (design expectation
    `acp.schema.Client`); both are semantically equivalent and the contract tests pin the actual names.
    Verified surface: `acp.client.connection.ClientSideConnection` with `initialize / new_session /
    load_session / set_config_option / prompt / cancel / close_session`; constructor takes
    `input_stream`/`output_stream` and enforces asyncio `StreamWriter`/`StreamReader` at runtime;
    callback surface `session_update / request_permission / read_text_file / write_text_file`;
    config-option carriers and `end_turn` stop reason are as expected. No semantic gap.
- **G5 audit (read-only; source-grounded consumer inventory, not a grep transcript):** searches (`grep -rn "AgentRunStatus"` plus at least one independent status-string-literal search, e.g. `grep -rn '"completed"\|"failed"\|_SUCCESS_STATUSES\|supervisor_status' src tests`, and CodeGraph cross-reference) are **discovery signals only — neither grep nor CodeGraph alone is claimed as proof of consumer completeness or correctness**. The audit reads each consumer and must cover, at minimum, these classes:
  - enum definitions/imports and enum-keyed mappings: `exit_classifier.py:7` (`AgentRunStatus`), `_RETRYABLE_DEFAULT:21`, `_BASE_STATUS_FOR_EXIT:37`, `result.py:66` (`_ERROR_CODE_FOR_STATUS` + `_default_error_code:81` — `.get` tolerant, returns `None` for unmapped members);
  - direct and indirect status **string** readers, literal comparisons, and success sets: `commands.py:216,310` (`result["status"] == "completed"` exit-code mapping — new members map to exit 1; semantically meaningful), `runner.py:229` (literal `"dry_run"` status written outside the enum), `parser.py:145,392` (acpx stream/tool status vocabulary — classify explicitly as a distinct vocabulary, not a Run status consumer), `hermes_caller/events.py:54` (event status passthrough);
  - `result.json`/payload serialization and deserialization: `result.py:19-63` (`build_result_payload` — `status.value` write), `session_runtime.py:712,749` (payload build + `result.json` write), `session_inspect.py:160-171` (`_read_turn_status` — off-vocabulary degrades to `None`);
  - `session_runtime`, `session_inspect`, caller/result wrappers: `session_runtime.py:49-53,132` (turn classification, `SessionTurnOutcome.status`), `session_inspect.py:63` (`_KNOWN_TURN_STATUSES` — derived frozenset, auto-adapts), `session_inspect.py:129-141` (`_read_record_state` — hard-coded `(STATE_OPEN, STATE_CLOSED)` tuple; a session-state consumer whose behavior for C6's `quarantined` must be recorded), `caller.py:79,297-299` (`supervisor_status` carrier, `_status_from`);
  - Hermes caller verdict and view-model projections: `hermes_caller/verdict.py:18,72-103` (`_SUCCESS_STATUSES={"completed"}`, non-membership ⇒ non-success, status carried as evidence), `hermes_caller/view_model.py:25,59,131-132` (`_SUCCESS_STATUSES`, `supervisor_status` chip, `CardPhase.ERROR` for non-success), `hermes_caller/feishu_adapter.py:33,50` (passthrough);
  - CLI/result/status presentation surfaces and tests: `cli.py`/`commands.py` status/session surfaces, `runner.py:215` (dry-run) + classification passthrough, and every test asserting status values. Sachima consumes the released 0.1.7 wheel and never sees Native stores in Stage 0/1.

  **Evidence record:** the audit is persisted as a deterministic **out-of-Git G5 evidence record** (same evidence store as the §2 DoR record) listing every inspected path/symbol, its observed behavior for `failed`/`cancelled`/`unknown`, its classification (tolerant vs exhaustive/intolerant vs semantically meaningful), and the final additive-vs-superset decision with rationale. The commit message summarizes; the evidence record is the audit artifact.
  **Test obligation:** C2 must add a focused behavior test for **each** consumer classified intolerant or semantically meaningful — enum construction alone proves nothing about consumers.
  **Decision rule:** if no consumer exhaustively enumerates or misbehaves on new members → choose the **additive extension** (default, matches the evidence gathered so far); otherwise choose the Native-side superset enum + lossless mapping and record why. The chosen branch is implemented in C2, not here.
- **RED:** `uv run pytest tests/native_acp/test_sdk_contract.py -q` before the pyproject/lock change → fails with `importlib.metadata.PackageNotFoundError: agent-client-protocol` (SDK absent from the venv); `uv run pytest tests/test_verifier_lock_gates.py -q` before the CI/Makefile/verifier edits → fails (no `--locked`/`--extra native` install lines, no `uv lock --check` verifier step yet).
- **GREEN:** after extra+lock+sync the contract test passes; the lock-gate static test passes (both CI installs `--locked ... --extra native`, verifier contains `uv lock --check`, Makefile sync `--locked`); `uv lock --check` green; wheel smoke (base install, no extra) still green.
- **Verify:** focused test; `uv run pytest -q`; `compileall`; `make verify`; `git diff --check`.
- **Commit:** `feat(packaging): add narrow native extra pinning agent-client-protocol==0.11.0 with SDK contract tests`

**Stage 0 exit:** contract and lock-gate tests green, G5 out-of-Git evidence record complete and decision recorded, ladder green. Any SDK gap ⇒ BLOCKED report (exact missing symbol/behavior + candidate resolutions), no workaround.

---

## 4. Stage 1 — slices, dependency graph, per-slice contract

### 4.0 Dependency graph and commit order

```text
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

- **Goal:** carry the Native terminal set `completed | failed | cancelled | timed_out | unknown` (PRD R5 / architecture §5) with `unknown` round-tripping and persistent `retryable=false`. `completed`/`timed_out` already exist; the additive branch adds exactly the missing three members. (This is the settled vNext terminal vocabulary in PRD R5 and the technical solution, not a new decision.)
- **Inspect:** `exit_classifier.py:7-45` (`AgentRunStatus`, `_RETRYABLE_DEFAULT`, `_BASE_STATUS_FOR_EXIT`), `exit_classifier.py:79` (`classify_exit`), `result.py:19-82` (`build_result_payload`, `_ERROR_CODE_FOR_STATUS`), `session_inspect.py:63`, `hermes_caller/verdict.py:18`.
- **Modify (additive branch):** `exit_classifier.py` — add `FAILED = "failed"`, `CANCELLED = "cancelled"`, `UNKNOWN = "unknown"`; extend `_RETRYABLE_DEFAULT` with all three → `False` (UNKNOWN hard-`False` per contract); `classify_exit` logic untouched. `result.py` — `_ERROR_CODE_FOR_STATUS` entries `FAILED→"FAILED"`, `CANCELLED→"CANCELLED"`, `UNKNOWN→"UNKNOWN"`.
  *(Superset fallback if C1's audit flipped the decision: create `native_acp/status.py` with `NativeRunStatus`, a lossless mapping for the four non-unknown values, and a schema-compatible payload writer; same tests reshaped; `exit_classifier.py`/`result.py` untouched.)*
- **Tests:** create `tests/native_acp/test_status_vocabulary.py`:
  - `AgentRunStatus("unknown")` constructs; `.value` round-trips through JSON.
  - `build_result_payload(status=UNKNOWN, retryable=False, ...)` → `status=="unknown"`, `retryable is False`, `error_code=="UNKNOWN"`; same for `failed`/`cancelled`.
  - Zero-coercion guard: a bounded sweep of `ClassifierInput` (exit codes 0–5,130, plus flag combinations) shows `classify_exit` never returns any new member — acpx classification is provably unchanged.
  - `session_inspect._KNOWN_TURN_STATUSES` now contains the new values (documents the deliberate vocabulary growth of the derived frozenset).
  - **Consumer-behavior pins (one focused test per consumer the C1 evidence record classifies intolerant or semantically meaningful — enum construction alone is not consumer proof):** `commands.py` exit mapping — a result status of `failed`/`cancelled`/`unknown` maps to process exit 1; `session_inspect._read_turn_status` returns the new values (not `None`) for a well-formed `result.json`; `hermes_caller/verdict.py`/`view_model.py` — `unknown` yields a non-success verdict / `CardPhase.ERROR` with `supervisor_status` carried verbatim as evidence and never coerced to success; `result.py` payload serialize→re-read round-trip for all three members with `retryable=False` intact; plus a pin for every additional intolerant/semantically meaningful consumer the C1 inventory names.
  - Existing `tests/test_exit_classifier.py` untouched and green (regression anchor).
- **RED:** `uv run pytest tests/native_acp/test_status_vocabulary.py -q` → `ValueError: 'unknown' is not a valid AgentRunStatus`.
- **GREEN:** members + table entries only; no behavior change elsewhere.
- **Verify:** focused + `uv run pytest -q` + compileall.
- **Commit:** `feat(status): additive terminal-status vocabulary (failed/cancelled/unknown) for native runs`

### 4.2 Slice C3 — supervised live-process surface (`ManagedProcess`)

- **Goal:** the architecture §2 process-ownership triangle: supervision layer owns spawn/PID/PGID/identity/timeout/signal/reap; the SDK will own the live stdin/stdout wire; completion-oriented `execute_subprocess` stays acpx-only and untouched.
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

- **Goal:** the PRD R1 / architecture §3 admission freeze order: resolve profile revision/snapshot/hash + config-schema hash + grant/role/workspace/MCP/credential-ref hashes → materialize `ResolvedLaunchSpec` → seal `AgentRunSpec`/`spec_hash` → only then spawn; `EffectiveRunState` holds observations only and never writes back.
- **Inspect:** `workspace.py:40,71` (`validate_effective_cwd`, `workspace_hash` — binding-config hash, explicitly **not** content evidence), `role.py:11` (`PERMISSION_KINDS` vocabulary for grant capability naming), `event_store.py:110` (`exclusive_create_bytes` for later spec.json write).
- **Create:** `src/agent_run_supervisor/native_acp/__init__.py` (no eager SDK import; stdlib modules import cleanly without the extra; SDK-needing modules raise a typed `NativeSdkUnavailableError` only on use), `native_acp/spec.py`, `native_acp/profile.py`:
  - `spec.py`: `AgentRunRequest` (wire input), `AgentRunSpec` (frozen dataclass; fields per PRD R1 / technical solution §2: schema_version, identity{owner, namespace}, input refs+hashes, session{reuse, ars_session_id, expected_binding_hash}, agent{profile_id, profile_revision, profile_snapshot_ref, profile_hash, config_schema_hash}, execution_grant{grant_hash, role_hash, capabilities}, workspace{canonical_root, cwd, workspace_hash}, runtime{model_id, config{effort}, config_fidelity="exact"}, bindings{mcp snapshot hashes, credential_refs}, limits, evidence/recovery policy hashes, launch_spec_hash), `ResolvedLaunchSpec` (executable, fixed argv with only `<effective_cwd>` substitution, env-allowlist slot names + credential refs — **never values**, transport="stdio"), `EffectiveRunState` (observations only: ProcessIdentity, agent_info, protocol/capabilities incl. session-load, external agent_session_id, discovery snapshots, readback model/effort), `spec_hash()` (sha256 over canonical JSON excluding only generated Run identity/lineage fields: `run_id`, `submitted_at`, `retry_of_run_id`; authenticated `owner` and `namespace` are included, and changing either changes the hash).
  - `profile.py`: `AgentProfile` (typed: revision/hash/snapshot, registered executable reference, argv template, credential env allowlist, selector names `configId=model`/`configId=effort`, capability flags incl. `requires_session_load`), `ProfileRegistry` (code-registered closed set; unknown id ⇒ error), `OPENCODE_1_18_4` (id `opencode-1.18.4`; executable resolved only from the operator-managed registered installation mapping, with no caller/env path override; fixed subcommand `acp`; literal `model=kimi-for-coding/k3`, `effort=max`; `requires_session_load=True`). No command/argv/env/JSON passthrough anywhere.
- **Tests:** create `tests/native_acp/test_spec.py`, `tests/native_acp/test_profile.py`: freeze-order enforcement (spec cannot seal without a resolved profile snapshot + hashes; sealing twice fails; sealed spec immutable); `spec_hash` golden stability + generated-field exclusion (`run_id`/timestamps/`retry_of_run_id` do not change the hash; changing authenticated `owner` or `namespace`, or any input hash, does); `ResolvedLaunchSpec` serialization contains slot names only (adversarial env value never appears in `repr`/JSON); argv template substitutes only `<effective_cwd>`; registry closed-set; `OPENCODE_1_18_4` literals pinned. Plus `tests/native_acp/test_no_acpx_coupling.py`: `native_acp` modules import neither `agent_run_supervisor.policy` nor `agent_run_supervisor.parser` (structural no-fallback pin, extended in later slices).
- **RED:** `uv run pytest tests/native_acp/test_spec.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp'`.
- **GREEN:** minimal dataclasses/hashing passing; no SDK import.
- **Commit:** `feat(native-acp): frozen run spec, resolved launch spec, profile registry (OPENCODE_1_18_4)`

### 4.4 Slice C5 — driver + client callbacks + config-fidelity machine + fake-agent L2

- **Goal:** `NativeAcpDriver` owns wire + ACP state machine only (never spawns); single-Run exact sequence per PRD R3 / technical solution §5 (initialize → session new/load → discovery → set model → consume the complete model-dependent set → rediscover effort in the fresh set only → set effort → consume complete set + updates → exact verify → ready-to-prompt); fail-closed everywhere.
- **Inspect:** C1's pinned SDK symbols; `docs/design/result-event-schema.md` (event families, for the client's update hook shapes).
- **Create:** `native_acp/driver.py` (`NativeAcpDriver`: `open(proc: ManagedProcess)`, `initialize()`, `new_session(...)`, `load_session(agent_session_id)`, `set_config_exact(...)`, `prompt_once(...)`, `cancel()`, `close()`), `native_acp/client.py` (SDK `Client` implementation translating callbacks — session updates, permission requests, fs — into internal normalized shapes consumed by C7's bridge/normalizer; keeps SDK types out of the mapper modules), `native_acp/config_fidelity.py` (`ConfigFidelityMachine` — the sequence above as an explicit state machine; any violation ⇒ `ConfigFidelityError` ⇒ 0 Turn; constructed so the post-set-model option set is the only source for effort discovery — skipping rediscovery is structurally impossible).
- **Create (test asset):** `tests/native_acp/fake_agent.py` — an in-repo fake ACP agent **subprocess** speaking real stdio JSON-RPC framing (scripted via argv/env: advertised option sets pre/post model-set, readback values, fault injections: malformed frame, omit-effort, wrong readback, hang, exit-at-phase, silent-new-on-load). It is spawned through `ManagedProcess` (never a driver mock). L2-only; never production evidence.
- **Tests:** create `tests/native_acp/test_driver_config_fidelity.py` (guarded by `pytest.importorskip` on the pinned SDK module so environments without the extra skip cleanly; CI runs it): happy exact sequence reaches ready-to-prompt with recorded discovery snapshots; effort absent from post-model set ⇒ fail-closed, zero `session/prompt` frames observed by the fake; pre/post option sets differ and the machine provably binds the post set; inexact readback ⇒ error, no prompt; malformed frame ⇒ controlled driver error; child killed mid-handshake ⇒ controlled error; `cancel()` path; initialize capability recording (incl. `loadSession`).
- **RED:** `uv run pytest tests/native_acp/test_driver_config_fidelity.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp.driver'`.
- **GREEN:** driver/client/machine minimal implementation over the fake agent.
- **Verify:** focused + full suite. G7 wire-ownership evidence (exclusive SDK wire, single stdout consumer, cancel) at L2.
- **Commit:** `feat(native-acp): SDK driver, client callbacks, exact config-fidelity machine with fake-agent L2 suite`

### 4.5 Slice C6 — session layer: native identity, `quarantined`, `last_effective_*`, isolated `native-sessions/` store seam

- **Goal:** the PRD R4/R5 Session model — stable Session identity separated from per-Run model/effort; persistent `quarantined`; model/effort as mutable observations; acpx records byte-identical — **plus the explicit store-isolation seam:** every Native session operation binds to `.agent-run-supervisor/native-sessions/` through one narrow constructor helper, an implicit default into legacy `sessions/` is structurally impossible (test-pinned), and legacy code is never taught about Native roots — **plus the native-only creation contract:** a Native record is created without accepting or synthesizing any legacy role/acpx value (no `AgentRoleSpec`, no fake hashes, no sentinels), and the quarantine check is atomic with lease minting.
- **Inspect:** `session.py:95-116` (`SessionRecord`), `:48-49` (`STATE_OPEN/CLOSED`), `:176-223` (`create_session` — requires `AgentRoleSpec` and fills mandatory legacy role/policy/acpx fields; the live motivation for a native-only creation API), `:266` (`ensure_open` — a **pre-lock** static check, separate from the lease guard today), `:278` (`mark_closed` — serialized under `_session_lock_guard`), `:299-338` (`validate_binding` — full `role_hash` incl. `runner.model`, confirming current-main cannot express model switching), `:602-612` (`_record_to_dict` omit-when-unset pattern), `:650` (`_record_from_dict` tolerant reads), `:446-595` (lease surface: `acquire_lock` with `reclaimable=False` — holds `_session_lock_guard` but never reads `session.json` state, the TOCTOU gap this slice closes; `update_lock_holder` child identity, `release_lock`, `detect_stale_locks`), `process_liveness.py:216` (`classify_lock`), `event_store.py:128-133` (`secure_mkdir` — the approved `0700` primitive), `session_inspect.py:129-141` (`_read_record_state` — hard-coded `(STATE_OPEN, STATE_CLOSED)`; its `quarantined` behavior is pinned per the C1 inventory); additionally the `SessionStore` explicit-base-dir constructor surface in `session.py` and the `EventStore` explicit-base-dir constructor surface in `event_store.py` (both symbol-pinned by the added G4 greps; the seam passes explicit base dirs and requires **no change** to either constructor or to any store default).
- **Create:** `src/agent_run_supervisor/native_acp/storage.py` — the store-isolation binding seam (stdlib-only; no SDK import; importable without the extra; deliberately root-binding constructors plus thin native-only wrappers, **not** a storage abstraction):
  - `NATIVE_SESSIONS_DIRNAME = "native-sessions"` and `NATIVE_RUNS_DIRNAME = "native-runs"` — the only place in `src/` where these directory names are spelled.
  - `native_session_store(supervisor_root) -> SessionStore` — returns a `SessionStore` constructed explicitly on `<supervisor_root>/native-sessions`; performs no discovery of its own (the caller passes the already-resolved `.agent-run-supervisor` control root, the same value legacy code resolves for its own stores).
  - `native_event_store(supervisor_root) -> EventStore` — the same contract for `<supervisor_root>/native-runs` (consumed by C8).
  - **Secure roots (pinned behavior):** both constructors create-or-verify their root via the already approved `secure_mkdir` primitive: `native-sessions/` and `native-runs/` are `0700`; a pre-existing insecure mode is deliberately corrected to `0700` (that primitive's existing chmod semantics — the chosen behavior, test-pinned; silently proceeding on an insecure root is not an option). Per-session and per-run directories are likewise `0700` (`secure_mkdir` in the session create path; `EventStore.create_run`'s existing `DIR_MODE` mkdir+chmod); all files remain `0600`. `EventStore.create_run`'s own plain `base_dir.mkdir` never runs against an unverified root because the seam has already secured it.
  - `write_once_json(path, payload) -> Path` — the **single named write-once wrapper** over `exclusive_create_bytes` (canonical JSON at `0600`, `O_EXCL`); the only sanctioned writer for immutable Native artifacts (consumed by C8). A second create of the same path raises `FileExistsError`; nothing here ever overwrites. Ordinary atomic replacement (`RunHandle.write_json`/`atomic_write_json`) is **not** write-once and is never described or used as such.
  - `create_native_session(store, ...)` and `bind_agent_session(store, ...)` — thin seam wrappers over the two additive `SessionStore` operations below; **the only sanctioned Native call sites** for record creation and external-ID binding (structural guard below).
  - **Native state vocabulary (bijection, defined once here):** `to_native_state(persisted)`/`to_persisted_state(native)` map the authority/API term `active` to the existing on-disk compatibility value `open` (`STATE_OPEN`) at the shared-store boundary; `closed` and `quarantined` persist 1:1 and stay unambiguous. Every Native read/decision surface (RunTask, `validate_native_binding` diagnostics, later arsd) speaks the canonical Native vocabulary `active | closed | quarantined`; the persisted file never carries `active`. Round-trip is test-pinned in both directions.
  - **Constructor contract (binding seam):** these helpers are the only sanctioned way Native code obtains stores. Direct `SessionStore(...)`/`EventStore(...)` construction anywhere in `native_acp/` outside `storage.py` is a test failure (structural guard below). No Native call path may rely on any store default root, and `storage.py` itself never references legacy `sessions`/`runs` path segments.
  - All Native create/open/list, external-ID bind, lease (`acquire_lock`/`update_lock_holder`/`release_lock`), quarantine (`mark_quarantined`), and `commit_last_effective` operations in this and every later slice execute against a store instance obtained through this seam (creation/bind additionally via the seam wrappers); the operations themselves stay root-agnostic (below) — isolation comes entirely from construction.
- **Modify (`session.py`, additive; existing functions untouched unless listed):**
  - `SessionRecord` new optional fields, all defaulting `None` and omitted when unset (exact `mcp_config_*` pattern at `:602`): `session_kind` (absent ⇒ legacy acpx; `"native"` for native records), `native_profile_id`, `native_profile_revision`, `native_profile_hash`, `agent_session_id` (external), `owner`, `namespace`, `last_effective_model`, `last_effective_effort`, `quarantine_reason`, `quarantined_by_run_id`.
  - **Legacy-only field representation:** `role_id`, `role_hash`, `policy_hash`, `acpx_version`, `adapter_agent`, `acpx_session_id` become typed-optional and are **omitted** from Native `session.json` — never serialized as `null` and never given fake/sentinel values; `_record_to_dict:602` extends the exact `mcp_config_*` omit-when-unset pattern, and `_record_from_dict:650` already reads tolerantly. Legacy records always carry these fields today, so their serialized key set and bytes are unchanged (golden-pinned below). Dataclass default mechanics are implementation freedom; the persisted key set per record kind is the contract.
  - **New `SessionStore.create_native_session(*, session_id, profile_id, profile_revision, profile_hash, owner, namespace, workspace_hash, effective_cwd, matched_root, now=None) -> SessionRecord` — the only Native record creation API,** reachable from Native code solely via the `storage.py` seam wrapper. Contract: it takes **no** `AgentRoleSpec` and does not accept, synthesize, or default any legacy role hash, policy hash, acpx version, adapter, acpx session identifier, or sentinel value. Field provenance is fixed: profile identity from the resolved frozen `AgentProfile` (the same values sealed into `AgentRunSpec.agent`); `owner`/`namespace` from the authenticated caller identity frozen in `AgentRunSpec.identity`; workspace binding (`workspace_hash`/`effective_cwd`/`matched_root`) from the validated workspace sealed in `AgentRunSpec.workspace` (the Spec's binding hash — never the legacy role-based `workspace_hash(role, …)`); `session_kind="native"`; `state` created as canonical `active`, persisted as `STATE_OPEN` per the bijection; `schema_version`/`created_at`/`updated_at` as legacy; `agent_session_id=None` at creation. Mechanics reuse the existing create path (`secure_mkdir` + `exclusive_create_bytes`); legacy `create_session:176` is untouched.
  - **New `bind_agent_session(session_id, *, agent_session_id, now=None)`** — commits the external Agent Session ID **exactly once**, after the owning Run's first successful `session/new`; any second bind (same or different value) is refused (generalized rebind stays a non-goal). `validate_native_binding` on the `session/load` path requires it present.
  - New constant `STATE_QUARANTINED = "quarantined"`; new `SessionQuarantinedError(SessionClosedError)` so existing acpx catch-sites still catch; `ensure_open` raises it when `state == STATE_QUARANTINED` (acpx records never carry that state — no acpx behavior change).
  - New `validate_native_binding(record, *, profile, workspace_result, owner, namespace)` — hard fail-closed on profile/agent-type, workspace, owner, namespace mismatch, or `quarantined`; **model/effort differences are not a mismatch** (a new Run's frozen Spec is the legitimate switching input). Existing `validate_binding` untouched.
  - New `mark_quarantined(session_id, *, reason, run_id, now=None)` — serialized under the **same per-session `_session_lock_guard`** as `mark_closed:278` and the lease surface (one guard, so state transition vs lease minting is a single serialized decision), irreversible (no un-quarantine API; v1 contract), idempotent-safe; it never unlinks an existing `lock.json`.
  - **Quarantine-aware atomic lease (closes the pre-lock TOCTOU):** `acquire_lock` gains an optional `required_state: str | None = None` parameter — default `None` preserves exact legacy behavior and no acpx call site is edited. When set, the current `session.json` is re-read and required to equal that persisted state **inside the same `_session_lock_guard` critical section** that inspects, reclaims, and creates `lock.json` (covering the fresh-create, TTL-expired, and `reclaim_crashed` replacement paths alike); a mismatch raises `SessionQuarantinedError`/`SessionClosedError` as appropriate and neither creates nor unlinks any lock. Native callers always pass `required_state=STATE_OPEN`. A pre-lock `ensure_open()` may remain as fast-fail courtesy, but the in-guard check is the correctness mechanism — the plan no longer relies on `ensure_open()` followed later by `acquire_lock()`.
  - **Release/cleanup semantics:** `release_lock` stays token-gated and unchanged; a quarantining finalizer keeps its already-held lease valid for its own finalization writes and releases it on all paths (C8); after quarantine, every new `acquire_lock(required_state=STATE_OPEN)` refuses — a quarantined Session never returns a usable new lease, including via expired/crashed-holder reclamation.
  - New `commit_last_effective(session_id, *, model, effort, now=None)` — atomic record update; **commit timing contract:** called only after an exact-readback success inside the owning Run (post-readback, pre-prompt), and after a proven rollback (restoring the prior pair). Never written from agent-side drift; drift observed without a local set is a `config_drift_observed` evidence event (C8/C9), not a record write.
  - **Root-agnosticism note:** the new operations are store-scoped like the existing lease/close surface — they act on whatever base dir their `SessionStore` was constructed with and contain no root-resolution logic. No acpx call site constructs a `native-sessions/` store and no acpx call site is edited, so the new state/fields remain unreachable from legacy paths; store defaults in `session.py` are not modified.
- **Tests:** create `tests/native_acp/test_native_session_record.py`: byte-identical golden — a 0.1.7-shaped acpx record serializes to the exact pre-change bytes (zero-migration proof); **Native creation contract (RED/GREEN):** `create_native_session` → `open_session` → serialize round-trip; the created `session.json`'s exact key set contains **no** legacy role/policy/acpx key and no sentinel value (no-fabrication proof); `bind_agent_session` commits once and any second bind is refused; **`active ↔ open` compatibility:** a record created as canonical `active` persists `"open"` on disk, reads back as `active` through the Native vocabulary mapping, and `closed`/`quarantined` round-trip 1:1; native record round-trip with omit-when-unset; `validate_native_binding` matrix (profile/workspace/owner/namespace/quarantined/missing-`agent_session_id`-on-load ⇒ fail; model/effort delta ⇒ pass); quarantine irreversibility + `ensure_open` refusal + lease refusal for quarantined; **lease-vs-quarantine races, both interleavings (deterministically serialized on the shared guard):** (a) quarantine commits first ⇒ `acquire_lock(required_state=STATE_OPEN)` refuses and `lock.json` is never created; (b) lease minted first ⇒ a concurrent `mark_quarantined` blocks on the guard then commits exactly one transition, the holder's lease stays valid for finalization, and every subsequent acquire refuses — no interleaving yields a usable new lease on a quarantined record and no TOCTOU window remains (also pinned through the expired-lock reclamation path); `session_inspect._read_record_state` behavior for a `quarantined` record pinned exactly as the C1 inventory classified it (legacy acpx inspection surface; Native surfaces use the storage.py vocabulary mapping); `commit_last_effective` atomicity. Existing session tests (exact filenames enumerated at DoR via `ls tests`) stay green. **Plus create `tests/native_acp/test_native_store_isolation.py` (L1 isolation regression + structural guard):**
  - Seam binding: `native_session_store(root)` yields a store rooted at `root/native-sessions` and `native_event_store(root)` at `root/native-runs` (asserted via the store's actual recorded base-dir attribute, pinned at implementation); creating a Native record materializes files only under `native-sessions/`.
  - Secure-mode pins: after seam construction, `native-sessions/` and `native-runs/` are `0700`; a pre-seeded insecure root (e.g. `0755`) is corrected to `0700` by the seam's `secure_mkdir` call (the pinned chosen behavior); per-session and per-run directories are `0700`; `session.json`/`lock.json`/run artifacts are `0600`.
  - Same-ID no-collision (sessions): seed `root/sessions/` with a legacy-shaped record for session id S (golden fixture bytes); create a Native record with the **same id S** through the seam; both coexist; Native open/list return only Native content; the legacy record is never visible through the Native store.
  - Same-ID no-collision (runs): with `root/runs/<id>/` pre-seeded, `native_event_store(root).create_run` with the same id succeeds and creates `native-runs/<id>/` without touching `runs/<id>/`.
  - No-legacy-read proof: the seeded legacy same-ID record is poisoned (structurally invalid content); every Native operation (create/open/list, `acquire_lock`/`update_lock_holder`/`release_lock`, `mark_quarantined`, `commit_last_effective`, and the `validate_native_binding` load path) succeeds against the Native record — any read of the legacy root would surface the poison as a parse error or wrong content.
  - No-legacy-write proof: a byte snapshot (recursive directory listing + file contents) of `root/sessions/` and `root/runs/` taken before the full Native operation matrix equals the snapshot taken after — no new, changed, or deleted entries.
  - Structural call-site guard (future-accident pin): an AST scan of every module under `src/agent_run_supervisor/native_acp/` asserts (a) `SessionStore`/`EventStore` constructor calls appear only in `storage.py`, (b) no string literal exactly equal to `"sessions"` or `"runs"` appears anywhere in the package (`"native-sessions"`/`"native-runs"` appear only in `storage.py`), (c) no module in the package references `AgentRoleSpec`, and (d) `create_native_session`/`bind_agent_session` call sites within the package appear only in `storage.py` (the seam wrappers); plus a repo-level pin that no module outside `native_acp/` calls them (beyond the `session.py` definitions and their tests) — acpx surfaces never create Native records. The scan walks the package, so modules added by C7–C9 are covered automatically as they land.
- **RED (two focused commands, run before implementing):** `uv run pytest tests/native_acp/test_native_session_record.py -q` → `TypeError: SessionRecord.__init__() got an unexpected keyword argument 'session_kind'` (and `AttributeError: 'SessionStore' object has no attribute 'create_native_session'` for the creation-contract cases); `uv run pytest tests/native_acp/test_native_store_isolation.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp.storage'`.
- **GREEN:** additive fields + the new native operations (creation/bind/binding-validation/quarantine/commit + the `required_state` lease check) + the `storage.py` seam; no existing call-site edits; both new suites and the structural guard green.
- **Commit:** `feat(session): native-only session creation contract, quarantined state, quarantine-atomic lease, last-effective observations, isolated native-sessions store seam`

### 4.6 Slice C7 — permission bridge + event normalizer + bounded evidence writer

- **Goal:** default-deny mediation (no RBAC/policy engine) and the evidence surfaces, honest about being cooperative-agent policy enforcement, not an OS sandbox.
- **Inspect:** `role.py:11` (permission-kind vocabulary), `live_stream.py:23` + `hermes_caller/events.py:39` + `docs/design/result-event-schema.md` (normalized-event schema: `type`/`kind`/`status`/`text_length`/`key_summary`, integer `seq`; structural fields only), `event_store.py:22-57` (`RunHandle.append_ndjson`), and the **live** acpx fail semantics for unexpected permission prompts: `policy.py:132,152,172` (`--non-interactive-permissions fail` compile) plus the current `fixtures/acpx-0.12.0` fixtures and the C1-pinned external SDK contract. Permission behavior is grounded in current authority, live source, current fixtures, and the pinned external contract only — no archive directory is implementation input.
- **Create:** `native_acp/permissions.py` (`PermissionBridge`: clientCapabilities declaration built from the frozen `execution_grant` — first-E2E grant: fs read allowed inside the bound workspace, write refused, terminal not provided; mediation mapping table: workspace-internal read → allow; write/create/delete/terminal/execute/fetch and **any unregistered request type** → deny + `MediationEvent{requested_op, decision, reason}` into evidence; unexpected permission request ⇒ deny + fail-turn flag, aligning with acpx fail semantics; default deny throughout; grant snapshot only — never a runtime re-read), `native_acp/events.py` (`NativeAcpEventNormalizer`: ACP session updates → the existing normalized-event schema; unknown update types → `key_summary`, structural fields only; never copies text bodies — `text_length` only), `native_acp/event_writer.py` (per-Run single `EventWriter`: monotonic `seq`, bounded queue with producer timeout ⇒ controlled-run-failure signal, `max_event_bytes` cap writing a truncation marker while preserving lifecycle/permission/error families; writes through the run handle it is given — no store construction).
- **Tests:** create `tests/native_acp/test_permissions.py` (full decision table incl. default-deny for unknown ops; every decision emits a MediationEvent; read outside the bound workspace ⇒ deny), `tests/native_acp/test_events_normalizer.py` (golden mappings; adversarial update carrying a verbatim body yields `text_length` only), `tests/native_acp/test_event_writer.py` (seq monotonicity, queue-full timeout signal, byte-cap truncation with preserved families). These modules operate on internal shapes (C5's client adapts SDK objects), so the tests run without the extra.
- **RED:** `uv run pytest tests/native_acp/test_permissions.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp.permissions'`.
- **Commit:** `feat(native-acp): default-deny permission bridge, event normalizer, bounded evidence writer`

### 4.7 Slice C8 — `RunTask` vertical: admission → spawn → drive → markers → finalization

- **Goal:** the coordinating per-Run object (Stage-2 arsd will wrap it; no arsd code here): admission assembly, ManagedProcess + driver coordination, double markers, finalization per the architecture §5 terminal table, lease binding, top-level exception guard, and **explicit Native store wiring** — evidence to `native-runs/`, records to `native-sessions/`, both bound only through the C6 seam. Direct embedding of `RunTask` is the sanctioned test/dev path (architecture §9: production is arsd-only).
- **Inspect:** `event_store.py:59-75` (`EventStore.create_run` — reused **unmodified**; the instance is constructed on the `native-runs/` root via `native_acp.storage.native_event_store`; `EventStore` already accepts an explicit base dir, so no additive parameter and no default change in `event_store.py` is needed or allowed), `event_store.py:110` (`exclusive_create_bytes` — the primitive under the C6 `write_once_json` wrapper used for every immutable artifact below), `redaction.py` (`redact_text` usage as in `runner.py:144,317,355`), `session.py` lease surface (C6), `native_acp/storage.py` (C6 seam), `runner.py:775` (`progress.json` shape).
- **Create:** `native_acp/run_task.py`:
  - Store wiring: a `RunTask` binds, at construction, an explicit Native store pair — `EventStore` on `<supervisor_root>/native-runs/` and `SessionStore` on `<supervisor_root>/native-sessions/` — obtained exclusively through the C6 seam (`native_event_store`/`native_session_store`), either built by `RunTask` from a caller-supplied supervisor root via those helpers or injected pre-built by an embedding that obtained them the same way. `run_task.py` performs no direct `SessionStore`/`EventStore` construction (the C6 structural guard covers it) and no root discovery or defaulting. As a fail-fast belt-and-suspenders, `RunTask` validates at construction — using the `storage.py` dirname constants — that the bound stores' base dirs terminate in `native-runs`/`native-sessions` and refuses anything else. Every artifact write below goes through the Native `EventStore` run handle; every record/lease/quarantine/`commit_last_effective` call goes through the Native `SessionStore`. Legacy store defaults in `event_store.py`/`session.py` are untouched, and legacy code is never taught to read Native roots.
  - Admission: resolve profile → freeze hashes → `ResolvedLaunchSpec` → seal `AgentRunSpec` → write `spec.json` (exclusive create) and `launch.json` (no secrets) into the Run's `native-runs/` directory via the Native run handle, then spawn.
  - Drive: `spawn_managed_process` → driver `initialize` → `new_session` (this slice; the returned external ID is then committed exactly once via the seam's `bind_agent_session`) → observe `EffectiveRunState` (`effective.json`, write-once) → config-fidelity machine → `prompt-dispatch-started` marker (write-once, created immediately before the wire write attempt — the conservative uncertainty boundary) → `prompt_once` → `prompt-accepted` marker (write-once; semantics below) → events through writer/normalizer → bounded `stderr.log` (redacted) → terminal `result.json` (write-once) + mutable `progress.json` (atomic replacement — not write-once) — all under `native-runs/<run_id>/`.
  - **Write-once artifact rule:** immutable `spec.json`, `launch.json`, `effective.json`, both dispatch markers, and terminal `result.json` are created exclusively through the single C6 `write_once_json` wrapper over `exclusive_create_bytes`; a second creation attempt raises (surfaced as a controlled error) and can never overwrite the first bytes. Mutable `progress.json` uses atomic replacement and `events.jsonl` uses the bounded single-writer append; ordinary atomic replacement is never described or used as write-once.
  - **`prompt-accepted` semantics (precise):** `prompt-accepted` is a historical filename/label meaning **only** that the complete local ACP prompt frame was written to the supervised transport and `drain()` returned successfully — a local write-completion fact. It is **not** a remote Agent acceptance, execution, or semantic ACK. No first Agent update is required to create the marker; a later Agent update or terminal event is separate corroborating evidence, never part of the marker's meaning. `prompt-dispatch-started` remains the conservative uncertainty boundary created immediately before the write attempt; finalization and the `unknown` row key off `started` alone, and `prompt-accepted` never upgrades certainty or substitutes for an ACP terminal.
  - Session binding: for session-bound Runs, the record is created (when new) via the seam's `create_native_session`, then on the Native `SessionStore`: `acquire_lock(reclaimable=False, required_state=STATE_OPEN)` — the C6 in-guard atomic quarantine/closed check; no reliance on a separate pre-lock `ensure_open()` → `update_lock_holder` with the ManagedProcess child identity → release on all paths; `session_reuse=none` uses an internal ephemeral record (still in `native-sessions/`, created through the same seam) closed at terminal state.
  - Finalization: a pure function `finalize_run_state(observations) -> (run_status, session_disposition)` encoding the architecture §5 terminal table — result-exists ⇒ keep (irreversible); terminal-event-without-result ⇒ rebuild; reliable ACP terminal + expected reap ⇒ completed/cancelled/failed/timed_out; dispatch-started + no reliable terminal + observation interrupted ⇒ **unknown** (+ Session quarantined, `retryable=false` persisted in the payload); supervisor-present + proven abnormal exit of the identity-matched child ⇒ failed + quarantined; supervisor cancel/timeout with ACP terminal ⇒ cancelled/timed_out + active, escalated-kill-after-dispatch ⇒ quarantined; pre-dispatch failure ⇒ failed, 0 Turn, session stays active. Exit-code detail via `classify_exit` is subordinate: it can never produce a completed/cancelled-class terminal for a dispatched Turn without an ACP terminal.
  - Top-level exception guard: any per-Run exception (normalizer/evidence IO/SDK) ⇒ controlled terminal state + evidence, never propagation (arsd robustness seam, provable now).
- **Tests:** create `tests/native_acp/test_finalization_table.py` (L1 pure: every table row; `unknown` payload carries `retryable=false`; irreversibility — existing `result.json` never rewritten; `retry_of_run_id` on a successor never mutates the original), `tests/native_acp/test_run_task.py` (L2 via fake agent: happy vertical produces the full artifact set with both markers in order; marker write-once creation makes a duplicate dispatch impossible; **marker-semantics pins:** a fake-agent variant whose transport write and `drain()` succeed but which emits **zero** session updates still yields `prompt-accepted` (no first update required), and an observation-lost variant with **both** markers present still finalizes `unknown/quarantined/retryable=false` (the marker never upgrades certainty); kill-after-dispatch ⇒ failed + session quarantined; spawn failure and fidelity failure ⇒ failed, 0 Turn, session active; injected normalizer exception ⇒ controlled failure; lease held during and released after; **write-once terminal facts:** a duplicate terminal-`result.json` creation attempt fails and the first bytes are provably unchanged, and two concurrent finalizers racing the same Run produce exactly one winner (the write-once create decides); the same duplicate-refusal is pinned for `spec.json`, `launch.json`, `effective.json`, and both markers; **store-isolation vertical — the L2 half of the C6 regression:** the happy vertical runs against a supervisor root pre-seeded with legacy artifacts — `sessions/` holding a poisoned record whose session id equals the Run's ARS session id, and `runs/<run_id>/` holding a fixture with the same run id; the Run succeeds; every new artifact lands only under `native-runs/<run_id>/` and `native-sessions/`; the pre-seeded `sessions/`/`runs/` byte snapshots and directory listings are unchanged; the Run's result/evidence reflect only Native state; repeated for the kill-after-dispatch branch to prove quarantine state is written into the `native-sessions/` record only; **constructor guard:** `RunTask` constructed with a store rooted at legacy `sessions/`/`runs/` is refused).
- **RED:** `uv run pytest tests/native_acp/test_finalization_table.py -q` → `ModuleNotFoundError: No module named 'agent_run_supervisor.native_acp.run_task'` (the run-task and isolation tests share this RED).
- **Commit:** `feat(native-acp): RunTask vertical — admission, double markers, finalization table, isolated store + evidence wiring`
- Core G8 evidence.

### 4.8 Slice C9 — `session/load` continuity + controlled cross-Run switching

- **Goal:** PRD R4: same external session across process-per-Run; per-Run frozen model/effort (mid-Run switching structurally impossible); partial-failure rollback or quarantine; no generic rebind subsystem.
- **Inspect:** C5 driver `load_session`; C6 `validate_native_binding` / `commit_last_effective` / `mark_quarantined`.
- **Modify/extend:** `run_task.py` + `config_fidelity.py` switching branch: precondition (previous Run terminal; lease acquired via `acquire_lock(required_state=STATE_OPEN)` — the persisted active/open state check is atomic with lease minting per the C6 contract, never a separate pre-lock read) → new Run freezes the new pair in its Spec → spawn → initialize → **capability check** (`requires_session_load` advertised; else fail + escalate per G6) → `session/load(agent_session_id)` with external-ID-unchanged assertion (any `session/new` on this path ⇒ hard fail — silent re-creation forbidden) → discovery (target model must be advertised) → set model → fresh dependent set → set effort → exact readback → `commit_last_effective` → markers → prompt. Partial failure: no prompt; record observed partial changes as evidence; roll back to the session's `last_effective_*` pair **with exact readback proof**; rollback proven ⇒ Run `failed` (0 Turn, fidelity error) + session re-opened; rollback failed/unprovable ⇒ `mark_quarantined` (Run still `failed`). Cross-agent-type reuse refused by `validate_native_binding` (new Session + Hermes-side context handoff is the only path). All session-record access on this path continues through the C6-seam store already bound by `RunTask`; C9 adds no store construction site (the C6 structural guard automatically covers the new modules/lines).
- **Tests:** extend the fake agent with load/switch scripting; create `tests/native_acp/test_session_switching.py` (L2): load-reuse happy path (external ID unchanged, no new-session event); silent-new detection ⇒ fail; switch happy path (model then effort; `last_effective_*` committed only after readback); each failure branch (set-model rejected; effort missing post-model; inexact readback; rollback-success ⇒ active; rollback-failure ⇒ quarantined); quarantined session refuses lease and new Runs; `retry_of_run_id` linkage leaves the original `unknown`/`failed` record untouched.
- **RED:** `uv run pytest tests/native_acp/test_session_switching.py -q` → `AttributeError` on the missing switching entry point (driver/run_task lack the load/switch surface until implemented).
- **Commit:** `feat(native-acp): session/load continuity and controlled cross-run model/effort switching with rollback/quarantine`
- Completes G8 (switch branches) at L2.

### 4.9 Slice C10 — real OpenCode 1.18.4 B-grade smokes (opt-in)

- **Goal:** Stage-1 GREEN against the real agent. Deterministic RED discipline is carried by C5–C9's L2 suite; this slice's failures are real-world evidence, triaged and reported, never papered over.
- **Preconditions (checked and reported first — G3/G6):** `ARS_NATIVE_SMOKE=1`; the executable resolved by `OPENCODE_1_18_4` reports version **1.18.4** (mismatch ⇒ stop: the profile is closed); Kimi Code credentials and literal K3 access are present through registered credential slots; at least one second model is both advertised by the same live Agent/provider and actually usable. The execution evidence records and explicitly approves that exact advertised model ID before the switch; the plan never guesses or aliases it. A missing prerequisite is reported as a named gap — an effort-only switch never substitutes for the model-switch acceptance. This is the **full G3 gate re-run with real credential/model usability, fail-closed**; the §2 DoR advisory readiness snapshot never substitutes for it.
- **Second-model decision note (C10, chair-approved):** real OpenCode 1.18.4 advertises the `effort`
  selector model-dependently — for `kimi-for-coding/k3` (literal choices low|high|max) but for
  neither kimi candidate second model, which made the original candidates unusable under the exact
  sequence (named gap, fail-closed, rollback proven). Per chair decision the exact model+effort
  contract is kept unchanged and the registered second model is **`deepseek/deepseek-v4-pro`**
  (already-configured `deepseek` credential slot; zero-prompt capability probe shows literal effort
  choices high|max in its post-set-model set). `OPENCODE_1_18_4` revision 2 registers the closed
  model pair and the second credential slot name; the S3 switch acceptance runs
  K3/max → deepseek-v4-pro/high → K3/max on one external Session via `session/load`. Sanitized
  evidence: out-of-Git C10 records (`phase-a-second-model-selection`, `s3-model-switch`).
- **Create:** `tests/native_acp/test_real_opencode_smoke.py`, env-gated (skips in CI; operator-executed for Stage-1 exit), each smoke in a disposable empty workspace under a fresh temp dir **outside any tracked worktree**, with direct pre/post directory-listing assertions (both must be empty — the primary no-change evidence; `workspace_hash` is a binding-config hash and `git status` is never used as change evidence):
  1. **S1-equivalent read-only run** (new session): initialize capabilities recorded — **G6 checkpoint: `loadSession` advertised**; exact k3/`max` sequence with both discovery snapshots persisted; exactly one `session/prompt`; `stop_reason=end_turn`; result carries final_message and exact effective pair; normalized events + both markers + `redaction-report.json` with `matches: []`; workspace listings empty; no leftover processes (identity-probe on the recorded pgid/pid).
  2. **Continuity across process-per-Run:** R1 plants a random nonce; R2 on the same ARS session goes through `session/load` (external ID unchanged) and asks for recall; R2's final_message must contain the nonce. This is the **context-token continuity** proof the zero-prompt cross-process probe explicitly did not provide.
  3. **Switch smoke:** across R2/R3, at least one real model-ID switch and one effort switch (e.g., `max→high`), each with exact readback and `last_effective_*` commit verified; frozen Spec vs `EffectiveRunState` equality asserted per Run.
- Evidence handling: run stores (`native-runs/`, `native-sessions/`, bound via the C6 seam from a disposable supervisor root inside the temp dir) live under the temp root; the test prints artifact paths; the operator extracts a redacted summary out-of-band. Nothing is committed; artifacts may contain model output and stay out of git.
- **Verify:** smoke green in the operator environment; full deterministic suite still green; clean-process guard after each smoke.
- **Commit:** `test(native-acp): opt-in real OpenCode 1.18.4 B-grade smokes (readonly, load-continuity, switch)`

---

## 5. Gates G3–G8: order, evidence, stop/escalate

Execution order: **G4 + early G3 readiness snapshot (advisory, §2) → (Stage-0 SDK contract) → G5 → G7(L2 halves: C3, C5) → G8(L1/L2: C2, C6, C8, C9) → G3 full gate → G6 → G7/G8 real completion (C10)**. G1 (implementation authorization) precedes everything and is satisfied only by §8. The vNext-only authority reset is a completed documentation prerequisite in this tree; Stage-2 arsd source and G12 policy still require separate approvals.

| Gate | Where it runs | Pass evidence | On failure |
|---|---|---|---|
| G4 fresh-check | DoR (§2), before any edit | worktree `HEAD ==` freshly fetched `origin/main`, zero branch diff, and source/API/CodeGraph/tests match this plan | STOP; report delta; no edits |
| Stage-0 SDK contract | C1 | version/import-origin/symbol/I-O-model tests green | BLOCKED report naming exact gap (e.g., no session-load API); no workaround; acpx untouched |
| G5 status-consumer audit | C1 (decision), C2 (implementation) | deterministic out-of-Git evidence record (every inspected path/symbol; `failed/cancelled/unknown` behavior; tolerant-vs-exhaustive class; final decision + rationale) — grep/CodeGraph are discovery signals, never the proof; C2 consumer-behavior tests for every intolerant/semantically meaningful consumer prove zero acpx coercion | Intolerant consumer found ⇒ switch to superset branch (documented — this is a planned branch, not an escalation) |
| G7 live-ACP ownership | C3 (spawn/identity/group/reap/bounded stderr), C5 (exclusive wire, single stdout consumer, cancel), C10 (real) | listed tests green + smoke 1 clean-process check | Slice stops; defect fixed under TDD; if the SDK's I/O model cannot coexist with supervisor ownership ⇒ BLOCKED report |
| G8 state proofs | C2 (round-trip), C6 (quarantine persistence, quarantine-atomic lease races, store isolation), C8 (finalization table, markers, write-once terminal facts, irreversibility, seeded-legacy isolation), C9 (switch/rollback), C10 (real terminal evidence) | `unknown` round-trip with persistent `retryable=false`; double markers; all table branches; `retry_of_run_id` non-rewrite; zero replay paths exist | Slice stops; any table ambiguity discovered ⇒ report (table is settled design; do not improvise new rows) |
| G3 run prerequisites | advisory non-secret readiness snapshot at DoR (§2); full fail-closed gate at C10 start | K3 creds usable; second credentialable model confirmed; binary 1.18.4 — real usability, not the advisory snapshot | Report named gap (credential/model/binary); Stage-1 exit blocked on the missing smoke; **never** downgrade (no effort-only substitute, no version drift acceptance, no guessed alias). A `not_ready`/`unknown` DoR snapshot does not block C1–C9 but stays visible in every stage-boundary report |
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
                     # doctor + replay smokes, uv lock --check (internal canonical-verifier
                     # gate from C1 onward — not a separate operator command; §2 runs it
                     # explicitly only for the pre-C1 DoR baseline), docs index --check,
                     # drift --check, static_safety_scan (secrets / forbidden imports /
                     # stale phrases), check_version_sync, build + twine check,
                     # installed-wheel smoke, check_roadmap_governance, git diff --check
```

Additional gates layered on top:
- **Docs generation is not part of C1–C10.** Implementation runs docs tools in `--check` mode; any authority/status update is a separately reviewed documentation sync after behavior and evidence are complete.
- **No-leak scans:** `static_safety_scan.py` (secret-shaped values; forbidden network imports — the SDK is not on that list; stale phrases) plus the C4 test pinning that credential values never serialize, plus manual review of added lines per `docs/roadmap/verification.md:41`.
- **Store-isolation regression:** the C6 L1 suite (`test_native_store_isolation.py`) and the C8 L2 seeded-legacy vertical run inside every full-suite/`make verify` invocation from their slices onward; the structural guard (Native store constructors only in `native_acp/storage.py`; no legacy `sessions`/`runs` string literals in the package; Native record creation/external-ID bind only via the seam wrappers; no `AgentRoleSpec` reference in the package; `RunTask` refusing non-native-rooted stores) automatically extends to modules added by later slices, so a future accidental legacy-root default is a suite failure, not a review catch.
- **Wheel/base-install smoke:** the built wheel without the `native` extra must import and run `doctor` (already in `make verify`); one L1 test additionally pins that `import agent_run_supervisor.native_acp` succeeds without the SDK and only SDK-needing modules raise `NativeSdkUnavailableError` on use.
- **Clean-process/worktree guard:** after any L2/real run: no surviving child processes (identity-probe the recorded pids/pgids; report a `pgrep -f opencode` check for the operator after smokes), `git status --porcelain` clean, no `.tmp-*` debris in stores.
- **Version-sync:** untouched (no bump in this plan); `check_version_sync.py` must stay green precisely because nothing was bumped.

---

## 7. Rollback and compatibility

- **acpx stays the default and only wired surface.** Stage 1 adds no CLI/commands wiring for Native (`commands.py`/`cli.py` untouched; that is Stage-2 scope per architecture §9). Nothing existing changes behavior: `runner.py`, `parser.py`, `policy.py`, `session_runtime.py` have empty diffs; `exit_classifier.py`/`result.py`/`session.py` diffs are additive with regression pins (C2 zero-coercion sweep; C6 byte-identical serialization golden).
- **No Native→acpx fallback:** no code path constructs acpx invocations from `native_acp/`; structurally pinned by `test_no_acpx_coupling.py`.
- **No schema migration:** Native uses new store roots (`native-runs/`, `native-sessions/`) bound exclusively through the C6 `storage.py` seam; legacy artifacts are never read or rewritten — proven by the C6 poisoned-record/byte-snapshot regressions and the C8 seeded-legacy vertical, not assumed by convention; new `SessionRecord` fields are omit-when-unset so pre-existing `session.json` files round-trip byte-identically; acpx `result.json` payloads are unchanged.
- **Rollback:** before merge — drop the branch/worktree (`git worktree remove`, `git branch -D`); nothing outside the worktree changed. Per-slice — `git revert` of the offending commit(s); slices are ordered so reverting from the tail leaves a coherent tree. Dependency rollback — reverting C1 removes the extra/lock lines and the CI/Makefile/verifier lock-gate lines; base runtime never depended on the SDK. Store rollback — deleting `.agent-run-supervisor/native-*` directories removes all Native state without touching acpx artifacts.
- **Sachima:** pin `agent-run-supervisor==0.1.7` and the backend Protocol are untouched; no release happens under this plan, so Sachima cannot observe any of this work.

---


## 8. Separate approval packages

Approvals are independent and non-transitive.

1. **Local Stage 0/1 implementation (C1–C10):**

   > I approve local implementation of C1–C10 from `docs/plans/active/2026-07-21-vnext-stage01-native-acp-implementation.md`. Start a new `feat/native-acp-stage01-implementation` worktree/branch from fresh `origin/main` after the §2 DoR passes. This covers the pinned Native SDK extra/lock, additive Native ACP source, L1/L2 tests, and opt-in real OpenCode B-grade smokes. It covers no push, PR, merge, Stage 2/arsd, caller-UID policy, service/cgroup enablement, deployment, release/publication, Sachima, Gateway/IM, or live behavior.

2. **Push and open a new PR after Stage 1 done criteria:**

   > I approve pushing `feat/native-acp-stage01-implementation` and opening a new PR to `main`; merge remains separate.

3. **Merge the new exact-head PR after review/CI:** separate approval bound to that PR/head.

4. **Stage 2 and later:** `arsd`, G12 caller policy, cgroup/service acceptance or enablement, release,
   Sachima/Gateway/IM/live work each require later explicit scope and approval.

## 9. Risks and stop conditions

- SDK/client API or real OpenCode `session/load` may not satisfy the required semantics: stop and report.
- `AgentRunStatus` consumers may reject an additive `unknown`: choose the documented Native superset
  boundary only after C1 consumer evidence; never coerce to a legacy status.
- Partial model/effort switch is not transactional: zero prompt; exact rollback or quarantine.
- Native/legacy root isolation is load-bearing: any legacy read/write/collision is a blocker.
- Real permission and cgroup claims remain Stage 2; Stage 1 must not overclaim B-grade evidence.
- Any need for arbitrary launch/config passthrough, acpx fallback, long-lived AGENT processes between Runs,
  per-Run Worker, or cross-AGENT Session reuse is architecture drift and stops implementation.

## 10. Non-goals

No re-execution of archived documentation slices; no reuse of an archived branch or mutation of a merged PR;
no arsd/UDS server; no service/cgroup enablement or deployment; no caller-UID policy activation; no
release/tag/PyPI; no Sachima/Gateway/IM; no runtime plugin platform; no arbitrary command/argv/env/JSON;
no acpx fallback or shared session store; no content-digest service/watcher/sandbox claim; no production
acceptance from fake or Stage 1 evidence.

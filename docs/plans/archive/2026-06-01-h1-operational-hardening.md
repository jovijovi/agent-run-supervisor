---
title: "H1 Operational Hardening Plan"
status: archived
created_at: 2026-06-01
last_validated_at: 2026-06-01T00:00:00+0800
archived_at: 2026-07-07
---
> **Archived plan（冷区）：** 非 active 上下文。Roadmap 章节迁移见
> [`docs/roadmap/MIGRATION.md`](../roadmap/MIGRATION.md)。
> 验收摘要见对应 [`docs/roadmap/archive/phases/`](../roadmap/archive/phases/) 条目。

# H1 Operational Hardening Plan

> **Scope banner.** H1 implements the `H1 — Operational hardening` phase from
> `docs/roadmap/current-status.md` §3. It closes long-lived-use tails left open after
> S1: completing the read-only `doctor` probe set (`ARS-DOCTOR-COMPLETE` / PRD FR-10),
> adding safe run/session artifact retention/cleanup knobs (`ARS-RETENTION-CLEANUP` /
> `F-RETENTION-001` / PRD FR-8), documenting the result/event schema for caller stability
> (PRD FR-6/FR-7), and a **narrow, detection-first** crash/interruption tail beyond
> expired-lease replacement. It stays strictly a **local Python library + dev CLI**. It does
> **not** approve Sachima/Hermes/Gateway/IM integration, public ingress, real IM delivery,
> production config writes, automatic replies, real prompt/send during diagnostics, service
> restart, live/default-on behavior, `@all`, or agent-to-agent routing. Authoring this plan
> grants none of those.

## 1. Goal

Make `agent-run-supervisor` safe for repeated, long-lived **local** use by closing the H1
operational tails the roadmap already owns:

1. **Doctor completion** — `doctor` reports adapter availability, runtime `npx` fetch risk,
   policy parseability, role cwd/allowed-roots validation, a redaction self-check, and
   session readiness, all **read-only** and without ever launching an AGENT.
2. **Retention/cleanup** — a confined, dry-run-first cleanup surface for run/session
   artifacts under `.agent-run-supervisor/`, with provable deletion boundaries.
3. **Caller-stable schema docs** — a single authoritative document for `result.json`, the
   normalized event families, statuses/error codes, doctor output, and the cleanup plan
   shape, so callers can depend on the contract.
4. **Narrow crash/interruption tail** — *detection* of stale/expired locks and leftover
   temp artifacts plus safe removal of **provably expired** lock files; no process signals,
   no live-session takeover, no service lifecycle.

This plan is an execution artifact only. It must not redefine product goals, expand product
scope, or imply new live/runtime approvals (`docs/plans/README.md`, `docs/AI_FLOW.md`).

## 2. Current product position

Derived from `GOAL.md`, `docs/product/prd.md`, `docs/design/architecture.md`,
`docs/design/technical-solution.md`, `docs/roadmap/features.md`, and
`docs/roadmap/current-status.md`:

- **Product** = an independent, local-first Python library + dev CLI that supervises
  ACP/acpx AGENT runs and persistent sessions, normalizing runner output into redacted,
  auditable evidence. Two execution modes are required: one-shot exec **and** persistent
  sessions. The supervisor never emits a business verdict (`business_verdict` is always
  `null`).
- **E1 — one-shot exec runner**: **Done**, merged/closed on `main` via PR #8 (`21b3393`);
  `F-EXEC-001` Done.
- **S1 — persistent sessions**: **Closed for the local persistent-session lifecycle** after
  S1a–S1d plus closure acceptance (multi-turn continuity regression +
  `scripts/smoke_persistent_session.py` real-acpx smoke). `F-SESSION-001` Done for the local
  lifecycle.
- **H1 — operational hardening**: **Planned** — this plan. It is the next approved
  implementation phase.
- **I1 — thin caller integration**: **Parked** pending separate approval. Out of scope here.

**Base:** `origin/main` at `b2079ea`. Branch: `ai/h1-operational-hardening-2026-06-01`.
Worktree: `/home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/h1-operational-hardening-2026-06-01`.

## 3. Exact H1 target and why it is allowed by the roadmap

`docs/roadmap/current-status.md` §3 `H1 — Operational hardening` checklist (verbatim intent):

- [ ] Doctor probes adapter availability without launching AGENT work.
- [ ] Doctor detects runtime `npx` fetch risk.
- [ ] Doctor checks policy parseability safely.
- [ ] Doctor reports role cwd/allowed-roots validation.
- [ ] Doctor reports redaction probe.
- [ ] Doctor reports session readiness after S1.
- [ ] Retention/cleanup knobs exist for run/session artifacts.
- [ ] Result/event schema is documented for caller stability.

Tail register (§4): `ARS-DOCTOR-COMPLETE` (NEXT_PHASE → H1) and `ARS-RETENTION-CLEANUP`
(NEXT_PHASE → H1 / long-lived use) are both **Open** and explicitly scheduled for H1.
`F-RETENTION-001` is **Planned** with remaining acceptance "Cleanup API/CLI, retention
tests, no unsafe deletion." `F-CLI-003` (doctor) is **Partial** with remaining acceptance
"Adapter/npx/policy/cwd/redaction/session readiness probes." `architecture.md` §8.1 lists
`ARS-DOCTOR-COMPLETE` and `ARS-RETENTION-CLEANUP` as the open H1 tails.

**Why allowed:** the user has approved continuous execution of H1 for local operational
hardening. Every item above is roadmap-owned H1 work that closes already-accepted tails; none
of it redefines product scope or introduces a non-goal. The crash/interruption tail is named
in PRD FR-5 acceptance and `architecture.md` §4.1 control point 3 as an H1 carry-over and is
included here only in its safe, local, detection-first form.

### Open tails this plan closes vs. carries

| Tail | Disposition in H1 |
|---|---|
| `ARS-DOCTOR-COMPLETE` | **Close** — full read-only probe set (W1). |
| `ARS-RETENTION-CLEANUP` / `F-RETENTION-001` | **Close** — confined dry-run-first cleanup (W2). |
| Result/event schema docs | **Close** — caller-stable schema doc (W3). |
| Crash/interruption recovery beyond expired-lease replacement | **Partially close** — detection + provably-expired lock removal only (W4). Full process-liveness/live-takeover recovery stays an explicit carry-over (no real service/lifecycle expansion). |
| `ARS-SANDBOX-BOUNDARY` | **Carry** — parked; untouched. |
| `ARS-CALLER-INTEGRATION` / I1 | **Carry** — parked; untouched. |

## 4. Explicit non-approvals (unchanged by this plan)

From PRD §6 and `non-approvals.md` — none of these are introduced, implied, or enabled:

- Sachima behavior integration; real AGENT automatic replies; public ingress; real IM
  delivery; Gateway restart/reload/replace; production config writes; live/default-on
  behavior; worker auto-routing; participant persistence/management UI; `@all` fanout;
  agent-to-agent automatic routing; trusted Markdown/HTML rendering; treating `allowed_roots`
  as an OS/filesystem sandbox; per-run human approval as the default authorization model.
- **Diagnostics-specific:** `doctor` never launches an AGENT, never runs `acpx exec`, never
  runs a session `prompt`/`send`, never triggers an `npx` package fetch, and never restarts
  or signals any process. It only runs `--version`-style read-only probes and pure local
  computation.
- **Cleanup-specific:** no deletion outside a resolved `.agent-run-supervisor` artifact root;
  no following symlinks out of that root; no deleting open or live-locked sessions; dry-run is
  the default and real deletion requires an explicit flag.

## 5. Source-of-truth trace

- Product goal: `GOAL.md`.
- Product requirements: `docs/product/prd.md` FR-3 (cwd gate), FR-6 (events), FR-7 (status),
  FR-8 (artifacts/redaction/retention), FR-10 (doctor).
- Architecture: `docs/design/architecture.md` §2.1 diagnostics plane, §3.2 status
  classification, §4.1 control points, §5 artifact/redaction model + §5.2 future H1 layout,
  §6 boundaries, §8.1 open H1 tails.
- Technical solution: `docs/design/technical-solution.md` §3.2 policy, §3.3 workspace,
  §3.4 preflight/doctor, §3.6 session store/runtime, §3.8 classifier, §3.9 event store +
  retention, §4 data models, §5 artifact layout.
- Feature tracker: `docs/roadmap/features.md` `F-CLI-003`, `F-RETENTION-001`, `F-STORE-001`,
  `F-STATUS-001`, `F-PARSER-001`.
- Roadmap phase: `docs/roadmap/current-status.md` §3 `H1`, §4 tail register.
- Workflow + plan rules: `docs/AI_FLOW.md`, `docs/plans/README.md`.

## 6. Scope

### In scope

- **W1 Doctor completion** (`preflight.py`, `commands.py`): add read-only probes
  `probe_policy`, `probe_workspace`, `probe_redaction`, `probe_npx`, `probe_adapter`,
  `probe_session_readiness`; wire them into `cmd_doctor`; preserve `launched_real_agent: false`.
- **W2 Retention/cleanup** (`retention.py` new, `cli.py`, `commands.py`): a confined,
  dry-run-first `plan_cleanup` / `apply_cleanup` API and a `cleanup` CLI command.
- **W3 Schema docs** (`docs/design/result-event-schema.md` new): the caller-stable contract
  for `result.json`, session turn results, statuses/error codes, normalized event families,
  doctor output, and the cleanup plan shape; plus a fidelity test guarding it against drift.
- **W4 Narrow crash/interruption tail** (`session.py`, folded into W1/W2): read-only
  detection of stale/expired locks and leftover `.tmp-*` artifacts, and removal of
  **provably expired** lock files only.
- TDD coverage for every behavior change; authority-doc updates (PRD/design/feature
  tracker/current-status) after code/tests are green; regenerate `docs/INDEX.md` and
  `docs/lessons/_drift_report.md` via the tools.

### Out of scope / not approved in H1

- All PRD §6 non-goals listed in §4 above.
- Any probe that executes acpx subcommands, the adapter agent, a session prompt, or an `npx`
  fetch. Adapter "availability" is **declared + hostable**, not live enumeration (see §7.1).
- Process-liveness/PID inspection, killing/signaling processes, live-session takeover, remote
  session reconciliation, or service restart for crash recovery.
- Cleanup of anything outside `.agent-run-supervisor`; cleanup that follows symlinks out of
  root; deletion of open/live-locked sessions.
- New non-stdlib runtime dependencies (`AGENTS.md` tooling rule: stdlib-only unless a phase
  explicitly approves a dependency — H1 does not).
- Result-schema **changes**: H1 documents the existing schema and may add purely additive
  fields; it must not rename/remove existing keys or set `business_verdict` to anything but
  `null`.

## 7. Design

### 7.1 W1 — Doctor completion (read-only)

Today `cmd_doctor` (`commands.py:87`) runs `EventStore.permission_probe`, fixture replay,
optional role validation, and `probe_node`/`probe_acpx` (both `--version` only, per
`preflight.py`). H1 adds the remaining FR-10 probes. **All new probes are read-only and never
launch an AGENT.** Each returns a structured dict (never raises) so the CLI serializes
deterministically — matching the existing `preflight.py` `ProbeRunner` pattern, including a
1- or 0-arg injection seam so tests drive them without touching the real environment.

New functions in `src/agent_run_supervisor/preflight.py`:

- `probe_policy(role) -> dict` — **pure/local, no subprocess.** Calls
  `policy.compile_permission_policy(role)`, `json.dumps(...)` it, and confirms
  `defaultAction == "deny"`; also computes `policy.policy_hash(role)`. Returns
  `{"ok", "parseable", "default_action", "auto_approve_count", "auto_deny_count",
  "policy_hash", "error_detail"}`. Fails closed (`ok: False`, `error_detail`) if compilation
  raises. Honors persistent vs exec roles (both compile a permission policy).
- `probe_workspace(role, *, cwd=None) -> dict` — **pure/local.** Calls
  `workspace.validate_effective_cwd(role, cwd)`. On success:
  `{"ok": True, "effective_cwd", "matched_root", "allowed_roots_security_boundary": False,
  "disclaimer", "error_detail": None}`. On `WorkspaceValidationError`:
  `{"ok": False, ... , "error_detail": <message>}`. Always reports the
  `ALLOWED_ROOTS_DISCLAIMER`; never claims a sandbox.
- `probe_redaction() -> dict` — **pure/local, synthetic input only.** Runs **synthetic,
  non-secret** pattern-shaped samples through `redaction.redact_text`, `redact_env`,
  `redact_argv` and asserts none survive. Returns
  `{"ok", "patterns_exercised", "leaked", "error_detail"}` with `leaked == []` on success.
  Uses fabricated tokens that match patterns but are not real credentials, e.g.:

  ```python
  SAMPLES = {
      "openai_api_key": "sk-" + "A" * 24,            # matches \bsk-[A-Za-z0-9_\-]{8,}
      "bearer_token": "Authorization: Bearer FAKE-" + "B" * 12,
      "jwt": "eyJ" + "C" * 8 + "." + "D" * 8 + "." + "E" * 8,
      "pem_private_key": "-----BEGIN RSA PRIVATE KEY-----",
  }
  # ok == all(sample not in redact_text(sample)[0] for sample in SAMPLES.values())
  ```

- `probe_npx(role, *, runner=None) -> dict` — **read-only; never fetches.** If
  `role.runner.acpx_binary` is set, `fetch_risk=False` and report whether the binary resolves
  (`shutil.which`/explicit path). If `acpx_binary` is `None`, the compiler resolves
  `["npx", "-y", "acpx@<version>"]` (see `policy._acpx_prefix`), so `fetch_risk=True`; probe
  `npx --version` (read-only) to report `npx_available` and the `pinned_spec`
  (`acpx@0.10.0`). It **must never** invoke `npx acpx` / `npx -y acpx@...` (that would fetch
  and execute). Returns `{"ok", "fetch_risk", "npx_available", "pinned_spec",
  "acpx_binary", "error_detail"}`.
- `probe_adapter(role, *, acpx_runner=None, npx_runner=None) -> dict` — **read-only; does NOT
  run the adapter/agent.** acpx hosts the adapter, and no fixture-proven read-only adapter
  enumeration command exists, so "availability" here means **declared + hostable**: the role
  declares a non-empty `runner.adapter_agent`, and the hosting acpx is resolvable (delegating
  to the same acpx-binary / npx resolution as `probe_acpx`/`probe_npx`). Returns
  `{"ok", "adapter_agent", "declared", "hostable", "detail"}` and explicitly documents in
  `detail` that the adapter process is not launched. Open question O-1 tracks whether a later
  fixture-proven read-only adapter list is worth adding.
- `probe_session_readiness(role=None) -> dict` — **local temp probe + read-only scan.**
  Mirrors `EventStore.permission_probe`: in a `tempfile.TemporaryDirectory`, construct a
  `SessionStore`, create a probe record (when a persistent role is supplied) or just verify
  the sessions root is creatable at `0700`/`0600`, and assert dir/file modes. When a role is
  supplied, also report persistent-strategy lease validity. Returns
  `{"ok", "dir_mode_ok", "file_mode_ok", "lease_seconds", "stale_locks", "error_detail"}`.
  `stale_locks` comes from the W4 read-only detector. No acpx launch.

`cmd_doctor` wiring (`commands.py`): extend the payload with `policy_probe`,
`workspace_probe`, `redaction_probe`, `npx_probe`, `adapter_probe`, `session_probe`.
Role-dependent probes (`policy`/`workspace`/`npx`/`adapter`/`session_probe` with role) run
only when `--role` is supplied. `redaction_probe`, the existing `event_store_probe`, fixture
replay, and a role-less `session_probe` (dir-mode only) run always.

**`ok` roll-up (CI-safe, explicit):**
`ok` gates only on **pure-local deterministic** probes so the existing CI gate
`PYTHONPATH=src python3 -m agent_run_supervisor doctor` (no `--role`) keeps exiting `0` in
environments without `node`/`acpx`/`npx`:

- Always contributes to `ok`: fixture replay (no `protocol_error`), `event_store_probe`
  modes, `redaction_probe`, role-less `session_probe` dir modes.
- Contributes to `ok` **only when `--role` is supplied**: `policy_probe`, `workspace_probe`.
- **Informational only (never gates `ok`):** `node_probe`, `acpx_probe`, `npx_probe`,
  `adapter_probe` (they depend on external binaries that are legitimately absent in CI).

This preserves today's no-role doctor exit semantics (Risk R-1) while making role-aware
doctor genuinely stricter. `launched_real_agent: false` stays in the payload and is asserted
by a test.

### 7.2 W2 — Retention/cleanup (confined, dry-run-first)

New module `src/agent_run_supervisor/retention.py`, stdlib-only, same security posture as
`event_store.py`/`session.py`.

Types:

- `RetentionError(RuntimeError)`.
- `RetentionPolicy(max_age_days: int | None = None, max_count: int | None = None)` — at least
  one bound required; `keep_open_sessions` behavior is unconditional (open/live-locked
  sessions are never deletable).
- `CleanupCandidate(kind: str, id: str, path: Path, age_seconds: float, action: str,
  reason: str)` where `action ∈ {"delete", "skip"}`.
- `CleanupPlan(root: Path, runs_dir: Path, sessions_dir: Path, delete: list[CleanupCandidate],
  skip: list[CleanupCandidate])`.
- `CleanupResult(plan: CleanupPlan, deleted: list[str], failed: list[dict])`.

Functions:

- `plan_cleanup(*, runs_dir, sessions_dir, policy, now=None) -> CleanupPlan` — **read-only;
  deletes nothing.** Enumerates run dirs (`<runs_dir>/<run_id>/`) and session dirs
  (`<sessions_dir>/<session_id>/`), computes age (from `result.json`/`session.json` mtime,
  else dir mtime), and classifies each as `delete` or `skip` per policy and the safety rules
  below.
- `apply_cleanup(plan, *, confirm: bool, now=None) -> CleanupResult` — deletes **only**
  `plan.delete` entries, re-verifying each safety invariant immediately before removal
  (TOCTOU-aware). Refuses entirely if `confirm is not True`.

Safety rules (each has a dedicated boundary test — see §8 W2):

1. **Artifact-root confinement.** `runs_dir`/`sessions_dir` must resolve to a path that has
   `.agent-run-supervisor` as a path segment (`ARTIFACT_ROOT_NAME in resolved.parts`). Else
   `RetentionError` — refuse to operate on arbitrary directories (`/`, `$HOME`, `/tmp`, …).
2. **Per-candidate confinement.** A candidate is `skip`/refused unless its **resolved** path
   is strictly within the resolved artifact root (`candidate.resolve().relative_to(root)`
   succeeds and is non-empty). Never delete the root itself.
3. **No symlink escape.** If the run/session dir is a symlink, or any path component resolves
   outside root (`os.path.realpath` compared against root), the candidate is `skip`ped with
   `reason="symlink_escape"`; the symlink target is never traversed or removed. Deletion uses
   a non-symlink-following strategy and re-checks `is_symlink()` at apply time.
4. **Never delete live sessions.** A session dir is *always* `skip`ped when its record
   `state == "open"` (`reason="open_session"`), regardless of policy or flags, and the state
   is re-read at apply time so a session that re-opens between plan and apply is still refused.
   A session holding a `lock.json` whose `expires_at > now` (held lease) is also always skipped
   (`reason="live_lock"`). Only closed sessions (`state == "closed"`) are eligible. Reuses
   `session.SessionStore` / `SessionRecord` to read state without launching acpx.
5. **Expired-lock hygiene (W4).** When pruning an eligible session, a `lock.json` whose
   `expires_at <= now` may be removed as part of the dir deletion; a non-expired lock makes
   the whole session non-deletable (rule 4).
6. **Atomic-write debris.** Leftover `.tmp-*` files inside an eligible dir are removed with
   that dir; they are also reported by the W4 detector.

CLI `cleanup` (`cli.py` + `commands.py`):

```text
agent-run-supervisor cleanup [--runs-dir <dir>] [--sessions-dir <dir>]
                             [--max-age-days N] [--max-count N]
                             [--apply]
```

- **Dry-run is the default.** Without `--apply`, it prints the `CleanupPlan` as JSON
  (`delete` + `skip` with reasons) and `applied: false`; nothing is deleted. This enforces
  "list first."
- `--apply` calls `apply_cleanup(plan, confirm=True)` and prints the `CleanupResult`.
- Defaults: `--runs-dir` → `.agent-run-supervisor/runs`, `--sessions-dir` →
  `.agent-run-supervisor/sessions` (matching `commands.DEFAULT_SESSIONS_DIR_NAME` and the
  runner default). Stable JSON stdout; exit `0` on a clean plan/apply, nonzero on
  `RetentionError`.

### 7.3 W3 — Result/event schema docs (caller stability)

New `docs/design/result-event-schema.md` (frontmatter required by
`tools/build_docs_index.py`). It documents, from the code as-is:

- **`result.json`** keys from `result.build_result_payload`: `run_id`, `status`,
  `business_verdict` (always `null`), `error_code`, `detail_code`, `origin`, `retryable`,
  `acpx_exit_code`, `signal`, `stop_reason`, `usage`, `final_message`, `truncated`,
  `truncate_reason`, `run_dir`, `stderr_path`, `raw_event_path`, `redaction_report_path`.
- **Session turn result** keys (the `SessionRuntime.send`/`status`/`close`/`abort`/`list`
  result dicts) and the `_parse_result_payload` projection in `commands.py`.
- **Statuses** (10) and `error_code` mapping from `exit_classifier`/`result._ERROR_CODE_FOR_STATUS`;
  the exit-code → status table from `architecture.md` §3.2; the invariant that supervisor
  status ≠ business verdict.
- **Normalized event families** from `technical-solution.md` §4.2 / `parser.py`: lifecycle
  start/update/complete/failure, agent message delta, tool start/update/complete, usage
  update, permission requested/denied, unknown-update summary, watchdog/kill/lifecycle
  metadata.
- **Doctor output** schema including the W1 probes and `launched_real_agent: false`.
- **Cleanup plan** JSON shape from W2.
- A **caller-stability contract**: `business_verdict` is always `null`; changes are additive;
  document a `schema_version`/compatibility note for callers.

A **fidelity test** (`tests/test_result_event_schema.py`) asserts the documented top-level
`result.json` keys exactly equal `set(build_result_payload(...).keys())`, so the doc cannot
silently drift from code. This is the W3 behavior guard (use-driven validation ethos in
`AGENTS.md`).

### 7.4 W4 — Narrow crash/interruption tail (detection-first)

Beyond the deterministic **expired-lease replacement** already implemented in
`SessionStore.acquire_lock` (it clears an expired `lock.json` and mints a fresh lease), H1
adds only **read-only detection** plus **provably-expired** lock hygiene:

- `session.SessionStore.detect_stale_locks(*, now=None) -> list[dict]` (read-only): for each
  record, report `{"session_id", "state", "lock_present", "lease_expired", "tmp_debris"}`
  where `lease_expired` is `lock.json.expires_at <= now` and `tmp_debris` lists leftover
  `.tmp-*` names. Launches nothing; takes no lock.
- Surfaced by `probe_session_readiness` (`stale_locks`) and consumed by W2 rule 5.
- **Boundary:** no PID/liveness inspection, no `kill`/signals, no live-session takeover, no
  remote reconciliation, no service restart. A non-expired lock is always treated as live and
  never force-broken. Full process-crash recovery (e.g. detecting a dead holder before lease
  expiry) remains an explicit documented carry-over, not implemented in H1.

## 8. TDD implementation checklist

TDD is mandatory for every behavior change (`docs/AI_FLOW.md`, user directive). For each task:
**write the failing test first → run it red → implement minimal code → run it green →
commit.** W3's doc body is non-behavioral, but its fidelity test follows TDD. Suggested order:
W1 → W4 detector → W2 → W3, then authority-doc updates last.

### W1 — Doctor completion

- [ ] **`probe_policy`** — test (`tests/test_preflight.py`): persistent and exec roles →
  `ok True`, `default_action == "deny"`, counts sum to 9 permission kinds, `policy_hash`
  present; a monkeypatched compile raising → `ok False`, `error_detail` set. Then implement.
- [ ] **`probe_workspace`** — test: cwd inside a root → `ok True`, `matched_root` set,
  `allowed_roots_security_boundary False`, disclaimer present; cwd outside roots → `ok False`,
  `error_detail` contains the not-a-sandbox disclaimer. Then implement.
- [ ] **`probe_redaction`** — test: every synthetic sample is absent from the redacted output,
  `ok True`, `leaked == []`; assert the samples are synthetic (no real-secret strings in the
  test). Then implement.
- [ ] **`probe_npx`** — test with injected runner: `acpx_binary` set → `fetch_risk False`;
  `acpx_binary None` → `fetch_risk True`, `pinned_spec == "acpx@0.10.0"`, runner invoked with
  `["npx", "--version"]`; assert the runner is **never** called with any argv containing
  `"acpx@"` or `"exec"`. Then implement.
- [ ] **`probe_adapter`** — test: declared adapter + resolvable host → `ok True`,
  `hostable True`, `detail` states the agent is not launched; empty/missing adapter handled;
  assert no adapter/acpx subcommand is executed. Then implement.
- [ ] **`probe_session_readiness`** — test (`tests/test_preflight.py` /
  `tests/test_session_store.py`): temp store → `dir_mode_ok`/`file_mode_ok True`; persistent
  role reports `lease_seconds`; `stale_locks` reflects an injected expired lock; no acpx
  launch. Then implement.
- [ ] **`cmd_doctor` wiring** — test (`tests/test_cli_commands.py`): no-`--role` doctor still
  exits `0` and keeps `launched_real_agent False` with the new keys present; with a valid
  `--role`, role probes appear and a bad-cwd role drives `ok False`/exit `1`; assert external
  binary absence does not flip `ok`. Then implement.

### W4 — Stale-lock detector (do before W2 rule 5)

- [ ] **`detect_stale_locks`** — test (`tests/test_session_store.py`): expired `lock.json` →
  `lease_expired True`; live lock → `lease_expired False`; `.tmp-*` debris listed; read-only
  (record/state unchanged, no lock created/removed). Then implement.

### W2 — Retention/cleanup

- [ ] **`plan_cleanup` selection** — test (`tests/test_retention.py`): by `max_age_days`
  (old → delete, fresh → skip) and `max_count` (keep newest N, rest → delete); deletes nothing
  (all paths still exist after planning). Then implement.
- [ ] **Confinement refusal** — test: `runs_dir`/`sessions_dir` outside any
  `.agent-run-supervisor` segment → `RetentionError`. Then implement.
- [ ] **Per-candidate confinement** — test: a candidate resolving outside root → `skip` with
  `reason` confinement; root itself never in `delete`. Then implement.
- [ ] **Symlink escape** — test: a run dir that is a symlink to an outside temp dir → `skip`
  `reason="symlink_escape"`; after `apply`, the outside target still exists. Then implement.
- [ ] **Open/live session protection** — test: `state == "open"` session skipped by default;
  session with unexpired `lock.json` skipped; `closed` session eligible; expired-lock session
  eligible and its expired lock removed with the dir. Then implement.
- [ ] **Dry-run vs apply** — test: `plan_cleanup` + no apply deletes nothing;
  `apply_cleanup(confirm=False)` refuses; `apply_cleanup(confirm=True)` deletes exactly
  `plan.delete` and nothing else. Then implement.
- [ ] **CLI `cleanup`** — test (`tests/test_cli_commands.py`): default = dry-run JSON,
  `applied false`, nothing deleted; `--apply` deletes and reports `CleanupResult`; bad root →
  nonzero exit + `RetentionError` message on stderr. Then implement.

### W3 — Schema docs

- [ ] **Fidelity test** — test (`tests/test_result_event_schema.py`): documented `result.json`
  key set equals `build_result_payload(...).keys()`; fails red before the doc/parsing helper
  exists. Then write `docs/design/result-event-schema.md` to satisfy it.

### Authority-doc + generated updates (after code/tests green)

- [ ] Update `docs/roadmap/current-status.md` (check H1 boxes, move tails to "recently
  closed", note crash-recovery carry-over), `docs/roadmap/features.md`
  (`F-CLI-003`→Done, `F-RETENTION-001`→Done, `F-STORE-001`/`F-STATUS-001` evidence),
  `docs/product/prd.md` FR-8/FR-10 checkboxes, `docs/design/technical-solution.md` §3.4/§3.9,
  `docs/design/architecture.md` §8.1.
- [ ] `python tools/build_docs_index.py --write` and `python tools/docs_drift_signal.py --write`
  (regenerate; never hand-edit `docs/INDEX.md`).

## 9. Acceptance criteria

- **Doctor:** `doctor` (no role) exits `0` in a clean env, keeps `launched_real_agent: false`,
  and includes `policy_probe`, `workspace_probe`, `redaction_probe`, `npx_probe`,
  `adapter_probe`, `session_probe`. With `--role`, role probes run; a bad-cwd role yields
  `ok: false`/exit `1`. No probe runs `acpx exec`, an adapter, a session prompt, or an `npx`
  fetch (asserted via injected runners / argv inspection). Redaction probe shows `leaked: []`.
- **Retention:** `plan_cleanup` lists deletions and deletes nothing; `apply_cleanup` requires
  `confirm=True` and deletes only planned entries; refuses any target outside a
  `.agent-run-supervisor` root; skips symlink-escape candidates without touching their
  targets; never deletes open or live-locked sessions; CLI defaults to dry-run.
- **Schema docs:** `docs/design/result-event-schema.md` exists with valid frontmatter,
  documents the result/event/status/doctor/cleanup shapes, and the fidelity test passes.
- **Crash tail:** `detect_stale_locks` flags expired (not live) locks and `.tmp-*` debris,
  read-only; no process is signaled.
- **Determinism/safety:** `business_verdict` remains `null` everywhere; artifacts stay
  `0700`/`0600`, atomic; no secrets in artifacts/tests (synthetic fakes only); stdlib-only.
- **Existing behavior unchanged:** `validate-role`, `replay`, `run` (exec + dry-run),
  `session create|send|status|close|abort|list`, and the existing doctor keys are unaffected.
- **Full gates pass** (§10) and review gate passes (Codex primary, Claude auxiliary, Hermes
  arbitration).
- S1 closure and E1 remain intact; I1 stays parked; no non-goal introduced.

## 10. Verification gates

```bash
python3 -m pytest -q tests/test_preflight.py tests/test_retention.py \
  tests/test_session_store.py tests/test_cli_commands.py tests/test_result_event_schema.py
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

- Secret-shaped scan over added/changed lines (the redaction probe and retention tests must
  use only **synthetic** pattern-shaped fakes, never real credentials).
- Static dangerous-pattern scan over new subprocess/network/filesystem-deletion surfaces:
  confirm `retention.py` deletes only within a resolved `.agent-run-supervisor` root, never
  follows symlinks out of root, and that no probe adds an `acpx exec`/adapter/`npx acpx`
  invocation.
- A focused **doctor read-only assertion**: every doctor/probe test proves no AGENT/adapter/
  `acpx exec`/`npx` fetch was launched (injected runners record argv; assert forbidden tokens
  absent).

> **Note for the plan step only:** `python tools/build_docs_index.py --check` is expected to
> report this new plan as out-of-date until `docs/INDEX.md` is regenerated during
> implementation; do not regenerate generated files in the plan step. `git diff --check` must
> be clean.

## 11. Files likely to change

Runtime / library:

- `src/agent_run_supervisor/preflight.py` — new W1 probes.
- `src/agent_run_supervisor/commands.py` — `cmd_doctor` wiring; new `cmd_cleanup`.
- `src/agent_run_supervisor/cli.py` — `cleanup` subparser.
- `src/agent_run_supervisor/retention.py` — **new** W2 module.
- `src/agent_run_supervisor/session.py` — `detect_stale_locks` (W4); read-only.
- (read-only callers of) `policy.py`, `workspace.py`, `redaction.py`, `event_store.py`,
  `result.py` — no signature changes expected; H1 consumes their existing public APIs.

Tests:

- `tests/test_preflight.py` — W1 probes.
- `tests/test_retention.py` — **new** W2 boundary/selection tests.
- `tests/test_session_store.py` — `detect_stale_locks`, session-readiness store probe.
- `tests/test_cli_commands.py` — doctor wiring + `cleanup` CLI.
- `tests/test_result_event_schema.py` — **new** W3 fidelity test.

Docs (authority + generated, updated after code/tests green):

- `docs/design/result-event-schema.md` — **new** W3 schema doc.
- `docs/product/prd.md`, `docs/design/architecture.md`,
  `docs/design/technical-solution.md`, `docs/roadmap/features.md`,
  `docs/roadmap/current-status.md`.
- `docs/INDEX.md`, `docs/lessons/_drift_report.md` — **generated**; via tools only.
- `docs/plans/archive/2026-06-01-h1-operational-hardening.md` — this file.

## 12. PR / scope strategy

H1 is cohesive enough for **one PR** (`ai/h1-operational-hardening-2026-06-01`): the four
workstreams share the "safe long-lived local use" theme, the doctor and retention surfaces
both depend on the W4 stale-lock detector, and the schema doc describes the doctor/cleanup
output they add. Keep it compressive.

Fallback split if review finds the diff too large: **PR-A** = W1 doctor completion + W4
detector (`ARS-DOCTOR-COMPLETE`); **PR-B** = W2 retention + W3 schema docs
(`ARS-RETENTION-CLEANUP` / `F-RETENTION-001` + caller-stability docs). Either way, authority
docs are updated in the same PR as the code they describe, and generated indexes are
regenerated, not hand-edited.

## 13. Risks / open questions

- **R-1 CI doctor exit semantics.** The CI gate runs `doctor` without `--role` and expects
  exit `0`. Mitigation: external-binary probes (`node`/`acpx`/`npx`/`adapter`) are
  informational and never gate `ok`; only pure-local deterministic probes do (§7.1). A test
  pins no-role doctor → exit `0`.
- **R-2 Retention is destructive.** Cleanup deletes files. Mitigations: dry-run default,
  explicit `--apply`/`confirm=True`, hard artifact-root confinement, symlink-escape refusal,
  open/live-lock protection, TOCTOU re-checks at apply time, and a dedicated boundary test per
  rule. No deletion path exists that can leave `.agent-run-supervisor`.
- **R-3 Redaction/secret-shaped scan false positives.** The redaction probe and tests embed
  pattern-shaped strings. Mitigation: use clearly synthetic fakes (`sk-AAAA…`, `FAKE-…`) and,
  if the scanner still flags them, annotate as test fixtures; never use real credentials.
- **O-1 Adapter availability fidelity.** Without a fixture-proven read-only acpx adapter-list
  command, `probe_adapter` reports "declared + hostable", not live presence. A future
  fixture-backed read-only enumeration could strengthen it; out of H1 scope. Documented in the
  probe `detail`.
- **O-2 Age source for cleanup.** Age is derived from `result.json`/`session.json` mtime with
  a dir-mtime fallback. If callers need creation-time semantics, the records already carry
  `created_at` (sessions); runs could add one later — additive, not required for H1.
- **O-3 Crash-recovery depth.** H1 intentionally stops at expired-lease + detection. Detecting
  a dead holder before lease expiry needs process-liveness inspection, which risks unsafe
  cross-process assumptions and lifecycle expansion; explicitly deferred and documented.

## 14. Rollback strategy

All work lives on branch `ai/h1-operational-hardening-2026-06-01` in the isolated worktree
`/home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/h1-operational-hardening-2026-06-01`.
Pre-merge rollback is branch/worktree discard. Post-merge rollback is reverting the PR — H1
adds new read-only probes, a new `retention.py` module + `cleanup` command, a new docs file,
and additive doctor keys, so a revert cleanly restores the post-S1 state with no schema
migration. H1 touches no production config, Gateway, Sachima, public ingress, or external
service state; nothing to roll back outside the repo. The `cleanup` command only ever deletes
within a `.agent-run-supervisor` root the operator points it at, so a revert cannot orphan or
expand any external state.

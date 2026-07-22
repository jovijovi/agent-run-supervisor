# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

### Notes

## [0.2.0] - 2026-07-22

### Added

- Stage 0/1 Native ACP core through ars-core (`agent_run_supervisor.native_acp`),
  additive alongside the released acpx surfaces:
  - Supervised live-stdio processes (`managed_process.py`): each Run spawns its
    AGENT in a fresh POSIX session/process group with recorded `ProcessIdentity`,
    supervisor-owned bounded stderr, SIGTERM→grace→SIGKILL group escalation with
    kill metadata, and a reaping `wait()`; the official ACP SDK exclusively owns
    the live stdin/stdout JSON-RPC wire.
  - Frozen admission identity: a typed, closed `AgentProfile` registry
    (`OPENCODE_1_18_4` revision 2 — OpenCode 1.18.4 with the registered closed
    model pair `kimi-for-coding/k3` / `deepseek/deepseek-v4-pro`, literal effort
    values, and credential slot names only — never values) resolves to a
    controlled `ResolvedLaunchSpec`, then an immutable `AgentRunSpec`/`spec_hash`
    sealed before spawn; `EffectiveRunState` records observations only and never
    rewrites the frozen request.
  - Exact-or-zero configuration fidelity: initialize → `session/new` or
    `session/load` → discovery → set model → fresh model-dependent option set →
    rediscover and set effort → exact requested == effective readback; a missing
    capability, unadvertised value, or inexact readback fails before dispatch
    with zero prompt (literal `max` is never downgraded).
  - Session continuity and cross-Run switching: process-per-Run over one
    unchanged external session ID with real `session/load` (silent session
    re-creation is a hard failure); model/effort are immutable per Run and
    switch only between completed Runs with exact readback; partial switching
    sends no prompt and must prove rollback, otherwise the Session quarantines.
  - Isolated Native storage and evidence: the `native_acp/storage.py` seam binds
    `native-runs/` and `native-sessions/` roots (`0700` dirs, `0600` files),
    write-once `spec`/`launch`/`effective`/`result` artifacts and dispatch
    markers, and one bounded per-Run event writer with monotonic `seq` and
    truncation markers; legacy acpx `runs/`/`sessions/` stores are never read,
    written, or migrated (regression-pinned with poisoned same-ID fixtures and
    byte snapshots).
  - Fail-closed terminal state and duplicate prevention: additive Run statuses
    `failed`/`cancelled`/`unknown`, `prompt-dispatch-started`/`prompt-accepted`
    markers, and a finalization table under which a possibly-dispatched Run
    without a trustworthy terminal result ends `unknown` with `retryable=false`
    and quarantines its Session; nothing auto-retries, replays, or resumes it,
    and successor work is a new caller-authorized Run linked by
    `retry_of_run_id`.
  - Permission and workspace boundaries: `PermissionBridge` enforces the frozen
    per-Run execution grant default-deny (registered workspace-internal reads
    allowed; write/terminal/unknown operations denied) with redacted
    `MediationEvent` evidence — cooperative-agent mediation, not an OS sandbox.
  - Cancellation and finalization cleanup: supervisor cancel/timeout escalates
    through ACP cancel and process-group termination; finalization reaps the
    child, persists one irreversible terminal fact, and releases the session
    lease on all paths, including quarantine.
  - Packaging: optional `native` extra pinning `agent-client-protocol==0.11.0`
    with SDK contract tests; the base install stays stdlib-only.

### Changed

- Dev/CI installs are lock-enforced and include the Native suite: `make sync`
  and the CI verify jobs run `uv sync --locked --extra dev --extra release
  --extra native`, and the canonical verifier gained an `uv lock --check` gate.

### Fixed

- Inbound `session/update` drain ordering in the new Native driver: prompt
  completion waits for every update frame observed before the prompt response
  to finish its client callback (a pre-response delivery barrier), so
  finalization can never cancel queued handlers and silently lose
  final-message/event evidence.

### Notes

- Stage 0/1 is a library-level core with real OpenCode 1.18.4 B-grade
  acceptance evidence (exact K3/`max`, `session/load` context continuity across
  process-per-Run, and registered-model switching). It is not production
  acceptance: this version ships no `arsd` daemon, no Native service or Native
  CLI production entry, and no Stage 2 socket-path acceptance; production
  enablement, release publication, and Sachima integration remain separately
  approved work.
- The released v0.1.7 acpx one-shot/persistent-session surfaces are unchanged
  and remain the compatibility baseline; Native code never reads or writes
  their stores and never falls back to acpx.
- Developers verify with `make verify`; the real OpenCode smoke is opt-in via
  `ARS_NATIVE_SMOKE=1` (`tests/native_acp/test_real_opencode_smoke.py`).

## [0.1.7] - 2026-07-16

### Added

- Role-bound native ACPX MCP configuration injection. A role may bind an
  absolute JSON MCP config path; ARS validates and fingerprints that binding,
  then compiles it as the ACPX `--mcp-config` argument for the run/session.

### Changed

- Persistent-session binding now includes the MCP configuration identity, so a
  changed bound config fails closed rather than being silently reused.

### Fixed

- Existing roles with no MCP configuration retain their prior serialized role
  shape and hash, avoiding an unnecessary migration for already persisted
  sessions.

### Notes

- This release does not start MCP services or expand a role's permissions. The
  bound configuration is a local, role-owned input and remains subject to the
  existing permission and workspace policy.

## [0.1.6] - 2026-07-09

### Added

- Read-only local session inspection API for caller hot paths:
  `inspect_session(...)` summarizes a persisted session record, lease/liveness
  state, latest turn, progress snapshot, and artifact paths without launching an
  agent, mutating session state, or shelling out.
- `list_turns(...)` returns ordered persisted turn summaries for a session,
  including status, timestamps, result paths, observed-effect metadata, and
  redacted final-message excerpts.
- Regression coverage for corrupt/missing artifacts, turn ordering, raw-content
  boundaries, lease/liveness classification, and no-subprocess execution.

### Changed

- README, README.zh-CN, technical solution, and roadmap docs now describe the
  inspection API as the supported library seam for products such as Sachima that
  need safe local progress/status reads instead of controller CLI glue.

### Fixed

- Release package metadata now advertises `0.1.6`, keeping `pyproject.toml`,
  `src/agent_run_supervisor/__init__.py`, and `uv.lock` in sync before tag
  publication.

### Notes

- This is an additive library/API release. It does not change the acpx transport
  contract, does not add runtime dependencies, and does not start or control
  external agents from the inspection path.

## [0.1.5] - 2026-07-09

### Fixed

- `session send --goal-file` now compiles the goal through
  `goal.compile_goal_prompt` instead of composing a literal `/goal <text>` slash
  turn. On the codex ACP surface (`acpx@0.12.0`) the literal slash turn was
  answered with `Unknown command "/goal"` — a transport-completed no-op that
  still reported `status=completed`. Non-native adapters (all of them today) now
  receive the `goal-contract/v1` text template (`prompt_kind: "text"`); a literal
  slash turn would only be sent for adapters explicitly registered in
  `NATIVE_GOAL_ADAPTERS` (fixture-gated, currently empty). Goal-text validation
  semantics are unchanged (empty/nested-slash/control characters still fail
  closed before any lease/acpx work).

### Notes

- Follow-up (not addressed here): classifying an `Unknown command "<x>"` agent
  reply as non-success requires the `available_commands` capture +
  `UNSUPPORTED_SLASH_COMMAND` slice already registered as deferred in
  `docs/plans/active/2026-07-08-permissioned-session-goal-noop.md`; text-matching
  the reply would be brittle across adapters.

## [0.1.4] - 2026-07-08

### Added

- Fail-closed `no_op` supervisor status: exit `0` with a protocol-clean stream but no
  agent output and no tool activity (`parser.has_observed_effect`) is no longer reported
  as `completed` (`error_code: NO_OP`, `retryable: false`). Applies to both exec runs and
  persistent-session prompt turns.
- `goal.py`: validated goal-turn composition (`compose_goal_prompt` → `/goal <text>`,
  `is_slash_prompt`); session turn results carry the additive `prompt_kind` key
  (`slash_command` | `text`).
- CLI: `session send --goal-file <file>` composes and sends a validated `/goal` slash
  prompt turn (mutually exclusive with `--prompt-file`).
- Additive `observed_effect` result key (`true`/`false`/`null`): callers can verify a
  `completed` run/turn actually produced output or tool activity (schema §1).
- Goal-contract compilation (`goal.compile_goal_prompt`): adapters without a native
  ACP `goal` command (all of them today — `NATIVE_GOAL_ADAPTERS` starts empty) get the
  versioned `goal-contract/v1` plain-text template with a deterministic trailing
  `GOAL_STATUS:` anchor for caller judge loops.
- Session turns now persist `generated-policy.json` (audit symmetry with exec runs)
  and report the additive `prompt_permission_mode` result key (`policy` | `deny_all`).
- 0.1.3 hash-stability goldens: `role_hash`/`policy_hash` are pinned byte-identical to
  the released 0.1.3 distribution, guarding the zero-migration session-binding
  invariant.
- `hermes_caller.derive_verdict` fails closed on a blank `final_message`: a completed
  run that produced no findings text is `BLOCK`, never `PASS`.

### Changed

- Persistent-session prompt turns no longer hardcode `--deny-all`: roles granting
  permission kinds compile the same role-derived `--permission-policy` JSON as the exec
  path; roles granting no kinds keep the fixture-proven `--deny-all` fail-closed shape.

### Notes

- A live acpx fixture capture for the permissioned `prompt -s` shape is an operator
  follow-up before the next release.

## [0.1.3] - 2026-07-07

### Added

- `tools/bump_version.py` and `make bump VERSION=X.Y.Z` to sync `pyproject.toml`,
  `__init__.py`, `uv.lock`, and a CHANGELOG stub in one step.
- `tools/check_version_sync.py` verify gate for three-way version consistency.
- `release.yml` guard: git tag must match `pyproject.toml` version before PyPI publish.

### Changed

- README EN/ZH and AGENTS publish instructions updated for the new bump workflow.
- `uv.lock` workspace package version synced with `pyproject.toml`.

### Notes

- Release engineering only; no supervisor runtime behavior changes.
## [0.1.2] - 2026-07-07

### Added

- GitHub Release asset upload with `SHA256SUMS` for wheels and sdists (`release.yml`).
- `invoke_caller` modes `session_abort` and `session_list` (delegates to `SessionRuntime`).
- `fixtures/README.md` documenting `acpx-0.10.0` legacy vs `acpx-0.12.0` canonical fixtures.

### Changed

- Test coverage raised: `preflight.py` 98%, `role.py` 99%, `parser.py` 95%, `live_stream.py` 97%;
  package total **93%**.
- README EN/ZH library usage updated for new caller modes and checksum verification steps.

### Notes

- No acpx runtime contract changes; test, CI, library API, and release-provenance improvements.

## [0.1.1] - 2026-07-07

### Added

- CI verify and Codecov coverage jobs on Python 3.11, 3.12, 3.13, and 3.14.
- Codecov integration with branch coverage upload and README badges.
- README Library usage and live progress polling sections (English and Chinese).
- PyPI classifiers for Python 3.12, 3.13, and 3.14.

### Changed

- GitHub Actions upgraded to `actions/checkout@v6`, `actions/setup-python@v6`, and
  `codecov/codecov-action@v6`; `setup-uv` pinned to immutable commit SHA.
- Roadmap and features synced with CI matrix, Codecov, and live streaming PR1/PR2 closure.
- `runner.py` test coverage raised to 88% with subprocess edge-case tests.

### Notes

- No runtime behavior changes in this release; documentation, CI, and test improvements only.

## [0.1.0] - 2026-07-06

### Added

- Local-first Python library and CLI for supervising ACP/acpx external AGENT runs with redacted, auditable artifacts.
- One-shot `acpx exec` supervision with role-bound authorization, outer watchdog, and kill metadata.
- Local persistent-session lifecycle: create, send, status, close, abort, and list.
- Read-only `doctor` probe set, confined artifact retention/cleanup, and process-liveness crash recovery.
- Generic local caller boundary (`caller.py`) and local/offline Hermes caller with offline Feishu view-model adapter.
- acpx `0.12.0` contract fixtures, validator, and deterministic replay.
- `scripts/verify_local.sh` local gate entry and GitHub Actions Trusted Publishing release workflow.

[0.1.6]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.6
[0.1.5]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.5
[0.1.4]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.4
[0.1.3]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.3
[0.1.2]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.2
[0.1.1]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.1
[0.1.0]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.0

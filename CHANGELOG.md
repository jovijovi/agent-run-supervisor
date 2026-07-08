# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Notes

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

[0.1.2]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.2
[0.1.1]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.1
[0.1.0]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.0

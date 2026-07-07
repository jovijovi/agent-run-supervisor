# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

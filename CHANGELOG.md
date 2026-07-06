# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-06

### Added

- Local-first Python library and CLI for supervising ACP/acpx external AGENT runs with redacted, auditable artifacts.
- One-shot `acpx exec` supervision with role-bound authorization, outer watchdog, and kill metadata.
- Local persistent-session lifecycle: create, send, status, close, abort, and list.
- Read-only `doctor` probe set, confined artifact retention/cleanup, and process-liveness crash recovery.
- Generic local caller boundary (`caller.py`) and local/offline Hermes caller with offline Feishu view-model adapter.
- acpx `0.12.0` contract fixtures, validator, and deterministic replay.
- `scripts/verify_local.sh` local gate entry and GitHub Actions Trusted Publishing release workflow.

[0.1.0]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.0

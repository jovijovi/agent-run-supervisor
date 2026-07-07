---
title: "P3 — Engineering basics"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: p3-engineering-basics
---

# P3 — Engineering basics

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## P3 — Engineering basics (uv + verify + PyPI)

Goal: reproducible local dev with uv, CI/local gate alignment, and tag-triggered PyPI Trusted
Publishing for the first public release — without changing supervisor runtime behavior or introducing
runtime dependencies.

Checklist:

- [x] Track `uv.lock` for reproducible dev tooling (`dev` + `release` extras only; runtime stays
  stdlib-only).
- [x] Add `scripts/verify_local.sh` as the single local gate entry mirroring §6 and CI.
- [x] Add `scripts/smoke_installed_wheel.sh` for reusable installed-wheel smoke.
- [x] Migrate `.github/workflows/verify.yml` to uv (`astral-sh/setup-uv@d31148d669074a8d0a63714ba94f3201e7020bc3`
  immutable SHA pin, Python 3.11/3.12/3.13/3.14 matrix on `verify` + `coverage` jobs,
  `concurrency` cancel-in-progress).
- [x] Bump `pyproject.toml` to `0.1.0`; add `CHANGELOG.md`.
- [x] Add `.github/workflows/release.yml` (tag `v*`, OIDC Trusted Publishing, environment `pypi`).
- [x] Update README Development + Publishing sections; governance docs and plan
  `docs/plans/archive/2026-07-06-p3-engineering-basics.md`.
- [x] README Library usage sections (EN/ZH) with `invoke_caller` examples.
- [x] Codecov coverage job (`pytest --cov --cov-branch --cov-report=xml` +
  `codecov/codecov-action@v6`); README CI/Codecov/PyPI badges (PR #43).
- [x] CI Actions upgraded: `actions/checkout@v6`, `actions/setup-python@v6` (PR #44).

Acceptance:

- `uv sync --extra dev --extra release` + `./scripts/verify_local.sh` pass locally.
- CI `Verify` workflow runs `./scripts/verify_local.sh` on Python **3.11, 3.12, 3.13, 3.14**.
- CI `coverage` job uploads to Codecov per matrix version with `CODECOV_TOKEN`.
- `release.yml` publishes via Trusted Publishing; no secrets in repo.
- `pip install agent-run-supervisor==0.1.0` works from PyPI; `agent-run-supervisor doctor` passes
  after install.

Status: **Closed on `main`.** First public release [`0.1.0`](https://pypi.org/project/agent-run-supervisor/0.1.0/)
published via tag [`v0.1.0`](https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.0)
(GitHub Actions Trusted Publishing, 2026-07-06). Engineering merged via PR #40 (`288eeb3`); doc sync
for library usage and publish status in follow-up PR. CI matrix + Codecov merged via PR #43/#44.
All §5 non-approvals remain in force.

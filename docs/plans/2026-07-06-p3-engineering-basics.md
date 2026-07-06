---
title: "P3 Engineering Basics (uv + verify + PyPI)"
status: active
created_at: 2026-07-06
last_validated_at: 2026-07-06T16:00:00+0800
---
# P3 Engineering Basics (uv + verify + PyPI)

## Context and target

- **Product position:** Core supervisor features (E1/S1/H1/K1/I1/L2) are closed on `main`. F-RELEASE-001
  previously covered pre-release build/twine/wheel-smoke only — not PyPI publish or a unified local dev
  workflow.
- **Phase target:** P3 — reproducible local dev with uv, CI/local gate alignment via
  `scripts/verify_local.sh`, and tag-triggered PyPI Trusted Publishing for the first public release
  `0.1.0`.
- **Roadmap trace:** Extends F-RELEASE-001; does not change supervisor runtime behavior or introduce
  runtime dependencies.
- **Non-approvals unchanged:** No Sachima/Gateway/IM/public ingress/live behavior, no secrets in repo.

## Implementation checklist

### P3a — Local dev and verify scripts

- [x] Commit-track `uv.lock` (reproducible dev deps; runtime stays stdlib-only).
- [x] Add `scripts/verify_local.sh` — single gate entry mirroring `current-status.md` §6 and CI.
- [x] Add `scripts/smoke_installed_wheel.sh` — reusable installed-wheel smoke from CI.
- [x] Update `README.md` / `README.zh-CN.md` with Development section (uv primary, pip fallback).

### P3b — CI with uv

- [x] Migrate `.github/workflows/verify.yml` to `astral-sh/setup-uv@v5` + `uv sync`.
- [x] Run `./scripts/verify_local.sh` in CI (single source of truth).
- [x] Add `concurrency` with `cancel-in-progress`.
- [x] Python matrix `3.11` + `3.12`.

### P3c — PyPI release

- [x] Bump `pyproject.toml` version to `0.1.0`.
- [x] Add `CHANGELOG.md` (Keep a Changelog, `0.1.0` entry).
- [x] Add `.github/workflows/release.yml` (tag `v*`, OIDC, environment `pypi`).
- [x] Update README Publishing section.

### P3d — Governance docs

- [x] This plan document.
- [x] Update `docs/roadmap/features.md` (F-RELEASE-001 extension).
- [x] Update `docs/roadmap/current-status.md` (P3 phase).
- [x] Regenerate `docs/INDEX.md` and drift report.

## Files likely to change

| Path | Change |
|---|---|
| `uv.lock` | Track in git |
| `scripts/verify_local.sh` | New |
| `scripts/smoke_installed_wheel.sh` | New |
| `.github/workflows/verify.yml` | uv migration |
| `.github/workflows/release.yml` | New |
| `pyproject.toml` | Version `0.1.0` |
| `CHANGELOG.md` | New |
| `README.md`, `README.zh-CN.md` | Development + Publishing |
| `docs/roadmap/features.md` | F-RELEASE-001 |
| `docs/roadmap/current-status.md` | P3 phase |
| `docs/INDEX.md` | Regenerated |

## Verification gates

```bash
uv sync --extra dev --extra release
./scripts/verify_local.sh
```

## Acceptance criteria

- `./scripts/verify_local.sh` passes on a clean checkout after `uv sync`.
- `uv.lock` is tracked; `uv sync --extra dev --extra release` reproduces the full test suite.
- CI `Verify` workflow uses uv and `./scripts/verify_local.sh` (Python 3.11 + 3.12 matrix).
- `pyproject.toml` version is `0.1.0`; `CHANGELOG.md` documents the release.
- `release.yml` exists with Trusted Publishing permissions; no secrets in repo.
- Roadmap/features reflect P3 completion; docs index/drift gates pass.

## Operator checklist (manual, out of repo)

Before pushing `v0.1.0`:

1. Register PyPI project `agent-run-supervisor` (if not already).
2. Configure Trusted Publishing: Owner `jovijovi`, Repo `agent-run-supervisor`, Workflow
   `release.yml`, Environment `pypi`.
3. (Optional) Configure TestPyPI Trusted Publisher for a dry-run first.
4. Create GitHub Environment `pypi` with optional approval gate for maintainers.
5. After tag push succeeds: `pip install agent-run-supervisor==0.1.0` + `agent-run-supervisor doctor`.

**Forbidden:** `.pypirc`, API tokens, or secrets committed to git.

## Risks and rollback

| Risk | Mitigation |
|---|---|
| Trusted Publishing misconfiguration | TestPyPI dry-run first; failed publish does not affect main CI |
| Accidental tag trigger | GitHub Environment approval gate |
| uv vs pip CI drift | Single `verify_local.sh` source |
| Bad `0.1.0` publish | PyPI yank; patch release `0.1.1` |

Rollback: yank PyPI version; revert `release.yml`; keep or hotfix version as `0.1.1`.

## Open questions

- First publish may use TestPyPI before production PyPI — operator choice at tag time.
- ruff/mypy/pre-commit deferred to a future P3b+ plan.

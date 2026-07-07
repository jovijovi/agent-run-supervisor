---
title: "Version bump automation"
status: active
created_at: 2026-07-07
last_validated_at: 2026-07-07T16:45:00+0800
---
# Version bump automation

## Context and target

- **Product position:** P3 release engineering is closed; this extends F-RELEASE-001 with version-sync
  tooling — no runtime behavior change.
- **Phase target:** One command bumps `pyproject.toml`, `__init__.py`, `uv.lock`, and a CHANGELOG stub;
  verify and `release.yml` guard against tag/metadata drift.
- **Roadmap trace:** cicd/release tooling only.

## Scope

### In scope

- `tools/bump_version.py` — explicit semver bump + `uv lock` + CHANGELOG stub
- `tools/check_version_sync.py` — read-only three-way version check
- `scripts/verify_local.sh`, `.github/workflows/release.yml`, `Makefile` `bump` target
- Tests in `tests/test_version_sync.py`
- README / AGENTS / verification doc updates

### Out of scope

- Actual tag, GitHub Release, or PyPI publish for `0.1.3`
- Dynamic versioning (setuptools-scm)
- Removing `__init__.py` `__version__`

## Acceptance

- [x] `make bump VERSION=X.Y.Z` updates pyproject, `__init__.py`, CHANGELOG stub, and runs `uv lock`
- [x] `tools/check_version_sync.py` fails when `uv.lock` drifts from pyproject
- [x] `release.yml` fails when tag ≠ pyproject version
- [x] `make verify` includes version sync check
- [x] Unit tests cover bump validation, changelog stub, and lock drift

## Operator flow (post-merge)

```bash
make bump VERSION=0.1.3
# edit CHANGELOG [0.1.3]
make verify
# merge → make release-tag → push v0.1.3
```

## Completion

Merge this PR, then `git mv` to `docs/plans/archive/` when closed.

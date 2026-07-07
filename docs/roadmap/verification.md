---
title: "Verification gates for implementation PRs"
status: active
created_at: 2026-05-28
last_validated_at: 2026-07-07T15:30:00+0800
---

# Verification gates for implementation PRs

Canonical local entry: [`scripts/verify_local.sh`](../../scripts/verify_local.sh)
(mirrors CI `Verify` workflow).

Primary local entry (after `uv sync --extra dev --extra release`):

```bash
./scripts/verify_local.sh
```

Step-by-step equivalent:

```bash
uv run python scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0
uv run pytest -q
uv run python -m compileall -q src scripts tests
uv run agent-run-supervisor doctor
uv run agent-run-supervisor replay fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson
uv run python tools/build_docs_index.py --check
uv run python tools/docs_drift_signal.py --check
uv run python tools/static_safety_scan.py
uv run python tools/check_roadmap_governance.py
uv run python -m build
uv run python -m twine check dist/*
./scripts/smoke_installed_wheel.sh
git diff --check
```

**pip fallback** (without uv): replace `uv run …` with `PYTHONPATH=src python3 -m …` or installed
console scripts after `pip install -e '.[dev,release]'`.

For source changes, also run secret-shaped and static dangerous-pattern scans over added lines.

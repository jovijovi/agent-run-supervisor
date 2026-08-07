---
title: "Verification gates for implementation PRs"
status: active
created_at: 2026-05-28
last_validated_at: 2026-08-06
---

# Verification gates for implementation PRs

Canonical local entry: [`scripts/verify_local.sh`](../../scripts/verify_local.sh)
(mirrors CI `Verify` workflow).

Real external-AGENT Session continuity, refusal, concurrency, and recovery acceptance is defined separately in
[`session-reuse-acceptance.md`](session-reuse-acceptance.md). That opt-in procedure is not part of the default
hermetic implementation gate and never runs model calls implicitly.

Primary local entry (after `make sync`, i.e.
`uv sync --locked --extra dev --extra release --extra native`):

```bash
./scripts/verify_local.sh
```

Step-by-step equivalent:

```bash
uv lock --check
uv run pytest -q
uv run python -m compileall -q src scripts tests
uv run python tools/build_docs_index.py --check
uv run python tools/docs_drift_signal.py --check
uv run python tools/static_safety_scan.py
uv run python tools/check_version_sync.py
uv run python -m build
uv run python -m twine check dist/*
uv run python tools/check_dist_manifest.py
./scripts/smoke_installed_artifact.sh wheel
./scripts/smoke_installed_artifact.sh sdist
uv run python tools/check_roadmap_governance.py
git diff --check
```

**Distribution boundary.** `tools/check_dist_manifest.py` compares the built wheel
*and* sdist against the committed allowlists in `tools/dist_manifest_wheel.txt`
and `tools/dist_manifest_sdist.txt` as an exact set, in both directions, and
additionally refuses a removed module or packaged fixture data even if an
allowlist were regenerated carelessly. It reuses the artifacts the build step
just produced — there is no second build. Both artifacts are then installed into
their own throwaway venvs and smoked for the three supported commands
(`agents validate`, `agents doctor`, `run inspect`), a `--help` naming no removed command, the
`agent_run_supervisor.arsd` import, and the `--print-service-unit` refusal.
Neither smoke installs into an operator environment, enables a service, or starts
a real AGENT.

**pip fallback** (without uv): replace `uv run …` with `PYTHONPATH=src python3 -m …` or installed
console scripts after `pip install -e '.[dev,release,native]'` (the `uv lock --check` step is
uv-only and is skipped on this path).

For source changes, also run secret-shaped and static dangerous-pattern scans over added lines.

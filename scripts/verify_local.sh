#!/usr/bin/env bash
# Single local verify entry — mirrors docs/roadmap/verification.md and CI verify gates.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Check uv lock consistency"
uv lock --check

echo "==> Run tests"
uv run pytest -q

echo "==> Compile Python sources"
uv run python -m compileall -q src scripts tests

echo "==> Check docs index"
uv run python tools/build_docs_index.py --check

echo "==> Check docs drift report"
uv run python tools/docs_drift_signal.py --check

echo "==> Static safety scan"
uv run python tools/static_safety_scan.py

echo "==> Check version sync"
uv run python tools/check_version_sync.py

echo "==> Build package and check metadata"
rm -rf dist build src/agent_run_supervisor.egg-info
uv run python -m build
uv run python -m twine check dist/*

# The distribution boundary, asserted against the artifacts the step above just
# produced — no second build. Exact set comparison for the wheel and the sdist.
echo "==> Check distribution manifests"
uv run python tools/check_dist_manifest.py

echo "==> Installed wheel smoke"
./scripts/smoke_installed_artifact.sh wheel

echo "==> Installed sdist smoke"
./scripts/smoke_installed_artifact.sh sdist

echo "==> Roadmap governance check"
uv run python tools/check_roadmap_governance.py

echo "==> Whitespace diff check"
git diff --check

echo "All verify gates passed."

#!/usr/bin/env bash
# Single local verify entry — mirrors docs/roadmap/verification.md and CI verify gates.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Validate acpx contract fixtures"
uv run python scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0

echo "==> Run tests"
uv run pytest -q

echo "==> Compile Python sources"
uv run python -m compileall -q src scripts tests

echo "==> CLI doctor smoke"
doctor_json="$(mktemp)"
uv run agent-run-supervisor doctor > "$doctor_json"
uv run python - <<PY
import json
from pathlib import Path

data = json.loads(Path("$doctor_json").read_text())
assert data["ok"] is True
assert data["launched_real_agent"] is False
PY
rm -f "$doctor_json"

echo "==> CLI replay smoke"
uv run agent-run-supervisor replay fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson

echo "==> Check docs index"
uv run python tools/build_docs_index.py --check

echo "==> Check docs drift report"
uv run python tools/docs_drift_signal.py --check

echo "==> Static safety scan"
uv run python tools/static_safety_scan.py

echo "==> Build package and check metadata"
rm -rf dist build src/agent_run_supervisor.egg-info
uv run python -m build
uv run python -m twine check dist/*

echo "==> Installed wheel smoke"
./scripts/smoke_installed_wheel.sh

echo "==> Roadmap governance check"
uv run python tools/check_roadmap_governance.py

echo "==> Whitespace diff check"
git diff --check

echo "All verify gates passed."

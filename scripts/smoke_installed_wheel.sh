#!/usr/bin/env bash
# Smoke-test an already-built wheel in an isolated venv (no editable install).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d dist ]] || ! compgen -G "dist/*.whl" > /dev/null; then
  echo "error: dist/*.whl not found; run build first" >&2
  exit 1
fi

VENV="$(mktemp -d /tmp/ars-wheel-smoke.XXXXXX)"
trap 'rm -rf "$VENV"' EXIT

uv venv "$VENV" --quiet
uv pip install --python "$VENV/bin/python" dist/*.whl --quiet

"$VENV/bin/agent-run-supervisor" --help >/dev/null

doctor_json="$(mktemp)"
"$VENV/bin/agent-run-supervisor" doctor > "$doctor_json"
"$VENV/bin/python" - <<PY
import json
from pathlib import Path

data = json.loads(Path("$doctor_json").read_text())
assert data["ok"] is True
assert data["launched_real_agent"] is False
assert data["fixture_replay"]["protocol_error"] is False
assert data["fixture_replay"]["final_message"] == "CODEX_ACPX_OK"
PY
rm -f "$doctor_json"

echo "Installed wheel smoke passed."

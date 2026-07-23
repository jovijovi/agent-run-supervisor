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

# Slice 6a: arsd package + --print-service-unit ship in the wheel with no
# pyproject.toml change (subpackage discovery). Never activates a service or
# calls a real AGENT.
"$VENV/bin/python" - <<'PY'
import agent_run_supervisor.arsd  # noqa: F401
import agent_run_supervisor.arsd.service_unit  # noqa: F401
PY
"$VENV/bin/python" -m agent_run_supervisor.arsd --help >/dev/null
unit="$("$VENV/bin/python" -m agent_run_supervisor.arsd --print-service-unit)"
printf '%s\n' "$unit" | grep -F 'Restart=on-failure' >/dev/null
printf '%s\n' "$unit" | grep -F 'KillMode=control-group' >/dev/null

echo "Installed wheel smoke passed."

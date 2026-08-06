#!/usr/bin/env bash
# Smoke-test one already-built artifact in its own isolated venv.
#
#   scripts/smoke_installed_artifact.sh wheel
#   scripts/smoke_installed_artifact.sh sdist
#
# Both are smoked, because they are built by different code paths and can differ:
# the wheel is what `pip install agent-run-supervisor` resolves, the sdist is what
# a source build produces, and a package-data or discovery mistake can reach one
# without the other. Neither installs into an operator environment — each venv is
# a temporary directory removed on exit — and neither starts a service or a real
# AGENT.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KIND="${1:-wheel}"
case "$KIND" in
  wheel) PATTERN="dist/*.whl" ;;
  sdist) PATTERN="dist/*.tar.gz" ;;
  *) echo "usage: $0 wheel|sdist" >&2; exit 2 ;;
esac

if ! compgen -G "$PATTERN" > /dev/null; then
  echo "error: $PATTERN not found; run build first" >&2
  exit 1
fi

VENV="$(mktemp -d "/tmp/ars-${KIND}-smoke.XXXXXX")"
trap 'rm -rf "$VENV"' EXIT

uv venv "$VENV" --quiet
# shellcheck disable=SC2086
uv pip install --python "$VENV/bin/python" $PATTERN --quiet

BIN="$VENV/bin/agent-run-supervisor"

# The installed surface is exactly the three commands design authority declares,
# and the help text names none of the removed runtime.
help_text="$("$BIN" --help)"
printf '%s' "$help_text" | grep -qi 'acpx' && {
  echo "error: --help still names the removed runtime" >&2; exit 1; }
for leaf in validate-role replay doctor session cleanup; do
  if printf '%s' "$help_text" | grep -qE "(^|[[:space:]])${leaf}([[:space:]]|$)"; then
    echo "error: --help still advertises the removed '${leaf}' command" >&2
    exit 1
  fi
done
"$BIN" agents validate --help >/dev/null
"$BIN" agents doctor --help >/dev/null
"$BIN" run inspect --help >/dev/null
for leaf in validate-role replay doctor session cleanup; do
  if "$BIN" "$leaf" >/dev/null 2>&1; then
    echo "error: removed command '${leaf}' still runs" >&2
    exit 1
  fi
done

# The package ships no removed module and no packaged fixture data. Import is the
# strongest form of the check: a module that is not there cannot be imported, and
# one that is there under a different name still fails this.
"$VENV/bin/python" - <<'PY'
import importlib
import importlib.util

import agent_run_supervisor.arsd  # noqa: F401
import agent_run_supervisor.arsd.service_unit  # noqa: F401

removed = (
    "caller", "goal", "hermes_caller", "live_stream", "mcp_config", "parser",
    "policy", "preflight", "retention", "role", "runner", "session_inspect",
    "session_runtime", "workspace", "fixtures",
)
for name in removed:
    spec = importlib.util.find_spec(f"agent_run_supervisor.{name}")
    assert spec is None, f"the installed package still ships {name!r}"

package = importlib.import_module("agent_run_supervisor")
root = __import__("pathlib").Path(package.__file__).parent
stray = sorted(p.name for p in root.rglob("*") if "acpx" in p.name.lower())
assert not stray, f"installed package carries {stray}"
PY
"$VENV/bin/python" -m agent_run_supervisor.arsd --help >/dev/null

# Rendering a unit requires an explicit operator agents file, so a production
# unit can never silently omit the registry that decides which command is which
# agent. This path is deliberately synthetic: print mode treats it as argv data
# only and never opens it.
#
# This smoke deliberately does not probe SMOKE_AGENTS_FILE. `[[ -e ... ]]` is
# itself a metadata query on the agents file, so "prove it was not created" and
# "prove nothing queried it" cannot both be asserted from here — the probe would
# be the very access the boundary forbids, and a smoke that performs it is
# citing contradictory evidence. Non-creation and the no-query ordering are
# covered hermetically in tests/arsd/, where the filesystem primitives are
# instrumented and the assertion is made outside the recorded window. What is
# checked here, and only here, is that the *installed artifact* exposes the flag,
# refuses to render without it, and carries it into the unit's argv.
SMOKE_AGENTS_FILE="/nonexistent/ars-smoke-agents.toml"
if "$VENV/bin/python" -m agent_run_supervisor.arsd --print-service-unit \
    >/dev/null 2>&1; then
  echo "error: --print-service-unit must refuse without --agents-file" >&2
  exit 1
fi
unit="$("$VENV/bin/python" -m agent_run_supervisor.arsd --print-service-unit \
  --agents-file "$SMOKE_AGENTS_FILE")"
printf '%s\n' "$unit" | grep -F 'Restart=on-failure' >/dev/null
printf '%s\n' "$unit" | grep -F 'KillMode=control-group' >/dev/null
printf '%s\n' "$unit" | grep -F -- "--agents-file $SMOKE_AGENTS_FILE" >/dev/null

echo "Installed ${KIND} smoke passed."

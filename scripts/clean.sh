#!/usr/bin/env bash
# Remove local build artifacts, test caches, and supervisor runtime scratch under the repo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

INCLUDE_VENV="${CLEAN_VENV:-0}"

prune_find() {
  local name="$1"
  find . \
    \( -path ./.git -o -path ./.venv -o -path ./venv -o -path ./node_modules \) -prune \
    -o -type d -name "$name" -print \
    | while IFS= read -r dir; do
        rm -rf "$dir"
        echo "  removed $dir"
      done
}

prune_pyc_files() {
  find . \
    \( -path ./.git -o -path ./.venv -o -path ./venv -o -path ./node_modules \) -prune \
    -o -type f \( -name '*.pyc' -o -name '*.pyo' \) -print \
    | while IFS= read -r file; do
        rm -f "$file"
        echo "  removed $file"
      done
}

remove_if_exists() {
  local path="$1"
  if [[ -e "$path" ]]; then
    rm -rf "$path"
    echo "  removed $path"
  fi
}

echo "==> Packaging artifacts"
remove_if_exists dist
remove_if_exists build
remove_if_exists src/agent_run_supervisor.egg-info

echo "==> Python caches"
prune_find __pycache__
prune_find .pytest_cache
prune_find .mypy_cache
prune_find .ruff_cache
prune_find .hypothesis
prune_pyc_files
remove_if_exists .coverage
shopt -s nullglob
for coverage_file in .coverage.*; do
  remove_if_exists "$coverage_file"
done
shopt -u nullglob
remove_if_exists .dmypy.json
remove_if_exists htmlcov
remove_if_exists cover
remove_if_exists .tox
remove_if_exists .nox

echo "==> Local supervisor runtime artifacts (repo root)"
remove_if_exists .agent-run-supervisor
remove_if_exists outputs
remove_if_exists .tmp
remove_if_exists docs/_build

echo "==> Ephemeral wheel-smoke dirs in /tmp"
shopt -s nullglob
for dir in /tmp/ars-wheel-smoke.*; do
  rm -rf "$dir"
  echo "  removed $dir"
done
shopt -u nullglob

if [[ "$INCLUDE_VENV" == "1" ]]; then
  echo "==> Virtual environment"
  remove_if_exists .venv
fi

echo "Clean complete."

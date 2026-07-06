#!/usr/bin/env bash
# Upload built dist/* to TestPyPI or PyPI after verify gates pass.
# Credentials via TWINE_USERNAME / TWINE_PASSWORD (never commit tokens).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TARGET="${1:-testpypi}"

case "$TARGET" in
  testpypi)
    REPO_URL="https://test.pypi.org/legacy/"
    REPO_NAME="TestPyPI"
    ;;
  pypi)
    REPO_URL="https://upload.pypi.org/legacy/"
    REPO_NAME="PyPI"
    ;;
  *)
    echo "usage: $0 [testpypi|pypi]" >&2
    echo "  testpypi  upload to https://test.pypi.org (default, recommended dry-run)" >&2
    echo "  pypi      upload to https://pypi.org (prefer tag + release.yml for production)" >&2
    exit 1
    ;;
esac

if [[ -z "${TWINE_USERNAME:-}" || -z "${TWINE_PASSWORD:-}" ]]; then
  echo "error: set TWINE_USERNAME and TWINE_PASSWORD before uploading" >&2
  echo "  export TWINE_USERNAME=__token__" >&2
  echo "  export TWINE_PASSWORD=pypi-..." >&2
  exit 1
fi

echo "==> Run verify gates (includes build + twine check)"
./scripts/verify_local.sh

echo "==> Upload dist/* to ${REPO_NAME}"
uv run python -m twine upload --repository-url "$REPO_URL" dist/*

echo "Uploaded to ${REPO_NAME}."
if [[ "$TARGET" == "testpypi" ]]; then
  version="$(grep '^version' pyproject.toml | sed -n 's/version = "\(.*\)"/\1/p')"
  echo ""
  echo "Install smoke (TestPyPI needs extra-index-url for dependencies):"
  echo "  pip install --index-url https://test.pypi.org/simple/ \\"
  echo "              --extra-index-url https://pypi.org/simple/ \\"
  echo "              agent-run-supervisor==${version}"
  echo "  agent-run-supervisor doctor"
fi

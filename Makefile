.PHONY: help sync verify docs-sync docs-check docs docs-serve build smoke bump release-test release-tag clean clean-all

VERSION := $(shell grep '^version' pyproject.toml | sed -n 's/version = "\(.*\)"/\1/p')

help:
	@echo "agent-run-supervisor dev shortcuts (requires uv)"
	@echo ""
	@echo "  make sync          Install dev + release extras (uv sync)"
	@echo "  make verify        Full local gates (same as CI)"
	@echo "  make docs          Validate and build the public documentation site"
	@echo "  make docs-serve    Serve the public documentation site locally"
	@echo "  make build         sdist/wheel + twine check"
	@echo "  make smoke         build + manifest gate + wheel/sdist smoke"
	@echo "  make clean         Remove build artifacts, caches, local runtime scratch"
	@echo "  make clean-all     clean + remove .venv (re-run make sync after)"
	@echo "  make bump          Bump version (make bump VERSION=X.Y.Z)"
	@echo "  make release-test  verify + upload to TestPyPI (needs TWINE_* env)"
	@echo "  make release-tag   Print tag push commands for GitHub Actions PyPI publish"
	@echo ""
	@echo "Current version: $(VERSION)"

sync:
	uv sync --locked --extra dev --extra release --extra native

verify: sync
	./scripts/verify_local.sh

docs-sync:
	uv sync --locked --extra docs

docs-check:
	uv run python tools/check_docs_site.py

docs: docs-sync docs-check
	uv run mkdocs build --strict

docs-serve: docs-sync
	uv run mkdocs serve

build: sync
	rm -rf dist build src/agent_run_supervisor.egg-info
	uv run python -m build
	uv run python -m twine check dist/*

smoke: build
	uv run python tools/check_dist_manifest.py
	./scripts/smoke_installed_artifact.sh wheel
	./scripts/smoke_installed_artifact.sh sdist

clean:
	./scripts/clean.sh

clean-all:
	CLEAN_VENV=1 ./scripts/clean.sh

release-test: sync
	./scripts/release.sh testpypi

bump:
	@test -n "$(VERSION)" || (echo "usage: make bump VERSION=X.Y.Z" >&2 && exit 1)
	PYTHONPATH=. uv run python tools/bump_version.py $(VERSION)

release-tag:
	@echo "Production PyPI publish uses GitHub Actions Trusted Publishing."
	@echo "Configure PyPI Trusted Publisher for workflow release.yml, environment pypi,"
	@echo "then on main after verify passes:"
	@echo ""
	@echo "  git tag v$(VERSION)"
	@echo "  git push origin v$(VERSION)"
	@echo ""
	@echo "Verify: pip install agent-run-supervisor==$(VERSION) && agent-run-supervisor --help"

---
title: "P2 Release Engineering"
status: archived
created_at: 2026-06-01
last_validated_at: 2026-06-01T21:58:00+0800
archived_at: 2026-06-01T21:58:00+0800
---
# P2 Release Engineering

## Goal

Add pre-release packaging confidence without publishing anything: build the source distribution and
wheel, validate package metadata, and prove the built wheel can run the installed console `doctor`
without launching a real AGENT.

## Scope

- Add CI coverage for `python -m build` and `python -m twine check dist/*`.
- Add a non-editable installed-wheel smoke for `agent-run-supervisor --help` and
  `agent-run-supervisor doctor`.
- Add package metadata needed for pre-release validation: `LICENSE`, classifiers, URLs, keywords,
  and a `release` optional extra for build/twine tooling.
- Keep runtime dependencies empty and stdlib-only.
- Update README / README.zh-CN and roadmap feature tracking with the new quality gate.

## Boundaries

Still out of scope:

- publishing to PyPI or any registry;
- version bumping beyond current `0.0.0`;
- signing/provenance artifacts;
- release notes automation;
- live Feishu/Sachima/Gateway/public ingress/automatic replies;
- real AGENT launch from `doctor`.

## Acceptance

- CI passes on the PR and on `main` after merge.
- Local gates pass:
  ```bash
  python3 tools/build_docs_index.py --check
  python3 tools/docs_drift_signal.py --check
  git diff --check
  python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
  python3 -m pytest -q
  PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src scripts tests
  PYTHONPATH=src python3 -m agent_run_supervisor doctor
  PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
  python -m build
  python -m twine check dist/*
  ```
- Installed-wheel smoke proves:
  - console entry point exists;
  - `doctor` returns `ok: true`;
  - `doctor.launched_real_agent` is `false`;
  - packaged minimal fixture replay remains protocol-clean.

## Review focus

Codex review must check that this is release engineering only: package/build/metadata/CI, no runtime
platform behavior, no network publishing, no secret material, no live delivery, and no supervisor
business verdict.

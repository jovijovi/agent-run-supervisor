# AGENTS.md

Project-local instructions for AI agents working in this repository.

## Project identity

- Project: `agent-run-supervisor`
- Local canonical path: `/home/ecs-user/workspace/hermes/repo/agent-run-supervisor`
- Worktree root: `/home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/`
- GitHub repo: `jovijovi/agent-run-supervisor`
- Visibility: private

## Product and documentation preflight

Documentation is the project soul and precedes code. For roadmap, design, implementation, PR, CI, review, merge, or next-phase-readiness work, read in order:

1. `GOAL.md`
2. `docs/product/prd.md`
3. `docs/design/architecture.md`
4. `docs/design/technical-solution.md`
5. `docs/roadmap/features.md`
6. `docs/roadmap/current-status.md`
7. `docs/AI_FLOW.md`

Before changing files, state the current product position, feature/phase target, open tails, explicit non-approvals, and whether the requested task is allowed by the roadmap.

The pre-realignment `docs/plans/` and `docs/dev_log/` artifacts were retired and cleared and remain non-authoritative. Do not treat historical plan/dev-log artifacts as source-of-truth.

Concrete task/phase implementation plans live under `docs/plans/`, named `docs/plans/YYYY-MM-DD-<task-slug>.md`; `docs/roadmap/` owns roadmap/status/feature tracking, not task-level execution plans. See `docs/plans/README.md`.

## Development workflow

Use short-lived task branches and isolated worktrees for AI-assisted work. Branch prefixes:
`feat/`, `fix/`, `docs/`, `cicd/` — see `docs/AI_FLOW.md` § Branch model. Do not use `cursor/`
or other ad-hoc prefixes. Derive implementation plans from PRD/design/roadmap; a plan must not
redefine product goals.

Default role split unless explicitly changed:

- Claude Code: main worker for implementation/debugging/design work.
- Codex CLI: primary reviewer.
- Hermes: scope control, verification, evidence, and arbitration.

### Post-implementation documentation sync

When implementation work is **fully complete** (code, tests, and verification gates pass), check
whether project documentation needs updating. Update when needed — do not treat doc sync as optional
tail work. Typical surfaces:

- `docs/` — roadmap/status, design, product, plans, and other governed docs when behavior,
  acceptance, or completion state changed.
- `README.md` and `README.zh-CN.md` — when CLI usage, library API, install/dev/publish instructions,
  or examples changed.
- `CHANGELOG.md` — when preparing user-visible release notes (usually before a release).

Run `python tools/build_docs_index.py --write` and `python tools/docs_drift_signal.py --write` after
governed docs changes (see Tooling expectations).

### Release and publishing authorization

Git tag creation, GitHub Release publication, and PyPI package publishing are **not** part of
default implementation work. Perform them **only after explicit human permission and authorization**
— typically after development is finished, documentation is synced, and verification passes.

Do not tag, publish a release, or upload to PyPI proactively, during active implementation, or
immediately after merge without an explicit request.

## Product boundaries

The product requirement includes both acpx one-shot exec and persistent sessions. Engineering may implement exec first and sessions later, but PRD/DESIGN/GOAL must not describe the product as exec-only.

Do not infer Sachima integration, real AGENT auto-replies, public ingress, real delivery, Gateway lifecycle operations, production config writes, live/default-on behavior, `@all`, or agent-to-agent auto-routing from docs/governance changes.

## Secrets and credentials

Never commit secrets, API keys, tokens, cookies, raw environment values, real webhook secrets, or private platform identifiers.

Use `[REDACTED]` in docs and examples when referring to sensitive values. Keep real runtime values in local environment files that are ignored by git.

## Tooling expectations

- Runtime should stay Python stdlib-only unless a phase explicitly approves dependencies.
- Use `python3 -m pytest -q` for tests.
- Use `python3 -m compileall -q src scripts tests` for syntax/import smoke.
- Use `PYTHONPATH=src python3 -m agent_run_supervisor ...` for local CLI smoke unless the package has been installed in the active environment.
- Use `python tools/build_docs_index.py --write` after docs changes and never hand-edit `docs/INDEX.md`.
- Use `python tools/docs_drift_signal.py --write` after governed docs changes.

## Knowledge document validation

Lessons and practices carry `last_validated_at`. Validation is use-driven: when a commit, PR body, or active project doc cites a specific lesson or practice path, the citing change must either bump `last_validated_at`, refine the document and bump it, or deprecate/supersede it.

`tools/docs_drift_signal.py` writes `docs/lessons/_drift_report.md`. Treat it as a signal that the next citing change must process the named knowledge docs.

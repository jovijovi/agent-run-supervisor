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

The vNext-only authority chain is the sole basis for new development. The former mixed v0.1.7/vNext
authority snapshot, completed plans/phases, migration documents, and old branch/PR instructions are
cold history under `docs/archive/`, `docs/plans/archive/`, and `docs/roadmap/archive/`. Never load them by
default or use them to choose product scope, modules, branches, gates, acceptance, or approval.

Concrete **active** implementation plans live under `docs/plans/active/` (see
`docs/plans/README.md`). Non-approvals: `docs/roadmap/non-approvals.md`. The released v0.1.7 acpx code
and `docs/design/result-event-schema.md` remain compatibility baselines only; they do not redefine vNext.

## Development workflow

Use short-lived task branches and isolated worktrees for AI-assisted work. Branch prefixes:
`feat/`, `fix/`, `docs/`, `cicd/` — see `docs/AI_FLOW.md` § Branch model. Do not use `cursor/`
or other ad-hoc prefixes. Derive implementation plans from PRD/design/roadmap; a plan must not
redefine product goals.

Task role/model assignment comes from the current user/controller authorization, not from archived plans
or a repository-pinned AGENT. Preserve independent review for authority-bearing or implementation changes;
Hermes owns scope control, deterministic verification, evidence arbitration, and side-effect authority.

### Post-implementation documentation sync

When implementation work is **fully complete** (code, tests, and verification gates pass), check
whether project documentation needs updating. Update when needed — do not treat doc sync as optional
tail work. Typical surfaces:

- `docs/` — board, features, `docs/plans/active/` or archive moves, design/product when behavior
  or acceptance changed; closed phase detail → `docs/roadmap/archive/phases/`.
- `README.md` and `README.zh-CN.md` — when CLI usage, library API, install/dev/publish instructions,
  or examples changed.
- `CHANGELOG.md` — when preparing user-visible release notes (usually before a release).

- When a plan's work merges: `git mv` from `docs/plans/active/` to `docs/plans/archive/`,
  update board `active_plan:` and phase archive as needed.

Run `python tools/build_docs_index.py --write` and `python tools/docs_drift_signal.py --write` after
governed docs changes (see Tooling expectations).

### Implementation plan context

- Read **`docs/plans/active/`** only for in-flight execution plans (plus board `active_plan:`).
- Do **not** load `docs/plans/archive/` or `docs/roadmap/archive/` by default — audit/dispute only.

### Release and publishing authorization

Git tag creation, GitHub Release publication, and PyPI package publishing are **not** part of
default implementation work. Perform them **only after explicit human permission and authorization**
— typically after development is finished, documentation is synced, and verification passes.

Do not tag, publish a release, or upload to PyPI proactively, during active implementation, or
immediately after merge without an explicit request.

## Product boundaries

The only new-development target is the vNext supervision architecture in GOAL/PRD/design: local `arsd`
production ingress, ars-core/Native ACP, process-per-Run plus real Session load, immutable per-Run config,
and fail-closed evidence/recovery. The v0.1.7 acpx modes remain compatibility-only and never become
Native driver/fallback or a reason to restore archived product requirements.

Do not infer Stage 0/1 implementation, Stage 2 `arsd`, service/cgroup enablement, release/publication,
Sachima integration, real AGENT auto-replies, public ingress, delivery, Gateway lifecycle, production
config writes, live/default-on behavior, `@all`, or agent-to-agent auto-routing from docs/governance changes.

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
- Before a release: `make bump VERSION=X.Y.Z` (or `uv run python tools/bump_version.py X.Y.Z`) to sync
  `pyproject.toml`, `src/agent_run_supervisor/__init__.py`, `uv.lock`, and a CHANGELOG stub; then edit
  CHANGELOG and run `make verify` (includes `tools/check_version_sync.py`).

## Knowledge document validation

Lessons and practices carry `last_validated_at`. Validation is use-driven: when a commit, PR body, or active project doc cites a specific lesson or practice path, the citing change must either bump `last_validated_at`, refine the document and bump it, or deprecate/supersede it.

`tools/docs_drift_signal.py` writes `docs/lessons/_drift_report.md`. Treat it as a signal that the next citing change must process the named knowledge docs.

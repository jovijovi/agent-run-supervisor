# AGENTS.md

Project-local instructions for AI agents working in this repository.

## Project identity

- Project: `agent-run-supervisor`
- Local canonical path: `/home/ecs-user/workspace/hermes/repo/agent-run-supervisor`
- Worktree root: `/home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/`
- GitHub repo: `jovijovi/agent-run-supervisor`
- Visibility: private

## Project goal and roadmap preflight

For any roadmap, phase-gate, implementation, PR, CI, review, merge, or next-phase-readiness work, read these first:

1. `GOAL.md`;
2. `docs/roadmap/current-status.md`;
3. `docs/AI_FLOW.md`;
4. the latest relevant plan and dev log linked from current status.

Before changing files, state the current project position, next allowed request, explicit non-approvals, open `BLOCKER` / `NEXT_PHASE` / `WATCH` / `PARKED` tails, and whether the requested task is allowed by current status.

If `docs/roadmap/current-status.md` is missing, stale, or contradicts the requested work, stop and report the drift risk before editing. If the requested task is to create or repair this roadmap system, use a clean `origin/main` worktree and record the repair in `docs/dev_log/`.

For multi-phase, production-adjacent, high-risk, or next-phase-readiness work, load the `phase-gate-drift-control` skill. Do not infer persistent sessions, Sachima integration, real AGENT auto-replies, public ingress, real delivery, Gateway lifecycle operations, production config writes, or live/default-on approval from docs/governance changes.

## Development workflow

Read `docs/AI_FLOW.md` before starting any non-trivial development task.

Use short-lived task branches and isolated worktrees for AI-assisted work. Persist approved plans under `docs/plans/`, write a dev log under `docs/dev_log/`, and open PRs that link the plan, dev log, verification evidence, review result, and secret-safety review.

Default role split unless explicitly changed:

- Claude Code: main worker for implementation/debugging.
- Codex CLI: primary reviewer.
- Hermes: scope control, verification, evidence, and arbitration.

## Secrets and credentials

Never commit secrets, API keys, tokens, cookies, raw environment values, real webhook secrets, or private platform identifiers.

Use `[REDACTED]` in docs and examples when referring to sensitive values. Keep real runtime values in local environment files that are ignored by git.

## Tooling expectations

- Runtime should stay Python stdlib-only unless a phase explicitly approves dependencies.
- Use `python3 -m pytest -q` for tests.
- Use `python3 -m compileall -q src scripts tests` for syntax/import smoke.
- Use `PYTHONPATH=src python3 -m agent_run_supervisor ...` for local CLI smoke unless the package has been installed in the active environment.
- Use `python tools/build_docs_index.py --write` after docs changes and never hand-edit `docs/INDEX.md`.

## Documentation workflow

Every development task ends with a `docs/dev_log/YYYY-MM-DD-<topic>.md` entry. Required deliverable, no exceptions.

Plans live under `docs/plans/YYYY-MM-DD-<topic>.md`. They are repository artifacts, not chat scratchpads.

If a task surfaces a repeatable pitfall, add a lesson under `docs/lessons/YYYY-MM-DD-<topic>.md` and link it from root `LESSONS.md`. If a task produces a reusable process or convention, add a practice under `docs/practices/YYYY-MM-DD-<topic>.md` and link it from `docs/practices/README.md`.

## Knowledge document validation

Lessons and practices carry `last_validated_at`. Validation is use-driven: when a dev log, commit, or PR body cites a specific lesson or practice path, the citing change must either bump `last_validated_at`, refine the document and bump it, or deprecate/supersede it.

`tools/docs_drift_signal.py` writes `docs/lessons/_drift_report.md`. Treat it as a signal that the next citing change must process the named knowledge docs.

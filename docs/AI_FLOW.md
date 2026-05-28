---
title: "AI-assisted development flow"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-28T20:00:00+0800
---
# AI-assisted development flow

## Purpose

`agent-run-supervisor` is developed with humans and AI agents working together. This document defines the branch model, task lifecycle, plan persistence, commit and PR conventions, verification gates, rollback strategy, and worktree rules that keep that collaboration auditable, reversible, and safe.

A new human or AI agent should be able to read this document once and operate correctly without chat-history context.

## Goal and roadmap preflight

Before any roadmap, phase-gate, implementation, PR, CI, review, merge, or next-phase-readiness work, read:

1. `GOAL.md`;
2. `docs/roadmap/current-status.md`;
3. this file;
4. the latest relevant plan and dev log linked from current status.

Before changing files, state the current project position, next allowed request, explicit non-approvals, open tails, and whether the requested task is allowed by the current status.

Docs/governance work can update `GOAL.md` or `docs/roadmap/current-status.md`, but it does not approve persistent sessions, Sachima integration, real AGENT auto-replies, public ingress, real delivery, Gateway lifecycle operations, production config writes, or live/default-on behavior.

## Branch model

This project uses trunk-based development with short-lived per-task branches.

```text
main                              # integration trunk
  ├── ai/<topic>-<yyyy-mm-dd>      # AI-led task branch
  ├── feat/<topic>                 # feature branch
  ├── fix/<topic>                  # bugfix branch
  └── docs/<topic>-<yyyy-mm-dd>     # documentation/governance branch
```

Rules:

- `main` is the integration trunk and should be kept releasable.
- One task branch = one task = one PR.
- Do not commit directly to `main` except for explicitly approved trivial metadata changes.
- If a task spans sessions, push WIP and leave a clear plan/dev-log trail.

## Per-task lifecycle

Every AI-assisted task follows this loop:

1. **Start from a clean trunk**
   ```bash
   git fetch origin main
   git worktree add /home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/<branch-slug> -b ai/<topic>-$(date +%F) origin/main
   cd /home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/<branch-slug>
   ```
2. **Preflight** — read `GOAL.md`, `docs/roadmap/current-status.md`, `docs/AI_FLOW.md`, and the latest relevant plan/dev log.
3. **Plan** — inspect relevant context, write a concrete plan, and get human approval before product/code changes when the task is non-trivial.
4. **Persist the plan** — save it under `docs/plans/YYYY-MM-DD-<topic>.md`.
5. **Implement with narrow commits** — use TDD for behavior changes; stage files by name after inspecting the diff.
6. **Write task knowledge** — every task gets a dev log under `docs/dev_log/`. Add lessons/practices when the task surfaces reusable knowledge.
7. **Verify locally** — run the gates below.
8. **Push and open PR** — PR targets `main`, links plan/dev log, and lists verification and review evidence.
9. **Review, fix, merge, verify main** — address blockers, merge only when green, then verify `main` from a clean checkout/worktree.

## Plan-as-artifact

Plans are repository artifacts, not disposable scratchpads.

- Canonical location: `docs/plans/YYYY-MM-DD-<topic>.md`.
- Required shape:
  - `## Context`
  - `## Proposed approach`
  - `## Step-by-step plan`
  - `## Files likely to change`
  - `## Verification`
  - `## Risks and open questions`
- Lifecycle: write once at task start after approval. Do not rewrite merged plans to hide what changed later; write a dev log for actual execution.
- PR bodies should link the plan. Non-trivial commit bodies should include `Plan: docs/plans/...`.

## Commit conventions

Use Conventional Commits:

```text
<type>(<optional-scope>): <imperative summary>

Explain why the change exists.

Verification:
- <command> -> <result>

Plan: docs/plans/YYYY-MM-DD-<topic>.md
```

Never include secrets, tokens, cookies, raw environment values, real webhook values, or private platform identifiers.

## Verification gates

Run these before PR or merge unless the task clearly explains why a gate is irrelevant:

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

Secret/static safety gates:

- Run a secret-shaped scan over added/changed text before commit.
- Run a static dangerous-pattern scan for new subprocess/network/config-write surfaces when the phase forbids them.
- Use `[REDACTED]`, placeholders, or split synthetic strings in docs/tests.

## CI gates

GitHub Actions should mirror the portable local gates:

- install project with dev dependencies;
- fixture validation;
- pytest;
- compileall;
- CLI doctor/replay smoke;
- docs index check;
- docs drift check;
- `git diff --check`.

## PR requirements

Every PR should include:

- Summary of changes.
- Plan link or explicit note that no plan was needed.
- Dev log link.
- Lessons/practices touched.
- Test plan with commands and results.
- Review evidence: Claude Code main-worker notes when applicable, Codex review result when required.
- Secret-safety statement.
- Boundary statement for explicit non-approvals.

Target `main` unless a future roadmap explicitly introduces another integration trunk.

## Rollback strategy

Granular history keeps AI changes reversible.

| Scope | Action |
|---|---|
| Single merged PR | Revert the PR merge or squash commit on `main` |
| Single commit before merge | Revert or amend on the task branch |
| Multiple related PRs | Revert in reverse merge order, then open a fresh corrective PR |
| Broken trunk | Stop new merges, identify last good commit, revert forward with reviewed PRs |

Never force-push `main`. If a force-push seems necessary, stop and discuss first.

## Anti-patterns to avoid

- Starting product/code changes without reading the goal, current status, and AI flow.
- Letting a plan live only in chat history.
- Skipping `docs/dev_log/` because a PR body already explains the work.
- Treating `allowed_roots` as an OS sandbox.
- Treating runner completion as business PASS.
- Letting dry-run/preview work imply real AGENT execution.
- Broad `git add -A` without inspecting the diff.
- Committing runtime outputs, prompt material, raw stderr with secrets, `.env`, or token files.
- Ignoring CI because local tests passed.

## Quick reference

```bash
TOPIC=<topic>
BRANCH=ai/$TOPIC-$(date +%F)
git fetch origin main
git worktree add /home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/$TOPIC -b "$BRANCH" origin/main
cd /home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/$TOPIC
# read GOAL.md, docs/roadmap/current-status.md, docs/AI_FLOW.md
# write docs/plans/$(date +%F)-$TOPIC.md before non-trivial implementation
# implement + write docs/dev_log/$(date +%F)-$TOPIC.md
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git push -u origin HEAD
gh pr create --base main --title "<conventional subject>" --body-file <body-file>
```

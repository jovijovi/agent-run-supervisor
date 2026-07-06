---
title: "AI-assisted development flow"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-29T18:25:40+0800
---
# AI-assisted development flow

## Purpose

This repository is developed with humans and AI agents working together. This document defines how to move from product documents to implementation while keeping work auditable, reversible, and aligned with the product goal.

Documentation development and management come before code development: the documents are the project soul. Code implements the documents, not the other way around.

## Document hierarchy

The authority chain is:

```text
PRD -> design documents -> roadmap/current-status + feature tracker -> approved phase implementation plan (docs/plans/) -> code
```

Concrete task/phase implementation plans live under `docs/plans/`; `docs/roadmap/`
owns roadmap, status, and feature tracking, not task-level execution plans. See
`docs/plans/README.md`.

Required preflight for roadmap, phase-gate, implementation, PR, CI, review, merge, or next-phase-readiness work:

1. `GOAL.md`
2. `docs/product/prd.md`
3. `docs/design/architecture.md`
4. `docs/design/technical-solution.md`
5. `docs/roadmap/features.md`
6. `docs/roadmap/current-status.md`
7. this file

The pre-realignment `docs/plans/` and `docs/dev_log/` artifacts were retired and cleared and remain non-authoritative; do not use historical plan/dev-log artifacts as source-of-truth. The `docs/plans/` directory is now the home for fresh, approved implementation plans named `YYYY-MM-DD-<task-slug>.md` (see `docs/plans/README.md`).

## Branch model

Use trunk-based development with short-lived per-task branches.

```text
main                              # integration trunk
  ├── feat/<topic>                 # feature / product capability work
  ├── fix/<topic>                    # bugfix
  ├── docs/<topic>                   # documentation-only changes
  └── cicd/<topic>                   # CI/CD, release engineering, dev workflow tooling
```

Prefix rules:

| Prefix | Use for |
|---|---|
| `feat/` | New features or product capability implementation |
| `fix/` | Bug fixes |
| `docs/` | Documentation-only updates (no runtime behavior change) |
| `cicd/` | CI/CD pipelines, release/packaging workflow, local dev gate scripts |

Rules:

- `main` is the integration trunk and should stay releasable.
- One task branch = one task = one PR.
- Do **not** use `cursor/`, `ai/`, or other ad-hoc prefixes for task branches.
- Do not commit directly to `main` except explicitly approved trivial metadata changes.
- Start from a clean `origin/main` worktree.

## Per-task lifecycle

1. **Preflight** — read the document hierarchy above and state whether the requested work matches current roadmap/status.
2. **Scope** — confirm whether the task is documentation, design, implementation, review, or cleanup.
3. **Plan** — for non-trivial implementation, derive a phase implementation plan from PRD/design/roadmap. The plan must not redefine product goals.
4. **Implement** — use narrow commits and TDD for behavior changes.
5. **Update authority docs** — update PRD/design/feature tracker/roadmap when the product, design, completion state, or acceptance evidence changes.
6. **Verify** — run local gates and scans.
7. **Review** — Claude Code may be main worker; Codex CLI is primary reviewer; Hermes verifies and arbitrates with evidence.
8. **PR and merge** — push branch, open PR, wait for CI, merge only when green, then verify `main` from a clean checkout/worktree.

## Implementation plan rule

A phase implementation plan is an execution artifact created only after PRD/design/roadmap target is clear. It lives under `docs/plans/` and is named `docs/plans/YYYY-MM-DD-<task-slug>.md`. It must include:

- context and exact target from PRD/design/roadmap;
- checklist of implementation goals;
- acceptance criteria;
- files likely to change;
- verification gates;
- risks/open questions;
- rollback strategy.

A plan must not redefine product goals, expand product scope, or imply new live/runtime approvals. Keep task-level execution plans in `docs/plans/`, not in `docs/roadmap/`, which owns roadmap/status/feature tracking. The pre-realignment plans were cleared because they no longer represent the current authority chain; future plans must be fresh and trace back to `docs/roadmap/current-status.md`. See `docs/plans/README.md`.

## Commit conventions

Use Conventional Commits:

```text
<type>(<optional-scope>): <imperative summary>

Explain why the change exists.

Verification:
- <command> -> <result>
```

Never include secrets, tokens, cookies, raw environment values, real webhook values, or private platform identifiers.

## Verification gates

Run these before PR or merge unless the task clearly explains why a gate is irrelevant:

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

Secret/static safety gates:

- Run a secret-shaped scan over added/changed text before commit.
- Run static dangerous-pattern scans for new subprocess/network/config-write surfaces when relevant.
- Use `[REDACTED]` placeholders for sensitive examples.

## PR requirements

Every non-trivial PR should include:

- summary of changes;
- source-of-truth docs touched;
- feature tracker / roadmap status impact;
- test plan with commands and results;
- review evidence;
- secret-safety statement;
- boundary statement for explicit non-approvals.

Target `main` unless a future roadmap explicitly introduces another integration trunk.

## Anti-patterns

- Starting code work before PRD/design/roadmap alignment.
- Treating historical plan/dev-log files as authority.
- Putting task-level implementation plans in `docs/roadmap/` instead of `docs/plans/`.
- Letting exec-first engineering sequence shrink PRD or design scope.
- Treating `allowed_roots` as an OS sandbox.
- Treating runner completion as business PASS.
- Letting dry-run/preview docs imply real AGENT auto-replies, public ingress, delivery, or Gateway operations.
- Broad `git add -A` without inspecting the diff.
- Committing runtime outputs, prompt material, raw stderr with secrets, `.env`, or token files.

---
title: "AI-assisted development flow"
status: active
created_at: 2026-05-28
last_validated_at: 2026-07-07T15:30:00+0800
---
# AI-assisted development flow

## Purpose

This repository is developed with humans and AI agents working together. This document defines how to move from product documents to implementation while keeping work auditable, reversible, and aligned with the product goal.

Documentation development and management come before code development: the documents are the project soul. Code implements the documents, not the other way around.

## Document hierarchy

The authority chain is:

```text
PRD -> design -> features + living board -> docs/plans/active/ -> code
```

| Layer | Path |
|---|---|
| Living board | `docs/roadmap/current-status.md` |
| Features | `docs/roadmap/features.md` |
| Non-approvals | `docs/roadmap/non-approvals.md` |
| Verification | `docs/roadmap/verification.md` |
| Active plans | `docs/plans/active/` |
| Closed phase archive | `docs/roadmap/archive/phases/` |
| Closed plans | `docs/plans/archive/` |

**Agent context:** read the board, features, and `docs/plans/active/` only. Do not default-load archive directories.

Required preflight for roadmap, phase-gate, implementation, PR, CI, review, merge, or next-phase-readiness work:

1. `GOAL.md`
2. `docs/product/prd.md`
3. `docs/design/architecture.md`
4. `docs/design/technical-solution.md`
5. `docs/roadmap/features.md`
6. `docs/roadmap/current-status.md`
7. this file

Path migration map: `docs/roadmap/MIGRATION.md`. Plan layout: `docs/plans/README.md`.

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
3. **Plan** — for non-trivial implementation, create a plan in `docs/plans/active/`. The plan must not redefine product goals.
4. **Implement** — use narrow commits and TDD for behavior changes.
5. **Update authority docs** — update board/features/archive when completion state changes; `git mv` plan to archive when merged.
6. **Verify** — run `./scripts/verify_local.sh` (see `docs/roadmap/verification.md`).
7. **Review** — see Review requirements below.
8. **PR and merge** — push branch, open PR, wait for CI, merge only when green.

## Implementation plan rule

Active plans live at `docs/plans/active/YYYY-MM-DD-<task-slug>.md`. On merge, move to `docs/plans/archive/` with `status: archived`.

A plan must include: context/target, checklist, acceptance, files likely to change, verification gates, risks, rollback.

A plan must not redefine product goals or imply new live/runtime approvals. Trace back to `docs/roadmap/current-status.md`.

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

Canonical entry: [`scripts/verify_local.sh`](../../scripts/verify_local.sh) and
[`docs/roadmap/verification.md`](roadmap/verification.md).

Quick smoke (when full verify is too heavy for a tiny doc-only change):

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
python3 tools/build_docs_index.py --check
python3 tools/docs_drift_signal.py --check
python3 tools/check_roadmap_governance.py
git diff --check
```

## Review requirements

- Claude Code: main worker for implementation/debugging/design work unless explicitly deviating.
- Codex CLI: primary reviewer.
- Hermes: scope control, verification, gates, and evidence arbitration.
- Reviewers must check PRD/design/feature/roadmap alignment, not only whether tests pass.

## PR requirements

Every non-trivial PR should include:

- summary of changes;
- source-of-truth docs touched;
- feature tracker / roadmap status impact;
- test plan with commands and results;
- review evidence;
- secret-safety statement;
- boundary statement for explicit non-approvals (`docs/roadmap/non-approvals.md`).

Target `main` unless a future roadmap explicitly introduces another integration trunk.

## Anti-patterns

- Starting code work before PRD/design/roadmap alignment.
- Loading `docs/plans/archive/` or `docs/roadmap/archive/` as default context.
- Putting task-level implementation plans in `docs/roadmap/` instead of `docs/plans/active/`.
- Appending merge history to the living board.
- Letting exec-first engineering sequence shrink PRD or design scope.
- Treating `allowed_roots` as an OS sandbox.
- Treating runner completion as business PASS.
- Broad `git add -A` without inspecting the diff.
- Committing runtime outputs, prompt material, raw stderr with secrets, `.env`, or token files.

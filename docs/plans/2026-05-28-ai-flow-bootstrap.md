---
title: "AI_FLOW Bootstrap Plan"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-28T20:00:00+0800
---
# AI_FLOW Bootstrap Plan

## Context

The user reminded us that `agent-run-supervisor` must follow the same AI-assisted development discipline proven in `sachima-im-simulator`: a project goal, living roadmap/status, explicit AI flow, persisted plans/dev logs, generated docs index, drift signal, verification gates, review evidence, and PR hygiene.

Current gap: V0.1a was implemented and pushed, but the new repo did not yet carry the full `AI_FLOW` scaffolding that future agents can follow without chat history.

## Proposed approach

Bootstrap the lightweight governance layer directly in this repo, adapted to Python/acpx rather than Next.js/Playwright:

- add `GOAL.md`;
- add `AGENTS.md`;
- add `docs/AI_FLOW.md`;
- add `docs/roadmap/current-status.md` and `docs/roadmap/README.md`;
- install docs index/drift tools;
- add root `LESSONS.md` and `docs/practices/README.md`;
- add frontmatter to existing docs so the generated index can own discovery;
- add a portable GitHub Actions `Verify` workflow.

## Step-by-step plan

1. Create a clean task worktree from `origin/main` on `ai/ai-flow-bootstrap-2026-05-28`.
2. Read Sachima IM Simulator `GOAL.md`, `docs/AI_FLOW.md`, and `docs/roadmap/current-status.md` as the model.
3. Add project-local governance docs adapted to `agent-run-supervisor` boundaries.
4. Install `tools/build_docs_index.py` and `tools/docs_drift_signal.py` from the knowledge-discipline skill.
5. Add frontmatter to existing design/plan/dev-log documents.
6. Generate `docs/INDEX.md` and `docs/lessons/_drift_report.md`.
7. Run local gates:
   - `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0`
   - `python3 -m pytest -q`
   - `python3 -m compileall -q src scripts tests`
   - `PYTHONPATH=src python3 -m agent_run_supervisor doctor`
   - `PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson`
   - `python tools/build_docs_index.py --check`
   - `python tools/docs_drift_signal.py --check`
   - `git diff --check`
8. Commit, push, open PR, and watch CI.

## Files likely to change

- `AGENTS.md`
- `GOAL.md`
- `README.md`
- `.gitignore`
- `.github/workflows/verify.yml`
- `docs/AI_FLOW.md`
- `docs/roadmap/README.md`
- `docs/roadmap/current-status.md`
- `docs/INDEX.md`
- `docs/lessons/_drift_report.md`
- `docs/plans/*.md`
- `docs/dev_log/*.md`
- `docs/design/v0.1a-design.md`
- `docs/practices/README.md`
- `LESSONS.md`
- `tools/build_docs_index.py`
- `tools/docs_drift_signal.py`

## Verification

Use the local gates listed above, plus GitHub Actions `Verify` after opening the PR.

## Risks and open questions

- Do not copy Sachima-specific protocol/IM boundaries as if this repo owned them; adapt them to runner-supervision boundaries.
- Do not let governance docs imply real-run/live execution approval.
- Docs drift is history-dependent. If a future validation commit becomes necessary, do not squash that PR unless the drift report is regenerated after squash.

---
title: "Goal-first final docs plan"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T11:09:05+0800
---
# Goal-first final docs plan

## Context

The user corrected the project workflow: AI collaboration must start from a complete project/product/feature/requirements target, then design the full solution, then derive a phased implementation plan. The current repository had drifted toward a V0.1c manual-approval direction that conflicts with the earlier role-bound authorization decision and `docs/design/v0.1a-design.md`.

## Proposed approach

Create a final documentation set that fixes the north star and prevents more drift:

1. PRD: `docs/product/prd.md`.
2. Technical solution: `docs/design/technical-solution.md`.
3. Implementation plan: `docs/roadmap/implementation-plan.md`.
4. V0.1a conformance matrix: `docs/roadmap/v0.1a-design-conformance.md`.
5. Living status updates that deprecate the manual approval branch and point next work to V0.1a exec-only runner completion.

## Step-by-step plan

1. Read `GOAL.md`, `docs/roadmap/current-status.md`, `docs/AI_FLOW.md`, and `docs/design/v0.1a-design.md`.
2. Inspect current implementation files under `src/agent_run_supervisor/` and tests under `tests/`.
3. Write the four final documents.
4. Update `GOAL.md`, `docs/roadmap/current-status.md`, and the V0.1c design frontmatter/body so it is no longer active mainline design.
5. Write a dev log for this docs correction.
6. Regenerate `docs/INDEX.md` and `docs/lessons/_drift_report.md`.
7. Run local gates and commit.
8. Open a PR with a no-squash note if drift validation requires a follow-up commit.

## Files likely to change

- `GOAL.md`
- `docs/product/prd.md`
- `docs/design/technical-solution.md`
- `docs/design/v0.1c-hitl-manual-real-run-design.md`
- `docs/roadmap/current-status.md`
- `docs/roadmap/implementation-plan.md`
- `docs/roadmap/v0.1a-design-conformance.md`
- `docs/plans/2026-05-29-goal-first-final-docs.md`
- `docs/dev_log/2026-05-29-goal-first-final-docs.md`
- `docs/INDEX.md`
- `docs/lessons/_drift_report.md`

## Verification

- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0`
- `python3 -m pytest -q`
- `python3 -m compileall -q src scripts tests`
- `PYTHONPATH=src python3 -m agent_run_supervisor doctor`
- `PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson`
- `python tools/build_docs_index.py --check`
- `python tools/docs_drift_signal.py --check`
- `git diff --check`
- docs-only secret/static scan

## Risks and open questions

- `session_search` did not return the prior detailed chat transcript; repo docs and compacted context are used as authority.
- Drift report may need a post-content validation commit because this task touches governed docs.
- This docs phase must not imply real launch approval, persistent sessions, Sachima integration, public ingress, Gateway operations, or production config writes.

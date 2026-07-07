---
title: "L2 — Hermes caller + offline Feishu view-model"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: l2-hermes-caller
---

# L2 — Hermes caller + offline Feishu view-model

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## L2 — Hermes caller + offline Feishu view-model implementation (local/offline)

Goal: implement the approved concrete Hermes caller and offline Feishu rich-card view-model
adapter above the generic I1 boundary, covering both one-shot `exec` and persistent-session
document-check flows while keeping the supervisor generic.

Checklist:

- [x] Add the caller-side package `src/agent_run_supervisor/hermes_caller/` with `task`,
  `intake`, caller-owned `verdict`, normalized-event evidence projection, view-model,
  offline Feishu payload adapter, and `HermesDocCheckCaller` orchestration.
- [x] Add `tests/hermes_caller/` RED/GREEN coverage for intake, verdict derivation,
  normalized-event projections, progress/result view-models, escaped offline card payloads,
  exec flow, persistent-session flow, and static forbidden-surface guards.
- [x] Preserve the I1 generic contract: no platform fields in `CallerInvocationSpec` /
  `CallerResult`, supervisor `business_verdict` remains `null`, and caller-owned verdicts live
  only in `hermes_caller.verdict` / view-models.
- [x] Keep everything stdlib-only and fake/local/offline: no Feishu SDK/API, no IM delivery,
  no public ingress, no Gateway/Sachima behavior, no automatic replies, no live/default-on
  behavior, and no trusted Markdown/HTML rendering.

Acceptance evidence for this branch:

- `python3 -m pytest -q tests/hermes_caller` → 39 passed.
- `python3 -m pytest -q` → full suite passed.
- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0` → passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src scripts tests` → passed.
- `PYTHONPATH=src python3 -m agent_run_supervisor doctor` → `ok: true`, `launched_real_agent: false`.
- `PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson` → `final_message: CODEX_ACPX_OK`, `business_verdict: null`.

Status: **Closed on `main` via PR #27 (`eb7912e`).** PR #27 passed CI, Codex primary
post-PR review, full local post-merge gates, docs index/drift checks, and post-merge verification.

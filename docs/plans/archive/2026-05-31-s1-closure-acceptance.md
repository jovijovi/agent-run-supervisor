---
title: "S1 Persistent Session Closure Acceptance Plan"
status: archived
created_at: 2026-05-31
archived_at: 2026-07-07
---
> **Archived plan（冷区）：** 非 active 上下文。Roadmap 章节迁移见
> [`docs/roadmap/MIGRATION.md`](../roadmap/MIGRATION.md)。
> 验收摘要见对应 [`docs/roadmap/archive/phases/`](../roadmap/archive/phases/) 条目。

# S1 Persistent Session Closure Acceptance Plan

## Goal

Close the remaining **S1 acceptance tails** without inventing new S1 phase labels. S1 remains the four approved slices (`S1a` / `S1b` / `S1c` / `S1d`); this PR is an S1 closure/acceptance hardening PR, not `S1e`.

## Current position

- S1a captured `acpx@0.10.0` persistent-session command/schema fixtures.
- S1b implemented the local session store, role/workspace/policy binding, and lease foundation.
- S1c implemented local `session create|send|status` over fake-executor/fixture evidence.
- S1d implemented local `session close|abort|list`, lifecycle guard, closed-session refusal, and race regressions.

Remaining S1 acceptance tails from `docs/roadmap/current-status.md` are:

1. real-acpx persistent-session smoke evidence;
2. multi-turn resume/continuity proof through `SessionRuntime`/CLI;
3. clear disposition of crash/interruption and retention/cleanup tails between S1 and H1.

## Scope

### In scope

- Add a reproducible local smoke script for the S1 persistent-session lifecycle:
  `create -> send turn 1 -> send turn 2 -> status -> list -> close`.
- Keep the smoke local-only and explicit; it may use `npx -y acpx@0.10.0` when `acpx` is not on PATH.
- Persist only sanitized smoke evidence suitable for docs/PR summaries; raw local smoke artifacts remain outside git.
- Add fake-executor regression coverage for two-turn continuity using S1a `session-prompt-turn1` and `session-prompt-turn2` fixtures.
- Update PRD/design/feature tracker/current-status so S1 closure is accurate and S1a–S1d labels remain stable.
- Move full operational retention/cleanup and broad long-lived hardening to H1 where the roadmap already owns them.

### Out of scope / still not approved

- Sachima/Hermes/Gateway/IM integration.
- Public ingress, real IM delivery, production config writes, Gateway lifecycle operations, automatic replies, `@all`, or agent-to-agent routing.
- Treating `allowed_roots` as an OS/filesystem sandbox.
- New non-stdlib runtime dependencies.
- Broad cleanup/retention implementation beyond documenting the H1 carry-over boundary.

## Acceptance criteria

- `scripts/smoke_persistent_session.py` (or equivalent) runs a real local persistent-session lifecycle using the existing CLI/library surface and exits 0 when:
  - both sends complete;
  - turn markers are observed;
  - status reports ok;
  - list reports the local session record;
  - close marks the local record closed;
  - every lifecycle result keeps `business_verdict: null`.
- A focused test proves two sequential `SessionRuntime.send(...)` calls against the same local session use the same session record, persist two distinct turn artifacts, release the lease after each turn, and parse `S1A_SESSION_TURN_1_OK` / `S1A_SESSION_TURN_2_OK`.
- Docs explicitly say S1 is closed for local persistent-session lifecycle after S1a–S1d plus closure evidence, while H1 owns retention/cleanup and broader long-lived operational hardening.
- Generated docs are synced.
- Full gates pass:
  - `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0`
  - `python3 -m pytest -q`
  - `python3 -m compileall -q src scripts tests`
  - `PYTHONPATH=src python3 -m agent_run_supervisor doctor`
  - `PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson`
  - `python tools/build_docs_index.py --check`
  - `python tools/docs_drift_signal.py --check`
  - `git diff --check`
  - added-line secret/static scans
- Review gate passes: Codex primary review + Claude auxiliary review.

## Likely files

- `scripts/smoke_persistent_session.py`
- `tests/test_session_runtime.py`
- `docs/product/prd.md`
- `docs/design/architecture.md`
- `docs/design/technical-solution.md`
- `docs/roadmap/features.md`
- `docs/roadmap/current-status.md`
- `docs/INDEX.md`
- `docs/lessons/_drift_report.md`

## Rollback

Revert this PR. S1 returns to the post-S1d state: local lifecycle implementation exists, but S1 remains Partial pending closure evidence.

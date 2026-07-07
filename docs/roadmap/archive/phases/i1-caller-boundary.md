---
title: "I1 — Thin caller integration"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: i1-caller-boundary
---

# I1 — Thin caller integration

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## I1 — Thin caller integration

Goal: add a generic, local-only library caller boundary while keeping the supervisor independent.

Checklist before start:

- [x] E1 exec support merged and verified.
- [x] S1 session support merged and verified for local persistent-session lifecycle.
- [x] H1 operational hardening merged on `main` via PR #19 at `484ae23`.
- [x] Generic caller responsibility split is documented: caller chooses role/prompt/context/cwd/mode/artifact dirs and owns verdict/rendering.
- [x] No public ingress / real delivery / Gateway lifecycle operation is implied.
- [x] Caller owns business verdict and rendering.

Acceptance:

- `caller.py` remains a local library-only wrapper over `SupervisorRunner` and `SessionRuntime`.
- `CallerResult` wraps existing supervisor payload/projection and keeps `business_verdict: null`.
- Tests use fake executors and dry-run only; no real external service/platform smoke is required.
- No new CLI, public ingress, real delivery, Gateway lifecycle, automatic reply, live/default-on behavior, `@all`, or agent-to-agent routing.

Status: **Merged on `main` via PR #20 at `83d9cb2` as a generic
local library boundary; main `Verify` CI passed.** Evidence: `src/agent_run_supervisor/caller.py`,
`tests/test_caller.py`, and design/schema docs. Concrete Sachima/Gateway/IM/public-ingress/
delivery/auto-reply behavior remains unapproved and separate; the standing non-approvals (§5)
all hold.

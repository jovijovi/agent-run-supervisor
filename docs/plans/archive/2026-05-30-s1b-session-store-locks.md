---
title: "S1b Session Store and Lock Foundation Plan"
status: archived
created_at: 2026-05-30
last_validated_at: 2026-05-30T00:00:00+0800
archived_at: 2026-07-07
---
> **Archived plan（冷区）：** 非 active 上下文。Roadmap 章节迁移见
> [`docs/roadmap/MIGRATION.md`](../roadmap/MIGRATION.md)。
> 验收摘要见对应 [`docs/roadmap/archive/phases/`](../roadmap/archive/phases/) 条目。

# S1b Session Store and Lock Foundation Plan

> **Scope banner.** S1b implements the local persistent-session **foundation**: role
> session configuration, session artifact store, role/workspace/policy/acpx binding,
> and lease locks. It does **not** launch real persistent `acpx prompt` work and does
> not add the final session CLI/library lifecycle surface.

## Goal

Implement the next slice of `F-SESSION-001` after S1a's contract spike: a local,
auditable session store that can safely hold persistent-session identity and block
unsafe reuse before any future session runtime sends prompts.

S1b turns persistent sessions from pure fixture evidence into a tested local state
foundation. Full persistent-session supervision remains incomplete until later S1
slices connect create/send/status/close/abort runtime behavior to fixture-proven
`acpx@0.10.0` commands.

## Source-of-truth trace

- Product goal: `GOAL.md`.
- Product requirements: `docs/product/prd.md`, especially FR-1 session config,
  FR-3 revalidation, FR-5 persistent session supervision, and FR-8 session artifacts.
- Architecture: `docs/design/architecture.md` §4 persistent session lifecycle and §5
  artifact layout.
- Technical solution: `docs/design/technical-solution.md` §3.1, §3.3, §3.6, §5, §6.
- Feature tracker: `docs/roadmap/features.md`, `F-SESSION-001`.
- Roadmap phase: `docs/roadmap/current-status.md`, `S1 — Persistent session support`.
- Workflow: `docs/AI_FLOW.md`.

## Scope

In scope:

- Extend `AgentRoleSpec.session` so roles can declare `strategy: exec` or
  `strategy: persistent`.
- Keep exec backward-compatible while refusing persistent roles on the one-shot `run`
  path before artifacts or subprocess launch.
- Add deterministic workspace binding via `workspace_hash(...)` over the validated cwd
  decision.
- Add reusable artifact primitives for atomic JSON writes, secure directories, and
  exclusive lock-file creation.
- Add `SessionStore` for session directories and `session.json` records that persist:
  session id, optional `acpx_session_id`/name, role id/hash, workspace hash, policy hash,
  acpx version, adapter agent, effective cwd, matched root, state, and timestamps.
- Add binding validation that refuses role, workspace, policy, acpx-version, or adapter
  drift before mutation.
- Add lease locks in `lock.json` with owner, token, acquired/expiry timestamps;
  non-expired locks block, expired locks are replaced deterministically, and release
  requires the matching token.
- Cover all of the above with focused tests.

Out of scope / not approved:

- Real persistent `acpx prompt`, `sessions new`, `sessions close`, or other runtime
  command launches from the supervisor.
- Final session CLI/library lifecycle commands (`session create/send/status/close/abort`).
- Session parser/event normalization for prompt-turn or management command output.
- Crash/interruption runtime recovery beyond stale lease replacement.
- Retention/cleanup policy.
- Sachima/Hermes behavior integration; real AGENT automatic replies; public ingress;
  real IM delivery; Gateway restart/reload/replace; production config writes;
  live/default-on behavior; worker auto-routing; participant persistence or management UI;
  `@all`; agent-to-agent automatic routing; trusted Markdown/HTML rendering; treating
  `allowed_roots` as an OS/filesystem sandbox; per-run human approval as the default
  authorization model.

## Implementation checklist

- [x] Accept `session.strategy='persistent'` in `AgentRoleSpec` with bounded lease config.
- [x] Reject unknown session config keys and invalid lease values.
- [x] Guard one-shot exec compilation/runner/CLI paths with fail-closed
      `ExecStrategyError` for persistent roles.
- [x] Add `workspace_hash(...)` based on the validated workspace decision, not raw cwd.
- [x] Add public secure artifact helpers in `event_store.py`.
- [x] Add `src/agent_run_supervisor/session.py` with session records, binding validation,
      and lease locks.
- [x] Add focused tests for role session config, exec refusal, workspace hashing,
      session artifacts, mismatch refusal, lock contention, token release, and stale-lock
      replacement.
- [ ] Implement real session create/send/status/close/abort runtime.
- [ ] Add session parser/event coverage for prompt-turn and management command schemas.
- [ ] Add final session CLI/library surface.
- [ ] Add full crash/interruption lifecycle recovery and retention/cleanup knobs.

## Acceptance criteria

- Persistent roles validate but cannot accidentally run through one-shot exec.
- Session directories are mode `0700`; `session.json` and `lock.json` are mode `0600`.
- Session ids reject traversal and unsafe path components.
- Binding validation refuses role, workspace, policy, acpx-version, or adapter drift before
  mutation.
- Lease locks prevent concurrent unsafe mutation, recover expired locks deterministically,
  and require token-matched release.
- S1 remains only **Partial** after this plan: runtime session lifecycle, parser coverage,
  CLI/library commands, close/abort semantics, crash recovery, and cleanup remain open.

## Files

Runtime / library:

- `src/agent_run_supervisor/role.py`
- `src/agent_run_supervisor/policy.py`
- `src/agent_run_supervisor/runner.py`
- `src/agent_run_supervisor/commands.py`
- `src/agent_run_supervisor/event_store.py`
- `src/agent_run_supervisor/workspace.py`
- `src/agent_run_supervisor/session.py`

Tests:

- `tests/test_role.py`
- `tests/test_cli_commands.py`
- `tests/test_event_store.py`
- `tests/test_workspace_gate.py`
- `tests/test_session_strategy_guard.py`
- `tests/test_session_store.py`

Docs:

- `docs/plans/archive/2026-05-30-s1b-session-store-locks.md` (this file)
- `docs/roadmap/current-status.md`
- `docs/roadmap/features.md`
- `docs/design/architecture.md`
- `docs/design/technical-solution.md`
- `docs/INDEX.md`, `docs/lessons/_drift_report.md` (generated)

## Verification gates

```bash
python3 -m pytest -q tests/test_role.py tests/test_event_store.py tests/test_workspace_gate.py tests/test_session_strategy_guard.py tests/test_session_store.py
python3 -m pytest -q
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

Additional pre-PR gates:

- Secret-shaped scan over changed source/docs/tests.
- Static dangerous-pattern scan over added source lines.
- Codex CLI primary read-only review; Hermes evidence arbitration.

## Risks / open questions

- **Runtime lifecycle still open.** S1b stores and guards state but does not prove that the
  supervisor can drive acpx persistent sessions end to end.
- **Race semantics are filesystem-local.** Lease replacement is deterministic for local
  artifact state; later runtime work must test crash/interruption behavior around real
  acpx processes.
- **Session parser split remains future work.** S1a proved prompt NDJSON and management
  JSON differ; S1b does not parse either as runtime session output.
- **No cleanup yet.** Long-lived session directories need retention/cleanup policy in a
  later H1/S1 tail.

## Rollback strategy

All work lives on branch `ai/s1b-session-store-locks-2026-05-30` in an isolated worktree.
Rollback is a branch/worktree discard. S1b does not touch production config, Gateway,
Sachima, public ingress, or any external service state.

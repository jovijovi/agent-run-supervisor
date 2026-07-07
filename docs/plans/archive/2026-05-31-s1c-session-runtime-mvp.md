---
title: "S1c Persistent Session Runtime MVP Plan"
status: archived
created_at: 2026-05-31
last_validated_at: 2026-05-31T00:00:00+0800
archived_at: 2026-07-07
---
> **Archived plan（冷区）：** 非 active 上下文。Roadmap 章节迁移见
> [`docs/roadmap/MIGRATION.md`](../roadmap/MIGRATION.md)。
> 验收摘要见对应 [`docs/roadmap/archive/phases/`](../roadmap/archive/phases/) 条目。

# S1c Persistent Session Runtime MVP Plan

> **Scope banner.** S1c connects S1a's fixture-proven `acpx@0.10.0` persistent-session
> command contract to S1b's local session store/lock foundation. It implements a local
> CLI/library MVP for creating/opening sessions, sending/resuming prompt turns, and reading
> session status/show data. It does **not** approve Sachima/Hermes/Gateway integration,
> public ingress, real IM delivery, production config writes, automatic replies, or
> agent-to-agent routing.

## Goal

Implement the first behavior-bearing persistent-session runtime slice for
`F-SESSION-001`: callers can create/open a local role-bound persistent session, send a
prompt turn through fixture-shaped acpx session commands, persist redacted turn artifacts,
and query basic session status/show data while revalidating the S1b binding and lease lock
on every mutation.

S1c should make persistent sessions usable from the local supervisor surface, but S1 will
remain **Partial** until close/abort semantics, full crash/interruption recovery, retention
cleanup, and any remaining lifecycle tails land in later slices.

## Source-of-truth trace

- Product goal: `GOAL.md`.
- Product requirements: `docs/product/prd.md` FR-2, FR-3, FR-5, FR-6, FR-7, FR-8, FR-9.
- Architecture: `docs/design/architecture.md` §4 persistent session lifecycle, §5 artifact/redaction model, §6 boundaries.
- Technical solution: `docs/design/technical-solution.md` §3.2, §3.3, §3.6, §3.7, §3.9, §3.10, §5.
- Feature tracker: `docs/roadmap/features.md`, especially `F-SESSION-001`, `F-POLICY-001`, `F-PARSER-001`, `F-CLI-001/003`.
- Roadmap phase: `docs/roadmap/current-status.md`, `S1 — Persistent session support`.
- Workflow: `docs/AI_FLOW.md`.
- Foundation evidence: S1a fixtures under `fixtures/acpx-0.10.0/session-*`; S1b `src/agent_run_supervisor/session.py` and `tests/test_session_store.py`.

## Scope

In scope:

- Add persistent-session command compilation for fixture-proven acpx session operations:
  - create/open/ensure named session;
  - send/continue prompt turn against an existing acpx session id/name;
  - show/status management query.
- Add a session runtime/library surface that:
  - validates role and workspace before artifacts or subprocess launch;
  - uses `SessionStore.create_session`, `open_session`, `validate_binding`, `acquire_lock`, and `release_lock`;
  - persists `acpx_session_id`/session name returned by management fixtures;
  - stores redacted turn artifacts under `sessions/<session_id>/turns/<turn_id>/`;
  - parses prompt-turn NDJSON with fixture-proven parser behavior;
  - parses management-command JSON responses into safe summaries.
- Add CLI/API session MVP commands, expected shape:
  - `agent-run-supervisor session create --role <role-file> --session-id <id> [--session-name <name>] [--cwd <dir>]`;
  - `agent-run-supervisor session send --role <role-file> --session-id <id> --prompt-file <file> [--cwd <dir>]`;
  - `agent-run-supervisor session status --role <role-file> --session-id <id> [--cwd <dir>]` or equivalent `show/status` naming if fixtures demand it.
- Add fake-executor/TDD tests for command compilation, binding refusal, lock release on success/failure, artifact redaction, CLI smoke, and fixture parser coverage.
- Update PRD/design/roadmap/features/docs index/drift after implementation.

Out of scope / not approved in S1c:

- Sachima/Hermes/Gateway/IM integration, public ingress, production config writes, real delivery, automatic real replies, `@all`, or agent-to-agent routing.
- Treating `allowed_roots` as an OS/filesystem sandbox.
- Per-run human approval as the default authorization model.
- Retention/cleanup knobs and long-lived storage pruning.
- Complex UI/management dashboard.
- Full close/abort semantics unless required as a tiny helper to keep session state coherent; if included, it must be explicitly limited and tested, not a production integration.
- Real smoke that requires network/package fetch or secrets; if `acpx` is unavailable locally, fake/fixture tests are the acceptance source for this slice.

## Implementation checklist

- [ ] Add failing tests first for session command compilation and CLI/API MVP behavior.
- [ ] Add persistent-session argv compiler helpers without weakening exec fail-closed behavior.
- [ ] Add session runtime orchestration around S1b store/binding/lease primitives.
- [ ] Persist management responses and prompt-turn artifacts with 0700/0600 permissions and redaction.
- [ ] Parse management JSON safely; never trust raw output beyond fixture-proven summaries.
- [ ] Keep `business_verdict = null` and supervisor status separate from caller success.
- [ ] Add CLI `session` subcommands and JSON outputs for caller automation.
- [ ] Update docs/roadmap/features/current-status after code/tests are green.
- [ ] Run local gates, secret/static scans, Codex primary review, Claude auxiliary review, PR/CI/merge.

## Acceptance criteria

- Persistent roles can create/open a local session record backed by a fixture-shaped acpx management command.
- Sending a turn revalidates role/workspace/policy/acpx/adapter binding, acquires a lease, persists redacted turn artifacts, parses prompt-turn NDJSON, and releases the lease.
- Status/show query returns safe JSON summaries and refuses binding mismatches.
- Existing exec/dry-run behavior remains unchanged and persistent roles still fail closed on one-shot `run`.
- Tests prove lock release on success and failure, mismatch refusal before mutation, no shell interpolation, redaction of prompt/stdout/stderr/final message, and CLI JSON shape.
- S1 remains Partial unless close/abort/crash/retention tails are fully completed and explicitly documented.

## Files likely to change

Runtime / library:

- `src/agent_run_supervisor/policy.py`
- `src/agent_run_supervisor/session.py`
- `src/agent_run_supervisor/commands.py`
- `src/agent_run_supervisor/cli.py`
- `src/agent_run_supervisor/parser.py`
- `src/agent_run_supervisor/result.py` if a session-specific payload helper is needed

Tests:

- `tests/test_policy.py`
- `tests/test_session_store.py`
- `tests/test_session_runtime.py` (new)
- `tests/test_cli_commands.py`
- `tests/test_parser.py`

Docs:

- `docs/plans/archive/2026-05-31-s1c-session-runtime-mvp.md` (this file)
- `docs/product/prd.md`
- `docs/design/architecture.md`
- `docs/design/technical-solution.md`
- `docs/roadmap/current-status.md`
- `docs/roadmap/features.md`
- `docs/INDEX.md`, `docs/lessons/_drift_report.md` (generated)

## Verification gates

```bash
python3 -m pytest -q tests/test_policy.py tests/test_parser.py tests/test_session_store.py tests/test_session_runtime.py tests/test_cli_commands.py
python3 -m pytest -q
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

Additional gates:

- Secret-shaped scan over changed files.
- Static dangerous-pattern scan over new subprocess/network/config-write surfaces.
- Codex CLI primary review; Claude Code auxiliary review; Hermes arbitration.

## Risks / open questions

- **acpx binary availability.** Current environment may not have `acpx` on PATH. S1c acceptance can rely on fake-executor and fixture tests; a real smoke is optional only when local tooling is available without secrets or production effects.
- **Management JSON vs prompt NDJSON split.** S1a proved these surfaces differ. Keep separate parsers/summarizers instead of pretending every session command is NDJSON.
- **State update safety.** If acpx create/send succeeds but artifact persistence fails, fail closed and preserve local evidence; later crash recovery can improve this.
- **Close/abort semantics.** Keep for a later S1 slice unless the implementation needs a tiny, tested status mutation to avoid dangling state.

## Rollback strategy

All work lives on branch `ai/s1c-session-runtime-mvp-2026-05-31` in an isolated worktree.
Rollback is branch/worktree discard before merge. After merge, revert the PR. S1c must not touch
production config, Gateway, Sachima, public ingress, or external service state.

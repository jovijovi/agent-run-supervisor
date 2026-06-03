---
title: "S1d Persistent Session Lifecycle Completion Plan"
status: archived
created_at: 2026-05-31
last_validated_at: 2026-05-31T00:00:00+0800
archived_at: 2026-06-03T18:14:07+0800
---
# S1d Persistent Session Lifecycle Completion Plan

> **Scope banner.** S1d continues `S1 — Persistent session support` after S1a/S1b/S1c. It closes the next local lifecycle tails for persistent sessions: fixture-proven close, abort/cancel, list/history/read-tail style management surfaces, local state transitions, CLI/library commands, redacted management evidence, and lifecycle tests. It stays strictly local CLI/library. It does **not** approve Sachima/Hermes/Gateway integration, public ingress, real IM delivery, production config writes, automatic replies, `@all`, or agent-to-agent routing.

## Goal

Implement the next behavior-bearing persistent-session lifecycle slice for `F-SESSION-001` so callers can manage local persistent sessions beyond create/send/status:

- close an open local session using the fixture-proven `sessions close <name>` command;
- abort/cancel a session prompt safely using the fixture-proven `cancel -s <name>` management command;
- list local supervisor session records without launching an AGENT;
- read safe management summaries for fixture-proven history/read-tail/show surfaces when needed for lifecycle evidence;
- persist redacted lifecycle management artifacts and update local session state deterministically.

S1d should make lifecycle management coherent for local callers, but S1 remains **Partial** unless real-acpx smoke, full crash/interruption recovery, retention/cleanup, and any remaining long-lived-use hardening are completed and documented.

## Source-of-truth trace

- Product goal: `GOAL.md`.
- Product requirements: `docs/product/prd.md` FR-2, FR-3, FR-5, FR-6, FR-7, FR-8, FR-9.
- Architecture: `docs/design/architecture.md` §4 persistent session lifecycle/control points, §5 artifact/redaction model, §6 boundaries.
- Technical solution: `docs/design/technical-solution.md` §3.2, §3.3, §3.6, §3.7, §3.9, §3.10, §5.
- Feature tracker: `docs/roadmap/features.md`, especially `F-SESSION-001`, `F-POLICY-001`, `F-PARSER-001`, `F-STATUS-001`, `F-STORE-001`, `F-CLI-003`.
- Roadmap phase: `docs/roadmap/current-status.md`, `S1 — Persistent session support` and tail register `ARS-SESSIONS`.
- Workflow: `docs/AI_FLOW.md`.
- Contract evidence: S1a fixtures under `fixtures/acpx-0.10.0/session-*`, especially `session-close-named`, `session-cancel-no-active`, `session-history-after-turns`, `session-read-tail-after-turns`, `session-show-open`, `session-show-closed`, and `session-status-after-turns`.
- Foundation/runtime evidence: S1b `src/agent_run_supervisor/session.py`; S1c `src/agent_run_supervisor/session_runtime.py`, session command compilers, management summarizer, CLI `session create|send|status`.

## Scope

In scope:

- Add fixture-proven command compilers for:
  - `sessions close <session_name>`;
  - `cancel -s <session_name>`;
  - local-safe session query helpers if needed: `sessions show`, `sessions history --limit`, `sessions read --tail`.
- Extend `SessionStore` with deterministic local lifecycle state updates:
  - mark open sessions `closed` after successful close;
  - preserve binding checks before close/abort/read operations;
  - refuse mutation of already-closed sessions where sending/abort would be unsafe;
  - expose local record listing without shelling out or parsing untrusted raw streams.
- Extend `SessionRuntime` with local lifecycle APIs:
  - `close(...)` with management artifact `management/close.json` and state transition;
  - `abort(...)` with management artifact `management/abort.json` (or `cancel.json`) and explicit result semantics;
  - `list_sessions(...)` over local session records only;
  - optional `history(...)` / `read_tail(...)` safe summaries only if useful and fixture-backed.
- Extend CLI surface:
  - `agent-run-supervisor session close --role <role-file> --session-id <id> [--cwd <dir>]`;
  - `agent-run-supervisor session abort --role <role-file> --session-id <id> [--cwd <dir>]`;
  - `agent-run-supervisor session list [--sessions-dir <dir>] [--role <role-file>]` where list is local and read-only;
  - optional `history` / `read-tail` commands only if implementation remains small, local, and fixture-proven.
- Add TDD coverage for policy command compilation, runtime state transitions, closed-session refusal, lock release/safety, CLI JSON shape, redaction, safe management summaries, and local list behavior.
- Update PRD/design/roadmap/features/docs index/drift after code/tests are green.

Out of scope / not approved in S1d:

- Sachima/Hermes/Gateway/IM integration, public ingress, production config writes, real delivery, automatic real replies, `@all`, or agent-to-agent routing.
- Treating `allowed_roots` as an OS/filesystem sandbox.
- Per-run human approval as the default authorization model.
- Retention/cleanup deletion knobs; list may enumerate local records, but cleanup remains H1/retention work.
- UI/dashboard or participant management.
- New network/package-fetching real smoke unless explicitly safe in the local environment and separately reported as smoke evidence.
- Trusting unbounded raw management payloads; summaries must stay allow-listed/redacted.

## Implementation checklist

- [ ] Add failing tests first for close/abort/list command compilers and CLI/runtime behaviors.
- [ ] Add session lifecycle command compilers without weakening exec fail-closed or persistent-strategy guards.
- [ ] Add local session state update helpers with atomic writes and binding validation before mutation.
- [ ] Add `SessionRuntime.close`, `SessionRuntime.abort`, and local `list_sessions` over S1b store records.
- [ ] Persist redacted lifecycle management artifacts with restrictive permissions.
- [ ] Refuse unsafe operations on closed sessions (`send` and `abort` should fail closed; `status/show/list` remain safe reads).
- [ ] Keep `business_verdict = null` and supervisor status separate from caller success.
- [ ] Add CLI subcommands and stable JSON stdout/exit semantics.
- [ ] Update docs/roadmap/features/current-status/PRD/design after code/tests are green.
- [ ] Run local gates, secret/static scans, Codex primary review, Claude auxiliary review, PR/CI/merge.

## Acceptance criteria

- `session close` revalidates role/workspace/policy/acpx/adapter binding, runs fixture-shaped management close, writes redacted `management/close.json`, atomically marks the local record closed, and returns stable JSON with `business_verdict: null`.
- `session abort`/`cancel` revalidates binding, uses the fixture-proven `cancel -s` shape, writes redacted management evidence, and returns stable JSON that distinguishes `cancelled: true` vs `cancelled: false` without pretending cancelled work is a business verdict.
- `session list` is local/read-only, does not launch acpx/AGENT work, and returns redacted, minimal session records; optional role filtering validates role-bound ownership if provided.
- Sending to a closed session fails closed before subprocess launch or turn artifact mutation.
- Existing `session create|send|status`, one-shot exec, dry-run, doctor, and replay behavior remains unchanged.
- Tests cover close success, close parse/error refusal, abort/cancel success/no-active semantics, local list, closed-session refusal, close-vs-send/close-vs-abort stale-open race regression, binding mismatch refusal before mutation, no shell interpolation, redaction, and CLI JSON/exit codes.
- S1 remains Partial unless remaining tails (real-acpx smoke, crash/interruption recovery, retention/cleanup, and long-lived hardening) are explicitly completed and documented.

## Files likely to change

Runtime / library:

- `src/agent_run_supervisor/policy.py`
- `src/agent_run_supervisor/session.py`
- `src/agent_run_supervisor/session_runtime.py`
- `src/agent_run_supervisor/parser.py` if management summaries need extra allow-listed lifecycle fields
- `src/agent_run_supervisor/commands.py`
- `src/agent_run_supervisor/cli.py`

Tests:

- `tests/test_policy.py`
- `tests/test_session_store.py`
- `tests/test_session_runtime.py`
- `tests/test_cli_commands.py`
- `tests/test_parser.py`

Docs:

- `docs/plans/2026-05-31-s1d-session-lifecycle-completion.md` (this file)
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

- **Close vs abort semantics.** `sessions close` produces `session_closed`; `cancel -s` may return `cancelled: false` when no active request exists. The supervisor must report this honestly and avoid laundering it into a business success/failure verdict.
- **Closed-session mutation.** Once local state is closed, turn mutation should fail before subprocess/artifact work. If acpx says closed but local state is still open due to a partial failure, fail closed and preserve management evidence for diagnosis.
- **List scope.** S1d list should be local supervisor records, not global acpx session discovery, unless a later fixture proves a bounded safe global list surface.
- **Real smoke.** Current acceptance can rely on fake-executor and fixtures. A real local acpx smoke is useful only if it can run without secrets, production state, or Gateway/Sachima coupling.
- **Crash recovery.** S1d improves lifecycle coherence but does not claim full crash/interruption recovery; that remains an explicit tail unless implemented and tested separately.

## Rollback strategy

All work lives on branch `ai/s1d-session-lifecycle-completion-2026-05-31` in isolated worktree `/home/ecs-user/workspace/hermes/worktrees/agent-run-supervisor/s1d-session-lifecycle-completion-2026-05-31`. Rollback is branch/worktree discard before merge. After merge, revert the PR. S1d must not touch production config, Gateway, Sachima, public ingress, or external service state.

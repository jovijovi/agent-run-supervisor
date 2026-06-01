---
title: "I1 Local Caller Thin Integration Plan"
status: active
created_at: 2026-06-01
last_validated_at: 2026-06-01T00:00:00+0800
---
# I1 Local Caller Thin Integration Plan

> **Scope banner.** This is the I1 design and implementation plan for a generic,
> local-only caller boundary in `agent-run-supervisor`. It does not implement code.
> I1 is not Sachima integration, not Gateway integration, not public ingress, not
> real IM delivery, not automatic replies, and not live/default-on behavior.

## 1. Product Position

Current position from the required authority chain:

- **E1** one-shot local exec supervision is merged and closed on `main` via PR #8.
- **S1** persistent sessions are merged and closed for the local lifecycle.
- **H1** operational hardening is merged on `main` via PR #19 at `484ae23`
  (`feat: harden local operational tooling (#19)`). User-provided status says PR #19 main
  CI passed. Local git evidence also shows `origin/main` and `main` at `484ae23`.
- **I1** is next, but only as a **local caller integration boundary** plus controlled local
  validation. It must keep the supervisor independent and generic.

Open tails and parked boundaries remain unchanged:

- `ARS-CRASH-RECOVERY` stays carried.
- `ARS-SANDBOX-BOUNDARY` stays parked.
- `ARS-CALLER-INTEGRATION` moves only far enough to define a generic local caller boundary;
  any concrete platform behavior still needs separate approval.

This task is allowed by the roadmap because `current-status.md` names I1 as next design-only
thin local integration work. This plan must not redefine product goals.

## 2. Exact Scope

In scope for the future I1 implementation PR:

- A generic library-level caller boundary that lets a local caller choose:
  - role source: `AgentRoleSpec` object or role file path;
  - prompt/context text owned by the caller;
  - effective `cwd`;
  - execution mode: one-shot `exec` or session `create`, `send`, `status`, `close`;
  - run/session artifact directories.
- A normalized local caller result/report that wraps existing supervisor outputs.
- Delegation to existing library surfaces: `SupervisorRunner` for exec and
  `SessionRuntime` for sessions.
- Fake-executor and local dry-run tests only. Real acpx smoke is not part of I1 validation.
- Documentation corrections that update stale H1 "branch/not yet merged" wording to PR #19
  merged evidence.

Explicitly not approved:

- Sachima behavior, Gateway behavior, IM adapters, public ingress, real delivery, automatic
  replies, service restart/reload/replace, production config writes, live/default-on behavior,
  `@all`, worker auto-routing, agent-to-agent routing, trusted Markdown/HTML rendering, or
  treating `allowed_roots` as an OS/filesystem sandbox.
- Any platform-specific fields such as channel id, webhook id, message id, delivery state,
  external recipient, or Gateway lifecycle state in supervisor results.
- Parsing raw ACP/acpx streams in the I1 adapter. Stream parsing stays inside
  `SupervisorRunner`, `SessionRuntime`, and existing parser code.
- A new CLI surface. I1 stays library + examples/tests to avoid implying ingress, auto-reply,
  or public automation.

## 3. Proposed Library Shape

Create a small module such as `src/agent_run_supervisor/caller.py`.

Conceptual types:

- `CallerInvocationSpec`
  - `role: AgentRoleSpec | None`
  - `role_file: Path | None`
  - `prompt: str | None`
  - `context: str | None`
  - `cwd: str | None`
  - `mode: "exec" | "exec_dry_run" | "session_create" | "session_send" | "session_status" | "session_close"`
  - `runs_dir: Path | None`
  - `sessions_dir: Path | None`
  - `session_id: str | None`
  - `session_name: str | None`

- `CallerResult`
  - `mode: str`
  - `supervisor_status: str | None`
  - `result: dict[str, Any]`
  - `artifact_dir: str | None`
  - `run_dir: str | None`
  - `session_dir: str | None`
  - `business_verdict: None`

Design rules:

- Exactly one role source is accepted: `role` or `role_file`.
- `prompt`/`context` are caller-owned content. The helper may combine them into a prompt
  string, but it must not interpret business success.
- `exec` and `exec_dry_run` call `SupervisorRunner.run(...)` or
  `SupervisorRunner.dry_run(...)`.
- Session modes call `SessionRuntime.create_session(...)`, `send(...)`, `status(...)`, or
  `close(...)`.
- `CallerResult.result` is the existing supervisor payload/projection. It must retain
  `business_verdict: null`; the wrapper also exposes `business_verdict: null`.
- The caller owns business verdict, rendering, progress display, and delivery.
- No delivery/platform fields are added.

## 4. Likely Files To Change

Create:

- `src/agent_run_supervisor/caller.py` - generic local caller spec/result and invoke helper.
- `tests/test_caller.py` - TDD coverage for spec validation, delegation, result shape, and
  non-live validation.
- `examples/local_caller_exec.py` - local dry-run or fake-executor example, no real external
  service.
- `examples/local_caller_session.py` - fake-executor session lifecycle example, no real
  external service.

Modify:

- `src/agent_run_supervisor/__init__.py` - export stable caller types only if the project wants
  them in the top-level package.
- `docs/design/architecture.md` - add the generic local caller boundary without platform
  behavior.
- `docs/design/technical-solution.md` - document `caller.py` responsibilities and delegation.
- `docs/design/result-event-schema.md` - document any additive caller report shape if
  `CallerResult` exposes a JSON projection.
- `docs/product/prd.md` - correct stale H1 branch wording, not product scope.
- `docs/roadmap/features.md` - correct stale H1 branch wording and mark I1 implementation
  evidence only after code lands.
- `docs/roadmap/current-status.md` - correct H1 from "branch/not yet merged" to PR #19 merged
  evidence and keep I1 local-only.
- `docs/INDEX.md` and `docs/lessons/_drift_report.md` - regenerate only in the future
  implementation/docs PR using the repo tools.

Expected no changes:

- `src/agent_run_supervisor/cli.py` and `src/agent_run_supervisor/commands.py`.
- Policy/session/runner internals, except for narrow refactors that tests prove necessary.

## 5. TDD Task Checklist

### Task 1 - Correct H1 Authority Wording

- [ ] Write a docs scan test or use a local grep gate for stale strings:
  `not yet merged`, `pending review/merge`, and `H1 branch` in current authority docs.
- [ ] Update `docs/product/prd.md`, `docs/design/technical-solution.md`,
  `docs/roadmap/features.md`, and `docs/roadmap/current-status.md` to say H1 is merged on
  `main` via PR #19 at `484ae23`, with main CI success per user-provided evidence.
- [ ] Keep historical plan wording in `docs/plans/2026-06-01-h1-operational-hardening.md`
  unless it is actively cited as current state.
- [ ] Run `python tools/build_docs_index.py --write` and
  `python tools/docs_drift_signal.py --write` only during the future implementation/docs PR.

### Task 2 - Define Caller Spec Validation

- [ ] Add failing tests in `tests/test_caller.py` for:
  - exactly one of `role` or `role_file`;
  - prompt required for `exec`, `exec_dry_run`, and `session_send`;
  - `session_id` required for all session modes;
  - `session_name` accepted only for `session_create`;
  - invalid mode fails closed before any runner/runtime call.
- [ ] Implement `CallerInvocationSpec`, `CallerResult`, and validation in
  `src/agent_run_supervisor/caller.py`.
- [ ] Keep validation stdlib-only.

### Task 3 - Exec Delegation

- [ ] Add tests proving `exec_dry_run` delegates to `SupervisorRunner.dry_run(...)` and creates
  only local dry-run artifacts.
- [ ] Add tests proving `exec` can run through a fake `SubprocessExecutor` and returns the
  existing `result.json` shape with `business_verdict: null`.
- [ ] Assert the caller helper does not import or call parser functions directly; the runner
  owns stdout parsing.
- [ ] Implement minimal exec delegation using `SupervisorRunner`.

### Task 4 - Session Delegation

- [ ] Add tests with fake executor outputs from existing fixtures:
  `session-new-named/stdout.json`, `session-prompt-turn1/stdout.ndjson`,
  `session-status-after-turns/stdout.json`, and `session-close-named/stdout.json`.
- [ ] Prove `session_create`, `session_send`, `session_status`, and `session_close` delegate to
  `SessionRuntime`.
- [ ] Prove binding, closed-session refusal, redaction, and `business_verdict: null` behavior
  remain owned by `SessionRuntime`.
- [ ] Implement minimal session delegation using `SessionRuntime`.

### Task 5 - Caller Result Contract

- [ ] Add tests that `CallerResult` contains only local supervisor fields:
  `mode`, `supervisor_status`, `result`, artifact path fields, and `business_verdict`.
- [ ] Add negative tests or static assertions that no delivery/platform fields are present.
- [ ] Document the additive wrapper shape if it is serialized.

### Task 6 - Local Examples And Docs

- [ ] Add examples that are dry-run/fake-executor only and clearly local.
- [ ] Update design docs to show the caller/supervisor split:
  caller chooses role/prompt/context/cwd/mode/artifact dirs and owns verdict/rendering;
  supervisor returns normalized local evidence only.
- [ ] Do not add a CLI command for I1.

## 6. Acceptance Criteria

- The I1 library boundary is generic and does not mention Sachima-specific behavior.
- `CallerInvocationSpec` supports role file/spec, prompt/context, cwd, execution mode, and
  artifact directory choices.
- `CallerResult` wraps existing local supervisor outputs and keeps `business_verdict: null`.
- Caller-owned concerns stay outside the supervisor: business verdict, rendering, delivery,
  platform ids, and progress UX.
- New code delegates to `SupervisorRunner` and `SessionRuntime`; it does not parse raw
  ACP/acpx streams.
- Tests use fake executors and local dry-run only; no real external service, platform, or
  acpx smoke is required for I1.
- No new CLI, daemon, public ingress, automatic reply path, or live/default-on behavior exists.
- H1 docs wording is corrected from branch/not-yet-merged to PR #19 merged evidence.
- Existing runner/session/doctor/cleanup behavior remains unchanged.

## 7. Verification Gates

For the future implementation PR:

```bash
python3 -m pytest -q tests/test_caller.py
python3 -m pytest -q
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

Additional I1-specific gates:

- Grep changed files for banned platform/live terms used as fields or behavior:
  `delivery`, `webhook`, `gateway`, `Sachima`, `@all`, `auto_reply`, `public_ingress`.
  Mentions are allowed only in non-approval prose.
- Confirm no new command is added to `cli.py`.
- Confirm examples/tests do not run real acpx, network services, IM delivery, Gateway actions,
  or platform APIs.

## 8. Risks And Open Questions

- **API size creep:** keep I1 to one generic local module. Do not add platform concepts.
- **Context assembly ownership:** callers own business context. The supervisor may concatenate
  supplied prompt/context text, but it must not understand business contracts.
- **Serialized wrapper stability:** if `CallerResult` gains a JSON projection, document it as
  additive and caller-stable.
- **Session operation set:** I1 includes create/send/status/close only. Abort/list remain
  existing lower-level `SessionRuntime` features unless explicitly added later.
- **H1 wording drift:** several authority docs currently still say H1 branch/not-yet-merged;
  the first implementation task must correct them.

## 9. Rollback Strategy

I1 should be rollback-simple:

- Remove `src/agent_run_supervisor/caller.py`.
- Remove `tests/test_caller.py` and local examples.
- Revert only I1 docs additions and H1 wording corrections if they are part of the same PR.
- No runtime data migration, external service state, Gateway state, platform state, or
  production config rollback should exist because I1 is local library-only.

## 10. Review And PR Process

- This plan is authored by Codex CLI under explicit temporary Architect + Documentation
  Engineer role substitution because Claude Code timed out or may be unavailable.
- Future implementation may be done by Claude Code if available, or Codex under the same
  explicit user substitution.
- Any later Codex review must be done from fresh context and labeled as fresh-context Codex
  review, not by the same context that authored or implemented the change.
- Claude may be used as auxiliary review only if available.
- Hermes remains scope control, verification, evidence, and arbitration.
- PR body must include: source-of-truth docs touched, non-approval boundary statement, local
  validation evidence, no-secret statement, and confirmation that no real delivery/public
  ingress/Gateway/auto-reply behavior was added.

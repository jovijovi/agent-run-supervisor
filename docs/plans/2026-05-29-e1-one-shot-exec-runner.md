---
title: "E1 One-shot Exec Runner Implementation Plan"
status: archived
created_at: 2026-05-29
last_validated_at: 2026-05-29T13:33:48+0800
archived_at: 2026-06-03T18:14:07+0800
---
# E1 One-shot Exec Runner Implementation Plan

> **For Hermes:** execute this plan with strict TDD. Claude Code may be the main implementation/debugging worker; Codex CLI is the primary reviewer; Hermes owns scope, verification, evidence, and arbitration.

## Goal

Implement real local one-shot `acpx exec` supervision under role-bound authorization while preserving the product requirement that persistent sessions remain required future work.

## Source-of-truth trace

- Product goal: `GOAL.md`
- Product requirements: `docs/product/prd.md`, especially FR-2, FR-4, FR-6, FR-7, FR-8, FR-9
- Technical design: `docs/design/technical-solution.md`, especially sections 3.2, 3.5, 3.7, 3.8, 3.9, 6, and 7
- Feature tracker: `docs/roadmap/features.md`, `F-EXEC-001`
- Roadmap phase: `docs/roadmap/current-status.md`, `E1 — One-shot exec runner completion`
- Workflow: `docs/AI_FLOW.md`

## Scope approved by user

Approved request text: “批准开工 E1”.

In scope:

- Local one-shot `acpx exec` subprocess supervision.
- argv-list execution only; no shell interpolation.
- validated cwd / allowed-roots intent gate before process launch.
- controlled environment snapshot and redacted persisted env artifact.
- stdout/stderr capture into run artifacts.
- parser/classifier integration through existing fixture-proven stdout parser.
- outer watchdog with graceful termination and force-kill fallback.
- kill metadata in persisted result/evidence.
- CLI/library `run` path without `--no-real-run` after RED/GREEN fake-subprocess tests pass.
- minimal local scratch-repo acpx smoke after fake-runner gates pass, if the local environment can run it without production/Gateway/Sachima integration.
- roadmap/feature tracker updates with evidence.

Out of scope / still not approved:

- persistent sessions (`S1`).
- Sachima/Hermes behavior integration.
- real AGENT automatic replies.
- public ingress.
- real IM delivery.
- Gateway restart/reload/replace.
- production config writes.
- live/default-on behavior.
- worker auto-routing.
- participant persistence or management UI.
- `@all` fanout.
- agent-to-agent automatic routing.
- trusted Markdown/HTML rendering.
- treating `allowed_roots` as an OS/filesystem sandbox.
- per-run human approval as the default authorization model.

## Architecture

Add a narrow subprocess execution abstraction under `src/agent_run_supervisor/runner.py` or a small adjacent module if the file becomes too large. The abstraction should accept an argv list, effective cwd, timeout/watchdog settings, and environment mapping, then return a structured `SubprocessOutcome` with stdout, stderr, exit code, optional signal, timeout/kill booleans, and kill metadata.

`SupervisorRunner.run(...)` should reuse existing `_prepare_artifacts(...)`, `compile_command(...)`, `compile_permission_policy(...)`, `parse_acpx_stdout_bytes(...)`, `classify_exit(...)`, and `build_result_payload(...)`. Do not create a separate command compiler for real exec.

## Definition of Ready

- R0 documentation authority realignment is merged and closed.
- C0 acpx fixtures and validator exist.
- F0 foundation provides role/policy/workspace/parser/store/dry-run/finalization surfaces.
- E1 acceptance checklist is explicit in `docs/roadmap/current-status.md`.
- This plan records approved scope and non-approvals.
- Work starts from clean `origin/main` in an isolated worktree.

## TDD task list

### Task 1: Add subprocess outcome metadata tests

Objective: make `SubprocessOutcome` capable of representing kill/watchdog metadata without changing existing fake finalization behavior.

Files:

- Modify: `tests/test_runner_dry_run.py`
- Modify: `src/agent_run_supervisor/runner.py`
- Maybe modify: `src/agent_run_supervisor/result.py`

RED tests:

- `test_finalize_outcome_records_watchdog_kill_metadata`
  - create `SubprocessOutcome(exit_code=3, signal=15, stdout=b"", stderr=b"", supervisor_killed=True, supervisor_timed_out=True, kill_reason="watchdog_timeout", kill_signal="SIGTERM", grace_ms=250, process_group_used=True, stdout_closed=True, stderr_closed=True)`
  - assert `result.json` and returned payload include `kill_reason`, `kill_signal`, `grace_ms`, `process_group_used`, `stdout_closed`, `stderr_closed`
  - assert status is `timed_out`, origin is `supervisor`, business verdict is `None`

GREEN implementation:

- Extend `SubprocessOutcome` with optional metadata fields.
- Thread metadata into `build_result_payload(...)` or add a nested `supervisor`/`kill` field in the result payload.
- Keep backward compatibility for existing tests.

### Task 2: Add fake subprocess executor contract tests

Objective: prove runner launches through an injectable executor without shell interpolation and writes normal artifacts.

Files:

- Create or modify: `tests/test_runner_exec.py`
- Modify: `src/agent_run_supervisor/runner.py`

RED tests:

- `test_exec_run_uses_argv_list_without_shell`
  - inject fake executor recording argv, cwd, env, timeout/watchdog values.
  - fake returns fixture success stdout.
  - assert recorded argv is `list[str]`, not string.
  - assert no `shell` flag exists or is `False`.
  - assert cwd is the validated effective cwd.
  - assert status is `completed`.
- `test_exec_run_uses_same_compiled_command_as_dry_run`
  - compare persisted `command.argv.json` from dry-run and exec for the same role/prompt/cwd, allowing only redaction-identical output.

GREEN implementation:

- Add `SupervisorRunner.run(...)` or `execute(...)` method that validates workspace, prepares artifacts, calls injectable executor, then reuses `finalize_outcome`-equivalent logic without creating duplicate run dirs.
- Avoid shell strings entirely.

### Task 3: Capture stdout/stderr and parser/classifier outcomes

Objective: prove real-run pipeline persists stdout/stderr, normalized events, result, and redaction report.

Files:

- Modify: `tests/test_runner_exec.py`
- Modify: `src/agent_run_supervisor/runner.py`

RED tests:

- success fixture stdout produces `completed`, final message from fixture, `business_verdict is None`.
- nonzero exit code `5` produces `permission_denied`.
- malformed stdout with exit `0` produces `protocol_error`.
- stderr containing a secret-shaped value is redacted in `stderr.log` and redaction report records a match.

GREEN implementation:

- Factor existing `finalize_outcome(...)` internals so both fake outcomes and real execution share persistence/parser/classifier code.
- Redact stdout/stderr before persistence.
- Continue preserving raw event path name `acpx-stdout.ndjson`, but redacted content only.

### Task 4: Add watchdog/kill path tests

Objective: prove timeout/interruption/kill behavior is represented deterministically.

Files:

- Modify: `tests/test_runner_exec.py`
- Modify: `src/agent_run_supervisor/runner.py`

RED tests:

- fake executor simulates timeout and returns `supervisor_timed_out=True`; assert `timed_out`, kill metadata, retryable behavior, and persisted evidence.
- fake executor simulates supervisor kill without timeout; assert infrastructure/error classification and metadata.
- if implementing concrete subprocess helper directly, add a tiny local Python child command test that sleeps past watchdog and is terminated; keep it deterministic and local-only.

GREEN implementation:

- Implement outer watchdog around `subprocess.Popen` with `communicate(timeout=...)`.
- Prefer process group handling where supported (`start_new_session=True` on POSIX).
- On timeout, terminate group/process, wait grace, force kill if needed.
- Record `kill_reason`, `kill_signal`, `grace_ms`, `process_group_used`, and stdout/stderr closure/truncation state.

### Task 5: Wire CLI real exec path

Objective: remove the E1 refusal for `run` without `--no-real-run` and route it to the supervised exec runner.

Files:

- Modify: `src/agent_run_supervisor/commands.py`
- Modify: `src/agent_run_supervisor/cli.py`
- Modify: `tests/test_cli_commands.py`
- Modify: `tests/test_cli_smoke.py`

RED tests:

- replace `test_run_without_no_real_run_emits_stable_refusal_and_creates_no_artifacts` with a fake-local executable role that returns fixture stdout and assert real `run` exits `0`, writes artifacts, and `launched_real_agent` is not falsely asserted in normal result.
- keep a test that invalid cwd still fails before artifact creation.
- update help text away from “refuses real agent launch until E1 lands” while preserving local-only scope.

GREEN implementation:

- `cmd_run` loads role/prompt first, validates workspace, then if `--no-real-run` uses dry-run; otherwise calls real exec runner.
- Remove/retire `REAL_RUN_DISABLED` as active behavior, but do not remove non-approval docs.
- Do not add per-run manual approval.

### Task 6: Minimal local smoke and docs update

Objective: prove the implementation works with a local executable and, where environment permits, a scratch local acpx smoke without production/Sachima/Gateway effects.

Files:

- Modify: `docs/roadmap/features.md`
- Modify: `docs/roadmap/current-status.md`
- Maybe modify: `docs/design/technical-solution.md` only if implementation reveals a necessary design correction.
- Regenerate: `docs/INDEX.md`, `docs/lessons/_drift_report.md`

Verification:

- fake local executable CLI smoke in tests.
- optional manual/local smoke with role `acpx_binary` pointing to available local acpx in a scratch repo; if unavailable, record as environment tail rather than faking success.
- Update feature tracker and roadmap with exact evidence.

## Required verification gates

Run before PR:

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

Additional E1 gates:

- focused RED/GREEN evidence for new runner tests.
- added-line secret scan.
- added-line static scan for unsafe subprocess patterns:
  - fail on `shell=True`, `os.system`, `subprocess.*(..., shell=True)`, unreviewed network/server/listener surfaces, production config writes, Gateway/Sachima imports.
  - allow reviewed `subprocess.Popen`/`subprocess.run` in the exec runner and tests only when argv-list and no-shell tests cover it.
- Codex CLI primary review of final diff.
- Claude Code auxiliary review if it did not own the implementation.

## Kill criteria

Stop and report instead of expanding scope if:

- a test or implementation needs public ingress, Gateway/Sachima runtime, production config, real IM delivery, or external network integration;
- runner implementation requires shell interpolation;
- artifacts leak raw prompt/env/stderr/final message secrets;
- cwd gate can be bypassed before process launch;
- fake-subprocess tests cannot cover timeout/kill behavior deterministically;
- local acpx smoke would require credentials or production workspace material.

## Rollback strategy

- Keep all work on branch `ai/e1-one-shot-exec-runner-2026-05-29`.
- If implementation fails, revert E1 code commits and leave only a docs/status note if useful.
- No production config or external service state is touched.
- Runtime evidence artifacts are local-only and must not be committed unless explicitly documented as fixtures.

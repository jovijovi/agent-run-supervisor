---
title: "Feature and Capability Tracker"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T13:44:07+0800
---
# Feature and Capability Tracker

This table is the unified feature/capability completion register for `agent-run-supervisor`. It tracks product features, implementation status, evidence, and next acceptance gates.

Status legend:

- **Done** — implemented and covered by tests/docs evidence.
- **Partial** — meaningful implementation exists, but PRD/design acceptance is incomplete.
- **Planned** — product requirement accepted, implementation not started or not merged.
- **Parked** — intentionally outside current engineering sequence, but not necessarily outside product scope.
- **Non-goal** — explicitly not owned by this product.

| ID | Feature / capability | Product status | Implementation status | Evidence | Remaining acceptance |
|---|---|---|---|---|---|
| F-GOV-001 | Documentation-first authority chain: GOAL -> PRD -> design -> feature tracker -> roadmap/status -> phase plans | Required | Done | `GOAL.md`, `docs/product/prd.md`, `docs/design/technical-solution.md`, this file, `docs/roadmap/current-status.md`, PR #6 (`7dcbe4f`) | Maintain this chain as features/phases change |
| F-ROLE-001 | `AgentRoleSpec` validation and role hash | Required | Done | `src/agent_run_supervisor/role.py`, tests | Extend schema for persistent-session config without breaking role-bound authorization |
| F-POLICY-001 | acpx policy/argv compiler | Required | Partial | `src/agent_run_supervisor/policy.py`, tests, dry-run and real-exec artifacts | Add session command compilation after session fixtures are captured |
| F-WORKSPACE-001 | cwd / allowed-roots intent gate | Required | Partial | `src/agent_run_supervisor/workspace.py`, tests, real-exec prelaunch gate | Re-check role/workspace hash for persistent sessions |
| F-PARSER-001 | Observed acpx event/stdout parser | Required | Partial | `src/agent_run_supervisor/parser.py`, fixtures, replay tests | Add persistent-session event fixtures and parser coverage |
| F-STATUS-001 | Supervisor status and exit classification | Required | Partial | `src/agent_run_supervisor/exit_classifier.py`, tests | Add session lifecycle detail if needed |
| F-STORE-001 | EventStore, artifact permissions, redaction | Required | Partial | `src/agent_run_supervisor/event_store.py`, redaction tests | Add session layout plus retention/cleanup knobs |
| F-CLI-001 | `validate-role` CLI | Required | Done | CLI smoke/tests | Keep stable |
| F-CLI-002 | `replay` CLI | Required | Done | Fixture replay smoke/tests | Keep stable |
| F-CLI-003 | `doctor` CLI | Required | Partial | `src/agent_run_supervisor/preflight.py`, doctor smoke/tests | Adapter/npx/policy/cwd/redaction/session readiness probes |
| F-RUN-001 | `run --no-real-run` compile/artifact preview | Useful support feature | Done | dry-run tests/artifacts | Keep or deprecate only with replacement evidence |
| F-EXEC-001 | Real one-shot acpx exec supervision | Required | Done | `src/agent_run_supervisor/runner.py`, `src/agent_run_supervisor/commands.py`, `tests/test_runner_exec.py`, local smoke `/tmp/agent-run-supervisor-e1-smoke/result.json` | Keep local-only boundary; persistent sessions remain S1 |
| F-SESSION-001 | Persistent session lifecycle | Required | Partial | Product requirement in PRD/design; S1a contract evidence: `fixtures/acpx-0.10.0/session-*`, `fixtures/acpx-0.10.0/session-contract-summary.json`, manifest `session_contract`, `scripts/validate_contract_fixtures.py`, `docs/plans/2026-05-30-s1a-session-contract-spike.md`; S1b foundation: `src/agent_run_supervisor/session.py`, session config/exec guard in `role.py`/`policy.py`/`runner.py`/`commands.py`, workspace binding in `workspace.py`, artifact primitives in `event_store.py`, tests `tests/test_session_store.py`, `tests/test_session_strategy_guard.py` | Real session create/open/send/status/close/abort runtime, session parser/event coverage, final CLI/library lifecycle, close/abort semantics, crash/interruption recovery, retention/cleanup, and end-to-end lifecycle tests |
| F-RETENTION-001 | Artifact retention/cleanup knobs | Required for long-lived use | Planned | Current EventStore only | Cleanup API/CLI, retention tests, no unsafe deletion |
| F-INTEGRATION-001 | Thin caller integration boundary | Future optional | Parked | PRD/design boundary | Separate approval and plan; caller owns business verdict/rendering |
| F-NONGOAL-001 | Public ingress / real IM delivery / Gateway lifecycle / production config / `@all` | Non-goal | Non-goal | PRD non-goals | Requires separate product/project approval outside this product's local supervisor scope |

## Completion roll-up

| Area | Done | Partial | Planned/Parked | Note |
|---|---:|---:|---:|---|
| Governance/docs | 1 | 0 | 0 | Authority realignment complete via PR #6 (`7dcbe4f`). |
| Core role/policy/workspace | 1 | 2 | 0 | Role supports persistent session config; policy/workspace have exec proof plus S1b binding foundation, with runtime session command proof still open. |
| Parser/status/store | 0 | 3 | 1 | Current run surfaces exist; session/retention remain. |
| CLI | 4 | 1 | 1 | validate/replay/dry-run/real exec done; doctor partial; session CLI pending. |
| Execution modes | 1 | 1 | 0 | One-shot exec done; persistent sessions have S1a contract evidence and S1b local store/lock foundation, runtime lifecycle still open. |

## Maintenance rule

Update this file whenever a feature's product requirement, implementation state, or acceptance evidence changes. Do not bury feature completion inside PR bodies, chat logs, or historical dev logs.

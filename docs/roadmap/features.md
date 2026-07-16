---
title: "Feature and Capability Tracker"
status: active
created_at: 2026-05-29
last_validated_at: 2026-07-09T19:00:00+0800
---
# Feature and Capability Tracker

Unified feature/capability register. **Evidence** cells are short pointers only; detail lives in
[`archive/phases/`](archive/phases/), tests, and [`docs/plans/archive/`](../plans/archive/).

Status legend: **Done** · **Partial** · **Planned** · **Parked** · **Non-goal**

| ID | Feature / capability | Product | Impl | Evidence | Remaining |
|---|---|---|---|---|---|
| F-GOV-001 | Doc authority chain | Required | Done | `GOAL.md`, `prd.md`, board, PR #6 | Maintain chain |
| F-ROLE-001 | AgentRoleSpec + role hash | Required | Done | `role.py`, `tests/test_role.py` | Schema maintenance |
| F-POLICY-001 | acpx policy/argv compiler | Required | Partial | `policy.py`, `goal.py`, `tests/test_policy.py`, `tests/test_goal.py` | Read-tail compilers; live permissioned-prompt fixture |
| F-WORKSPACE-001 | cwd / allowed-roots gate | Required | Done | `workspace.py`, `tests/test_workspace_gate.py` | OS sandbox parked |
| F-PARSER-001 | acpx stdout/event parser | Required | Partial | `parser.py`, `tests/test_parser.py` | New schemas only |
| F-STATUS-001 | Status / exit classification | Required | Partial | `exit_classifier.py`, schema §3 (incl. `no_op`) | Session detail if needed |
| F-STORE-001 | EventStore + redaction | Required | Partial | `event_store.py`, `session.py`, retention | Optional raw-capture FR-8 |
| F-CLI-001 | `validate-role` | Required | Done | `commands.py`, CLI tests | Keep stable |
| F-CLI-002 | `replay` | Required | Done | fixtures, CLI tests | Keep stable |
| F-CLI-003 | `doctor` | Required | Done | `preflight.py`, `tests/test_preflight.py` | Read-only probes |
| F-RUN-001 | `run --no-real-run` | Useful | Done | dry-run tests | Keep or replace with evidence |
| F-EXEC-001 | Real acpx exec | Required | Done | `runner.py`, `tests/test_runner_exec.py` | Local-only |
| F-SESSION-001 | Persistent session lifecycle | Required | Done | `session_runtime.py`, S1 archive | Closed local lifecycle |
| F-RETENTION-001 | Retention / cleanup | Required | Done | `retention.py`, `tests/test_retention.py` | K1 crash recovery done |
| F-INTEGRATION-001 | Caller + Hermes (local) | Approved | Done | `caller.py`, `hermes_caller/` | Live platform parked |
| F-SMOKE-001 | Codex/acpx smoke helper | Useful | Done | `scripts/smoke_codex_acpx.py` | Operator-only |
| F-LIVE-STREAM-001 | Live stream core | Required | Done | `live_stream.py`, schema §4.1 | No delivery |
| F-LIVE-EVENTS-001 | Event cursor API | Useful | Done | `hermes_caller/events.py` | PR3 Sachima unapproved |
| F-SESSION-INSPECT-001 | Read-only session inspection API | Useful | Done | `session_inspect.py`, `tests/test_session_inspect.py` | Release + caller pin bump |
| F-MCP-CONFIG-001 | Native role-bound acpx `--mcp-config` | Approved | Done | `mcp_config.py`, `tests/test_mcp_config.py`, policy/session/runtime mcp tests | Branch merge; release notes |
| F-RELEASE-001 | Release engineering | Required | Done | `verify_local.sh`, `release.yml` | See CHANGELOG / PyPI |
| F-NONGOAL-001 | Public ingress / IM / Gateway | Non-goal | Non-goal | PRD §6 | Separate approval |

Evidence archive links: S1 → [`archive/phases/s1-persistent-sessions.md`](archive/phases/s1-persistent-sessions.md);
H1 → [`archive/phases/h1-operational-hardening.md`](archive/phases/h1-operational-hardening.md);
P3 → [`archive/phases/p3-engineering-basics.md`](archive/phases/p3-engineering-basics.md).

## Completion roll-up

| Area | Done | Partial | Parked | Note |
|---|---:|---:|---:|---|
| Governance/docs | 1 | 0 | 0 | R0 closed |
| Core role/policy/workspace | 2 | 1 | 0 | Policy partial for optional compilers |
| Parser/status/store | 1 | 3 | 0 | Retention + sessions merged |
| CLI | 5 | 0 | 0 | Includes session + cleanup |
| Execution modes | 2 | 0 | 0 | Exec + sessions closed |
| Smoke helpers | 1 | 0 | 0 | Operator-run only |
| Live supervision | 3 | 0 | 0 | Local artifacts only; incl. read-only inspection |
| Packaging / release | 1 | 0 | 0 | uv + verify + PyPI workflow |

## Maintenance rule

Update when product requirement, implementation state, or acceptance changes.

- **Evidence column:** max ~120 characters; use paths/tests + one archive/plan link.
- **No** PR narratives, SHAs, or duplicate phase prose — link [`archive/phases/`](archive/phases/).
- Do not bury completion in PR bodies or chat logs.

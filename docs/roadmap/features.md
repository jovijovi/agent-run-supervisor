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
| F-POLICY-001 | acpx policy/argv compiler | Required | Partial | `src/agent_run_supervisor/policy.py`, tests, dry-run and real-exec artifacts; S1c session command compilers (`compile_session_create/ensure/show/status/prompt_command`) and S1d lifecycle compilers (`compile_session_close_command` → `sessions close`, `compile_session_cancel_command` → `cancel -s`) with `tests/test_policy.py` | History/read-tail compilers only if a later slice needs them; broaden prompt-turn tool permissions only with fresh fixtures |
| F-WORKSPACE-001 | cwd / allowed-roots intent gate | Required | Partial | `src/agent_run_supervisor/workspace.py`, tests, real-exec prelaunch gate | Re-check role/workspace hash for persistent sessions |
| F-PARSER-001 | Observed acpx event/stdout parser | Required | Partial | `src/agent_run_supervisor/parser.py`, fixtures, replay tests; S1c prompt-turn NDJSON coverage + `summarize_management_json` safe management summarizer with `tests/test_parser.py`; S1d reuses the existing allow-listed `session_closed`/`cancel_result` summaries (no parser change needed) | Extend coverage only if new management schemas appear |
| F-STATUS-001 | Supervisor status and exit classification | Required | Partial | `src/agent_run_supervisor/exit_classifier.py`, tests; H1 documents the status/error-code vocabulary for caller stability in `docs/design/result-event-schema.md` §3 | Add session lifecycle detail if needed |
| F-STORE-001 | EventStore, artifact permissions, redaction | Required | Partial | `src/agent_run_supervisor/event_store.py`, redaction tests; S1c redacted session turn artifacts (`sessions/<id>/turns/<turn_id>/`) and `management/` evidence via `session_runtime.py`; S1d atomic lifecycle state (`SessionStore.mark_closed`/`ensure_open`/`list_records`) and redacted `management/close.json`/`abort.json` with `tests/test_session_store.py`; H1 retention/cleanup over these artifacts delivered via F-RETENTION-001 (`retention.py` + `cleanup` CLI) and read-only `SessionStore.detect_stale_locks` | Retention/cleanup knobs delivered (F-RETENTION-001); optional unsafe raw-capture opt-in remains a future PRD FR-8 item only if a later phase proves it necessary |
| F-CLI-001 | `validate-role` CLI | Required | Done | CLI smoke/tests | Keep stable |
| F-CLI-002 | `replay` CLI | Required | Done | Fixture replay smoke/tests | Keep stable |
| F-CLI-003 | `doctor` CLI | Required | Done | `src/agent_run_supervisor/preflight.py` (full read-only probe set: `probe_policy`/`probe_workspace`/`probe_redaction`/`probe_npx`/`probe_adapter`/`probe_session_readiness`), `cmd_doctor` wiring in `commands.py`, doctor output documented in `docs/design/result-event-schema.md` §5, `tests/test_preflight.py`, `tests/test_cli_commands.py`; H1 merged on `main` via PR #19 at `484ae23` | Doctor probe set complete; keep read-only (`launched_real_agent: false`) and `ok`-gating CI-safe |
| F-RUN-001 | `run --no-real-run` compile/artifact preview | Useful support feature | Done | dry-run tests/artifacts | Keep or deprecate only with replacement evidence |
| F-EXEC-001 | Real one-shot acpx exec supervision | Required | Done | `src/agent_run_supervisor/runner.py`, `src/agent_run_supervisor/commands.py`, `tests/test_runner_exec.py`, local smoke `/tmp/agent-run-supervisor-e1-smoke/result.json` | Keep local-only boundary; persistent sessions are tracked by F-SESSION-001/S1 |
| F-SESSION-001 | Persistent session lifecycle | Required | Done | Product requirement in PRD/design; S1a contract evidence: `fixtures/acpx-0.10.0/session-*`, `fixtures/acpx-0.10.0/session-contract-summary.json`, manifest `session_contract`, `scripts/validate_contract_fixtures.py`, `docs/plans/2026-05-30-s1a-session-contract-spike.md`; S1b foundation: `src/agent_run_supervisor/session.py`, session config/exec guard in `role.py`/`policy.py`/`runner.py`/`commands.py`, workspace binding in `workspace.py`, artifact primitives in `event_store.py`, tests `tests/test_session_store.py`, `tests/test_session_strategy_guard.py`; **S1c runtime MVP**: `src/agent_run_supervisor/session_runtime.py` (create/send/status over `SessionStore` with binding revalidation, lease acquire/release, redacted turn + management artifacts), session command compilers in `policy.py`, `summarize_management_json` in `parser.py`, `session` CLI in `cli.py`/`commands.py`; **S1d lifecycle completion**: `SessionRuntime.close`/`abort`/`list_sessions` with fixture-proven `sessions close`/`cancel -s` compilers, atomic `closed` state transition + closed-session refusal (`SessionStore.mark_closed`/`ensure_open`/`list_records`), redacted `management/close.json`/`abort.json`, honest `cancelled: true|false`, and `session close|abort|list` CLI; **S1 closure acceptance**: `scripts/smoke_persistent_session.py` real local acpx lifecycle smoke, `tests/test_smoke_persistent_session.py` smoke preflight/leak-prevention coverage, plus `tests/test_session_runtime.py::test_two_sequential_sends_reuse_record_persist_distinct_turns_and_release_lease` multi-turn continuity regression. S1 remains exactly S1a/S1b/S1c/S1d; closure adds evidence, not a new subphase. | Local persistent-session lifecycle closed. H1 retention/cleanup (F-RETENTION-001) and read-only stale-lock detection (`SessionStore.detect_stale_locks`) are merged via PR #19 at `484ae23`; K1 branch implements process-liveness crash/interruption recovery as the `ARS-CRASH-RECOVERY` candidate, pending PR review/merge. |
| F-RETENTION-001 | Artifact retention/cleanup knobs | Required for long-lived use | Done | `src/agent_run_supervisor/retention.py` (`plan_cleanup`/`apply_cleanup`, confined dry-run-first), `cleanup` CLI in `cli.py`/`commands.py`, read-only `SessionStore.detect_stale_locks` in `session.py`, plan/result shape in `docs/design/result-event-schema.md` §7, `tests/test_retention.py`, `tests/test_cli_commands.py`, `tests/test_session_store.py`; H1 merged on `main` via PR #19 at `484ae23` | Artifact-root confinement, symlink-escape refusal, open/live-lock protection, and TOCTOU re-check covered; full process-crash recovery is implemented as the K1 branch candidate (`ARS-CRASH-RECOVERY`) and remains pending PR review/merge |
| F-INTEGRATION-001 | Thin local caller boundary | Approved I1 local-only | Done | `src/agent_run_supervisor/caller.py` (`CallerInvocationSpec`, `CallerResult`, `invoke_caller`), `tests/test_caller.py`, design docs; delegates to `SupervisorRunner` and `SessionRuntime`, keeps `business_verdict: null`, and adds no CLI/platform fields; I1 merged on `main` via PR #20 at `83d9cb2` | Concrete Sachima/Gateway/IM/public-ingress/delivery/auto-reply behavior remains unapproved and separate; caller owns business verdict/rendering |
| F-NONGOAL-001 | Public ingress / real IM delivery / Gateway lifecycle / production config / `@all` | Non-goal | Non-goal | PRD non-goals | Requires separate product/project approval outside this product's local supervisor scope |

## Completion roll-up

| Area | Done | Partial | Planned/Parked | Note |
|---|---:|---:|---:|---|
| Governance/docs | 1 | 0 | 0 | Authority realignment complete via PR #6 (`7dcbe4f`). |
| Core role/policy/workspace | 1 | 2 | 0 | Role supports persistent session config; policy/workspace have exec proof plus S1b binding foundation, S1c session command compilation, and S1d close/cancel compilation. |
| Parser/status/store | 1 | 3 | 0 | Current run surfaces exist; S1c adds prompt-turn/management parsing and session turn artifacts; S1d adds atomic lifecycle state + close/abort evidence; H1 merged confined retention/cleanup over those artifacts (F-RETENTION-001 Done). Parser/status/store classifications unchanged. |
| CLI | 5 | 0 | 0 | validate/replay/dry-run/real exec/doctor done (doctor completed in H1); S1c adds `session create|send|status`; S1d adds `session close|abort|list`; H1 adds the `cleanup` command (dry-run default). |
| Execution modes | 2 | 0 | 0 | One-shot exec done; persistent sessions are closed for the local lifecycle after S1a contract evidence, S1b store/lock foundation, S1c create/send/status runtime, S1d close/abort/list lifecycle, multi-turn continuity regression, and the reproducible real-acpx smoke. H1 delivered doctor and retention/cleanup hardening; K1 branch implements process-liveness crash/interruption recovery as a pending PR candidate. |

## Maintenance rule

Update this file whenever a feature's product requirement, implementation state, or acceptance evidence changes. Do not bury feature completion inside PR bodies, chat logs, or historical dev logs.

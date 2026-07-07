---
title: "Closed tail register"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
---

# Closed tail register

Historical closed tails moved from the living board. Open tails remain in
[`current-status.md`](../current-status.md#open-tails).

## Recently closed tails

| ID | Closed by | Evidence | Result |
|---|---|---|---|
| ARS-CRASH-RECOVERY | PR #22 (`0ad531e`) | Process-liveness crash/interruption recovery beyond deterministic expired-lease replacement: supervisor-owned lease metadata, additive child-subprocess metadata, conservative `alive`/`crashed`/`unknown` holder-set classification, additive read-only detector fields, and opt-in reclamation only for provably-crashed, reclaimable holder sets; supervisor+child locks require both identities to be provably crashed; pending `reclaimable: false` holders remain TTL-only. | Closed; no terminating signal to recorded holders, no prior-holder kill, no live/unknown-holder takeover |
| ARS-DOCTOR-COMPLETE | PR #19 (`484ae23`) | Full read-only doctor probe set in `src/agent_run_supervisor/preflight.py` (`probe_policy/workspace/redaction/npx/adapter/session_readiness`) wired into `cmd_doctor`; `tests/test_preflight.py`, `tests/test_cli_commands.py`; doctor output documented in `docs/design/result-event-schema.md` §5 | Closed; `launched_real_agent: false` preserved |
| ARS-RETENTION-CLEANUP | PR #19 (`484ae23`) | `src/agent_run_supervisor/retention.py` (`plan_cleanup`/`apply_cleanup`, confined dry-run-first), `cleanup` CLI in `cli.py`/`commands.py`, read-only `SessionStore.detect_stale_locks`; `tests/test_retention.py`, `tests/test_cli_commands.py`, `tests/test_session_store.py`; plan/result shape in `docs/design/result-event-schema.md` §7 | Closed; `F-RETENTION-001` Done; full crash recovery was later closed by K1 via PR #22 (`0ad531e`) |
| ARS-EXEC-RUNNER | PR #8 (`21b3393`) | `tests/test_runner_exec.py`, local gates, local smoke `result.json` (`final_message=AGENT_RUN_SUPERVISOR_E1_OK`), `docs/roadmap/features.md` F-EXEC-001 Done | Closed |
| ARS-SESSIONS | S1a-S1d + closure acceptance branch | S1a fixtures, S1b store/lock foundation, S1c create/send/status runtime, S1d close/abort/list lifecycle, `tests/test_session_runtime.py::test_two_sequential_sends_reuse_record_persist_distinct_turns_and_release_lease`, `tests/test_smoke_persistent_session.py`, and `scripts/smoke_persistent_session.py` real local acpx lifecycle smoke | Closed for the local persistent-session lifecycle; H1 carried retention/cleanup and detection-first crash hygiene, and K1 later closed full process-liveness crash recovery |
| ARS-DOC-AUTHORITY | PR #6 (`7dcbe4f`) | docs index/drift, CI `Verify`, post-merge gates | Closed |
| ARS-LEGACY-DOCS | PR #6 (`7dcbe4f`) | retired mixed/stale docs deleted, old plan/dev-log artifacts cleared, stale-reference scan | Closed |

## K1 post-merge evidence

See [`phases/k1-crash-recovery.md`](phases/k1-crash-recovery.md).

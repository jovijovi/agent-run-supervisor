---
title: "H1 — Operational hardening"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: h1-operational-hardening
---

# H1 — Operational hardening

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## H1 — Operational hardening

Goal: close long-lived-use tails.

Checklist:

- [x] Doctor probes adapter availability without launching AGENT work. *(W1: `preflight.probe_adapter` — declared + hostable only, never launches the adapter/agent.)*
- [x] Doctor detects runtime `npx` fetch risk. *(W1: `preflight.probe_npx` — read-only `npx --version`; `fetch_risk` true only when no explicit `acpx_binary`; never runs `npx acpx`.)*
- [x] Doctor checks policy parseability safely. *(W1: `preflight.probe_policy` — pure-local permission-policy compile, asserts `default_action == "deny"`, no subprocess.)*
- [x] Doctor reports role cwd/allowed-roots validation. *(W1: `preflight.probe_workspace` — reuses the workspace intent gate; always reports the not-a-sandbox disclaimer.)*
- [x] Doctor reports redaction probe. *(W1: `preflight.probe_redaction` — synthetic pattern-shaped samples only, asserts `leaked == []`.)*
- [x] Doctor reports session readiness after S1. *(W1/W4: `preflight.probe_session_readiness` — temp-store `0700`/`0600` mode probe + read-only stale-lock detection; no acpx launch.)*
- [x] Retention/cleanup knobs exist for run/session artifacts. *(W2: `retention.plan_cleanup`/`apply_cleanup` + `agent-run-supervisor cleanup` CLI — confined, dry-run-first.)*
- [x] Result/event schema is documented for caller stability. *(W3: `docs/design/result-event-schema.md`, pinned by `tests/test_result_event_schema.py`.)*

Acceptance:

- Doctor tests cover success/failure for each probe. *(`tests/test_preflight.py`, `tests/test_cli_commands.py`.)*
- Retention tests prove safe deletion/listing boundaries. *(`tests/test_retention.py`, `tests/test_cli_commands.py`.)*
- Feature tracker marks hardening tails complete. *(`docs/roadmap/features.md`: `F-CLI-003` Done, `F-RETENTION-001` Done.)*

H1 evidence (merged on `main` via PR #19 at `484ae23`; four workstreams):

- **W1 — Doctor completion (`ARS-DOCTOR-COMPLETE`).** Full read-only probe set in
  `src/agent_run_supervisor/preflight.py` (`probe_policy`, `probe_workspace`,
  `probe_redaction`, `probe_npx`, `probe_adapter`, `probe_session_readiness`) wired into
  `cmd_doctor` (`src/agent_run_supervisor/commands.py`). Every probe is read-only:
  `launched_real_agent` stays `false`; no probe runs `acpx exec`, the adapter, a session
  prompt, or an `npx` fetch. `ok` gates only on pure-local deterministic probes so the no-role
  CI `doctor` still exits `0`; role-dependent probes run only with `--role`. Tests:
  `tests/test_preflight.py`, `tests/test_cli_commands.py`.
- **W2 — Retention/cleanup (`ARS-RETENTION-CLEANUP`, `F-RETENTION-001`).** New
  `src/agent_run_supervisor/retention.py` (`plan_cleanup`/`apply_cleanup`,
  `RetentionPolicy`, `CleanupCandidate`/`CleanupPlan`/`CleanupResult`, `RetentionError`) plus
  the `agent-run-supervisor cleanup` command (`cli.py`, `commands.py`). Dry-run is the default;
  `--apply` deletes **only** planned entries. Hard artifact-root confinement to a resolved
  `.agent-run-supervisor` root, symlink-escape refusal, open/live-locked-session protection,
  and TOCTOU re-checks at apply time. Tests: `tests/test_retention.py`, `tests/test_cli_commands.py`.
- **W3 — Caller-stable schema doc.** `docs/design/result-event-schema.md` documents the
  `result.json` payload, session result projections, statuses/error codes, normalized event
  families, `doctor` output, and the cleanup plan/result shape, with an additive-only
  stability contract (`business_verdict` always `null`). Fidelity test
  `tests/test_result_event_schema.py` pins the documented top-level `result.json` key set
  against `result.build_result_payload`.
- **W4 — Narrow detection-first crash tail.** Read-only
  `SessionStore.detect_stale_locks` (`src/agent_run_supervisor/session.py`) reports expired
  leases and `.tmp-*` debris (takes no lock, removes nothing, signals no process); it is
  surfaced by `probe_session_readiness` and consumed by the cleanup planner, and provably
  expired `lock.json` files are removed only as part of an eligible session dir. Tests:
  `tests/test_session_store.py`. **Carry-over:** full process-liveness crash/interruption
  recovery beyond deterministic expired-lease replacement (no PID inspection, signals, or
  live-session takeover) was deliberately deferred at H1 close; K1 later closed
  `ARS-CRASH-RECOVERY` on `main` via PR #22 (`0ad531e`) with safe
  process-liveness recovery.

H1 introduces no non-approval from §5: no Sachima/Gateway/IM/public-ingress/production-config/
real-delivery/service-restart/live/default-on/automatic-reply behavior; `doctor` never launches
an AGENT and `cleanup` never deletes outside a resolved `.agent-run-supervisor` root.

Status: **Merged on `main` via PR #19 at `484ae23`; user-provided status says main CI passed.**
W1–W4 are complete; `ARS-DOCTOR-COMPLETE` and `ARS-RETENTION-CLEANUP` are closed (see §4).
Full process-liveness crash recovery was later closed by K1 on `main` via PR #22 (`0ad531e`); H1 itself remains the detection-first slice.

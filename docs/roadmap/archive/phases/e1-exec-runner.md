---
title: "E1 — One-shot exec runner completion"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: e1-exec-runner
---

# E1 — One-shot exec runner completion

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## E1 — One-shot exec runner completion

Goal: implement real local acpx exec supervision under role-bound authorization.

Checklist:

- [x] Add subprocess execution abstraction accepting compiled argv, effective cwd, timeout, and environment snapshot.
- [x] Prove no shell interpolation.
- [x] Capture stdout/stderr from subprocess into EventStore.
- [x] Parse stdout with fixture-proven parser.
- [x] Add fake subprocess tests for success, nonzero exits, malformed stdout, permission denied, stderr redaction, interruption, timeout, and kill paths.
- [x] Implement outer watchdog with grace and process-group handling where supported.
- [x] Record kill metadata: `kill_reason`, `kill_signal`, `grace_ms`, `process_group_used`, stdout/stderr truncation/closure state.
- [x] Connect CLI/library `run` without `--no-real-run` to the exec runner after tests are green.
- [x] Preserve `business_verdict: null` and caller-owned business interpretation.
- [x] Run a minimal local acpx smoke in a scratch repo after fake-runner gates pass.
- [x] Update `docs/roadmap/features.md` and this file with evidence.

Acceptance:

- Existing tests continue to pass.
- New fake runner/watchdog tests pass.
- `python3 -m compileall -q src scripts tests` passes.
- Fixture validator passes.
- Doctor/replay smoke passes.
- Secret/static scans show no unsafe runner patterns.
- Local smoke evidence is redacted and does not use production/Gateway/Sachima integration.

Status: **Done — merged and closed on main via PR #8 (`21b3393`); `docs/roadmap/features.md` records F-EXEC-001 as Done. Evidence includes `tests/test_runner_exec.py`, full local gates, and local smoke `/tmp/agent-run-supervisor-e1-smoke/result.json` with `final_message=AGENT_RUN_SUPERVISOR_E1_OK`. Persistent sessions remain S1.**

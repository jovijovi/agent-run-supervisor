---
title: "K1 — Process-liveness crash recovery"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: k1-crash-recovery
---

# K1 — Process-liveness crash recovery

> **Closed phase archive.** Plan:
> [`docs/plans/archive/2026-06-01-k1-crash-recovery-hardening.md`](../../../plans/archive/2026-06-01-k1-crash-recovery-hardening.md).

Status: **Closed on `main` via PR #22.**

## Evidence

| ID | Branch evidence | Result |
|---|---|---|
| ARS-CRASH-RECOVERY | PR #22 (`0ad531e`): `src/agent_run_supervisor/process_liveness.py`, K1 changes in `session.py`/`session_runtime.py`, `tests/test_process_liveness.py`, K1 additions in `tests/test_session_store.py` and `tests/test_session_runtime.py`, and plan `docs/plans/archive/2026-06-01-k1-crash-recovery-hardening.md` | Closed on `main`; evidence passed local gates, PR CI, Codex review, and post-merge verification. |

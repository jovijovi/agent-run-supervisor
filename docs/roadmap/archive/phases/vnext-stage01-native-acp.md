---
title: "vNext Stage 0/1 — Native ACP core through ars-core"
status: archived
created_at: 2026-07-22
archived_at: 2026-07-22
last_validated_at: 2026-07-22
phase_id: vnext-stage01-native-acp
---

# vNext Stage 0/1 — Native ACP core through ars-core

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan: [`docs/plans/archive/2026-07-21-vnext-stage01-native-acp-implementation.md`](../../../plans/archive/2026-07-21-vnext-stage01-native-acp-implementation.md).

Goal: implement the additive vNext Stage 0/1 Native ACP core (slices C1–C10) through ars-core —
frozen `AgentProfile → ResolvedLaunchSpec → immutable AgentRunSpec` admission, supervised
live-stdio `ManagedProcess`, the Native driver/client with exact-or-zero configuration fidelity,
isolated `native-runs/`/`native-sessions/` storage and bounded evidence, fail-closed terminal
state with `unknown/quarantined/retryable=false` and duplicate prevention, default-deny permission
mediation, real `session/load` continuity, and controlled cross-Run model/effort switching —
without `arsd`, service/cgroup enablement, release/publication, or Sachima work. Feature IDs:
F-VNEXT-ADMISSION-001, F-VNEXT-PROCESS-001, F-NATIVE-ACP-001, F-VNEXT-SESSION-001,
F-VNEXT-STATE-001, F-VNEXT-EVIDENCE-001 (F-VNEXT-PERMISSION-001 stays open for the Stage 2 real
denied-action canary).

Gate closure (sanitized C10 acceptance records are operator-held, outside Git):

- G1 explicit operator authorization for C1–C10; G4 fresh-baseline start.
- G5 status-consumer audit with the additive terminal-status decision and per-consumer behavior
  pins (zero acpx coercion).
- G7 live-ACP ownership at L1/L2: supervised spawn/identity/group-termination/reap plus the
  exclusive SDK stdin/stdout wire and single stdout consumer.
- G8 state proofs at L1/L2: finalization table, double dispatch markers, write-once terminal
  facts, quarantine-atomic lease, poisoned-legacy store isolation, and switch rollback.
- G3/G6 full real gate at C10: OpenCode 1.18.4, exact literal `kimi-for-coding/k3` + `max`,
  `loadSession` advertised, historical-token continuity across process-per-Run, and exact
  model/effort switching through the registered second model (profile revision 2 closed pair)
  with proven rollback/quarantine behavior.

Integration: squash-merged to `main` via PR #69 after independent review; the 0.2.0 source
candidate versions the merged core (CHANGELOG 0.2.0 section, synced version metadata).

Non-approvals preserved unchanged (see [`docs/roadmap/non-approvals.md`](../../non-approvals.md)):
Stage 1 evidence is B-grade only — no production acceptance claim, no `arsd`/UDS service, no
service/cgroup enablement or deployment, no caller-UID policy activation, no tag / GitHub
Release / PyPI publication, and no Sachima/Gateway/IM or live/default-on behavior.

Status: **Closed** for the Stage 0/1 Native ACP core implementation — merged into `main` and
carried by the 0.2.0 source line. Stage 2 `arsd` production ingress, its gates (G9–G12), and all
production/release enablement remain open, separately approved work.

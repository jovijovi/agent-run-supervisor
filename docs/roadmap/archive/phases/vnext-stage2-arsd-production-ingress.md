---
title: "vNext Stage 2 — arsd production ingress"
status: archived
created_at: 2026-07-23
archived_at: 2026-07-23
last_validated_at: 2026-07-23
phase_id: vnext-stage2-arsd-production-ingress
---

# vNext Stage 2 — `arsd` production ingress

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan: [`docs/plans/archive/2026-07-22-vnext-stage2-arsd-production-ingress.md`](../../../plans/archive/2026-07-22-vnext-stage2-arsd-production-ingress.md).

Goal: implement and enable the sole vNext production ingress — a thin, unprivileged local
`arsd` Unix-domain-socket daemon over ars-core/Native ACP with `SO_PEERCRED` caller
authentication resolved through a closed zero-default CallerPolicy, durable idempotent
per-key admission, principal-bound Run/Session operations, startup-only reconciliation before
listen, bounded operation, user-service/cgroup crash containment, and real C-grade S1–S5
socket-path acceptance on OpenCode 1.18.4. Feature IDs: F-ARSD-001 (Done) and the
F-VNEXT-PERMISSION-001 real denied-action canary (Done).

Gate/approval closure (all 2026-07-23 unless noted; sanitized records are operator-held,
outside Git):

- **A1** source/default-closed foundation (Slices 1–5 and 6a) merged via PR #72: versioned
  bounded UDS protocol, `SO_PEERCRED` server host with fail-closed zero-mapping startup,
  durable idempotent admission with derived per-key Run identity and write-once
  `submission.json`, idempotent pre-listen reconciliation, typed client and unprivileged
  entrypoint, shipped systemd user-unit renderer, env-gated real acceptance harness.
- **A2/G12** caller policy closed by recorded operator decision; exact
  UID→principal/owner/namespace values are controller-only and never entered the repository;
  the mapping reaches the daemon only as `--caller-mapping` argv in the mode-0600 user unit.
  Closure digest `98d2d7b243d88f814afac5c5b36293917d5043db03778847dcdd681e6f72a04f`.
- **A3** user-service/restart readiness closed (no real Run in A3 scope). Closure digest
  `203971ffb12a5e51a9c0ec8f527398375508acb1e83b103cef5811b36e6f242c`.
- **A4** real OpenCode 1.18.4 S1–S5 socket-path acceptance closed (C-grade) on CPython
  3.12.3, covering G9/G10/G11 including the real denied-action canary (S2) after the PR #74
  permission-mediation repair (`OPENCODE_PERMISSION` ask-binding at spawn plus the
  `PERMISSION_VIOLATION` backstop) merged into `main`. Closure digest
  `0d1e850361a61c89b3bb947436f09324e821ec14607cf77f60459c80716f7086`; evidence tree
  `4b9a7a741ae1681e0c5242c198f6f36b775309fdfff638caa13e20cdbdce7706`.
- **A5** production/default-on enablement closed: exact-main wheel from source commit
  `b7f38eb0553786ec594c78a72ce62f33bdc57ae1` (tree
  `5bc3e13bad9f8ee6a39f9e747d07fd49ed07e5f9`) installed into a commit-keyed non-editable
  venv (CPython 3.12.3, ARS 0.2.0, ACP SDK 0.11.0, pidfd APIs present); renderer-authored
  user unit (mode 0600) installed disabled, verified, manual-start production canary passed
  (real pre-enable S1 exact `kimi-for-coding/k3`+`max` PASS, S2 mediated deny PASS, clean
  restart with no replay PASS), independent fresh-context blocker review PASS, then
  `enable --now`: unit enabled+active, socket dir/socket 0700/0600, cgroup membership
  verified, real post-enable S1 PASS and restart no-replay PASS. Three terminal clean Runs;
  zero workspace writes, zero redaction matches, zero stderr bytes, zero mapping leaks, zero
  sensitive-pattern hits, failed-unit baseline unchanged. Runtime closure digest
  `78a75a2d0d3c8a967d974598bdbb77d138a9ff94dc6fb9654797993d7a5dd6cb`; pre-enable evidence
  tree `11723d37a09211f1219d320c10ced647affe32fbc16fe2b8e86d20d320631e91`; post-enable
  evidence tree `7dcbd7b0ed57ab3c98502fdef8eeb13d0781c451de456a05aba47ba8aa025d7b`.

Outcome: `arsd` is production/default-on enabled as a local user supervision service for
trusted local callers under the closed A2 caller policy, on the CPython 3.12.3 runtime
invariant (standalone 3.11.15 lacks the required pidfd APIs and is not equivalent).

Non-approvals preserved unchanged (see
[`docs/roadmap/non-approvals.md`](../../non-approvals.md)): this closure authorized and
performed no push/PR/merge, no tag / GitHub Release / PyPI publication, no Sachima
`ArsdBackend` or pin change, no Gateway/IM behavior, no public ingress, and no branch
cleanup. Evidence stays sanitized and operator-held outside Git; exact CallerPolicy mapping
and credential values remain controller-only.

Status: **Closed** for Stage 2 `arsd` production ingress — A1–A5 complete and the local
production service enabled 2026-07-23. Later Sachima `ArsdBackend` integration remains
parked, separately approved work.

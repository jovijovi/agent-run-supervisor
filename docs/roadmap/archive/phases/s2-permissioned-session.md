---
title: "S2 — Session permission / goal-turn / no-op hardening"
status: archived
created_at: 2026-07-21
archived_at: 2026-07-21
last_validated_at: 2026-07-21T00:00:00+0800
phase_id: s2-permissioned-session
---

# S2 — Session permission / goal-turn / no-op hardening

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan: [`docs/plans/archive/2026-07-08-permissioned-session-goal-noop.md`](../../../plans/archive/2026-07-08-permissioned-session-goal-noop.md).

## S2 — Session permission / goal-turn / no-op hardening

Goal: fix three supervised-session defects surfaced by the 2026-07-08 operator directive —
persistent prompt turns that could never use tools (hardcoded `--deny-all`), no first-class
validated `/goal` turn compilation, and silent `exit 0` no-op turns misclassified as
`completed` — as an additive, fail-closed hardening slice over the closed local
session/exec lifecycle. Feature IDs: F-POLICY-001, F-STATUS-001, F-SESSION-001
(with the `session send` CLI surface).

Acceptance (derived from the checked boxes of the archived execution plan):

- [x] Persistent prompt turn for a role granting permissions compiles the role-derived
  `--permission-policy` (same compiler as exec), not an unconditional `--deny-all`.
- [x] A role granting no permission kinds still compiles `--deny-all` (fail-closed default
  preserved); `--non-interactive-permissions fail` stays in both shapes.
- [x] A `/goal`-style prompt turn with `exit 0` and no observed agent output / tool events is
  classified `no_op` (fail-closed, `retryable=False`), never `completed`; protocol errors
  keep precedence and non-zero exits are unchanged.
- [x] Goal prompt composition refuses empty / slash-leading / control-poisoned goal text and
  always passes the prompt as a single argv element (no shell).
- [x] Exec runs with `exit 0` and no observed effect also classify `no_op`.
- [x] `role_hash` / `policy_hash` golden tests pinned byte-identical to the installed 0.1.3
  distribution (zero-migration invariant).
- [x] Session turns persist `generated-policy.json` and report `prompt_permission_mode`.
- [x] `derive_verdict` blocks a `completed` verdict when `final_message` is blank.
- [x] `compile_goal_prompt` renders the `goal-contract/v1` text template with the
  `GOAL_STATUS:` anchor; `NATIVE_GOAL_ADAPTERS` starts empty.

Deferred tails carried out of S2 (needing their own fixtures/approvals, not S2 blockers):

- `prompt_kind` invocation parameter + `SLASH_PROMPT_REFUSED` pre-spawn guard.
- `available_commands` capture + `UNSUPPORTED_SLASH_COMMAND` detail (needs a claude capture).
- `NO_OP_COMMAND` / `NO_OP_TURN` detail split.
- Sachima E-3 write-approval slice.
- Live acpx capture for the permissioned `prompt -s` (`--permission-policy`) shape —
  tracked as open tail `ARS-SESSION-PROMPT-POLICY-FIXTURE` (operator follow-up);
  compilation is proven at the flag-family layer by the existing 0.12.0 permission-policy
  and S1a session-prompt fixtures.
- Pin bump deferred to a separate, post-release, operator-authorized step.

Non-approvals preserved unchanged (see [`docs/roadmap/non-approvals.md`](../../non-approvals.md)):
no Sachima behavior integration, no real automatic replies / IM delivery, no public ingress,
no Gateway lifecycle, no production config writes, no live/default-on behavior, no
agent-to-agent auto-routing; no release / tag / PyPI publishing under this slice.

Status: **Closed** for the local supervised-session permission / goal-turn / no-op
hardening — the additive `--permission-policy` session compiler with a fail-closed
`--deny-all` default, validated `/goal` turn composition, the `no_op` fail-closed status
for effect-less `exit 0` turns (exec and session), and the zero-migration golden pins landed
and released in v0.1.7. The deferred tails above and the standing non-approvals were carried
forward, not closed, by this phase.

---
title: "agent-run-supervisor Roadmap Current Status"
status: active
created_at: 2026-05-28
last_validated_at: 2026-07-09T19:00:00+0800
---
# agent-run-supervisor Roadmap Current Status

> Living phase board — **not** merge history.
> Closed acceptance: [`archive/phases/`](archive/phases/) · Features: [`features.md`](features.md)

```text
last_updated: 2026-07-09
base_branch: main
active_plan: docs/plans/active/2026-07-08-permissioned-session-goal-noop.md
```

## How to read this board

Document hierarchy:

```text
PRD -> design -> features.md + this board -> docs/plans/active/ -> code
```

- [`GOAL.md`](../../GOAL.md) — stable product positioning.
- [`docs/product/prd.md`](../product/prd.md) — product requirements.
- [`docs/design/`](../design/) — architecture and technical solution.
- [`features.md`](features.md) — feature/capability completion.
- **This file** — current phase snapshot, open tails, pointers only.
- [`docs/plans/active/`](../plans/active/) — in-progress implementation plans (agent context).
- Closed phase detail: [`archive/phases/`](archive/phases/) · closed plans: [`docs/plans/archive/`](../plans/archive/).
- Path migration: [`MIGRATION.md`](MIGRATION.md).

## Snapshot {#snapshot}

- **Product:** local one-shot exec and local persistent-session lifecycle are implemented and closed for local use.
- **Current engineering stage:** S2 session permission / goal-turn / no-op hardening (operator-approved 2026-07-08, in progress).
- **Caller inspection API:** read-only `inspect_session` / `list_turns` (`session_inspect.py`) implemented with tests — the non-spawning session-liveness surface for external callers (F-SESSION-INSPECT-001); release/pin bump pending.
- **Next allowed implementation:** the S2 plan below; nothing else until approved on this board.
- **Active plan:** [`docs/plans/active/2026-07-08-permissioned-session-goal-noop.md`](../plans/active/2026-07-08-permissioned-session-goal-noop.md).
- **Release pointer:** see README / PyPI badge; this board does not track publish history.

## Phase index {#phase-index}

| Phase | Status | Archive | Feature IDs |
|---|---|---|---|
| R0 Documentation authority | Closed | [`archive/phases/r0-doc-authority.md`](archive/phases/r0-doc-authority.md) | F-GOV-001 |
| C0 acpx fixtures | Closed | [`archive/phases/c0-acpx-fixtures.md`](archive/phases/c0-acpx-fixtures.md) | (contract) |
| F0 Foundation | Closed | [`archive/phases/f0-foundation.md`](archive/phases/f0-foundation.md) | F-ROLE-001 … F-CLI-002 |
| E1 Exec runner | Closed | [`archive/phases/e1-exec-runner.md`](archive/phases/e1-exec-runner.md) | F-EXEC-001 |
| S1 Persistent sessions | Closed | [`archive/phases/s1-persistent-sessions.md`](archive/phases/s1-persistent-sessions.md) | F-SESSION-001 |
| H1 Operational hardening | Closed | [`archive/phases/h1-operational-hardening.md`](archive/phases/h1-operational-hardening.md) | F-CLI-003, F-RETENTION-001 |
| I1 Caller boundary | Closed | [`archive/phases/i1-caller-boundary.md`](archive/phases/i1-caller-boundary.md) | F-INTEGRATION-001 |
| L1 Caller design | Closed | [`archive/phases/l1-caller-design.md`](archive/phases/l1-caller-design.md) | F-INTEGRATION-001 |
| L2 Hermes caller | Closed | [`archive/phases/l2-hermes-caller.md`](archive/phases/l2-hermes-caller.md) | F-INTEGRATION-001 |
| K1 Crash recovery | Closed | [`archive/phases/k1-crash-recovery.md`](archive/phases/k1-crash-recovery.md) | F-SESSION-001, F-RETENTION-001 |
| P3 Engineering basics | Closed | [`archive/phases/p3-engineering-basics.md`](archive/phases/p3-engineering-basics.md) | F-RELEASE-001 |
| Live event streaming | Closed | [`archive/phases/live-event-streaming.md`](archive/phases/live-event-streaming.md) | F-LIVE-STREAM-001, F-LIVE-EVENTS-001 |
| Phase B ARS evidence | Closed | [`archive/phases/phase-b-ars-evidence.md`](archive/phases/phase-b-ars-evidence.md) | (evidence gate) |
| **S2 session permission / goal / no-op hardening** | **In progress** | [`docs/plans/active/2026-07-08-permissioned-session-goal-noop.md`](../plans/active/2026-07-08-permissioned-session-goal-noop.md) | F-POLICY-001, F-STATUS-001, F-SESSION-001 |
| **Backlog — deeper hardening** | **Open (not started)** | — | — |

Backlog items (parked tails only): `npx` strict-offline, redaction/DLP + caller allowlist, lock-release audit trail. Any live/platform integration requires separate approval.

## Open tails {#open-tails}

| ID | Class | Description | Blocks code? | Status |
|---|---|---|---:|---|
| ARS-SANDBOX-BOUNDARY | PARKED | `allowed_roots` is not an OS/filesystem sandbox | No | Parked |
| ARS-CALLER-INTEGRATION | PARTIAL | Generic + local/offline Hermes done; live platform seams unapproved | No | Live behavior parked |
| ARS-NPX-STRICT-OFFLINE | PARKED | Default `npx` fetch path when `acpx_binary` unset | No | Backlog |
| ARS-REDACTION-DLP-HARDENING | PARKED | Stronger DLP / caller allowlist before real user data | No | Backlog |
| ARS-LOCK-RELEASE-AUDIT | PARKED | Lock-release failures not yet structured audit evidence | No | Backlog |
| ARS-SESSION-PROMPT-POLICY-FIXTURE | OPEN | Live acpx capture for the permissioned `prompt -s` (`--permission-policy`) shape | No | Operator follow-up (S2) |

Closed tails: [`archive/tails.md`](archive/tails.md).

## Explicit non-approvals

See [`non-approvals.md`](non-approvals.md) (formerly §5).

## Verification gates

See [`verification.md`](verification.md) and [`scripts/verify_local.sh`](../../scripts/verify_local.sh) (formerly §6).

Review role split: [`docs/AI_FLOW.md`](../AI_FLOW.md).

# agent-run-supervisor — vNext Goal

## Product identity

`agent-run-supervisor` (ARS) is local execution and supervision infrastructure for external ACP AGENTs.
It accepts a caller-authorized, structured Run request and proves whether that Run happened under the
frozen identity, configuration, workspace, permission, process, and evidence constraints.

ARS is not a business orchestrator and never converts process/ACP completion into business success.

## Only production shape for new development

```text
Hermes / FlowWeaver / trusted local CLI
        │  AgentRunRequest + frozen execution_grant
        ▼
arsd — local Unix domain socket; sole production ingress
        ▼
ars-core / RunTask / Native ACP Driver
        ▼
registered external ACP AGENT process
        ▼
model / provider
```

- `arsd` is a thin, unprivileged local service host, not a root daemon, network service, scheduler,
  multi-tenant platform, or second runtime.
- Direct `ars-core` use is test/dev-only. Production fails closed when `arsd` is unavailable.
- There is no durable per-Run Worker. One `arsd` directly owns each in-process `RunTask`, Native ACP
  connection, and external AGENT process tree.
- Native ACP never falls back to acpx. acpx is retained only for released compatibility paths and
  diagnostic/differential reference.

## Authority split

Callers own user intent, business authorization, task decomposition, role/AGENT choice, retries,
approvals, delivery, and business verdicts.

ARS owns caller authentication, approved-resource binding, immutable per-Run execution grants,
AgentProfile resolution, process/ACP lifecycle, Session/Run technical state, permission mediation,
recovery semantics, and redacted evidence. ARS enforces the frozen grant but never widens it and is
not a broad RBAC or policy-decision engine.

External AGENTs own their actual conversation/context state. ARS stores only the minimal SessionBinding
and runtime ledger needed for supervision, recovery, duplicate prevention, progress, configuration,
and result verification.

## vNext load-bearing contracts

1. Resolve and freeze a typed, versioned `AgentProfile` and its launch/config schema; materialize a
   controlled `ResolvedLaunchSpec`; then seal immutable `AgentRunSpec/spec_hash` before spawn.
2. A supervised `ManagedProcess` owns PID/PGID/identity, bounded stderr, timeout, terminate/kill/reap;
   the ACP SDK exclusively owns the live stdin/stdout JSON-RPC wire.
3. v1 uses process-per-Run. Same-Session continuity uses one external session ID and real
   `session/load`; AGENT processes do not survive between Runs.
4. model/effort are immutable per Run, switchable only between completed Runs on the same external
   AGENT Session: load → discovery → set model → rediscovery → set effort → exact readback → prompt.
5. A prompt that may have been dispatched without a trustworthy terminal result ends as
   `Run=unknown`, `Session=quarantined`, `retryable=false`. It is never replayed, resumed, or retried
   automatically; successor work is a separate caller-authorized Run.
6. Permission mediation is default-deny and must be proven by a real denied-action canary. It is
   cooperative-agent policy enforcement, not an OS sandbox.
7. Native state uses isolated `native-runs/` and `native-sessions/` roots. Native code never reads,
   writes, imports, mirrors, or migrates acpx/legacy session storage.
8. Production crash containment uses a user-level service manager/cgroup: an `arsd` crash terminates
   all AGENT descendants; restart performs reconciliation only and never resends a prompt.
9. The first closed profile is OpenCode 1.18.4 with literal `kimi-for-coding/k3` and literal `max`;
   selectors are typed and registered, with no arbitrary command/argv/env/JSON passthrough.

## Released legacy line

v0.1.7 acpx one-shot and persistent-session behavior remains a supported compatibility baseline until
separately retired. It may receive compatibility/security maintenance, but its old requirements,
architecture, plans, and phase vocabulary are archived and **must not direct vNext development**.

The cold snapshot is `docs/archive/pre-vnext-reset-2026-07-21/`. Closed plans and phases remain under
their archive directories. Git history remains the implementation audit trail.

## Current status and authorization

The vNext goal, PRD, architecture, technical solution, roadmap, and Stage 0/1 plan are documentation
authority. They do not claim the target is implemented and do not authorize implementation by their
existence.

Stage 0/1 source/dependency work, Stage 2 `arsd`, caller UID policy, service/cgroup enablement,
release/publication, Sachima integration, and any Gateway/IM/live behavior each require separate,
explicit authorization.

## Non-goals

Public ingress, TCP/root service, distributed scheduling, multi-tenant cloud control plane, broad RBAC,
per-Run Worker, runtime plugin platform, arbitrary launch/config passthrough, acpx fallback, shared or
imported acpx session storage, generalized Session rebind, cross-AGENT Session reuse, automatic replay,
workspace content-digest service, filesystem watcher, hostile-process sandbox claims, and embedding
Feishu/Gateway/business semantics in ARS.

## Development source of truth

New work reads, in order:

1. `GOAL.md`
2. `docs/product/prd.md`
3. `docs/design/architecture.md`
4. `docs/design/technical-solution.md`
5. `docs/roadmap/features.md`
6. `docs/roadmap/current-status.md`
7. `docs/plans/active/`

Archive documents are never default development context.

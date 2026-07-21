---
title: "agent-run-supervisor vNext PRD"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-21
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/prd.md"
---
# agent-run-supervisor vNext PRD

## 1. Product goal

ARS vNext provides one local, auditable, fail-closed supervision plane for external ACP AGENT execution.
A trusted caller submits a structured request that already contains its business decision and a frozen
execution grant. ARS authenticates the caller, binds approved resources, resolves a registered
AgentProfile, supervises one external AGENT Run, and returns redacted technical facts and evidence.

Production execution always follows:

```text
trusted caller → arsd UDS → ars-core / Native ACP → registered external AGENT
```

The released v0.1.7 acpx paths remain a compatibility baseline, not the design basis for new work.
Their former authority is preserved under `docs/archive/pre-vnext-reset-2026-07-21/`.

## 2. Actors and authority

| Actor | Owns | Does not own |
|---|---|---|
| Hermes / FlowWeaver / trusted caller | user intent, business authorization, task graph, AGENT/profile choice, frozen execution grant, retry/approval/delivery/business verdict | process/ACP supervision facts |
| `arsd` / ars-core | caller authentication, resource binding, Run/Session lifecycle, process ownership, ACP state, grant enforcement, evidence, reconciliation | business judgment, Feishu/Gateway semantics, broad RBAC |
| External AGENT | actual conversation/context and task execution | ARS Run/Session authority |
| User-level service manager | daemon/cgroup liveness and crash containment | Run/Session/lease/business state |

A technical `completed` result never means the caller's business task succeeded.

## 3. Product requirements

### R1 — Structured admission and immutable Run identity

- Accept only a versioned `AgentRunRequest`; never accept arbitrary shell text, argv, env, JSON config,
  executable paths, or credential values from callers.
- Authenticate the local caller and bind owner/namespace, workspace, Session, credential references,
  MCP/config snapshots, limits, evidence/recovery policy, and frozen `execution_grant`.
- Resolve a closed, code-registered, versioned `AgentProfile` and config schema.
- Materialize `ResolvedLaunchSpec`, then seal immutable `AgentRunSpec/spec_hash` before spawn.
- Store requested specification and observed effective state separately; observations never rewrite the
  frozen request/profile.

### R2 — Supervised live ACP process

- Native ACP uses a live process surface (`ManagedProcess` or equivalent), not the legacy
  completion-oriented `execute_subprocess`.
- The supervision layer owns spawn, PID/PGID, complete `ProcessIdentity`, bounded stderr, timeout,
  signal escalation, process-group termination, wait, and reap.
- The official ACP client connection exclusively owns stdin/stdout JSON-RPC framing.
- `RunTask` coordinates the process and ACP state machine in `arsd`; no independent per-Run Worker exists.

### R3 — Exact configuration fidelity

Before any prompt, one ACP connection must complete:

```text
initialize / capability discovery
→ session/new or session/load
→ discover current config options
→ set requested model
→ consume the model-dependent option set
→ rediscover effort
→ set requested effort
→ exact requested == effective readback
→ persist EffectiveRunState
→ prompt
```

Missing capability, unadvertised value, alias/coercion, stale option set, failed set, or inexact readback
produces zero Turn and no prompt. Literal `max` must never be downgraded to `high` or another value.

### R4 — Session continuity and between-Run switching

- v1 is process-per-Run; the AGENT process lifetime is contained within one Run.
- One ARS Session binds one external AGENT Session ID, AgentProfile revision/hash, owner/namespace, and
  compatibility resources. The external AGENT remains conversation/context authority.
- Later Runs use real `session/load` on the unchanged external ID; silently creating a new external
  Session is failure.
- model/effort are immutable per Run but may change between completed Runs under the Session lease.
- Partial switching failure sends no prompt. Exact rollback to the previous observed configuration
  reopens the Session; failed or unprovable rollback quarantines it.
- Changing AGENT type requires a new Session plus caller-owned, explicit context handoff.

### R5 — Terminal state, uncertainty, and duplicate prevention

The Native terminal vocabulary includes `completed | failed | cancelled | timed_out | unknown`; all
terminal states are irreversible. Sessions include persistent `active | closed | quarantined`.

Persist two dispatch markers:

```text
prompt-dispatch-started
prompt-accepted
```

If a prompt may have been dispatched but no trustworthy terminal result exists:

```text
Run.status = unknown
Session.status = quarantined
retryable = false
```

No component auto-retries, auto-replays, auto-resumes, or resends that prompt. Successor work is a new,
caller-authorized Run linked by `retry_of_run_id`; it never rewrites the original terminal fact.

### R6 — Caller authentication and resource ownership

- Production ingress is a local Unix socket in a `0700` directory with a `0600` socket; no TCP or root.
- `arsd` authenticates peer credentials with `SO_PEERCRED`, enforces an approved caller UID policy, and
  records owner identity on Runs/Sessions.
- Only the owner may query, stream, cancel, or close its resources.
- Exact UID values and policy ownership are Stage 2 gate G12; documentation does not choose them.

### R7 — Permission mediation and honest security

- The caller freezes `execution_grant`; ARS enforces it default-deny and never widens or refreshes it.
- AgentProfile owns launch/config compatibility, not business authorization.
- Registered ACP permission/filesystem/terminal requests map to deterministic allow/deny decisions and
  redacted mediation evidence. Unknown operations deny by default.
- A real denied-action canary is mandatory for production acceptance; zero permission events prove
  nothing about denial.
- This is cooperative-agent mediation, not an OS sandbox, hostile-process containment, or proof that
  `allowed_roots` restricts filesystem access.

### R8 — Workspace and storage boundaries

- v1 no-change acceptance uses a disposable, known-empty bound workspace and direct pre/post directory
  assertions. `workspace_hash` binds configuration/canonical paths only and is not content integrity.
- ARS v1 does not add a content-digest service, filesystem watcher, or new integrity authority.
- Native data lives only under explicit `native-runs/` and `native-sessions/` roots wired through one
  storage seam. Native paths never read, write, import, migrate, mirror, or collide with legacy/acpx
  stores; same textual IDs may coexist safely across roots.

### R9 — Evidence and runtime ledger

- Persist immutable Spec/launch material without secrets, observed effective state, normalized events,
  bounded/redacted stderr, markers, result, permission evidence, and redaction report.
- One writer owns each Run event stream with monotonic sequence and bounded queue/bytes.
- The ledger supports supervision, recovery, duplicate prevention, progress, config/result proof, and
  audit. It is not a second AGENT conversation database.
- Evidence tiers never substitute for each other:
  - A: pre-implementation compatibility probes — context only;
  - B: Stage 1 direct-drive real-AGENT evidence;
  - C: Stage 2 `arsd` socket-path production acceptance.

### R10 — Crash containment and reconciliation

- `arsd` is the sole production supervision authority and must isolate any Run/connection exception so
  one failure cannot kill the daemon.
- Queues, events, stderr, output, concurrent Runs, per-Session activity, and socket backlog are bounded.
- Production runs under a user-level service manager/cgroup with semantics equivalent to
  `Restart=on-failure` and `KillMode=control-group`; `arsd` and every AGENT descendant share the managed
  cgroup.
- An `arsd` crash kills the entire descendant tree. Restart performs reconciliation only, never prompt
  replay. Graceful `killpg` and crash-time cgroup cleanup are distinct mechanisms.

### R11 — Compatibility and no fallback

- vNext is additive to the released v0.1.7 code until a separate retirement decision.
- Native ACP never calls acpx as driver, compatibility layer, Session store, or fallback.
- Legacy acpx artifacts remain readable by their existing path; Native artifacts are isolated.
- Compatibility maintenance must not reintroduce legacy role/model binding as the vNext product model.

### R12 — First closed profile and implementation language

- ARS remains Python. The Native client pins and verifies the official Python ACP SDK in the consuming
  environment before implementation.
- The first closed profile is OpenCode 1.18.4 with literal `model=kimi-for-coding/k3` and literal
  `effort=max`, registered selectors, fixed executable/argv template, credential slots, and required
  `session/load` capability.
- New profiles are typed, versioned, closed registrations. An Agent-specific adapter is allowed only
  after conformance evidence proves a standard ACP gap; v1 has no runtime plugin system.

## 4. Acceptance and staged delivery

### Stage 0 — dependency/API gate

Verify the consuming environment, exact SDK version/import origin/API, current source symbols, all
status/result/session consumers, and real target-Agent `session/load` capability. Any gap stops the work;
no workaround may silently change the approved architecture.

### Stage 1 — Native ACP through ars-core (B-grade)

Implement the additive Native core: frozen spec/profile/launch, ManagedProcess, Native driver/client,
config fidelity, permission bridge, event writer/normalizer, Native stores, state/quarantine, markers,
Session switching, and `RunTask`. Hermetic fakes cover deterministic faults only.

A real OpenCode 1.18.4 smoke must prove exact K3/max and a real same-Session load/switch/context-continuity
checkpoint. Stage 1 is not production acceptance and contains no `arsd` source or deployment.

### Stage 2 — `arsd` production ingress (C-grade)

Implement UDS protocol/versioning, peer/ownership policy, bounded concurrency, cancellation, startup
reconciliation, graceful shutdown, and service/cgroup containment. Production acceptance requires:

1. real read-only success with exact configuration and empty-workspace pre/post proof;
2. real denied-action mediation canary;
3. same external Session load plus historical-token continuity and model/effort switching;
4. cgroup crash containment yielding `unknown/quarantined/retryable=false` and no redispatch;
5. malformed/failed Run isolation, bounded behavior, and a subsequent successful Run.

Sachima `ArsdBackend` is a later, separately approved integration after ARS production acceptance.

## 5. Current implementation status

- Released compatibility baseline: v0.1.7 acpx one-shot/persistent paths are implemented.
- vNext authority: active in this PRD/design set.
- vNext Stage 0/1 source/dependency implementation: not implemented and not authorized by documentation.
- Stage 2 `arsd`, service/cgroup enablement, release/publication, Sachima integration, and live behavior:
  not implemented and separately authorized.

## 6. Non-goals

Public ingress, root/TCP daemon, distributed or multi-tenant control plane, business orchestration,
Feishu/Gateway semantics, broad RBAC, per-Run Worker, arbitrary command/argv/env/config passthrough,
runtime adapter plugins, acpx fallback, shared/imported acpx sessions, cross-AGENT Session reuse,
general rebind, automatic replay, content-digest service, filesystem watcher, and hostile-process sandbox
claims.

## 7. Authority and archive rule

This PRD is the product requirement authority for new development. Architecture and module design live in
`docs/design/`. Implementation sequencing lives only in the board and `docs/plans/active/`.

Documents under `docs/archive/`, `docs/plans/archive/`, and `docs/roadmap/archive/` are retained history.
They cannot approve work, redefine this PRD, or serve as default agent context.

---
title: "agent-run-supervisor vNext System Architecture"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-21
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/architecture.md"
---
# agent-run-supervisor vNext System Architecture

## 0. Scope and status

This is the system architecture authority for **new ARS development**. It describes the settled vNext
target, not the released v0.1.7 topology and not an implementation claim. The previous mixed document is
preserved at `docs/archive/pre-vnext-reset-2026-07-21/architecture.md` for history only.

Status markers:

- ✅ released compatibility baseline reused unchanged;
- 🟦 required vNext target, not implemented until its separately approved stage lands;
- ⏸ separately approved later integration.

## 1. System context

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Caller/business authority                                            │
│ Hermes / FlowWeaver / trusted CLI                                    │
│ - user intent, task graph, AGENT/profile choice                      │
│ - business approval, frozen execution_grant, retry/delivery/verdict  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ versioned AgentRunRequest
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 🟦 arsd — thin local UDS host; sole production ingress               │
│ - SO_PEERCRED caller authentication and ownership                    │
│ - admission, Run/Session/lease authority, bounded concurrency        │
│ - startup reconciliation, query/events/cancel/session API            │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ in-process RunTask
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 🟦 ars-core / Native ACP vertical                                    │
│ AgentProfile → ResolvedLaunchSpec → AgentRunSpec                     │
│ ManagedProcess + NativeAcpDriver + PermissionBridge + EventWriter     │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ supervised stdio ACP
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ External registered ACP AGENT → model/provider                       │
│ Owns conversation/context; untrusted output/effects                  │
└──────────────────────────────────────────────────────────────────────┘
```

`arsd` is unprivileged, local, single-trust-domain infrastructure. It is not root, TCP/public,
distributed, multi-tenant, or a business scheduler. Direct ars-core is test/dev-only; production has no
in-process or acpx fallback.

## 2. Single supervision authority

No durable per-Run Worker exists. One `arsd` process owns:

- an in-process async `RunTask` per active Run;
- one supervised external AGENT process per Run;
- one Native ACP connection per Run;
- the Run/Session state machine, lease, evidence writer, and finalization decision.

The user-level service manager owns only daemon/cgroup liveness. It never owns Run/Session/lease or
business state.

### Process ownership triangle

| Component | Owns | Must not own |
|---|---|---|
| `ManagedProcess` supervision layer | spawn, PID/PGID, full `ProcessIdentity`, bounded stderr, timeout, SIGTERM→grace→SIGKILL, wait/reap | ACP stdin/stdout protocol |
| ACP SDK connection / `NativeAcpDriver` | live stdin/stdout JSON-RPC wire and ACP state machine | process identity, Run authority, profile selection |
| `RunTask` | admission products, process/driver coordination, markers, events, finalization, Session switching | a second process/runtime layer |

The released `execute_subprocess → SubprocessOutcome` remains ✅ compatibility code for acpx only; its
`stdin=DEVNULL`, stdout-drain threads, and wait-before-return shape cannot carry Native ACP.

## 3. Admission and immutable identity

```text
AgentRunRequest
→ authenticate caller; bind owner/namespace/workspace/Session
→ validate frozen execution_grant and referenced resources
→ resolve closed AgentProfile revision/snapshot/hash + config schema hash
→ materialize ResolvedLaunchSpec
→ seal immutable AgentRunSpec/spec_hash
→ spawn
→ observe EffectiveRunState
→ exact requested/effective comparison
→ prompt
```

`AgentProfile` owns registered launch/config compatibility. `execution_grant` owns per-Run authorization.
`AgentRunSpec` owns immutable requested facts. `EffectiveRunState` owns observations only. No observed
value flows backward into Profile or Spec. No caller-supplied executable, arbitrary argv/env/JSON, or
credential value crosses admission.

## 4. Process-per-Run Session model

Cardinality:

```text
ARS Session 1 ── N Runs (strictly serial under lease)
Run 1 ── 0..1 Turn
Run 1 ── 1 external AGENT process
true parallelism = multiple Sessions
```

Each Run launches a new AGENT process. The first Run uses `session/new`; later Runs use `session/load`
with the same opaque external session ID. The AGENT owns conversation/context storage; ARS stores only
the binding and observed metadata.

Between completed Runs on the same Session, model/effort may change:

```text
previous Run terminal → acquire lease → spawn → initialize
→ session/load(same external ID)
→ discovery → set model → rediscovery → set effort → exact readback
→ persist EffectiveRunState → dispatch markers → prompt
```

model/effort never change during an active Run. Failed partial switching sends no prompt; exact rollback
reopens the Session, otherwise it becomes `quarantined`. Changing AGENT type requires a new Session and
caller-owned explicit context handoff.

## 5. Technical state and uncertainty

Native Run terminal states are irreversible:

```text
completed | failed | cancelled | timed_out | unknown
```

Session states include persistent `active | closed | quarantined`.

Before wire dispatch, `RunTask` exclusively creates `prompt-dispatch-started`; after the write succeeds,
it creates `prompt-accepted`. The conservative uncertainty boundary depends on the first marker:

| Observation | Run | Reusable Session |
|---|---|---|
| no dispatch marker; admission/config/spawn failure | `failed` | yes unless rollback cannot be proven |
| trustworthy ACP terminal event | corresponding terminal result | normally yes |
| dispatch may have occurred; supervisor stayed present and proves abnormal matched-child exit | `failed` | no; quarantine |
| dispatch may have occurred; observation was lost | `unknown`, `retryable=false` | no; quarantine |

An `unknown` Run is never retried, replayed, resumed, or rewritten. Caller-authorized successor work is a
new Run linked by `retry_of_run_id`.

## 6. Crash containment and reconciliation

Production places `arsd` and every external AGENT descendant in one user-managed cgroup with semantics
equivalent to `Restart=on-failure` and `KillMode=control-group`.

```text
arsd crash/SIGKILL
→ service manager kills the entire descendant tree
→ restarted arsd reconciles durable facts
→ uncertain dispatched Runs become unknown/quarantined/retryable=false
→ no prompt redispatch
→ accept later independent Runs after reconciliation
```

Normal cancellation/graceful shutdown uses ACP cancel and process-group escalation. Crash cleanup uses
the external cgroup. These mechanisms are distinct. Full process identity, not PID/name/port guessing,
governs any liveness or orphan decision.

Every RunTask and connection has a top-level exception boundary. Malformed ACP, SDK, normalization,
evidence I/O, and child faults terminate only that Run. Queues, events, stderr, output, concurrency,
Session activity, and socket backlog are bounded.

## 7. Permission and caller boundary

Callers decide and freeze business authorization. ARS authenticates the UDS peer, binds ownership, and
enforces `execution_grant` default-deny without widening or live-policy refresh.

- Registered read operations may be allowed within the bound workspace.
- write/create/delete/terminal/execute/fetch and unknown operations deny unless the frozen grant and
  registered mediation contract explicitly permit them.
- Every mediation decision produces redacted evidence.
- A real denied-action canary is mandatory; zero mediation events prove nothing.
- `allowed_roots`, UDS auth, and ACP mediation are not OS sandboxing or hostile-process containment.

Exact caller UID values and policy ownership remain Stage 2 gate G12.

## 8. Storage and evidence

```text
.agent-run-supervisor/
├── native-runs/<run_id>/
│   ├── spec.json                  # immutable; exclusive create
│   ├── launch.json                # controlled launch; no secret values
│   ├── effective.json             # observed identity/capabilities/config
│   ├── events.jsonl               # single writer; monotonic seq; bounded
│   ├── result.json                # one terminal fact
│   ├── prompt-dispatch-started
│   ├── prompt-accepted
│   └── evidence / redaction / bounded stderr
└── native-sessions/<session_id>/
    ├── session.json               # stable binding + last_effective_* + state
    └── lock.json                  # lease/process identity while held
```

`native_acp/storage.py` is the only constructor seam for Native roots. Legacy `runs/`/`sessions/` and
acpx storage are never read, written, imported, mirrored, or migrated by Native code.

The runtime ledger records supervision facts, not AGENT conversation memory. v1 no-change acceptance uses
a disposable known-empty workspace and direct pre/post directory listing; `workspace_hash` is only a
binding hash. No content-digest service or filesystem watcher is part of ARS.

Evidence grades:

- A — pre-implementation compatibility context;
- B — Stage 1 direct-drive real-AGENT evidence;
- C — Stage 2 production socket-path acceptance.

No lower grade can claim a higher one.

## 9. Deployment stages

| Stage | Target | Evidence | Production claim |
|---|---|---|---|
| 0 | SDK/source/API/consumer/load capability gates | deterministic preflight | none |
| 1 | ManagedProcess + Native ACP core + state/session/permission/evidence | L1/L2 + real OpenCode direct-drive B-grade | none |
| 2 | `arsd` UDS, ownership, reconciliation, cgroup containment | real S1–S5 C-grade | ARS production acceptance |
| later | Sachima `ArsdBackend` | separate integration evidence | separately approved |

Stage 1 is intentionally an intermediate implementation boundary, not a downgrade of the production
target. Production is achieved only after Stage 2 acceptance.

## 10. Legacy coexistence and rollback

The released v0.1.7 acpx paths remain ✅ compatibility surfaces until a separate retirement decision.
They do not define vNext modules, Session semantics, status vocabulary, or production ingress. Native
failure never routes to them.

Rollback disables Native/`arsd` ingress and stops new submissions. It never converts failures into acpx
fallback and never rewrites terminal Run facts.

## 11. Authority map

- Product intent: `GOAL.md`
- Requirements: `docs/product/prd.md`
- Module design: `docs/design/technical-solution.md`
- Compatibility schema: `docs/design/result-event-schema.md`
- Current status/gates: `docs/roadmap/`
- Executable work: `docs/plans/active/`
- Historical-only material: all archive directories

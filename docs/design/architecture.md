---
title: "agent-run-supervisor vNext System Architecture"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-26
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/architecture.md"
---
# agent-run-supervisor vNext System Architecture

## 0. Scope and status

This is the system architecture authority for **new ARS development**. It describes the settled vNext
target, not the released v0.1.7 topology. The previous mixed document is preserved at
`docs/archive/pre-vnext-reset-2026-07-21/architecture.md` for history only.

Status markers:

- ✅ released compatibility baseline reused unchanged;
- 🟦 vNext supervision plane, implemented on `main` (Stage 0/1 and Stage 2 closed);
- 🟨 accepted design whose source work is planned and not implemented — the Runtime Binding layer of
  §3.1–§3.3 plus every 🟨-marked line in §3, §4, §8, §9, and §10;
- ⏸ separately approved later integration.

Marker 🟦 records the settled design, not an approval: per-stage implementation status, gates, and
enablement decisions live in [`docs/roadmap/current-status.md`](../roadmap/current-status.md). Marker
🟨 is weaker still — it is accepted architecture with no source on `main`, carried by the board-linked
active plan, and it approves no deployment, promotion, or rollout of any kind.

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
→ resolve closed AgentProfile revision/snapshot/hash + config schema hash + adapter_contract_hash
→ read the Runtime Binding exactly once (active.json + selected generation) 🟨
→ project only contract-accepted slots; revalidate contract match and artifact digest 🟨
→ materialize ResolvedLaunchSpec incl. complete runtime identity
→ seal immutable AgentRunSpec/spec_hash (launch sealed by launch_spec_hash)
→ spawn
→ observe EffectiveRunState
→ exact requested/effective comparison
→ prompt
```

`AgentProfile` owns registered launch/config compatibility. `execution_grant` owns per-Run authorization.
`AgentRunSpec` owns immutable requested facts. `EffectiveRunState` owns observations only. No observed
value flows backward into Profile, Binding, or Spec. No caller-supplied executable, arbitrary argv/env/
JSON, credential value, runtime path/version/digest, or Binding generation crosses admission.

### 3.1 Runtime authority layers 🟨

```text
LAYER 1 — code-closed AgentProfile / AdapterContract          owner: the registry
  stable profile ID · revision · adapter_contract_hash
  launch_kind: wrapped_acp | direct_acp
  accepted Binding schema + slot projection
  fixed executable/argv construction · code-known env keys only
  ACP protocol/name · required + forbidden capabilities
  permission / config / model / effort / session semantics
  wrapped adapter + interpreter artifact identity
  code-owned safe version-probe rule
        │
        │ accepts (profile_id, revision, adapter_contract_hash)
        ▼
LAYER 2 — operator-owned Runtime Binding generation           owner: the operator
  declared contract identity: profile_id · profile_revision ·
    adapter_contract_hash          ← the only acceptance inputs
  external CLI artifact descriptor: immutable versioned path,
    actual version, digest, complete executable code closure
  optional values for Profile-declared config-root slots
  positive session_compatibility_epoch
  provenance block: created_at, accepted_by, accepted_at,
    acceptance receipt ref/hash   ← recorded and reported, never consulted
        │
        │ read exactly once per Run, at admission
        ▼
LAYER 3 — per-Run sealed ResolvedLaunchSpec + provenance      owner: the Run
  write-once launch.json · launch_spec_hash · never re-read after sealing
```

A Binding never declares a command, argv, env key, adapter, launch kind, capability, permission, or
selector. Every slot binds to the exact profile ID, revision, and `adapter_contract_hash` that accepted
it, so a contract revision fails stale generations closed rather than letting a new source contract
reinterpret operator-authored values.

Acceptance rests on those explicit machine fields plus trusted ownership and digest validation, and on
nothing else. The provenance block is recorded and reported for audit; it never authorizes a generation,
never substitutes for a missing or mismatched machine field, and never becomes part of profile identity.
A generation with a valid acceptance receipt but the wrong declared contract identity is refused.

Read-once is structural, not advisory: `arsd` admission opens the Binding root once per Run, and spawn,
finalization, and reconciliation have no Binding read path at all. Two Runs admitted on either side of a
promotion are each sealed to what they read; an in-flight Run is never re-pointed.

### 3.2 Binding layout, validation, and operator surface 🟨

```text
<binding_root>/                     # operator/root-owned; outside the repository
├── active.json                     # regular file, atomically replaced — never a symlink
└── generations/<generation_id>/
    └── manifest.json               # immutable once written
```

Validation is fail-closed on every read: strict canonical JSON, finite size bound, `O_NOFOLLOW`/dirfd
walks, verified ownership, modes, and full ancestor chain, and refusal of traversal, symlink, FIFO,
device, unknown fields, and unknown slots. There is no active symlink to retarget.

The planned operator command surface is exactly these, and no command beyond them is defined:

```text
agent-run-supervisor runtime-binding validate     # probe-backed check of a generation
agent-run-supervisor runtime-binding promote      # atomically replace active.json
agent-run-supervisor runtime-binding rollback     # re-promote a previously validated generation
agent-run-supervisor runtime-binding inspect-run  # per-Run provenance recomputation
```

No `--force` is defined and no command escalates privilege internally; preparing an immutable artifact root
is an operator action outside ARS. `validate`/`promote` obtain the real external CLI version through the
Profile's code-owned probe and compare it with the Binding — a manifest's version string alone is not
proof. A pure Binding promotion does not restart `arsd`, because admission re-reads the active pointer
per Run; changing the Binding root, the service unit, or the runtime does require a restart and stays
separately approved.

`inspect-run` recomputes the launch hash from the sealed launch record after excluding only the
top-level `launch_spec_hash`, and reports profile/contract identity, adapter/protocol identity, Binding
generation/set/slot hashes, the complete CLI artifact identity/version/digest, and the epoch.

### 3.3 Launch kinds and artifact code closure 🟨

| Launch kind | Source freezes | Binding freezes |
|---|---|---|
| `wrapped_acp` (Codex ACP, Claude Agent ACP) | interpreter/Node identity, ACP adapter artifact identity, argv construction, env keys, protocol/capability contract | downstream CLI artifact identity/version/digest, config-root slot values |
| `direct_acp` (OpenCode) | direct launch, protocol, and capability semantics | that one executable's identity/version/digest |

OpenCode is one artifact, not two: the same executable is the AGENT CLI and the ACP implementation, and
the documentation must not pretend otherwise.

Artifact identity must cover the complete executable code closure:

- **Standalone native binary** — regular-file SHA-256, plus the interpreter/dynamic-loader policy where
  one applies.
- **Package or launcher CLI** — an immutable package root/tree or canonical manifest digest, the
  launcher identity, and the required interpreter/runtime identity. A launcher-file hash alone never
  freezes the sibling code the launcher loads.
- The artifact and every path ancestor are operator- or root-owned and non-writable by the `arsd`/AGENT
  UID.

A `direct_acp` executable is pinned by descriptor and exec'd from that descriptor, with TOCTOU rechecks
on both sides of the spawn window. A wrapped downstream CLI is reopened later by the adapter, which ARS
cannot fd-pin on the adapter's behalf; the guarantee there is that the path and package closure remain
under an immutable operator-owned root that the `arsd`/AGENT UID cannot rewrite.

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

🟨 The Session record also persists the `session_compatibility_epoch` in force when it was created.
Reuse requires equal profile ID/revision/`adapter_contract_hash`, equal workspace/owner/namespace, and
equal epoch. A missing or different epoch is rejected before any lease mutation and before
`session/load`, and never degrades into `session/new` — a silent new external Session would be exactly
the continuity failure R4 forbids. An epoch may be retained across a Binding change only after an
approved continuity canary proves the external AGENT still loads its own prior Sessions; otherwise the
operator bumps it, and every older Session becomes non-reusable by construction.

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

Exact caller UID values and policy ownership are gate G12, closed as a recorded operator decision; the
repository stores no production mapping value.

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
    ├── session.json               # stable binding + last_effective_* + state (+ epoch 🟨)
    └── lock.json                  # lease/process identity while held
```

🟨 `launch.json` gains the resolved runtime provenance — profile/contract identity, launch kind,
adapter/interpreter identity for wrapped profiles, the complete external CLI artifact identity, the
Binding generation/set/slot hashes, the epoch, and the acceptance receipt reference — and embeds its own
`launch_spec_hash` so the record is self-verifying. The hash excludes exactly one top-level field,
`launch_spec_hash`, and nothing else may be excluded. The Binding root itself is operator storage
outside `.agent-run-supervisor/`; ARS reads it and never writes it.

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
target. Production is achieved only after Stage 2 acceptance — which is closed on `main`; the board
carries the closure and enablement facts. Publication and later integration are not implied by it.

🟨 The Runtime Binding refactor is not a fourth stage. It is a change of authority shape on the closed
Stage 2 line, delivered through two PR gates: PR-A updates this authority chain and activates one plan;
PR-B lands one coherent vertical source/test/docs implementation whose work packages are internal
commits. No separate unused foundation PR is landed, and neither gate is a rollout: promoting a Binding
against a real deployment, real-provider acceptance, publication, and any service restart each remain
separate operator decisions.

## 10. Legacy coexistence and rollback

The released v0.1.7 acpx paths remain ✅ compatibility surfaces until a separate retirement decision.
They do not define vNext modules, Session semantics, status vocabulary, or production ingress. Native
failure never routes to them.

Rollback disables Native/`arsd` ingress and stops new submissions. It never converts failures into acpx
fallback and never rewrites terminal Run facts.

🟨 Binding rollback is a distinct, narrower mechanism: `runtime-binding rollback` re-promotes a
previously validated generation and affects only Runs admitted afterwards. It never rewrites a sealed
`launch.json`, never changes a terminal Run fact, and never substitutes for a source revert or for
disabling ingress.

🟨 A *source* rollback that removes the Binding layer is fail-closed for Binding-era Sessions, not a
return to pre-epoch reuse. The reverted runtime cannot enforce epoch or contract identity, so it must stop
new admissions and must never silently `session/load` a Session created under the Binding era. Those
Sessions stay read-only (`status`/`list`/`close`), closed, or quarantined; continuing that work needs a new
Session, or an explicitly approved rollback procedure that states how epoch and contract identity are
checked. Reuse is never inferred from a missing field. Terminal Run facts and sealed launch evidence
remain immutable across any rollback.

## 11. Authority map

- Product intent: `GOAL.md`
- Requirements: `docs/product/prd.md`
- Module design: `docs/design/technical-solution.md`
- Compatibility schema: `docs/design/result-event-schema.md`
- Current status/gates: `docs/roadmap/`
- Executable work: `docs/plans/active/`
- Historical-only material: all archive directories

---
title: "agent-run-supervisor vNext PRD"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-26
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
- Resolve the operator-owned Runtime Binding exactly once per Run (R13), project only the slots the
  profile's `AdapterContract` accepts, and revalidate contract match plus artifact digest against the
  trusted immutable paths. Callers never select a runtime, path, version, digest, or Binding generation.
- Materialize `ResolvedLaunchSpec` — including the complete resolved runtime identity — then seal
  immutable `AgentRunSpec/spec_hash` before spawn. `launch_spec_hash` remains the launch seal.
- Store requested specification and observed effective state separately; observations never rewrite the
  frozen request/profile/Binding.

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
- One ARS Session binds one external AGENT Session ID, AgentProfile revision/hash, owner/namespace,
  compatibility resources, and the `session_compatibility_epoch` that was in force when it was created.
  The external AGENT remains conversation/context authority.
- Later Runs use real `session/load` on the unchanged external ID; silently creating a new external
  Session is failure. Reuse requires equal profile ID/revision/`adapter_contract_hash`,
  workspace/owner/namespace, **and** equal epoch. A missing or different epoch is rejected before any
  lease mutation and before `session/load`, and never falls back to `session/new`.
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
- Exact UID values and policy ownership are gate G12, closed as a recorded operator decision. The
  repository never stores a production mapping value; it reaches the daemon only as `--caller-mapping`
  arguments supplied by the operator.

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
- Each profile carries an `AdapterContract`: the source-frozen compatibility contract described in R13.
  A profile freezes `launch_kind`, argv construction, code-known env keys, ACP protocol/identity and
  required plus forbidden capabilities, permission/config/model/effort/session semantics, and — for
  wrapped adapters — the interpreter and adapter artifact identity. It does **not** freeze the
  deployment-specific external CLI path, version, or digest; those are Binding facts (R13).
- Three profiles are registered, and R13 gives each one a `launch_kind`. The OpenCode profile is
  `direct_acp`: one OpenCode executable is both the AGENT CLI and the ACP implementation, so the profile
  freezes direct launch/protocol/capability semantics while the Binding freezes that single executable's
  identity. Its required stable ID is `opencode-native-acp`, which §5 records as approved and not yet
  registered. The official Codex ACP and Claude Agent ACP profiles are `wrapped_acp`: source freezes the
  interpreter plus the ACP adapter, and the Binding freezes the downstream CLI artifact and the
  config-root values.
- New profiles are typed, versioned, closed registrations. An Agent-specific adapter is allowed only
  after conformance evidence proves a standard ACP gap; v1 has no runtime plugin system.
- Adding a profile, or revising a registered one, requires a fresh install/discovery/permission-canary
  cycle, a revision bump, and independent review. Discovery evidence must come from a real non-prompt
  ACP `initialize` exchange; the ACP `agentInfo.version` and the external CLI `--version` are separate
  facts and neither may be assumed equal to the other.

### R13 — Runtime Binding: operator-owned deployment facts

Three authority layers stay separate and are never merged:

| Layer | Owner | Freezes | Never carries |
|---|---|---|---|
| `AgentProfile` / `AdapterContract` | code (registry) | compatibility semantics | deployment paths, versions, digests |
| Runtime Binding | operator (outside the repository) | deployment facts | command, argv, env key, adapter, launch kind, capability, permission, selector |
| `ResolvedLaunchSpec` / runtime provenance | one Run | the resolved, sealed launch and runtime identity | anything re-read after sealing |

**Contract side.** `AdapterContract` source-freezes: stable profile ID, revision, and
`adapter_contract_hash`; `launch_kind` (`wrapped_acp` or `direct_acp`); the accepted Binding schema and
slot projection; the fixed executable/argv construction and code-known env keys only; ACP
protocol/name plus required and forbidden capabilities; permission, config, model, effort, and session
semantics; the wrapped adapter/interpreter artifact identity; and a code-owned safe version-probe rule.

**Binding side.** A Binding generation supplies only: its declared contract identity (profile ID, profile
revision, `adapter_contract_hash`), the external CLI artifact descriptor (immutable versioned path, actual
version, digest), optional values for Profile-declared config-root slots, a positive
`session_compatibility_epoch`, and a provenance block. Every slot binds to the exact profile ID, revision,
and `adapter_contract_hash` that accepted it. After a contract revision, stale generations fail closed; a
Binding is never reinterpreted by a new source contract.

**Acceptance authority.** A generation is accepted only on those explicit machine identity fields plus
trusted owner and artifact validation. Provenance metadata — creation time, `accepted_by`, `accepted_at`,
and the acceptance receipt reference/hash — is recorded and reported, never consulted: it never
self-authorizes, never substitutes for a missing or mismatched machine field, and never becomes a profile
identity field. A generation with a valid receipt but the wrong declared contract identity is refused.

**Artifact identity covers the complete executable code closure.**

- Standalone native binary: regular-file SHA-256, plus the interpreter/dynamic-loader policy where one
  applies.
- Package or launcher CLI: an immutable package root/tree or canonical manifest digest, the launcher
  identity, and the required interpreter/runtime identity. A launcher-file hash alone never freezes the
  sibling code that launcher loads, and ARS must not claim that it does.
- The artifact and every path ancestor are operator- or root-owned and non-writable by the `arsd`/AGENT
  UID.

**Layout and validation.** A Binding root holds a regular, atomically replaced `active.json` plus
`generations/<id>/manifest.json`; there is no active symlink. Validation requires strict canonical JSON
within a finite size bound, `O_NOFOLLOW`/dirfd walks, verified ownership, modes, and ancestors, and
refusal of traversal, symlink, FIFO, device, unknown fields, and unknown slots.

**Promotion and admission.** `validate` and `promote` obtain the real external CLI version through the
Profile's code-owned version probe and compare it with the Binding; a manifest's version string alone is
not proof. Admission reads `active.json` and the selected generation exactly once per Run, revalidates
contract match and artifact digest against the trusted immutable paths, resolves the complete
launch/runtime identity, writes write-once `launch.json`, and seals `launch_spec_hash`. Spawn,
finalization, and reconciliation never reread the active Binding, and admission never accepts caller
selection.

**Operator surface.** The installed commands are `runtime-binding validate`, `promote`, `rollback`, and
`inspect-run`. There is no `--force` and no internal `sudo`. Pure Binding promotion does not restart
`arsd`; changing the Binding root, the service unit, or the runtime does, and remains separately
approved. `inspect-run` recomputes the launch hash after excluding only the top-level
`launch_spec_hash`, and reports profile/contract, adapter/protocol, Binding generation/set/slot hashes,
the complete CLI artifact identity/version/digest, and the epoch.

**Compatibility.** `AgentRunRequest` and `AgentRunSpec` field sets, the `arsd` v1 public wire, the
result/event grammar, reconcile semantics, and the `ManagedProcess` public API are unchanged. Old Runs
stay readable. Old Native Sessions stay status/list/close-readable, but `session/load` on a record
without a matching epoch fails closed.

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

### Runtime Binding refactor — two PR gates

R13 is delivered on the already-accepted Stage 2 line, not as a new stage, through exactly two PR gates:

1. **PR-A** — this authority/design update plus one active implementation plan. It changes documentation
   authority only and claims no source implementation, rollout, publication, deployment, service
   restart, or real-provider acceptance.
2. **PR-B** — one coherent vertical source/test/docs implementation whose work packages land as internal
   commits inside that single PR. No separate unused foundation PR is landed.

PR-B is complete when the contract/Binding split, the read-once sealed admission path, the epoch gate,
the artifact/owner/TOCTOU refusals, and the operator command surface are proven by hermetic tests, and
the compatibility surfaces above are unchanged. Real-runtime evidence, promotion against a real Binding
root, rollout, and publication remain separate operator decisions after PR-B, exactly as for every prior
stage.

Sachima `ArsdBackend` is a later, separately approved integration after ARS production acceptance.

## 5. Current implementation status

Volatile status truth lives in [`docs/roadmap/current-status.md`](../roadmap/current-status.md); this
section records only the coarse position.

- Released compatibility baseline: v0.1.7 acpx one-shot/persistent paths are implemented.
- vNext Stage 0/1 (Native ACP core) and Stage 2 (`arsd` UDS ingress, ownership, reconciliation,
  service/cgroup containment) are implemented on `main` with their acceptance closed; three closed
  profiles are registered and have operator-held local socket-path acceptance.
- Release/publication is not done: the published wheel predates `arsd` and the official adapter
  profiles. Sachima integration, public ingress, and Gateway/IM/live behavior remain unimplemented and
  separately authorized. Implementation status is never an approval for the next stage.
- R13 is accepted design with no source yet. The registry on `main` still carries deployment-specific
  downstream CLI paths, versions, and digests inside the profile constants, there is no Runtime Binding
  layer, no `session_compatibility_epoch`, and no `runtime-binding` command surface. The OpenCode
  profile ID and version string on `main` have drifted from the executable the operator reports as
  installed; the stable ID `opencode-native-acp` required by R12 is approved but not yet registered,
  and freezing it awaits discovery evidence. The board-linked active plan carries that work; nothing in
  R13 is deployed.

## 6. Non-goals

Public ingress, root/TCP daemon, distributed or multi-tenant control plane, business orchestration,
Feishu/Gateway semantics, broad RBAC, per-Run Worker, arbitrary command/argv/env/config passthrough,
runtime adapter plugins, acpx fallback, shared/imported acpx sessions, cross-AGENT Session reuse,
general rebind, automatic replay, content-digest service, filesystem watcher, and hostile-process sandbox
claims.

Runtime Binding adds four more: operator-declared launch semantics of any kind, caller-selected runtime
or Binding generation, a forced/unvalidated promotion path, and any ARS-internal privilege escalation to
prepare an artifact root. Artifact identity and ownership checks are fail-closed admission controls; they
are not an OS sandbox and do not contain a hostile process.

## 7. Authority and archive rule

This PRD is the product requirement authority for new development. Architecture and module design live in
`docs/design/`. Implementation sequencing lives only in the board and `docs/plans/active/`.

Documents under `docs/archive/`, `docs/plans/archive/`, and `docs/roadmap/archive/` are retained history.
They cannot approve work, redefine this PRD, or serve as default agent context.

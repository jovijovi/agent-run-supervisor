---
title: "agent-run-supervisor vNext PRD"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-29
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
- Four profiles are registered, and R13 gives each one a `launch_kind`. The OpenCode profile is
  `direct_acp`: one OpenCode executable is both the AGENT CLI and the ACP implementation, so the profile
  freezes direct launch/protocol/capability semantics while the Binding freezes that single executable's
  identity. Its required stable ID is `opencode-native-acp`, which §5 records as registered on `main`.
  The official Codex ACP and Claude Agent ACP profiles are `wrapped_acp`: source freezes the
  interpreter plus the ACP adapter's complete package closure, and the Binding freezes the downstream
  CLI artifact and the config-root values.
- `standard-native-acp-v1` is a fourth registered profile of a different shape: a versioned
  `direct_acp` contract that freezes ACP-v1 **conformance only** — protocol major, `loadSession` as a
  required capability, real `session/load`, the accepted Binding slot schema, the code-known env key
  set, and the code-owned probe rule — and freezes no agent-specific identity, selector, or domain.
  It is instantiated per agent by an operator-owned Agent Registration (R14). Its `-v1` suffix is
  load-bearing: profile construction refuses a contract whose frozen protocol major disagrees with
  the id, so a future `standard-native-acp-v2` is a separate profile rather than a revision.
- New profiles are typed, versioned, closed registrations. An Agent-specific adapter is allowed only
  after conformance evidence proves a standard ACP gap; v1 has no runtime plugin system, and an
  Agent Registration is not one: it selects and narrows inside source-declared bounds and can supply
  no code, path, or capability of its own.
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
- Wrapped ACP adapter: the same rule applied to the source-frozen side. The closure root is the
  smallest root the runtime's own module resolution cannot escape downward from — for a Node adapter,
  the npm install root the entry's parent walk reaches, because dependencies hoist above the package
  directory. The frozen entry must lie inside that root, judged on path components so a sibling
  directory sharing a name prefix is never mistaken for a member, and no further module-resolution
  root may exist on the ancestor chain above it. A tree digest is necessary and not sufficient: the
  contract must also freeze the interpreter argv prefix that disables the runtime's *path-independent*
  search roots, and that prefix is contract identity — hashed, sealed, and re-proven against the real
  argv at the spawn boundary — never an incidental launch literal.
- Every source-frozen runtime path names the root-owned artifact location a separate materialization
  step is expected to create. A path under the service account's home can never satisfy the ownership
  rule, because its ancestors are service-owned and no per-leaf change fixes that; declaring such a
  path would push the contradiction into deployment. Declaring the expected path is not creating it.
- The artifact and every path ancestor are operator- or root-owned and non-writable by the `arsd`/AGENT
  UID.

**Layout and validation.** One daemon takes one Binding root and the registry is closed at several
profiles, so the root's active-selection namespace is **profile-scoped**: it holds one independently
promotable active selection per registered profile, under
`profiles/<profile_id>/active.json` plus `profiles/<profile_id>/generations/<id>/manifest.json`. The
pointer is a regular, atomically replaced file and there is no active symlink. The pointer declares its
own `profile_id` as a machine field, so a pointer or generation belonging to one profile can never
satisfy another — by path separation and by explicit identity, not by filename. The subtree component
is derived from the already-resolved closed profile; no request field reaches it, and an id that is not
a safe path component is refused. Validation requires strict canonical JSON within a finite size bound,
`O_NOFOLLOW`/dirfd walks, verified ownership, modes, and ancestors, and refusal of traversal, symlink,
FIFO, device, unknown fields, and unknown slots. ARS creates no directory in a Binding root: a
promotion into a subtree the operator has not authored is refused, never materialized.

**Promotion and admission.** `validate` and `promote` obtain the real external CLI version through the
Profile's code-owned version probe and compare it with the Binding; a manifest's version string alone is
not proof. Promotion and rollback replace exactly one profile's pointer, so updating one profile can
never disable or overwrite another's selection, concurrently or in sequence. Admission reads that
profile's `active.json` and the selected generation exactly once per Run, revalidates
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

The pre-0.5.2 single-pointer root layout — one `active.json` at the root — is **rejected, not read**.
It could hold only one activation, so honouring it would silently fail every other registered profile
on a contract mismatch, and its pointer body cannot say which profile it activates. A root still
carrying it is refused with the stable rule `LEGACY_BINDING_LAYOUT`, and a root with no subtree for the
resolving profile with `PROFILE_BINDING_ABSENT`. ARS neither migrates nor repairs operator storage: the
operator moves each generation under `profiles/<profile_id>/generations/` and re-promotes per profile,
which is a separate operator decision.

### R14 — Agent Registration: one standard contract, many registered agents

A profile whose contract sets `requires_agent_registration` is not frozen agent by agent in source. It
is instantiated by a typed, bounded, operator-owned **Agent Registration** — a fourth authority that
sits strictly *inside* layer 2, never beside layer 1.

**Anchor.** An agent-scoped profile descends one level deeper than the profile-scoped layout above:

```text
<binding_root>/profiles/<profile_id>/
├── active.json                            # non-agent-scoped only — unchanged
├── generations/<gen>/manifest.json        # non-agent-scoped only — unchanged
└── agents/<agent_id>/                     # agent-scoped only
    ├── registration.json
    ├── active.json
    └── generations/<gen>/manifest.json
```

Field-set widening is contract-dependent, never global: a non-agent-scoped profile's pointer and
`contract_identity` field sets are byte-identical to R13's, so its promoted generations keep resolving
unchanged. An agent-scoped pointer additionally declares `agent_id`, and its `contract_identity`
additionally declares `agent_id` and `agent_registration_hash` — so a pointer or generation moved
between two agent subtrees is refused on an explicit machine field (`POINTER_AGENT_MISMATCH`,
`REGISTRATION_CONTRACT_MISMATCH`) rather than by path separation alone. The registration is deliberately
*not* folded into the generation manifest, despite costing a third read: folding would put agent
identity inside `generation_hash`, so an artifact-only bump would force re-authoring agent facts and a
rollback would silently change the agent's ACP name.

**What a registration may say.** Only values that select within, or narrow, a bound source already
declared: the ACP `agent_name`; 1..4 bounded ASCII `argv_tokens` structurally incapable of being a path
or a shell fragment; a `version_probe_argv_suffix` validated by the contract's own probe rule, which
keeps parser, timeout, and output bound code-owned; selector ids and their 1..32-entry value domains
with each default inside its own domain; a `forbidden_capabilities` set that is a **superset** of the
source floor and disjoint from the required capabilities; one `permission_binding_id` from a
source-closed mediation registry, or `null`; credential slot names with `required_refs` a subset of
them; and a shape-validated provenance block that is recorded and never consulted. It supplies no
executable, path, digest, version, env key, launch kind, protocol version, or capability requirement —
those are not fields, so the refusal is structural rather than filtered.

**Identity and staleness.** `agent_registration_hash` is computed over the whole payload except
provenance, so re-recording a receipt does not retire an agent's Sessions while any
compatibility-bearing edit does. The generation **freezes** it: the digest a generation declares is
compared with the digest of the Registration that is live at admission, so an in-place Registration edit
under a promoted generation fails closed rather than being launched. That comparison is a single
invariant carried by the runtime pair that holds both halves, and operator validation applies the same
one, so a drifted Registration can be neither admitted nor promoted. It is also sealed into
`AgentRunSpec.agent`, into `launch.json`, and into Session identity, and equality there is symmetric: a Session created under one agent is refused for
another, and an agent-bearing record is refused by a runtime carrying none. A registration must also
re-declare `(profile_id, profile_revision, adapter_contract_hash)`, so a contract revision retires every
registration accepted under the old hash, closed.

**Caller surface.** `AgentRunRequest` gains one optional field, `agent_id`. Admission refuses
`requires_agent_registration XOR agent_id` in both directions before sealing, which makes the absence of
agent identity in a sealed spec a total function of the `profile_id` in the same record. Neither
`SPEC_SCHEMA_VERSION` nor `DIGEST_SCHEMA_VERSION` moves: the digest material drops exactly one named
field when it is `None`, so a pre-upgrade frame digests byte-identically while a request naming an agent
digests differently. `agent_id` is **not** a forbidden runtime-selection field — it selects among
operator-authored, source-bounded registrations exactly as `profile_id` selects among source-registered
profiles, and names no path, executable, argv, env key, digest, or version.

**The one new exposure.** `agent_id` is the first caller-supplied value in this system to become a path
component. It is fail-closed because the value passes the component grammar **before any filesystem
query**; the type is judged by exact identity rather than `isinstance` and frozen once, because a `str`
subclass with a lying `__str__`/`__eq__` is the class of bug this system has already paid for; the
descent is dirfd-relative and `O_NOFOLLOW` under an ownership-verified directory; ARS creates nothing,
so a caller can only name a directory an operator authored under a trusted root; and the registration
re-declares the same `agent_id` as an explicit machine field.

**Operator surface.** `validate`, `promote`, and `rollback` take `--agent`. It is required for an
agent-scoped profile and refused for any other, both by a stable rule. No new command, no `--force`, no
new daemon flag, and no `arsd` restart: admission re-reads the pointer per Run.

**Registering a real agent is an operator sequence, not a source change.** Install the artifact under a
root-owned immutable prefix; run zero-prompt ACP `initialize` discovery for name, protocol,
`loadSession`, selector ids, and the model-dependent effort domain read *after* the exact model is set;
record the code-owned CLI `--version` probe as a separate fact; run the mandatory denied-action
mediation canary; author `registration.json` and the manifest; then `validate --agent` and
`promote --agent`. Each step is a separate decision, and none is implied by a merged source change.

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
- The R13 Runtime Binding source framework is merged on `main`: the contract/Binding split, one
  read-once Binding resolution per Run with sealed launch and runtime provenance, the
  `session_compatibility_epoch` reuse gate, the `runtime-binding` operator commands, and an `arsd` that
  requires an explicit `--binding-root`. Deployment-specific downstream CLI paths, versions, and digests
  are no longer profile constants, and the stable ID `opencode-native-acp` is registered on its
  discovery evidence. Its active-selection namespace is profile-scoped, so the one configured root
  holds a concurrent, independently promotable selection for each of the three registered profiles.
- The wrapped artifact identity is a complete package closure in source: each `wrapped_acp` contract
  freezes the adapter install root and its whole tree digest, the frozen entry is proven to lie inside
  that root, the frozen interpreter argv prefix closes the runtime's path-independent search roots, and
  the spawn boundary re-proves the tree, its ownership, and that exact argv prefix on both sides of the
  race seam. Both wrapped profiles bumped a revision for it (Codex r3, Claude r4) and now name the
  root-owned artifact location rather than the service home. Merged source is still not deployment: no
  materialized artifact root, promoted current generation, re-acceptance at the current profile
  revisions, permission canary owed by the current Claude revision, rollout, release, or deployment
  follows from it; each remains a separate operator decision, nothing under the declared artifact
  prefix exists, and nothing in R13 is deployed.

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

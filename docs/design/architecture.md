---
title: "agent-run-supervisor vNext System Architecture"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-29
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/architecture.md"
---
# agent-run-supervisor vNext System Architecture

## 0. Scope and status

This is the system architecture authority for **new ARS development**. It describes the settled vNext
target, not the released v0.1.7 topology. The previous mixed document is preserved at
`docs/archive/pre-vnext-reset-2026-07-21/architecture.md` for history only.

Status markers:

- ✅ released compatibility baseline reused unchanged;
- 🟦 vNext supervision plane, implemented on `main` (Stage 0/1 closed, Stage 2 closed, and the Runtime
  Binding layer of §3.1–§3.3 merged as source, including the complete wrapped-adapter package closure
  of §3.3);
- ⏸ separately approved later integration.

Marker 🟦 records the settled design and the merged source that implements it, not an approval:
per-stage implementation status, gates, and enablement decisions live in
[`docs/roadmap/current-status.md`](../roadmap/current-status.md). Merged source is never operator
activation — preparing an immutable artifact root, promoting a Binding generation, re-accepting a
profile at its current revision, the permission canary owed at the current Claude revision, rollout,
release, and deployment each remain separate operator decisions.

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
→ read the Runtime Binding exactly once (active.json + selected generation)
→ project only contract-accepted slots; revalidate contract match and artifact digest
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

### 3.1 Runtime authority layers 🟦

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
LAYER 2b — operator-owned Agent Registration (agent-scoped profiles only)
  registration.json under profiles/<profile_id>/agents/<agent_id>/
  ACP agent_name · bounded argv tokens · probe argv suffix
  selector ids + value domains · forbidden-capability superset
  one source-registered mediation binding id, or null
  credential slot names · provenance   ← recorded and reported, never consulted
  agent_registration_hash (excludes provenance) → frozen by the generation,
    and sealed into spec, launch, and Session identity
        │
        ▼
LAYER 3 — per-Run sealed ResolvedLaunchSpec + provenance      owner: the Run
  write-once launch.json · launch_spec_hash · never re-read after sealing
```

Layer 2b exists so one source contract can serve many standards-conforming agents without a plugin
platform. It is strictly *inside* layer 2, never beside layer 1: every value it supplies selects within
or narrows a bound layer 1 already declared, and it supplies no executable, path, digest, version, env
key, launch kind, protocol version, or capability requirement. `adapter_contract_hash` deliberately does
**not** become agent-derived — synthesizing a per-agent contract from a registration would make the
contract hash a function of operator data, so the operator's registration would satisfy the operator's
own manifest. The registration carries its own `agent_registration_hash` instead.

A Binding never declares a command, argv, env key, adapter, launch kind, capability, permission, or
selector. Every slot binds to the exact profile ID, revision, and `adapter_contract_hash` that accepted
it, so a contract revision fails stale generations closed rather than letting a new source contract
reinterpret operator-authored values.

Acceptance rests on those explicit machine fields plus trusted ownership and digest validation, and on
nothing else. The provenance block is recorded and reported for audit; it never authorizes a generation,
never substitutes for a missing or mismatched machine field, and never becomes part of profile identity.
A generation with a valid acceptance receipt but the wrong declared contract identity is refused.

Read-once is structural, not advisory: `arsd` admission opens the Binding root once per Run — one
pointer read and one generation read, both inside the resolved profile's own subtree — and spawn,
finalization, and reconciliation have no Binding read path at all. Two Runs admitted on either side of a
promotion are each sealed to what they read; an in-flight Run is never re-pointed, and a promotion for
one profile is invisible to every other.

### 3.2 Binding layout, validation, and operator surface 🟦

```text
<binding_root>/                            # operator/root-owned; outside the repository
└── profiles/<profile_id>/                 # one independent selection per registered profile
    ├── active.json                        # non-agent-scoped only — regular file, never a symlink
    ├── generations/<generation_id>/
    │   └── manifest.json                  # immutable once written
    └── agents/<agent_id>/                 # agent-scoped profiles only
        ├── registration.json              # operator-owned Agent Registration
        ├── active.json
        └── generations/<generation_id>/manifest.json
```

The agent anchor is a **new subtree that only an agent-scoped profile descends into**, never a rewrite
of the existing one: a non-agent-scoped profile's descent, pointer field set, and `contract_identity`
field set are byte-identical to what they were, so its already-promoted generations keep resolving
without migration, re-promotion, or restart. An agent-scoped pointer adds `agent_id`, and its
`contract_identity` adds `agent_id` and `agent_registration_hash`, so separation is proven one level
deeper than `POINTER_PROFILE_MISMATCH`: a pointer or generation moved between agent subtrees is refused
on `POINTER_AGENT_MISMATCH` or `REGISTRATION_CONTRACT_MISMATCH`.

That frozen digest is **compared against the Registration that is actually live**, not merely recorded.
The comparison is one invariant in one place — the runtime pair that holds both halves — and operator
validation applies it through that same object rather than restating it, so a generation whose
Registration has drifted can be neither admitted nor promoted. Reading a generation is deliberately a
separate, weaker act: it returns what the generation says and never claims to have admitted a
Registration, because the object that reads one manifest is not the object that can compare two facts.
Without that split the manifest would satisfy itself, and every other check — pointer bytes, manifest
bytes, manifest digest, epoch, contract identity — would still pass on an in-place Registration swap,
since none of them is about the Registration's contents.

`agent_id` is the one place caller text becomes a path component. It is judged by the component grammar
**before any filesystem query**, by exact type identity rather than `isinstance` and frozen once (a
`str` subclass with a lying `__str__`/`__eq__` is the failure this codebase has already paid for once),
and the descent below it is dirfd-relative and `O_NOFOLLOW` under an ownership-verified directory. ARS
creates nothing, so a caller can only name a directory an operator authored under a trusted root, and
the registration inside re-declares the same `agent_id` as a machine field.

Reads stay exactly-once and are instrumented: three per agent-scoped Run — one `registration.json`, one
`active.json`, one `manifest.json` — two for a non-agent-scoped Run, and **zero** during spawn,
finalization, and reconciliation.

The active-selection namespace is profile-scoped because the shape of the deployment demands it: one
`arsd` takes one `--binding-root`, the registry is closed at several profiles, and each refuses admission
until a generation is promoted **for that profile**. A single root-level pointer could satisfy exactly
one of them at a time. Each subtree is therefore independent — promoting or rolling back one
profile replaces one file inside that profile's own directory and cannot disable, overwrite, or race
another's, concurrently or in sequence — and the generation namespace is per profile too, so two
profiles may carry the same generation id without either meaning the other.

Separation is proven twice over. The subtree component is derived from the already-resolved closed
profile, never from request text, and is refused unless it is a safe path component; and the pointer
declares its own `profile_id` as a machine field, so a pointer moved or copied between subtrees is
refused (`POINTER_PROFILE_MISMATCH`) rather than inheriting authority from its filename. A generation
still has to declare the matching contract identity on top of that.

Validation is fail-closed on every read: strict canonical JSON, finite size bound, `O_NOFOLLOW`/dirfd
walks, verified ownership, modes, and full ancestor chain, and refusal of traversal, symlink, FIFO,
device, unknown fields, and unknown slots. There is no active symlink to retarget. ARS creates nothing
in a Binding root: `profiles/<profile_id>/generations/` is operator-authored, an absent subtree is
`PROFILE_BINDING_ABSENT`, and a root still carrying a pre-0.5.2 root-level `active.json` is
`LEGACY_BINDING_LAYOUT` — refused and never read, because that layout can hold only one activation and
its pointer cannot say whose it is.

The operator command surface is exactly these, and no command beyond them is defined:

```text
agent-run-supervisor runtime-binding validate     # probe-backed check of a generation
agent-run-supervisor runtime-binding promote      # atomically replace active.json
agent-run-supervisor runtime-binding rollback     # re-promote a previously validated generation
agent-run-supervisor runtime-binding inspect-run  # per-Run provenance recomputation
```

No `--force` is defined and no command escalates privilege internally; preparing an immutable artifact root
is an operator action outside ARS. Each command names one registered profile — and, for an agent-scoped
profile, one registered agent through `--agent`, which is required there and refused anywhere else — and
touches only that subtree. Without `--agent` an operator would have no way to promote an agent-scoped
generation at all, which would leave such a profile permanently unusable. `validate`/`promote` obtain the real external CLI version through the
Profile's code-owned probe and compare it with the Binding — a manifest's version string alone is not
proof. A pure Binding promotion does not restart `arsd`, because admission re-reads the active pointer
per Run; changing the Binding root, the service unit, or the runtime does require a restart and stays
separately approved.

`inspect-run` recomputes the launch hash from the sealed launch record after excluding only the
top-level `launch_spec_hash`, and reports profile/contract identity, adapter/protocol identity, Binding
generation/set/slot hashes, the complete CLI artifact identity/version/digest, and the epoch.

### 3.3 Launch kinds and artifact code closure 🟦

| Launch kind | Source freezes | Binding freezes |
|---|---|---|
| `wrapped_acp` (Codex ACP, Claude Agent ACP) | interpreter/Node identity, the ACP adapter's complete package closure (install root + tree digest + entry), argv construction, env keys, protocol/capability contract | downstream CLI artifact identity/version/digest, config-root slot values |
| `direct_acp` (OpenCode) | direct launch, protocol, and capability semantics | that one executable's identity/version/digest |
| `direct_acp`, agent-scoped (`standard-native-acp-v1`) | ACP-v1 conformance only: protocol major, required `loadSession`, real `session/load`, the accepted slot schema, the code-known env key set, the probe rule, and the forbidden-capability **floor** | that one executable's identity/version/digest, plus — through the Agent Registration (§3.1 layer 2b) — the ACP name, argv tokens, selector ids and domains, capability narrowing, and mediation selection |

OpenCode is one artifact, not two: the same executable is the AGENT CLI and the ACP implementation, and
the documentation must not pretend otherwise.

The agent-scoped row freezes no agent-specific constant at all, and that is deliberate rather than
incomplete: across standards-conforming native ACP agents the facts that actually vary are a small typed
bounded set, while launch kind, slot schema, env allowlist, session-load requirement, required
capabilities, protocol version, permission/config/session semantics, and the probe parser and bounds are
shared ACP-v1 conformance that belongs in one source contract. Its `executable_key` appears in neither
the registered-executable map nor the profile-keyed mediation map, so its executable can only arrive
through the slot and its mediation can only arrive through a registration's selection from the
source-closed mediation registry — the two mediation registries are disjoint by profile-construction
invariant rather than by convention.

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

🟦 The same rule now holds on the wrapped **adapter** side. `WrappedRuntimeArtifacts` freezes an
`adapter_package_root` plus its `adapter_tree_sha256` beside the interpreter and entry, and the closure
root is chosen so the runtime's own resolution cannot leave it:

- the root is the adapter's npm **install root**, not its package directory, because a Node entry at
  `<root>/node_modules/<scope>/<pkg>/dist/index.js` resolves bare specifiers by walking its parent
  chain — hoisted dependencies live in `<root>/node_modules`, which the package directory does not
  contain;
- the frozen entry is required to lie inside the root, judged on path components, so a sibling like
  `…/1.0.0-evil` can never pass as a member of `…/1.0.0`;
- everything at or below the root is covered by the tree digest, and the spawn boundary refuses any
  `node_modules` on the ancestor chain **above** the root, which is the only place that parent walk can
  still reach. Preparing an artifact root with no such directory above it is therefore part of
  preparing an immutable root, and is an operator action.

A parent walk is not Node's only way out. Its CommonJS resolution also searches path-independent
*global folders* — `$HOME/.node_modules`, `$HOME/.node_libraries`, `<node prefix>/lib/node` — which no
closure root can contain, and both wrapped profiles forward `HOME`. The contract therefore also freezes
an `interpreter_argv_prefix`, which for the frozen Node is exactly `--no-global-search-paths`:

- it is required and non-empty for every `wrapped_acp` contract — an interpreter with no way to close
  that search cannot honestly carry one;
- it is the literal head of the profile's argv, and profile construction refuses any argv that does not
  begin with exactly the declared tokens, so the declared prefix and the real launch cannot drift;
- it rides in `adapter_contract_hash`, is sealed into `launch.json`, and the spawn boundary compares it
  against the real argv as a token *sequence*, with the adapter entry bound to the position immediately
  after it — so a dropped, reordered, altered, or padded prefix refuses the Run before spawn.

`NODE_PATH` and any other resolution root that would come from the environment are closed by the
profile's own closed env allowlist rather than by these checks.

The consequence the earlier gap made concrete: one adapter's `dist/index.js` stayed byte-identical
across two adapter versions while the siblings it imports moved, so an entry digest could not tell the
two apart. A tree digest does.

🟦 Every source-frozen runtime path names the root-owned artifact location a separate materialization
step is expected to create, under `/opt/agent-run-supervisor/artifacts/`. That is a declaration, not an
installation: nothing under it exists, ARS never creates, copies, or re-owns it, and admission simply
fails closed until an operator materializes it. A path under the service account's home was not an
option — C5 requires the artifact *and every ancestor* to be non-writable by the `arsd`/AGENT UID, and
no per-leaf ownership change can make a service-owned home satisfy that, so freezing such a path would
only have deferred the contradiction to deployment. The currently installed adapter trees remain
discovery and measurement sources — the frozen digests are byte identity, which ownership, mode, and
path do not enter — and are not activation targets.

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

🟦 The Session record also persists the `session_compatibility_epoch` in force when it was created.
Reuse requires equal profile ID/revision/`adapter_contract_hash`, equal workspace/owner/namespace, and
equal epoch. A missing or different epoch is rejected before any lease mutation and before
`session/load`, and never degrades into `session/new` — a silent new external Session would be exactly
the continuity failure R4 forbids. An epoch may be retained across a Binding change only after an
approved continuity canary proves the external AGENT still loads its own prior Sessions; otherwise the
operator bumps it, and every older Session becomes non-reusable by construction.

🟦 For an agent-scoped profile the record also persists `agent_id` and `agent_registration_hash`, and
reuse requires both to be equal on the same terms. Equality is symmetric on purpose: a Session created
under one agent is refused for another, and an agent-bearing record is refused by a runtime carrying
none, so a runtime that cannot enforce agent identity never silently loads an agent's Session. Because
the registration hash excludes provenance, re-recording an acceptance, discovery, or canary receipt does
not retire an agent's Sessions, while any compatibility-bearing edit does. A record created before
agents existed keeps its exact serialized shape — the two fields are omitted, never written as null.

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
    ├── session.json               # stable binding + last_effective_* + state + epoch
    └── lock.json                  # lease/process identity while held
```

🟦 `launch.json` carries the resolved runtime provenance — profile/contract identity, launch kind,
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

🟦 The Runtime Binding refactor is not a fourth stage. It changed the authority shape on the closed
Stage 2 line and its source framework is merged on `main`. Merging it was not a rollout: preparing an
immutable artifact root, promoting a Binding against a real deployment, re-accepting a profile at its
current revision, real-provider acceptance, publication, and any service restart each remain separate
operator decisions.

## 10. Legacy coexistence and rollback

The released v0.1.7 acpx paths remain ✅ compatibility surfaces until a separate retirement decision.
They do not define vNext modules, Session semantics, status vocabulary, or production ingress. Native
failure never routes to them.

Rollback disables Native/`arsd` ingress and stops new submissions. It never converts failures into acpx
fallback and never rewrites terminal Run facts.

🟦 Binding rollback is a distinct, narrower mechanism: `runtime-binding rollback` re-promotes a
previously validated generation for one named profile and affects only that profile's Runs admitted
afterwards. It never rewrites a sealed `launch.json`, never changes a terminal Run fact, never touches
another profile's selection, and never substitutes for a source revert or for disabling ingress.

🟦 A *source* rollback that removes the Binding layer is fail-closed for Binding-era Sessions, not a
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

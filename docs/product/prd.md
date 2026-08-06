---
title: "agent-run-supervisor vNext PRD"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-04
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/prd.md"
---
# agent-run-supervisor vNext PRD

## 1. Product goal

ARS vNext provides one local, auditable, fail-closed supervision plane for the execution of **external ACP
AGENTs that ARS does not own**. A trusted caller submits a structured request that already contains its
business decision and a frozen execution grant. ARS authenticates the caller, binds approved resources,
resolves a source-owned ACP compatibility profile and an operator-registered agent entry, starts exactly
one local process from the declared command, supervises one Run, and returns redacted technical facts and
evidence.

Production execution always follows:

```text
trusted caller → arsd UDS → ars-core / Native ACP → one local process running the registered command
```

Installation, configuration, credentials, `HOME` and AGENT state, and upgrades belong to users and
operators, outside ARS. ARS owns the process it starts, not the software it starts.

The legacy v0.1.7 acpx paths are **not** an ARS product, runtime, fallback, Session store, compatibility
baseline, or compatibility obligation, and they are not the design basis for new work. Their code and the
fields it emits still exist on `main`, and this authority chain describes them only as current-source facts;
removing that code and content is separately authorized work that this PRD neither performs nor approves.
The only acpx material the product retains is a bounded differential/comparison-test reference. The former
acpx authority is preserved under `docs/archive/pre-vnext-reset-2026-07-21/`, and the retired
artifact/Binding-era authority under `docs/archive/binding-era-2026-07/`.

## 2. Actors and authority

| Actor | Owns | Does not own |
|---|---|---|
| Hermes / FlowWeaver / trusted caller | user intent, business authorization, task graph, agent/model/effort choice, frozen execution grant, retry/approval/delivery/business verdict | process/ACP supervision facts; any command, argv, environment, path, or secret value |
| Operator | AGENT installation and upgrades, the agent registry file, environment declarations, credentials, AGENT `HOME`/config/cache/Session state, service unit, restarts | Run/Session authority, ACP semantics, mediation pairs, capability or protocol requirements |
| `arsd` / ars-core | caller authentication, resource binding, Run/Session lifecycle, process ownership, ACP state, grant enforcement, evidence, reconciliation | business judgment, Feishu/Gateway semantics, broad RBAC, AGENT software identity |
| External AGENT | actual conversation/context and task execution, its own home/config/cache/auth state | ARS Run/Session authority |
| User-level service manager | daemon/cgroup liveness and crash containment | Run/Session/lease/business state |

A technical `completed` result never means the caller's business task succeeded.

## 3. Product requirements

### R1 — Structured admission and immutable Run identity

- Accept only a versioned `AgentRunRequest`; never accept arbitrary shell text, argv, env, JSON config,
  executable paths, or credential values from callers.
- Authenticate the local caller and bind owner/namespace, workspace, Session, credential references,
  MCP/config snapshots, limits, evidence/recovery policy, and frozen `execution_grant`.
- Parse the operator agent registry **exactly once at daemon startup** into an immutable in-memory
  snapshot, then reconcile, then bind the socket. Any registry defect refuses to listen before any state
  write.
- Validate `agent_id` against its grammar, then resolve it against that startup snapshot as a **pure
  in-memory lookup with zero filesystem access**. Resolve the entry's profile from the source registry.
  Callers select no runtime, path, version, digest, or generation.
- Bind the workspace, validate the frozen grant, build `argv = [command_declared, *args]`, and resolve the
  final child environment **once, in memory** (R15). No command resolution, no realpath, no pre-flight
  resolution gate, no digest or ownership check on the command or its ancestors.
- Seal in order: write-once `spec.json` including the launch hash, then its referenced write-once
  `launch.json`. Nothing is re-read, re-resolved, or re-derived after the Spec write. `launch_spec_hash`
  remains the launch seal and covers value-blind material only.
- Store requested specification and observed effective state separately; observations never rewrite the
  frozen request, profile, registry snapshot, or Spec.

### R2 — Supervised live ACP process

- Native ACP uses a live process surface (`ManagedProcess` or equivalent), not the legacy
  completion-oriented `execute_subprocess`.
- The supervision layer owns spawn, PID/PGID, complete `ProcessIdentity`, bounded stderr, timeout,
  signal escalation, process-group termination, wait, and reap.
- The official ACP client connection exclusively owns stdin/stdout JSON-RPC framing.
- `RunTask` coordinates the process and ACP state machine in `arsd`; no independent per-Run Worker exists.
- Every Run owns exactly one local `ManagedProcess` and exactly one stdio ACP driver, from spawn to reap.
  The process is non-optional after spawn: there is no endpoint abstraction, no optional process
  ownership, no no-op termination path, and no remote-oriented indirection anywhere in the runtime.

### R3 — Exact configuration fidelity

Before any prompt, one ACP connection must complete:

```text
initialize / capability discovery
→ session/new or session/load
→ discover current config options
→ set requested model
→ consume the model-dependent option set
→ [separate-selectors only] rediscover effort
→ [separate-selectors only] set requested effort
→ exact requested == effective readback
→ persist EffectiveRunState
→ prompt
```

Missing capability, unadvertised value, alias/coercion, stale option set, failed set, or inexact readback
produces zero Turn and no prompt. Literal `max` must never be downgraded to `high` or another value.

**Two configuration-fidelity modes exist, and a profile declares exactly one.** `separate-selectors` is the
sequence above and is the default every existing profile keeps. `model-only` describes an agent whose model
selector *is* the whole configuration: it advertises no independent effort selector, so the sequence stops at
the exact model readback, **no effort option is discovered and no effort set is ever dispatched**, and the
effective effort is one shared `N/A` sentinel. A `model-only` Run must request that sentinel; any other
requested effort fails before the prompt rather than being silently ignored, because ignoring a requested
effort is the coercion this requirement forbids. The selector value stays opaque in both modes — a literal
such as `grok-4.5[effort=high,fast=true]` is set and read back byte-for-byte, and no code path parses it,
infers an effort from it, maps a model name, or reads an agent's ACP `mode` selector as an effort.

**The live-advertised option set is the domain authority.** No source-frozen `registered_models`,
`allowed_efforts`, or selector value domain gates admission: "an unadvertised value ⇒ zero Turn, no
prompt" is checked against what the running agent advertises right now, which is strictly stronger than
the same check against a constant frozen months earlier. A profile or registry entry may carry a selector
**id** hint; it never carries a value domain.

### R4 — Session continuity, closed start plan, and between-Run switching

- v1 is process-per-Run; the AGENT process lifetime is contained within one Run.
- **Runs terminate; Sessions do not close.** There is exactly one Session kind:

  ```text
  Session: create → reuse → reuse → indefinitely resumable
  Run:     create → execute → terminal
  ```

  A Session has no normal terminal state, no one-shot or ephemeral variant, and no close/revoke alias. ARS
  has no observable business event meaning "the user is finished with this conversation": a caller may
  continue immediately, return months later, change topic, or never return, and neither Run completion nor
  silence proves abandonment. External AGENT context is durable state rather than a resident ARS process,
  so normal operation needs no close transition.
- One ARS Session binds one external AGENT Session ID plus the complete identity field set of R13's
  registry model: `agent_id`, profile id/revision/hash, owner/namespace, `workspace_hash`, and the
  optional operator-controlled `session_epoch`. The external AGENT remains conversation/context authority.
- Admission derives a **closed start plan** from the immutable request before any Session-store recovery
  behavior. The Session portion of the request is exactly one optional field, `session_id`:

  | Input | Meaning |
  |---|---|
  | `session_id` absent | create one new durable Session and execute its first Run |
  | `session_id` present | existing-only reuse of that Session |
  | unknown or invalid `session_id` | stable refusal; never create a replacement |

  A reuse request opens the named Session **existing-only**: an absent record fails, a corrupt
  record fails, and neither branch creates a record. Reuse then validates the full binding with the
  load-time gate **before** acquiring the lease and requires a non-empty stored external ID. Only then may
  it load. A create plan is constructible **only** from a request that carries no `session_id`.
- Creation is atomic with the first Run. There is no standalone `session_create` operation, because empty
  Sessions have no product use and a two-step creation can be abandoned. The create path derives the
  prospective `session_id` deterministically from the same authenticated principal/`request_id` identity
  that determines the Run, so repeating a lost request returns the same Run and Session facts instead of
  creating a second Session. The durable submission record — not an in-memory lock — is that reservation;
  the keyed admission lock only serializes concurrent live attempts.
- There is no provisional or unbound Session record. Before `session/new`, the prospective `session_id`
  exists only in the sealed submission/Spec identity and the live keyed admission lock. After `session/new`
  succeeds, ARS atomically persists **one fully bound Session record** carrying the exact external AGENT
  Session ID, before the dispatch marker. If creation fails before that commit, `run_status` reports the
  terminal failed Run and `session_status` for the prospective ID returns the stable unknown/not-found
  result, so a failed creation is never mistaken for a resumable Session. A provider context created by
  `session/new` but never durably bound may become an unreachable provider-side orphan: ARS does not guess
  its ID, scan AGENT-owned storage, or convert it into a Session. The ordering makes that safe, because the
  prompt was not sent.
- Later Runs use real `session/load` on the stored external ID, sent **byte-unchanged** — no trimming,
  Unicode normalization, parsing, case conversion, canonicalization, or regeneration — and **no identity
  field is read out of the response**. Reuse is proven by a successful load whose `config_options` seed
  the fidelity machine.
- **A reuse path never emits `session/new`** — not as a fallback, not after a failure, not under any error
  class. This is structural, not conditional: every reuse failure terminates inside or before the load
  path.
- **Every conflicting ID-bearing callback is rejected at callback entry**, before any sink, handler,
  filesystem access, permission decision, terminal response, or unsupported-surface response. The expected
  ID is bound before the load request is issued, so callbacks racing with `session/load` are covered, and
  an unbound expected ID is itself a violation. A rejected callback is never serviced or persisted in a
  `finally` block.
- `session_epoch` is an **operator escape hatch and nothing else**. No automatic bump exists anywhere: an
  AGENT or adapter version change, an ARS package upgrade, a profile revision that does not change ACP
  semantics, a `command`/`args`/`env`/`mediation`/selector edit, a registry file replacement, and a daemon
  restart never change it. Identity comparison is symmetric equality, so **adding `session_epoch` to an
  entry for the first time cuts that agent's existing Sessions, because absent ≠ 1** — the same deliberate
  act as a bump.
- model/effort are immutable per Run but may change between completed Runs under the Session lease.
- Partial switching failure sends no prompt. Exact rollback to the previous observed configuration leaves
  the Session reusable; failed or unprovable rollback quarantines it.
- Changing AGENT type requires a new Session plus caller-owned, explicit context handoff.
- **A Session record retains identity and continuity evidence only:** `session_id`, owner and namespace,
  `agent_id`, profile identity, workspace binding, the optional operator `session_epoch`, the external
  AGENT session id, creation and last-use timestamps, the last observed effective model/effort, and
  optional quarantine evidence. It carries no `state = open | active | closed`, no `closed_at`, no close
  reason or source, no ephemeral/persistent flag, and no reuse mode as identity.

### R5 — Terminal state, uncertainty, and duplicate prevention

The Native terminal vocabulary includes `completed | failed | cancelled | timed_out | unknown`; all
terminal states are irreversible. **They are Run terminals only** — no member of that vocabulary is a
Session state, and every trustworthy Run terminal releases the Session lease while leaving the Session
resumable. `run_cancel` affects only the current Run. Daemon restart performs reconciliation only.

Quarantine is not a lifecycle state. It is optional, independent safety evidence on a Session — a reason
code, the source Run, and when it was recorded — written when continuity is machine-proven unsafe. A
quarantined Session still exists and stays queryable, and it refuses new Runs. There is no unquarantine
operation.

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
caller-authorized Run linked by `retry_of_run_id`; it never rewrites the original terminal fact. There is
no unquarantine tool.

### R6 — Caller authentication and resource ownership

- Production ingress is a local Unix socket in a `0700` directory with a `0600` socket; no TCP or root.
- `arsd` authenticates peer credentials with `SO_PEERCRED`, enforces an approved caller UID policy, and
  records owner identity on Runs/Sessions.
- Only the owner may query, stream, or cancel its resources.
- Exact UID values and policy ownership are gate G12, closed as a recorded operator decision. The
  repository never stores a production mapping value; it reaches the daemon only as `--caller-mapping`
  arguments supplied by the operator.

### R7 — Permission mediation and honest security

- The caller freezes `execution_grant`; ARS enforces it default-deny and never widens or refreshes it.
- A compatibility profile owns ACP launch/config semantics, not business authorization.
- Registered ACP permission/filesystem/terminal requests map to deterministic allow/deny decisions and
  redacted mediation evidence. Unknown operations deny by default.
- Mediation environment values are source-owned in **key and value**, keyed by the capability family they
  mediate. A registry entry may select one binding id or none; it can never author a pair, a key, or a
  value, and there is no "mediation off". Reserved mediation keys are global across every registered
  binding; a colliding registry fails the startup parse and **the daemon refuses to listen**.
- A real denied-action canary is mandatory per registered agent before that agent's use; zero permission
  events prove nothing about denial.
- **A profile may additionally select one closed, source-owned launch-permission policy id.** ACP mediation
  decides before a side effect only when the agent asks, and an agent that completes an edit without ever
  emitting `session/request_permission` is detected only after the file exists. Where cited evidence shows
  that, ARS compiles the selected policy from the Run's frozen grant, materializes it privately per Run under
  the supervisor root before spawn, and points the agent at it, so the agent refuses the side effect itself.
  The one registered policy is read-only: it denies write and shell execution explicitly and denies no read.
  A grant the selected backend cannot faithfully enforce fails **before spawn and before any prompt** with a
  stable code rather than being widened or narrowed silently. This is defense in depth — the permission
  bridge and the completion backstop are unchanged — and it is not a permission framework: there is no
  dynamic or per-tool approval, no path-level write policy, and no new write capability.
- **Mediation is cooperative.** An agent that ignores the knob, or one with no registered binding, can
  execute in-process tools with no ACP permission event, and the permission bridge will never see them.

**Non-guarantees, stated and not softened:**

1. **Not a sandbox.** The AGENT runs as the `arsd` UID with that UID's full authority over the filesystem,
   network, and process table. `allowed_roots` constrains what ARS *approves via ACP*, not what the
   process *can do*.
2. **No supply-chain or integrity claim.** ARS does not verify that the executable it launched is the one
   an operator intended, is unmodified, or came from a trusted publisher. Recorded resolution facts and
   probe output are evidence for humans, never gates.
3. **No hostile-code containment.** An agent that ignores ACP mediation, spawns its own children, writes
   outside the workspace, or exfiltrates data is not stopped by anything here.
4. **Unmediated in-process tools** are invisible to the permission bridge.
5. **Registry trust is transitive to the operator.** A wrong `command` launches the wrong thing. The
   defenses are that the registry is operator-authored, not caller-supplied, read-only to ARS, refused
   when world-writable, parsed once, and fully recorded per Run.
6. **No guarantee about the AGENT's own config or credentials.** They are AGENT-owned; ARS does not read,
   validate, or vouch for them.
7. **No containment guarantee for values the operator projects** (R15).
8. **`agentInfo` is a self-report** (R14).
9. **Process-group and cgroup limits are exactly as stated below.**

**Process boundary.** ARS guarantees a new POSIX session and process group, `ProcessIdentity` recorded
immediately after spawn, signals to the process group, and `wait()` plus reap of its direct child. It
therefore reliably terminates the direct child and every descendant that remains in the process group it
created. It does **not** control a descendant that calls `setsid()`/`setpgid()`, a payload handed to a
service manager as a separate transient unit, a container runtime that relocates the payload to another
namespace and cgroup, or an agent that double-forks. Crash containment via the user-level service manager
cgroup is real, load-bearing, and **external** to ARS. Real isolation belongs at the OS layer — a dedicated
UID, user namespaces, `seccomp`/Landlock, `bwrap`/container/VM boundaries, cgroup limits, network
namespaces — and composes because `command` is opaque to ARS: a wrapper that `exec`s into the payload keeps
it in ARS's process group, while a wrapper that relocates it breaks ARS's termination and timeout
guarantees. ARS makes no isolation claim either way.

### R8 — Workspace and storage boundaries

- v1 no-change acceptance uses a disposable, known-empty bound workspace and direct pre/post directory
  assertions. `workspace_hash` binds configuration/canonical paths only and is not content integrity.
- ARS v1 does not add a content-digest service, filesystem watcher, or new integrity authority.
- `cwd` is the caller-bound workspace, canonicalized at admission. ARS creates nothing in it, digests
  nothing in it, watches nothing in it, and **no longer refuses it for containing an AGENT's own project
  configuration file** — that file is AGENT-owned configuration, and refusing it asserted authority over a
  surface ARS does not own.
- ARS-owned **writable** surfaces are exactly two: the supervisor root (`native-runs/`,
  `native-sessions/`) through one storage seam, and the configured UDS runtime path. Read-only access is
  permitted wherever the paths live, including below `$HOME` and through symlinks and PATH shims.
- ARS never creates, writes, stages, mirrors, repairs, deletes, or **manages** AGENT auth, config, cache,
  plugin, or Session state, and never inspects those surfaces as a control surface. A child that mutates
  its own `HOME`, cache, config, or Session state during a Run completes normally; attribution is by
  process, not by filesystem diff.
- Native data lives only under explicit `native-runs/` and `native-sessions/` roots wired through one
  storage seam. Native paths never read, write, import, migrate, mirror, or collide with legacy/acpx
  stores; same textual IDs may coexist safely across roots.

### R9 — Evidence and runtime ledger

- Persist immutable Spec/launch material — value-blind, with no secret and no environment value in it —
  plus observed effective state, normalized events, bounded and statically redacted stderr, markers, result,
  permission evidence, and a redaction report. The value-blind claim covers the **structured** material ARS
  composes; the agent-authored text in the rest of that list is bounded and statically redacted, not matched
  against this Run's projected values (R15).
- One writer owns each Run event stream with monotonic sequence and bounded queue/bytes.
- The ledger supports supervision, recovery, duplicate prevention, progress, config/result proof, and
  audit. It is not a second AGENT conversation database.
- **Session identity records are small and durable by default.** Silence, age, Run completion, daemon
  restart, and caller disconnection never imply Session expiry, so retention never treats a Session
  directory as a deletion candidate at all. A live lease and quarantine are query/admission facts, not
  deletion eligibility.
- **Run retention may prune bulky evidence only after a trustworthy terminal exists**, and it must
  preserve a minimal immutable idempotency and attribution spine inside the Run directory — at least the
  durable submission, the sealed Spec/launch attribution, and the terminal result that duplicate-submit
  handling and reconciliation depend on. Event streams, bounded stderr, and other non-authority bulk
  evidence may be pruned without ever making an authenticated `request_id` dispatchable again. Any
  destructive Session-data purge, or deletion of that minimal Run spine, is a separate
  administrator/data-governance design that does not exist here.
- Evidence tiers never substitute for each other:
  - A: pre-implementation compatibility probes — context only;
  - B: direct-drive real-AGENT evidence;
  - C: `arsd` socket-path production acceptance.

### R10 — Crash containment and reconciliation

- `arsd` is the sole production supervision authority and must isolate any Run/connection exception so
  one failure cannot kill the daemon.
- Queues, events, stderr, output, concurrent Runs, per-Session activity, and socket backlog are bounded.
- Production runs under a user-level service manager/cgroup with semantics equivalent to
  `Restart=on-failure` and `KillMode=control-group`; `arsd` and every AGENT descendant share the managed
  cgroup.
- An `arsd` crash kills the descendant tree still inside that cgroup. Restart performs reconciliation
  only, never prompt replay. Graceful `killpg` and crash-time cgroup cleanup are distinct mechanisms.
- **Reconciliation is total, ordered, and fail-closed.** It runs at startup, after the registry parse and
  strictly before the socket is bound. It classifies terminal, dispatch marker, Spec, launch, and
  submission state with **absent distinguished from corrupt** — absent only on a clean no-such-path
  result, every other present-or-indeterminate state corrupt — and assigns exactly one outcome from one
  exhaustive first-match table to every state combination. A valid Spec is authoritative for
  owner/namespace/Session attribution; a valid submission is a fallback only when the Spec is unavailable
  and dispatch uncertainty must be fenced; launch records, result fields, directory names, progress,
  events, locks, and marker contents are never attribution authority. Its whole outcome vocabulary is
  *authoritative terminal*, *`unknown` + quarantine*, *pre-dispatch failed/reusable*, or *refuse to
  listen*. It never replays, never repairs, never creates or reopens a Session, never opens the registry,
  never calls ACP, and never rewrites an existing terminal.

### R11 — Compatibility, migration, and no fallback

- The caller wire is `api_version` 3, and 3 is the **only** version the daemon and the repository client
  accept or send. v3 is an unambiguous contract marker for the no-close Session model: the request drops
  `session_reuse` and `ars_session_id` for one optional `session_id`, and the operation set drops
  `session_close`. Silently reinterpreting an older frame is exactly the quiet fallback this product
  forbids.
- **There is no drain window, per-operation version matrix, dual protocol, alias, shim, or old-client
  grace period, because there is no external or production ARS client population to preserve.** ARS is
  developed and tested on one development machine. An unsupported `api_version` is refused on the
  envelope, for every operation including `server_info`, with `UNSUPPORTED_API_VERSION`.
- The **shutdown** drain is a separate mechanism and is unchanged: once shutdown begins, every frame,
  including `server_info`, is answered with `SHUTTING_DOWN`. Version admission narrows versions only; it
  never relaxes peer authentication, caller-UID policy, or owner scoping.
- Legacy Runs and Sessions stay readable through a value-blind projection (R15). Legacy Sessions carrying
  retired ARS-derived identity hashes are **refused for `session/load`** with a stable code while staying
  owner-scoped `status`/`list`-readable. The new runtime cannot honor identities it no longer
  models and must not pretend it can, and there is no silent `session/new`.
- No online schema migration, no dual-read or dual-write of old Session records, and no retention of
  close-only legacy fixture behavior as a supported surface. Archiving operator-selected evidence and
  rebuilding development Run/Session state at cutover is a separately approved operator action, never
  something source implementation performs.
- The one-time legacy-Session load refusal is a **deliberate continuity loss and a human decision**: every
  live Session at cutover ends, and continuing that work means a new Session with caller-owned context
  handoff.
- No retroactive erasure of old bytes, no dual-write, no dual-read, no shim, no alias, and no silent
  fallback in either direction. Old `/opt` artifact trees and Binding roots simply stop being referenced;
  they are not deleted, and their removal is a separate operator decision.
- Native ACP never calls acpx as driver, compatibility layer, Session store, or fallback, and no acpx
  behavior is a V4 product obligation. Legacy acpx artifacts remain readable by their existing path;
  Native artifacts are isolated. Any maintenance of the legacy line is separately approved, does not
  reopen its archived requirements, and must not reintroduce legacy role/model binding as the vNext
  product model.

### R12 — Compatibility profiles: ACP semantics only

- ARS remains Python and stdlib-only at runtime apart from the optional `native` extra. The Native client
  pins and verifies the official Python ACP SDK in the consuming environment before implementation, and
  never installs that SDK's own HTTP/WS transport extra: ARS is stdio ACP only.
- A profile is a small, source-owned, versioned value describing **how to speak ACP to a class of agent**:
  ACP protocol major; required capabilities; a forbidden-capability floor; session semantics including
  required real `session/load` and never `session/new` on a reuse path; default selector-id conventions;
  the base environment allowlist; permission-mediation semantics; and — only where evidenced — frozen ACP
  session metadata and a required permission-mode selector.
- A profile contains **no** path, version, digest, model literal, agent name, value domain, launch kind,
  artifact identity, or deployment fact. `profile_hash` therefore moves **only when ACP semantics move**,
  which is why an ordinary ARS release leaves every Session identity field untouched.
- A profile also declares its **configuration-fidelity mode** (R3), because how an agent is configured is an
  ACP semantic. It is the only place that fact may live.
- The target registry is a closed set of three: `standard-native-acp-v1` for every agent, native or
  adapter-reached; `claude-agent-acp-compat-v1` for one evidenced ACP-semantic deviation — frozen session
  metadata sent on **both** `session/new` and `session/load`, plus a required permission-mode selector proven
  by exact readback; and `cursor-native-acp-v1` for one evidenced configuration-fidelity deviation —
  `model-only`, with no independent effort selector. Every other frozen term of the Cursor profile equals the
  standard contract, and adding the mode moved no existing profile's `profile_hash`, so no live Session
  identity changed.
- A profile may also declare **one launch-permission policy id** (R7), because how an agent must be
  configured to honour a permission decision before it acts is a permission-mediation semantic. It is an id
  from a closed source-owned set, keyed by the capability family it enforces and never by an agent name; the
  profile that selects it is where the agent-keyed choice lives.
- A `-v<N>` suffix stays load-bearing: the id carries the ACP protocol generation, profile construction
  refuses a contract whose frozen major disagrees with it, and a future `…-v2` is a separate profile with
  its own registrations and Sessions, never a revision of this one.
- **An adapter command is not a profile.** A non-ACP CLI reached through an independently installed ACP
  adapter is an operator deployment fact: it is a registry `command`, registered against the standard
  profile. Admitting a *new* compatibility profile requires all three of: a cited, reproducible
  observation at the ACP layer; a demonstration that the deviation cannot be expressed by live discovery,
  exact readback, a selector-id hint, or an operator environment value; and review.
- Adding or revising a profile requires discovery evidence from a real non-prompt ACP `initialize`
  exchange, a revision bump, and independent review. The ACP `agentInfo.version` and an external CLI
  `--version` are separate facts and neither may be assumed equal to the other.

### R13 — Agent registry and preserved command semantics

**One operator-owned TOML file, supplied by a required `--agents-file` daemon flag, read exactly once at
daemon startup into an immutable in-memory snapshot. Operators replace it atomically; a replacement takes
effect at the next daemon start.**

| Layer | Owner | Carries | Never carries |
|---|---|---|---|
| ACP compatibility profile | ARS source, under review | ACP semantics (R12) | any path, version, digest, model literal, agent name, or deployment fact |
| Agent registry entry | operator, editable at will | `agent_id` → command, args, environment declarations, selector-id hints, capability narrowing, optional epoch | any digest, approved version, promotion state, receipt, capability requirement, protocol version, mediation pair, or transport |
| Sealed per-Run Spec + launch snapshot | one Run | the projection of profile × entry × request, taken once before spawn | anything re-read after sealing; **any environment value** |
| Observed evidence | one Run | what was resolved and observed (R14) | anything that gates admission or blocks continuity |

**Complete entry field set, closed:** `profile` (required), `command` (required), `args`, `mediation`,
`env_passthrough`, `env_overlay`, `model_selector`, `effort_selector`, `forbidden_capabilities`,
`session_epoch`. Nothing else. Unknown keys are refused at any level, and `transport` is refused as an
unknown key. Grammar, bounds, refusal rules, and worked examples are normative in
[`docs/design/agent-registry.md`](../design/agent-registry.md).

**Deliberately absent, each for a stated reason:** `transport` (v1 is stdio by definition; a one-valued key
is remote scaffolding); any secret slot (ARS resolves no credentials, R15); `version_probe` as schema (it
is an operator diagnostic, not a per-Run gate); registered model/effort domains (live discovery is the
domain authority, R3); default model/effort (the caller supplies both per Run); expected `agentInfo` fields
(evidence, R14); and any digest, artifact path, tree hash, ownership, or mode expectation (the entire
retired layer).

**Startup semantics.** Parse once → reconcile → bind and accept. The registry is never opened again for the
daemon's whole lifetime, so the Run, spawn, finalization, and reconciliation paths perform **zero**
registry filesystem access and two concurrent Runs can never resolve different registry contents. A
serving daemon cannot be re-pointed.

**Config hygiene, explicitly not attestation.** ARS resolves the registry path, follows symlinks, and
requires the resolved target to be a regular file that is not group- or world-writable. A dotfiles symlink
below `$HOME` therefore works; a file anyone can edit does not. This is ARS declining to take orders from a
world-writable file, and it is bounded to *its own configuration file*.

> **ARS performs no ownership, mode, ancestor, symlink, or digest check on `command`, on its ancestors, or
> on anything the AGENT subsequently loads.** That sentence is the boundary reset.

**Registered command semantics are preserved exactly.** `argv[0]` is the declared `command` string,
byte-for-byte — a bare name stays a bare name, exactly as a shell would pass it. The exec image is located
by ordinary `execvp`-style lookup over the **child's** projected `PATH` when `command` is a bare name, and
by the declared absolute path otherwise. No `executable=` override, no `/proc/self/fd/N` image, no realpath,
and no pre-flight resolution gate — a pre-flight lookup would add a second resolution that can disagree
with exec, a TOCTOU window, and a false-refusal risk. Version-manager and package-manager shims, symlink
farms, package-relative resolution from the real script location, multicall `argv[0]` dispatch, and an
agent's own self-update and self-relaunch logic all keep working. ARS classifies the exec failure itself:
`ENOENT → COMMAND_NOT_FOUND`, `EACCES → COMMAND_NOT_EXECUTABLE`, anything else `→ SPAWN_FAILED`. These are
ordinary configuration errors — "you upgraded and the shim moved" — and must read as such, never as a
security refusal.

**The honest cost of read-once.** An *agent upgrade behind an unchanged registered command* — same PATH
name, repointed shim, reinstalled symlink target, new version at the same absolute path — needs no restart
and no ARS action at all, and an existing Session still reuses through a real `session/load`. A *registry
edit* needs a daemon restart, which means draining in-flight Runs first. That restart is a service action,
not a promotion: no measurement, no manifest, no acceptance receipt, and no re-canary.

**A restart is not a continuity cut, and an identity change is.** No Session identity field derives from
registry bytes, mtimes, digests, command paths, or observed runtime facts, so an *identity-preserving*
edit — `command`, `args`, environment declarations, `mediation`, selector hints, capability narrowing —
invalidates no Session and the next Run reuses normally. Reuse stops only when the operator changes a
semantic identity choice: adding or changing `session_epoch`, which is exactly R4's escape hatch, or
targeting a different `agent_id` or a different `profile`, each of which is simply a different Session
identity under the symmetric equality of R4.

### R14 — Observed evidence, explicitly non-authoritative

After a successful spawn ARS records, best-effort: the declared command and exact argv (already sealed);
the first `PATH` hit for a bare command under the projected launch `PATH`, when one can be computed;
`/proc/<pid>/exe`, the image the kernel actually mapped; `agentInfo` name and version, protocol version,
and advertised capabilities; and an optional operator-run version-probe result.

Every one of those carries an explicit **non-authoritative** marker. **No code path compares any of them
against a source constant, a prior Run, a Session record, or a registry value to decide admission or
reuse.** Divergence between a path-lookup observation and the mapped image, or drift in `agentInfo` or
advertised capabilities between two Runs of one Session, is recorded — and may be emitted as a **policy
warning event** for humans and dashboards — but never refuses a Run and never blocks continuity.

**The complete set of observation-based refusals** is: protocol major mismatch; a required capability
absent; a forbidden capability present (source floor ∪ the entry's declared set); inexact or coerced
configuration readback; and, on a compatibility profile, a required permission mode not proven by readback.
These are checks against a *declared contract*, evaluated within one Run. Nothing else refuses, and none of
them is a continuity comparison against a prior Run.

An `agentInfo` self-report is not an identity in either direction: a substituted agent can report any name
it likes, and an operator-declared expected name would refuse Runs for cosmetic vendor renames.

### R15 — Environment projection: what ARS will not write, and what it does not scan

**Layers and precedence.** A filtered environment is not the interactive environment: it silently omits
proxy, certificate, agent-socket, temp-directory, and provider variables, and the resulting failures look
like agent bugs. Four layers resolve in order — a bounded source-owned base allowlist taken from the
daemon's own environment when present; operator-declared pass-through names; an operator-authored overlay
of literal values; the source-owned mediation pairs; and, only for a profile that selected a
launch-permission policy (R7), that policy's source-owned pair, applied **last**. `HOME` unchanged is what makes
the AGENT's own credential store, plugin tree, cache, session store, and user config work exactly as they
do interactively; that is necessary and not sufficient. `PATH` is the single most likely cause of "works in
my shell, fails under ARS", and its remedies are an operator-owned overlay or an absolute `command`.
`SSH_AUTH_SOCK` is deliberately **not** in the base set, because forwarding it hands the AGENT live use of
the operator's SSH keys — a real authority transfer that must be an explicit per-agent opt-in.

**Resolution happens exactly once, in memory, before sealing and before spawn.** The resolved value
carrier is ephemeral and non-serializable, accepted only by the process-spawn seam and consumed by nothing
else; the durable projection is a separate value-blind name/source/precedence shape. One resolution means
the sealed projection describes exactly which names and precedence were handed to exec, with no window in
which the daemon's own environment could change between seal and spawn, and the exec mapping stays
byte-identical if the ambient environment mutates afterwards.

**The guarantee, stated exactly.** Every environment value is sensitive regardless of key name, source
class, length, or apparent shape, so ARS never *chooses* to record one. No projected value — and no digest,
fingerprint, or length-by-value computed to represent one — may flow into an ARS **durable structured
artifact or hash input**, and the resolved value carrier may not be rendered into a log line or an exception
message. There is no secret-shaped-name heuristic: name shape is unsound in both directions and can never be
a confidentiality boundary. What the guarantee does **not** cover is free-form text the AGENT itself
authored: see the accepted consequence below.

- **Value-blind structured evidence.** Durable environment evidence records per name: the name, its source
  class, its precedence layer, and its redaction status. Mediation values are withheld too — the mediation
  id is durable, no Run record repeats its pairs. Declared-but-absent names are recorded as names only.
  Launch, Spec, request, and event hashes cover only value-blind projections: no value, value digest,
  keyed digest, length, prefix, suffix, equality token, or matcher table is hash material. Two Runs whose
  transmitted value changed may therefore share a launch hash; the hash proves the declared projection,
  not the secret.
- **There is no per-Run exact-literal guard.** ARS does not scan free-form Run text for the complete values
  it handed the child, and must not reintroduce such a scan under another name. Static **shape** redaction
  (API key / Bearer / JWT / PEM) and the sensitive-env-**key** rule remain, because neither depends on a
  per-Run value set. So do every byte/event/final-message ceiling and every categorical failure code.
- **The accepted consequence, stated rather than discovered.** An AGENT that echoes an arbitrary projected
  environment value back through a final message, an event field, a tool-call id, `agentInfo`, usage
  metadata, stderr, or the external Session id it mints may have that value **retained** in ARS evidence
  unless it matches a static credential pattern. Exact-literal matching was removed because its cost was
  real and its benefit was not: short, common layer-1 base values — `TERM`, `LANG`, `TZ`, `USER`, `HOME`,
  and `PATH` elements, and any one-character value — erased substantial ordinary evidence from Run text and
  refused otherwise-valid external Session ids. Operators who treat a projected value as a secret must reason
  about what the AGENT does with it, exactly as they already must for what the AGENT sends to its provider.
- **The external Session ID is recorded as the agent minted it**, because it must later be replayed
  unchanged. There is no sensitive-collision refusal on it.
- **Legacy records are read value-blind.** Pre-reset launch records can contain environment values. They
  are immutable historical evidence: ARS neither rewrites nor deletes them. New readers classify the schema
  **before** selecting a verifier, return a strict categorical allowlist, mark environment values withheld,
  never return raw launch/spec documents or value-bearing seal material, and never recompute a hash over a
  value-bearing record. Legacy free-form text is withheld categorically.
- **Startup and registry validation is structurally value-blind**: refusals name a stable rule and at most a
  field path or an environment **name**, never an overlay value or a raw file fragment. Successful offline validation prints only entry ids, counts, names,
  source classes, and rule outcomes.
- **Workspace binding fields remain complete literals.** The canonical workspace root and the effective
  `cwd` stay complete in the sealed Spec and stay hash-covered, even when the workspace lives under `$HOME`.
  They are independently derived authority facts, and truncating or tokenising them would break workspace
  binding, reconciliation attribution, and audit.

**Exact scope and honest limits.** The guarantee covers ARS-authored durable material and the resolved
carrier. It does **not** cover what an AGENT echoes into free-form Run text, and it does **not** erase the
operator-authored value at its source, stop the child from writing its own logs or state, stop the child from
transmitting a value to a remote service, prevent OS crash dumps or privileged process inspection, or detect
a transformed disclosure such as a substring or partial value, base64, encryption, hashing,
character-by-character fragmentation, or a semantic paraphrase. Those require containment or information-flow control and are **not claimed**.
Independently derived public facts that happen to have identical bytes are not treated as value-derived
flow. No sandboxing or unrelated hostile-code hardening is introduced.

## 4. Acceptance and staged delivery

Documentation authority precedes source. The reset landed as one documentation gate followed by three
source gates, each under its own approval; passing one never implied the next. All four are merged on
`main`; the sequence is recorded below because it is the contract each stage was accepted against, not
because work remains.

### Stage 0 — Authority alignment (documentation only)

Make the reset the tracked authority chain, retire the artifact/Binding-era authority into a cold snapshot,
bring both public READMEs onto the new boundary, and put the plan board in a state where source readiness
can be reached. It changes no source file, no test, no dependency, and no runtime surface, and merging it
authorizes no source implementation.

### Stage 1 — Fail-closed reuse and total reconciliation (architecture-neutral)

Make a reuse request structurally unable to reach `session/new`, reject every conflicting ID-bearing
callback at entry before any side effect, and give reconciliation one exhaustive first-match algorithm over
classified artifact states with absent distinguished from corrupt. No schema, digest, hash, or wire
movement: every live Session identity field stays byte-identical.

### Stage 2 — Environment-value sink boundary (architecture-neutral)

Make the sealed launch material structurally value-blind and give the resolved carrier exactly one consumer.
Strictly a confidentiality strengthening of the architecture that is live today. This stage originally also
installed an ephemeral per-Run exact-literal guard over free-form Run text; that half was **removed later**
under its own decision, for the reason recorded in R15, and the structural half is what remains.

### Stage 3 — The boundary reset

Land the operator registry read once at startup, two source profiles, value-blind sealed launch material
with once-only environment resolution, the `agent_id` caller wire, `--agents-file`, the operator
validate/doctor/inspect surface, and value-blind legacy inspection. Deleting the three per-agent profiles
from source is a **separate confirmation** on top of source-implementation approval, because introducing a
retirement capability and using it are two decisions.

### Stage 4 — The Session no-close model

Replace the artificial Session closing lifecycle with the one durable, resumable Session of R4/R5: the
optional `session_id` request field, atomic create-plus-first-Run with deterministic prospective identity,
one fully bound Session record committed before the dispatch marker, quarantine as independent evidence,
Session directories excluded from retention deletion with a minimal immutable Run idempotency/attribution
spine preserved, and `api_version` 3 as a single-version clean cutover. Runtime cutover — archiving or
rebuilding development Run/Session state and restarting the daemon — is a separate operator action after
merge, not part of the stage.

Acceptance per stage is proven by real tests, not by prose. Real-provider evidence, deployment, service
restart, migration/cutover, and publication each remain separate operator decisions after every gate.
Sachima integration is a later, separately approved integration after ARS production acceptance.

## 5. Current implementation status

Lean task state, the active plan, and open gates live in
[`docs/roadmap/current-status.md`](../roadmap/current-status.md); this section records only the coarse
source position.

- Legacy source without product authority: the v0.1.7 acpx one-shot/persistent paths are still implemented
  on `main`. Their product, runtime, and compatibility authority is retired; removing that code and the
  content describing it is separately authorized work, and only a bounded differential/comparison-test
  reference is retained.
- vNext Stage 0/1 (Native ACP core) and Stage 2 (`arsd` UDS ingress, ownership, reconciliation,
  service/cgroup containment) are implemented on `main` with their acceptance closed.
- **Authority and source are aligned on `main`.** Every requirement above, R13–R15 included, has merged
  source: the agent registry read once at startup, the four-way boundary, value-blind sealed launch
  material with once-only environment resolution, observed-evidence demotion, `api_version` 3 as the sole
  accepted caller wire, `--agents-file`, and the validate/doctor/inspect operator surface. The
  retired artifact/Binding implementation and the three per-agent profiles are deleted from source; the
  retired authority is preserved at `docs/archive/binding-era-2026-07/`.
- **Merge, publication, and deployment stay three separate facts.** Published package/release facts come
  from live GitHub Releases and PyPI; deployed/running facts come from operator-held runtime/live checks. A
  merge is not a publication, a publication is not a deployment, and none of them approves the next one.
- The lifetime of the pre-reset line is an open human decision. Implementation status is never an approval
  for the next stage.

## 6. Non-goals

Public ingress, root/TCP daemon, distributed or multi-tenant control plane, business orchestration,
Feishu/Gateway semantics, broad RBAC, per-Run Worker, arbitrary command/argv/env/config passthrough from
the wire, runtime adapter plugins, remote transport, attach-to-running-agent, plugin loading, containers,
sandboxing, ARS credential resolution, acpx fallback, shared or imported acpx sessions, cross-AGENT Session
reuse, generalized Session rebind, automatic replay/retry/resume, a workspace content-digest service, a
filesystem watcher, a second conversation database, and hostile-process sandbox claims.

The agent registry adds four more, stated so they cannot be re-read as deferred work: **artifact
installation or hosting**; an **ARS-managed AGENT home** or credential-store inspection; **ARS credential
resolution**; and any **attestation, artifact-integrity, or isolation claim**. There is no forced or
unvalidated promotion path, because there is no promotion; and no ARS-internal privilege escalation,
because there is no artifact root to prepare.

## 7. Authority and archive rule

This PRD is the product requirement authority for new development. Architecture and module design live in
`docs/design/`; the operator-facing registry contract is `docs/design/agent-registry.md`. Implementation
sequencing lives only in the board and `docs/plans/active/`.

Documents under `docs/archive/`, `docs/plans/archive/`, and `docs/roadmap/archive/` are retained history.
They cannot approve work, redefine this PRD, or serve as default agent context.

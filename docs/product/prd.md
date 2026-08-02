---
title: "agent-run-supervisor vNext PRD"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-01
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
→ rediscover effort
→ set requested effort
→ exact requested == effective readback
→ persist EffectiveRunState
→ prompt
```

Missing capability, unadvertised value, alias/coercion, stale option set, failed set, or inexact readback
produces zero Turn and no prompt. Literal `max` must never be downgraded to `high` or another value.

**The live-advertised option set is the domain authority.** No source-frozen `registered_models`,
`allowed_efforts`, or selector value domain gates admission: "an unadvertised value ⇒ zero Turn, no
prompt" is checked against what the running agent advertises right now, which is strictly stronger than
the same check against a constant frozen months earlier. A profile or registry entry may carry a selector
**id** hint; it never carries a value domain.

### R4 — Session continuity, closed start plan, and between-Run switching

- v1 is process-per-Run; the AGENT process lifetime is contained within one Run.
- One ARS Session binds one external AGENT Session ID plus the complete identity field set of R13's
  registry model: `agent_id`, profile id/revision/hash, owner/namespace, `workspace_hash`, and the
  optional operator-controlled `session_epoch`. The external AGENT remains conversation/context authority.
- Admission derives a **closed start plan** from the immutable request before any Session-store recovery
  behavior. A reuse request opens the named Session **existing-only**: an absent record fails, a corrupt
  record fails, and neither branch creates a record. Reuse then validates the full binding with the
  load-time gate **before** acquiring the lease and requires a non-empty stored external ID. Only then may
  it load. A new-session plan is constructible **only** from a request whose reuse intent is "none", and
  it is the only path allowed to create a Session record without an external ID.
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
caller-authorized Run linked by `retry_of_run_id`; it never rewrites the original terminal fact. There is
no unquarantine tool.

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
- A compatibility profile owns ACP launch/config semantics, not business authorization.
- Registered ACP permission/filesystem/terminal requests map to deterministic allow/deny decisions and
  redacted mediation evidence. Unknown operations deny by default.
- Mediation environment values are source-owned in **key and value**, keyed by the capability family they
  mediate. A registry entry may select one binding id or none; it can never author a pair, a key, or a
  value, and there is no "mediation off". Reserved mediation keys are global across every registered
  binding; a colliding registry fails the startup parse and **the daemon refuses to listen**.
- A real denied-action canary is mandatory per registered agent before that agent's use; zero permission
  events prove nothing about denial.
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

- Persist immutable Spec/launch material without secrets or environment values, observed effective state,
  normalized events, bounded/redacted stderr, markers, result, permission evidence, and a value-blind
  redaction/suppression report.
- One writer owns each Run event stream with monotonic sequence and bounded queue/bytes.
- The ledger supports supervision, recovery, duplicate prevention, progress, config/result proof, and
  audit. It is not a second AGENT conversation database.
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

- The caller wire moves to `api_version` 2, because the primary selector's meaning changes: `profile_id`
  stops selecting a launch and `agent_id` starts. Silently reinterpreting a v1 frame is exactly the quiet
  fallback this product forbids.
- The drain window is defined **per operation** on the creating-versus-non-creating axis. Of the eight
  operations, only `submit` is refused at `api_version: 1`. `server_info` stays **accepted** because it is
  the version/capability discovery operation — refusing it would leave a v1 caller unable to discover
  *that* it must upgrade — and during the window it reports the supported version set including 2.
  `run_cancel` and `session_close` are state-mutating but non-creating, owner-scoped, and only ever narrow
  what is running; they stay accepted so in-flight v1 Runs can be stopped and their Sessions closed. The
  window exists to drain, not to operate.
- The **shutdown** drain is a separate mechanism and is unchanged: once shutdown begins, every frame,
  including `server_info`, is answered with `SHUTTING_DOWN`. The version drain narrows versions only; it
  never relaxes peer authentication, caller-UID policy, or owner scoping.
- Legacy Runs and Sessions stay readable through a value-blind projection (R15). Legacy Sessions carrying
  retired ARS-derived identity hashes are **refused for `session/load`** with a stable code while staying
  owner-scoped `status`/`list`/`close`-readable. The new runtime cannot honor identities it no longer
  models and must not pretend it can, and there is no silent `session/new`.
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

- ARS remains Python and stdlib-only at runtime. The Native client pins and verifies the official Python
  ACP SDK in the consuming environment before implementation.
- A profile is a small, source-owned, versioned value describing **how to speak ACP to a class of agent**:
  ACP protocol major; required capabilities; a forbidden-capability floor; session semantics including
  required real `session/load` and never `session/new` on a reuse path; default selector-id conventions;
  the base environment allowlist; permission-mediation semantics; and — only where evidenced — frozen ACP
  session metadata and a required permission-mode selector.
- A profile contains **no** path, version, digest, model literal, agent name, value domain, launch kind,
  artifact identity, or deployment fact. `profile_hash` therefore moves **only when ACP semantics move**,
  which is why an ordinary ARS release leaves every Session identity field untouched.
- The target registry holds exactly two profiles: `standard-native-acp-v1` for every agent, native or
  adapter-reached, and `claude-agent-acp-compat-v1` for one evidenced ACP-semantic deviation — frozen
  session metadata sent on **both** `session/new` and `session/load`, plus a required permission-mode
  selector proven by exact readback.
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
not a promotion: no measurement, no manifest, no acceptance receipt, no re-canary, and no Session
invalidation, because no Session identity field derives from registry bytes.

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

### R15 — Environment projection and ARS sink non-persistence

**Layers and precedence.** A filtered environment is not the interactive environment: it silently omits
proxy, certificate, agent-socket, temp-directory, and provider variables, and the resulting failures look
like agent bugs. Four layers resolve in order — a bounded source-owned base allowlist taken from the
daemon's own environment when present; operator-declared pass-through names; an operator-authored overlay
of literal values; and the source-owned mediation pairs, applied **last**. `HOME` unchanged is what makes
the AGENT's own credential store, plugin tree, cache, session store, and user config work exactly as they
do interactively; that is necessary and not sufficient. `PATH` is the single most likely cause of "works in
my shell, fails under ARS", and its remedies are an operator-owned overlay or an absolute `command`.
`SSH_AUTH_SOCK` is deliberately **not** in the base set, because forwarding it hands the AGENT live use of
the operator's SSH keys — a real authority transfer that must be an explicit per-agent opt-in.

**Resolution happens exactly once, in memory, before sealing and before spawn.** The resolved value
carrier is ephemeral and non-serializable, accepted only by the process-spawn seam and by the per-Run text
guard; the durable projection is a separate value-blind name/source/precedence shape. One resolution means
the sealed projection describes exactly which names and precedence were handed to exec, with no window in
which the daemon's own environment could change between seal and spawn, and the exec mapping stays
byte-identical if the ambient environment mutates afterwards.

**The guarantee, stated exactly.** Every environment value is sensitive regardless of key name, source
class, length, or apparent shape. No complete projected literal — and no digest, fingerprint,
length-by-value, or other metadata computed to represent that value — may flow into an ARS durable
artifact, hash input, log, exception or error message, event stream, inspect response, or daemon API
response. There is no secret-shaped-name heuristic: name shape is unsound in both directions and can never
be a confidentiality boundary.

- **Value-blind structured evidence.** Durable environment evidence records per name: the name, its source
  class, its precedence layer, and its redaction status. Mediation values are withheld too — the mediation
  id is durable, no Run record repeats its pairs. Declared-but-absent names are recorded as names only.
  Launch, Spec, request, and event hashes cover only value-blind projections: no value, value digest,
  keyed digest, length, prefix, suffix, equality token, or matcher table is hash material. Two Runs whose
  transmitted value changed may therefore share a launch hash; the hash proves the declared projection,
  not the secret.
- **Ephemeral per-Run literal guard.** Every non-empty final projected value creates a per-Run guard that
  denies that literal at every ARS-owned textual, event, log, error, storage, and API boundary, in both
  the Python string form and the actual exec byte form. Overlapping values are matched longest-first and
  the result is rescanned; dynamic keys and values in structured child-controlled data are guarded
  recursively; a key collision suppresses the enclosing record rather than overwriting one. If safe
  replacement cannot be established, the boundary suppresses the whole field or record and emits only a
  stable categorical withholding marker. Only coarse sink-local counts may be recorded — matched
  occurrences, suppressed fields, suppressed records — never a value, a hash of a value, or a
  length-by-value. **Confidentiality wins over evidence completeness:** there is no minimum secret length
  and no value is waived because redaction is inconvenient.
- **The evidence cost is operator-visible and must be documented, not discovered.** Short, common layer-1
  base values — `TERM`, `LANG`, `TZ`, `USER`, `HOME`, and `PATH` elements — are in the guard's literal set,
  so guarding them **will erase substantial evidence** from Run text. That is accepted deliberately: the
  coarse suppression counters make the loss measurable rather than invisible, and the remedy for
  untriageable Runs is an evidence-model decision about which names belong in layer 1 — never a per-value
  length hint, prefix, or "short value" exemption, which this requirement forbids.
- **The external Session ID cannot be redacted**, because it must later be replayed unchanged. A collision
  with a projected literal is therefore refused categorically before the ID is bound, persisted, exposed,
  or prompted against.
- **Legacy records are read value-blind.** Pre-reset launch records can contain environment values. They
  are immutable historical evidence: ARS neither rewrites nor deletes them. New readers classify the schema
  **before** selecting a verifier, return a strict categorical allowlist, mark environment values withheld,
  never return raw launch/spec documents or value-bearing seal material, and never recompute a hash over a
  value-bearing record. Legacy free-form text is withheld categorically.
- **Startup and registry validation is structurally value-blind**, because it happens before a per-Run
  guard exists: refusals name a stable rule and at most a field path or an environment **name**, never an
  overlay value or a raw file fragment. Successful offline validation prints only entry ids, counts, names,
  source classes, and rule outcomes.
- **Workspace binding fields are deliberately outside the guarded set.** The canonical workspace root and
  the effective `cwd` remain complete literals in the sealed Spec and remain hash-covered, even when the
  workspace lives under `$HOME`. They are independently derived authority facts, not environment-value
  flow, and guarding them would break workspace binding, reconciliation attribution, and audit.

**Exact scope and honest limits.** The guarantee covers ARS-owned persistence and every externally exposed
ARS daemon/CLI/log/error/event projection. It does **not** erase the operator-authored value at its source,
stop the child from writing its own logs or state, stop the child from transmitting a value to a remote
service, prevent OS crash dumps or privileged process inspection, or detect a transformed disclosure such
as a substring or partial value, base64, encryption, hashing, character-by-character fragmentation, or a
semantic paraphrase. Those require containment or information-flow control and are **not claimed**.
Independently derived public facts that happen to have identical bytes are not treated as value-derived
flow. No sandboxing or unrelated hostile-code hardening is introduced.

## 4. Acceptance and staged delivery

Documentation authority precedes source. The reset lands as one documentation gate followed by three
source gates, each with its own approval; passing one never implies the next.

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

Install the ephemeral per-Run literal guard and route every child-controlled or exception-controlled text,
event, log, error, storage, and API boundary through it. Strictly a confidentiality strengthening of the
architecture that is live today. Sequencing it before the reset is deliberate: authority claims the
boundary from Stage 0 onward, so the enforcement must not lag behind the claim any longer than necessary.

### Stage 3 — The boundary reset

Land the operator registry read once at startup, two source profiles, value-blind sealed launch material
with once-only environment resolution, `api_version` 2 with the eight-operation drain matrix,
`--agents-file`, the operator validate/doctor/inspect surface, and value-blind legacy inspection. Deleting
the three per-agent profiles from source is a **separate confirmation** on top of source-implementation
approval, because introducing a retirement capability and using it are two decisions.

Acceptance per stage is proven by real tests, not by prose. Real-provider evidence, deployment, service
restart, migration/cutover, and publication each remain separate operator decisions after every gate.
Sachima integration is a later, separately approved integration after ARS production acceptance.

## 5. Current implementation status

Volatile status truth lives in [`docs/roadmap/current-status.md`](../roadmap/current-status.md); this
section records only the coarse position.

- Legacy source without product authority: the v0.1.7 acpx one-shot/persistent paths are still implemented
  on `main`. Their product, runtime, and compatibility authority is retired; removing that code and the
  content describing it is separately authorized work, and only a bounded differential/comparison-test
  reference is retained.
- vNext Stage 0/1 (Native ACP core) and Stage 2 (`arsd` UDS ingress, ownership, reconciliation,
  service/cgroup containment) are implemented on `main` with their acceptance closed.
- **Authority and released source differ deliberately.** This PRD states the V4 target. Source on `main`
  still implements the retired artifact/Binding architecture: four registered profiles, a Binding reader, a
  required Binding-root daemon flag, artifact digests, promotion, and attestation. The retired authority is
  preserved at `docs/archive/binding-era-2026-07/`; the board carries the exact authority-versus-source
  delta and the Stage 1→3 sequence that closes it.
- R14 and R15 have source now, and it is **branch-local**. Stage 1 (fail-closed reuse, total
  reconciliation) and the dynamic half of the environment-value guard are merged on `main`; the rest —
  the registry reader, the value-blind launch snapshot, observed-evidence demotion, and `api_version` 2 —
  is implemented and tested on the Stage 3 task branch and is **uncommitted, unmerged, unreleased, and
  undeployed**. R13's registry file is therefore read by no released code, and authoring one against a
  live deployment changes nothing and is not approved.
- Release/publication is not done: the published wheel predates the reset. Production cutover with its
  one-time legacy-Session load refusal, and the lifetime of the legacy `v0.5.x` line, are open human
  decisions. Implementation status is never an approval for the next stage.

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

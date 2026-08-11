---
title: "agent-run-supervisor vNext Technical Solution"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-11
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/technical-solution.md"
---
# agent-run-supervisor vNext Technical Solution

## 0. Scope and implementation status

This is the module-level design authority for new ARS work. It describes the shape of the package after the
V4 external-AGENT boundary reset. The previous mixed v0.1.7/vNext solution is preserved at
`docs/archive/pre-vnext-reset-2026-07-21/technical-solution.md` and must not direct new development.

ARS stays Python and **stdlib-only at runtime**. `tomllib` is standard library on the supported Python
floor, so the registry parser adds no dependency. The only declared dependency is the optional `native`
extra, pinned exactly to `agent-client-protocol==0.12.0` (ACP schema v1.19); its own `http` extra is never
installed, because ARS is stdio ACP only and adds no HTTP/WS transport.
The retired acpx path was removed from source.
That removal took its runtime, parser, probe, policy, role, workspace, retention, caller, and fixture
modules, its CLI leaves, and the result field named after its process exit. It was never a baseline,
surface, or obligation, and never a Native driver, fallback, or session store; no module below owes it
compatibility, and none may reintroduce one. `tools/static_safety_scan.py` refuses an import of a removed
module, an argv naming that binary, a reference to a removed repository surface, and a present-tense
capability claim in a current-authority document.

**The V4 boundary-reset source is aligned on `main`.** Its module dispositions below describe merged
source: `native_acp/runtime_binding.py` and `native_acp/attestation.py` are deleted,
`native_acp/agent_registry.py` is the one reader of the operator agents file, and the sealed launch
snapshot is value-blind. The source profile registry contract is a closed set of five. That contract is a
source fact only: it makes no publication, deployment, or runtime claim. The retired Binding-era module
design is preserved under
[`docs/archive/binding-era-2026-07/`](../archive/binding-era-2026-07/architecture-3.1-3.3.md) as cold
history.

Merge, publication, and deployment stay three separate facts. Published package/release facts come from
live GitHub Releases and PyPI; deployed/running facts come from operator-held runtime/live checks.
[`docs/roadmap/current-status.md`](../roadmap/current-status.md) carries only lean task state, the active
plan, and open gates. No tag, GitHub Release, PyPI upload, deployment, service restart, or cutover follows
from a merge. This document grants no approval, and a green verification transfers approval to nothing.

## 1. Package shape

### 1.1 Shared additive seams

| Surface | Responsibility |
|---|---|
| `managed_process.py` | spawn a live supervised child; expose identity/stdin/stdout/stderr/wait/terminate/kill/reap; preserve bounded behavior; accept the resolved environment only at the spawn seam and never format it |
| `exit_classifier.py` / `result.py` or Native superset boundary | losslessly carry `completed/failed/cancelled/timed_out/unknown`; persist `retryable=false` for `unknown`; accept stable detail codes rather than raw child or exception text |
| `session.py` additive fields | Native Session identity, external session ID, owner, profile hash, `last_effective_model/effort`, persistent quarantine, optional operator `session_epoch`; legacy serialization unchanged when fields are absent |
| existing `process_liveness.py` | full `ProcessIdentity` and fail-safe liveness classification — **unchanged by the reset** |
| existing `event_store.py`, `redaction.py` | atomic files, bounded projection/evidence primitives; reused through explicit Native roots |

`runner.execute_subprocess` and `SubprocessOutcome` are **removed**, with the runtime they served. Their
stdout consumer and wait-before-return contract could not carry Native ACP, and no Native module may
reintroduce one.

### 1.2 `native_acp/` package — target module map

| Module | Responsibility |
|---|---|
| `agent_registry.py` **(new)** | the only reader of the operator agents file: strict `tomllib` parse, bounded validation, typed `REGISTRY_*`/`ENTRY_*`/`MEDIATION_KEY_COLLISION` refusals, **one read per daemon lifetime** into an immutable snapshot, zero per-Run filesystem access, and the config-hygiene check (resolve symlinks; require a regular file that is not group- or world-writable) |
| `profile.py` | `AcpCompatProfile` + `AgentInstance` + a **five-entry** registry (`standard-native-acp-v1`, `claude-agent-acp-compat-v1`, `codex-agent-acp-compat-v1`, `cursor-native-acp-v1`, `reasonix-agent-acp-compat-v1`) + the source-owned mediation binding table and its global `RESERVED_MEDIATION_KEYS`. A profile freezes ACP semantics only: protocol major, required and forbidden capabilities, session semantics, the declared configuration-fidelity mode and its selector-id conventions, the base environment allowlist, mediation semantics, and — only where evidenced — frozen session metadata and a required permission-mode selector whose required value is one frozen literal or is computed per Run from the Run's frozen grant by one of the closed source-owned grant-driven policies (`required_permission_mode_for`). No executables map, wrapped artifacts, binding slots, probe-as-gate, closure predicate, launch kind, or per-agent value domain |
| `agent_registration.py` | the typed operator registry **entry** value and its bounded grammars — command, argv tokens, environment declarations, mediation selection, selector-id hints, capability narrowing, optional epoch. **Pure**: no filesystem access, so the single reader of the agents file stays `agent_registry.py` |
| `spec.py` | versioned `AgentRunRequest`; immutable `AgentRunSpec`/`spec_hash`; the sealed **launch snapshot** that replaces `ResolvedLaunchSpec`; the ephemeral non-serializable `ResolvedEnvironment`; the durable value-blind `EnvProjection`; the observed-state record. `launch_spec_hash` on the Spec is **retained and load-bearing**. No sealed runtime identity, no runtime provenance, no artifact descriptor |
| `storage.py` | the only constructor seam for `native-runs/` and `native-sessions/`; write-once discipline; bounded no-follow classifying readers returning valid/absent/corrupt while retaining the existing terminal trichotomy; the one sanctioned writer for free-form Run text, which judges the type before writing |
| `driver.py` | ACP wire/state machine over a supplied `ManagedProcess`; never spawns or selects policy/profile. Accepts a typed load plan and the exact stored ID; `load_session()` keeps returning `None`, the expected ID is set before the call, and options are seeded from the load response |
| `config_fidelity.py` | exact-or-zero configuration and between-Run switch/rollback state machine; the two **configuration-fidelity modes** and the shared `EFFORT_NOT_APPLICABLE` sentinel; option domains come from **live discovery**, with no source-domain preflight |
| `launch_permissions.py` **(new)** | the closed set of source-owned launch-permission policies a profile may select, each keyed by the capability family it enforces. Compiles one deterministic document from the Run's frozen grant, digests it, materializes it privately per Run under the supervisor root, and removes it. No dynamic approval, no path-level write policy, no positive write/execute grant, and no agent-named literal |
| `client.py` | official SDK callback implementation. Synchronous fail-closed identity rejection at callback entry for every ID-bearing update, permission, filesystem, terminal, and session-scoped elicitation surface, using exact pinned SDK signatures rather than varargs; categorical violations carry no IDs |
| `permissions.py` | frozen-grant → default-deny mediation; deterministic mediation evidence; every decision reason is ARS-authored and stable |
| `events.py` | ACP update normalization into the caller-stable event families without copying thought/raw bulk bodies |
| `event_writer.py` | one event-loop-owned **Bounded Serial Ledger** per Run: atomic actual-sequence allocation; one retained canonical newline-terminated NDJSON `str`; exact UTF-8 count/byte charging through the durable `append_text` acknowledgement; separate admission/persistence ticket outcomes; FIFO absolute producer deadlines checked before room or growth; progress-earned policy rungs; value-only cancellation-isolated observers; one serial consumer; absorbing ordinal-ranked failure; and a private healthy-close stop distinct from failure drain |
| `run_task.py` | admission assembly, the closed start plan, lease, process/driver coordination, dispatch markers, timeout/cancel, finalization, quarantine, top-level exception boundary; once-only environment resolution; Spec-then-launch write order preserved; `agentInfo` name/version recorded as evidence and gating nothing |

**Deleted by the reset:** `runtime_binding.py` and `attestation.py`. No module may re-create artifact
identity, promotion, digests, ownership or mode gates, or credential-root inspection under another name.

### 1.3 `arsd/` package

| Module | Responsibility |
|---|---|
| `server.py` | asyncio UDS accept loop, `SO_PEERCRED`, finite backlog, per-connection isolation; UDS create/chmod/replace/unlink as the second writable surface |
| `protocol.py` | bounded JSON frames, mandatory `api_version`, **single-version** envelope admission (exactly 3; no per-operation matrix and no drain window, because no client population exists), the seven-operation set, and the submit wire mapping with one optional `session_id` |
| `handlers.py` | submit/status/events/cancel and Session status/list with owner checks; Session creation is part of `submit` and there is no Session-close operation; `server_info` reports the supported version set; responses expose only allowlisted fields and never raw stored objects or exceptions |
| `admission.py` | durable submission/idempotency records, keyed admission locks, typed terminal-result inspection; the strict submission writer/validator shared with reconciliation; **pure in-memory** agent resolution against the startup snapshot with zero filesystem access; value-blind digest material; the forbidden runtime-selection field set |
| `reconcile.py` | startup-only, ordered, exhaustive, fail-closed reconciliation (§9); no prompt replay, resume, or repair |
| `client.py` | typed local caller for Hermes/CLI; explicit connect/close, no silent reconnect or replay |
| `service_unit.py` | pure data→text renderer for the user-scope service unit; never installs, enables, or starts anything |
| `operand.py` | the single operand-admission seam: the shape-checking capture behind the daemon's operand doors. The rule is unchanged by the reset and must not be duplicated; only the entry it captures moves |
| `__main__.py` | unprivileged daemon entrypoint and the side-effect-free unit-rendering mode. Startup order is strictly **parse the agents file → reconcile → bind**, with the same fail-closed discipline at every step |

No TCP, root mode, runtime plugin loader, arbitrary command adapter, per-Run Worker, endpoint abstraction,
or transport indirection is introduced. `transport` is refused as an unknown registry key.

### 1.4 Operator command surface

`cli.py`/`commands.py` carry exactly these, and no command beyond them:

| Command | Reads | Writes | Side effects |
|---|---|---|---|
| `agents validate` | the agents file | nothing | none; parse, shape, bounds, and the identical mediation-collision check the daemon applies at startup |
| `agents doctor` | the agents file; then one zero-prompt ACP `initialize` per named agent | nothing under ARS or operator state | **starts an external child**, which writes its own AGENT-owned state; reports the projected environment **name** set and an optional version probe |
| `run inspect` | `spec.json`, `launch.json` | nothing | none |

`agents doctor` is read-only with respect to ARS and operator state and never claims otherwise about the
child. There is no `promote`, no `rollback`, no `--force`, no internal privilege escalation, and no command
that installs an artifact, edits a service unit, restarts the daemon, or contacts a provider.

`run inspect` recomputes the **value-blind** launch hash for a reset-schema record after excluding exactly
one top-level field, and reports only allowlisted evidence. For a pre-reset record it classifies the
schema **before** selecting a verifier, returns immediately through the safe legacy projection, marks the
record value-bearing with environment values withheld, reports launch-seal verification as not performed,
and **never calls any hash function over value-bearing material**.

## 2. Admission data model

### `AgentRunRequest`

Wire input contains the schema version, caller namespace/owner expectation, `agent_id`, the Session choice,
workspace/resource references, requested model/effort, frozen grant reference and hashes, limits, and
evidence policy. Inputs are validated as plain, bounded values before use. `profile_id` is **removed**: the
profile is derived from the resolved registry entry.

`agent_id` passes its grammar **before any resolution**, and no request field names a command, argv token,
environment key or value, path, digest, version, or secret. A structural test asserts that those never
become fields, so the refusal is structural rather than filtered.

### `AcpCompatProfile`

A small, source-owned, versioned value: ACP protocol major; required capabilities; a forbidden-capability
floor; session semantics including required real `session/load` and never `session/new` on a reuse path;
default selector-id conventions; the base environment allowlist; permission-mediation semantics; and — only
where cited ACP-level evidence requires it — frozen ACP session metadata and a required permission-mode
selector. `profile_hash` covers exactly that, so it moves only when ACP semantics move.

A `-v<N>` profile id must freeze exactly that protocol major; construction refuses a contract whose frozen
major disagrees with the id.

### Registry entry and `AgentInstance`

The complete, closed entry field set is `profile` (required), `command` (required), `args`, `mediation`,
`env_passthrough`, `env_overlay`, `model_selector`, `effort_selector`, `forbidden_capabilities`, and
`session_epoch`. Unknown keys are refused at any level. Grammar, bounds, refusal codes, and worked examples
are normative in [`agent-registry.md`](agent-registry.md).

`AgentInstance` is the `(profile, entry)` pair every generic consumer asks, so no runtime path branches on
an agent name. Selector ids come from the instance; there is no source-domain preflight.

### `ResolvedEnvironment` versus `EnvProjection`

Two types, deliberately unequal in power:

```python
resolved = resolve_environment(
    arsd_env=admission_environment_snapshot,   # copied once, at admission step 11
    base_names=profile.base_allowlist,         # layer 1
    passthrough_names=entry.env_passthrough,   # layer 2
    overlay=entry.env_overlay,                 # layer 3
    mediation=source_mediation_pairs(entry.mediation),   # layer 4
    launch_permission=material.env_pairs if material else (),  # layer 5, LAST
)
launch_env = resolved.value_blind_projection()
managed_process.start(argv=argv, env=resolved.exec_mapping)
```

- `ResolvedEnvironment` is **ephemeral and non-serializable**: its value mapping is `repr=False`, excluded
  from equality and hashing, exposes no `to_dict`, and is accepted **only** by the process-spawn seam. It has
  exactly one consumer and no accessor that enumerates its values for any other purpose. A type and
  static-boundary test prevents it from entering any Spec, launch, event, result, log, exception, or API
  serializer.
- `EnvProjection` is the separate **durable, value-blind** shape: per name, the name, its source class, its
  precedence layer, and its redaction status, plus a resolved count, the mediation id, and the
  declared-but-absent names. Nothing else. The old `fixed_env`/`permission_env` value fields disappear from
  the launch schema, and a schema-level allowlist **rejects** them rather than ignoring them.
- Resolution happens **exactly once**, in memory, before sealing and before spawn. This replaces the
  spawn-time re-read of the ambient environment, so the sealed projection describes exactly what was handed
  to exec and the exec mapping stays byte-identical if the ambient environment mutates afterwards.
- `SSH_AUTH_SOCK` is deliberately **not** in the layer-1 base set; forwarding it is an explicit per-agent
  pass-through opt-in.
- **Layer 5 is the launch-permission pair**, present only for a profile that selected a policy and empty for
  every other profile, so their projection is byte-identical to before. Like layer 4 it is source-owned in
  key *and* value and applied last, so an operator overlay can never shadow it; a profile-construction
  invariant additionally refuses a base allowlist or mediation binding that claims the same key. Its value is
  an ephemeral local path, withheld exactly like every other value — what is durable is the **name**, its
  source class, and its precedence.

**Workspace binding fields stay complete literals.** When the workspace lives under `$HOME`, `spec.json`'s
canonical root and effective cwd contain the complete `HOME` literal as a substring and `spec_hash` covers
them. That is correct and intentional: they are **independently derived authority facts**, and truncating or
tokenising them would break workspace binding, reconciliation attribution, and audit. A test encodes this
rather than a comment.

### `SessionStartPlan`

```python
SessionStartPlan = CreateSessionPlan | LoadSessionPlan

@dataclass(frozen=True)
class CreateSessionPlan:
    # Constructible ONLY from a request carrying no session_id. The id is
    # PROSPECTIVE: nothing durable exists under it until session/new returns.
    ar_session_id: str

@dataclass(frozen=True)
class LoadSessionPlan:
    # Captured exactly from an already-existing Native Session record.
    ar_session_id: str
    external_session_id: str = field(repr=False)
```

Invariants that must hold **structurally, not by convention**:

1. `driver.new_session` is reachable only from the create match arm.
2. `CreateSessionPlan.__init__` is reachable only from the `session_id is None` admission branch.
3. The startup sequence has **no default arm** and no conversion between plan types.
4. The load arm passes the stored ID with no trimming, Unicode normalization, parsing, case conversion,
   canonicalization, or regeneration, and reads **no** ID from the response — the pinned SDK's load response
   has no session-id field at all.
5. Callback identity ordering is: compare → on unbound-or-different, record only the categorical violation
   and raise with no IDs in the text → only on match may the callback normalize, enqueue, invoke a handler,
   touch the filesystem, or formulate **any** response, including an unsupported-surface response. No
   `finally` block may service or persist a rejected callback; delivery counters that unblock shutdown may
   advance but carry no payload.
6. Session-scoped elicitation identity is read from the leaf mode's own session field, selected by
   `isinstance` over the two session-scoped leaf types. The pinned SDK's elicitation mode is a plain union
   whose leaf instance is passed directly, so there is no wrapper attribute to reach through. A
   request-scoped mode carries no session id, is simply unsupported, and no id is invented for it.

### Sealed launch snapshot

Declared command, exact argv, effective cwd, the `EnvProjection` material, the mediation id, profile
identity, registry-source evidence, and a value-blind `launch_hash`. It carries no artifact descriptor, no
interpreter identity, no digest, no generation, and no acceptance receipt. `AgentRunSpec` continues to seal
launch through `launch_spec_hash`, and `spec.json` is written **before** `launch.json` so there is never a
durable launch record without the immutable request, grant, owner, and Session identity that reconciliation
and audit require.

### `AgentRunSpec`

Immutable, exclusive-created requested fact: input/context references and hashes; caller owner/namespace and
the optional `session_id`; `agent_id` and profile/launch/schema hashes; frozen execution grant,
role/capability, workspace, MCP, and credential-**reference** hashes; requested model/effort, limits,
recovery/evidence policy; and `spec_hash` excluding generated control fields such as `run_id`/timestamps.

`to_dict()` stays an explicit projection rather than raw `asdict`, and a structural test walks every spec
dataclass field asserting it appears in the projection except the declared omit set.

**Schema versions move with the material they seal, and only then.** At the Session no-close model the
request's Session block became one optional `session_id`, so `SPEC_SCHEMA_VERSION`, `DIGEST_SCHEMA_VERSION`,
`SUBMISSION_SCHEMA_VERSION`, and the caller `api_version` all moved to **3** together — a document sealed
under the old reuse-mode shape describes an intent this runtime no longer models, and is refused rather
than reinterpreted. `LAUNCH_SCHEMA_VERSION` deliberately stayed at **2**, because the launch snapshot did
not change and a version that tracks nothing tells a reader nothing.
The disposition of the retired expected-binding-hash request/Spec field is an explicitly carried follow-up
and is **not** silently changed while moving digest material.

### `ObservedRuntime`

Observed-only: `ProcessIdentity`, agent/protocol info, capability and config
advertisements, external Session ID, discovery snapshots, exact effective model/effort, and the
non-authoritative resolution observations — declared command, path-lookup observation, mapped-image
observation, and an optional operator probe result. Each carries an explicit non-authoritative marker. It
never alters a profile, the registry snapshot, or a Spec.

## 3. Run and Session state

### Native Session record

Stable identity: `agent_id`, profile identity, external Session ID, owner/namespace, `workspace_hash`, and
the optional operator `session_epoch`. Mutable observations: `last_effective_model/effort`, last-use
timestamp, and the last `initialize` self-report. Optional safety evidence: `quarantine: null |
{reason_code, source_run_id, recorded_at}`. model/effort are not Session identity.

**There is no Session lifecycle field.** `state = open | active | closed`, `closed_at`, close reason/source,
the ephemeral/persistent flag, and reuse-mode-as-identity are deleted, not hidden: a Session exists, is
durable, and is indefinitely resumable, and no Run terminal changes that. Quarantine is independent
evidence rather than a state — a quarantined Session still exists, stays queryable, and refuses new Runs.

**Creation is atomic and never provisional.** A `submit` without `session_id` derives a prospective
`session_id` deterministically from the authenticated `(principal_id, request_id)`; the sealed
submission/Spec is the only durable pre-Session reservation. After `session/new` returns, ARS writes one
fully bound record carrying the external ID, then takes its lease, then proves configuration, then marks
dispatch. A crash before that commit leaves a terminal failed Run and no Session record at all.

Reuse requires equality on the full identity set, and comparison is **symmetric**: a record carrying an
epoch is refused by a Run with none and vice versa, which is exactly why adding an epoch for the first time
cuts existing Sessions. The load-time gate runs **before the lease is mutated and before `session/load`**,
and it requires a non-empty stored external ID; there is no `session/new` fallback anywhere on that path.

Retired identity fields — the adapter contract hash, the ARS-derived compatibility epoch, and the agent
registration hash — are deleted as identity. Records carrying them stay **status-readable** and are
**refused for load** with a stable code.

Same Session has one lease and one active Run; the lease is independent of Session existence and every
trustworthy Run terminal releases it. A quarantined Session refuses new work. v1 has no
unquarantine tool; successor work uses a new Session with caller-owned context handoff.

**Retention never treats a Session directory as a deletion candidate.** Run retention prunes bulky evidence
only after a trustworthy terminal and always preserves one centrally defined immutable
idempotency/attribution allowlist inside the Run directory, so a repeated authenticated `request_id` stays
non-dispatching after pruning.

### Native Run record

One Run owns one immutable Spec, one launch snapshot, one observed-state record, one EventWriter, zero or
one Turn, two dispatch markers, and one irreversible result. A retry is an independent Run linked by
`retry_of_run_id`.

### Bounded event ledger

The production `QueuePolicy` remains fixed at 1024 → 2048 → 4096 → 8192 admitted-unacknowledged events and
8 → 16 → 32 → 64 MiB. One durable acknowledgement earns at most one growth rung; a stalled or dead sink
earns none. The pending FIFO is separately capped by `policy.max_event_capacity`, and pending plus admitted
canonical bytes may never exceed `policy.max_queued_bytes`. Generic ledger logic reads those fields rather
than embedding the production numbers.

Acceptance is non-awaiting and commits one ticket with its actual sequence, final `ndjson_line(...)` string,
exact encoded size, and absolute `monotonic + producer_timeout_seconds` deadline. A single head timer is
sound because the timeout is constant and FIFO acceptance on a monotonic clock produces non-decreasing
deadlines; every actual pump still checks deadline equality or expiry before capacity, growth, or admission.
The consumer passes the cached string unchanged to `RunHandle.append_text`; the in-flight ticket stays
charged until that call returns successfully. `last_seq` is therefore the durable contiguous-prefix high-water
mark.

Admission and persistence are independent ticket outcomes. Their external observers store values only and
are shielded from caller cancellation; cancelling one cannot mutate the ticket, byte charge, deadline, or
close result. RunTask starts deferred observations concurrently and records the minimum original emission
ordinal across every batch, without fresh timeout windows or an independent cancellation timeout.

`close()` establishes its submit cutoff synchronously. It waits for pending deadlines, uses a private
zero-capacity healthy stop only behind every admitted ticket, and otherwise drains the already-admitted lower
prefix before failure exit. It always joins and observes the consumer. An empty never-started writer is the
only no-consumer clean case; any accepted-but-unacknowledged ticket, primary failure, or other consumer exit
fails close.

## 4. Managed process and ACP wire

```python
class ManagedProcess:
    identity: ProcessIdentity
    stdin: object      # handed exclusively to ACP SDK
    stdout: object     # handed exclusively to ACP SDK
    stderr: object     # bounded/redacted collector owned by supervisor

    async def wait(self) -> ManagedExit: ...
    def terminate_group(self) -> None: ...
    def kill_group(self) -> None: ...
    def reap(self) -> None: ...
```

The supervision layer starts a new POSIX session/process group, records identity immediately, and owns
SIGTERM→grace→SIGKILL escalation. ACP framing begins while the child is alive. There is exactly one stdout
protocol consumer, and exactly one `ManagedProcess` per Run from spawn to reap.

`RunLimits` seals six existing fields per Run. Its `turn_timeout_seconds` default is `21_600.0` (6 hours)
and its inclusive maximum is `604_800.0` (7 days). The dispatch path applies that hard bound around the
complete `NativeAcpDriver.prompt_once` operation, including the AGENT's Prompt / tool multi-loop; expiry
uses the existing process-group terminate → `cancel_grace_seconds` → kill/reap sequence. It is not a
Session timeout. A later `session/load` Run seals a new independent `RunLimits`, and changing these numeric
policy values moves no wire, request, Spec, or launch schema version.

The reset drops the descriptor-based interpreter exec so the declared `command` and `argv[0]` survive
exactly as declared, accepts `ResolvedEnvironment` only at the spawn seam, never formats the environment
mapping, and bounds stderr before any retained diagnostic output. Child-exec errno is preserved
through the process error type so the caller can classify `ENOENT`, `EACCES`, and everything else without
embedding raw exception text. Process-group and reap behavior are unchanged.

## 5. ACP and exact configuration flow

`NativeAcpDriver` receives an already-spawned process. Its success path is fixed:

```text
initialize
→ verify protocol major, required capabilities, forbidden capabilities
→ record agentInfo as EVIDENCE (gates nothing)
→ session/new (create plan) or session/load (load plan), per the closed start plan
→ read complete config options (live discovery)
→ set model
→ consume complete model-dependent options
→ [separate-selectors only] rediscover effort from that fresh set
→ [separate-selectors only] set effort
→ consume updates and exact-read the effective configuration
→ persist ObservedRuntime
→ ready-to-prompt
```

Any missing, unknown, or inexact state raises a stable pre-dispatch failure. Prompt code is unreachable until
the state machine reaches `ready-to-prompt`.

**Launch permission, decided before the wire exists.** ACP mediation decides before a side effect only when
the agent asks, and some agents do not always ask: an `agent`-mode edit can complete with no
`session/request_permission` at all, so the completion backstop sees the violation only once the file exists.
A profile may therefore select one closed **launch-permission policy id**. Before spawn, ARS compiles that
policy's deterministic document from the Run's frozen grant, writes it `0600` inside a `0700` per-Run
directory under the Run's own supervisor-root directory — created with an exclusive `mkdir` and written
through the directory's own descriptor with `O_EXCL | O_NOFOLLOW`, so an existing path or a symlink refuses
rather than being adopted — and projects the pair that points the agent at it. The agent then refuses the
side effect itself.

The slice is read-only and stays that way. The one registered policy denies `Write(**)` and `Shell(*)`
explicitly, because leaving either unclassified is what lets an edit complete unasked, and it denies no read,
so ordinary workspace reading is untouched. A Run whose frozen grant asks for a capability this backend
cannot faithfully enforce is refused **before spawn and before any prompt** with
`LAUNCH_PERMISSION_UNSUPPORTED_GRANT`, rather than being silently widened by a document that ignores the
grant or silently narrowed by one that contradicts it. `launch.json` binds the policy id and a SHA-256
content digest — never the directory, never the document — so the launched policy is auditable value-blind.
The material is removed only once the child is **proven exited and reaped**, on every path including
pre-spawn failure, cancellation, turn timeout, and emergency finalization. Removal enumerates the directory
incrementally and stops at its entry bound, because the child owns what it wrote there and the listing is
bounded by nothing ARS controls. A removal that fails is classified durably — a write-once
`launch-permission-cleanup-failed.json` carrying a stable code and the Run id, plus an event when a writer is
still open — and never as errno, path, document, or exception text. Material that a *failed materialization*
created and then could not roll back is reported under that same cleanup code rather than as a materialize
failure, because no material was returned for the Run path to classify later and the leftover is the one fact
an operator needs; a refused pre-existing target creates nothing, so it never reports one. The marker is written once, so the outer
last-resort retry clears the leftover without erasing the fact that the first attempt failed, and cleanup
hygiene never becomes the Run's terminal verdict. `PermissionBridge` and the post-completion violation
detector are unchanged: this is an earlier line, not a replacement for either.

**Two configuration-fidelity modes, declared by the profile.** `config_fidelity.py` owns both, and a profile
declares exactly one.

| Mode | Selectors | Sequence | Effective effort |
|---|---|---|---|
| `separate-selectors` (default) | model **and** effort | the full sequence above | the exact effort read back |
| `model-only` | model only; `effort_selector_id` is `None` | stops at the exact model readback; **no effort option is discovered and no effort `set_config_option` frame is ever written** | the shared `EFFORT_NOT_APPLICABLE` sentinel, `"N/A"` |

The sentinel is one source constant. A `model-only` Run must *request* it: any other requested effort is a
pre-dispatch `CONFIG_FIDELITY` failure before the first ACP frame, because silently ignoring a requested
effort is exactly the coercion R3 forbids. An operator entry may not hint an effort selector on a
`model-only` profile, and the sealed launch snapshot records `effort_selector_id: null` — naming a selector
no Run ever sets would seal a call that never happened. Rollback re-runs the Session's declared mode.

The selector value is **opaque** in both modes. A model literal such as `grok-4.5[effort=high,fast=true]` is
set and read back byte-for-byte: no code path parses it, infers an effort from it, maps a model name, or
reads an agent's ACP `mode` selector as an effort.

**Value domains are live.** Registered model sets, allowed effort sets, and selector value domains are
deleted as admission gates: the live-discovered option set is the domain authority and exact literal readback
is the proof. An unadvertised value still yields zero Turn and no prompt — now checked against what the
running agent advertises, which is why "the agent added a model today" is a non-event for ARS.

**The post-`initialize` identity gate is narrowed to a contract check.** It verifies protocol major, required
capabilities, and forbidden capabilities (source floor ∪ the entry's declared set), and on a compatibility
profile it proves the required permission mode by exact readback. The required mode is the profile's answer
for this Run's sealed frozen grant — `required_permission_mode_for(grant_capabilities)` — either a static
literal (`default` for Claude or `ask` for Reasonix) or one of two closed source-owned grant-driven policies:

- `codex-agent-acp-compat-v1` requires `read-only` iff the grant is a subset of `{read, search}`, otherwise
  `agent`; `agent-full-access` is advertised evidence only and is unreachable from the policy;
- `cursor-native-acp-v1` revision 3 requires `ask` for that same subset class, otherwise `agent`.

`reasonix-agent-acp-compat-v1` uses the same separate-selector sequence but sets and exactly reads back
`tool_approval=ask` before model and effort on every `session/new` and `session/load`. It never selects
Reasonix's advertised `auto` or `yolo` values, and it leaves `work_mode` outside the profile.

The machine sets the mode **before** the model, requires exact readback immediately, and re-proves the mode
after the model set under model-only fidelity. Under separate-selector fidelity, it configures model and
effort and re-proves the mode once at the post-effort readback. Because the machine is constructed per Run
from the sealed grant, the mode is recomputed and re-proven on every Run, `session/new` and `session/load`
alike. Both grant-driven modes are cooperative mitigations, not sandboxes or permission
guarantees. The gate does **not** compare `agentInfo.name` or `.version` against anything. The ACP-reported
version and an external CLI `--version` remain separate facts, and no code path may assert they are equal.

For reuse, `session/load` receives the stored external ID byte-unchanged, its response seeds the fidelity
machine, no identity is read from the response, and `session/new` is structurally unreachable. Switch
rollback targets the prior effective pair and is itself exact-readback gated.

## 6. Dispatch, finalization, and reconciliation

`RunTask` exclusively creates `prompt-dispatch-started` immediately before the wire write and
`prompt-accepted` after a successful write. It additionally records the configuration-switch window as
three bounded categorical markers — `config-switch-started` before the first `session/set_config_option`,
then `config-proven` or `config-rollback-proven` — so a crash between the Session record and the dispatch
marker is classifiable (architecture §5.1). A started-but-unproven switch quarantines the Session on both
the create and the reuse path. Finalization prioritizes durable reconciliation facts over
ordinary process exit classification.

| Condition | Result | Session |
|---|---|---|
| pre-dispatch failure | `failed` | reusable unless switch rollback failed |
| trustworthy ACP terminal | matching terminal state | reusable unless continuity is disproven |
| dispatched; supervisor proves matched child abnormal exit while observation remained intact | `failed` | quarantined |
| dispatched; observation lost / no trustworthy terminal | `unknown`, `retryable=false` | quarantined |
| external session identity violation observed after dispatch | `unknown`, `retryable=false` | quarantined |

`classify_exit` alone cannot mark a dispatched/no-terminal Run completed or cancelled. Restart preserves
existing terminal results, reconstructs only from trustworthy terminal events, and maps uncertain started
Runs to `unknown/quarantined/retryable=false`. It never calls prompt.

## 7. Credentials and the environment boundary

**ARS resolves no credentials.** There is no credential resolution anywhere on the Native path;
`credential_refs` are caller-supplied **names** recorded as admission evidence and grant material, checked
for exact match against the required set, and never resolved to values or placed in the child environment.
ARS credential resolution is a future, separately designed capability — it would have to define the
source-owned slot-name → env-key mapping, the reserved-key collision rule, the provider authorization path,
and a no-persistence proof — and **no placeholder for it exists in any schema**.

Deleted with the reset: the credential-root slot, the managed-credential-root concept, auth-file inode and
mode inspection, credential-root permission enforcement, config-file absence checks, and every
credential-root refusal. ARS does not know what an AGENT's credential file is called and must not.

**No per-Run literal guard.** ARS previously constructed an ephemeral per-Run guard from every non-empty
final projected value and denied that exact literal at every ARS-owned textual, event, log, error, storage,
and API boundary. That guard — `RunTextGuard`, `SafeText`, its counters, its markers, and the handler-level
`arsd/safe_logging.py` filter — is **removed**, together with the external-Session-id sensitive-collision
refusal and `ResolvedEnvironment.sensitive_values()`. Nothing replaces it, and it must not be reintroduced
under another name.

**What the boundary still guarantees.** The scope narrows to what ARS itself authors or seals:

| Surface | Rule |
|---|---|
| structured launch/spec/hash material | per name: the name, its source class, its precedence layer, and its redaction status. **No value, value digest, keyed digest, length, prefix, suffix, or equality token is hash material**, and the retired value-bearing env fields are rejected by a schema-level allowlist rather than ignored |
| the resolved environment carrier | ephemeral, non-serializable, one consumer (process spawn). A source scan refuses any `repr` of, or f-string interpolation over, an environment mapping anywhere in `src/`, and `ManagedProcess` never formats it |
| free-form Run text | the static shape redactor (API key / Bearer / JWT / PEM) plus the existing byte/event/final-message ceilings. One sanctioned storage seam, which judges the type before writing |
| mapping and env-key projections | a key whose *name* marks it sensitive is replaced with `[REDACTED]`, independently of value shape |
| spawn, ACP, callback, timeout, cleanup, and SDK exceptions | translate known failures to stable codes; daemon and diagnostic-CLI outer boundaries replace any otherwise-unhandled exception with a stable code. Raw `repr`, raw args, raw frame bytes, raw traceback locals, and raw environment mappings are never emitted |
| SDK root logging | `_RootExceptionDetailRedactor` stays: the SDK's module-level `logging.exception` records keep their message and the exception's class name and lose the detail. It is a dependency-containment seam, not the removed literal guard |
| pre-reset value-bearing records | classify the schema **before** selecting a verifier; return a categorical allowlist; withhold environment fields, raw documents, and value-bearing seals; **never** call a launch-hash recomputation on a legacy record |
| startup and registry validation | refusals name a stable rule and at most a field path or an environment **name**, never an overlay value or a raw file fragment |

**The accepted consequence, stated rather than hidden.** An AGENT that echoes an arbitrary environment value
back through free-form Run text — a final message, an event field, a tool-call id, `agentInfo`, usage
metadata, stderr, or the external Session id it mints — may have that value **retained** in ARS evidence
unless it matches a static credential pattern. That is a deliberate trade: exact-literal matching erased
substantial ordinary evidence (`TERM`, `LANG`, `USER`, `HOME`, `PATH` elements, and any one-character value)
and refused otherwise-valid Session ids, and the erasure was not worth its cost. Operators who treat a
projected value as a secret must reason about what the AGENT does with it, exactly as they already must for
what the AGENT transmits to its provider.

**Honest limits, unchanged in kind.** ARS does not erase the operator-authored value at its source, stop the
child from writing its own logs or state, stop the child transmitting a value to a remote service, prevent OS
crash dumps or privileged process inspection, or detect a transformed disclosure. Those require containment
or information-flow control and are not claimed.

The permission and workspace evidence rules are otherwise unchanged: the caller-provided grant is frozen into
the Spec, the bridge maps only registered ACP operations and denies unknown classes, and decisions record
operation family, decision, stable reason, and correlation with no raw secret or payload. Production
acceptance still requires a real denied-action canary with a recorded deny, a confirmed failed operation, an
absent sentinel, and direct pre/post listing of a disposable known-empty workspace. `workspace_hash` remains
a canonical binding hash only; v1 adds no content-digest service, watcher, or sandbox claim.

## 8. Storage seam and free-form Run text

`native_acp/storage.py` constructs all Native `SessionStore(base_dir=.../native-sessions)` and
`EventStore(base_dir=.../native-runs)` instances. No other Native module constructs a legacy-root store.
Tests seed poisoned same-ID legacy records and prove Native never reads or mutates them; directory listings
and bytes remain unchanged.

Files and directories use `0600`/`0700`, with exclusive create or atomic replace as appropriate. One bounded
writer owns each event stream. ARS never writes a projected environment value here out of the resolved
carrier, and cookies, authorization headers, and unredacted bulk payloads are never retained. Free-form Run
text an AGENT authored is a different case: it is bounded and statically redacted, not matched against this
Run's projected values, so an echoed value can persist (§7).

**One seam for free-form Run text.** `storage.write_run_text` is the only sanctioned Native writer for it,
so the set of places that can create such an artifact stays enumerable, and it judges the type before
writing — `session.json` and `stderr.log` are durable text, and an object with a hostile `__str__` must not
reach a serializer. Reset-schema readers expose explicit allowlists; terminal builders accept stable detail
codes rather than raw child or exception text.

**The workspace binding fields keep their complete literal text.** `spec.json`'s canonical workspace root and
effective `cwd` stay covered by `spec_hash`, even when the workspace lives under `$HOME` and the literal
therefore contains the complete `HOME` value as a substring. They are independently derived authority facts,
and truncating or tokenising them would break workspace binding, reconciliation attribution, and audit.

**Two writable surfaces, enumerated.** The supervisor root through this seam, and the configured UDS runtime
path in `arsd/server.py`. The operator agents file is operator storage that ARS opens read-only, exactly once
at startup, and never creates, writes, repairs, or migrates. There is no Binding root, no artifact tree, and
no `attestation.json`. `run_task.py`, `reconcile.py`, and finalization have **no** registry read path at all,
so a registry edit can never re-point work that is already sealed — and cannot affect a serving daemon at
all.

## 9. Exhaustive reconciliation

Reconciliation runs at startup, **after the registry parse and strictly before the socket is bound**. It is
idempotent, never opens the registry, never re-resolves a command, never calls ACP, never mints or unlinks a
lock, and never rewrites an existing terminal.

**Classification first, all inputs, before any write.** The JSON reader uses `lstat` only to distinguish a
clean absence, then opens with `O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK`, applies `fstat`
regular-file and size checks, and performs a bounded read from that descriptor. A race, symlink, FIFO,
directory, oversize file, short or failed read, or any error after observed presence is **corrupt** — never a
second chance to become absent, and the open never blocks. Dispatch is **present** when `lstat` finds
*either* marker name, regardless of contents or file type: a symlink, a directory, a malformed marker, and
any indeterminate read all count as present. That is a deliberate strengthening of the single-marker check,
not a rename.

Spec validation requires the immutable request, grant, owner, namespace, agent, profile, and Session binding
plus its referenced launch hash. Launch validation is structural when no valid Spec exists; when the Spec is
valid, the launch hash must equal the Spec's reference or the launch is corrupt. Submission validation uses
the admission schema with exact request, principal, run, owner, namespace, and Session fields; a create
submission (no `session_id`) derives the deterministic prospective Session id from its own
`(principal_id, request_id)`.

**Attribution authority is ordered.** A valid Spec is authoritative and the submission is ignored for
attribution even when absent, corrupt, or conflicting. A valid submission is a fallback only when the Spec is
not valid, sufficient only to fence a possibly dispatched Run or safely scope a terminal record; it never
makes a corrupt Spec valid and never permits pre-dispatch launch recovery. Launch records, result fields,
directory names, progress, events, locks, and marker contents are never attribution authority. Attribution
is **actionable** only when the chosen identity resolves to an already-existing, strictly readable Session
record whose id, owner, and namespace match; existing quarantine evidence does not make it less actionable,
because converging quarantine on an already-quarantined Session is a no-op.

**One exhaustive first-match table** over terminal × dispatch × Spec × launch × submission assigns exactly
one outcome to every combination:

| First matching condition | Exactly one outcome |
|---|---|
| trusted non-unknown terminal | **authoritative terminal**; preserve the result byte-for-byte; no Run or Session mutation; for API ownership only, prefer a valid Spec then a valid submission, and keep the Run unexposed rather than inventing ownership |
| trusted `unknown` terminal, actionable attribution | **authoritative `unknown` + quarantine**; preserve the result byte-for-byte; idempotently converge fence, quarantine, and terminal progress |
| trusted `unknown` terminal, no actionable attribution | **refuse to listen**; the terminal stays immutable; no substitute owner or Session is invented and no new terminal is written |
| corrupt terminal | **refuse to listen**; write the fence and quarantine first only when dispatch is possible and attribution is actionable; never rewrite or delete the corrupt terminal and never write progress or a result |
| absent terminal, dispatch possible, actionable attribution | **`unknown` + quarantine**; a valid Spec wins even against a corrupt submission, and a valid submission may supply fallback attribution when the Spec is not valid; never replay |
| absent terminal, dispatch possible, no actionable attribution | **refuse to listen**; no terminal is fabricated without a durable, trustworthy Session attribution |
| no terminal, no dispatch, valid Spec, launch valid or absent | **pre-dispatch failed/reusable**; a missing launch is the allowed crash point between the ordered Spec and launch writes; the submission is lower priority and irrelevant even when corrupt |
| no terminal, no dispatch, valid Spec, corrupt launch | **refuse to listen**; a present referenced launch that fails schema or hash validation is not the allowed absent crash point |
| no terminal, no dispatch, corrupt Spec | **refuse to listen**, for every launch and submission state; a valid submission cannot rehabilitate corrupt immutable Spec evidence |
| no terminal, no dispatch, absent Spec, launch valid or corrupt | **refuse to listen**; any launch without the Spec it must follow violates the ordered seal |
| no terminal, no dispatch, absent Spec and launch | valid submission → **pre-dispatch failed/reusable**, scoped by the submission; absent submission → **pre-dispatch failed/reusable bare reservation**, with no owner or Session invented; corrupt submission → **refuse to listen** |

"Reusable" means reconciliation adds no quarantine or other Session mutation. It never certifies a missing or
corrupt Session and never bypasses the load proof: a later reuse request must still open a valid record and
supply a stored external ID.

**Write ordering and idempotence.** All classification and attribution reads complete before any mutation.
Then: write the durable quarantine-pending fence first, so lease acquisition already refuses an open Session
carrying it; atomically and idempotently mark quarantined, clearing the fence only after the quarantined
record is durable; write or repair categorical terminal progress; and, only when no trusted terminal existed,
write-once the terminal **last**. If any earlier step fails, no new terminal is written and startup is
refused. A crash after any step leaves a non-leasable fence, a quarantined Session, or both, and the next
startup resumes the same outcome. A pre-dispatch outcome writes its one failed terminal directly and mutates
no Session. On every rerun, an already-quarantined Session, already-matching progress, and an existing
trusted terminal are no-ops.

## 10. Tests and evidence

- **L1 pure/unit:** registry grammar, bounds, and typed refusals; profile construction invariants;
  value-blind launch and hash projection; Spec freeze order and goldens; once-only environment precedence;
  the two configuration-fidelity modes, their invalid combinations, and the shared `N/A` sentinel declared
  once; mediation collision and layer-4-last precedence; typed start-plan construction and the reuse truth
  table; SDK callback signature and entry-guard conformance; the generated reconciliation oracle over the
  full artifact product crossed with Session states; terminal and marker tables; event bounds.
- **L2 hermetic ACP child** over real stdio JSON-RPC: existing fake-agent coverage plus `argv[0]`/shim
  semantics, registry startup defects, errno spawn classes, observation drift without a continuity refusal,
  child-HOME mutation completing normally, every reuse and callback failure, the projected-value retention
  matrix (final message, split chunks, events, permission fields, observations, usage, stderr, and an
  external Session id equal to a projected value), a Cursor-shaped model-only server that can be prompted
  only after an exact model readback, legacy value-blind reads, and crash injection at every reconciliation
  write boundary.
- **L2 structural:** no deployment fact in source; no wire launch field; no endpoint, transport, remote, or
  attach key, field, branch, or dependency; exactly one process per Run; read-once open counters across a
  full daemon lifecycle; no raw environment `repr` or interpolation; no `write_text` outside the storage
  seam; no load→new edge; no reconciliation replay edge; a monkeypatched legacy hash function that raises,
  proving the legacy branch never calls it; the installed SDK distribution version.
- **L3 real, opt-in, never in CI, per registered agent:** zero-prompt discovery; exact readback including a
  literal that must not be coerced; same-Session continuity across a real agent upgrade behind an unchanged
  registered command; the mandatory denied-action canary; cgroup crash containment.

Grade discipline is retained: pre-implementation probes are context only, direct-drive is B-grade, and the
socket path is the only C-grade production acceptance.

**Retired test families:** every Binding-root synthetic-tree suite — schema, canonicalization, size,
unknown-field, path-shape, ownership/mode/ancestor, contract acceptance, stale generation, probe-versus-manifest,
digest mismatch at admission and at the race seam, descriptor-pinned exec, package-closure enforcement, argv-prefix
drop/reorder/alter/pad, cross-profile pointer isolation — plus the secret-shaped-key refusal tests and the
per-Run registry read-count tests. The opt-in real-agent ACP suites are **re-pointed onto a registry-entry
fixture, not deleted**: only their attestation, credential-root, and artifact-tamper legs retire, because
deleting the rest would silently drop the only real-agent continuity evidence.

## 11. Implementation and rollback boundaries

- Stage 0/1 (`native_acp/` plus the shared additive seams) and Stage 2 (`arsd/`, production acceptance, G12)
  landed under their own approvals. Landing them authorized nothing further.
- The four boundary-reset gates — authority alignment, fail-closed reuse and total reconciliation, the
  environment-value guard, and the reset itself, including deletion of the three per-agent profiles — each
  landed under its own recorded approval and are merged on `main`. The retired Binding source framework is
  deleted.
- Publication (tag, GitHub Release, PyPI) is separate from implementation, and a prepared version number is
  not a publication. The runtime remains stdlib-only apart from the optional `native` extra, which the
  locked-dependency and version-sync gates assert at every stage.
- Rollback disables Native ingress; there is no second runtime to fall back to and no terminal-fact rewrite.
- Each reset gate is one revertable merge commit. Reverting the authority alignment restores the Binding-era
  authority chain exactly and touches no source, runtime, or deployment state. Reverting the fail-closed
  hardening restores baseline reuse and reconciliation behavior; its only non-revertable side-effect class is
  a durable quarantine or fence written before the revert, and those are pre-existing, idempotent,
  irreversible-by-design facts that are correct outcomes under both versions.
  Reverting the boundary reset restores the Binding line in source, with the `/opt` trees and Binding roots
  intact because no gate ever wrote to them. Records written while the removed per-Run literal guard was live
  keep their categorical withholding markers; those markers stay readable and schema-valid, and nothing
  rewrites them.
- Sachima `ArsdBackend` is later work. The ACP SDK pin moved to `0.12.0` under this document; the SDK's
  `http` extra stays uninstalled and HTTP/WS transport remains a non-goal.

Executable slice sequences, fresh worktree/branch rules, exact commands, and separate push/PR/merge
approvals live only in `docs/plans/active/`. The current board-linked plan is
[`docs/plans/active/2026-08-11-omp-reasonix-source-support.md`](../plans/active/2026-08-11-omp-reasonix-source-support.md).
Archived plans remain cold history and authorize nothing.

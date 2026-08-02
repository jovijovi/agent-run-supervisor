---
title: "agent-run-supervisor vNext Technical Solution"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-01
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/technical-solution.md"
---
# agent-run-supervisor vNext Technical Solution

## 0. Scope and implementation status

This is the module-level design authority for new ARS work. It describes the target shape after the V4
external-AGENT boundary reset. The previous mixed v0.1.7/vNext solution is preserved at
`docs/archive/pre-vnext-reset-2026-07-21/technical-solution.md` and must not direct new development.

ARS stays Python and **stdlib-only at runtime**. `tomllib` and `contextvars` are standard library on the
supported Python floor, so the registry parser and the per-Run guard context add no dependency and no
lockfile change. vNext extends the existing package additively. The legacy acpx path is still present in
that package and is **not** a compatibility baseline, surface, or obligation, and never a Native driver,
fallback, or session store: no module below owes it compatibility, removing it is separately authorized
work this document does not perform, and it survives here only as a bounded differential/comparison-test
reference.

**Authority and released source differ right now, deliberately.** Every module disposition below is the
*target*. Source on `main` still carries the retired Binding line: `native_acp/runtime_binding.py` as the
only reader of a Binding root, `native_acp/attestation.py` at the spawn boundary, four registered profiles,
artifact digests and package closures, promotion, and a required Binding-root daemon flag. The retired
module design is preserved under
[`docs/archive/binding-era-2026-07/`](../archive/binding-era-2026-07/architecture-3.1-3.3.md).

The Stage 3 candidate implements this target — both files above are deleted, the registry reader and the
value-blind launch snapshot exist, and the suite is green — **on a task branch that is uncommitted,
unmerged, unreleased, and undeployed**. So "target" still means target for anyone reading `main` or
operating a deployment. The board carries the delta; this document grants no approval, and neither the
authority alignment, nor a green local verification, nor any later gate authorizes the next one.

## 1. Package shape

### 1.1 Shared additive seams

| Surface | Responsibility |
|---|---|
| `managed_process.py` | spawn a live supervised child; expose identity/stdin/stdout/stderr/wait/terminate/kill/reap; preserve bounded behavior; accept the resolved environment only at the spawn seam and never format it |
| `exit_classifier.py` / `result.py` or Native superset boundary | losslessly carry `completed/failed/cancelled/timed_out/unknown`; persist `retryable=false` for `unknown`; accept stable detail codes rather than raw child or exception text |
| `session.py` additive fields | Native Session identity, external session ID, owner, profile hash, `last_effective_model/effort`, persistent quarantine, optional operator `session_epoch`; legacy serialization unchanged when fields are absent |
| existing `process_liveness.py` | full `ProcessIdentity` and fail-safe liveness classification — **unchanged by the reset** |
| existing `event_store.py`, `live_stream.py`, `redaction.py` | atomic files, bounded projection/evidence primitives; reused through explicit Native roots |

`runner.execute_subprocess` and `SubprocessOutcome` are legacy acpx-only code that the reset does not touch;
leaving them unchanged is a scope statement, not a compatibility commitment, and their removal is separately
authorized. Native code must not share their stdout consumer or wait-before-return contract.

### 1.2 `native_acp/` package — target module map

| Module | Responsibility |
|---|---|
| `agent_registry.py` **(new)** | the only reader of the operator agents file: strict `tomllib` parse, bounded validation, typed `REGISTRY_*`/`ENTRY_*`/`MEDIATION_KEY_COLLISION` refusals, **one read per daemon lifetime** into an immutable snapshot, zero per-Run filesystem access, and the config-hygiene check (resolve symlinks; require a regular file that is not group- or world-writable) |
| `profile.py` | `AcpCompatProfile` + `AgentInstance` + a **two-entry** registry (`standard-native-acp-v1`, `claude-agent-acp-compat-v1`) + the source-owned mediation binding table and its global `RESERVED_MEDIATION_KEYS`. A profile freezes ACP semantics only: protocol major, required and forbidden capabilities, session semantics, selector-id conventions, the base environment allowlist, mediation semantics, and — only where evidenced — frozen session metadata and a required permission-mode selector. No executables map, wrapped artifacts, binding slots, probe-as-gate, closure predicate, launch kind, or per-agent value domain |
| `agent_registration.py` | the typed operator registry **entry** value and its bounded grammars — command, argv tokens, environment declarations, mediation selection, selector-id hints, capability narrowing, optional epoch. **Pure**: no filesystem access, so the single reader of the agents file stays `agent_registry.py` |
| `spec.py` | versioned `AgentRunRequest`; immutable `AgentRunSpec`/`spec_hash`; the sealed **launch snapshot** that replaces `ResolvedLaunchSpec`; the ephemeral non-serializable `ResolvedEnvironment`; the durable value-blind `EnvProjection`; guarded `ObservedRuntime` extending the observed-state record. `launch_spec_hash` on the Spec is **retained and load-bearing**. No sealed runtime identity, no runtime provenance, no artifact descriptor |
| `storage.py` | the only constructor seam for `native-runs/` and `native-sessions/`; write-once discipline; bounded no-follow classifying readers returning valid/absent/corrupt while retaining the existing terminal trichotomy; free-form Run text accepted only as a guard-produced safe projection type |
| `driver.py` | ACP wire/state machine over a supplied `ManagedProcess`; never spawns or selects policy/profile. Accepts a typed load plan and the exact stored ID; `load_session()` keeps returning `None`, the expected ID is set before the call, and options are seeded from the load response; on `session/new` the returned external ID is certified against the per-Run guard **before** it is assigned, returned, or persisted |
| `config_fidelity.py` | exact-or-zero configuration and between-Run switch/rollback state machine; option domains come from **live discovery**, with no source-domain preflight |
| `client.py` | official SDK callback implementation. Synchronous fail-closed identity rejection at callback entry for every ID-bearing update, permission, filesystem, terminal, and session-scoped elicitation surface, using exact pinned SDK signatures rather than varargs; categorical violations carry no IDs |
| `permissions.py` | frozen-grant → default-deny mediation; deterministic mediation evidence; child-supplied fields guarded before recording or returning |
| `events.py` | ACP update normalization into the caller-stable event families without copying thought/raw bulk bodies; dynamic keys and strings guarded before enqueue |
| `event_writer.py` | one bounded writer per Run, monotonic `seq`, truncation markers preserving lifecycle/permission/error events; guards again before sequence assignment, fan-out, and append — the last common boundary, not the only one |
| `run_task.py` | admission assembly, the closed start plan, lease, process/driver coordination, dispatch markers, timeout/cancel, finalization, quarantine, top-level exception boundary; once-only environment resolution and guard construction; Spec-then-launch write order preserved; `agentInfo` name/version recorded as evidence and gating nothing |

**Deleted by the reset:** `runtime_binding.py` and `attestation.py`. No module may re-create artifact
identity, promotion, digests, ownership or mode gates, or credential-root inspection under another name.

### 1.3 `arsd/` package

| Module | Responsibility |
|---|---|
| `server.py` | asyncio UDS accept loop, `SO_PEERCRED`, finite backlog, per-connection isolation; UDS create/chmod/replace/unlink as the second writable surface; installs the log filter before serving |
| `safe_logging.py` **(new)** | the mandatory **handler-level** log filter, installed before serving and before any diagnostic CLI spawns an ACP child. It guards complete ARS-authored `msg + args`, clears raw `args`/`exc_info`, replaces every dependency/SDK-originated record in inherited SDK context with a categorical record, and suppresses any Run-tagged record lacking a guard categorically |
| `protocol.py` | bounded JSON frames, mandatory `api_version`, **per-operation** version admission rather than envelope-level rejection, so the drain matrix is expressible |
| `handlers.py` | submit/status/events/cancel and Session status/list/close with owner checks; Session creation is part of `submit`; `server_info` reports the supported version set; responses expose only guarded fields through an explicit allowlist and never raw stored objects or exceptions |
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
one top-level field, and reports only guarded, allowlisted evidence. For a pre-reset record it classifies the
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
    mediation=source_mediation_pairs(entry.mediation),   # layer 4, applied LAST
)
guard = RunTextGuard.from_environment(resolved)
launch_env = resolved.value_blind_projection()
managed_process.start(argv=argv, env=resolved.exec_mapping)
```

- `ResolvedEnvironment` is **ephemeral and non-serializable**: its value mapping is `repr=False`, excluded
  from equality and hashing, exposes no `to_dict`, and is accepted **only** by the process-spawn seam and by
  the guard constructor. A type and static-boundary test prevents it from entering any Spec, launch, event,
  result, log, exception, or API serializer.
- `EnvProjection` is the separate **durable, value-blind** shape: per name, the name, its source class, its
  precedence layer, and its redaction status, plus a resolved count, the mediation id, and the
  declared-but-absent names. Nothing else. The old `fixed_env`/`permission_env` value fields disappear from
  the launch schema, and a schema-level allowlist **rejects** them rather than ignoring them.
- Resolution happens **exactly once**, in memory, before sealing and before spawn. This replaces the
  spawn-time re-read of the ambient environment, so the sealed projection describes exactly what was handed
  to exec and the exec mapping stays byte-identical if the ambient environment mutates afterwards.
- `SSH_AUTH_SOCK` is deliberately **not** in the layer-1 base set; forwarding it is an explicit per-agent
  pass-through opt-in.

**Reviewer-note boundary, binding.** The workspace canonical root and the effective `cwd` are **not** routed
through the guard. When the workspace lives under `$HOME`, `spec.json`'s canonical root and effective cwd
contain the complete `HOME` literal as a substring and `spec_hash` covers them. That is correct and
intentional: they are **independently derived authority facts, not environment-value flow**, and guarding
them would break workspace binding, reconciliation attribution, and audit. An implementer must not route the
binding fields through the guard, and a test encodes this rather than a comment.

### `SessionStartPlan`

```python
SessionStartPlan = NewSessionPlan | LoadSessionPlan

@dataclass(frozen=True)
class NewSessionPlan:
    # Constructible ONLY from a request whose immutable reuse intent is "none".
    ar_session_id: str

@dataclass(frozen=True)
class LoadSessionPlan:
    # Captured exactly from an already-existing Native Session record.
    ar_session_id: str
    external_session_id: str = field(repr=False)
```

Invariants that must hold **structurally, not by convention**:

1. `driver.new_session` is reachable only from the new-session match arm.
2. `NewSessionPlan.__init__` is reachable only from the non-reuse admission path.
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
Session reuse expectation; `agent_id` and profile/launch/schema hashes; frozen execution grant,
role/capability, workspace, MCP, and credential-**reference** hashes; requested model/effort, limits,
recovery/evidence policy; and `spec_hash` excluding generated control fields such as `run_id`/timestamps.

`to_dict()` stays an explicit projection rather than raw `asdict`, and a structural test walks every spec
dataclass field asserting it appears in the projection except the declared omit set. `SPEC_SCHEMA_VERSION`
and `DIGEST_SCHEMA_VERSION` both move with the reset, because the digest material genuinely changes when
environment values leave the launch snapshot; the launch-snapshot schema version moves for the same reason.
The disposition of the retired expected-binding-hash request/Spec field is an explicitly carried follow-up
and is **not** silently changed while moving digest material.

### `ObservedRuntime`

Observed-only, and guarded before storage: `ProcessIdentity`, agent/protocol info, capability and config
advertisements, external Session ID, discovery snapshots, exact effective model/effort, and the
non-authoritative resolution observations — declared command, path-lookup observation, mapped-image
observation, and an optional operator probe result. Each carries an explicit non-authoritative marker. It
never alters a profile, the registry snapshot, or a Spec.

## 3. Run and Session state

### Native Session record

Stable identity: `agent_id`, profile identity, external Session ID, owner/namespace, `workspace_hash`, and
the optional operator `session_epoch`. Mutable observations: `last_effective_model/effort`. Persistent
state: `active | closed | quarantined`, reason, source Run. model/effort are not Session identity.

Reuse requires equality on the full identity set, and comparison is **symmetric**: a record carrying an
epoch is refused by a Run with none and vice versa, which is exactly why adding an epoch for the first time
cuts existing Sessions. The load-time gate runs **before the lease is mutated and before `session/load`**,
and it requires a non-empty stored external ID; there is no `session/new` fallback anywhere on that path.

Retired identity fields — the adapter contract hash, the ARS-derived compatibility epoch, and the agent
registration hash — are deleted as identity. Records carrying them stay **status-readable** and are
**refused for load** with a stable code.

Same Session has one lease and one active Run. A quarantined Session refuses new work. v1 has no
unquarantine tool; successor work uses a new Session with caller-owned context handoff.

### Native Run record

One Run owns one immutable Spec, one launch snapshot, one observed-state record, one EventWriter, zero or
one Turn, two dispatch markers, and one irreversible result. A retry is an independent Run linked by
`retry_of_run_id`.

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

The reset drops the descriptor-based interpreter exec so the declared `command` and `argv[0]` survive
exactly as declared, accepts `ResolvedEnvironment` only at the spawn seam, never formats the environment
mapping, and guards stderr bytes before any retained diagnostic output. Child-exec errno is preserved
through the process error type so the caller can classify `ENOENT`, `EACCES`, and everything else without
embedding raw exception text. Process-group and reap behavior are unchanged.

## 5. ACP and exact configuration flow

`NativeAcpDriver` receives an already-spawned process. Its success path is fixed:

```text
initialize
→ verify protocol major, required capabilities, forbidden capabilities
→ record agentInfo as EVIDENCE (gates nothing)
→ session/new or session/load, per the closed start plan
→ read complete config options (live discovery)
→ set model
→ consume complete model-dependent options
→ rediscover effort from that fresh set
→ set effort
→ consume updates and exact-read the effective pair
→ persist guarded ObservedRuntime
→ ready-to-prompt
```

Any missing, unknown, or inexact state raises a stable pre-dispatch failure. Prompt code is unreachable until
the state machine reaches `ready-to-prompt`.

**Value domains are live.** Registered model sets, allowed effort sets, and selector value domains are
deleted as admission gates: the live-discovered option set is the domain authority and exact literal readback
is the proof. An unadvertised value still yields zero Turn and no prompt — now checked against what the
running agent advertises, which is why "the agent added a model today" is a non-event for ARS.

**The post-`initialize` identity gate is narrowed to a contract check.** It verifies protocol major, required
capabilities, and forbidden capabilities (source floor ∪ the entry's declared set), and on a compatibility
profile it proves the required permission mode by exact readback. It does **not** compare `agentInfo.name` or
`.version` against anything. The ACP-reported version and an external CLI `--version` remain separate facts,
and no code path may assert they are equal.

For reuse, `session/load` receives the stored external ID byte-unchanged, its response seeds the fidelity
machine, no identity is read from the response, and `session/new` is structurally unreachable. Switch
rollback targets the prior effective pair and is itself exact-readback gated.

## 6. Dispatch, finalization, and reconciliation

`RunTask` exclusively creates `prompt-dispatch-started` immediately before the wire write and
`prompt-accepted` after a successful write. Finalization prioritizes durable reconciliation facts over
ordinary process exit classification.

| Condition | Result | Session |
|---|---|---|
| pre-dispatch failure | `failed` | active unless switch rollback failed |
| trustworthy ACP terminal | matching terminal state | active unless continuity is disproven |
| dispatched; supervisor proves matched child abnormal exit while observation remained intact | `failed` | quarantined |
| dispatched; observation lost / no trustworthy terminal | `unknown`, `retryable=false` | quarantined |
| external session identity violation observed after dispatch | `unknown`, `retryable=false` | quarantined |

`classify_exit` alone cannot mark a dispatched/no-terminal Run completed or cancelled. Restart preserves
existing terminal results, reconstructs only from trustworthy terminal events, and maps uncertain started
Runs to `unknown/quarantined/retryable=false`. It never calls prompt.

## 7. Credentials, environment, and the sink guard

**ARS resolves no credentials.** There is no credential resolution anywhere on the Native path;
`credential_refs` are caller-supplied **names** recorded as admission evidence and grant material, checked
for exact match against the required set, and never resolved to values or placed in the child environment.
ARS credential resolution is a future, separately designed capability — it would have to define the
source-owned slot-name → env-key mapping, the reserved-key collision rule, the provider authorization path,
and a no-persistence proof — and **no placeholder for it exists in any schema**.

Deleted with the reset: the credential-root slot, the managed-credential-root concept, auth-file inode and
mode inspection, credential-root permission enforcement, config-file absence checks, and every
credential-root refusal. ARS does not know what an AGENT's credential file is called and must not.

**The guard.** `RunTextGuard.from_environment(resolved)` is constructed at admission step 11 from every
**non-empty** final projected value across all four layers. Empty strings contribute no bytes. The guard:

1. keeps its sensitive set in memory only, with `repr=False`, no serializer, no equality or hash
   implementation, and no diagnostic enumeration;
2. builds bounded longest-first lists for the Python string form **and** the actual POSIX exec byte encoding,
   removing duplicates by direct equality only; matching is a bounded direct scan / `startswith` walk —
   never a regex cache, set or dict key, Bloom filter, digest, or any operation that hashes a sensitive
   value, even transiently;
3. applies the existing static secret-pattern redactor **and** the per-Run exact matcher;
4. recursively guards every string value and every dynamic string key in structured child-controlled data
   before JSON encoding, suppressing the enclosing record when two guarded keys collide rather than
   overwriting one;
5. rescans the guarded result, and where safe replacement cannot be established suppresses the whole field or
   record and emits only a stable categorical withholding marker.

The replacement token is a fixed source literal containing no input data. Only coarse sink-local integers
may be recorded — matched occurrences, suppressed fields, suppressed records — and original or replaced
lengths are **not** recorded. Confidentiality wins over evidence completeness: there is no minimum secret
length and no value is waived for inconvenience. The guard stays installed through SDK close, cancellation
and join of every inherited task, persistence, logging flush, and final response projection; only then is the
context cleared and the carrier dereferenced. That is lifetime minimization, **not** a claim that Python can
zero immutable strings.

**Mandatory sink placement.** Every row below is inside the guarantee:

| Sink | Required boundary |
|---|---|
| ACP final/agent/thought text and the final-message accumulator | guard on ingestion with a rolling carry one byte short of the longest literal, so a value split across chunks is caught before accumulation; retain no unguarded chunk beyond that carry; guard the assembled message again before the result write |
| normalized updates and lifecycle/tool/config/permission evidence | guard all dynamic keys and strings before enqueue; the writer guards again before sequence assignment, fan-out, and append |
| permission and filesystem evidence | guard child tool-call ids, kinds, reasons, path and content summaries, option fields, handler exceptions, and denial diagnostics before recording or returning |
| `effective.json` and initialize/discovery evidence | guard `agentInfo`, capability and config structures, selector ids, observations, and every child-supplied string before storage |
| the external Session ID returned by `session/new` | it must be replayed unchanged and therefore **cannot** be redacted: test it inside the driver **before** assigning the expected ID, returning it, or updating the Session record; any match yields a categorical sensitive-collision refusal, connection teardown, no ID persistence, no callback servicing, no prompt, and no API exposure |
| stderr/stdout diagnostic capture | byte matcher over the joined bounded buffer (or a streaming carry) **before decode**, text matcher again after decode, then bounded safe retention; undecodable or unsafe input is replaced wholesale with a categorical marker |
| `result.json`, `progress.json`, the redaction report, terminal/failure detail | guard before the storage call; terminal codes stay stable and value-blind |
| spawn, ACP, callback, timeout, cleanup, and SDK exceptions | translate known failures to stable codes; otherwise guard the safe projection. Daemon and diagnostic-CLI outer boundaries replace any otherwise-unhandled exception with a stable code. Raw `repr`, raw args, raw frame bytes, raw traceback locals, and raw environment mappings are never emitted |
| daemon and SDK logging | a per-Run `contextvars` guard and an SDK-child flag are inherited by driver and SDK tasks. ARS-authored Run logs are preformatted as complete `msg + args`, guarded, and stripped of raw `args`/`exc_info`. **Every** dependency/SDK-originated record in that context is replaced wholesale by a stable categorical record rather than trying to recognize arbitrary repr or escape transforms, and any Run-tagged record lacking a guard is suppressed categorically. `contextvars` do **not** cross thread boundaries, so the implementer must prove that no value-bearing record can originate off-loop and untagged; the categorical suppression backstop is what makes that provable, and `ManagedProcess` never formatting the mapping is what makes it practically closed |
| live events, terminal response, status/list, `run inspect`, and other API projections | live data crosses the guard before fan-out; completed reset-schema data is read only from already-guarded stores through an explicit response-field allowlist; handlers never return raw exceptions or raw stored objects |
| structured launch/spec/hash material | names + source class + precedence only. **No value, value digest, keyed digest, length, prefix, suffix, equality token, or matcher table is hash material**, and the retired value-bearing env fields are rejected by a schema-level allowlist rather than ignored |
| pre-reset value-bearing records | classify the schema **before** selecting a verifier; return a categorical allowlist; withhold environment fields, raw documents, value-bearing seals, the external session id, and free-form text; **never** call a launch-hash recomputation on a legacy record |
| startup and registry validation | structurally value-blind by construction, because it runs before a per-Run guard exists: refusals name a stable rule and at most a field path or an environment **name**; successful offline validation prints only entry ids, counts, names, source classes, and rule outcomes |

The event writer is the **last** common boundary, not the only one: early guarding keeps values out of
buffers, while writer guarding stops a missed call site from becoming durable or externally visible.

**Honest limits.** The guarantee covers ARS-owned persistence and every externally exposed ARS
daemon/CLI/log/error/event projection. It does not erase the operator-authored value at its source, stop the
child from writing its own logs or state, stop the child transmitting a value to a remote service, prevent
OS crash dumps or privileged process inspection, or detect a transformed disclosure — a substring or partial
value, base64, encryption, hashing, character-by-character fragmentation, or a semantic paraphrase. Those
require containment or information-flow control and are not claimed. Independently derived public facts with
identical bytes are not treated as value-derived flow; tests prove the boundary with unique sentinels and
taint-directed call paths, not lexical coincidence.

The permission and workspace evidence rules are otherwise unchanged: the caller-provided grant is frozen into
the Spec, the bridge maps only registered ACP operations and denies unknown classes, and decisions record
operation family, decision, stable reason, and correlation with no raw secret or payload. Production
acceptance still requires a real denied-action canary with a recorded deny, a confirmed failed operation, an
absent sentinel, and direct pre/post listing of a disposable known-empty workspace. `workspace_hash` remains
a canonical binding hash only; v1 adds no content-digest service, watcher, or sandbox claim.

## 8. Storage seam and safe projection types

`native_acp/storage.py` constructs all Native `SessionStore(base_dir=.../native-sessions)` and
`EventStore(base_dir=.../native-runs)` instances. No other Native module constructs a legacy-root store.
Tests seed poisoned same-ID legacy records and prove Native never reads or mutates them; directory listings
and bytes remain unchanged.

Files and directories use `0600`/`0700`, with exclusive create or atomic replace as appropriate. One bounded
writer owns each event stream. Credential values, raw environment values, cookies, authorization headers,
and unredacted bulk payloads never persist.

**Safe projection types are a seam property, not a convention.** Storage APIs for free-form Run text accept a
guard-produced safe projection type, so an unguarded `str` is not accepted at those seams and a missed guard
call is a type error rather than a leak. Reset-schema readers expose explicit allowlists; terminal builders
accept stable detail codes rather than raw child or exception text.

**The workspace binding fields are outside the guarded set, deliberately.** `spec.json`'s canonical workspace
root and effective `cwd` retain their complete literal text and stay covered by `spec_hash`, even when the
workspace lives under `$HOME` and the literal therefore contains the complete `HOME` value as a substring.
They are independently derived authority facts, not environment-value flow. Guarding them would break
workspace binding, reconciliation attribution, and audit, so no storage seam may route them through the
guard.

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
the admission schema with exact request, principal, run, owner, namespace, and Session fields; a non-reuse
submission derives only the already-defined deterministic ephemeral Session id.

**Attribution authority is ordered.** A valid Spec is authoritative and the submission is ignored for
attribution even when absent, corrupt, or conflicting. A valid submission is a fallback only when the Spec is
not valid, sufficient only to fence a possibly dispatched Run or safely scope a terminal record; it never
makes a corrupt Spec valid and never permits pre-dispatch launch recovery. Launch records, result fields,
directory names other than the deterministic ephemeral derivation, progress, events, locks, and marker
contents are never attribution authority. Attribution is **actionable** only when the chosen identity
resolves to an already-existing, strictly readable Session record whose id, owner, and namespace match and
whose state is open/active or already quarantined.

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
  guard string, byte, recursive, overlap, and suppression behavior with a spy proving no hash of a sensitive
  value is computed even transiently; mediation collision and layer-4-last precedence; typed start-plan
  construction and the reuse truth table; SDK callback signature and entry-guard conformance; the generated
  reconciliation oracle over the full artifact product crossed with Session states; terminal and marker
  tables; event bounds.
- **L2 hermetic ACP child** over real stdio JSON-RPC: existing fake-agent coverage plus `argv[0]`/shim
  semantics, registry startup defects, errno spawn classes, observation drift without a continuity refusal,
  child-HOME mutation completing normally, every reuse and callback failure, every environment-value sink
  echo including short, overlapping, Unicode, JSON-metacharacter, non-ASCII exec-byte, and
  deliberately-split-across-chunk sentinels, legacy value-blind reads, and crash injection at every
  reconciliation write boundary.
- **L2 structural:** no deployment fact in source; no wire launch field; no endpoint, transport, remote, or
  attach key, field, branch, or dependency; exactly one process per Run; read-once open counters across a
  full daemon lifecycle; no raw environment `repr`; no hash over a value set; no unsafe storage signature; no
  load→new edge; no reconciliation replay edge; a monkeypatched legacy hash function that raises, proving the
  legacy branch never calls it.
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
- The retired Binding source framework is still merged on `main`. This document describes its replacement;
  it does not authorize writing it. Each reset gate needs a distinct source-implementation approval recorded
  after the authority alignment merges, and deleting the three per-agent profiles from source needs one more
  explicit confirmation on top of that.
- Publication (version bump, tag, GitHub Release, PyPI) is separate from implementation. Dependency and
  lockfile files stay unchanged throughout: the runtime remains stdlib-only, which the locked-dependency and
  version-sync gates assert at every stage.
- Rollback disables Native ingress; there is no auto-fallback to acpx and no terminal-fact rewrite.
- Each reset gate is one revertable merge commit. Reverting the authority alignment restores the Binding-era
  authority chain exactly and touches no source, runtime, or deployment state. Reverting the fail-closed
  hardening restores baseline reuse and reconciliation behavior; its only non-revertable side-effect class is
  a durable quarantine or fence written before the revert, and those are pre-existing, idempotent,
  irreversible-by-design facts that are correct outcomes under both versions. Reverting the guard restores
  baseline redaction while categorical withholding markers written under it stay readable and schema-valid.
  Reverting the boundary reset restores the Binding line in source, with the `/opt` trees and Binding roots
  intact because no gate ever wrote to them.
- Sachima `ArsdBackend` and pin changes are later work.

The executable slice sequence, fresh worktree/branch rules, exact commands, and separate push/PR/merge
approvals live only in `docs/plans/active/`.

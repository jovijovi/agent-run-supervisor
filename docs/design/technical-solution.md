---
title: "agent-run-supervisor vNext Technical Solution"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-28
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/technical-solution.md"
---
# agent-run-supervisor vNext Technical Solution

## 0. Scope and implementation status

This is the module-level design authority for new ARS work. The Stage 1 `native_acp/` and Stage 2
`arsd/` packages described below exist on `main`; the roadmap board remains the status authority for
what is closed, enabled, or still gated, and this document never grants approval. The previous mixed
v0.1.7/vNext solution is preserved at
`docs/archive/pre-vnext-reset-2026-07-21/technical-solution.md` and must not direct new development.

ARS stays Python. vNext extends the existing package additively, preserves the released acpx path as a
compatibility baseline, and never uses it as Native driver or fallback.

The Runtime Binding layer described below is merged source on `main`: `native_acp/runtime_binding.py`
is the only reader of a Binding root, every registered profile carries an `AdapterContract`,
deployment-specific downstream CLI paths, versions, and digests have moved out of the profile constants,
`session_compatibility_epoch` is persisted, and the `runtime-binding` command surface exists.
`WrappedRuntimeArtifacts` freezes the adapter's complete package closure — install root plus tree
digest, with the entry proven inside it — not the entry path and digest alone, and the
`interpreter_argv_prefix` that closes the interpreter's path-independent module search. Its frozen
paths name the root-owned artifact location a separate materialization step is expected to create;
declaring that location creates nothing.

Merged source is not operator activation. No immutable artifact root, promoted generation, re-acceptance
at a current profile revision, permission canary, rollout, release, or deployment follows from it, and
this document grants none of them.

The OpenCode examples below rest on completed evidence: an operator-run zero-prompt ACP `initialize`
discovery, plus the code-owned CLI version probe output and artifact digest for the same executable,
produced the identity, capabilities, and selector domains that `opencode-native-acp` revision 3
registers, retiring `opencode-1.18.4` with no compatibility alias. That is source registration only —
not operator activation, re-acceptance at the current revision, or provider acceptance — and the ACP
`agentInfo.version` and the CLI `--version` stay two independent facts.

## 1. Package shape

### 1.1 Shared additive seams

| Surface | Responsibility |
|---|---|
| `managed_process.py` (or final fresh-checked equivalent) | spawn a live supervised child; expose identity/stdin/stdout/stderr/wait/terminate/kill/reap; preserve bounded behavior |
| `exit_classifier.py` / `result.py` or Native superset boundary | losslessly carry `completed/failed/cancelled/timed_out/unknown`; persist `retryable=false` for `unknown` |
| `session.py` additive fields | Native Session identity, external session ID, owner, profile hash, `last_effective_model/effort`, persistent quarantine; legacy serialization unchanged when fields are absent |
| existing `process_liveness.py` | full `ProcessIdentity` and fail-safe liveness classification |
| existing `event_store.py`, `live_stream.py`, redaction | atomic files, bounded projection/evidence primitives; reused through explicit Native roots |

`runner.execute_subprocess` and `SubprocessOutcome` stay byte-identical and acpx-only. Native code must not
share their stdout consumer or wait-before-return contract.

### 1.2 `native_acp/` package — Stage 1

| Module | Responsibility |
|---|---|
| `spec.py` | versioned `AgentRunRequest`; immutable `AgentRunSpec/spec_hash`; controlled `ResolvedLaunchSpec`; observation-only `EffectiveRunState` |
| `profile.py` | typed, versioned, closed `AgentProfile` registry; the OpenCode profile plus the official Codex ACP and Claude Agent ACP adapter profiles. Each profile carries an `AdapterContract` with `adapter_contract_hash`, `launch_kind`, accepted Binding schema/slot projection, a code-owned version-probe rule, and — for `wrapped_acp` — the adapter's complete package closure (install root, tree digest, entry inside it) plus the frozen `interpreter_argv_prefix` that closes the interpreter's path-independent module search, checked against the profile's own argv so the two cannot drift; deployment-specific CLI path/version/digest live in the Binding, not here. It also owns `path_within_root`, the one lexical containment predicate every closure surface shares |
| `runtime_binding.py` | the only reader of a Binding root — `active.json` + `generations/<id>/manifest.json` loading, canonical-JSON/size/ownership/mode/ancestor validation through `O_NOFOLLOW`/dirfd walks, contract-acceptance matching, slot projection, generation/set/slot hashing, and the typed fail-closed refusal surface |
| `attestation.py` | spawn-boundary proof that the frozen interpreter/adapter/CLI identity and launch env are what the profile registered. It proves the *sealed* runtime identity rather than a profile constant, extends artifact identity to package/tree closures on both the Binding-sealed CLI **and** the source-frozen adapter, refuses a module-resolution root above the adapter closure, binds the frozen interpreter argv prefix as a token sequence with the adapter entry pinned immediately after it, and adds the ownership/ancestor and TOCTOU rechecks for both launch kinds |
| `storage.py` | only Native root-binding constructors for `native-runs/` and `native-sessions/`; structural guard against direct legacy store construction |
| `driver.py` | ACP wire/state machine over a supplied `ManagedProcess`; never spawns or selects policy/profile |
| `config_fidelity.py` | exact-or-zero configuration and between-Run switch/rollback state machine |
| `client.py` | official SDK callback implementation for updates, permission, and filesystem requests |
| `permissions.py` | frozen-grant → default-deny mediation; deterministic `MediationEvent` evidence |
| `events.py` | ACP update normalization into the caller-stable event families without copying thought/raw bulk bodies |
| `event_writer.py` | one bounded writer per Run, monotonic `seq`, truncation markers preserving lifecycle/permission/error events |
| `run_task.py` | admission assembly, lease, process/driver coordination, dispatch markers, timeout/cancel, finalization, quarantine, top-level exception boundary |

### 1.3 `arsd/` package — Stage 2

| Module | Responsibility |
|---|---|
| `server.py` | asyncio UDS accept loop, `SO_PEERCRED`, finite backlog, per-connection isolation |
| `protocol.py` | bounded JSON frames, mandatory `api_version`, unknown-version rejection |
| `handlers.py` | submit/status/events/cancel and Session status/list/close with owner checks; Session creation is part of `submit` |
| `admission.py` | durable submission/idempotency records, keyed admission locks, typed terminal-result inspection; also the single per-Run Binding read — one `active.json` read plus one generation read, revalidation of contract match and artifact digest, then sealing; no other module reads the Binding root |
| `reconcile.py` | startup-only reconciliation; no prompt replay/resume |
| `client.py` | typed local caller for Hermes/CLI; explicit connect/close, no silent reconnect or replay |
| `service_unit.py` | pure data→text renderer for the user-scope service unit; never installs, enables, or starts anything |
| `__main__.py` | unprivileged daemon entrypoint (`python -m agent_run_supervisor.arsd`) and the side-effect-free `--print-service-unit` mode; no state authority beyond `arsd` core |

No TCP, root mode, runtime plugin loader, arbitrary command adapter, or per-Run Worker is introduced.

### 1.4 Operator command surface

`cli.py`/`commands.py` carry exactly one subcommand group, wired to `runtime_binding.py` and to the
per-Run provenance reader:

| Command | Reads | Writes | Side effects |
|---|---|---|---|
| `runtime-binding validate <generation>` | generation manifest, artifacts, live contract | nothing | runs the code-owned version probe |
| `runtime-binding promote <generation>` | the same, revalidated | `active.json` (atomic replace) | none beyond that file; no daemon restart |
| `runtime-binding rollback <generation>` | a previously validated generation | `active.json` (atomic replace) | none beyond that file |
| `runtime-binding inspect-run <run_id>` | `spec.json`, `launch.json` | nothing | none |

No `--force` flag is defined, no command shells out to `sudo` or otherwise escalates, and no command
installs artifacts, edits a service unit, restarts `arsd`, or contacts a provider. `inspect-run`
recomputes the launch hash after excluding only the top-level `launch_spec_hash`, compares it with both
the embedded value and `spec.json`, and reports profile/contract, adapter/protocol, Binding
generation/set/slot hashes, the complete CLI artifact identity/version/digest, and the epoch. A
pre-PR-B `launch.json` has no embedded seal; `inspect-run` reports it as a legacy launch record and
verifies it against `spec.json` alone rather than failing.

## 2. Admission data model

### `AgentRunRequest`

Wire input contains schema version, caller namespace/owner expectation, profile ID, Session choice,
workspace/resource references, requested model/config, frozen grant reference/hash, limits, and evidence
policy. Inputs are validated as plain, bounded values before use.

### `AgentProfile`

A code-registered profile contains:

- profile ID/revision/snapshot/hash and config-schema hash;
- executable reference and fixed argv template with only registered substitutions;
- ACP transport/version/capability requirements including `requires_session_load`;
- credential and MCP injection **slot names**, never values;
- registered config selectors/types/value domains;
- optional built-in adapter ID only when conformance evidence proves a real standard-ACP gap.

### `AdapterContract`

The profile's source-frozen compatibility contract, hashed canonically into `adapter_contract_hash`:

- stable profile ID, revision, `adapter_contract_hash`;
- `launch_kind`: `wrapped_acp` or `direct_acp`;
- the accepted Binding schema and the slot projection it admits (slot names, kinds, and required
  descriptor fields per kind);
- fixed executable/argv construction and code-known env keys only — a Binding value may fill a
  code-declared slot, never introduce a key;
- ACP protocol version and agent name, required capabilities, and explicitly forbidden capabilities;
- permission, config, model, effort, and session semantics (selectors, domains, permission mode,
  frozen session metadata);
- for `wrapped_acp`, the interpreter and adapter-entry artifact identity, which stay in source;
- a code-owned safe version probe: a fixed non-prompt argv suffix, a hermetic environment with no
  workspace, no credential, and no network dependence, bounded output and timeout, and a code-owned
  parser. The probe is the only sanctioned way to learn an external CLI's real version.

The contract hash changes whenever any of the above changes, and a changed hash invalidates every
Binding generation accepted under the old one.

### `RuntimeBinding`

Operator-authored, read-only to ARS, stored outside the repository:

```json
{
  "schema_version": 1,
  "generation_id": "gen-0007",
  "contract_identity": {
    "profile_id": "opencode-native-acp",
    "profile_revision": 3,
    "adapter_contract_hash": "[REDACTED-SHA256]"
  },
  "slots": {
    "agent_cli": {
      "kind": "native_binary",
      "path": "/opt/[REDACTED]/opencode/1.18.5/bin/opencode",
      "version": "1.18.5",
      "sha256": "[REDACTED-SHA256]",
      "interpreter": null
    }
  },
  "session_compatibility_epoch": 3,
  "provenance": {
    "created_at": "2026-07-26T09:00:00+08:00",
    "accepted_by": "[REDACTED-OPERATOR]",
    "accepted_at": "2026-07-26T09:00:00+08:00",
    "acceptance_receipt": {"ref": "receipt:[REDACTED]", "sha256": "[REDACTED-SHA256]"}
  }
}
```

Every value above is illustrative. The `1.18.5` strings mirror the executable the operator discovered
locally; they are never a registered version, product authority, or provider acceptance, and the profile
freezes no OpenCode version constant at all — a deployed version is a Binding fact. The real values come
from the operator's own artifact plus the code-owned probe.

**Acceptance is decided by explicit machine fields only.** A generation is admissible only when
`contract_identity.profile_id`, `profile_revision`, and `adapter_contract_hash` equal the live contract's,
every slot projects onto a contract-declared slot of the declared kind, and the artifact plus every path
ancestor pass the trusted-ownership, mode, and digest checks. Everything under `provenance` — including
`created_at`, `accepted_by`, `accepted_at`, and the acceptance receipt reference/hash — is recorded and
reported, never consulted: it cannot authorize a generation, cannot stand in for a missing or mismatched
machine field, and never becomes part of profile identity. A generation carrying a well-formed receipt but
an absent or mismatched `contract_identity` is refused exactly like any other identity mismatch.

A `wrapped_acp` generation carries a `downstream_cli` slot of kind `package_tree` — package root, tree
or canonical manifest digest, launcher path and digest, and the required interpreter identity — plus any
`config_root` slot the profile declared. A `package_tree` slot that declares only a launcher digest is
refused by schema validation: a launcher hash alone does not freeze the sibling code it loads.

The active pointer is a regular file, replaced atomically:

```json
{"schema_version": 1, "generation_id": "gen-0007", "manifest_sha256": "[REDACTED-SHA256]"}
```

Refusal rules, all fail-closed: non-canonical JSON, byte size over the bound, unknown field, unknown
slot, slot missing a required descriptor field, absent `contract_identity`, `contract_identity` mismatch
against the live contract on profile ID, revision, or `adapter_contract_hash`, non-positive epoch, path
traversal, symlink, FIFO, device, non-regular artifact, artifact or ancestor owned outside the trusted
operator/root set, artifact or ancestor writable by the `arsd`/AGENT UID, and digest or probe-version
mismatch. Every refusal names the failing rule.

### `ResolvedLaunchSpec`

Resolved before Run sealing: executable, fixed argv, effective cwd, transport, env allowlist slots and
credential references, profile revision/hash, and schema hash. Credential values enter only at spawn and
are never serialized or represented in `repr`.

It additionally carries the resolved runtime provenance — `adapter_contract_hash`,
`launch_kind`, the wrapped interpreter identity plus its frozen `interpreter_argv_prefix` and the
adapter's complete package closure
(`adapter_package_root`, `adapter_tree_sha256`, entry path/digest), the complete external CLI artifact identity
(path, version, digest, closure kind), Binding `generation_id` plus set and per-slot hashes, the
`session_compatibility_epoch`, and the Binding's acceptance receipt reference carried for reporting
only, never as an authorization input. `launch.json` embeds the resulting
`launch_spec_hash`, and the hash covers the whole record minus exactly that one top-level field.
`AgentRunSpec` keeps its existing field set and continues to seal launch through `launch_spec_hash`.

### `AgentRunSpec`

Immutable, exclusive-created requested fact:

- input/context references and hashes;
- caller owner/namespace and Session reuse expectation;
- profile/launch/schema hashes;
- frozen execution grant, role/capability, workspace, MCP, credential-reference hashes;
- requested model/effort, limits, recovery/evidence policy;
- `spec_hash` excluding generated control fields such as `run_id`/timestamps.

### `EffectiveRunState`

Observed-only: `ProcessIdentity`, Agent/protocol info, capability/config advertisements, external Session
ID, discovery snapshots, and exact effective model/effort. It never alters Profile/Spec.

## 3. Run and Session state

### Native Session record

Stable identity: Agent type, profile revision/hash, external Session ID, owner/namespace, workspace and
credential-slot compatibility. Mutable observations: `last_effective_model/effort`. Persistent state:
`active | closed | quarantined`, reason, source Run. model/effort are not Session identity.

The record also persists `session_compatibility_epoch`. Reuse requires equal profile
ID/revision/`adapter_contract_hash`, equal workspace/owner/namespace, and equal epoch; a missing or
different epoch is refused before the lease is mutated and before `session/load`, with no `session/new`
fallback. The field is additive and omit-when-unset, so legacy and pre-PR-B records keep their exact
serialized shape and stay status/list/close-readable — only `load` fails closed on them. Retaining an
epoch across a Binding change requires an approved continuity canary; otherwise the operator bumps it.

Same Session has one lease and one active Run. A quarantined Session refuses new work. v1 has no
unquarantine tool; successor work uses a new Session with caller-owned context handoff when needed.

### Native Run record

One Run owns one immutable Spec, one launch record, one EffectiveRunState, one EventWriter, zero or one
Turn, two dispatch markers, and one irreversible result. A new retry is an independent Run linked by
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
protocol consumer.

## 5. ACP and exact configuration flow

`NativeAcpDriver` receives an already-spawned process. Its success path is fixed:

```text
initialize
→ verify protocol/capabilities/Agent identity
→ session/new or session/load
→ read complete config options
→ set model
→ consume complete model-dependent options
→ rediscover effort from that fresh set
→ set effort
→ consume updates and exact-read effective pair
→ persist EffectiveRunState
→ ready-to-prompt
```

Any missing/unknown/inexact state raises a stable pre-dispatch failure. Prompt code is unreachable until
the state machine reaches `ready-to-prompt`.

The post-`initialize` identity gate compares observed `agentInfo` name, protocol
version, and advertised capabilities against the contract, and additionally refuses any capability the
contract lists as forbidden. `agentInfo.version` is an ACP-reported fact about the running adapter or
agent implementation; the external CLI `--version` is a separate fact obtained only through the
code-owned probe at `validate`/`promote`. Neither is derived from the other, and no code path may assert
they are equal.

For reuse, `session/load` must return the unchanged external ID and must not emit/perform `session/new`.
Switch rollback targets the prior `last_effective_*` pair and is itself exact-readback gated.

## 6. Dispatch, finalization, and reconciliation

`RunTask` exclusively creates `prompt-dispatch-started` immediately before the wire write and
`prompt-accepted` after successful write. Finalization prioritizes durable reconciliation facts over
ordinary process exit classification.

| Condition | Result | Session |
|---|---|---|
| pre-dispatch failure | `failed` | active unless switch rollback failed |
| trustworthy ACP terminal | matching terminal state | active unless continuity is disproven |
| dispatched; supervisor proves matched child abnormal exit while observation remained intact | `failed` | quarantined |
| dispatched; observation lost/no trustworthy terminal | `unknown`, `retryable=false` | quarantined |

`classify_exit` alone cannot mark a dispatched/no-terminal Run completed or cancelled. Restart preserves
existing terminal results, reconstructs only from trustworthy terminal events, and maps uncertain
started Runs to `unknown/quarantined/retryable=false`. It never calls prompt.

## 7. Permission and workspace evidence

The caller-provided grant is frozen into Spec. `PermissionBridge` maps only registered ACP operations;
unknown classes deny. Decisions record operation family, decision, stable reason, and correlation without
raw secret/payload leakage.

Stage 1 L1/L2 proves deterministic mapping and failure paths. Stage 2 production acceptance uses a real
read-only AGENT canary that attempts a sentinel write. PASS requires a real mediation request, recorded
deny, confirmed failed operation, absent sentinel, and direct pre/post listing of a disposable
known-empty workspace. Zero mediation events is failure, not evidence.

`workspace_hash` remains a canonical binding hash only. v1 adds no content digest service, watcher, or
sandbox claim.

## 8. Storage seam and artifact rules

`native_acp/storage.py` constructs all Native `SessionStore(base_dir=.../native-sessions)` and
`EventStore(base_dir=.../native-runs)` instances. No other Native module constructs a legacy-root store.
Tests seed poisoned same-ID legacy records and prove Native never reads or mutates them; directory listings
and bytes remain unchanged.

Files/directories use `0600`/`0700`, exclusive create or atomic replace as appropriate. One bounded writer
owns each event stream. Credential values, raw env, cookies, authorization headers, and unredacted bulk
payloads never persist.

The Binding root is operator storage that ARS opens read-only and never creates,
writes, repairs, or migrates. `runtime_binding.py` is the only module that opens it, `arsd/admission.py`
is its only Run-path caller, and the read happens exactly once per Run. `run_task.py`,
`reconcile.py`, and finalization have no Binding read path, so a promotion can never re-point work that
is already sealed. `promote`/`rollback` write only `active.json`, atomically, and only from the operator
command surface.

## 9. Service containment and bounded operation

Stage 2 `arsd` starts reconciliation before accepting socket traffic. Per-Run and per-connection tasks
catch all exceptions and convert them to controlled technical results. Global and per-Session concurrency,
queues, events, stderr, output, frames, and backlog are bounded.

Production packaging must demonstrate user-level service semantics equivalent to
`Restart=on-failure`/`KillMode=control-group`. Harness acceptance kills `arsd` after dispatch, proves every
AGENT descendant dies, restarts, verifies `unknown/quarantined/retryable=false` with no second dispatch,
and then proves a new Session/Run succeeds.

G12 required explicit approval of caller UID policy and values before production enablement; it is
closed as a recorded operator decision. The values stay controller-only and reach the daemon solely as
`--caller-mapping` arguments in a mode-`0600` user unit — zero mappings refuse to listen.

## 10. Tests and evidence

- **L1 pure/unit:** Spec/profile/schema hashing, root wiring, status round-trip, terminal table, markers,
  Session binding/switch rollback, mediation mapping, event bounds, UDS frame/ownership helpers.
- **L2 hermetic ACP child:** real stdio JSON-RPC framing for malformed/inexact/timeout/cancel/load/switch/
  rollback/event-flood/reconciliation faults. Fake is never product runtime or production evidence.
- **L3 real:** the registered closed profiles with exact model/effort readback, same-Session historical
  token continuity, denied-action canary, and cgroup crash containment. Real-runtime suites are opt-in,
  skip-by-default, and never run in CI; sanitized evidence stays operator-held.

**Runtime Binding test families**, all hermetic and built over synthetic Binding roots and fake artifacts:
Binding schema/canonicalization/size/unknown-field refusals; path-shape refusals (traversal, symlink,
FIFO, device, non-regular); ownership/mode/ancestor refusals including artifacts writable by the
`arsd`/AGENT UID; contract-acceptance and stale-generation fail-closed; probe-versus-manifest version
mismatch; digest mismatch at admission and again at the spawn-boundary recheck through the existing
deterministic race seam; descriptor-pinned exec for `direct_acp` and package-closure enforcement for
`wrapped_acp` on both the downstream CLI and the ACP adapter — including a sibling/dependency byte
change that leaves the entry file untouched, a new file inside the closure, an unsafe or
closure-escaping tree entry, and a `node_modules` above the closure root — each proven before spawn and
again after the race seam; the frozen interpreter argv prefix dropped, reordered, altered, or padded
with an extra option, each refused before spawn; read-once instrumentation proving one `active.json` read plus one generation read per Run
and zero reads during spawn, finalization, and reconciliation; epoch reuse/rejection with no
`session/new` fallback; the absence of any caller-facing runtime-selection field; the absence of
`--force` and of any privilege escalation in the command path; and provenance recomputation that
excludes exactly one top-level field.

Stage 1 direct-drive real evidence is B-grade only. Stage 2 socket-path S1–S5 is the only C-grade
production acceptance.

## 11. Implementation and rollback boundaries

- Stage 0/1 (`native_acp/` plus the shared additive seams) and Stage 2 (`arsd/`, production acceptance,
  G12) have landed under their own approvals. Landing them authorized nothing further: dependency/lock,
  `pyproject.toml`, CI, profile-registry, and unit/caller-policy changes each need their own approval.
- Publication (version bump, tag, GitHub Release, PyPI) is separate from implementation; the published
  wheel carries the Stage 0/1 core only.
- Sachima `ArsdBackend` and pin changes are later work.
- Rollback disables Native ingress; no auto-fallback to acpx and no terminal fact rewrite.
- The Runtime Binding source framework has landed. That merge authorizes no promotion against a real
  Binding root, no artifact-root preparation or installation, no re-acceptance at a current profile
  revision, no service restart, no publication, and no real-provider acceptance; each remains a separate
  operator decision. The wrapped adapter's package closure has since landed under its own approval and
  bumped both wrapped profile revisions (Codex r3, Claude r4), which retires their prior Binding
  generations by contract hash and leaves re-acceptance an open operator action. The compatibility
  invariant is explicit and held: `AgentRunRequest`/`AgentRunSpec` field
  sets, the `arsd` v1 public wire, the result/event grammar, reconcile semantics, and the
  `ManagedProcess` public API stay unchanged, old Runs stay readable, and old Native Sessions stay
  status/list/close-readable while `load` fails closed.

The executable slice sequence, fresh worktree/branch rules, exact commands, and separate push/PR/merge
approvals live only in `docs/plans/active/`.

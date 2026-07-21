---
title: "agent-run-supervisor vNext Technical Solution"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-21
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/technical-solution.md"
---
# agent-run-supervisor vNext Technical Solution

## 0. Scope and implementation status

This is the module-level design authority for new ARS work. All described vNext modules are planned until
the roadmap records verified implementation. The previous mixed v0.1.7/vNext solution is preserved at
`docs/archive/pre-vnext-reset-2026-07-21/technical-solution.md` and must not direct new development.

ARS stays Python. vNext extends the existing package additively, preserves the released acpx path as a
compatibility baseline, and never uses it as Native driver or fallback.

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
| `profile.py` | typed, versioned, closed `AgentProfile` registry; first `OPENCODE_1_18_4` profile |
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
| `handlers.py` | submit/status/events/cancel and Session create/status/close/list with owner checks |
| `reconcile.py` | startup-only reconciliation; no prompt replay/resume |
| `client.py` | typed local caller for Hermes/CLI |
| `__main__.py` | unprivileged daemon entrypoint; no state authority beyond `arsd` core |

No TCP, root mode, runtime plugin loader, arbitrary command adapter, or per-Run Worker is introduced.

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

### `ResolvedLaunchSpec`

Resolved before Run sealing: executable, fixed argv, effective cwd, transport, env allowlist slots and
credential references, profile revision/hash, and schema hash. Credential values enter only at spawn and
are never serialized or represented in `repr`.

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

## 9. Service containment and bounded operation

Stage 2 `arsd` starts reconciliation before accepting socket traffic. Per-Run and per-connection tasks
catch all exceptions and convert them to controlled technical results. Global and per-Session concurrency,
queues, events, stderr, output, frames, and backlog are bounded.

Production packaging must demonstrate user-level service semantics equivalent to
`Restart=on-failure`/`KillMode=control-group`. Harness acceptance kills `arsd` after dispatch, proves every
AGENT descendant dies, restarts, verifies `unknown/quarantined/retryable=false` with no second dispatch,
and then proves a new Session/Run succeeds.

G12 requires explicit approval of caller UID policy and values before production enablement.

## 10. Tests and evidence

- **L1 pure/unit:** Spec/profile/schema hashing, root wiring, status round-trip, terminal table, markers,
  Session binding/switch rollback, mediation mapping, event bounds, UDS frame/ownership helpers.
- **L2 hermetic ACP child:** real stdio JSON-RPC framing for malformed/inexact/timeout/cancel/load/switch/
  rollback/event-flood/reconciliation faults. Fake is never product runtime or production evidence.
- **L3 real:** OpenCode 1.18.4 profile, K3/max exact readback, same-Session historical token continuity,
  denied-action canary, and cgroup crash containment.

Stage 1 direct-drive real evidence is B-grade only. Stage 2 socket-path S1–S5 is the only C-grade
production acceptance.

## 11. Implementation and rollback boundaries

- Stage 0/1 may modify dependencies/lock, shared additive seams, `native_acp/`, and tests only after
  explicit implementation approval. It does not add `arsd`, deploy, release, or change Sachima.
- Stage 2 adds `arsd` and production acceptance only after separate approval and G12 resolution.
- Sachima `ArsdBackend` and pin changes are later work.
- Rollback disables Native ingress; no auto-fallback to acpx and no terminal fact rewrite.

The executable slice sequence, fresh worktree/branch rules, exact commands, and separate push/PR/merge
approvals live only in `docs/plans/active/`.

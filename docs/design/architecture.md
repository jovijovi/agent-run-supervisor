---
title: "agent-run-supervisor vNext System Architecture"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-01
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/architecture.md"
---
# agent-run-supervisor vNext System Architecture

## 0. Scope and status

This is the system architecture authority for **new ARS development**. It describes the settled vNext
target after the V4 external-AGENT boundary reset, not the released v0.1.7 topology and not the retired
artifact/Binding architecture. The previous mixed document is preserved at
`docs/archive/pre-vnext-reset-2026-07-21/architecture.md`; the retired Binding-era sections §3.1–§3.3 are
preserved at [`docs/archive/binding-era-2026-07/architecture-3.1-3.3.md`](../archive/binding-era-2026-07/architecture-3.1-3.3.md).

Status markers:

- ✅ legacy released code still present on `main`, untouched by the reset and not a compatibility target;
- 🟦 vNext supervision plane, implemented on `main` (Stage 0/1 closed, Stage 2 closed);
- 🟨 settled target of the boundary reset, **not implemented in any released or merged source**;
- ⏸ separately approved later integration.

🟨 is the honest marker for most of §3, §4, §6, and §8 below. Source on `main` still runs the retired
Binding line: a Binding reader, artifact digests, promotion, attestation, a required Binding-root daemon
flag, and four registered profiles. What 🟨 no longer means is "unwritten": the Stage 3 candidate
implements these sections on a task branch, verified locally and **uncommitted, unmerged, unreleased, and
undeployed**. A reader operating a deployment gets nothing from that branch, which is why the marker
stays. This document is the target; the board
([`docs/roadmap/current-status.md`](../roadmap/current-status.md)) carries the exact
authority-versus-source delta and the staged sequence that closes it. No marker here is an approval.

## 1. System context and ownership

```text
╔══════════════════════════════════════════════════════════════════════════════╗
║ CALLER DOMAIN — business authority (outside ARS)                             ║
║ owns: user intent, task graph, agent + model + effort choice, business        ║
║       authorization, the frozen execution_grant, retry policy, delivery       ║
╚═══════════════════════════════╤══════════════════════════════════════════════╝
                                │  AgentRunRequest (versioned)
                                │  agent_id · model · effort · grant · limits
                                │  NO command / argv / env / path / secret value
                                ▼            AF_UNIX 0600 inside a 0700 dir
╔══════════════════════════════════════════════════════════════════════════════╗
║ ARS DOMAIN — one unprivileged local process (arsd), one trust domain          ║
║  startup ──── parse the agents file ONCE → immutable snapshot                 ║
║          ──── reconcile (ordered, fail-closed) → then bind the socket         ║
║  ingress ──── SO_PEERCRED · caller-UID policy · owner/namespace scoping       ║
║  admission ── resolve profile · resolve agent from the snapshot (no I/O) ·    ║
║               bind workspace · validate grant · resolve child env in memory · ║
║               seal Spec then launch snapshot                                  ║
║  RunTask ──── ManagedProcess (PID/PGID · bounded stderr · timeout · signals)  ║
║          ──── NativeAcpDriver (stdio JSON-RPC · exact-config machine)         ║
║          ──── PermissionBridge (default-deny against the frozen grant)        ║
║          ──── EventWriter (bounded · monotonic · redacted)                    ║
║  ARS-OWNED WRITABLE SURFACES — exactly two:                                   ║
║     <supervisor_root>/native-runs/ · native-sessions/                         ║
║     the configured UDS runtime path (dir create · socket · chmod · replace)   ║
╚═════════╤════════════════════════════════════════════╤═══════════════════════╝
          │ read-only, exactly once at startup         │ spawn + supervise
          ▼                                            ▼
╔═════════════════════════════╗          ╔═════════════════════════════════════╗
║ OPERATOR DOMAIN (read-only) ║          ║ AGENT DOMAIN — not owned by ARS      ║
║ agents file (may live below ║  read-   ║ agent / adapter executable            ║
║ $HOME, may be a symlink)    ║  only    ║ $HOME · auth store · plugins · caches ║
║ agent_id → command + args   ║ ───────► ║ user + project config · MCP servers   ║
║ ARS never writes it         ║  resolve ║ MAY FREELY WRITE ALL OF THE ABOVE     ║
╚═════════════════════════════╝  + exec  ║ owns conversation/context state       ║
                                         ╚══════════════╤══════════════════════╝
                                    ┌───────────────────┴──────────────┐
                                    │ user-level service manager/cgroup │
                                    │ owns daemon liveness and crash-   │
                                    │ time descendant kill. Owns no     │
                                    │ Run/Session/business state.       │
                                    └───────────────────────────────────┘
```

| Entity | Owner | ARS may | ARS must never |
|---|---|---|---|
| AGENT / adapter executable | user, via their package manager | resolve and spawn it exactly as declared, wherever it lives; record what it observed | copy, unpack, install, relocate, digest-gate, promote, freeze, or substitute a resolved path for it |
| AGENT `$HOME`, auth store, plugins, caches, user + project config, Session state | AGENT / user | project declared environment names; read nothing there for control purposes | create, populate, stage, mirror, write, manage, inspect as a control surface, stat-audit, digest, or require-absent |
| AGENT writes to its own HOME/config/cache/Session state during a Run | AGENT | expect and permit them | treat them as a violation, diff them, or fail a Run over them |
| Agent registry file | operator | open read-only, exactly once at daemon startup, wherever it lives, including through a symlink; refuse to listen on defect | write, create, repair, migrate, promote, or re-read while serving |
| ACP compatibility profiles | ARS source | version, register, revise under review with cited evidence | contain any path, version, digest, model literal, or deployment fact |
| Permission-mediation env binding | ARS source (key **and** value) | apply last, unconditionally | let a registry entry author, replace, or disable it |
| Environment values reaching the child | operator for ambient/pass-through/overlay; ARS source for mediation | resolve once in memory, construct the guard, and hand only the mapping to exec | copy into ARS evidence, hash, log, echo in an error, or return from any API |
| Per-Run Spec + launch snapshot | one Run | seal before spawn; write once | re-read, re-resolve, or mutate after sealing |
| Observed runtime evidence | one Run | record; report; warn | use as an admission gate or a continuity blocker |
| Process created by the Run | ARS (lifecycle only) | PGID, signals, wait, reap, bound | claim isolation or containment of hostile code |
| Conversation/context state | external AGENT | store the external session id + observations | become a second conversation database |

`arsd` is unprivileged, local, single-trust-domain infrastructure. It is not root, TCP/public, distributed,
multi-tenant, or a business scheduler. Direct ars-core is test/dev-only; production has no in-process or
acpx fallback.

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

The released `execute_subprocess → SubprocessOutcome` is ✅ legacy acpx-only code the reset leaves untouched,
not a compatibility surface this architecture owes anything to; its `stdin=DEVNULL`, stdout-drain threads,
and wait-before-return shape cannot carry Native ACP.

## 3. Admission, spawn, and the ACP flow 🟨

```text
 S1 parse the agents file ONCE → immutable snapshot        ✗ REGISTRY_* → refuse to listen
 S2 reconcile, ordered and fail-closed (§6)                ✗ → refuse to listen
 S3 bind UDS 0600 inside 0700, accept connections
 ── the registry is never opened again for the daemon's whole lifetime ──

  1 caller connects; SO_PEERCRED → peer uid/gid/pid
  2 caller-UID policy → principal_id, owner, namespace        ✗ PEER_UID_DENIED
  3 decode bounded frame; api_version checked per operation    ✗ UNSUPPORTED_API_VERSION
  4 parse AgentRunRequest (bounded plain values only)         ✗ INVALID_REQUEST
  5 idempotency: run_id = f(principal_id, request_id); keyed
    admission lock; write-once submission record              ✗ IDEMPOTENCY_CONFLICT
  6 validate agent_id grammar                                 ✗ AGENT_ID_INVALID
  7 resolve agent_id against the STARTUP SNAPSHOT — pure
    in-memory lookup, ZERO filesystem access                  ✗ AGENT_NOT_REGISTERED
  8 resolve the entry's profile from the source registry      ✗ UNKNOWN_PROFILE
  9 bind workspace (canonical root, effective cwd, hash)      ✗ WORKSPACE_*
 10 validate the frozen execution_grant + referenced refs     ✗ GRANT_INVALID
 11 build argv = [command_declared, *args]; RESOLVE THE FINAL
    CHILD ENVIRONMENT ONCE, IN MEMORY, and construct its
    ephemeral per-Run literal guard. No command resolution.
    No realpath. No value is serialized or hashed.
 12 compute the Spec and the launch snapshot IN MEMORY; the
    snapshot carries env NAMES + source class + precedence
    only; launch_hash covers that value-blind material
 13 SEAL, in this order:
      a. write-once spec.json  (+ spec_hash, incl. launch_hash)
      b. write-once launch.json (+ launch_hash)
    ── nothing is re-read, re-resolved, or re-derived after 13a
 14 derive one closed session start plan (§4):
      · non-reuse only → new-session plan
      · reuse only → open the existing record, validate it for
        load, require its external id → load plan
    BEFORE the lease; no reuse failure can create a Session   ✗ SESSION_*
 15 acquire the Session lease (one active Run per Session)     ✗ SESSION_BUSY / QUARANTINED
 16 SPAWN: new POSIX session + process group; exec the declared
    command through declared PATH/shim/symlink semantics with
    declared argv[0]; hand it the in-memory environment from
    step 11; record ProcessIdentity immediately; bounded stderr
    enters only through the byte-aware Run guard
      · exec ENOENT  → COMMAND_NOT_FOUND
      · exec EACCES  → COMMAND_NOT_EXECUTABLE
      · other        → SPAWN_FAILED
 17 record guarded, non-authoritative resolution evidence
 18 ACP initialize over the child's stdin/stdout
      · protocol major must equal the profile's               ✗ PROTOCOL_MISMATCH
      · required capabilities present                         ✗ CAPABILITY_MISSING
      · forbidden capabilities absent (floor ∪ entry)         ✗ CAPABILITY_FORBIDDEN
      · record agentInfo{name,version} — EVIDENCE, gates nothing
 19 execute the closed start plan:
      · new-session plan  → session/new
      · load plan         → session/load with the stored id, and
        never session/new under any return or exception        ✗ SESSION_LOAD_FAILED
 20 read the complete config option set (live discovery)
 21 set requested model → exact                               ✗ CONFIG_*
 22 consume the model-dependent option set; rediscover effort
 23 set requested effort → exact
 24 exact readback: requested == effective, literal, no coercion ✗ CONFIG_INEXACT
    (a compatibility profile additionally proves its required mode)
 25 persist guarded observed runtime state; ready to prompt
 26 marker: prompt-dispatch-started      ← THE UNCERTAINTY BOUNDARY
 27 write the prompt frame
 28 marker: prompt-accepted
 29 at each callback entry, reject a conflicting session id
    before any sink or handler; then guard normalized updates
    and mediate every permission / fs / terminal request
    default-deny against the frozen grant
 30 terminal ACP event | turn timeout | cancel | child exit
 31 finalize: one irreversible result.json; Session state;
    lease release; ACP close; terminate_group → grace →
    kill_group; wait; reap; guarded bounded stderr + a
    value-blind redaction/suppression report
```

**Fail-closed invariants.** Steps 1–25 all fail *pre-dispatch*: the Run is `failed`, no prompt is written,
and the Session remains reusable unless a between-Run config-switch rollback could not be proven exactly.
Step 26 is the only irreversible line. **The Run path never reopens the operator registry and never reads
AGENT auth/config/cache/plugin/Session state as a control surface.**

**Deliberately absent versus the retired line:** no pointer read, no generation read, no manifest digest, no
contract-identity match, no slot projection, no artifact digest, no ownership/mode/ancestor walk, no
`O_PATH` pin, no hash-through-inode, no descriptor-pinned exec, no credential-root structural check, no
project-config closure check, no TOCTOU recheck, no `attestation.json`, and no epoch staleness comparison.

### 3.1 The four-way boundary 🟨

| | **A. Source profile** | **B. Operator registry snapshot** | **C. Sealed per-Run Spec + launch** | **D. Observed evidence** |
|---|---|---|---|---|
| Owner | ARS source, under review | operator, editable at will | one Run, immutable from seal to terminal | one Run, append-only |
| Lives in | Python source | an operator file outside the repository → immutable in-memory snapshot | `native-runs/<run_id>/{spec,launch}.json` | `effective.json`, `events.jsonl` |
| Written by | a release | the operator's editor | ARS, exactly once, before spawn | ARS, during the Run |
| Read by ARS | at import | **once, at daemon startup** | once, at seal | continuously, during the Run |
| Changing it costs | a source release + review | an atomic file replacement, effective at the next daemon start | nothing — it is per-Run | nothing — it is a record |
| Never contains | any path, version, digest, model literal, agent name, or deployment fact | any digest, approved version, promotion state, or receipt | anything re-read after sealing; **any environment value** | anything that gates admission or blocks continuity |

Four directional rules make it real:

1. **A never learns from B or D.** No profile field is derived from operator data or from an observation.
2. **C is a projection of A × B × request, taken once.** After the Spec write nothing re-reads the snapshot,
   re-resolves a command, re-reads the ambient environment, or re-derives argv. An in-flight Run is never
   re-pointed, and neither is a queued one.
3. **D never flows backward.** Observations are compared only against *the request* (exact model/effort
   readback) and against *the profile's protocol/capability contract* — never against a frozen artifact
   constant, and never against a prior Session's self-report.
4. **The wire never reaches B's value space.** `agent_id` is validated as a bounded key *before* any lookup
   and names no path, argv token, env key, digest, or version. Command, argv, environment, and secret values
   are not fields on the request, and a structural test asserts they never become fields.

There is no fifth layer, no promotion, no digest gate, and no ARS-owned copy of anything the AGENT needs to
run. Operator-facing registry detail — schema, grammar, bounds, refusals, environment layers, restart
semantics, and worked examples — is normative in [`agent-registry.md`](agent-registry.md).

### 3.2 Registered command semantics are preserved exactly 🟨

> **ARS executes the operator-declared command through its declared PATH, shim, symlink, and `argv[0]`
> semantics. It never substitutes a resolved path as the executable or as `argv[0]`.**

- `argv[0]` is the **declared `command` string, byte-for-byte** — a bare name stays a bare name, exactly as
  a shell would pass it.
- The exec image is located by ordinary `execvp`-style lookup over the **child's** projected `PATH` when
  `command` is a bare name, and by the declared absolute path otherwise. No `executable=` override, no
  `/proc/self/fd/N` image, no realpath.
- Consequences preserved by construction: version-manager and package-manager shims work; symlink farms
  work; package-relative and require-relative resolution from the real script location works;
  multicall/`argv[0]` dispatch works; and an agent's own self-update and self-relaunch logic works.
- **There is no pre-flight resolution gate.** A pre-flight lookup would introduce a second resolution that
  can disagree with exec, a TOCTOU window, and a false-refusal risk. ARS classifies the exec failure itself,
  and those classifications are ordinary configuration errors, never security refusals. Because the child's
  exec failure is reported to the parent before spawn returns, no process exists in those cases.

### 3.3 Resolution facts are observations, never authority 🟨

After a successful spawn ARS records, best-effort, into `effective.json`: the declared command and exact
argv (already sealed in `launch.json`); the first `PATH` hit for a bare command under the projected launch
`PATH`, when one can be computed; `/proc/<pid>/exe`, the image the kernel actually mapped (for a script shim
that is the interpreter, for a wrapper the wrapper — both are correct and useful); `agentInfo` name and
version, protocol version, and advertised capabilities; and an optional operator-run version-probe result.

Every one carries an explicit non-authoritative marker. **No code path compares any of them against a source
constant, a prior Run, a Session record, or a registry value to decide admission or reuse.** Divergence, or
drift in `agentInfo` or advertised capabilities between two Runs of one Session, is recorded and may be
emitted as a **policy warning event** — never a refusal, never a continuity block. The complete set of
observation-based refusals is protocol major, required capabilities, forbidden capabilities, exact config
readback, and a compatibility profile's required permission mode.

## 4. Process-per-Run Session model 🟨

Cardinality:

```text
ARS Session 1 ── N Runs (strictly serial under lease)
Run 1 ── 0..1 Turn
Run 1 ── 1 external AGENT process
true parallelism = multiple Sessions
```

Each Run launches a new AGENT process. The first Run uses `session/new`; later Runs use `session/load` with
the same opaque external session ID. The AGENT owns conversation/context storage; ARS stores only the
binding and observed metadata.

### 4.1 Identity is operator-owned and ARS-owned; proof is AGENT-owned

Continuity is a property of the external AGENT's own ability to load its own session. ARS records a stable
binding, refuses reuse when the *binding* changed, and lets the AGENT prove the rest. An ARS hash of an
operator's file never proved continuity and stops pretending to.

| Session identity field | Owner | Moves when |
|---|---|---|
| `agent_id` | operator | the caller targets a different registered agent |
| `profile_id`, `profile_revision`, `profile_hash` | ARS source | **ACP semantics** move — never on an ordinary release |
| `owner`, `namespace` | ARS | the authority binding changes |
| `workspace_hash` | ARS | bound resources change |
| `session_epoch` (optional) | **operator, explicitly** | the operator decides to cut continuity — never otherwise |

**No automatic epoch bump exists anywhere.** An AGENT or adapter version change, an ARS package upgrade, a
profile revision that does not change ACP semantics, a `command`/`args`/`env`/`mediation`/selector edit, a
registry file replacement, and a daemon restart never change it. There is no code path that derives,
increments, or infers an epoch from an observation, a digest, a version, or a file's bytes. Comparison is
symmetric equality, so a record at epoch 1 is refused by a Run at epoch 2 *and* by a Run with no epoch —
which is why **adding an epoch for the first time cuts that agent's existing Sessions: absent ≠ 1**.

**Must not invalidate reuse:** an agent CLI or adapter version change; an `agentInfo` name or version change;
the observed executable, mapped image, path-lookup hit, probe result, or any file hash; observed capability
drift between Runs of one Session; a `command` path change from a repointed shim or reinstall; an `args`,
overlay, pass-through, mediation, or selector-hint edit; the registry file's bytes, digest, mtime, or
location; an ARS release that does not change ACP semantics; and any digest, tree hash, ownership, or mode
change — those concepts no longer exist.

**Deleted as Session-identity fields:** the adapter contract hash, the ARS-derived compatibility epoch, and
the agent-registration hash. The operator-controlled `session_epoch` replaces the last of these and shares
nothing with it but the idea of an epoch.

**The trade-off, stated honestly.** An operator can edit `args` and restart, and the next Run reuses an
existing Session. If that edit changed agent behavior materially, the change is *recorded* — full argv and
the complete value-blind environment name/source/precedence material live in every Run's launch snapshot —
but not *refused*, unless the operator also bumps `session_epoch`. The alternative, fingerprint-as-gate, is
precisely the failure mode the reset exists to remove.

### 4.2 The load proof and the closed start plan

Reuse is proven when all four hold:

1. ARS sends the **stored external session id, byte-unchanged**, as the session argument of `session/load`,
   together with the bound `cwd` and — on a compatibility profile — the frozen session metadata.
2. The call returns a **successful load response** whose `config_options` seed the fidelity machine. **No
   identity field is required from, or read out of, the response.**
3. ARS **never emits `session/new` on a reuse path** — not as a fallback, not after a failure, not under any
   error class. This is structural, not conditional.
4. **Every conflicting ID-bearing callback is rejected at callback entry**, before it can be serviced.

The start plan is a closed union derived from the immutable request: a load plan exists only after an
existing record is opened existing-only, passes the load-time binding validation, and supplies its stored
external ID; a new-session plan is constructible only from a request whose reuse intent is "none". The
startup sequence dispatches on the plan with disjoint arms, no default arm, and no conversion between plan
types, so the load arm may only load and the new arm may only create. Every reuse failure — missing or
corrupt record, missing external ID, binding mismatch, busy or quarantined lease, initialize or load
capability failure, load RPC failure, callback identity violation, option-discovery failure, config
inexactness, cancellation, timeout, child exit, cleanup failure — terminates without reaching the
new-session call.

The callback boundary is synchronous and fail-closed: compare against the expected bound ID; on unbound or
different, record only a stable categorical violation and raise, with no IDs in the error text; only after a
match may the callback normalize data, enqueue an event, invoke a permission or filesystem handler, touch
the workspace, formulate a terminal or elicitation response, or return success. No `finally` block may
service or persist a rejected callback. Because the expected ID is bound before the load request is issued,
callbacks racing with `session/load` are covered; before a new session's ID is bound, any ID-bearing
callback is an unbound-identity violation.

Failure handling is unchanged in terminal meaning: a failed load or a pre-dispatch identity violation yields
`failed` with a stable code and `retryable=false`, and the Session stays `active` and readable, because a
clean pre-dispatch refusal is not uncertainty. An identity violation *after* the dispatch marker yields
`unknown` plus quarantine.

Between completed Runs on the same Session, model/effort may change:

```text
previous Run terminal → acquire lease → spawn → initialize
→ session/load(same external ID)
→ discovery → set model → rediscovery → set effort → exact readback
→ persist observed state → dispatch markers → prompt
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
| external session identity violation observed after dispatch | `unknown`, `retryable=false` | no; quarantine |

An `unknown` Run is never retried, replayed, resumed, or rewritten. Caller-authorized successor work is a
new Run linked by `retry_of_run_id`. There is no unquarantine tool.

## 6. Crash containment and ordered reconciliation

Production places `arsd` and every external AGENT descendant in one user-managed cgroup with semantics
equivalent to `Restart=on-failure` and `KillMode=control-group`.

```text
arsd crash/SIGKILL
→ service manager kills the descendant tree still inside that cgroup
→ restarted arsd parses the registry, then reconciles durable facts
→ uncertain dispatched Runs become unknown/quarantined/retryable=false
→ no prompt redispatch
→ accept later independent Runs only after reconciliation completes
```

Normal cancellation/graceful shutdown uses ACP cancel and process-group escalation. Crash cleanup uses the
external cgroup. These mechanisms are distinct. Full process identity, not PID/name/port guessing, governs
any liveness or orphan decision.

Every RunTask and connection has a top-level exception boundary. Malformed ACP, SDK, normalization,
evidence I/O, and child faults terminate only that Run. Queues, events, stderr, output, concurrency,
Session activity, and socket backlog are bounded.

### 6.1 Absent is not corrupt 🟨

Reconciliation classifies **all** inputs before any write, using bounded no-follow readers:

| Input | Closed states | Meaning |
|---|---|---|
| terminal (`result.json`) | trusted-non-unknown, trusted-unknown, corrupt, absent | trusted terminals split by status, because `unknown` additionally requires a quarantine convergence check |
| dispatch marker | present, absent | **present** when either marker name is found, regardless of contents or file type; a symlink, directory, malformed marker, or any I/O result that cannot prove clean absence is conservative evidence that dispatch may have happened |
| Spec, launch, submission | valid, absent, corrupt | **absent only on a clean no-such-path result**; valid requires a bounded regular non-symlink file, a supported schema, the exact run id, known fields and types, and artifact-specific invariants; every other present-or-indeterminate state is corrupt |

A race, symlink, FIFO, directory, oversize file, short or failed read, or any error after observed presence
is **corrupt — never a second chance to become absent**, and the open never blocks. A corrupt artifact is
never relabeled absent, though it is not automatically fatal when a higher-priority fact has already made it
irrelevant.

### 6.2 Attribution authority and outcome vocabulary 🟨

Owner/namespace/Session attribution has one priority order: a **valid Spec is authoritative** and supplies
owner, namespace, reuse intent, and Session id, with the submission ignored for attribution even when
absent, corrupt, or conflicting; a **valid submission is a fallback** only when the Spec is not valid, and
is sufficient only to fence a possibly dispatched Run or safely scope a terminal record; and launch records,
result fields, directory names other than the deterministic ephemeral derivation, progress, events, locks,
and marker contents are **never** attribution authority.

For any outcome that requires quarantine, attribution is **actionable** only when the chosen identity
resolves to an already-existing, strictly readable Session record whose id, owner, and namespace match and
whose state is open/active or already quarantined. Reconciliation never creates, reopens, or repairs a
Session; non-actionable attribution means startup is refused rather than guessed.

One exhaustive first-match table covers every combination of the five classified inputs, and its whole
outcome vocabulary is exactly four results: **authoritative terminal**, **`unknown` + quarantine**,
**pre-dispatch failed/reusable**, or **refuse to listen**. Trusted non-unknown terminals are preserved
byte-for-byte with no Run or Session mutation. A trusted `unknown` converges the fence, quarantine, and
progress when attribution is actionable and refuses startup otherwise. A corrupt terminal always refuses,
quarantining first only when dispatch is possible and attribution is actionable, and is never rewritten or
deleted. Possible dispatch without a terminal becomes `unknown` + quarantine exactly when attribution is
actionable. A valid Spec with an absent or valid launch and no marker settles as pre-dispatch
failed/reusable — a missing launch is the allowed crash point between the ordered Spec and launch writes —
while a corrupt referenced launch, a corrupt Spec, a launch without its Spec, or a corrupt submission on an
otherwise empty tree all refuse to listen.

Write ordering is exact and crash-idempotent: fence → quarantine → categorical progress → terminal last,
with the terminal written only when no trusted terminal existed. If any earlier step fails, no new terminal
is written and startup is refused. A crash after any step leaves a non-leasable fence, a quarantined
Session, or both, and the next startup resumes the same outcome. Reconciliation never sends ACP, spawns a
process, reconstructs a prompt, acquires or removes a lease, creates a Session, or opens the registry.

## 7. Permission and caller boundary

Callers decide and freeze business authorization. ARS authenticates the UDS peer, binds ownership, and
enforces `execution_grant` default-deny without widening or live-policy refresh.

- Registered read operations may be allowed within the bound workspace.
- write/create/delete/terminal/execute/fetch and unknown operations deny unless the frozen grant and
  registered mediation contract explicitly permit them.
- Every mediation decision produces redacted evidence.
- A real denied-action canary is mandatory per registered agent; zero mediation events prove nothing.
- `allowed_roots`, UDS auth, and ACP mediation are not OS sandboxing or hostile-process containment.

🟨 **Mediation environment authority.** Mediation env routes an agent's privileged in-process tool families
through ACP permission requests so the bridge decides *before* a side effect. If configuration could disable
it, the default-deny claim would be decorative. Therefore: the binding is source-owned in **key and value**,
keyed by the capability family it mediates rather than by the agent that needs it; a registry entry may
**select** one id or none and can never author a pair, a key, or a value; reserved mediation keys are the
**union of every key in any registered binding**, so the rule does not depend on which binding an entry
chose or whether it chose one; a collision in an entry's overlay or pass-through fails the registry parse and
**the daemon refuses to listen**, with the identical check available offline at authoring time; and layer 4
is applied last anyway as defense in depth, so a defect in the collision check cannot silently disable
mediation. A profile-registry construction invariant asserts the base allowlist and the reserved key set are
disjoint. There is no "mediation off" and no per-entry key/value form.

**Honest limit.** Mediation is cooperative. An agent that ignores the knob, or one with no registered
binding, can execute in-process tools with no ACP permission event and the bridge will never see them. The
mandatory denied-action canary proves the knob works *for a specific agent* and must precede that agent's
use.

Exact caller UID values and policy ownership are gate G12, closed as a recorded operator decision; the
repository stores no production mapping value.

## 8. Storage and evidence

```text
.agent-run-supervisor/
├── native-runs/<run_id>/
│   ├── spec.json                  # immutable; exclusive create
│   ├── launch.json                # sealed launch snapshot; value-blind
│   ├── effective.json             # observed identity/capabilities/config
│   ├── events.jsonl               # single writer; monotonic seq; bounded
│   ├── result.json                # one terminal fact
│   ├── prompt-dispatch-started
│   ├── prompt-accepted
│   └── evidence / redaction / bounded stderr
└── native-sessions/<session_id>/
    ├── session.json               # stable binding + last_effective_* + state
    └── lock.json                  # lease/process identity while held
```

**ARS-owned writable surfaces are exactly two, and nothing else:** the supervisor root above, through the
single `native_acp/storage.py` write-once seam, and the configured UDS runtime path, where ARS creates the
parent, replaces a stale socket, binds, chmods `0600`, and unlinks at shutdown.

**Read-only access ARS is explicitly permitted, wherever the paths live:** the operator registry file, once
at startup, including through a symlink and including below `$HOME`; everything the kernel and loader need
to resolve and launch the declared command — `PATH` directory lookup, a PATH shim, a symlink chain, an
interpreter, a package-relative module tree — including below `$HOME` and including `argv[0]`-dispatch
layouts; `/proc/<pid>/exe` and process-liveness reads for its own child; and the caller-bound workspace, as
already bounded by workspace binding.

**What ARS is prohibited from doing:** ARS never creates, writes, populates, stages, mirrors, repairs,
deletes, or otherwise **manages** AGENT auth, configuration, cache, plugin, or Session state, and never
**inspects** those surfaces as a control surface — no stat audit, no mode or ownership enforcement, no
digest, no required-absence check. ARS models no path inside the AGENT's state at all. The child AGENT may
mutate its own HOME and state normally during a Run, and such a Run completes normally: those writes are the
child's, and the attribution boundary is the process boundary. Acceptance instruments write-intent calls
inside the `arsd` process only, so the child's writes are invisible to that interceptor by construction.

🟨 `launch.json` carries the declared command, the exact argv, the effective cwd, the environment
**name/source-class/precedence** material with **no values**, the mediation id, profile identity,
registry-source evidence, and its own value-blind `launch_spec_hash`, so the record is self-verifying. The
hash excludes exactly one top-level field, `launch_spec_hash`, and nothing else may be excluded. The
operator registry file is operator storage outside `.agent-run-supervisor/`; ARS reads it once and never
writes it. There is no `attestation.json` and no Binding root.

The workspace canonical root and effective `cwd` in `spec.json` remain complete literals and remain
hash-covered even when the workspace lives under `$HOME`. They are independently derived authority facts,
not environment-value flow, and are deliberately outside the guarded sink set: guarding them would break
workspace binding, reconciliation attribution, and audit.

`native_acp/storage.py` is the only constructor seam for Native roots. Legacy `runs/`/`sessions/` and acpx
storage are never read, written, imported, mirrored, or migrated by Native code.

The runtime ledger records supervision facts, not AGENT conversation memory. v1 no-change acceptance uses a
disposable known-empty workspace and direct pre/post directory listing; `workspace_hash` is only a binding
hash. No content-digest service or filesystem watcher is part of ARS.

Evidence grades: A — pre-implementation compatibility context; B — direct-drive real-AGENT evidence; C —
`arsd` socket-path production acceptance. No lower grade can claim a higher one.

## 9. Deployment stages

| Stage | Target | Evidence | Production claim |
|---|---|---|---|
| 0 | SDK/source/API/consumer/load capability gates | deterministic preflight | none |
| 1 | ManagedProcess + Native ACP core + state/session/permission/evidence | L1/L2 + real direct-drive B-grade | none |
| 2 | `arsd` UDS, ownership, reconciliation, cgroup containment | real S1–S5 C-grade | ARS production acceptance |
| 🟨 reset | authority alignment, then fail-closed reuse + reconciliation, then the environment-value guard, then the boundary reset | per-gate hermetic suites; documentation gate for the authority stage | none until a separate cutover decision |
| ⏸ later | Sachima `ArsdBackend` | separate integration evidence | separately approved |

Stage 1 was an intermediate implementation boundary, not a downgrade of the production target. Production was
achieved after Stage 2 acceptance, which is closed on `main`.

🟨 **The boundary reset is not a fifth stage of the original ladder and not a rollout.** Its documentation
gate changes authority only. Its source gates each need a distinct source-implementation approval recorded
after the documentation gate merges, and deleting the retired per-agent profiles from source needs one more
confirmation on top of that.

**Deploy sequence, for reference only and authorized by nothing here:** package upgrade → the operator
authors the registry file → offline validation → per-agent diagnostics → the mandatory denied-action canary
per agent → re-render the service unit → restart `arsd` → registry parse → reconcile-only → accept new
submits. Restarts recur only when the registry itself changes, never for an agent upgrade behind an
unchanged registered command.

## 10. Legacy coexistence and rollback

The legacy v0.1.7 acpx paths are ✅ still present in source and are **not** compatibility surfaces: not a
product, runtime, fallback, or Session store, and nothing on the reset line owes them compatibility. They do
not define vNext modules, Session semantics, status vocabulary, or production ingress; Native failure never
routes to them; and their storage stays isolated from the Native roots (§8). Removing that code, and the
documentation that describes it, is separately authorized work this architecture does not perform. Until
then the only acpx material it retains is a bounded differential/comparison-test reference.

Rollback disables Native/`arsd` ingress and stops new submissions. It never converts failures into acpx
fallback and never rewrites terminal Run facts.

🟨 **Reset-line migration and rollback.** The supervisor root is shared, so reconciliation and evidence
history stay continuous. Old Run directories are immutable historical files: the new runtime never rewrites,
migrates, deletes, or re-hashes them, and readers project them value-blind. Old Sessions stay owner-scoped
`status`/`list`/`close`-readable through a value-blind categorical projection, while those carrying retired
ARS-derived identity hashes are **refused for `session/load`** with a stable code — the new runtime cannot
honor identities it no longer models and must not pretend it can, and there is no silent `session/new`. The
`/opt` artifact trees and Binding roots simply stop being referenced; they are **not deleted**, and their
removal is a separate, later operator decision.

A *source* rollback is fail-closed in both directions with no new mechanism, no dual-write, no dual-read, no
shim, and no alias. Sessions created under the reset line are automatically refused by a reverted runtime,
because identity comparison is symmetric equality and a reset-line record carries neither the retired
contract hash nor the retired epoch. Terminal Run facts and sealed launch records are immutable across a
revert in either direction.

The one-time legacy-Session load refusal is a deliberate continuity loss and an open human decision, not a
technical detail: every live Session at cutover ends, and continuing that work means a new Session with
caller-owned context handoff.

## 11. Authority map

- Product intent: `GOAL.md`
- Requirements: `docs/product/prd.md`
- Module design: `docs/design/technical-solution.md`
- Operator registry contract: `docs/design/agent-registry.md`
- Emitted JSON shapes: `docs/design/result-event-schema.md`
- Current status/gates: `docs/roadmap/`
- Executable work: `docs/plans/active/`
- Historical-only material: all archive directories, including
  `docs/archive/binding-era-2026-07/` for the retired artifact/Binding architecture

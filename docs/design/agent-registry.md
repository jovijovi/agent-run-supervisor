---
title: "ARS agent registry — the operator contract"
status: active
created_at: 2026-07-30
last_validated_at: 2026-08-21
---
# ARS agent registry — the operator contract

This is the one document an operator needs in order to tell ARS which commands are which AGENTs. It is the
normative contract for the registry file, its grammar and bounds, its refusal rules, the environment it
projects into a child, and the restart semantics that follow from reading it exactly once.

**Status.** This contract is **merged on `main`** as part of the V4 boundary reset. Published
package/release facts come from live GitHub Releases and PyPI; deployed/running facts come from
operator-held runtime/live checks. [`docs/roadmap/current-status.md`](../roadmap/current-status.md) carries
only lean task state, the active plan, and open gates. Nothing here authorizes writing a registry file
against a live deployment, restarting a service, or cutting any caller over; each of those remains a
separate operator decision, whatever is already released.

Every example below uses **placeholders**. A placeholder is never a supported version, a registered value
domain, or an acceptance target.

## 1. Who owns what

| Layer | Owner | Carries |
|---|---|---|
| ACP compatibility profile | ARS source, under review | how to speak ACP to a class of agent: protocol major, required and forbidden capabilities, session semantics, selector-id conventions, the base environment allowlist, mediation semantics |
| **Agent registry entry** | **you, the operator** | which command is that agent here, its argv, its environment declarations, its selector-id hints, its capability narrowing, and an optional continuity epoch |
| Sealed per-Run Spec + launch snapshot | one Run | the projection of profile × entry × request, taken once before spawn |
| Observed evidence | one Run | what was resolved and observed — recorded, never a gate |

A profile contains no path, version, digest, model literal, agent name, value domain, or deployment fact. An
entry contains no capability requirement, protocol version, mediation pair, digest, or transport. Callers
supply none of it: `command`, `args`, environment keys and values, paths, and secret values are **not fields
on the wire**.

> **ARS performs no ownership, mode, ancestor, symlink, or digest check on `command`, on its ancestors, or on
> anything the AGENT subsequently loads.**

## 2. The contract

> **One operator-owned TOML file, supplied by a required `--agents-file` daemon flag, read exactly once at
> daemon startup into an immutable in-memory snapshot. Replace it atomically (`mv`); a replacement takes
> effect at the next daemon start.**

TOML, JSON, and a directory of per-agent files are not alternatives: one strict-parsed file is reviewable,
diffable, and atomically replaceable, and the TOML parser is in the Python standard library, so ARS keeps its
zero-runtime-dependency property.

**Startup order is strictly sequential and fail-closed at every step:**

1. Resolve and parse `--agents-file` into an immutable snapshot. Any defect refuses to listen, **before any
   state write**.
2. Reconcile durable Run and Session facts. Any fail-closed rule refuses to listen.
3. Bind the Unix socket (`0600` inside a `0700` directory) and accept.

After that the registry is **never opened again for the daemon's whole lifetime**. The Run, spawn,
finalization, and reconciliation paths perform zero registry filesystem access, two concurrent Runs can never
resolve different registry contents, and a serving daemon cannot be re-pointed.

**Config hygiene — explicitly not attestation.** ARS resolves the registry path, follows symlinks, and
requires the resolved target to be a **regular file that is not group- or world-writable**. A dotfiles symlink
works, including below `$HOME`; a file anyone can edit does not. This is ARS declining to take orders from a
world-writable file — the same standard as an SSH config — and it is bounded to *its own configuration file*.
It says nothing about `command`.

## 3. File shape

```toml
schema_version = 1

# A standards-conforming native ACP agent — the common case.
[agents.native-agent]
profile   = "standard-native-acp-v1"
command   = "some-agent"          # PATH-resolved bare name, exactly as typed
args      = ["acp"]
mediation = "ask-privileged-tool-families-v1"   # selects a source-owned binding

# A standards-conforming agent reached through an independently installed ACP
# adapter command. The executable is a deployment fact; an evidenced ACP
# semantic deviation selects its source-owned compatibility profile.
[agents.adapter-backed-agent]
profile = "standard-native-acp-v1"
command = "/home/<service-user>/.local/bin/<some-acp-adapter>"
args    = []
env_passthrough = ["SSH_AUTH_SOCK", "SOME_AGENT_CONFIG", "SOME_PROVIDER_TOKEN"]
env_overlay     = { SOME_AGENT_HOME = "/home/<service-user>/.some-agent", NO_BROWSER = "1" }
effort_selector = "reasoning_effort"    # selector *id*, not a value domain
forbidden_capabilities = ["terminal"]
session_epoch   = 1                     # operator-controlled continuity cut

# An agent whose ACP behavior itself deviates, with cited evidence in source.
[agents.compat-backed-agent]
profile = "claude-agent-acp-compat-v1"
command = "<some-adapter-command>"
args    = []
```

The repository also carries [`examples/agents.omp-reasonix.toml`](../../examples/agents.omp-reasonix.toml),
an operator example with the explicitly approved installed OMP and Reasonix command paths. It is example
configuration only: ARS never copies those binaries or writes the live registry for an operator.

`agent_id` is the table key (`native-agent` above). It is what a caller names in `AgentRunRequest`, and it is
the only registry-facing value that ever crosses the wire.

The planned Socket API v3 `agent_list` operation returns the stable-sorted set of these table keys from the
immutable snapshot already loaded by the serving daemon. It returns no entry field or health/readiness
claim, never reopens this file, and does not make a replaced file effective before the next daemon restart.
See the board-linked [active plan](../plans/active/2026-08-21-live-agent-roster-query.md).

## 4. Complete field set

Required: `profile`, `command`. Optional: `args`, `mediation`, `env_passthrough`, `env_overlay`,
`model_selector`, `effort_selector`, `forbidden_capabilities`, `session_epoch`. **Nothing else.** An unknown
key at any level is refused.

### Deliberately absent, each for a stated reason

| Absent field | Reason |
|---|---|
| `transport` | v1 is stdio by definition. A one-valued key is remote-transport scaffolding; remote transport and attach are future scope with their own design and approval |
| any secret slot or `secret_refs` | ARS resolves no credentials (§10). Adding a slot would be a schema placeholder for a capability that is not designed |
| `version_probe` | a probe is an operator diagnostic, not a per-Run gate |
| registered model or effort sets, selector value domains | live discovery is the domain authority and exact readback is the proof (§7) |
| `default_model`, `default_effort` | the caller supplies model and effort on every Run |
| expected `agentInfo.name` / `.version` | a self-report is evidence, never identity (§8) |
| any digest, artifact path, tree hash, ownership, or mode expectation | the entire retired artifact layer |

**There is no secret-shaped-name heuristic.** Refusing keys that look like `*TOKEN*` or `*SECRET*` is unsound
in both directions — `AUTH="…"` evades it while `SOME_AGENT_TOKEN_PATH=/path/to/dir` trips it falsely — and a
name-shape test can never be a confidentiality boundary. The replacement is the universal rule of §6: every
environment value is treated as potentially sensitive, and ARS never serializes one out of the resolved
carrier into structured launch/Spec/environment material, a hash input, or a configuration-inspection
response. What an AGENT echoes back into free-form Run text is not scanned against those values and may be
retained — §6 states that half exactly.

## 5. Grammar and bounds

Strict parse, all fail-closed, each refusal naming a stable rule.

| Field | Rule |
|---|---|
| file | ≤ 1 MiB; strict TOML; `schema_version` exactly the supported value |
| `agent_id` (table key) | matches `[a-z0-9][a-z0-9._-]{0,63}` |
| `profile` | names a source-registered profile |
| `command` | non-empty, ≤ 4096 bytes, no NUL; either absolute, or a single basename with **no path separator** |
| `args` | ≤ 32 tokens, each ≤ 1024 bytes, no NUL; **passed as an argv list, never through a shell**, so an empty token `""` is a valid token and is passed through unchanged |
| `mediation` | names a source-registered mediation id |
| `env_passthrough` | ≤ 32 names, each matching `[A-Za-z_][A-Za-z0-9_]*` |
| `env_overlay` | ≤ 32 pairs; values ≤ 4096 printable bytes |
| `model_selector`, `effort_selector` | a selector **id** hint; never a value domain |
| `forbidden_capabilities` | ≤ 16 bounded names; applied as a superset of the profile's own floor |
| `session_epoch` | a positive integer |

A reserved-mediation-key collision (§6.4) is refused **at parse time**, so it refuses the daemon rather than
a Run.

### Command semantics are preserved exactly

- `argv[0]` is the **declared `command` string, byte-for-byte**. A bare name stays a bare name, exactly as a
  shell would pass it.
- Every declared token reaches `exec` unchanged, including an **empty** one. `args = ["--label", "", "--end"]`
  is three tokens, not two: nothing coalesces, drops, or rewrites a token, because doing so would hand the
  child a different command than the one declared. `command` itself is separate and stays non-empty — it is
  the image to locate, not a token to pass.
- The exec image is located by ordinary `execvp`-style lookup over the **child's** projected `PATH` for a bare
  name, and by the declared absolute path otherwise. There is no `executable=` override, no descriptor-based
  image, and no realpath.
- Therefore version-manager and package-manager shims work, symlink farms work, package-relative resolution
  from the real script location works, multicall `argv[0]` dispatch works, and an agent's own self-update and
  self-relaunch logic works.
- **There is no pre-flight resolution check.** ARS classifies the exec failure itself: `ENOENT →
  COMMAND_NOT_FOUND`, `EACCES → COMMAND_NOT_EXECUTABLE`, anything else `→ SPAWN_FAILED`. Read those as
  ordinary configuration errors — "you upgraded and the shim moved" — never as a security refusal. No process
  exists in those cases.

## 6. Refusal rules

### 6.1 Registry-level — the daemon refuses to listen

| Class | Codes |
|---|---|
| unavailable / unreadable / unsafe mode | `REGISTRY_ABSENT`, `REGISTRY_UNREADABLE`, `REGISTRY_UNSAFE_MODE`, `REGISTRY_NOT_REGULAR_FILE` |
| malformed | `REGISTRY_PARSE`, `REGISTRY_UNKNOWN_KEY`, `REGISTRY_SCHEMA_VERSION`, `REGISTRY_TOO_LARGE` |
| entry defects | `AGENT_ID_INVALID`, `ENTRY_FIELD_MISSING`, `ENTRY_UNKNOWN_PROFILE`, `ENTRY_COMMAND_INVALID`, `ENTRY_ARG_TOKEN_INVALID`, `ENTRY_ENV_KEY_INVALID`, `ENTRY_ENV_VALUE_INVALID`, `ENTRY_SELECTOR_INVALID`, `ENTRY_CAPABILITY_INVALID`, `ENTRY_UNKNOWN_MEDIATION_ID`, `ENTRY_SESSION_EPOCH_INVALID` |
| mediation authority | `MEDIATION_KEY_COLLISION` |
| launch-permission authority | `LAUNCH_PERMISSION_KEY_COLLISION` |

`AGENT_ID_INVALID` is deliberately the same rule at both layers: the table key is judged by exactly the
grammar that judges a caller's `agent_id` at admission (§6.2), so the two can never drift. An overlay
*value* that breaks its bounds is `ENTRY_ENV_VALUE_INVALID` rather than `ENTRY_ENV_KEY_INVALID` — the
distinction matters because the refusal names the environment **name** and never the value.

The **whole file** is refused. It is never partially honored, never cached from a previous start, and never
repaired. Refusals name the failing rule and, where operator-facing, a field path or an environment **name** —
never an overlay value or a raw file fragment.

### 6.2 Per-Run — pre-dispatch `failed`

`AGENT_ID_INVALID` and `AGENT_NOT_REGISTERED` are resolved against the startup snapshot, in memory, with zero
filesystem access. Spawn classifications are `COMMAND_NOT_FOUND`, `COMMAND_NOT_EXECUTABLE`, and
`SPAWN_FAILED`. Contract checks are `PROTOCOL_MISMATCH`, `CAPABILITY_MISSING`, and `CAPABILITY_FORBIDDEN`.

### 6.3 Reconciliation — the daemon refuses to listen

Startup reconciliation runs after the registry parse and before the socket is bound. Corrupt terminal
records, unattributable uncertainty, corrupt Spec or launch material, a launch without its Spec, and a
corrupt submission on an otherwise empty Run tree each refuse to listen, after any outcome-mandated quarantine
side effect. Messages are stable and contain no raw artifact text. This is stricter than the pre-reset
behavior, which tolerated some of these states; that is deliberate.

### 6.4 Mediation authority is closed

Mediation environment values route an agent's privileged in-process tool families through ACP permission
requests, so the permission bridge decides *before* a side effect. If configuration could disable that, the
default-deny claim would be decorative. Therefore:

- **the binding is source-owned in key and value**, keyed by the capability family it mediates. Your entry may
  **select** one id, or none. It can never author a pair, a key, or a value, and there is no `mediation =
  off`;
- **reserved keys are global** — the union of every key in *any* registered binding, not only the one you
  selected — so the rule does not depend on which binding you chose or whether you chose one;
- **a collision refuses startup.** If any entry's `env_overlay` contains a reserved key, or its
  `env_passthrough` names one, the parse fails with `MEDIATION_KEY_COLLISION` and the daemon refuses to
  listen. `agents validate` applies the identical check offline, so you see it at authoring time;
- **layer 5 is the launch-permission pair, and declaring its key refuses the file.** If the profile your
  entry names selects a launch-permission policy, and your `env_passthrough` or `env_overlay` declares that
  policy's key, the parse fails with `LAUNCH_PERMISSION_KEY_COLLISION` and the daemon refuses to listen —
  `agents validate` applies the identical check offline. Layer 5 would otherwise overwrite your declaration
  silently and leave a projection that looks consistent while hiding the conflict. Unlike the mediation
  rule, this reservation follows the **selection**: a profile that selects no policy projects no layer 5, so
  the key is not reserved for it here;
- **layer 5 is the launch-permission pair.** Some agents complete a side effect without ever asking over
  ACP, so where cited evidence shows that, the profile selects a policy and ARS writes a private per-Run
  configuration before the process starts and points the agent at it. You do not select it and cannot author
  or disable it: it is a profile fact, its key and value are source-owned, and it is applied after layer 4.
  You will see the **name** in a Run's launch projection with source class `launch_permission`; the value is
  an ephemeral local path and is withheld like every other value.
- **mediation is applied last among the layers you can influence**, as defense in depth: a defect in the collision check cannot silently
  disable mediation.

**Honest limit.** Mediation is cooperative. An agent that ignores the knob, or one with no registered
binding, can execute in-process tools with no ACP permission event, and the bridge will never see them. The
mandatory denied-action canary proves the knob works *for one specific agent*, and it must precede that
agent's use.

## 7. Environment projection

A filtered environment is not the interactive environment. It silently omits proxy, certificate,
agent-socket, temp-directory, and provider variables, and the resulting failures look like agent bugs. Real
commands need those values, so you get an explicit way to supply them.

| Layer | Source | You control |
|---|---|---|
| 1 — base allowlist | names taken from the daemon's own environment, only when present, values unchanged | no (source-owned, per profile) |
| 2 — pass-through | additional names read from the daemon's environment | yes, `env_passthrough` |
| 3 — overlay | literal values you author | yes, `env_overlay` |
| 4 — mediation | source-owned pairs | selection only, `mediation` |
| 5 — launch permission | one source-owned pair, applied **last**, present only when the entry's profile selects a launch-permission policy | no (source-owned, per profile) |

The layer-1 base set covers the ordinary interactive essentials: `HOME`, `PATH`, `USER`, `LOGNAME`, `SHELL`,
`LANG`, `LC_ALL`, `TZ`, `TERM`, `TMPDIR`, the `XDG_*` directories, the lower- and upper-case proxy variables,
and the common certificate-bundle variables.

- **`HOME` unchanged** is what makes the AGENT's own credential store, plugin tree, cache, session store, and
  user config work exactly as they do when you run the agent by hand. Necessary, and not sufficient.
- **`PATH` is the single most likely cause of "works in my shell, fails under ARS."** A user-level daemon
  typically inherits a minimal `PATH` that omits `~/.local/bin` and version-manager shim directories. The
  remedies are an `env_overlay.PATH` you own, or an absolute `command`. `agents doctor` reports the exact
  projected **name** set so the gap is visible rather than mysterious.
- **`SSH_AUTH_SOCK` is deliberately not in the base set.** Forwarding it hands the AGENT live use of your SSH
  keys. That is a real authority transfer and must be an explicit per-agent `env_passthrough` opt-in.

**Resolution happens exactly once**, in memory, before the Run is sealed and before the child is spawned. The
sealed launch snapshot therefore describes exactly which names and precedence were handed to exec, with no
window in which the daemon's own environment could change in between.

### What ARS records, and what it never records

Durable environment evidence is **value-blind**: per name, the name, its source class, its precedence layer,
and its redaction status, plus a resolved count, the mediation id, and the names you declared that were
absent from the daemon's environment. Nothing else.

```json
"env": {
  "values_persisted": false,
  "redaction": "all-values-withheld",
  "resolved_count": 27,
  "mediation_id": "ask-privileged-tool-families-v1",
  "names": [
    {"name": "PATH",                "source": "base",        "precedence": 1, "redacted": true},
    {"name": "SSH_AUTH_SOCK",       "source": "passthrough", "precedence": 2, "redacted": true},
    {"name": "SOME_PROVIDER_TOKEN", "source": "passthrough", "precedence": 2, "redacted": true},
    {"name": "SOME_AGENT_HOME",     "source": "overlay",     "precedence": 3, "redacted": true},
    {"name": "SOME_MEDIATION_KEY",  "source": "mediation",   "precedence": 4, "redacted": true}
  ],
  "declared_absent": ["SOME_AGENT_CONFIG"]
}
```

**Mediation values are withheld too**: the mediation id is durable, and no Run record repeats its
source-owned pairs. Launch, Spec, request, and event hashes cover only value-blind material — no value, value
digest, keyed digest, length, prefix, suffix, equality token, or matcher table is hash input. Two Runs whose
transmitted value changed may therefore share a launch hash; the hash proves the declared projection, not the
secret.

**ARS does not scan free-form Run text for the values it projected (see also §12 item 6).** There is no
per-Run exact-literal guard. Static shape redaction — API key, `Authorization: Bearer`, JWT, PEM — and the
sensitive-env-**key** rule still apply, but an arbitrary value that matches none of those and that the AGENT
echoes back into a final message, an event field, a tool-call id, `agentInfo`, usage metadata, stderr, or the
external Session id it mints **may be retained** in that Run's evidence.

**The workspace root and the effective `cwd` remain complete literals (see also §12 item 7)** in the sealed
Spec and remain hash-covered, even when the workspace lives under `$HOME` and therefore contains the whole
`HOME` value as a substring. They are independently derived authority facts, and truncating or tokenising
them would break workspace binding, reconciliation attribution, and audit.

## 8. Observations are evidence, never gates

After a successful spawn ARS records the declared command and exact argv, the first `PATH` hit for a bare
command when one can be computed, the image the kernel actually mapped, the agent's self-reported name and
version, the protocol version, the advertised capabilities, and any probe result you ran. **Every one is
marked non-authoritative.**

No code path compares any of them against a source constant, a prior Run, a Session record, or a registry
value to decide admission or reuse. Divergence, or drift between two Runs of one Session, is recorded and may
be emitted as a policy-warning event — never a refusal. A self-report is not an identity in either direction:
a substituted agent can report any name it likes, and an operator-declared expected name would refuse Runs
for cosmetic vendor renames.

Recording drift across Runs means one observation outlives its Run, in the Session record. The self-report is
child-chosen text that no contract check inspects, and it is stored as the agent reported it — so an agent
that echoes a projected environment value through `agentInfo` will put that value into `session.json`, where
it outlives the Run. If that matters for a particular agent, the remedy is not projecting the value to it.

**The complete set of observation-based refusals** is: protocol major mismatch, a required capability absent,
a forbidden capability present, an inexact or coerced configuration readback, and — on a compatibility profile
— a required permission mode not proven by readback. Those are checks against a declared contract inside one
Run, not continuity comparisons.

Model and effort domains are **live**: whatever the running agent advertises right now is the authority, and
exact literal readback is the proof. A value the agent does not advertise yields zero Turn and no prompt. This
is why "the agent added a model today" is a non-event for ARS, and why `model_selector` and `effort_selector`
carry an id hint only.

**`effort_selector` is refused on a `model-only` profile.** Such a profile declares that the agent advertises
no independent effort selector, so ARS discovers none, sets none, and reports `N/A` as the effective effort.
An id hint for a selector no Run ever sets would be a fiction in every launch snapshot, so the pairing is
refused rather than ignored. A caller targeting such an agent must request effort `N/A`; any other value
fails before the prompt.

### Pinning an exact model through Claude Code / `claude-agent-acp`

An operator who wants a *specific* Claude model — not "whatever is current" — has to know one thing about
Claude Code's naming first: **`opus[1m]` is a rolling alias, not a fixed model id.** The official
documentation defines an alias as the provider's currently recommended model — here Opus in the 1M-context
lane — and states that aliases update over time, so the concrete generation behind one changes. It is
therefore **not** a permanent synonym for `claude-opus-5[1m]`. Any mapping you observe today is a snapshot,
never a definition; that aliases are documented as changing is the only durable fact about them.

**A caller that needs a fixed model requests the full concrete model id** — `claude-opus-5[1m]`, for example —
exactly as it expects to read it back. That is necessary but not sufficient: by the live-domain rule above,
the value must also be one the running agent actually advertises, and the adapter advertises what the
effective Claude Code settings allow. The operator makes the exact id available through `availableModels`:

```json
{
  "availableModels": ["claude-opus-5[1m]"]
}
```

`availableModels` is **Claude Code / adapter-owned configuration, not ARS configuration.** It is not a
registry field (§4), not a registered model domain (§8), and it never appears in the operator's agents file.
The operator places it in whichever Claude settings source is effective for the adapter's own `cwd` and
config root — a deployment choice this document neither makes nor records.

**Adapter behavior, bounded and version-sensitive.** `@agentclientprotocol/claude-agent-acp` resolves the
user, project, local, and managed settings sources, uses the **exact** `availableModels` entries as the model
option ids it surfaces over ACP, and passes that same canonical advertised value to `setModel`. That is an
observed property of one adapter version, not a contract ARS owns. Revalidate it after any adapter or Claude
Code upgrade: a change to the settings merge or to how ids are surfaced changes what ARS sees advertised.

**ARS does not paper over the difference.** The caller supplies model and effort on every Run, live discovery
is the domain authority, and the effective configuration is proved by exact literal readback before the
prompt. So an exact request is never quietly satisfied by a rolling alias: if the adapter advertises or reads
back an id that is not byte-for-byte the requested one — `opus[1m]` where `claude-opus-5[1m]` was asked for —
the Run fails pre-dispatch with `CONFIG_FIDELITY`, before any prompt, rather than proceeding as though the
pin had been proven.

**Acceptance, minimally:** the exact id appears in the discovered option set; set and readback are
byte-for-byte identical to the requested id; a real prompt on that Run succeeds; and a new process loads the
same Session and continues at the same effective model and effort.

**Sources.** Claude Code model aliases —
<https://code.claude.com/docs/en/model-config#model-aliases>; restricting model selection —
<https://code.claude.com/docs/en/model-config#restrict-model-selection>; the adapter —
<https://github.com/agentclientprotocol/claude-agent-acp>. The first two are rolling documentation: read them
as the current statement, not a fixed one.

## 9. The honest cost of read-once, and restart semantics

A registry edit — a new `command`, changed `args`, a new overlay pair, an epoch bump, a new agent — takes
effect at the **next daemon start**, not the next Run.

| Change | Cost |
|---|---|
| **Agent upgrade behind an unchanged registered command** — same PATH name, repointed shim, reinstalled symlink target, new version at the same absolute path | **nothing.** No restart, no ARS action, no re-acceptance. An existing Session still reuses through a real `session/load`. This is the case the reset exists to fix, and it is the common one |
| **Identity-preserving registry edit** — `command`, `args`, `env_passthrough`, `env_overlay`, `mediation`, selector hints, `forbidden_capabilities` | one daemon restart, which means draining in-flight Runs first. The restart is a service action, **not a promotion**: no measurement, no manifest, no acceptance receipt, no re-canary, and **no Session invalidation**, because no Session identity field derives from registry bytes, mtimes, digests, command paths, or observed runtime facts |
| **Identity-changing registry edit** — adding or changing `session_epoch`, targeting a different `agent_id`, selecting a different `profile` | the same one restart, **plus the continuity cut you asked for**: that is a different Session identity, so existing Sessions are refused for reuse by the symmetric equality of §13 |
| **ARS release that does not change ACP semantics** | no Session invalidation, by construction: no identity field derives from an ARS version |

What read-once buys: zero operator-config filesystem access on the Run path, one fail-closed parse per daemon
lifetime, no possibility of two concurrent Runs resolving different registry contents, and a sealed Run that
cannot be re-pointed by construction rather than by discipline.

## 10. Credentials

**ARS v1 does not discover, resolve, mint, refresh, store, or manage any credential.** The default and only
posture is AGENT-owned auth stores: the AGENT authenticates through its own store under its own `HOME`,
exactly as it does interactively. There is no ARS-managed home and no staged credential file.

`credential_refs` on a request are caller-supplied **references** recorded as admission evidence and grant
material. They are never resolved to values and never reach the child environment.

**ARS does not claim that no sensitive value reaches the child.** If you pass through a provider token, a
proxy URL with embedded credentials, or `SSH_AUTH_SOCK`, or you author an overlay literal, that value is
transmitted to the child process in memory — by your own declaration — and is recorded by ARS only as a name
and a source class.

## 11. Operator surface

```bash
agent-run-supervisor agents validate --agents-file <path>
agent-run-supervisor agents doctor   --agents-file <path> [--agent <agent-id>] [--no-probe]
agent-run-supervisor run inspect     --run-dir <native-run-dir>
```

| Command | What it does | Side effects |
|---|---|---|
| `agents validate` | parses the file, checks shape and bounds, and applies the **identical** mediation-collision check the daemon applies at startup | none. It prints only entry ids, counts, environment **names**, source classes, and rule outcomes — never a normalized overlay or mediation value |
| `agents doctor` | the projected environment **name** report and the declared launch per named agent, plus a zero-prompt ACP `initialize`. `--no-probe` reports the projection only and starts nothing | without `--no-probe` it **starts an external child**, which writes its own AGENT-owned state. "Read-only" refers to ARS and operator state, and never claims otherwise about the child. The child is reaped on every path: close, `SIGTERM` to the group, a bounded wait, then `SIGKILL` and a final bounded wait. A group that survives all of that is reported as a failed probe rather than left behind quietly |
| `run inspect` | per-Run evidence. For a reset-schema record it recomputes the value-blind launch hash and reports guarded, allowlisted evidence | none. For a pre-reset record it classifies the schema first and withholds **every** field the record itself supplied — environment fields, raw documents, seal material, free-form text, and the profile identity, which looks structural but is untrusted bytes in a document ARS did not write. It never recomputes a hash over value-bearing material |

There is **no** `promote`, `rollback`, or `--force`, no command that installs an artifact, edits a service
unit, restarts the daemon, escalates privilege, or contacts a provider.

The daemon requires `--agents-file` both in daemon mode and when rendering a service unit, so a rendered unit
can never silently omit it.

**Reference deploy sequence, authorized by nothing in this document:** package upgrade → author the registry
file → `agents validate` → `agents doctor` per agent → the **mandatory denied-action canary per agent** →
re-render the service unit → restart `arsd` → registry parse → reconcile-only → accept new submits. Restarts
recur only when the registry itself changes.

## 12. Operator-visible changes at cutover

Seven, and each needs an explicit note in your own runbook:

1. **The AGENT project-config workspace refusal disappears.** ARS no longer refuses a workspace for
   containing an AGENT's own project configuration file. That file is AGENT-owned, and refusing it asserted
   authority over a surface ARS does not own.
2. **You must author `env_passthrough` / `env_overlay`** for anything the layer-1 base set does not cover.
   `PATH` is the single most likely cause of "works in my shell, fails under ARS", and `SSH_AUTH_SOCK` is
   deliberately opt-in.
3. **New launch records carry names, source classes, and precedence only.** Legacy value-bearing and
   free-form inspection is withheld behind categorical markers.
4. **A registry edit takes effect at the next daemon start, not the next Run** — while an agent upgrade behind
   an unchanged registered command costs nothing at all.
5. **Adding `session_epoch` to an entry for the first time cuts that agent's existing Sessions**, because
   absent ≠ 1. See §13.
6. **An AGENT that echoes a projected value into Run text may have ARS retain it.** ARS keeps values out of
   its own sealed material and never renders the resolved mapping, but it does not scan free-form Run text
   for the literals it projected. Static credential shapes are still redacted; an arbitrary value is not.
   This is the tradeoff most likely to surprise you, and the remedy is deciding what you project to which
   agent — not a per-value exemption.
7. **The canonical workspace root and the effective `cwd` remain complete literals and remain hash-covered.**
   They are independently derived authority facts — so a workspace under `$HOME` will show that path in full
   in `spec.json`.

Additionally, at a cutover: every **live Session ends**. Legacy Sessions carrying the retired ARS-derived
identity hashes are refused for `session/load` with a stable code while staying owner-scoped
`status`/`list`-readable, and continuing that work means a new Session with caller-owned context
handoff. That is a deliberate one-time continuity loss and an open human decision, not a technical detail.

## 13. `session_epoch` — the continuity rule, exactly

`session_epoch` is an operator escape hatch for the case where **you know** a change broke continuity and want
every Session under that agent to stop reusing. Therefore:

- **No automatic bump exists anywhere.** An AGENT or adapter version change, an ARS package upgrade, a profile
  revision that does not change ACP semantics, a `command`/`args`/`env`/`mediation`/selector edit, a registry
  file replacement, and a daemon restart **never** change it. No code path derives, increments, or infers an
  epoch from an observation, a digest, a version, or a file's bytes.
- **Only your edit changes it**, and its effect is exactly the continuity cut you asked for. Identity
  comparison is **symmetric equality**, so a record at epoch 1 is refused by a Run at epoch 2 *and* by a Run
  with no epoch.
- **Adding `session_epoch` to an entry for the first time cuts continuity for that agent's existing Sessions,
  because absent ≠ 1.** That is the same deliberate act as a bump. If you do not want the cut, do not add the
  field.

What must **not** invalidate reuse, and does not: an agent CLI or adapter version change; a self-reported name
or version change; the observed executable, mapped image, path-lookup hit, or probe result; capability drift
between Runs of one Session; a `command` path change from a repointed shim or a reinstall; an `args`, overlay,
pass-through, mediation, or selector-hint edit; the registry file's bytes, digest, mtime, or location; and any
digest, tree hash, ownership, or mode change — those concepts no longer exist.

**The trade-off, stated honestly.** You can edit `args` and restart, and the next Run reuses an existing
Session. If that edit changed agent behavior materially, the change is *recorded* — full argv and the complete
value-blind environment material live in every Run's launch snapshot — but not *refused*, unless you also bump
`session_epoch`. The alternative, fingerprint-as-gate, is the failure mode the reset removes.

## 14. Honest limits

1. **Not a sandbox.** The AGENT runs as the daemon's UID with that UID's full authority over the filesystem,
   network, and process table. `allowed_roots` constrains what ARS *approves via ACP*, not what the process
   *can do*.
2. **No supply-chain or integrity claim.** ARS does not verify that the executable it launched is the one you
   intended, is unmodified, or came from a trusted publisher. Recorded resolution facts and probe output are
   evidence for humans, never gates.
3. **No hostile-code containment.** An agent that ignores ACP mediation, spawns its own children, writes
   outside the workspace, or exfiltrates data is not stopped by anything here.
4. **Unmediated in-process tools** are invisible to the permission bridge.
5. **Registry trust is transitive to you.** A wrong `command` launches the wrong thing. The defenses are that
   the registry is operator-authored rather than caller-supplied, read-only to ARS, refused when
   world-writable, parsed once, and fully recorded per Run.
6. **No guarantee about the AGENT's own config or credentials.** They are AGENT-owned; ARS does not read,
   validate, or vouch for them.
7. **No containment for values you project.** ARS's guarantee covers its own sinks. It cannot erase the value
   at its source, stop the child writing its own logs or state, stop the child transmitting it to a remote
   service, prevent OS crash dumps or privileged process inspection, or detect a transformed disclosure — a
   partial value, base64, encryption, hashing, character-by-character fragmentation, or a paraphrase.
8. **Termination reaches the process group ARS created.** ARS reliably terminates its direct child and every
   descendant still in that group. It does not control a descendant that calls `setsid()`/`setpgid()`, a
   payload handed to a service manager as a separate transient unit, a container runtime that relocates the
   payload to another namespace and cgroup, or an agent that double-forks. Crash containment through the
   user-level service manager cgroup is real, load-bearing, and **external** to ARS. If work continues
   elsewhere anyway, the Run fails loudly as `unknown`/`quarantined`/`retryable=false` rather than silently.

**Where real isolation belongs: outside ARS, at the OS layer** — a dedicated UID per agent, user namespaces,
`seccomp`/Landlock, `bwrap`/container/VM boundaries, cgroup resource limits, network namespaces. The registry
composes with these because `command` is opaque to ARS: register the isolation wrapper as the command. A
wrapper that `exec`s into the payload keeps it in ARS's process group and composes cleanly; a wrapper that
*relocates* the payload to another supervisor breaks ARS's termination and timeout guarantees. Both are
permitted, the difference is stated, and it is your knowing choice — ARS makes no isolation claim either way.

## 15. Authority and approval boundary

This document is design authority for the registry contract. Product requirements are
[`docs/product/prd.md`](../product/prd.md) R13–R15; system shape is
[`architecture.md`](architecture.md); module design is
[`technical-solution.md`](technical-solution.md); current status and gates are
[`docs/roadmap/current-status.md`](../roadmap/current-status.md); what is explicitly not approved is
[`docs/roadmap/non-approvals.md`](../roadmap/non-approvals.md).

It authorizes nothing: no source implementation, no registry authoring against a live deployment, no service
install, restart, or unit change, no migration or cutover, no release, and no production configuration write.
Each of those is a separate, narrow operator decision.

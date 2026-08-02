<!-- Hero -->
<p align="center">
  <img src="docs/assets/branding/readme-hero.png" alt="Agent Run Supervisor" width="860">
</p>

<!-- Language links -->
<p align="center">
  <b>English</b>
  &nbsp;·&nbsp;
  <a href="README.zh-CN.md">简体中文</a>
</p>

<!-- Badges -->
<p align="center">
  <a href="https://pypi.org/project/agent-run-supervisor/">
    <img src="https://img.shields.io/pypi/v/agent-run-supervisor.svg" alt="PyPI">
  </a>
  <a href="https://github.com/jovijovi/agent-run-supervisor/actions/workflows/verify.yml">
    <img src="https://github.com/jovijovi/agent-run-supervisor/actions/workflows/verify.yml/badge.svg" alt="CI">
  </a>
  <a href="https://codecov.io/gh/jovijovi/agent-run-supervisor">
    <img src="https://codecov.io/gh/jovijovi/agent-run-supervisor/graph/badge.svg" alt="codecov">
  </a>
  <a href="https://pypi.org/project/agent-run-supervisor/">
    <img src="https://img.shields.io/pypi/pyversions/agent-run-supervisor.svg" alt="Python">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT">
  </a>
</p>

<p align="center">
  A <b>local-first</b> supervisor for external coding AGENTs.<br>
  One unprivileged daemon, one process per run, <b>redacted and auditable</b> local evidence.
</p>

---

## Contents

[What it is](#what-it-is) ·
[Documented target vs. released code](#documented-target-vs-released-code) ·
[How it works](#how-it-works) ·
[Requirements](#requirements) ·
[Install](#install) ·
[Upgrade](#upgrade) ·
[Uninstall](#uninstall) ·
[Run `arsd`](#run-arsd) ·
[The agent registry](#the-agent-registry) ·
[Use from Python](#use-from-python) ·
[ACP protocol support](#acp-protocol-support) ·
[Supported agents](#supported-agents) ·
[Agent runtimes you install](#agent-runtimes-you-install) ·
[Operator-visible changes](#operator-visible-changes) ·
[Guarantees and boundaries](#guarantees-and-boundaries) ·
[Development](#development) ·
[Contributing](#contributing) ·
[License](#license)

## What it is

Anything that drives an external coding AGENT rebuilds the same plumbing: launching and babysitting
the agent process, deciding what it may touch, reading a stream of protocol events, classifying how
the run ended, and scrubbing secrets before anything reaches disk. Written ad-hoc, every caller grows
its own subtly unsafe copy.

**Agent Run Supervisor (ARS)** factors that into one independent local layer. Your application
submits a run — which registered agent, which model, which workspace, which prompt. ARS admits the
request, launches exactly one supervised agent process from the command an operator registered,
mediates every permission request under a default-deny policy, normalizes agent output into ordered
events, classifies a supervisor-owned status, and writes redacted artifacts with restrictive
permissions.

You get **auditable evidence** instead of process-lifecycle code — enough to answer *what did the
agent try to do, what was it allowed to do, and how did it actually end?*

**ARS supervises external AGENTs it does not own.** It does not install, package, copy, freeze, promote,
pin, host, or attest agents, their ACP adapters, their homes, credential stores, plugin trees, caches,
or configuration. You install and upgrade those with your own package manager. ARS owns the *process it
starts*; you own the *software it starts*.

ARS reports technical supervision facts only. The business verdict stays yours.

## Documented target vs. released code

> **Read this before following any command below.** This README documents the **agent-registry
> boundary** — one operator-owned registry file, `--agents-file`, and the `agents` / `run inspect`
> operator surface. That is the project's tracked architecture.
>
> **It is implemented in source on a task branch and has not been released.** The registry reader, the
> two-profile source registry, the value-blind launch snapshot, `api_version` 2, and the `agents` /
> `run inspect` commands all exist in code and are covered by tests. Nothing is deployed: no PR is
> merged, no release is cut, no service is installed or restarted, and no registry file is authored
> against a live deployment. Every one of those remains a separate, explicit decision.
>
> The **released `v0.5.x` line implements the earlier artifact/Binding architecture instead**: a
> different required daemon flag, an operator-owned Binding root with promoted generations, and frozen
> artifact identity. That architecture is **retired as a target** and is therefore no longer documented
> here. If you operate a `v0.5.x` deployment today, use that release's own notes in
> [`CHANGELOG.md`](CHANGELOG.md) and the cold archive at
> [`docs/archive/binding-era-2026-07/`](docs/archive/binding-era-2026-07/README.md); nothing in this
> README describes how to operate it, and installing this branch would not upgrade it in place —
> Sessions do not carry across the two lines, by design.
>
> The exact position and the decisions still open are on the board,
> [`docs/roadmap/current-status.md`](docs/roadmap/current-status.md). No documentation change here
> deploys, restarts, or migrates anything.

## How it works

<p align="center">
  <img src="docs/assets/diagrams/how-it-works.svg" alt="A trusted local caller submits over the arsd Unix-domain socket; arsd authenticates the peer and admits the request; ars-core runs one RunTask over Native ACP against a registered external AGENT; normalized events, status, and redacted local artifacts come back" width="900">
</p>

The whole path is local:

1. **Your application connects to `arsd`**, the small unprivileged supervisor daemon.
2. **`arsd` listens on a Unix-domain socket** — a `0600` socket inside a `0700` directory. No TCP, no
   root, no public ingress.
3. **At startup, `arsd` parses your agent registry exactly once**, reconciles durable facts, and only
   then binds the socket. Any registry defect refuses to listen before anything is written.
4. **The peer is authenticated, then the request is admitted.** `arsd` reads peer credentials from
   the socket and maps them to a principal, then admits the request against your caller-owned
   `request_id`, which doubles as the idempotency key. Runs and sessions are owner-scoped: only their
   owner can query, stream, cancel, or close them.
5. **`ars-core` executes the work.** One in-process `RunTask` owns one supervised agent process and
   one Native ACP connection, driven by an immutable run spec sealed at admission.
6. **The agent is the command you registered**, launched exactly as declared — no arbitrary command,
   argv, or environment passthrough from the wire, and no re-reading of the registry while serving.

Back over the same socket you get normalized, seq-ordered events and a supervisor-owned status, plus
redacted local artifacts on disk.

### Four authority layers

Nothing merges ACP semantics with deployment facts, and there is no fifth layer:

| Layer | Owner | Carries |
|---|---|---|
| ACP compatibility profile | ARS source, under review | how to speak ACP to a class of agent: protocol major, required and forbidden capabilities, session semantics, selector-id conventions, the base environment allowlist, permission-mediation semantics |
| **Agent registry entry** | **operator** | which command is that agent here, its argv, its environment declarations, selector-id hints, capability narrowing, an optional continuity epoch |
| Sealed per-run spec + launch snapshot | one run | the projection of profile × entry × request, taken once before spawn |
| Observed evidence | one run | what was resolved and observed — recorded, never a gate |

A caller chooses none of them. A profile carries no path, version, digest, model literal, or deployment
fact. An entry carries no capability requirement, protocol version, mediation pair, digest, or
transport. Admission resolves the agent from the startup snapshot **in memory, with zero filesystem
access**, seals the result, and never re-reads it: spawn, finalization, and reconciliation have no
registry read path at all.

### Two protocols, two different versions

ARS sits between two independently versioned protocols, and neither implies the other:

- **ACP Protocol v1** — the *downstream* Agent Client Protocol, spoken over stdio JSON-RPC between
  ARS and the agent process.
- **`arsd` API** — the *upstream* ARS-owned wire between your application and `arsd`. Every frame
  carries `api_version`; an unknown version is rejected rather than guessed.

The `arsd` API moves to **2** with the registry boundary, because the primary selector's meaning
changes: `profile_id` stops selecting a launch and `agent_id` starts, and silently reinterpreting an
old frame is exactly the quiet fallback this project forbids. During the drain window only `submit` is
refused at `api_version: 1`; the other seven operations are accepted, including `server_info`, which is
how an older caller discovers *that* it must upgrade. The separate shutdown drain is unchanged: once
shutdown begins, every frame is answered with `SHUTTING_DOWN`.

Design detail: [`docs/design/architecture.md`](docs/design/architecture.md).

## Requirements

| Need | Requirement |
|---|---|
| Runtime | **Python ≥ 3.11**. The package itself has **zero third-party runtime dependencies**. |
| Driving a real agent | The `native` extra, which pins the official ACP client library (`agent-client-protocol==0.11.1`). A base install imports fine and fails only when the SDK is actually used. |
| Running `arsd` | Linux with a POSIX user session for the AF_UNIX socket, plus a supervisor root, an agent registry file, and at least one caller mapping you supply. |
| Crash containment | A user-level service manager cgroup and a CPython build with pidfd support. |
| Running an agent | The agent installed locally by you, and one registry entry naming its command. See [Agent runtimes you install](#agent-runtimes-you-install). |

## Install

From PyPI:

```bash
# base install
pip install agent-run-supervisor

# recommended: with the ACP client library needed to drive a real agent
pip install 'agent-run-supervisor[native]'
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv pip install 'agent-run-supervisor[native]'
```

From a source checkout — the way to get the test suite, fixtures, and dev tooling:

```bash
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor

# runnable immediately: the runtime is standard library only
PYTHONPATH=src python3 -m agent_run_supervisor doctor

# or an editable install
pip install -e '.[dev,native]'
```

Nothing in ARS launches an agent implicitly. `doctor`, `replay`, `--print-service-unit`, and
`run inspect` are read-only with respect to ARS and operator state. `agents doctor` is the one
diagnostic that *does* start an external child — and that child writes its own agent-owned state, which
this document does not pretend otherwise about. It is reaped on every path: `SIGTERM` to the group, a
bounded wait, then `SIGKILL` and a final bounded wait, and a group that survives even that is reported
as a failed probe rather than left running.

## Upgrade

```bash
pip install --upgrade 'agent-run-supervisor[native]'
```

Check what is installed:

```bash
python3 -c "import agent_run_supervisor as a; print(a.__version__)"
```

A new package version never restarts a running daemon and never touches operator storage. Restarting
`arsd` after an upgrade is your decision.

**Upgrading across the boundary reset.** The retired artifact/Binding operator inputs — its required
daemon flag, its Binding root with promoted generations, and its `runtime-binding` command group — are
replaced by one registry file, `--agents-file`, and the `agents` commands. That change fails closed
rather than guessing: an older service unit does not silently keep working. It also ends every live
session once, because sessions created under the retired identity model are refused for reload with a
stable code while staying readable. Old artifact roots and Binding roots simply stop being referenced —
ARS never deletes them, and removing them is your separate decision.

ARS never migrates operator storage on your behalf. Full history: [`CHANGELOG.md`](CHANGELOG.md).

## Uninstall

```bash
pip uninstall agent-run-supervisor
```

Removing the package leaves your local state and operator storage untouched. Clean up in this order,
keeping only what you still need:

```bash
# 1. stop and remove the user service, under whatever name you installed it as
systemctl --user disable --now <your-unit>.service
rm -f ~/.config/systemd/user/<your-unit>.service
systemctl --user daemon-reload

# 2. review local artifacts before deleting anything — dry-run by default
agent-run-supervisor cleanup --help

# 3. remove the supervisor root (evidence, sessions, socket directory)
rm -rf <supervisor-root>        # user-service default: ~/.local/share/agent-run-supervisor

# 4. remove build artifacts and caches from a checkout
make clean
```

Your **agent registry file** and the **agents you installed** are operator-owned and outside ARS.
Remove them separately and deliberately.

## Run `arsd`

`arsd` is a module entry point, not a console script:

```bash
# options and boundaries (read-only)
python3 -m agent_run_supervisor.arsd --help

# render a user-scope systemd unit to stdout and exit.
# pure text: no privilege check, no reconciliation, no socket bind — nothing is
# installed, enabled, or started. --agents-file is required here too, so a
# rendered unit can never silently omit it; the path is argv data, not accessed.
python3 -m agent_run_supervisor.arsd \
  --agents-file <agents-file> \
  --print-service-unit

# start the daemon
python3 -m agent_run_supervisor.arsd \
  --supervisor-root <supervisor-root> \
  --agents-file <agents-file> \
  --caller-mapping <UID>:<principal_id>:<owner>:<namespace>
```

From a checkout without installing, prefix with `PYTHONPATH=src`.

Daemon mode requires `--supervisor-root`, `--agents-file`, and at least one `--caller-mapping` —
**zero mappings refuse to listen** — and refuses to start as root. `--socket` defaults to
`$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`, falling back to `<supervisor-root>/arsd/arsd.sock`.
`--max-concurrent-runs`, `--max-connections`, and `--log-level` bound the rest.

Caller mappings, socket paths, and the registry path are deployment values. Keep them in a mode-`0600`
unit file, never in a repository.

Startup order is strict: **parse the registry once → reconcile → bind**. On restart the daemon
reconciles durable facts only, and it is stricter than a tolerant reader: a corrupt terminal record,
unattributable uncertainty, or a launch record without its spec each refuse to listen rather than
guess. A run that may have been dispatched without a trustworthy terminal result ends `unknown` /
`quarantined` / `retryable=false`, and is never re-prompted.

## The agent registry

`--agents-file` points at **one operator-owned TOML file, read exactly once at daemon startup** into an
immutable in-memory snapshot. You replace it atomically; a replacement takes effect at the next daemon
start.

```toml
schema_version = 1

# A standards-conforming native ACP agent — the common case.
[agents.native-agent]
profile   = "standard-native-acp-v1"
command   = "some-agent"          # PATH-resolved bare name, exactly as typed
args      = ["acp"]
mediation = "ask-privileged-tool-families-v1"   # selects a source-owned binding

# An agent reached through an independently installed ACP adapter command.
# Same profile: the adapter is a deployment fact, not a source constant.
[agents.adapter-backed-agent]
profile = "standard-native-acp-v1"
command = "/home/<service-user>/.local/bin/<some-acp-adapter>"
env_passthrough = ["SSH_AUTH_SOCK", "SOME_PROVIDER_TOKEN"]
env_overlay     = { SOME_AGENT_HOME = "/home/<service-user>/.some-agent", NO_BROWSER = "1" }
forbidden_capabilities = ["terminal"]
```

Every value above is a **placeholder**. The complete closed field set is `profile`, `command`, `args`,
`mediation`, `env_passthrough`, `env_overlay`, `model_selector`, `effort_selector`,
`forbidden_capabilities`, and `session_epoch` — nothing else, and an unknown key at any level is
refused. `transport` is refused as an unknown key: v1 is stdio by definition.

**What ARS does and does not check.** It resolves the registry path, follows symlinks, and requires the
resolved target to be a regular file that is not group- or world-writable — ARS declining to take
orders from a file anyone can edit, bounded to *its own configuration file*. It performs **no
ownership, mode, ancestor, symlink, or digest check on `command`**, on its ancestors, or on anything the
agent later loads.

**Your command is launched exactly as declared.** `argv[0]` is the declared string byte-for-byte; a bare
name is located by ordinary PATH lookup over the child's projected `PATH`. So shims, symlink farms,
package-relative resolution, and an agent's own self-update all keep working. Every `args` token reaches
the child unchanged, including an empty one — `["--label", "", "--end"]` is three tokens, because argv
goes to `exec` and never through a shell. There is no pre-flight resolution check: a failed exec is
classified as `COMMAND_NOT_FOUND`, `COMMAND_NOT_EXECUTABLE`, or `SPAWN_FAILED`, and those read as
ordinary configuration errors, not security refusals.

**Read-once, and what it costs.** An **agent upgrade behind an unchanged registered command** — same
PATH name, repointed shim, reinstalled symlink target, new version at the same absolute path — costs
**nothing at all**: no restart, no re-acceptance, and an existing session still reuses through a real
`session/load`. A **registry edit** costs one daemon restart, which means draining in-flight runs
first. That restart is a service action, not a promotion: no measurement, no manifest, no receipt, no
re-canary, and no session invalidation, because no session identity field derives from registry bytes.

The operator surface is a separate CLI:

```bash
agent-run-supervisor agents validate --agents-file <path>
agent-run-supervisor agents doctor   --agents-file <path> [--agent <agent-id>] [--no-probe]
agent-run-supervisor run inspect     --run-dir <native-run-dir>
```

`agents validate` parses, bounds-checks, and applies the identical mediation-collision check the daemon
applies at startup — printing only entry ids, counts, environment **names**, source classes, and rule
outcomes, never a value. `agents doctor` runs a zero-prompt ACP `initialize` per agent and reports the
projected environment **name** set, which is how you find the `PATH` gap that causes "works in my
shell, fails under ARS". `run inspect` reports per-run evidence.

There is no `promote`, no `rollback`, and no `--force`. Nothing here installs software, edits a unit
file, escalates privilege, or restarts the daemon.

Full contract — grammar, bounds, every refusal code, environment layers and precedence,
`session_epoch`, and the honest limits: [`docs/design/agent-registry.md`](docs/design/agent-registry.md).

## Use from Python

[`ArsdClient`](src/agent_run_supervisor/arsd/client.py) is the supported caller boundary: explicitly
connected, context-managed, never silently reconnecting, never replaying a request.

```python
from agent_run_supervisor.arsd.client import ArsdClient

socket_path = "<XDG_RUNTIME_DIR>/agent-run-supervisor/arsd.sock"

with ArsdClient(socket_path) as client:
    client.server_info()                      # protocol/version handshake facts

    ack = client.submit(                      # caller-owned request_id = idempotency key
        request_id="my-caller-request-id",
        payload={
            "request": {...},                 # versioned AgentRunRequest (see below)
            "prompt_text": "Summarize the diff in plain language.",
            "workspace_root": "/path/to/bound/workspace",
        },
    )
    run_id = ack["run_id"]

    client.run_status(run_id)                          # accepted → progress → one terminal result
    client.run_events(run_id, from_seq=0, limit=100)   # bounded, seq-ordered page
    client.run_cancel(run_id)                          # cooperative; never rewrites a terminal fact

    client.session_list()                     # owner-scoped session inventory
    client.session_status("my-session-id")
    client.session_close("my-session-id")
```

Live tailing — `follow=True` returns a context-managed subscription of event frames:

```python
with ArsdClient(socket_path) as client:
    with client.run_events(run_id, from_seq=0, follow=True) as stream:
        for frame in stream:
            ...
```

The `request` object is a versioned `AgentRunRequest`: `owner` / `namespace`, **`agent_id`**, the
session-reuse choice, `requested_model` / `requested_effort`, input references, the frozen
`execution_grant` reference and hashes, credential **references**, and limits. `agent_id` names one
entry in the operator's registry; it passes its grammar before any resolution and names no path,
executable, argv token, environment key, digest, or version.

It never carries shell text, argv, environment values, executable paths, or credential material —
those fields do not exist on the wire.

Errors are typed and fail closed. Client exceptions carry a stable code (for example
`PEER_UID_DENIED`, `OWNER_MISMATCH`, `IDEMPOTENCY_CONFLICT`, `CAPACITY_EXHAUSTED`); server-side
message text is never echoed back into an exception.

## ACP protocol support

ARS speaks **ACP Protocol v1** (`protocolVersion: 1`) over stdio JSON-RPC, using the official Python
client library `agent-client-protocol`, pinned to **0.11.1** by the `native` extra.

Every profile freezes ACP protocol version `1`. A live agent that reports anything else fails the run at
`initialize`, before any prompt is dispatched. Every profile also requires the `loadSession` capability,
because same-session continuity uses a real `session/load` on an unchanged external session ID.

**A reuse request can never become a new session.** Not as a fallback, not after a failure, not under
any error class — that is structural, not conditional. An absent or corrupt session record, a missing
stored external ID, or a changed binding all fail *before* the lease, and every conflicting
identity-bearing callback is rejected at entry before any handler, event, filesystem access, or
permission decision runs.

Before any prompt, one connection must complete `initialize` → `session/new` or `session/load` →
discovery → set model → rediscovery → set effort → **exact readback**. A missing capability, an
unadvertised value, or an inexact readback produces zero turns and no prompt. The **live-advertised**
option set is the authority: ARS freezes no model or effort value domain, so an agent adding a model
today is a non-event.

A profile id that names an ACP generation — `standard-native-acp-v1` — freezes exactly that protocol
major. A future v2 would be a separate profile and session domain, never a revision of this one.

## Supported agents

A profile is a small, source-owned, versioned description of **how to speak ACP to a class of agent**.
It is not an agent list, and it carries no path, version, digest, model literal, or agent name.

| `profile` | Use it for | Extra ACP semantics |
|---|---|---|
| `standard-native-acp-v1` | every agent, native ACP or reached through an independently installed ACP adapter command | — |
| `claude-agent-acp-compat-v1` | one adapter whose ACP behavior deviates in a way live discovery cannot express | frozen session metadata sent on **both** `session/new` and `session/load`, plus a required permission-mode selector proven by exact readback |

**Which agents are supported is your registry, not this table.** Any agent that speaks ACP v1 over
stdio — directly, or through an ACP adapter command you installed — is one registry entry against
`standard-native-acp-v1`. A non-ACP CLI needs an adapter command; it does not, by itself, need a
profile.

Admitting a *new* compatibility profile requires all three of: a cited, reproducible observation at the
ACP layer; a demonstration that the deviation cannot be expressed by live discovery, exact readback, a
selector-id hint, or an operator environment value; and review. That bar exists because a per-agent
profile would re-couple that agent's routine upgrades to ARS releases — the exact cost this
architecture removes.

Model and effort literals come from the running agent, not from ARS. You pass them per run and the agent
must read them back exactly.

## Agent runtimes you install

ARS launches agents; it does not ship, install, host, freeze, or verify them. For each agent you want:

1. install it with your own package manager, wherever you like — including below `$HOME`, behind a
   version-manager shim, or through a symlink farm;
2. add one registry entry naming its `command` (and `args`, if its ACP mode needs a subcommand);
3. declare any environment it needs that the base allowlist does not cover — `PATH` first;
4. run `agents validate`, then `agents doctor`, then the **mandatory denied-action canary** for that
   agent before using it;
5. restart `arsd` so the new registry is read.

Upgrading that agent afterwards, behind the same registered command, needs nothing from ARS.

**No artifact ownership.** There is no ARS-owned artifact prefix, no package closure, no tree digest, no
frozen interpreter identity, no promotion, and no attestation. ARS performs no ownership, mode,
ancestor, symlink, or digest check on your command or on anything it loads. What ARS records per run is
the declared command, the exact argv, the resolved environment **names**, and — as explicitly
non-authoritative evidence — what it observed: the PATH hit, the image the kernel mapped, and the
agent's self-reported name and version. None of that gates a run or blocks continuity.

**What ARS gives up by that.** ARS no longer detects a swapped or modified executable. That trade is
deliberate: the detection ran as the same UID that then executed the agent with full authority, so it
never bounded what a byte-identical agent could do. Executable integrity belongs to your OS and
deployment tooling — package signatures, immutable images, filesystem permissions, host integrity
tooling — and ARS's contribution is per-run recorded evidence for after-the-fact audit.

## Operator-visible changes

Seven changes on the registry boundary deserve a note in your own runbook:

1. **The agent project-config workspace refusal disappears.** ARS no longer refuses a workspace for
   containing an agent's own project configuration file — that file is agent-owned.
2. **You author `env_passthrough` / `env_overlay`** for anything the base allowlist does not cover.
   `PATH` is the most likely cause of "works in my shell, fails under ARS", and `SSH_AUTH_SOCK` is
   deliberately opt-in, because forwarding it hands the agent live use of your SSH keys.
3. **New launch records carry environment names, source classes, and precedence only.** No value, no
   digest of a value, no length. Older value-bearing records are read value-blind and their free-form
   text is withheld behind stable categorical markers.
4. **A registry edit takes effect at the next daemon start, not the next run** — while an agent upgrade
   behind an unchanged registered command costs nothing.
5. **Adding `session_epoch` for the first time cuts that agent's existing sessions**, because absent ≠
   1. Comparison is symmetric equality, so this is the same deliberate act as a bump. If you do not want
   the cut, do not add the field. Nothing else bumps it — not an agent upgrade, not an ARS upgrade, not
   a command, args, environment, or selector edit, not a file replacement, not a restart.
6. **Guarding short, common environment values erases substantial evidence.** `TERM`, `LANG`, `TZ`,
   `USER`, `HOME`, and `PATH` elements are all in the guard's literal set, so run text that echoes them
   is replaced or withheld. Confidentiality wins over evidence completeness: there is no minimum secret
   length and no inconvenience waiver. Coarse suppression counters make the loss measurable rather than
   invisible — and this is the tradeoff most likely to surprise you while triaging a failed run.
7. **The canonical workspace root and the effective `cwd` stay complete literals and stay
   hash-covered.** They are independently derived authority facts, not environment-value flow, and are
   deliberately outside the guarded set — so a workspace under `$HOME` appears in full in `spec.json`.
   Guarding them would break workspace binding, reconciliation attribution, and audit.

At a cutover, additionally: **every live session ends once.** Sessions created under the retired
identity model are refused for reload with a stable code while staying owner-scoped readable, and
continuing that work means a new session with caller-owned context handoff.

## Guarantees and boundaries

**What ARS guarantees**

- **Supervisor, not business judge.** Protocol or process completion is never a business verdict;
  `business_verdict` is always `null` and belongs to the caller.
- **Default-deny, caller-frozen permissions.** The caller freezes the execution grant; ARS enforces
  it and never widens or refreshes it. Registered workspace-internal reads may be allowed; write,
  terminal, execute, and unknown operations are denied. Every decision produces redacted mediation
  evidence. The mediation environment binding is source-owned in key and value, applied last, and a
  registry entry can select one or none but can never author, replace, or disable it.
- **No environment value in an ARS sink.** Every environment value is treated as sensitive regardless
  of key name, length, or shape. No projected literal — and no digest, fingerprint, or length of one —
  reaches an ARS artifact, hash input, log, error, event, inspect response, or API response.
- **Auditable by default.** Runs produce deterministic, redacted artifacts with restrictive
  permissions: `0700` directories, `0600` files, atomic final writes. ARS writes to exactly two
  surfaces — the supervisor root and its socket path — and nothing else.
- **Fail closed on uncertainty.** Invalid input, protocol drift, denied permissions, timeouts, and
  untrustworthy recovery resolve to deterministic non-success states rather than a guess. Nothing
  auto-retries, replays, or resumes a prompt that may already have been dispatched, and there is no
  unquarantine tool.
- **Local and unprivileged.** A `0600` socket in a `0700` directory, peer-credential authentication
  against an explicit caller policy, and no root.

**What ARS is not**

- **Not a sandbox.** This is cooperative-agent policy mediation, not OS-level isolation, not
  hostile-process containment, and not multi-tenancy. The agent runs as the daemon's UID with that
  UID's full authority. Real isolation belongs at the OS layer — a dedicated UID, user namespaces,
  `seccomp`/Landlock, `bwrap`/container/VM boundaries, cgroup limits — and composes here, because you
  can register the isolation wrapper as the command.
- **Not an integrity or supply-chain check.** ARS does not verify that the executable it launched is
  the one you intended, is unmodified, or came from a trusted publisher.
- **Not a complete kill switch.** ARS reliably terminates its direct child and every descendant still
  in the process group it created. A descendant that leaves the group, a payload handed to a service
  manager as a separate unit, a container runtime that relocates it, or an agent that double-forks are
  all outside that guarantee. If work continues elsewhere anyway, the run fails loudly as `unknown` /
  `quarantined`, never silently.
- **Not a crash-containment mechanism by itself.** Production expects a user-level service manager
  cgroup (`Restart=on-failure`, `KillMode=control-group`) so killing the daemon kills every agent
  descendant still inside it.
- **Not a credential manager.** ARS resolves, mints, refreshes, and stores no credentials. Agents use
  their own auth stores under their own `HOME`. If you project a token or an agent socket into the
  child, that value reaches the child by your declaration — ARS records only its name and source class,
  and cannot stop the child from writing it, sending it, or disclosing it in transformed form.
- **Not an ingress, a gateway, or a chat integration.** No public ingress, no message delivery, no
  agent-to-agent routing. Those belong to the caller and its platform.

## Development

The primary path uses [uv](https://docs.astral.sh/uv/); the root [`Makefile`](Makefile) wraps the
common commands.

```bash
make sync      # uv sync --locked --extra dev --extra release --extra native
make verify    # full local gate (same as CI)
make build     # sdist/wheel + twine check
make clean     # remove build artifacts, caches, local scratch data
make help      # list all targets
```

Equivalent without Make:

```bash
uv sync --locked --extra dev --extra release --extra native
./scripts/verify_local.sh
```

`make verify` / `./scripts/verify_local.sh` is the single local gate — tests, read-only CLI smoke,
docs index checks, a static safety scan, and package checks. It is what CI runs, and it is documented
in [`docs/roadmap/verification.md`](docs/roadmap/verification.md).

Coverage, matching CI:

```bash
uv run pytest --cov --cov-branch --cov-report=term-missing
```

The suite covers the Native ACP core and the `arsd` daemon — protocol framing, peer authentication
and ownership, admission and idempotency, reconciliation, the client round-trip — against a hermetic
fake agent and temporary sockets. Suites needing a real agent runtime are opt-in, skipped by default,
and never run in CI.

**pip fallback** (without uv):

```bash
pip install -e '.[dev,release,native]'
python3 -m pytest -q
```

## Contributing

Issues and pull requests are welcome.

1. **Read the authority chain first.** Documentation precedes code here:
   [`GOAL.md`](GOAL.md) → [`docs/product/prd.md`](docs/product/prd.md) →
   [`docs/design/architecture.md`](docs/design/architecture.md) →
   [`docs/design/technical-solution.md`](docs/design/technical-solution.md) →
   [`docs/roadmap/features.md`](docs/roadmap/features.md) →
   [`docs/roadmap/current-status.md`](docs/roadmap/current-status.md).
   [`docs/design/agent-registry.md`](docs/design/agent-registry.md) is the operator contract, and
   [`docs/roadmap/non-approvals.md`](docs/roadmap/non-approvals.md) records what is explicitly out of
   scope. Anything under `docs/archive/` is cold history and never current authority.
2. **Branch from `main`** with a short-lived task branch: `feat/`, `fix/`, `docs/`, or `cicd/`.
3. **Write the test first** for any behavior change, and keep the runtime standard-library only
   unless the change is explicitly approved to add a dependency.
4. **Run the gate**: `make verify` must be green before you open a PR.
5. **Use Conventional Commits**, and explain *why* the change exists rather than restating the diff.
6. **Never commit secrets** — no API keys, tokens, cookies, real UID mappings, socket paths, or other
   deployment values. Use `[REDACTED]` in documentation and examples.

A PR should state its summary, the source-of-truth docs it touches, its roadmap impact, a test plan
with commands and results, and a secret-safety statement. Full process:
[`docs/AI_FLOW.md`](docs/AI_FLOW.md).

## License

© the `agent-run-supervisor` authors. Released under the
**[MIT](https://opensource.org/license/mit)** license — see [`LICENSE`](LICENSE).

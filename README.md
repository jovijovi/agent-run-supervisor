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
[How it works](#how-it-works) ·
[Requirements](#requirements) ·
[Install](#install) ·
[Upgrade](#upgrade) ·
[Uninstall](#uninstall) ·
[Run `arsd`](#run-arsd) ·
[Use from Python](#use-from-python) ·
[ACP protocol support](#acp-protocol-support) ·
[Supported agents](#supported-agents) ·
[Required agent runtimes](#required-agent-runtimes) ·
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
submits a run — which agent profile, which model, which workspace, which prompt. ARS admits the
request, launches exactly one supervised agent process, mediates every permission request under a
default-deny policy, normalizes agent output into ordered events, classifies a supervisor-owned
status, and writes redacted artifacts with restrictive permissions.

You get **auditable evidence** instead of process-lifecycle code — enough to answer *what did the
agent try to do, what was it allowed to do, and how did it actually end?*

ARS reports technical supervision facts only. The business verdict stays yours.

## How it works

<p align="center">
  <img src="docs/assets/diagrams/how-it-works.svg" alt="A trusted local caller submits over the arsd Unix-domain socket; arsd authenticates the peer and admits the request; ars-core runs one RunTask over Native ACP against a registered external AGENT; normalized events, status, and redacted local artifacts come back" width="900">
</p>

The whole path is local:

1. **Your application connects to `arsd`**, the small unprivileged supervisor daemon.
2. **`arsd` listens on a Unix-domain socket** — a `0600` socket inside a `0700` directory. No TCP, no
   root, no public ingress.
3. **The peer is authenticated, then the request is admitted.** `arsd` reads peer credentials from
   the socket and maps them to a principal, then admits the request against your caller-owned
   `request_id`, which doubles as the idempotency key. Runs and sessions are owner-scoped: only their
   owner can query, stream, cancel, or close them.
4. **`ars-core` executes the work.** One in-process `RunTask` owns one supervised agent process and
   one Native ACP connection, driven by an immutable run spec frozen at admission.
5. **The agent is a registered external process** launched from a closed profile — no arbitrary
   command, argv, or environment passthrough from the wire.

Back over the same socket you get normalized, seq-ordered events and a supervisor-owned status, plus
redacted local artifacts on disk.

### Four authority layers

Nothing merges launch semantics with deployment facts:

| Layer | Owner | Freezes |
|---|---|---|
| `AgentProfile` / `AdapterContract` | code (registry) | launch shape, ACP protocol and capabilities, selectors, permission/config/session semantics |
| Agent Registration *(optional)* | operator | which agent a conformance profile is instantiated as — only selecting inside or narrowing what the contract already declared |
| Runtime Binding | operator | which CLI artifact is installed, at which immutable path, version, and digest |
| `ResolvedLaunchSpec` | one run | the sealed launch and runtime identity, hashed before spawn |

A caller chooses none of them. Admission reads the Binding exactly once per run and seals the result;
spawn, finalization, and reconciliation never re-read it.

### Two protocols, two different `1`s

ARS sits between two independently versioned protocols. Both currently say `1`, and they are not the
same `1`:

- **ACP Protocol v1** — the *downstream* Agent Client Protocol, spoken over stdio JSON-RPC between
  ARS and the agent process.
- **`arsd` API v1** — the *upstream* ARS-owned wire between your application and `arsd`. Every frame
  carries `api_version`; an unknown version is rejected rather than guessed.

An ACP change is an agent-compatibility fact, an `arsd` API change is a caller-compatibility fact,
and neither implies the other.

Design detail: [`docs/design/architecture.md`](docs/design/architecture.md).

## Requirements

| Need | Requirement |
|---|---|
| Runtime | **Python ≥ 3.11**. The package itself has **zero third-party runtime dependencies**. |
| Driving a real agent | The `native` extra, which pins the official ACP client library (`agent-client-protocol==0.11.1`). A base install imports fine and fails only when the SDK is actually used. |
| Running `arsd` | Linux with a POSIX user session for the AF_UNIX socket, plus a supervisor root, a Runtime Binding root, and at least one caller mapping you supply. |
| Crash containment | A user-level service manager cgroup and a CPython build with pidfd support. |
| Running an agent | The agent runtime installed and pinned locally, plus a promoted Binding generation for its profile. See [Required agent runtimes](#required-agent-runtimes). |

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
`runtime-binding inspect-run` are read-only and start no agent process.

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

Two upgrades change operator inputs and fail closed rather than guessing:

| Upgrade | What changed | What you do |
|---|---|---|
| → 0.5.1 | Daemon mode and `--print-service-unit` require `--binding-root`. | Re-render and reconfigure the service unit before restarting; an older unit fails closed. |
| → 0.5.2 | The Binding root became profile-scoped. A single root-level `active.json` is refused with `LEGACY_BINDING_LAYOUT`; a missing profile subtree with `PROFILE_BINDING_ABSENT`. | Move each generation to `profiles/<profile-id>/generations/<generation-id>/`, delete the root-level `active.json`, then run `runtime-binding promote` once per profile. |

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

The **Runtime Binding root** and the **installed agent artifacts** are operator-owned and outside
ARS. Remove them separately and deliberately.

## Run `arsd`

`arsd` is a module entry point, not a console script:

```bash
# options and boundaries (read-only)
python3 -m agent_run_supervisor.arsd --help

# render a user-scope systemd unit to stdout and exit.
# pure text: no privilege check, no reconciliation, no socket bind — nothing is
# installed, enabled, or started. --binding-root is required here too, so a
# rendered unit can never silently omit it; the path is argv data, not accessed.
python3 -m agent_run_supervisor.arsd \
  --binding-root <binding-root> \
  --print-service-unit

# start the daemon
python3 -m agent_run_supervisor.arsd \
  --supervisor-root <supervisor-root> \
  --binding-root <binding-root> \
  --caller-mapping <UID>:<principal_id>:<owner>:<namespace>
```

From a checkout without installing, prefix with `PYTHONPATH=src`.

Daemon mode requires `--supervisor-root`, `--binding-root`, and at least one `--caller-mapping` —
**zero mappings refuse to listen** — and refuses to start as root. `--socket` defaults to
`$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`, falling back to `<supervisor-root>/arsd/arsd.sock`.
`--max-concurrent-runs`, `--max-connections`, and `--log-level` bound the rest.

Caller mappings, socket paths, and the Binding root are deployment values. Keep them in a mode-`0600`
unit file, never in a repository.

On restart the daemon reconciles durable facts only: a run that may have been dispatched without a
trustworthy terminal result ends `unknown` / `quarantined` / `retryable=false`, and is never
re-prompted.

### The Runtime Binding

`--binding-root` points at the **operator-owned Runtime Binding**, the deployment half of a run. The
source contract owns launch and compatibility semantics; the Binding owns which CLI artifact is
installed, at which immutable path, version, and digest, plus any config-root value the profile
declared. A Binding never declares a command, argv, env key, adapter, capability, or selector.

ARS opens the Binding root **read-only, exactly once per run**, and never creates, writes, or
promotes it. Every profile refuses admission fail-closed until an operator has prepared an immutable
artifact root the daemon's own UID cannot rewrite and promoted a generation for that profile — so a
freshly started daemon with no promoted Binding runs nothing.

One daemon takes one root, which carries **one independently promotable selection per profile**:

```text
<binding-root>/
└── profiles/<profile-id>/
    ├── active.json                        # regular file, atomically replaced — never a symlink
    └── generations/<generation-id>/
        └── manifest.json                  # immutable once written
```

The operator authors these directories; ARS creates nothing here and writes only `active.json`.
Promoting or rolling back one profile replaces one file inside that profile's own subtree, so it
cannot disable, overwrite, or race another profile's selection.

The operator surface is a separate CLI, and each generation command acts on exactly one profile:

```bash
agent-run-supervisor runtime-binding validate    --binding-root <root> --profile <id> --generation <gen>
agent-run-supervisor runtime-binding promote     --binding-root <root> --profile <id> --generation <gen>
agent-run-supervisor runtime-binding rollback    --binding-root <root> --profile <id> --generation <gen>
agent-run-supervisor runtime-binding inspect-run --run-dir <native-run-dir>
```

For a profile instantiated per Agent Registration, add `--agent <agent-id>`; it is required for such
a profile and refused for any other.

There is no `--force`: a generation that does not validate is never promoted. Nothing here installs
an artifact, edits a unit file, escalates privilege, or restarts the daemon. Promotion takes effect
on the *next* run and never re-points a run that is already sealed.

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

The `request` object is a versioned `AgentRunRequest`: `owner` / `namespace`, `profile_id`, the
session-reuse choice, `requested_model` / `requested_effort`, input references, the frozen
`execution_grant` reference and hashes, credential **references**, and limits. A profile instantiated
per Agent Registration also takes `agent_id`; naming one for any other profile is refused.

It never carries shell text, argv, environment values, executable paths, or credential material —
those fields do not exist on the wire.

Errors are typed and fail closed. Client exceptions carry a stable code (for example
`PEER_UID_DENIED`, `OWNER_MISMATCH`, `IDEMPOTENCY_CONFLICT`, `CAPACITY_EXHAUSTED`); server-side
message text is never echoed back into an exception.

## ACP protocol support

ARS speaks **ACP Protocol v1** (`protocolVersion: 1`) over stdio JSON-RPC, using the official Python
client library `agent-client-protocol`, pinned to **0.11.1** by the `native` extra.

Every registered profile freezes ACP protocol version `1` in its contract. A live agent that reports
anything else fails the run at `initialize`, before any prompt is dispatched. Every profile also
requires the `loadSession` capability, because same-session continuity uses a real `session/load` on
an unchanged external session ID — silently creating a new session is a failure, never a fallback.

Before any prompt, one connection must complete `initialize` → `session/new` or `session/load` →
discovery → set model → rediscovery → set effort → **exact readback**. A missing capability, an
unadvertised value, or an inexact readback produces zero turns and no prompt.

A profile id that names an ACP generation — `standard-native-acp-v1` — freezes exactly that protocol
major. A future v2 would be a separate profile, registration, Binding, and session domain, never a
revision of this one.

## Supported agents

A profile is a closed, versioned, code-registered launch and compatibility contract. Submit every
literal below verbatim.

| `profile_id` | Agent | Launch | `requested_model` | `requested_effort` |
|---|---|---|---|---|
| `opencode-native-acp` | OpenCode | direct ACP | `kimi-for-coding/k3` | `low` / `high` / `max` *(default `max`)* |
| `codex-acp-1.1.7` | Codex, via its official ACP adapter | wrapped ACP | `gpt-5.6-sol` | `max` |
| `claude-agent-acp-0.63.0` | Claude, via its official ACP adapter | wrapped ACP | `claude-fable-5[1m]`, `opus[1m]` *(default)* | `max` |
| `standard-native-acp-v1` | any ACP-v1-conforming direct-ACP agent | direct ACP | per Agent Registration | per Agent Registration |

> `standard-native-acp-v1` is on `main` and **not in a published release yet** (see the
> `Unreleased` section of [`CHANGELOG.md`](CHANGELOG.md)). It freezes ACP-v1 conformance only and
> freezes no agent identity. Making a real agent runnable through it is an operator sequence —
> install the artifact, run zero-prompt ACP discovery, run the code-owned version probe, run the
> mandatory denied-action mediation canary, author a registration, then validate and promote a
> generation.

The literals come from two different namespaces. `profile_id` is ARS registry input, matched exactly
at admission. Model and effort literals are live ACP values that the agent advertises and must read
back exactly. Neither is interchangeable with the selector names a vendor's own CLI accepts — that is
a third namespace.

**A `profile_id` is not a CLI version.** It identifies a closed launch and compatibility contract.
Which downstream CLI build is deployed — path, version, digest — is a Runtime Binding fact owned by
the operator, which is why a profile id carrying an adapter version pins the *adapter contract*, not
the agent CLI you installed. This is also why speaking generic ACP does not remove the need for
profiles: ACP standardizes the wire, not the launch, the selector names, the permission semantics, or
the literals a given agent will actually accept and read back.

## Required agent runtimes

ARS launches agents; it does not ship or install them. Each profile needs its runtime installed under
a **root-owned immutable prefix that the `arsd` UID cannot rewrite**, and a promoted Binding
generation. The source-frozen prefix is `/opt/agent-run-supervisor/artifacts/`.

### Wrapped-ACP profiles: Codex and Claude

Both wrapped profiles run through an ARS-controlled Node interpreter and an official npm ACP adapter.
The contract source-freezes the interpreter and the adapter package closure — install root, whole-tree
digest, contained entry, and the `--no-global-search-paths` interpreter prefix that closes Node's
out-of-closure module search. All of it is re-proven at the spawn boundary.

| Dependency | Pinned identity | Frozen location |
|---|---|---|
| Node interpreter | v24.14.0, launched with `--no-global-search-paths` | `/opt/agent-run-supervisor/artifacts/node/v24.14.0/bin/node` |
| `@agentclientprotocol/codex-acp` | 1.1.7 | `/opt/agent-run-supervisor/artifacts/adapters/codex-acp/1.1.7` |
| `@agentclientprotocol/claude-agent-acp` | 0.63.0 | `/opt/agent-run-supervisor/artifacts/adapters/claude-agent-acp/0.63.0` |

Each adapter entry resolves inside its own install root:

```text
<install root>/node_modules/@agentclientprotocol/<package>/dist/index.js
```

The **downstream CLI** each adapter drives is a Binding fact, not a source constant:

| Profile | Binding slot | Env key the adapter honours |
|---|---|---|
| `codex-acp-1.1.7` | `downstream_cli` (package tree) | `CODEX_PATH` |
| `codex-acp-1.1.7` | `codex_home` (config root, credentials) | `CODEX_HOME` |
| `claude-agent-acp-0.63.0` | `downstream_cli` (package tree) | `CLAUDE_CODE_EXECUTABLE` |

Claude manages its own credential storage, which ARS neither stages nor inspects, so its admission
requires zero caller credential references. Codex binds its credential root through `codex_home`.

### Direct-ACP profiles: OpenCode and standard-native

A direct-ACP agent has no separate adapter — the deployed executable is both the agent CLI and the
ACP implementation — so its whole executable closure is bound through the Binding's `agent_cli` slot.
OpenCode additionally declares the `kimi-for-coding` credential slot; slot **names** only, never
values.

An adapter or CLI version bump is never a silent swap: it moves the frozen artifact identity, which
means a contract revision, and every Binding generation accepted under the old contract fails closed.

## Guarantees and boundaries

**What ARS guarantees**

- **Supervisor, not business judge.** Protocol or process completion is never a business verdict;
  `business_verdict` is always `null` and belongs to the caller.
- **Default-deny, caller-frozen permissions.** The caller freezes the execution grant; ARS enforces
  it and never widens or refreshes it. Registered workspace-internal reads may be allowed; write,
  terminal, execute, and unknown operations are denied. Every decision produces redacted mediation
  evidence.
- **Auditable by default.** Runs produce deterministic, redacted artifacts with restrictive
  permissions: `0700` directories, `0600` files, atomic final writes.
- **Fail closed on uncertainty.** Invalid input, protocol drift, denied permissions, timeouts, and
  untrustworthy recovery resolve to deterministic non-success states rather than a guess. Nothing
  auto-retries, replays, or resumes a prompt that may already have been dispatched.
- **Local and unprivileged.** A `0600` socket in a `0700` directory, peer-credential authentication
  against an explicit caller policy, and no root.

**What ARS is not**

- **Not a sandbox.** This is cooperative-agent policy mediation, not OS-level isolation, not
  hostile-process containment, and not multi-tenancy.
- **Not a crash-containment mechanism by itself.** Production expects a user-level service manager
  cgroup (`Restart=on-failure`, `KillMode=control-group`) so killing the daemon kills every agent
  descendant.
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

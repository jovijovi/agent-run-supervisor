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

<p align="center">
  <a href="https://github.com/jovijovi/agent-run-supervisor/actions/workflows/verify.yml">
    <img src="https://github.com/jovijovi/agent-run-supervisor/actions/workflows/verify.yml/badge.svg" alt="CI">
  </a>
  <a href="https://codecov.io/gh/jovijovi/agent-run-supervisor">
    <img src="https://codecov.io/gh/jovijovi/agent-run-supervisor/graph/badge.svg" alt="codecov">
  </a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT">
  </a>
</p>

<p align="center">
  A small, <b>local-first</b> supervisor for external coding AGENTs.<br>
  One local daemon, one process per run, and <b>redacted, auditable local evidence</b>.
</p>

## What it is

Anything that drives an external coding AGENT ends up rebuilding the same plumbing: launching and
babysitting the agent process, deciding what the agent is allowed to touch, reading a stream of
protocol events, classifying how the run ended, and scrubbing secrets out of everything before it
touches disk. Written ad-hoc, every caller grows its own subtly unsafe copy.

**Agent Run Supervisor (ARS)** factors that out into one independent local layer. Your application
submits a run — which agent profile, which model, which workspace, which prompt — and ARS does the
rest: it admits the request, launches exactly one supervised agent process, mediates every
permission request under a default-deny policy, normalizes what the agent emits into ordered events,
classifies a supervisor-owned status, and writes redacted artifacts with restrictive permissions.

What you get back is **auditable evidence**, not a tangle of process-lifecycle code.

Use it when you want to run a coding agent programmatically and still be able to answer *what did it
try to do, what was it allowed to do, and how did it actually end?*

## How it works

<p align="center">
  <img src="docs/assets/diagrams/how-it-works.svg" alt="A trusted local caller submits over the arsd Unix-domain socket; arsd authenticates the peer and admits the request; ars-core runs one RunTask over Native ACP against a registered external AGENT; normalized events, status, and redacted local artifacts come back" width="900">
</p>

The primary path is entirely local:

1. **Your application** connects to `arsd`, the small unprivileged supervisor daemon.
2. **`arsd` listens on a Unix-domain socket** — a `0600` socket inside a `0700` directory. No TCP,
   no root, no public ingress.
3. **The peer is authenticated and the request is admitted.** `arsd` reads the peer's credentials
   from the socket and maps them to a principal, then admits the request against your caller-owned
   `request_id`, which doubles as the idempotency key. Runs and sessions are owner-scoped: only the
   caller that owns one can query, stream, cancel, or close it.
4. **`ars-core` runs the work.** One in-process `RunTask` owns one supervised agent process and one
   Native ACP connection, driven by an immutable run spec frozen at admission.
5. **The agent is a registered external process** launched from a closed profile — no arbitrary
   command, argv, or environment passthrough from the wire.

Coming back, you get normalized, seq-ordered events and a supervisor-owned status over the same
socket, plus redacted local artifacts on disk. ARS reports **technical supervision facts only**;
your application owns the business verdict.

### Two protocols, two different `1`s

ARS sits between two independently versioned protocols. Both currently say `1`, and they are not the
same `1`:

- **ACP Protocol v1** — the *downstream* Agent Client Protocol, spoken over stdio JSON-RPC between
  ARS and the external AGENT process. Every registered profile freezes ACP protocol version `1` in
  its contract, and a live agent that reports anything else fails the run at `initialize`, before any
  prompt is dispatched.
- **`arsd` API v1** — the *upstream* ARS-owned wire between your application and `arsd` over the Unix
  socket. Every frame carries `api_version` (currently `1`); an unknown version is rejected rather
  than guessed.

They move independently: an ACP protocol change is an agent-compatibility fact, an `arsd` API change
is a caller-compatibility fact, and neither implies the other.

Design detail lives in [`docs/design/architecture.md`](docs/design/architecture.md).

## Install

Install from this repository — that is the supported way to get everything described below.

```bash
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor
```

The runtime is Python standard library only, so a checkout is immediately usable without installing
anything:

```bash
PYTHONPATH=src python3 -m agent_run_supervisor doctor
```

For an editable local install into the active environment:

```bash
pip install -e .

# with the optional extras used by the test suite and the Native ACP suites
pip install -e '.[dev,native]'
```

Nothing in ARS launches an agent implicitly. `doctor`, `replay`, `--print-service-unit`, and
`runtime-binding inspect-run` are read-only and start no agent process.

## Run `arsd` locally

`arsd` is a module entry point, not a console script:

```bash
# Options and boundaries (read-only)
PYTHONPATH=src python3 -m agent_run_supervisor.arsd --help

# Render a user-scope systemd unit to stdout and exit.
# Pure text: no privilege check, no reconciliation, no socket bind — nothing is
# installed, enabled, or started. --binding-root is required here too, so a
# rendered unit can never silently omit it; the path is argv data, not accessed.
PYTHONPATH=src python3 -m agent_run_supervisor.arsd \
  --binding-root <binding-root> \
  --print-service-unit

# Start the daemon
PYTHONPATH=src python3 -m agent_run_supervisor.arsd \
  --supervisor-root <supervisor-root> \
  --binding-root <binding-root> \
  --caller-mapping <UID>:<principal_id>:<owner>:<namespace>
```

Daemon mode requires `--supervisor-root`, `--binding-root`, and at least one `--caller-mapping` —
**zero mappings refuse to listen**, and the daemon refuses to start as root. `--socket` defaults to
`$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`, falling back to
`<supervisor-root>/arsd/arsd.sock`. `--max-concurrent-runs`, `--max-connections`, and `--log-level`
bound the rest.

Caller mappings, socket paths, and the Binding root are deployment values. Keep them in a
mode-`0600` unit file, never in a repository.

If the daemon is restarted, it reconciles durable facts only: a run that may have been dispatched
without a trustworthy terminal result ends `unknown` / `quarantined` / `retryable=false` and is
never re-prompted.

### The Runtime Binding

`--binding-root` points at the **operator-owned Runtime Binding**, the deployment half of a run.
The source contract owns the launch and compatibility semantics — launch shape, ACP protocol and
capabilities, selectors, permission and session semantics — and, for a wrapped-ACP profile, also the
identity of the ARS-controlled interpreter and adapter entry, which are ARS artifacts rather than
operator deployment facts. The Binding owns the operator's deployment facts: which downstream or
direct AGENT CLI artifact is installed, at which immutable path, at which version and digest, plus
any config-root value the profile declared. It never declares a command, argv, env key, adapter,
capability, or selector. A caller chooses neither side.

ARS opens the Binding root **read-only, exactly once per run**, and never creates, writes, or
promotes it. Every registered profile refuses admission fail-closed until an operator has prepared an
immutable artifact root the daemon's own UID cannot rewrite and promoted a generation for that
profile — so a freshly started daemon with no promoted Binding runs nothing.

The operator surface is a separate CLI:

```bash
agent-run-supervisor runtime-binding validate    --binding-root <root> --profile <id> --generation <gen>
agent-run-supervisor runtime-binding promote     --binding-root <root> --profile <id> --generation <gen>
agent-run-supervisor runtime-binding rollback    --binding-root <root> --profile <id> --generation <gen>
agent-run-supervisor runtime-binding inspect-run --run-dir <native-run-dir>
```

From a checkout without installing, replace `agent-run-supervisor` with
`PYTHONPATH=src python3 -m agent_run_supervisor`.

There is no `--force`: a generation that does not validate is never promoted. Nothing here installs
an artifact, edits a unit file, escalates privilege, or restarts the daemon. Promotion needs no
restart and takes effect on the *next* run, because admission re-reads the active pointer per run and
never re-points a run that is already sealed.

## Call it from Python

[`ArsdClient`](src/agent_run_supervisor/arsd/client.py) is the supported caller boundary: explicitly
connected, context-managed, never silently reconnecting and never replaying a request. Every frame
carries `api_version` (currently `1`); an unknown version is rejected rather than guessed.

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

# Live tailing: follow=True returns a context-managed subscription of event frames
with ArsdClient(socket_path) as client:
    with client.run_events(run_id, from_seq=0, follow=True) as stream:
        for frame in stream:
            ...
```

The `request` object is a versioned `AgentRunRequest`: `owner` / `namespace`, `profile_id`, the
session-reuse choice, `requested_model` / `requested_effort`, input references, the frozen
`execution_grant` reference and hashes, credential **references**, and limits.

It never carries shell text, argv, environment values, executable paths, or credential material —
those fields do not exist on the wire.

Errors are typed and fail closed. Client exceptions carry a stable code (for example
`PEER_UID_DENIED`, `OWNER_MISMATCH`, `IDEMPOTENCY_CONFLICT`, `CAPACITY_EXHAUSTED`); server-side
message text is never echoed back into an exception.

## Agent profiles

A profile is a closed, versioned, code-registered launch definition, and every registered profile
speaks **ACP Protocol v1** — distinct from the `arsd` API v1 your application speaks, as described
above. Model and effort must read back **exactly** from the live agent: a missing capability, an
unadvertised value, or an inexact readback fails the run before any prompt is dispatched.

| `profile_id` | Agent | Launch | `requested_model` | `requested_effort` |
|---|---|---|---|---|
| `opencode-native-acp` | OpenCode | direct ACP | `kimi-for-coding/k3` (default) | `low` / `high` / `max` (default `max`) |
| `codex-acp-1.1.7` | Codex, via its official ACP adapter | wrapped ACP | `gpt-5.6-sol` | `max` |
| `claude-agent-acp-0.63.0` | Claude, via its official ACP adapter | wrapped ACP | `claude-fable-5[1m]`, `opus[1m]` (default) | `max` |

Submit every literal above verbatim — but they come from two different places. `profile_id` is ARS
registry input: it names a contract in the code-registered registry and is matched exactly at
admission. The model and effort literals are live ACP values: the agent advertises them over the
wire and must read them back exactly. Neither kind is interchangeable with the selector names a
vendor's own CLI accepts — that is a third namespace.

**A `profile_id` is not a CLI version.** It is an ARS-owned identifier for a closed launch and
compatibility contract: the launch shape, the ACP protocol and required/forbidden capabilities, the
selector IDs, the exact model/effort domains proven by discovery, and the permission, config, and
session semantics. Which downstream CLI build is actually deployed — path, version, digest — is a
Runtime Binding fact owned by the operator, never a source constant, which is why a profile ID that
still carries an adapter version pins the *adapter contract*, not the agent CLI you installed. And
this is why speaking generic ACP does not remove the need for profiles: ACP standardizes the wire,
not the launch, the selector names, the permission semantics, or the literals a given agent will
actually accept and read back.

**The Claude contract moved to `claude-agent-acp-0.63.0`.** The registered Claude source contract is
`claude-agent-acp-0.63.0` (revision 4), frozen against zero-prompt ACP discovery of the 0.63.0
adapter. There is **no** `0.61.0` compatibility alias: the retired ID is now an unknown profile and
admission refuses it. Registration is a source fact only — the new revision still needs its own
operator acceptance and a Binding generation promoted at the new `adapter_contract_hash` before any
Run can use it, and a generation accepted under the old contract fails closed by design.

Each profile launches an agent runtime that you install and pin, but the split differs by launch
kind. A **wrapped-ACP** profile source-freezes the interpreter and adapter entry by absolute path
*and* hash, while the Binding freezes the downstream CLI by immutable path, version, and digest
closure. The **direct-ACP** profile has no separate adapter — the deployed executable is both the
agent CLI and the ACP implementation — so its executable and interpreter closure is bound entirely
through the Runtime Binding. Either way, identity is attested before spawn. A source checkout does
not, by itself, make an agent launchable: you still install the agent locally and promote a Binding
generation for its profile.

## Guarantees and boundaries

**What ARS guarantees**

- **Supervisor, not business judge.** Protocol or process completion is never a business verdict.
  `business_verdict` is always `null` and belongs to the caller.
- **Default-deny, caller-frozen permissions.** The caller freezes the execution grant; ARS enforces
  it and never widens or refreshes it. Registered workspace-internal reads may be allowed;
  write, terminal, execute, and unknown operations are denied. Every decision produces redacted
  mediation evidence.
- **Auditable by default.** Runs produce deterministic, redacted artifacts with restrictive
  permissions: `0700` directories, `0600` files, atomic final writes.
- **Fail closed on uncertainty.** Invalid input, protocol drift, denied permissions, timeouts, and
  untrustworthy recovery all resolve to deterministic non-success states rather than a guess.
- **Local and unprivileged.** A `0600` socket in a `0700` directory, peer-credential
  authentication against an explicit caller policy, and no root.

**What ARS is not**

- **Not a sandbox.** This is cooperative-agent policy mediation, not OS-level isolation, not
  hostile-process containment, and not multi-tenancy.
- **Not a crash-containment mechanism by itself.** Production expects a user-level service manager
  cgroup (`Restart=on-failure`, `KillMode=control-group`) so that killing the daemon kills every
  agent descendant.
- **Not an ingress, a gateway, or a chat integration.** No public ingress, no message delivery, no
  agent-to-agent routing. Those belong to the caller and its platform.

## Requirements

| Need | Requirement |
|---|---|
| Runtime | **Python ≥ 3.11**, standard library only — zero third-party runtime dependencies. |
| Running `arsd` | Linux with a POSIX user session for the AF_UNIX socket, plus a supervisor root, a Runtime Binding root, and at least one caller mapping you supply. Crash containment additionally needs a user-level service manager cgroup and a CPython build with pidfd support. |
| Running an agent | Each profile launches an agent runtime you install and pin locally, plus a promoted Binding generation for that profile. A checkout alone does not provide OpenCode, Codex, or Claude. |
| Tests (optional) | The `dev` extra for the suite; the `native` extra adds the ACP client library (`agent-client-protocol`, pinned to `0.11.1`) used by the Native ACP and `arsd` suites. |

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

`make verify` / `./scripts/verify_local.sh` is the single local gate: tests, read-only CLI smoke,
docs index checks, a static safety scan, and package checks. It is what CI runs, and it is
documented in [`docs/roadmap/verification.md`](docs/roadmap/verification.md).

The suite covers the Native ACP core and the `arsd` daemon — protocol framing, peer authentication
and ownership, admission and idempotency, reconciliation, the client round-trip — against a
hermetic fake agent and temporary sockets. Suites that need a real agent runtime are opt-in, skipped
by default, and never run in CI.

**pip fallback** (without uv):

```bash
pip install -e '.[dev,release,native]'
python3 -m pytest -q
```

## License

© the `agent-run-supervisor` authors. Released under the
**[MIT](https://opensource.org/license/mit)** license (see [`LICENSE`](LICENSE)).

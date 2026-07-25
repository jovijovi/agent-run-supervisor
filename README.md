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

Nothing in ARS launches an agent implicitly. `doctor`, `replay`, `--print-service-unit`,
`session list`, and dry runs are read-only and start no agent process.

## Run `arsd` locally

`arsd` is a module entry point, not a console script:

```bash
# Options and boundaries (read-only)
PYTHONPATH=src python3 -m agent_run_supervisor.arsd --help

# Render a user-scope systemd unit to stdout and exit.
# Pure text: no privilege check, no reconciliation, no socket bind — nothing is
# installed, enabled, or started.
PYTHONPATH=src python3 -m agent_run_supervisor.arsd --print-service-unit

# Start the daemon
PYTHONPATH=src python3 -m agent_run_supervisor.arsd \
  --supervisor-root <supervisor-root> \
  --caller-mapping <UID>:<principal_id>:<owner>:<namespace>
```

Daemon mode requires `--supervisor-root` and at least one `--caller-mapping` — **zero mappings
refuse to listen**, and the daemon refuses to start as root. `--socket` defaults to
`$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`, falling back to
`<supervisor-root>/arsd/arsd.sock`. `--max-concurrent-runs`, `--max-connections`, and `--log-level`
bound the rest.

Caller mappings and socket paths are deployment values. Keep them in a mode-`0600` unit file, never
in a repository.

If the daemon is restarted, it reconciles durable facts only: a run that may have been dispatched
without a trustworthy terminal result ends `unknown` / `quarantined` / `retryable=false` and is
never re-prompted.

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

A profile is a closed, versioned, code-registered launch definition. Model and effort must read back
**exactly** from the live agent — a missing capability, an unadvertised value, or an inexact
readback fails the run before any prompt is dispatched.

| `profile_id` | Agent | `requested_model` | `requested_effort` |
|---|---|---|---|
| `opencode-1.18.4` | OpenCode | `kimi-for-coding/k3` (default), `deepseek/deepseek-v4-pro` | `low` / `medium` / `high` / `max` (default `max`) |
| `codex-acp-1.1.7` | Codex, via its official ACP adapter | `gpt-5.6-sol` | `max` |
| `claude-agent-acp-0.61.0` | Claude, via its official ACP adapter | `claude-fable-5[1m]`, `opus[1m]` (default) | `max` |

Use the literals above verbatim. They are the identifiers the agent itself advertises over ACP, and
they are not interchangeable with the selector names a vendor's own CLI accepts.

Each profile launches an agent runtime that you install and pin — by absolute path *and* hash for
the interpreter, adapter entrypoint, and downstream CLI, proven at the spawn boundary. A source
checkout does not, by itself, make an agent launchable; you still install the agent locally.

## Compatibility surface: `acpx` CLI and library

The repository also provides a daemon-free compatibility interface built on `acpx`. It runs one-shot
`exec` and a local persistent-session lifecycle, and writes the same kind of redacted artifacts. Use
`arsd` when a run should pass through the supervisor daemon: peer-authenticated admission,
caller-owned idempotency, owner-scoped runs and sessions, and daemon-wide concurrency limits. Use the
compatibility interface directly when a single local process drives one agent itself and no daemon is
part of the deployment.

```bash
agent-run-supervisor validate-role <role>.json      # validate a role spec, print its stable hash
agent-run-supervisor doctor                         # read-only readiness probe, starts no agent
agent-run-supervisor replay <events>.ndjson         # deterministic replay, starts no agent
agent-run-supervisor run --role <role>.json --prompt-file <p>.txt --no-real-run   # compile + preview
agent-run-supervisor run --role <role>.json --prompt-file <p>.txt                 # one local agent
agent-run-supervisor session create|send|status|close|abort|list ...              # persistent session
agent-run-supervisor cleanup                        # plan retention; --apply actually deletes
```

From a checkout without installing, replace `agent-run-supervisor` with
`PYTHONPATH=src python3 -m agent_run_supervisor`. Real `run` and `session` turns need Node, `acpx`,
and the target agent CLI available locally.

Programmatically, prefer the generic caller boundary in
[`caller.py`](src/agent_run_supervisor/caller.py):

```python
from agent_run_supervisor.caller import CallerInvocationSpec, invoke_caller

result = invoke_caller(
    CallerInvocationSpec(
        mode="exec",
        role_file="reviewer.json",
        prompt="Summarize the diff in plain language.",
        cwd="/path/to/repo",
    )
)
print(result.supervisor_status)  # e.g. "completed"
print(result.run_dir)            # redacted artifact directory
assert result.business_verdict is None
```

Supported modes: `exec`, `exec_dry_run`, `session_create`, `session_send`, `session_status`,
`session_close`, `session_abort`, `session_list`.

Two helpers are worth knowing about:
[`session_inspect`](src/agent_run_supervisor/session_inspect.py) answers liveness and health
questions by reading local artifacts only — safe on a hot polling path because it spawns nothing —
and [`hermes_caller.events`](src/agent_run_supervisor/hermes_caller/events.py) pages through
structural progress while a run is still in flight, without exposing raw agent text.

Artifacts land under `.agent-run-supervisor/runs/<run_id>/` and
`.agent-run-supervisor/sessions/<session_id>/`. The payload contract is documented in
[`docs/design/result-event-schema.md`](docs/design/result-event-schema.md).

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
| Running `arsd` | Linux with a POSIX user session for the AF_UNIX socket, plus a supervisor root and at least one caller mapping you supply. Crash containment additionally needs a user-level service manager cgroup and a CPython build with pidfd support. |
| Running an agent | Each profile launches an agent runtime you install and pin locally. A checkout alone does not provide OpenCode, Codex, or Claude. |
| `acpx` compatibility runs | Node, `acpx`, and the target agent CLI available locally — needed only for real `run` and `session` turns. |
| Tests (optional) | The `dev` extra for the suite; the `native` extra adds the ACP client library used by the Native ACP and `arsd` suites. |

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

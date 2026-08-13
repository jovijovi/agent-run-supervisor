---
title: Quickstart
description: Install ARS, register one agent, start the daemon, and submit a first supervised Run.
---

# Quickstart

Five steps to a first supervised Run on your own machine. Steps 1–3 create only
the installation, checkout, and registry files you explicitly request; they do
not write ARS runtime state or submit a Run. Step 4 starts the daemon.

!!! note "Before you start"

    You need Linux with a POSIX user session, Python ≥ 3.11, and an ACP-capable
    coding agent **already installed by you**. ARS launches the command you
    register; it never installs, packages, or upgrades an agent. If you do not
    have one yet, pick a guide from [How-to](how-to/index.md) first.

## 1. Install

For a published release (recommended for production), install an exact PyPI
version into a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install 'agent-run-supervisor[native]==<X.Y.Z>'
. .venv/bin/activate
agent-run-supervisor --help
```

The `native` extra pins the official ACP client, which is what actually drives a
real agent. For development or an unreleased commit, install a clean, pinned
local checkout into a separate virtual environment:

```bash
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor
git checkout <full-40-character-commit-id>
test -z "$(git status --porcelain=v1 -uall)"
python3 -m venv .venv
.venv/bin/python -m pip install '.[native]'
. .venv/bin/activate
agent-run-supervisor --help
```

Do not treat `PYTHONPATH=src` as an installation or deploy from a moving/dirty
checkout. The complete immutable PyPI and pinned-source procedures are in the
[operations runbook](deployment/operations-runbook.md).

Keep that virtual environment active for the remaining steps. In a new shell,
return to the installation directory and run `. .venv/bin/activate` first; the
bare `agent-run-supervisor` and `python3` commands below then resolve to the
selected installation rather than an unrelated system package.

## 2. Write an agents file

The agent registry is **one operator-owned TOML file**, read exactly once at
daemon startup. Save this as `agents.toml` and replace the placeholders with
your agent's real command:

```toml title="agents.toml"
schema_version = 1

[agents.my-agent]                                    # the agent_id a caller names
profile   = "standard-native-acp-v1"                 # how to speak ACP
command   = "my-agent-cli"                           # PATH name, or an absolute path
args      = ["acp"]
mediation = "ask-privileged-tool-families-v1"        # selects a source-owned binding

# A user daemon inherits a minimal environment. Declare what your agent needs.
env_passthrough = ["SOME_PROVIDER_TOKEN"]
env_overlay     = { PATH = "/usr/local/bin:/usr/bin:/bin" }
```

The file must be a regular file that is **not group- or world-writable**. The
complete field set and its bounds are in the [agents file
reference](reference/agents-file.md).

!!! tip "`PATH` is the usual cause of \"works in my shell, fails under ARS\""

    A user-level daemon typically inherits a minimal `PATH` that omits
    `~/.local/bin` and version-manager shim directories. Either declare an
    `env_overlay.PATH` you own, or register an absolute `command`.

## 3. Validate and probe

Check the file offline. This starts nothing:

```bash
agent-run-supervisor agents validate --agents-file agents.toml
```

Then run the per-agent diagnostic, which reports the projected environment
**names** and the declared launch:

```bash
# Projection report only — starts no external child.
agent-run-supervisor agents doctor --agents-file agents.toml --agent my-agent --no-probe

# Without --no-probe this starts the registered command for a zero-prompt ACP
# handshake. The child writes its own agent-owned state and is reaped on every path.
agent-run-supervisor agents doctor --agents-file agents.toml --agent my-agent
```

!!! danger "Run the denied-action canary before you trust mediation"

    Permission mediation is cooperative: an agent that ignores the knob can
    execute in-process tools with no ACP permission event at all, and zero
    permission events prove nothing about denial. Before putting an agent into
    service, submit a Run whose grant forbids an action you can observe, ask the
    agent to perform exactly that action, and confirm the denial appears in the
    Run's events. The canary proves the knob works **for that one agent**.

## 4. Start the daemon

`arsd` is a module entry point. `--agents-file` must be an **absolute** path;
daemon mode also requires a supervisor root and at least one caller mapping —
zero mappings refuse to listen — and refuses to run as root.

```bash
# Render a user-scope service unit to stdout. Pure text; installs nothing.
python3 -m agent_run_supervisor.arsd \
  --agents-file /absolute/path/to/agents.toml \
  --print-service-unit

# Start it.
python3 -m agent_run_supervisor.arsd \
  --supervisor-root /path/to/supervisor-root \
  --agents-file /absolute/path/to/agents.toml \
  --caller-mapping <UID>:<principal_id>:<owner>:<namespace>
```

`--socket` defaults to `$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`, falling
back to `<supervisor-root>/arsd/arsd.sock`.

!!! warning "Caller mappings and socket paths are deployment values"

    Keep them in a mode-`0600` unit file, never in a repository. See
    [Local daemon](deployment/local-daemon.md).

## 5. Submit your first Run

`ArsdClient` is the supported caller boundary: explicitly connected,
context-managed, never silently reconnecting, never replaying a request.

```python title="first_run.py"
from agent_run_supervisor.arsd.client import ArsdClient

socket_path = "<XDG_RUNTIME_DIR>/agent-run-supervisor/arsd.sock"

with ArsdClient(socket_path) as client:
    client.server_info()                      # protocol/version handshake facts

    ack = client.submit(                      # request_id = your idempotency key
        request_id="my-first-request",
        payload={
            "request": {
                "owner": "my-team",
                "namespace": "my-team/docs",
                "agent_id": "my-agent",       # an agent_id in your registry
                # "session_id" is OMITTED, so this creates one durable Session
                # and runs its first Run. Naming one reuses it, existing-only.
                "expected_binding_hash": None,
                "input_refs": [
                    {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64},
                ],
                "requested_model": "<model-the-agent-advertises>",
                "requested_effort": "<effort-the-agent-advertises>",
                "grant_ref": "grant:my-first-grant",
                "grant_hash": "sha256:" + "b" * 64,
                "grant_role_hash": "sha256:" + "c" * 64,
                "grant_capabilities": ["read"],
                "mcp_snapshot_hashes": [],
                "credential_refs": [],
                "limits": {},                 # {} takes the sealed defaults
                "evidence_policy_hash": "sha256:" + "d" * 64,
                "recovery_policy_hash": "sha256:" + "e" * 64,
            },
            "prompt_text": "Summarize the diff in plain language.",
            "workspace_root": "/path/to/bound/workspace",
        },
    )
    run_id = ack["run_id"]

    client.run_status(run_id)                          # accepted → progress → one terminal
    client.run_events(run_id, from_seq=0, limit=100)   # bounded, seq-ordered page

    with client.run_events(run_id, follow=True) as stream:
        for frame in stream:
            print(frame)
```

The values above are placeholders. Replace owner, namespace, agent, model,
effort, the grant and policy hashes, and the input refs with values from your own
admission and grant pipeline — a configured daemon will refuse them as-is.

## What you just built

- One **Session** was created durably by that first `submit`. Pass the
  `session_id` from the terminal result back on your next `submit` to continue
  the same conversation. Sessions are resumable indefinitely.
- One **Run** executed inside it and terminated with a supervisor-owned status.
- The Run's normalized events and its redacted `result.json` are under your
  supervisor root, readable with:

```bash
agent-run-supervisor run inspect --run-dir <supervisor-root>/<path-to-run>
```

## Next

- [Runs and Sessions](concepts/runs-and-sessions.md) — the lifecycle rules that
  decide how you structure a caller.
- [Register an agent](how-to/register-an-agent.md) — the registry in depth.
- [Socket API](reference/socket-api.md) — every operation, field, and error code.
- [Error codes](reference/error-codes.md) — what a refusal means and what to do.

---
title: Local daemon
description: Starting arsd — flags, the supervisor root, the socket, caller mappings, and what each refusal means.
---

# Local daemon

`arsd` is a module entry point, separate from the `agent-run-supervisor` console
script. The console script carries three read-only operator commands and starts
nothing; the daemon is what serves Runs.

## Starting it

```bash
python3 -m agent_run_supervisor.arsd \
  --supervisor-root <supervisor-root> \
  --agents-file /absolute/path/to/agents.toml \
  --caller-mapping <UID>:<principal_id>:<owner>:<namespace>
```

| Flag | Required | Notes |
|---|---|---|
| `--agents-file` | always | Must be an **absolute** path, in daemon mode *and* when rendering a service unit |
| `--supervisor-root` | daemon mode | The single state directory ARS owns |
| `--caller-mapping` | daemon mode, at least one | `UID:principal_id:owner:namespace`. **Zero mappings refuse to listen** |
| `--socket` | no | Defaults to `$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`, falling back to `<supervisor-root>/arsd/arsd.sock` |
| `--print-service-unit` | no | Renders a user-scope unit to stdout and exits. Pure text; installs nothing |

`arsd` **refuses to run as root**.

!!! warning "These are deployment values, not repository content"

    Caller mappings, socket paths, and the registry path describe one
    installation. Keep them in a mode-`0600` unit file or an environment file
    that is ignored by version control — never in a repository, an issue, or a
    documentation page.

## The socket

One `AF_UNIX` stream socket, mode `0600`, inside a `0700` directory. No TCP, no
root, and nothing reachable from outside the machine.

On every connection `arsd` authenticates the peer's credentials, maps the UID to
a principal through your `--caller-mapping` entries, and scopes every Run and
Session to that owner. A UID with no mapping is refused with `PEER_UID_DENIED`;
an authenticated caller reaching another owner's Run or Session gets
`OWNER_MISMATCH`.

## Idempotency

Your own `request_id` is the idempotency key. Repeating one returns the same
`run_id` and `session_id` facts and dispatches nothing a second time. Reusing a
`request_id` for a *different* request is `IDEMPOTENCY_CONFLICT`.

If a submission's outcome cannot be determined safely, you get
`SUBMISSION_INDETERMINATE` rather than a guess. Treat it as "unknown, do not
blindly resubmit with a new id" — resubmit the *same* `request_id` to learn the
real outcome.

## Capacity and backpressure

| Code | Meaning |
|---|---|
| `SESSION_BUSY` | a live lease already holds that Session; one Run at a time |
| `CAPACITY_EXHAUSTED` | the daemon is at its configured concurrency limit |
| `EVENT_BACKLOG_EXCEEDED` | a follow subscriber fell too far behind its bounded queue |
| `SHUTTING_DOWN` | the daemon is draining and accepts no new work |

All four are ordinary, expected conditions in a busy caller. Handle them; do not
treat them as faults.

## The supervisor root

Everything ARS persists lives here, as `0700` directories and `0600` files with
atomic final writes:

```text
<supervisor-root>/
  arsd/                       # socket, when no XDG_RUNTIME_DIR is available
  <run storage>/
    spec.json                 # the sealed per-Run spec
    launch.json               # value-blind launch snapshot
    events.jsonl              # the persisted normalized, seq-ordered event stream
    result.json               # the terminal
    stderr.log                # redacted
    redaction-report.json
  <session storage>/
    session.json              # durable Session record, with schema_version
```

Read one Run's recorded evidence with:

```bash
agent-run-supervisor run inspect --run-dir <run-dir>
```

Native ACP state stays in its own isolated run and session roots. ARS never
reads, writes, imports, mirrors, or migrates any pre-existing legacy session
storage in either direction.

## Termination and crash containment

ARS reliably terminates its direct child and every descendant still in the
process group it created: close, `SIGTERM` to the group, a bounded wait, then
`SIGKILL` and a final bounded wait.

!!! danger "Crash containment is external to ARS, and load-bearing"

    A descendant that calls `setsid()`, a payload handed to a service manager as
    a separate transient unit, a container runtime that relocates the payload to
    another namespace and cgroup, or an agent that double-forks is outside the
    group ARS created.

    Production expects a **user-level service manager cgroup** with
    `Restart=on-failure` and `KillMode=control-group`, plus a CPython build with
    pidfd support. See [Service manager](systemd.md).

    When work does continue somewhere ARS cannot reach, the Run fails loudly as
    `unknown` / quarantined / `retryable = false` rather than silently.

## Startup refusals

Registry defects and reconciliation defects both refuse to bind the socket, so a
misconfigured daemon never accepts a single request. The whole registry file is
refused — never partially honored, never cached from a previous start, never
repaired.

Refusals name the failing rule and, where operator-facing, a field path or an
environment **name** — never an overlay value or a raw file fragment. Full list:
[Error codes](../reference/error-codes.md).

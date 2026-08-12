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
| `--max-run-event-budget-bytes` | no | Admission ceiling on one Run's event ledger, in bytes. Defaults to `4294967296` (4 GiB). Must be a positive integer no larger than `1048576000000` (1 MiB × 1 000 000, the most any Run could request); anything else refuses to start |
| `--print-service-unit` | no | Renders a user-scope unit to stdout and exits. Pure text; installs nothing |

`arsd` **refuses to run as root**.

!!! warning "These are deployment values, not repository content"

    Caller mappings, socket paths, and the registry path describe one
    installation. Keep them in a mode-`0600` unit file or an environment file
    that is ignored by version control — never in a repository, an issue, or a
    documentation page.

## The per-Run event budget

`--max-run-event-budget-bytes` is this daemon's admission policy: a `submit`
whose `max_event_bytes * max_events` exceeds it is refused with
`INVALID_REQUEST` before any Run or Session exists. It applies to every Run this
daemon accepts, while each Run still seals its own `max_event_bytes` and
`max_events`. `server_info` reports the effective value as
`limits.max_run_event_budget_bytes`.

It is a **theoretical per-Run ceiling on persistent event-ledger bytes** — the
worst case of one `events.jsonl`. It is **not** preallocated memory, **not** the
total disk quota of a Run directory, and **not** a daemon-wide aggregate across
concurrent Runs. Sizing storage is still yours: budget for the Runs you keep,
not for one Run's worst case.

The effective ceiling is also written into each accepted Run's write-once
`submission.json`, so you can audit which policy admitted a historical Run after
you change this flag and restart. A later daemon never rewrites that record.

Lowering the ceiling changes what the daemon **accepts**, never what it already
accepted. Retransmitting an accepted `request_id` returns the original `run_id`,
`session_id`, and `accepted_at` whatever the ceiling is now, and dispatches
nothing a second time; the same `request_id` with different content is still
`IDEMPOTENCY_CONFLICT`. Only a genuinely new submission over the current ceiling
is refused with `INVALID_REQUEST`.

The per-field bounds — `max_event_bytes` at most 1 MiB and at least 256,
`max_events` at most 1 000 000 — are independent structural limits that this
flag does not move. Their product is also this flag's own maximum: a larger
ceiling could admit nothing extra. See [Socket API](../reference/socket-api.md).

`--print-service-unit` carries a configured ceiling into the rendered
`ExecStart`, so the unit you install starts the daemon you just configured. At
the default it renders nothing, and a value the daemon would refuse is refused
at render time instead of at the next start.

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
    submission.json           # the write-once admission record for this Run
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

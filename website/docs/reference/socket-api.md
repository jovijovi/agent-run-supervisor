---
title: Socket API
description: The arsd API v3 wire contract — envelope, operations, request fields, and bounds.
---

# Socket API

`arsd` speaks one line-framed JSON protocol over a local `AF_UNIX` stream
socket. This page is the wire contract; the [Python client](api/client.md)
implements it.

## Transport

| Property | Value |
|---|---|
| Address family | `AF_UNIX`, `SOCK_STREAM` |
| Socket mode | `0600`, inside a `0700` directory |
| Default path | `$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock`, falling back to `<supervisor-root>/arsd/arsd.sock` |
| Authentication | peer credentials, mapped to a principal by operator `--caller-mapping` entries |

There is no TCP listener and no other transport.

## The request envelope

Every request is one JSON object drawn from exactly these four keys. Three are
always required; `payload` is operation-dependent:

```json
{
  "api_version": 3,
  "op": "submit",
  "request_id": "my-caller-request-id",
  "payload": { }
}
```

| Key | Required | Type | Rule |
|---|---|---|---|
| `api_version` | yes | `int` | Must be `3`. Anything else is refused with `UNSUPPORTED_API_VERSION` rather than guessed — for every operation, including `server_info` |
| `op` | yes | `string` | One of the operations below, else `UNKNOWN_OP` |
| `request_id` | yes | `string` | Caller-owned. Matches `[A-Za-z0-9._-]{1,128}`. **This is the idempotency key** |
| `payload` | operation-dependent | `object` | Operation-specific and closed: unknown keys are refused. When present it must be a JSON object; when omitted it is read as an empty one, so you may leave it out exactly where the operation below takes `{}` |

There is no fifth key: an unrecognised envelope key is `INVALID_REQUEST`. Leaving
`payload` out of an operation that requires fields is not a shortcut either — the
empty payload simply fails that operation's own field rules with
`INVALID_REQUEST`.

Result and error frames carry the correlating `request_id`, **not** a version.

### Bounds

| Bound | Value |
|---|---|
| Maximum frame size | 1 048 576 bytes (`FRAME_TOO_LARGE`) |
| Maximum prompt size | 262 144 bytes |
| Maximum `request_id` length | 128 characters |
| Maximum error message length | 512 characters |
| Maximum JSON nesting depth | 64 |

## Operations

Eight, and deliberately no ninth.

| `op` | Payload | Returns |
|---|---|---|
| `server_info` | `{}` | protocol and version handshake facts |
| `submit` | see below | `{"run_id", "session_id", "accepted_at"}` |
| `run_status` | `{"run_id"}` | admission state, or the terminal result |
| `run_events` | `{"run_id", "from_seq", "limit", "follow", "follow_idle_seconds"}` | a bounded `seq`-ordered page, or a follow stream |
| `run_cancel` | `{"run_id"}` | cooperative cancel; never rewrites a terminal fact |
| `session_status` | `{"session_id"}` | the Session projection |
| `session_list` | `{}` | owner-scoped Session projections |
| `agent_list` | `{}` or omitted | `{"agent_ids": [...]}` — the canonical agent ids this daemon loaded |

Runs and Sessions are owner-scoped: reaching another owner's is `OWNER_MISMATCH`.

## `submit`

The payload key set is closed:

| Key | Required | Meaning |
|---|---|---|
| `request` | yes | the `AgentRunRequest` object below |
| `prompt_text` | yes | the prompt, within the prompt bound |
| `workspace_root` | yes | the canonical workspace root |
| `cwd` | no | the effective working directory |
| `retry_of_run_id` | no | caller-declared provenance |

### The `request` object

Closed and complete. Unknown keys are refused.

```json
{
  "owner": "my-team",
  "namespace": "my-team/docs",
  "agent_id": "my-agent",
  "expected_binding_hash": null,
  "input_refs": [{"ref": "prompt:inline", "content_hash": "sha256:<64 hex>"}],
  "requested_model": "<model-the-agent-advertises>",
  "requested_effort": "<effort-the-agent-advertises>",
  "grant_ref": "grant:my-grant-1",
  "grant_hash": "sha256:<64 hex>",
  "grant_role_hash": "sha256:<64 hex>",
  "grant_capabilities": ["read"],
  "mcp_snapshot_hashes": [],
  "credential_refs": [],
  "limits": {},
  "evidence_policy_hash": "sha256:<64 hex>",
  "recovery_policy_hash": "sha256:<64 hex>"
}
```

`limits` is required as an object, but each of its six fields is optional. `{}` takes all
sealed defaults; an omitted field takes its own default independently.

| Field | Type | Sealed default | Accepted bound | What it limits |
|---|---|---:|---:|---|
| `startup_timeout_seconds` | number | `60.0` | `0 < value <= 3_600.0` | initialize, Session start/load, and exact configuration before Prompt dispatch |
| `turn_timeout_seconds` | number | `21_600.0` (6 hours) | `0 < value <= 604_800.0` (7 days) | the complete Prompt / AGENT multi-loop execution of this Run |
| `cancel_grace_seconds` | number | `10.0` | `0 < value <= 300.0` | the grace window after termination begins and before forced kill |
| `max_stderr_bytes` | integer | `262_144` | `1 <= value <= 67_108_864` | retained bounded stderr bytes |
| `max_event_bytes` | integer | `65_536` | `256 <= value <= 1_048_576` | one normalized event record |
| `max_events` | integer | `10_000` | `1 <= value <= 1_000_000` | events reserved for the Run |

The combined event budget must also satisfy
`max_event_bytes * max_events <= max_run_event_budget_bytes` — the admission
ceiling the daemon was started with. It defaults to **4 GiB**
(`4294967296` bytes); an operator overrides it with
[`--max-run-event-budget-bytes`](../deployment/local-daemon.md), and
`server_info` reports the effective value as
`limits.max_run_event_budget_bytes`. Unknown limit keys, invalid numeric types,
non-finite or non-positive values, and values above an inclusive maximum are
refused with `INVALID_REQUEST`.

!!! note "What the event budget is, and what it is not"

    It bounds the **theoretical worst case of one Run's persistent event
    ledger**: `max_event_bytes` × `max_events` for that Run's `events.jsonl`.

    It is **not** preallocated memory, **not** the total disk quota of a Run
    directory — which also holds the sealed spec, launch snapshot, result, and
    bounded stderr — and **not** a daemon-wide aggregate across concurrent Runs.
    The per-field bounds in the table above are separate structural limits, and
    no daemon setting moves them.

`turn_timeout_seconds` is a hard **Run** timeout, not a Session lifetime. It
starts at Prompt dispatch and covers the complete AGENT tool/multi-turn loop
inside that Prompt operation. Expiry preserves the supervisor's existing
process-group terminate → `cancel_grace_seconds` → kill/reap sequence. Reusing a
Session starts a new Run whose limits are sealed independently.

!!! contract "What is not on the wire, by construction"

    There is no shell text, argv, environment name or value, executable path, or
    credential material on a request. **Those fields do not exist.** `command`,
    `args`, and environment declarations are the operator's, carried by the
    registry; `credential_refs` are *references* recorded as admission evidence
    and never resolved to values.

### `session_id` — the whole Session choice

| Form | Behaviour |
|---|---|
| the key is **omitted** | atomically create one new durable Session and run its first Run |
| an existing Session id | reuse it, existing-only; never falls back to creating one |
| present, but `null` | refused with `INVALID_REQUEST` |

Absent and present-null are different caller statements. Collapsing them would
let a caller whose id-producing code returned `None` silently start a second
conversation. See [Runs and Sessions](../concepts/runs-and-sessions.md).

### Idempotency

`request_id` is the idempotency key. Repeating one returns the same `run_id` and
`session_id` facts and dispatches nothing a second time. Reusing it for a
*different* request is `IDEMPOTENCY_CONFLICT`. If the outcome cannot be
determined safely you get `SUBMISSION_INDETERMINATE` rather than a guess —
resubmit the *same* `request_id` to learn the real outcome, never a new one.

## `run_events`

```json
{"run_id": "...", "from_seq": 0, "limit": 100, "follow": false}
```

- Non-follow reads return a bounded page in `seq` order, starting at `from_seq`.
- `follow: true` opens a stream on that connection. The subscription is
  connection-exclusive and never sends `run_cancel` on your behalf.
- A subscriber that falls too far behind its bounded queue is dropped with
  `EVENT_BACKLOG_EXCEEDED`.

## `agent_list`

The read-only roster of canonical `agent_id` values this daemon loaded at startup.

```json
{"api_version": 3, "op": "agent_list", "request_id": "my-caller-request-id", "payload": {}}
```

The `payload` key may be omitted entirely, or sent as `{}`. It is otherwise
closed: any field is refused with `INVALID_REQUEST` rather than ignored.

The reply is the generic result frame, like every other operation's — the roster
is the `result` object inside it, not the frame:

```json
{
  "request_id": "my-caller-request-id",
  "result": {"agent_ids": ["claude", "codex", "cursor", "oh-my-pi", "opencode"]}
}
```

The [Python client](api/client.md) unwraps that frame and returns the complete
`result` object, so `client.agent_list()` gives you
`{"agent_ids": [...]}` — the object, deliberately not a bare list.

- The ids are unique and returned in stable sorted order. A valid empty registry
  returns `{"agent_ids": []}` — success, not an error and not a default agent.
- The `result` object has exactly one key. There is no command, argv, executable
  path, environment name or value, profile, credential, adapter parameter,
  capability, model, role, priority, or health field, and none is coming.
- The source is the daemon's **immutable in-memory snapshot**, parsed once at
  startup. A request never reopens or re-parses the agents file, so editing that
  file under a serving daemon changes nothing until the daemon is restarted —
  which is an operator action, not something this operation triggers.

!!! contract "Registration is not eligibility"

    Membership means one thing: this daemon process has a registry entry with
    that id. It is **not** health, readiness, authorization, capability, role
    suitability, or permission to run anything. `submit` remains the execution
    admission boundary, and it can still refuse a Run for an id that appears
    here. A caller may use membership as one admission fact of its own, and must
    fail closed when the query fails or the id it wanted is absent.

`server_info.operations` advertises the operation, but `server_info` itself
carries no roster. Against a daemon that predates `agent_list` the answer is the
ordinary `UNKNOWN_OP` — there is no feature-specific code and no partial answer.

## Errors

Every error frame carries a stable code and a bounded message. Server-side text
is never echoed back into a client exception — the client raises a typed
exception with local, stable text only.

```json
{"request_id": "my-caller-request-id", "error": {"code": "SESSION_BUSY", "message": "..."}}
```

Full vocabulary: [Error codes](error-codes.md).

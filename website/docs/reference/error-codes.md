---
title: Error codes
description: The complete arsd wire-error set, plus selected startup and Run evidence codes.
---

# Error codes

Errors are typed and fail closed. Every error frame carries a stable code and a
bounded message, and **server-side text is never echoed back into a client
exception** — the Python client raises a typed exception with local, stable text
only.

The first four sections below — through **Capacity and lifecycle** — enumerate
the complete `arsd` wire-error set. Later sections document separate,
source-owned startup and Run evidence vocabularies. Those values can appear in
records and diagnostics, are not socket error frames, and are intentionally not
presented as one exhaustive cross-layer list.

## Protocol and framing

| Code | Meaning | What to do |
|---|---|---|
| `UNSUPPORTED_API_VERSION` | the envelope's `api_version` is not `3` | send `3`. There is no drain window, dual protocol, or alias |
| `UNKNOWN_OP` | `op` is not one of the eight operations | check the [operation list](socket-api.md#operations). A daemon older than an operation answers this too — fail closed rather than retry |
| `MALFORMED_FRAME` | the frame is not decodable JSON, or nests deeper than 64 | fix the encoder |
| `FRAME_TOO_LARGE` | the frame exceeds 1 048 576 bytes | send less; prompts have their own 262 144-byte ceiling |
| `INVALID_REQUEST` | a field is missing, malformed, unknown, or refused | see below — this is the code that carries the closed-key rules |

`INVALID_REQUEST` is what you get for an unknown key anywhere in the request, for
`session_id` present as `null`, and for a `request_id` outside
`[A-Za-z0-9._-]{1,128}`.

## Authentication and ownership

| Code | Meaning | What to do |
|---|---|---|
| `UNAUTHENTICATED_PEER` | peer credentials could not be established | check that you are connecting over the local socket, not a proxy |
| `PEER_UID_DENIED` | the peer UID maps to no principal | the operator must add a `--caller-mapping` and restart |
| `OWNER_MISMATCH` | the authenticated identity does not own that Run or Session | Runs and Sessions are owner-scoped; use your own |

## Admission and idempotency

| Code | Meaning | What to do |
|---|---|---|
| `IDEMPOTENCY_CONFLICT` | the `request_id` was already used for a *different* request | use a fresh `request_id` for genuinely new work |
| `SUBMISSION_INDETERMINATE` | the submission's outcome cannot be determined safely | resubmit the **same** `request_id` to learn the real outcome. Never a new one, and never a blind retry |
| `UNKNOWN_RUN` | no such Run for this owner | check the `run_id` from the submit ack |
| `UNKNOWN_SESSION` | no such Session for this owner | reuse is existing-only; omit `session_id` to create one |

!!! danger "`SUBMISSION_INDETERMINATE` is not a retry signal"

    It means ARS will not guess whether your prompt was dispatched. Resubmitting
    with a *new* `request_id` is exactly the mistake it exists to prevent: it can
    dispatch the same work twice. Resubmit the same key.

## Capacity and lifecycle

| Code | Meaning | What to do |
|---|---|---|
| `SESSION_BUSY` | a live lease already holds that Session — one Run at a time | serialize your Runs per Session, or back off and retry |
| `CAPACITY_EXHAUSTED` | the daemon is at its concurrency limit | back off and retry. This is normal load shedding |
| `EVENT_BACKLOG_EXCEEDED` | a follow subscriber fell too far behind its bounded queue | consume faster, or use bounded paging instead of `follow` |
| `SHUTTING_DOWN` | the daemon is draining | stop submitting; existing Runs finish |
| `INTERNAL` | an unclassified server-side failure | a bug. The message is bounded and carries no payload |

## Registry — the daemon refuses to listen

These never reach a caller: they refuse startup, before the socket is bound.

| Class | Codes |
|---|---|
| unavailable / unreadable / unsafe mode | `REGISTRY_ABSENT`, `REGISTRY_UNREADABLE`, `REGISTRY_UNSAFE_MODE`, `REGISTRY_NOT_REGULAR_FILE` |
| malformed | `REGISTRY_PARSE`, `REGISTRY_UNKNOWN_KEY`, `REGISTRY_SCHEMA_VERSION`, `REGISTRY_TOO_LARGE` |
| entry defects | `AGENT_ID_INVALID`, `ENTRY_FIELD_MISSING`, `ENTRY_UNKNOWN_PROFILE`, `ENTRY_COMMAND_INVALID`, `ENTRY_ARG_TOKEN_INVALID`, `ENTRY_ENV_KEY_INVALID`, `ENTRY_ENV_VALUE_INVALID`, `ENTRY_SELECTOR_INVALID`, `ENTRY_CAPABILITY_INVALID`, `ENTRY_UNKNOWN_MEDIATION_ID`, `ENTRY_SESSION_EPOCH_INVALID` |
| mediation authority | `MEDIATION_KEY_COLLISION` |
| launch-permission authority | `LAUNCH_PERMISSION_KEY_COLLISION` |

`agents validate` applies the identical checks offline, so you see them at
authoring time rather than at restart. See [Agents file](agents-file.md).

## Selected per-Run, pre-dispatch detail codes

These commonly actionable `detail_code` values fail one Run before any prompt is
sent. This table is not the complete Run-detail vocabulary; the exact terminal
record and the source version that emitted it remain authoritative.

| Code | Meaning |
|---|---|
| `AGENT_NOT_REGISTERED` | no such `agent_id` in the startup snapshot |
| `COMMAND_NOT_FOUND` | `exec` returned `ENOENT`. Usually `PATH`, or a moved shim — a configuration error, not a security refusal |
| `COMMAND_NOT_EXECUTABLE` | `exec` returned `EACCES` |
| `SPAWN_FAILED` | any other `exec` failure |
| `PROTOCOL_MISMATCH` | the agent's protocol major disagrees with the profile's frozen major |
| `CAPABILITY_MISSING` | a capability the profile requires is absent |
| `CAPABILITY_FORBIDDEN` | a capability the profile or entry forbids is present |
| `CONFIG_FIDELITY` | configuration readback was inexact or coerced — including a model id that is not byte-for-byte the requested one |
| `PERMISSION_MODE_UNPROVEN` | a compatibility profile could not prove its required permission mode by exact readback |
| `STARTUP_TIMEOUT` | the agent did not complete startup inside the Run's bound |
| `EVIDENCE_PIPELINE` | normalized evidence could not be finalized safely |

No process exists in the `COMMAND_NOT_*` and `SPAWN_FAILED` cases.

## Selected post-dispatch detail codes

These fail a Run whose prompt was already dispatched. The same caveat applies:
this is not the complete Run-detail vocabulary.

| Code | Meaning |
|---|---|
| `PERMISSION_VIOLATION` | a tool call reported `completed` after ARS denied that same call, or a write-family tool completed without the grant capability that could have allowed it. `status = failed`, `retryable = false`, Session still reusable |

!!! warning "`PERMISSION_VIOLATION` is detection, not prevention"

    It is observed after the completion is reported, so it never means the side
    effect was blocked — only that the Run must not persist as `completed`. See
    [Events](events.md#the-permission-families).

## Terminal error codes

The five terminal statuses map to their own codes. See
[Results](results.md#statuses).

| `status` | `error_code` |
|---|---|
| `completed` | `null` |
| `failed` | `FAILED` |
| `cancelled` | `CANCELLED` |
| `timed_out` | `TIMED_OUT` |
| `unknown` | `UNKNOWN` |

## Launch-permission markers

Categorical codes attached to launch-permission material rather than to a
supervision verdict:

| Code | Meaning |
|---|---|
| `LAUNCH_PERMISSION_UNKNOWN_POLICY` | the profile named a policy that is not registered |
| `LAUNCH_PERMISSION_UNSUPPORTED_GRANT` | the grant cannot be faithfully compiled by that policy backend |
| `LAUNCH_PERMISSION_MATERIALIZE_FAILED` | the private per-Run material could not be written |
| `LAUNCH_PERMISSION_CLEANUP_FAILED` | it could not be removed after the child was proven reaped. **Hygiene, not a supervision verdict** — the Run's terminal status is unaffected |

## Handling errors in Python

```python
from agent_run_supervisor.arsd.client import (
    ArsdClient,
    ArsdClientError,
    ERROR_CODE_TO_EXCEPTION,
)

SessionBusy = ERROR_CODE_TO_EXCEPTION["SESSION_BUSY"]

with ArsdClient(socket_path) as client:
    try:
        client.submit(request_id="req-1", payload=payload)
    except SessionBusy:
        ...                      # serialize, or back off
    except ArsdClientError as exc:
        print(exc.code)          # the stable code
        print(exc.message)       # local, bounded text — never server-supplied
```

Every **wire code in the first four sections** has a generated exception type in
`ERROR_CODE_TO_EXCEPTION`, and each is a subclass of `ArsdClientError`.
Registry refusals and Run/result detail codes are evidence and diagnostics, not
keys in that client mapping. See the [client API](api/client.md).

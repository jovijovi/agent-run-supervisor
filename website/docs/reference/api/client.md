---
title: Client
description: ArsdClient — the supported caller boundary for the arsd local socket.
---

# Client

`agent_run_supervisor.arsd.client` is the supported caller boundary.

## `ArsdClient`

::: agent_run_supervisor.arsd.client.ArsdClient

### Usage notes

The methods map one-to-one onto the [socket API](../socket-api.md) operations.
Three of them carry rules a signature cannot express:

**`submit`** — omitting `session_id` creates one new durable Session and runs its
first Run; sending an existing id reuses it, existing-only. A *present* `None` is
refused with `INVALID_REQUEST`, because absent and null are different caller
statements. `request_id` is the idempotency key.

**`run_events`** — with `follow=False` it returns a bounded, `seq`-ordered page.
With `follow=True` it returns an [`ArsdFollowSubscription`](#follow-subscription)
that takes over the connection until you close it.

```python
# Bounded page.
page = client.run_events(run_id, from_seq=0, limit=100)

# Live stream. The context manager closes the subscription deterministically.
with client.run_events(run_id, follow=True) as stream:
    for frame in stream:
        ...
```

**`agent_list`** — returns the complete result object, not a bare list, so a
later result field cannot silently change the return type:

```python
result = client.agent_list()
assert result == {"agent_ids": ["claude", "codex"]}
```

The ids are the canonical `agent_id` values the connected daemon loaded at
startup, unique and stable-sorted. Membership is a registration fact about that
daemon — not health, readiness, authorization, or execution eligibility, and
`submit` remains the admission boundary. Against a daemon that predates the
operation this raises the `UNKNOWN_OP` exception; fail closed rather than
falling back to a roster you read from a file yourself.

## Follow subscription

::: agent_run_supervisor.arsd.client.ArsdFollowSubscription

A follow subscription is **connection-exclusive**: while one is open, that client
cannot issue other operations. It never sends `run_cancel` on your behalf —
leaving a stream is not cancelling a Run.

Iterating with `for` yields an ephemeral generator, so `break` drops the last
strong reference and the generator's `finally` closes the client. Explicit
`.close()` and context-manager exit are both supported and preferred.

## Exceptions

::: agent_run_supervisor.arsd.client.ArsdClientError

Every code in the closed error set has its own generated subclass, looked up
through this mapping:

::: agent_run_supervisor.arsd.client.ERROR_CODE_TO_EXCEPTION

```python
from agent_run_supervisor.arsd.client import ArsdClientError, ERROR_CODE_TO_EXCEPTION

CapacityExhausted = ERROR_CODE_TO_EXCEPTION["CAPACITY_EXHAUSTED"]

try:
    client.submit(request_id="req-1", payload=payload)
except CapacityExhausted:
    ...                              # back off; normal load shedding
except ArsdClientError as exc:
    log.error("arsd refused: %s", exc.code)
```

!!! contract "Exception text is local, never remote"

    `ArsdClientError.message` is a bounded, locally-generated string. Remote
    `message` fields from the server are **discarded**, not passed through, so an
    untrusted payload cannot reach your logs or your users through an exception.
    Switch on `.code`, which is drawn from the closed
    [error-code set](../error-codes.md).

::: agent_run_supervisor.arsd.client.raise_for_error_code

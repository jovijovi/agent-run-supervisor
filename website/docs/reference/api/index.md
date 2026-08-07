---
title: Python API
description: The supported caller boundary — ArsdClient and the arsd protocol constants.
---

# Python API

The supported caller boundary is small on purpose.

| Page | Surface |
|---|---|
| [Client](client.md) | `ArsdClient`, the follow subscription, and the typed exception family |
| [Protocol](protocol.md) | version constants, the operation set, the error-code set, and frame bounds |

## What is public, and what is not

Public is what appears on these two pages: the client, its exceptions, and the
protocol constants a caller needs to validate its own frames.

Everything else in the package — the daemon server, request handlers, admission,
reconciliation, the Native ACP driver, and the storage layer — is **internal**.
It is not a caller-stable surface, it carries no compatibility promise, and it is
deliberately absent from this reference. Documenting it would advertise a
boundary that does not exist.

## Installing

```bash
pip install 'agent-run-supervisor[native]'
```

The runtime is standard library only, so the base install imports with no
third-party packages at all. The `native` extra pins the official ACP client,
which the daemon needs to drive a real agent; a caller that only talks to a
running `arsd` does not need it.

## The shape of a caller

```python
from agent_run_supervisor.arsd.client import ArsdClient

with ArsdClient(socket_path) as client:
    ack = client.submit(request_id="req-1", payload=payload)
    run_id = ack["run_id"]

    for frame in client.run_events(run_id, follow=True):
        ...

    terminal = client.run_status(run_id)
```

Three properties are worth stating explicitly, because they are choices rather
than accidents:

- **Explicitly connected.** The client connects on `connect()` or context entry,
  and raises if you try to connect twice.
- **Never silently reconnects.** A broken connection is an error you see, not a
  retry you did not ask for.
- **Never replays a request.** Idempotency is the server's, keyed on your
  `request_id`; the client does not resend on your behalf.

## Errors

Every wire error code has a generated exception type, all subclasses of
`ArsdClientError`:

```python
from agent_run_supervisor.arsd.client import ERROR_CODE_TO_EXCEPTION

SessionBusy = ERROR_CODE_TO_EXCEPTION["SESSION_BUSY"]
```

Exception text is **local and bounded**. Remote server messages are discarded
rather than echoed into an exception, so nothing untrusted reaches your logs
through this path. See [Error codes](../error-codes.md).

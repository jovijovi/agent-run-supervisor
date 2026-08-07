---
title: Protocol
description: Version constants, the operation set, the error-code set, and frame bounds.
---

# Protocol

`agent_run_supervisor.arsd.protocol` holds the constants a caller needs to
validate its own frames without hard-coding literals.

## Versioning

::: agent_run_supervisor.arsd.protocol.ARSD_API_VERSION

::: agent_run_supervisor.arsd.protocol.SUPPORTED_API_VERSIONS

Every *request* envelope carries `api_version`, and anything other than the
supported value is refused with `UNSUPPORTED_API_VERSION` rather than guessed —
for every operation, including `server_info`. Result and error frames carry the
correlating `request_id`, not a version.

```python
from agent_run_supervisor.arsd import protocol

assert protocol.ARSD_API_VERSION in protocol.SUPPORTED_API_VERSIONS
```

## Operations

::: agent_run_supervisor.arsd.protocol.OPERATIONS

Seven, and deliberately no eighth. See the [socket API](../socket-api.md#operations).

## Error codes

::: agent_run_supervisor.arsd.protocol.ERROR_CODES

The closed set every error frame draws from. `ERROR_CODE_TO_EXCEPTION` in the
[client](client.md#exceptions) is generated from exactly this set, so a code and
its exception type can never drift apart.

## Bounds

::: agent_run_supervisor.arsd.protocol.MAX_FRAME_BYTES

::: agent_run_supervisor.arsd.protocol.MAX_PROMPT_BYTES

::: agent_run_supervisor.arsd.protocol.MAX_REQUEST_ID_CHARS

A frame over the byte ceiling is refused with `FRAME_TOO_LARGE`. A `request_id`
outside `[A-Za-z0-9._-]{1,128}` is `INVALID_REQUEST`.

## Framing

::: agent_run_supervisor.arsd.protocol.encode_frame

::: agent_run_supervisor.arsd.protocol.decode_frame

These are the same functions the client uses. A caller implementing the wire
protocol in another language should reproduce their behaviour rather than
approximating it: the bounds above, and the nesting-depth limit, are part of the
contract.

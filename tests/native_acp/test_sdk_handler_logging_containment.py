"""Containment of the ACP SDK's handler-exception logging (SDK 0.12.0).

``agent-client-protocol`` 0.11.1 changed exactly one file, ``acp/connection.py``,
in exactly two places: ``_run_request`` and ``_run_notification`` now call
``logging.exception(..., exc_info=exc)`` when a handler raises. 0.11.0 answered
the request from the exception and, for a *notification*, dropped it through
``contextlib.suppress(Exception)`` — emitting nothing at all. 0.12.0's
transport refactor moved the byte framing out of that file but left both
``logging.exception`` calls exactly where they were, so the containment below
is asserted against 0.12.0 unchanged.

Both calls are module-level ``logging.exception``, so their records are emitted
directly on the **root** logger, which ``arsd`` configures with
``logging.basicConfig``. Two things then reach that boundary verbatim, neither
of which ARS can contain at its own callback surface:

1. A ``session/update`` frame the SDK's own params validation rejects never
   reaches :class:`NativeAcpClient` at all — the ``pydantic`` ``ValidationError``
   is raised inside the SDK and renders the rejected wire values
   (``input_value=...``) into its message.
2. An exception raised by an injected ARS handler travels out as ``exc_info``,
   so an ``OSError`` naming an absolute workspace path prints that path in the
   traceback's final line.

Everything ARS itself writes goes through ``redaction`` before it lands in
evidence; this path bypassed all of it. These tests pin the containment, not the
SDK's behavior: agent-supplied content and handler exception detail must not
reach the root logger, while the operational fact — which ACP method failed, and
with what exception type — must survive.

``caplog`` stands in for the handler ``arsd`` installs on the root logger: both
are root handlers, so both see exactly the records the root logger admits.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from typing import Any

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.native_acp.client import NativeAcpClient

_SESSION_ID = "sess-logging-containment"
# Short enough that pydantic renders it whole rather than eliding its middle.
_AGENT_PAYLOAD = "hunter2"
_WORKSPACE_PATH = "/srv/private-workspace/.env.secret"

# Rejected by SessionNotification validation inside the SDK, before any ARS
# callback runs: the update kind is not in the discriminated union.
_REJECTED_NOTIFICATION = {
    "jsonrpc": "2.0",
    "method": "session/update",
    "params": {
        "sessionId": _SESSION_ID,
        "update": {
            "sessionUpdate": "history_replay_v2",
            "content": {"type": "text", "text": _AGENT_PAYLOAD},
        },
    },
}

# Schema-valid request whose injected ARS handler raises with the path in it.
_FAILING_FS_READ = {
    "jsonrpc": "2.0",
    "id": 7,
    "method": "fs/read_text_file",
    "params": {"sessionId": _SESSION_ID, "path": _WORKSPACE_PATH},
}

# Valid and deliverable: its callback proves the two frames ahead of it in
# receive order have already been dispatched and handled. No sleeps.
_DELIVERABLE_NOTIFICATION = {
    "jsonrpc": "2.0",
    "method": "session/update",
    "params": {
        "sessionId": _SESSION_ID,
        "update": {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": "DELIVERABLE"},
        },
    },
}


def _frame(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


async def _drive_failing_handlers() -> None:
    """Feed a real ``ClientSideConnection`` the two failing frames."""
    from acp.client.connection import ClientSideConnection

    client_sock, agent_sock = socket.socketpair()
    client_reader, client_writer = await asyncio.open_connection(sock=client_sock)
    agent_reader, agent_writer = await asyncio.open_connection(sock=agent_sock)

    delivered = asyncio.Event()

    async def fs_read(request: dict[str, Any]) -> str:
        # The shape run_task's real handler produces when the validated
        # workspace path cannot be read: an OSError naming that path.
        raise FileNotFoundError(2, "No such file or directory", request["path"])

    client = NativeAcpClient(
        on_update=lambda session_id, update: delivered.set(),
        fs_read_handler=fs_read,
    )
    # Bound before any frame is written (B1): these frames carry the expected
    # external id, so the identity gate admits them and the failures under test
    # are the SDK's own validation error and the injected handler's exception —
    # not an identity refusal.
    client.expected_session_id = _SESSION_ID
    ClientSideConnection(lambda _conn: client, client_writer, client_reader)
    try:
        for payload in (
            _REJECTED_NOTIFICATION,
            _FAILING_FS_READ,
            _DELIVERABLE_NOTIFICATION,
        ):
            agent_writer.write(_frame(payload))
        await agent_writer.drain()
        await asyncio.wait_for(delivered.wait(), 10)
        # Deterministic scheduling ticks: let the request runner finish its
        # error response and its logging call. No wall-clock wait.
        for _ in range(50):
            await asyncio.sleep(0)
    finally:
        for writer in (agent_writer, client_writer):
            with contextlib.suppress(Exception):
                writer.close()
        assert agent_reader is not None


@pytest.fixture()
def failing_handler_log(caplog: pytest.LogCaptureFixture) -> str:
    caplog.set_level(logging.ERROR)
    asyncio.run(_drive_failing_handlers())
    return caplog.text


def test_rejected_notification_payload_never_reaches_the_root_logger(
    failing_handler_log: str,
) -> None:
    assert _AGENT_PAYLOAD not in failing_handler_log


def test_handler_exception_detail_never_reaches_the_root_logger(
    failing_handler_log: str,
) -> None:
    assert _WORKSPACE_PATH not in failing_handler_log


def test_no_traceback_frames_reach_the_root_logger(
    failing_handler_log: str,
) -> None:
    assert "Traceback (most recent call last)" not in failing_handler_log


def test_contained_records_still_name_the_method_and_exception_type(
    failing_handler_log: str,
) -> None:
    # Containment is redaction, not silence: an operator must still see that a
    # handler failed, for which ACP method, and with which exception class.
    assert "session/update" in failing_handler_log
    assert "fs/read_text_file" in failing_handler_log
    assert "ValidationError" in failing_handler_log
    assert "FileNotFoundError" in failing_handler_log


def test_ars_named_loggers_keep_their_own_exception_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The containment is scoped to records emitted *on* the root logger, which
    # is where the SDK writes and where ARS never does. A record propagated
    # from a named ARS logger reaches root's handlers without passing root's
    # filters, so ARS's own diagnostics are untouched.
    from agent_run_supervisor.native_acp import require_sdk

    require_sdk()
    caplog.set_level(logging.ERROR)
    logger = logging.getLogger("agent_run_supervisor.arsd.test-containment-scope")
    try:
        raise RuntimeError(_WORKSPACE_PATH)
    except RuntimeError:
        logger.exception("ars-authored diagnostic")
    assert _WORKSPACE_PATH in caplog.text
    assert "Traceback (most recent call last)" in caplog.text


def test_containment_is_installed_once_per_process() -> None:
    from agent_run_supervisor.native_acp import require_sdk

    require_sdk()
    before = list(logging.getLogger().filters)
    require_sdk()
    require_sdk()
    assert list(logging.getLogger().filters) == before

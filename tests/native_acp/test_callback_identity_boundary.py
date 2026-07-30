"""B1 — synchronous fail-closed identity rejection at callback entry.

Stage 1 WP1.3 (plan §5). Every ID-bearing callback surface compares the
incoming external Session id against the bound expectation *first*. A mismatch
— including an unbound expectation — records only a categorical violation and
raises, before normalization, queueing, handler invocation, filesystem access,
sink persistence, or any response at all, including the unsupported-surface
refusal. No id ever reaches the error text or the recorded evidence.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

pytest.importorskip("acp")

import acp.interfaces as sdk_interfaces  # noqa: E402
import acp.schema as sdk_schema  # noqa: E402

from agent_run_supervisor.native_acp.client import (  # noqa: E402
    NativeAcpClient,
    SessionIdentityViolation,
)

# The categorical code is pinned here as a literal contract: the recorded
# evidence and the error text are exactly this string and never an id.
SESSION_IDENTITY_VIOLATION = "SESSION_IDENTITY_VIOLATION"

EXPECTED = "external-session-expected"
OTHER = "external-session-other"

# Every ID-bearing surface the pinned SDK routes to a client callback.
ID_BEARING_SURFACES = (
    "session_update",
    "request_permission",
    "read_text_file",
    "write_text_file",
    "create_terminal",
    "terminal_output",
    "release_terminal",
    "wait_for_terminal_exit",
    "kill_terminal",
)


class Sinks:
    """Records every downstream effect a rejected callback must never cause."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.permissions: list[dict[str, Any]] = []
        self.reads: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []

    def on_update(self, session_id: str, payload: dict[str, Any]) -> None:
        self.updates.append((session_id, payload))

    async def permission(self, request: dict[str, Any]) -> dict[str, Any]:
        self.permissions.append(request)
        return {"decision": "deny", "reason": "spy"}

    async def read(self, request: dict[str, Any]) -> str:
        self.reads.append(request)
        return "content"

    async def write(self, request: dict[str, Any]) -> None:
        self.writes.append(request)

    def touched(self) -> bool:
        return bool(self.updates or self.permissions or self.reads or self.writes)


def _client(*, expected: str | None = EXPECTED) -> tuple[NativeAcpClient, Sinks]:
    sinks = Sinks()
    client = NativeAcpClient(
        on_update=sinks.on_update,
        permission_handler=sinks.permission,
        fs_read_handler=sinks.read,
        fs_write_handler=sinks.write,
    )
    client.expected_session_id = expected
    return client, sinks


def _tool_call() -> Any:
    return sdk_schema.ToolCallUpdate(toolCallId="call-1", title="t", kind="edit")


def _invoke(client: NativeAcpClient, surface: str, session_id: str):
    """Call one surface exactly as the pinned SDK router would (by keyword)."""
    calls = {
        "session_update": dict(
            session_id=session_id,
            update=sdk_schema.AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=sdk_schema.TextContentBlock(type="text", text="hi"),
            ),
        ),
        "request_permission": dict(
            session_id=session_id, tool_call=_tool_call(), options=[]
        ),
        "read_text_file": dict(session_id=session_id, path="/tmp/x", line=None, limit=None),
        "write_text_file": dict(session_id=session_id, path="/tmp/x", content="c"),
        "create_terminal": dict(
            session_id=session_id,
            command="/bin/true",
            args=None,
            env=None,
            cwd=None,
            output_byte_limit=None,
        ),
        "terminal_output": dict(session_id=session_id, terminal_id="term-1"),
        "release_terminal": dict(session_id=session_id, terminal_id="term-1"),
        "wait_for_terminal_exit": dict(session_id=session_id, terminal_id="term-1"),
        "kill_terminal": dict(session_id=session_id, terminal_id="term-1"),
    }[surface]
    return asyncio.run(getattr(client, surface)(**calls))


# ---------------------------------------------------------------------------
# pinned signatures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "surface",
    ID_BEARING_SURFACES + ("create_elicitation", "complete_elicitation"),
)
def test_callback_signature_matches_the_pinned_sdk_protocol(surface: str) -> None:
    """No varargs: the pinned SDK dispatches by keyword, so names are contract."""
    ours = inspect.signature(getattr(NativeAcpClient, surface))
    theirs = inspect.signature(getattr(sdk_interfaces.Client, surface))

    def shape(sig: inspect.Signature) -> list[tuple[str, Any, Any]]:
        return [
            (name, param.kind, param.default)
            for name, param in sig.parameters.items()
            if name != "self"
        ]

    assert shape(ours) == shape(theirs)
    assert not any(
        param.kind is inspect.Parameter.VAR_POSITIONAL
        for param in ours.parameters.values()
    )


# ---------------------------------------------------------------------------
# the wrong-ID matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface", ID_BEARING_SURFACES)
def test_wrong_id_is_rejected_before_any_side_effect(surface: str) -> None:
    client, sinks = _client()

    with pytest.raises(SessionIdentityViolation) as violation:
        _invoke(client, surface, OTHER)

    # No sink, handler, filesystem access, or response — not even the
    # unsupported-surface refusal, which is a formulated response.
    assert not sinks.touched()
    assert not isinstance(violation.value, PermissionError)
    # Categorical evidence only.
    assert client.identity_violation == SESSION_IDENTITY_VIOLATION
    message = str(violation.value)
    assert message == SESSION_IDENTITY_VIOLATION
    for secret in (EXPECTED, OTHER):
        assert secret not in message
        assert secret not in str(client.identity_violation)


@pytest.mark.parametrize("surface", ID_BEARING_SURFACES)
def test_unbound_expected_id_is_itself_a_violation(surface: str) -> None:
    client, sinks = _client(expected=None)

    with pytest.raises(SessionIdentityViolation):
        _invoke(client, surface, EXPECTED)

    assert not sinks.touched()
    assert client.identity_violation == SESSION_IDENTITY_VIOLATION


def test_matching_id_reaches_the_existing_behavior() -> None:
    client, sinks = _client()

    asyncio.run(
        client.session_update(
            session_id=EXPECTED,
            update=sdk_schema.AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=sdk_schema.TextContentBlock(type="text", text="hi"),
            ),
        )
    )
    response = asyncio.run(
        client.request_permission(session_id=EXPECTED, tool_call=_tool_call(), options=[])
    )
    read = asyncio.run(client.read_text_file(session_id=EXPECTED, path="/tmp/x"))
    asyncio.run(client.write_text_file(session_id=EXPECTED, path="/tmp/x", content="c"))

    assert [session for session, _ in sinks.updates] == [EXPECTED]
    assert len(sinks.permissions) == 1
    assert read.content == "content"
    assert len(sinks.writes) == 1
    assert response.outcome.outcome == "cancelled"
    assert client.identity_violation is None


@pytest.mark.parametrize(
    "surface",
    (
        "create_terminal",
        "terminal_output",
        "release_terminal",
        "wait_for_terminal_exit",
        "kill_terminal",
    ),
)
def test_matching_id_still_refuses_the_unsupported_terminal_surfaces(
    surface: str,
) -> None:
    client, _ = _client()
    with pytest.raises(PermissionError):
        _invoke(client, surface, EXPECTED)
    assert client.identity_violation is None


def test_rejected_update_advances_the_barrier_without_a_payload() -> None:
    """Shutdown must not hang, but nothing about the frame may be serviced."""
    client, sinks = _client()
    before = client.updates_completed

    with pytest.raises(SessionIdentityViolation):
        _invoke(client, "session_update", OTHER)

    assert client.updates_completed == before + 1
    assert sinks.updates == []
    assert client.callback_failure is not None
    for secret in (EXPECTED, OTHER):
        assert secret not in client.callback_failure


# ---------------------------------------------------------------------------
# elicitation — session-scoped leaves, request-scoped unsupported
# ---------------------------------------------------------------------------


def _form_session_mode(session_id: str):
    return sdk_schema.ElicitationFormSessionMode(
        sessionId=session_id,
        requestedSchema=sdk_schema.ElicitationSchema(
            type="object", properties={}, required=[]
        ),
    )


def _url_session_mode(session_id: str):
    return sdk_schema.ElicitationUrlSessionMode(
        sessionId=session_id,
        elicitationId="elicit-1",
        url="https://example.invalid/elicit",
    )


@pytest.mark.parametrize(
    "mode_factory", (_form_session_mode, _url_session_mode), ids=("form", "url")
)
def test_session_scoped_elicitation_rejects_a_wrong_leaf_session_id(
    mode_factory,
) -> None:
    client, sinks = _client()
    mode = mode_factory(OTHER)
    # The pinned SDK passes the leaf instance itself: there is no ``.root``
    # wrapper to reach through, so the accessor must be the leaf's own field.
    assert not hasattr(mode, "root")
    assert isinstance(mode, sdk_schema.ElicitationSessionScope)

    with pytest.raises(SessionIdentityViolation) as violation:
        asyncio.run(client.create_elicitation(message="m", mode=mode))

    assert not sinks.touched()
    assert client.identity_violation == SESSION_IDENTITY_VIOLATION
    assert str(violation.value) == SESSION_IDENTITY_VIOLATION


@pytest.mark.parametrize(
    "mode_factory", (_form_session_mode, _url_session_mode), ids=("form", "url")
)
def test_session_scoped_elicitation_with_a_matching_id_is_unsupported(
    mode_factory,
) -> None:
    client, _ = _client()
    with pytest.raises(PermissionError):
        asyncio.run(client.create_elicitation(message="m", mode=mode_factory(EXPECTED)))
    assert client.identity_violation is None


def _form_request_mode():
    return sdk_schema.ElicitationFormRequestMode(
        requestId="req-1",
        requestedSchema=sdk_schema.ElicitationSchema(
            type="object", properties={}, required=[]
        ),
    )


def _url_request_mode():
    return sdk_schema.ElicitationUrlRequestMode(
        requestId="req-1",
        elicitationId="elicit-1",
        url="https://example.invalid/elicit",
    )


@pytest.mark.parametrize(
    "mode_factory", (_form_request_mode, _url_request_mode), ids=("form", "url")
)
def test_request_scoped_elicitation_is_unsupported_and_invents_no_session_id(
    mode_factory,
) -> None:
    client, sinks = _client()
    mode = mode_factory()
    assert not hasattr(mode, "session_id")

    with pytest.raises(PermissionError):
        asyncio.run(client.create_elicitation(message="m", mode=mode))

    # Request scope carries no Session id, so nothing is compared, nothing is
    # invented, and no violation is recorded against the bound Session.
    assert client.identity_violation is None
    assert client.expected_session_id == EXPECTED
    assert not sinks.touched()


def test_complete_elicitation_invents_no_session_id() -> None:
    client, sinks = _client()
    with pytest.raises(PermissionError):
        asyncio.run(client.complete_elicitation(elicitation_id="elicit-1"))
    assert client.identity_violation is None
    assert client.expected_session_id == EXPECTED
    assert not sinks.touched()

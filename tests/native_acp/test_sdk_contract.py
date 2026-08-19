"""Stage-0 contract gate for the pinned official ACP Python SDK.

Pins the exact distribution version, import origin, and API surface that the
Native ACP core builds on. Symbol pins record the *actual* SDK names; where an
actual name differs from the design expectation the active plan carries the
C1 symbol drift note (`set_config_option`, `acp.interfaces.Client`).
"""

from __future__ import annotations

import asyncio
import enum
import importlib
import importlib.metadata
import inspect
import sys
import typing
from pathlib import Path

import pytest

DISTRIBUTION = "agent-client-protocol"
PINNED_VERSION = "0.12.1"
WORKTREE_ROOT = Path(__file__).resolve().parents[2]

# The SDK's own optional extras. ARS is stdio ACP only, so neither may be
# resolvable in the consuming environment: installing them would add a network
# transport surface this product does not have and does not want.
HTTP_EXTRA_DISTRIBUTIONS = ("httpx", "websockets")

# Actual entry points on the client-side connection class (design expected
# set_session_config_option; actual is set_config_option).
CONNECTION_ENTRY_POINTS = (
    "initialize",
    "new_session",
    "load_session",
    "set_config_option",
    "prompt",
    "cancel",
    "close_session",
)


def _sdk_module():
    # Raises importlib.metadata.PackageNotFoundError while the native extra is
    # absent — the Stage-0 RED for this suite.
    importlib.metadata.version(DISTRIBUTION)
    return importlib.import_module("acp")


def _field_names(model: type) -> set[str]:
    fields = getattr(model, "model_fields", None)
    if fields is not None:
        return set(fields)
    dataclass_fields = getattr(model, "__dataclass_fields__", None)
    if dataclass_fields is not None:
        return set(dataclass_fields)
    return set(inspect.signature(model.__init__).parameters) - {"self"}


def test_distribution_version_is_pinned() -> None:
    assert importlib.metadata.version(DISTRIBUTION) == PINNED_VERSION


def test_top_level_import_name_resolves_to_acp() -> None:
    _sdk_module()
    mapping = importlib.metadata.packages_distributions()
    names = sorted(
        name for name, dists in mapping.items() if DISTRIBUTION in dists
    )
    assert names == ["acp"]


def test_import_origin_is_this_worktrees_venv() -> None:
    module = _sdk_module()
    origin = Path(module.__file__).resolve()
    venv_root = (WORKTREE_ROOT / ".venv").resolve()
    assert origin.is_relative_to(venv_root), origin


def test_client_side_connection_class_and_entry_points() -> None:
    _sdk_module()
    connection_module = importlib.import_module("acp.client.connection")
    connection = connection_module.ClientSideConnection
    assert inspect.isclass(connection)
    for name in CONNECTION_ENTRY_POINTS:
        assert callable(getattr(connection, name)), name


def test_client_callback_protocol_surface() -> None:
    _sdk_module()
    interfaces = importlib.import_module("acp.interfaces")
    client = interfaces.Client
    assert inspect.isclass(client)
    for name in (
        "session_update",
        "request_permission",
        "read_text_file",
        "write_text_file",
    ):
        assert callable(getattr(client, name)), name


def test_http_and_ws_transport_extras_are_not_installed() -> None:
    """ARS remains stdio ACP only; the SDK's ``[http]`` extra stays uninstalled.

    The 0.12 line added Streamable-HTTP and WebSocket transports behind an
    optional extra. Nothing in this product may depend on them, so the
    assertion is on the *environment*: the extra's distributions must not
    resolve at all.
    """
    _sdk_module()
    for distribution in HTTP_EXTRA_DISTRIBUTIONS:
        with pytest.raises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.version(distribution)


def test_acp_import_does_not_pull_the_http_or_ws_transport_modules() -> None:
    """Importing the SDK must not import its network transports.

    ``acp.http``/``acp.ws`` guard their heavy imports, so a plain ``import acp``
    leaves them unloaded. If that ever changes, a base ARS install would start
    importing an HTTP client stack it never uses.
    """
    _sdk_module()
    assert "acp.http.client" not in sys.modules
    assert "acp.http.server" not in sys.modules
    assert "acp.ws.client" not in sys.modules


def test_connection_seams_the_causal_turn_boundary_is_built_on() -> None:
    """The SDK seams ARS's causal Turn boundary is built on, on 0.12.1.

    0.12.1's connection refactor deleted the pluggable ``queue``/``state_store``
    /``dispatcher_factory``/``sender_factory`` constructor keywords, so the
    pre-write tap can no longer be injected into the SDK-constructed
    ``MessageSender``. What remains is one step further out: the connection
    still fans raw frames out to ``observers`` (the update ordinal domain), and
    it still accepts a **message-level** ``Transport`` in place of the
    (writer, reader) pair. ARS therefore assembles the SDK's own
    ``NdjsonTransport``/``MessageSender`` around the tap and passes that
    transport in. All four pieces are pinned here; ``sender_factory`` is pinned
    *absent*, so a future re-addition is a deliberate decision rather than a
    silent second way to do this.
    """
    _sdk_module()
    connection_module = importlib.import_module("acp.connection")
    parameters = inspect.signature(connection_module.Connection.__init__).parameters
    assert "observers" in parameters
    assert parameters["observers"].kind is inspect.Parameter.KEYWORD_ONLY
    for removed in ("sender_factory", "queue", "state_store", "dispatcher_factory"):
        assert removed not in parameters, removed

    task_module = importlib.import_module("acp.task")
    sender_parameters = list(
        inspect.signature(task_module.MessageSender.__init__).parameters
    )
    assert sender_parameters[:3] == ["self", "writer", "supervisor"]
    supervisor_parameters = list(
        inspect.signature(task_module.TaskSupervisor.__init__).parameters
    )
    assert supervisor_parameters[:2] == ["self", "source"]
    for name in ("create", "shutdown"):
        assert callable(getattr(task_module.TaskSupervisor, name)), name

    transport_module = importlib.import_module("acp._transport")
    assert inspect.isclass(transport_module.NdjsonTransport)
    ndjson_parameters = list(
        inspect.signature(transport_module.NdjsonTransport.__init__).parameters
    )
    assert ndjson_parameters[:3] == ["self", "reader", "sender"]
    # The runtime-checkable protocol is what routes a transport to the
    # message-level construction form inside ``ClientSideConnection``.
    assert isinstance(
        transport_module.NdjsonTransport.__new__(transport_module.NdjsonTransport),
        transport_module.Transport,
    )


def test_client_connection_accepts_a_message_level_transport() -> None:
    """The injection point ARS's prompt pre-write tap now rides on.

    Passing a ``Transport`` as ``input_stream`` (and no ``output_stream``) must
    build a working client connection that uses that transport verbatim — and
    supplying both must still be refused.
    """
    _sdk_module()
    connection_module = importlib.import_module("acp.client.connection")
    transport_module = importlib.import_module("acp._transport")

    async def case() -> None:
        left, _right = transport_module.memory_transport_pair()
        connection = connection_module.ClientSideConnection(object(), left)
        try:
            assert connection._conn._transport is left
        finally:
            await connection.close()
        with pytest.raises(TypeError, match="asyncio StreamWriter/StreamReader"):
            connection_module.ClientSideConnection(object(), left, left)

    asyncio.run(case())


def test_prompt_awaits_started_session_updates_before_returning() -> None:
    """0.12.1 ``fix(connection): preserve notification response ordering``.

    ``ClientSideConnection.prompt`` now waits for the session's *already
    started* ``session_update`` handlers before returning. ARS keeps its own
    observed-versus-completed barrier because this one cannot see a frame whose
    handler task the receive loop created but has not stepped yet; the pin here
    is that the SDK-side wait exists and is session-scoped, so its removal is
    noticed rather than assumed.
    """
    _sdk_module()
    connection_module = importlib.import_module("acp.client.connection")
    tracker = connection_module._SessionUpdateTracker
    assert inspect.isclass(tracker)
    assert inspect.iscoroutinefunction(tracker.wait)
    assert list(inspect.signature(tracker.wait).parameters)[:2] == ["self", "session_id"]
    prompt_source = inspect.getsource(connection_module.ClientSideConnection.prompt)
    assert "self._session_updates.wait(session_id)" in prompt_source


def test_extension_elicitation_mode_exists_and_never_reaches_a_client_callback() -> None:
    """v1.19 extensible unions add a catch-all elicitation request.

    ``CreateOtherElicitationRequest`` is the extension leaf. The SDK's own
    client router refuses to build a mode for it and raises ``invalid_params``
    *before* the ARS ``create_elicitation`` callback runs, so an unknown mode
    is denied without ARS having to recognise it — and ARS must not start
    treating one as supported.
    """
    _sdk_module()
    schema = importlib.import_module("acp.schema")
    router = importlib.import_module("acp.client.router")
    exceptions = importlib.import_module("acp.exceptions")
    assert inspect.isclass(schema.CreateOtherElicitationRequest)
    request = router._validate_create_elicitation_request(
        {"message": "hi", "mode": "someFutureMode"}
    )
    assert isinstance(request, schema.CreateOtherElicitationRequest)
    with pytest.raises(exceptions.RequestError):
        router._mode_from_create_elicitation_request(request)


def test_elicitation_mode_is_a_four_leaf_union_with_session_scoped_ids() -> None:
    """Pins the accessor the callback boundary depends on (B1, reviewer note 1).

    ``ElicitationMode`` is a plain ``Union`` of four leaf types and the client
    router passes a leaf instance directly, so the Session id is read from the
    leaf's own ``session_id`` field. There is no ``root`` wrapper attribute on
    the leaves, so ``mode.root.session_id`` is not merely discouraged here — it
    does not exist.
    """
    _sdk_module()
    schema = importlib.import_module("acp.schema")
    leaves = typing.get_args(schema.ElicitationMode)
    assert set(leaves) == {
        schema.ElicitationFormSessionMode,
        schema.ElicitationFormRequestMode,
        schema.ElicitationUrlSessionMode,
        schema.ElicitationUrlRequestMode,
    }
    assert len(leaves) == 4

    for leaf in (schema.ElicitationFormSessionMode, schema.ElicitationUrlSessionMode):
        assert issubclass(leaf, schema.ElicitationSessionScope)
        assert "session_id" in _field_names(leaf)
        assert "root" not in _field_names(leaf)
    for leaf in (schema.ElicitationFormRequestMode, schema.ElicitationUrlRequestMode):
        assert issubclass(leaf, schema.ElicitationRequestScope)
        assert "session_id" not in _field_names(leaf)

    assert "session_id" in _field_names(schema.ElicitationSessionScope)
    # The load response carries no identity field at all, so the load arm has
    # nothing to read back even if it wanted to.
    assert "session_id" not in _field_names(schema.LoadSessionResponse)


def test_config_option_carriers() -> None:
    _sdk_module()
    schema = importlib.import_module("acp.schema")
    assert "config_options" in _field_names(schema.NewSessionResponse)
    assert "config_options" in _field_names(schema.SetSessionConfigOptionResponse)
    assert inspect.isclass(schema.ConfigOptionUpdate)


def test_stop_reason_vocabulary_includes_end_turn() -> None:
    _sdk_module()
    schema = importlib.import_module("acp.schema")
    stop_reason = schema.StopReason
    if isinstance(stop_reason, type) and issubclass(stop_reason, enum.Enum):
        values = {member.value for member in stop_reason}
    else:
        values = set(typing.get_args(stop_reason))
    assert "end_turn" in values, stop_reason


def test_connection_constructor_pins_asyncio_stream_io_model() -> None:
    # Pins the C3 stream surface: the SDK connection requires asyncio streams
    # (StreamWriter toward agent stdin as input_stream, StreamReader from agent
    # stdout as output_stream), not raw pipe file descriptors. The constructor
    # enforces this with a runtime isinstance gate before any wire activity.
    _sdk_module()
    connection_module = importlib.import_module("acp.client.connection")
    parameters = list(
        inspect.signature(
            connection_module.ClientSideConnection.__init__
        ).parameters
    )
    assert parameters[:4] == ["self", "to_client", "input_stream", "output_stream"]
    with pytest.raises(TypeError, match="asyncio StreamWriter/StreamReader"):
        connection_module.ClientSideConnection(lambda conn: None, object(), object())

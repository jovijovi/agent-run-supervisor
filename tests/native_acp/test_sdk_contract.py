"""Stage-0 contract gate for the pinned official ACP Python SDK.

Pins the exact distribution version, import origin, and API surface that the
Native ACP core builds on. Symbol pins record the *actual* SDK names; where an
actual name differs from the design expectation the active plan carries the
C1 symbol drift note (`set_config_option`, `acp.interfaces.Client`).
"""

from __future__ import annotations

import enum
import importlib
import importlib.metadata
import inspect
import sys
import typing
from pathlib import Path

import pytest

DISTRIBUTION = "agent-client-protocol"
PINNED_VERSION = "0.12.0"
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

    0.12.0 added Streamable-HTTP and WebSocket transports behind an optional
    extra. Nothing in this product may depend on them, so the assertion is on
    the *environment*: the extra's distributions must not resolve at all.
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


def test_stdio_connection_still_accepts_the_sender_factory_and_observer_seams() -> None:
    """The two SDK seams ARS's causal Turn boundary is built on.

    0.12.0 moved the byte framing behind a message-level ``Transport``. The
    stdio path must still construct a ``MessageSender`` through the injectable
    ``sender_factory`` (the pre-write tap that pins the prompt boundary) and
    still fan raw frames out to ``observers`` (the update ordinal domain).
    """
    _sdk_module()
    connection_module = importlib.import_module("acp.connection")
    parameters = inspect.signature(connection_module.Connection.__init__).parameters
    for name in ("sender_factory", "observers"):
        assert name in parameters, name
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    sender_module = importlib.import_module("acp.task.sender")
    sender_parameters = list(
        inspect.signature(sender_module.MessageSender.__init__).parameters
    )
    assert sender_parameters[:3] == ["self", "writer", "supervisor"]
    transport_module = importlib.import_module("acp._transport")
    assert inspect.isclass(transport_module.NdjsonTransport)


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

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
import typing
from pathlib import Path

import pytest

DISTRIBUTION = "agent-client-protocol"
PINNED_VERSION = "0.11.0"
WORKTREE_ROOT = Path(__file__).resolve().parents[2]

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

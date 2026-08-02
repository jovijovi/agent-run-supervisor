"""B5 / A15 — the exact eight-operation ``api_version`` drain matrix.

The wire moves to ``api_version`` 2 because ``submit`` genuinely changed: the
request drops ``profile_id`` and requires ``agent_id``. Everything else on the
wire is unchanged, so refusing a v1 caller outright would break seven operations
for no reason.

The version check therefore moves **off the envelope decoder** and onto
per-operation dispatch: ``submit`` is refused at v1 with
``UNSUPPORTED_API_VERSION``, and the other seven are accepted, so an existing
caller can still observe, page, cancel, and close its in-flight work while it
migrates. The separate shutdown drain is a different mechanism entirely and
still refuses **every** frame, ``server_info`` included, with ``SHUTTING_DOWN``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import protocol

SOURCE = Path(protocol.__file__)

ALL_OPERATIONS = (
    "server_info",
    "submit",
    "run_status",
    "run_events",
    "run_cancel",
    "session_status",
    "session_list",
    "session_close",
)
# The one operation whose payload shape moved, and therefore the only one a v1
# caller may not use.
V2_ONLY_OPERATIONS = ("submit",)
V1_ACCEPTED_OPERATIONS = tuple(op for op in ALL_OPERATIONS if op not in V2_ONLY_OPERATIONS)


def envelope(op: str, *, api_version: int, payload=None) -> dict:
    return {
        "api_version": api_version,
        "op": op,
        "request_id": "req-1",
        "payload": {} if payload is None else payload,
    }


# -- the version constants ---------------------------------------------------


def test_api_version_is_two_and_one_stays_supported_during_the_drain():
    assert protocol.ARSD_API_VERSION == 2
    assert protocol.SUPPORTED_API_VERSIONS == (1, 2)


def test_operation_set_is_exactly_eight():
    assert protocol.OPERATIONS_V1 == frozenset(ALL_OPERATIONS)
    assert len(ALL_OPERATIONS) == 8


def test_v2_only_operations_are_declared_once_and_named():
    assert protocol.V2_ONLY_OPERATIONS == frozenset(V2_ONLY_OPERATIONS)


# -- the matrix --------------------------------------------------------------


@pytest.mark.parametrize("op", ALL_OPERATIONS)
def test_every_operation_is_accepted_at_v2(op):
    parsed = protocol.parse_request(envelope(op, api_version=2))
    assert parsed.op == op
    assert parsed.api_version == 2


@pytest.mark.parametrize("op", V1_ACCEPTED_OPERATIONS)
def test_seven_operations_are_accepted_at_v1(op):
    parsed = protocol.parse_request(envelope(op, api_version=1))
    assert parsed.op == op
    assert parsed.api_version == 1


@pytest.mark.parametrize("op", V2_ONLY_OPERATIONS)
def test_submit_is_refused_at_v1(op):
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.parse_request(envelope(op, api_version=1))
    assert excinfo.value.code == protocol.UNSUPPORTED_API_VERSION


@pytest.mark.parametrize("version", [0, 3, -1, True, "2", 2.0, None])
def test_an_unsupported_version_is_refused_for_every_operation(version):
    for op in ALL_OPERATIONS:
        with pytest.raises(protocol.ProtocolError) as excinfo:
            protocol.parse_request(envelope(op, api_version=version))
        assert excinfo.value.code == protocol.UNSUPPORTED_API_VERSION


def test_an_unknown_op_is_still_unknown_at_both_versions():
    for version in (1, 2):
        with pytest.raises(protocol.ProtocolError) as excinfo:
            protocol.parse_request(envelope("teleport", api_version=version))
        assert excinfo.value.code == protocol.UNKNOWN_OP


# -- reviewer note 7: the check is not on the envelope decoder ---------------


def test_version_check_is_per_operation_not_on_the_envelope():
    """Structural, not behavioural: the op must be known before the version is judged.

    If the envelope refused v1 outright, no per-operation rule could ever admit
    the seven, and the drain window would not exist.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "parse_request"
    )
    op_assigned_at = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == "op" and isinstance(node.ctx, ast.Store)
    )
    per_op_rule_at = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == "V2_ONLY_OPERATIONS"
    ]
    assert per_op_rule_at, "the per-operation version rule left parse_request"
    assert min(per_op_rule_at) > op_assigned_at, (
        "the version verdict must come after the operation is known, or the "
        "drain window cannot exist"
    )


def test_decode_frame_never_judges_the_version():
    """``decode_frame`` is framing only; a v1 frame decodes at both versions."""
    for version in (1, 2, 99):
        payload = protocol.decode_frame(protocol.encode_frame(envelope("submit", api_version=version)))
        assert payload["api_version"] == version


# -- the request shape moved -------------------------------------------------


def test_submit_request_requires_agent_id_and_has_no_profile_id():
    fields = protocol._REQUEST_FIELD_NAMES
    assert "agent_id" in fields
    assert "profile_id" not in fields
    assert "agent_id" in protocol._REQUIRED_REQUEST_FIELDS


def test_a_request_naming_a_profile_is_refused_as_an_unknown_field():
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.parse_submit(
            {
                "request": {"profile_id": "standard-native-acp-v1"},
                "prompt_text": "hi",
                "workspace_root": "/tmp",
            }
        )
    assert excinfo.value.code == protocol.INVALID_REQUEST


@pytest.mark.parametrize(
    "field",
    [
        "command",
        "argv",
        "env",
        "executable",
        "runtime_path",
        "binding_generation_id",
        "transport",
        "endpoint",
        "secret",
    ],
)
def test_no_runtime_selection_field_exists_on_the_wire(field):
    """A5: the caller supplies no command, argv, env, path, digest, or secret."""
    assert field not in protocol._REQUEST_FIELD_NAMES

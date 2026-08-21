"""D1 — single-version ``api_version`` admission on the no-close wire.

The wire is ``api_version`` 3 and nothing else. v3 is an unambiguous contract
marker for the Session no-close model: the request drops ``session_reuse`` and
``ars_session_id`` for one optional ``session_id``, and ``session_close`` leaves
the operation set.

There is deliberately **no drain window and no per-operation version matrix**.
The eight-operation matrix that served the v1→v2 move existed to let a real
caller population migrate; no such population exists, so keeping a matrix would
be keeping a mechanism for nobody. The verdict therefore moves back **onto the
envelope**: one version, judged once, for every operation including
``server_info``. The separate shutdown drain is a different mechanism entirely
and still refuses **every** frame, ``server_info`` included, with
``SHUTTING_DOWN``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import protocol

SOURCE = Path(protocol.__file__)

#: The seven operations the no-close wire shipped with, in their original order.
SESSION_NOCLOSE_OPERATIONS = (
    "server_info",
    "submit",
    "run_status",
    "run_events",
    "run_cancel",
    "session_status",
    "session_list",
)

#: Plus the one additive read-only roster query. Additive, so still ``api_version`` 3.
ALL_OPERATIONS = SESSION_NOCLOSE_OPERATIONS + ("agent_list",)


def envelope(op: str, *, api_version: int, payload=None) -> dict:
    return {
        "api_version": api_version,
        "op": op,
        "request_id": "req-1",
        "payload": {} if payload is None else payload,
    }


# -- the version constants ---------------------------------------------------


def test_api_version_is_three_and_it_is_the_only_supported_one():
    assert protocol.ARSD_API_VERSION == 3
    assert protocol.SUPPORTED_API_VERSIONS == (3,)


def test_operation_set_is_exactly_eight_and_excludes_session_close():
    assert protocol.OPERATIONS == frozenset(ALL_OPERATIONS)
    assert len(ALL_OPERATIONS) == 8
    assert "session_close" not in protocol.OPERATIONS


def test_the_eighth_operation_is_additive_and_costs_no_version():
    """``agent_list`` joins the set; nothing about the other seven moves.

    An additive read-only operation is exactly the change a version bump is
    *not* for: an old caller never sends it, and a new caller sending it to a
    daemon that predates it gets ``UNKNOWN_OP`` — the existing refusal, not a
    version negotiation and not a feature-specific code.
    """
    assert frozenset(SESSION_NOCLOSE_OPERATIONS) < protocol.OPERATIONS
    assert protocol.OPERATIONS - frozenset(SESSION_NOCLOSE_OPERATIONS) == {"agent_list"}
    assert protocol.SUPPORTED_API_VERSIONS == (3,)


def test_no_per_operation_version_matrix_constant_survives():
    """The drain matrix is deleted, not disabled: no constant, no flag, no set."""
    for retired in (
        "V2_ONLY_OPERATIONS",
        "MIN_API_VERSION_FOR_V2_ONLY",
        "OPERATIONS_V1",
        "ERROR_CODES_V1",
    ):
        assert not hasattr(protocol, retired), retired


# -- single-version admission -------------------------------------------------


@pytest.mark.parametrize("op", ALL_OPERATIONS)
def test_every_operation_is_accepted_at_v3(op):
    parsed = protocol.parse_request(envelope(op, api_version=3))
    assert parsed.op == op
    assert parsed.api_version == 3


@pytest.mark.parametrize("version", [0, 1, 2, 4, -1, True, "3", 3.0, None])
def test_every_other_version_is_refused_for_every_operation(version):
    for op in ALL_OPERATIONS:
        with pytest.raises(protocol.ProtocolError) as excinfo:
            protocol.parse_request(envelope(op, api_version=version))
        assert excinfo.value.code == protocol.UNSUPPORTED_API_VERSION


def test_session_close_is_refused_as_an_unknown_op_not_as_a_version_problem():
    """The operation is gone, so it is unknown — never "wrong version"."""
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.parse_request(envelope("session_close", api_version=3))
    assert excinfo.value.code == protocol.UNKNOWN_OP


def test_an_unknown_op_is_still_unknown():
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.parse_request(envelope("teleport", api_version=3))
    assert excinfo.value.code == protocol.UNKNOWN_OP


# -- the verdict is back on the envelope -------------------------------------


def test_version_is_judged_on_the_envelope_before_the_operation_is_known():
    """Structural, not behavioural.

    With one served version there is nothing an operation could add to the
    verdict, so judging it before the op is resolved is both cheaper and
    impossible to make per-operation by accident.
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
    version_rule_at = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == "SUPPORTED_API_VERSIONS"
    )
    assert version_rule_at < op_assigned_at, (
        "with a single served version the envelope decides; a per-operation "
        "rule would be a drain window with no caller to drain"
    )


def test_decode_frame_never_judges_the_version():
    """``decode_frame`` is framing only; any version decodes."""
    for version in (1, 2, 3, 99):
        payload = protocol.decode_frame(
            protocol.encode_frame(envelope("submit", api_version=version))
        )
        assert payload["api_version"] == version


# -- the request shape -------------------------------------------------------


def test_submit_request_requires_agent_id_and_has_no_profile_id():
    fields = protocol._REQUEST_FIELD_NAMES
    assert "agent_id" in fields
    assert "profile_id" not in fields
    assert "agent_id" in protocol._REQUIRED_REQUEST_FIELDS


def test_the_session_field_is_exactly_one_optional_session_id():
    fields = protocol._REQUEST_FIELD_NAMES
    assert "session_id" in fields
    assert "session_reuse" not in fields
    assert "ars_session_id" not in fields
    assert "session_id" not in protocol._REQUIRED_REQUEST_FIELDS


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

"""Versioned bounded UDS frame protocol (api_version 3).

Pure protocol-layer contracts: NDJSON framing and byte caps, the closed
envelope/operation/error sets, request_id format and correlation, and
the strict submit wire mapping onto the untouched ``native_acp.spec``
dataclasses. No sockets, no server, no I/O.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import traceback

import pytest

from agent_run_supervisor.arsd import protocol
from agent_run_supervisor.native_acp.spec import (
    AgentRunRequest,
    InputRef,
    NativeSpecError,
    RunLimits,
)

_ABSENT = object()

OPS = (
    "server_info",
    "submit",
    "run_status",
    "run_events",
    "run_cancel",
    "session_status",
    "session_list",
)

ERROR_CODES = {
    "UNSUPPORTED_API_VERSION",
    "UNKNOWN_OP",
    "MALFORMED_FRAME",
    "FRAME_TOO_LARGE",
    "INVALID_REQUEST",
    "UNAUTHENTICATED_PEER",
    "PEER_UID_DENIED",
    "OWNER_MISMATCH",
    "IDEMPOTENCY_CONFLICT",
    "SUBMISSION_INDETERMINATE",
    "UNKNOWN_RUN",
    "UNKNOWN_SESSION",
    "SESSION_BUSY",
    "CAPACITY_EXHAUSTED",
    "EVENT_BACKLOG_EXCEEDED",
    "SHUTTING_DOWN",
    "INTERNAL",
}


def _reject(code, fn, *args):
    with pytest.raises(protocol.ProtocolError) as err:
        fn(*args)
    assert err.value.code == code
    return err.value


def envelope(**overrides):
    # The current wire version. The v1/v2 drain split — ``submit`` refused at
    # v1 while the other seven are accepted — is owned by
    # ``test_api_version_matrix.py``, so this module never has to encode it.
    frame = {
        "api_version": protocol.ARSD_API_VERSION,
        "op": "server_info",
        "request_id": "req-1",
    }
    frame.update(overrides)
    return {key: value for key, value in frame.items() if value is not _ABSENT}


def valid_wire_request() -> dict:
    return {
        "owner": "hermes",
        "namespace": "hermes/doc-check",
        "agent_id": "fake-agent",
        "session_id": "sess-arsd-1",
        "expected_binding_hash": None,
        "input_refs": [
            {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64},
        ],
        "requested_model": "kimi-for-coding/k3",
        "requested_effort": "max",
        "grant_ref": "grant:doc-check-1",
        "grant_hash": "sha256:" + "b" * 64,
        "grant_role_hash": "sha256:" + "c" * 64,
        "grant_capabilities": ["read"],
        "mcp_snapshot_hashes": [],
        "credential_refs": [],
        "limits": {},
        "evidence_policy_hash": "sha256:" + "d" * 64,
        "recovery_policy_hash": "sha256:" + "e" * 64,
    }


def expected_request() -> AgentRunRequest:
    return AgentRunRequest(
        owner="hermes",
        namespace="hermes/doc-check",
        agent_id="fake-agent",
        session_id="sess-arsd-1",
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),),
        requested_model="kimi-for-coding/k3",
        requested_effort="max",
        grant_ref="grant:doc-check-1",
        grant_hash="sha256:" + "b" * 64,
        grant_role_hash="sha256:" + "c" * 64,
        grant_capabilities=("read",),
        mcp_snapshot_hashes=(),
        credential_refs=(),
        limits=RunLimits(),
        evidence_policy_hash="sha256:" + "d" * 64,
        recovery_policy_hash="sha256:" + "e" * 64,
    )


def valid_submit_payload() -> dict:
    return {
        "request": valid_wire_request(),
        "prompt_text": "run the doc check",
        "workspace_root": "/tmp/ws",
        "cwd": None,
        "retry_of_run_id": None,
    }


# --- closed sets and caps -------------------------------------------------


def test_api_version_constants() -> None:
    assert protocol.ARSD_API_VERSION == 3
    assert protocol.SUPPORTED_API_VERSIONS == (3,)


def test_operation_set_is_closed() -> None:
    assert protocol.OPERATIONS == frozenset(OPS)


def test_error_code_set_is_closed() -> None:
    assert protocol.ERROR_CODES == frozenset(ERROR_CODES)


def test_byte_caps_are_coherent() -> None:
    assert 0 < protocol.MAX_PROMPT_BYTES < protocol.MAX_FRAME_BYTES
    assert protocol.MAX_REQUEST_ID_CHARS >= 1
    assert protocol.MAX_ERROR_MESSAGE_CHARS >= 1


# --- framing --------------------------------------------------------------


def test_encode_frame_is_canonical_newline_terminated() -> None:
    assert protocol.encode_frame({"b": 1, "a": 2}) == b'{"a":2,"b":1}\n'


def test_frame_round_trip_preserves_payload() -> None:
    payload = {
        "op": "server_info",
        "payload": {"nested": {"unicode": "π ≠ ascii", "n": 3.5}},
        "list": [1, "two", None, True],
    }
    encoded = protocol.encode_frame(payload)
    assert encoded.endswith(b"\n")
    assert encoded.count(b"\n") == 1
    assert protocol.decode_frame(encoded) == payload


def test_decode_frame_accepts_missing_trailing_newline() -> None:
    encoded = protocol.encode_frame({"a": 1})
    assert protocol.decode_frame(encoded) == protocol.decode_frame(encoded[:-1])


@pytest.mark.parametrize("payload", [[1, 2], "text", 42, None])
def test_encode_frame_rejects_non_object_payload(payload) -> None:
    _reject("MALFORMED_FRAME", protocol.encode_frame, payload)


def test_frame_size_boundary_both_directions() -> None:
    overhead = len(protocol.encode_frame({"k": ""}))  # {"k":""}\n
    exact = {"k": "a" * (protocol.MAX_FRAME_BYTES - overhead)}
    assert len(protocol.encode_frame(exact)) == protocol.MAX_FRAME_BYTES

    oversize = {"k": "a" * (protocol.MAX_FRAME_BYTES - overhead + 1)}
    _reject("FRAME_TOO_LARGE", protocol.encode_frame, oversize)

    inbound = b'{"k":"' + b"a" * protocol.MAX_FRAME_BYTES + b'"}'
    _reject("FRAME_TOO_LARGE", protocol.decode_frame, inbound)


@pytest.mark.parametrize(
    "data",
    [
        b"not json\n",
        b"[1,2]\n",
        b'"text"\n',
        b"42\n",
        b"null\n",
        b"",
        b"\xff\xfe{}",
        b'{"a":1}\n{"b":2}\n',
    ],
)
def test_decode_frame_malformed(data: bytes) -> None:
    _reject("MALFORMED_FRAME", protocol.decode_frame, data)


# --- request envelope -----------------------------------------------------


@pytest.mark.parametrize("op", OPS)
def test_parse_request_accepts_every_v1_op(op: str) -> None:
    parsed = protocol.parse_request(envelope(op=op))
    assert parsed.op == op
    assert parsed.request_id == "req-1"
    assert parsed.payload == {}


def test_parse_request_echoes_request_id_and_payload() -> None:
    parsed = protocol.parse_request(
        envelope(request_id="Caller.Req_01-a", payload={"a": 1})
    )
    assert parsed.request_id == "Caller.Req_01-a"
    assert parsed.payload == {"a": 1}


@pytest.mark.parametrize("version", [_ABSENT, 0, 1, 2, 4, -1, "3", None, True])
def test_missing_or_unknown_api_version_reports_supported_list(version) -> None:
    err = _reject(
        "UNSUPPORTED_API_VERSION",
        protocol.parse_request,
        envelope(api_version=version),
    )
    # The refusal names the served set, so a caller learns what to send.
    assert "3" in err.message


def test_api_version_error_takes_precedence_over_unknown_op() -> None:
    _reject(
        "UNSUPPORTED_API_VERSION",
        protocol.parse_request,
        envelope(api_version=99, op="bogus"),
    )


def test_request_id_boundary_accepts() -> None:
    for request_id in ("a", "A9._-z", "r" * protocol.MAX_REQUEST_ID_CHARS):
        parsed = protocol.parse_request(envelope(request_id=request_id))
        assert parsed.request_id == request_id


@pytest.mark.parametrize(
    "request_id",
    [
        _ABSENT,
        "",
        "r" * 1000,  # placeholder length; real cap asserted below
        "has space",
        "café",
        "a/b",
        "a:b",
        7,
        None,
        ["x"],
    ],
)
def test_request_id_rejects_closed_format_violations(request_id) -> None:
    if isinstance(request_id, str) and request_id == "r" * 1000:
        request_id = "r" * (protocol.MAX_REQUEST_ID_CHARS + 1)
    _reject("INVALID_REQUEST", protocol.parse_request, envelope(request_id=request_id))


def test_request_id_error_takes_precedence_over_unknown_op() -> None:
    _reject(
        "INVALID_REQUEST",
        protocol.parse_request,
        envelope(request_id="", op="bogus"),
    )


def test_unknown_envelope_field_rejected_never_ignored() -> None:
    _reject("INVALID_REQUEST", protocol.parse_request, envelope(extra=1))
    _reject("INVALID_REQUEST", protocol.parse_request, envelope(extra=1, op="bogus"))


@pytest.mark.parametrize("op", ["bogus", "", _ABSENT, 3])
def test_unknown_op_rejected(op) -> None:
    _reject("UNKNOWN_OP", protocol.parse_request, envelope(op=op))


@pytest.mark.parametrize("payload", [[1], "x", 3, True, None])
def test_payload_must_be_object(payload) -> None:
    _reject("INVALID_REQUEST", protocol.parse_request, envelope(payload=payload))


# --- response builders ----------------------------------------------------


def test_build_result_echoes_request_id_exact_shape() -> None:
    frame = protocol.build_result("req-9", {"pong": True})
    assert frame == {"request_id": "req-9", "result": {"pong": True}}


def test_build_error_shape_and_null_request_id() -> None:
    frame = protocol.build_error(None, "MALFORMED_FRAME", "bad line")
    assert set(frame) == {"request_id", "error"}
    assert frame["request_id"] is None
    assert set(frame["error"]) == {"code", "message"}
    assert frame["error"]["code"] == "MALFORMED_FRAME"

    echoed = protocol.build_error("req-2", "SHUTTING_DOWN", "bye")
    assert echoed["request_id"] == "req-2"


def test_build_error_rejects_unknown_code() -> None:
    with pytest.raises(ValueError):
        protocol.build_error("req-1", "NOT_A_CODE", "nope")


def test_build_error_bounds_message_length() -> None:
    frame = protocol.build_error("req-1", "INTERNAL", "x" * 100_000)
    assert len(frame["error"]["message"]) <= protocol.MAX_ERROR_MESSAGE_CHARS


def test_error_frame_encodes_and_round_trips() -> None:
    frame = protocol.build_error("req-1", "UNKNOWN_RUN", "no such run")
    assert protocol.decode_frame(protocol.encode_frame(frame)) == frame


# --- submit wire mapping --------------------------------------------------


def test_parse_submit_maps_field_for_field() -> None:
    custom_limits = {
        "startup_timeout_seconds": 5.0,
        "turn_timeout_seconds": 120.0,
        "cancel_grace_seconds": 3.0,
        "max_stderr_bytes": 1024,
        "max_event_bytes": 2048,
        "max_events": 50,
    }
    payload = valid_submit_payload()
    payload["request"].update(
        input_refs=[
            {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64},
            {"ref": "file:notes.md", "content_hash": "sha256:" + "f" * 64},
        ],
        mcp_snapshot_hashes=["sha256:" + "1" * 64],
        credential_refs=["slot-a"],
        limits=dict(custom_limits),
        schema_version=3,
    )
    payload.update(cwd="/tmp/ws/sub", retry_of_run_id="run-prior")

    command = protocol.parse_submit(payload)

    assert command.request == dataclasses.replace(
        expected_request(),
        input_refs=(
            InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),
            InputRef(ref="file:notes.md", content_hash="sha256:" + "f" * 64),
        ),
        mcp_snapshot_hashes=("sha256:" + "1" * 64,),
        credential_refs=("slot-a",),
        limits=RunLimits(**custom_limits),
    )
    assert command.prompt_text == "run the doc check"
    assert command.workspace_root == "/tmp/ws"
    assert command.cwd == "/tmp/ws/sub"
    assert command.retry_of_run_id == "run-prior"


def test_parse_submit_minimal_defaults() -> None:
    payload = valid_submit_payload()
    del payload["cwd"]
    del payload["retry_of_run_id"]

    command = protocol.parse_submit(payload)

    assert command.request == expected_request()
    assert command.request.limits == RunLimits()
    assert command.request.schema_version == 3
    assert command.cwd is None
    assert command.retry_of_run_id is None


def test_parse_submit_empty_limits_use_the_six_hour_turn_default() -> None:
    payload = valid_submit_payload()
    payload["request"]["limits"] = {}

    command = protocol.parse_submit(payload)

    assert command.request.limits.turn_timeout_seconds == 21_600.0


def test_parse_submit_accepts_the_seven_day_turn_timeout_maximum() -> None:
    payload = valid_submit_payload()
    payload["request"]["limits"] = {"turn_timeout_seconds": 604_800.0}

    command = protocol.parse_submit(payload)

    assert command.request.limits.turn_timeout_seconds == 604_800.0


def test_parse_submit_rejects_a_turn_timeout_above_seven_days() -> None:
    payload = valid_submit_payload()
    payload["request"]["limits"] = {"turn_timeout_seconds": 604_800.1}

    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


def test_parse_submit_requires_every_non_defaulted_request_field() -> None:
    required = [
        field.name
        for field in dataclasses.fields(AgentRunRequest)
        if field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING
    ]
    assert "owner" in required and "limits" in required
    for name in required:
        payload = valid_submit_payload()
        del payload["request"][name]
        _reject("INVALID_REQUEST", protocol.parse_submit, payload)


@pytest.mark.parametrize("version", ["1", True, 1.5, None])
def test_parse_submit_schema_version_must_be_integer(version) -> None:
    payload = valid_submit_payload()
    payload["request"]["schema_version"] = version
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


def test_r4_b4_parse_submit_rejects_a_foreign_schema_version_before_admission() -> None:
    """A pre-reset frame is refused rather than reinterpreted under a shape it
    was never sealed against."""
    payload = valid_submit_payload()
    payload["request"]["schema_version"] = 1
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


def test_r4_b4_parse_submit_accepts_a_missing_schema_version_as_the_current_one() -> None:
    payload = valid_submit_payload()
    payload["request"].pop("schema_version", None)
    command = protocol.parse_submit(payload)
    assert command.request.schema_version == 3


@pytest.mark.parametrize("key", ["argv", "env", "executable", "config_json"])
def test_parse_submit_rejects_launch_surfaces_at_payload_level(key: str) -> None:
    payload = valid_submit_payload()
    payload[key] = "anything"
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


@pytest.mark.parametrize("key", ["argv", "extra"])
def test_parse_submit_rejects_unknown_request_fields(key: str) -> None:
    payload = valid_submit_payload()
    payload["request"][key] = "anything"
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


def test_parse_submit_rejects_unknown_nested_fields() -> None:
    payload = valid_submit_payload()
    payload["request"]["limits"] = {"cpu_seconds": 1}
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)

    payload = valid_submit_payload()
    payload["request"]["input_refs"] = [
        {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64, "path": "/x"}
    ]
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request", ["not", "object"]),
        ("request", "text"),
        ("input_refs", "not-a-list"),
        ("input_refs", ["not-an-object"]),
        ("input_refs", [{"ref": "prompt:inline"}]),
        ("limits", []),
        ("limits", "big"),
        ("grant_capabilities", "read"),
        ("credential_refs", [1]),
        ("mcp_snapshot_hashes", [None]),
        ("expected_binding_hash", 7),
        ("session_id", 7),
    ],
)
def test_parse_submit_rejects_bad_shapes(field: str, value) -> None:
    payload = valid_submit_payload()
    if field == "request":
        payload["request"] = value
    else:
        payload["request"][field] = value
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


@pytest.mark.parametrize(
    "limits",
    [
        {"startup_timeout_seconds": True},
        {"startup_timeout_seconds": "60"},
        {"max_events": 50.5},
    ],
)
def test_parse_submit_limit_numbers_strictly_typed(limits: dict) -> None:
    payload = valid_submit_payload()
    payload["request"]["limits"] = limits
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


def test_parse_submit_prompt_bounded_by_bytes() -> None:
    payload = valid_submit_payload()
    payload["prompt_text"] = "a" * protocol.MAX_PROMPT_BYTES
    assert protocol.parse_submit(payload).prompt_text == payload["prompt_text"]

    payload["prompt_text"] = "a" * (protocol.MAX_PROMPT_BYTES + 1)
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)

    multibyte = "é" * (protocol.MAX_PROMPT_BYTES // 2 + 1)
    assert len(multibyte) <= protocol.MAX_PROMPT_BYTES  # chars fit; bytes do not
    payload["prompt_text"] = multibyte
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


@pytest.mark.parametrize("prompt", [_ABSENT, None, 7, ["x"]])
def test_parse_submit_prompt_must_be_string(prompt) -> None:
    payload = valid_submit_payload()
    if prompt is _ABSENT:
        del payload["prompt_text"]
    else:
        payload["prompt_text"] = prompt
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workspace_root", _ABSENT),
        ("workspace_root", ""),
        ("workspace_root", 7),
        ("cwd", ""),
        ("cwd", 7),
        ("retry_of_run_id", ""),
        ("retry_of_run_id", 7),
    ],
)
def test_parse_submit_path_and_link_fields_validated(field: str, value) -> None:
    payload = valid_submit_payload()
    if value is _ABSENT:
        del payload[field]
    else:
        payload[field] = value
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request.update(session_reuse="sometimes"),
        lambda request: request.update(limits={"startup_timeout_seconds": -1}),
        lambda request: request.update(grant_capabilities=["not-a-permission"]),
        lambda request: request.update(owner=""),
    ],
)
def test_native_spec_error_surfaces_as_invalid_request(mutate) -> None:
    payload = valid_submit_payload()
    mutate(payload["request"])
    err = _reject("INVALID_REQUEST", protocol.parse_submit, payload)
    assert not isinstance(err, NativeSpecError)


# --- stable errors never echo caller text ---------------------------------

SECRET_SENTINEL = "sk-live-" + "LEAKCANARY"  # concatenated so no scannable literal


def _assert_never_leaks(err: protocol.ProtocolError) -> None:
    assert SECRET_SENTINEL not in err.message
    assert SECRET_SENTINEL not in str(err)
    frame = protocol.build_error("req-1", err.code, err.message)
    assert SECRET_SENTINEL.encode("utf-8") not in protocol.encode_frame(frame)


@pytest.mark.parametrize(
    "place", ["envelope", "submit", "request", "limits", "input_ref_item"]
)
def test_unknown_field_errors_never_echo_caller_key_names(place: str) -> None:
    if place == "envelope":
        frame = envelope()
        frame[SECRET_SENTINEL] = 1
        err = _reject("INVALID_REQUEST", protocol.parse_request, frame)
    else:
        payload = valid_submit_payload()
        if place == "submit":
            payload[SECRET_SENTINEL] = "x"
        elif place == "request":
            payload["request"][SECRET_SENTINEL] = "x"
        elif place == "limits":
            payload["request"]["limits"] = {SECRET_SENTINEL: 1}
        else:
            payload["request"]["input_refs"] = [
                {
                    "ref": "prompt:inline",
                    "content_hash": "sha256:" + "a" * 64,
                    SECRET_SENTINEL: "x",
                }
            ]
        err = _reject("INVALID_REQUEST", protocol.parse_submit, payload)
    _assert_never_leaks(err)


def test_native_spec_error_mapping_never_echoes_caller_values() -> None:
    payload = valid_submit_payload()
    payload["request"]["grant_capabilities"] = [SECRET_SENTINEL]
    err = _reject("INVALID_REQUEST", protocol.parse_submit, payload)
    _assert_never_leaks(err)
    # A server logger rendering exc_info must not see the validator text either:
    # the raw NativeSpecError may not survive as an exposed cause chain.
    assert err.__cause__ is None
    rendered = "".join(traceback.format_exception(err))
    assert SECRET_SENTINEL not in rendered


def test_correlated_request_id_helper_matches_parse_request_contract() -> None:
    assert protocol.correlated_request_id({"request_id": "ok-1"}) == "ok-1"
    assert protocol.correlated_request_id({"request_id": "r" * protocol.MAX_REQUEST_ID_CHARS}) == (
        "r" * protocol.MAX_REQUEST_ID_CHARS
    )
    for bad in (None, "", "has space", "café", 7, True, ["x"], "r" * (protocol.MAX_REQUEST_ID_CHARS + 1)):
        assert protocol.correlated_request_id({"request_id": bad}) is None
    assert protocol.correlated_request_id("not-a-mapping") is None  # type: ignore[arg-type]
    assert protocol.correlated_request_id({}) is None


def test_parse_request_errors_preserve_valid_request_id_for_correlation() -> None:
    # Helper + parse_request share one validator: valid id survives typed errors.
    unknown = envelope(op="bogus", request_id="corr-unknown")
    assert protocol.correlated_request_id(unknown) == "corr-unknown"
    err = _reject("UNKNOWN_OP", protocol.parse_request, unknown)
    assert err.message  # bounded/sanitized
    assert SECRET_SENTINEL not in err.message

    unsupported = envelope(api_version=99, request_id="corr-version")
    assert protocol.correlated_request_id(unsupported) == "corr-version"
    _reject("UNSUPPORTED_API_VERSION", protocol.parse_request, unsupported)

    invalid_payload = envelope(payload=["nope"], request_id="corr-payload")
    assert protocol.correlated_request_id(invalid_payload) == "corr-payload"
    _reject("INVALID_REQUEST", protocol.parse_request, invalid_payload)

    # Malformed / invalid request_id never correlates.
    assert protocol.correlated_request_id(envelope(request_id="bad id", op="bogus")) is None
    assert protocol.correlated_request_id(envelope(request_id=None, op="bogus")) is None


def test_r6_b3_decode_frame_over_digit_integer_is_malformed() -> None:
    # CPython raises ValueError when integer digit limit is exceeded.
    data = b'{"n":' + b"1" + b"0" * 10000 + b"}\n"
    assert len(data) <= protocol.MAX_FRAME_BYTES
    err = _reject("MALFORMED_FRAME", protocol.decode_frame, data)
    assert "10000" not in err.message
    assert "1" + "0" * 20 not in err.message


def test_r6_b3_decode_frame_deep_nesting_is_malformed() -> None:
    depth = 5000
    data = b"{" + b'"a":{' * depth + b'"x":1' + b"}" * depth + b"}\n"
    # Keep under frame cap so nesting, not size, is the failure.
    if len(data) > protocol.MAX_FRAME_BYTES:
        depth = 2000
        data = b"{" + b'"a":{' * depth + b'"x":1' + b"}" * depth + b"}\n"
    assert len(data) <= protocol.MAX_FRAME_BYTES
    err = _reject("MALFORMED_FRAME", protocol.decode_frame, data)
    assert "Recursion" not in err.message


def test_r6_b3_frame_nesting_boundary_accepts_bound_rejects_over() -> None:
    at_bound: dict = {"a": 1}
    for _ in range(protocol.MAX_JSON_NESTING_DEPTH - 1):
        at_bound = {"a": at_bound}
    assert protocol.decode_frame(protocol.encode_frame(at_bound)) == at_bound

    over = {"a": at_bound}
    err = _reject("MALFORMED_FRAME", protocol.encode_frame, over)
    assert "Recursion" not in err.message

    raw_over = (
        b"{"
        + b'"a":{' * protocol.MAX_JSON_NESTING_DEPTH
        + b'"x":1'
        + b"}" * protocol.MAX_JSON_NESTING_DEPTH
        + b"}\n"
    )
    err = _reject("MALFORMED_FRAME", protocol.decode_frame, raw_over)
    assert "Recursion" not in err.message


def test_r6_b3_frame_nesting_bound_covers_arrays() -> None:
    deep: object = 1
    for _ in range(protocol.MAX_JSON_NESTING_DEPTH - 1):
        deep = [deep]
    at_bound = {"a": deep}
    assert protocol.decode_frame(protocol.encode_frame(at_bound)) == at_bound
    _reject("MALFORMED_FRAME", protocol.encode_frame, {"a": [deep]})

    raw_over = (
        b'{"a":'
        + b"[" * protocol.MAX_JSON_NESTING_DEPTH
        + b"1"
        + b"]" * protocol.MAX_JSON_NESTING_DEPTH
        + b"}\n"
    )
    _reject("MALFORMED_FRAME", protocol.decode_frame, raw_over)


def test_r6_b3_encode_frame_cyclic_payload_is_malformed() -> None:
    cyclic: dict = {"k": "secret-cyclic-xyz"}
    cyclic["self"] = cyclic
    err = _reject("MALFORMED_FRAME", protocol.encode_frame, cyclic)
    assert "Circular" not in err.message
    assert "secret-cyclic-xyz" not in err.message


def test_r6_b3_encode_frame_unencodable_payload_is_malformed() -> None:
    err = _reject("MALFORMED_FRAME", protocol.encode_frame, {"k": object()})
    assert "serializable" not in err.message

    err = _reject("MALFORMED_FRAME", protocol.encode_frame, {"n": 10**10000})
    assert "10000" not in err.message


class HostileValuesMapping(collections.abc.Mapping):
    """Honors the public Mapping ABC; traversal via values() raises."""

    def __getitem__(self, key):
        raise KeyError(key)

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def values(self):
        raise RuntimeError("secret-hostile-values")


class HostileMaterializeMapping(collections.abc.Mapping):
    """Nesting walk sees no children; dict() materialization raises."""

    def __getitem__(self, key):
        raise RuntimeError("secret-hostile-getitem")

    def __iter__(self):
        raise RuntimeError("secret-hostile-iter")

    def __len__(self) -> int:
        return 1

    def keys(self):
        raise RuntimeError("secret-hostile-keys")

    def values(self):
        return ()


class InterruptingMapping(collections.abc.Mapping):
    """values() raises a BaseException that must never be converted."""

    def __getitem__(self, key):
        raise KeyError(key)

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def values(self):
        raise KeyboardInterrupt


def test_r6_b3_nesting_check_fails_closed_on_hostile_traversal() -> None:
    assert protocol.json_nesting_exceeds_bound(HostileValuesMapping()) is True
    assert (
        protocol.json_nesting_exceeds_bound({"deep": [HostileValuesMapping()]})
        is True
    )


def test_r6_b3_encode_frame_hostile_mapping_traversal_is_malformed() -> None:
    err = _reject("MALFORMED_FRAME", protocol.encode_frame, HostileValuesMapping())
    assert "secret-hostile-values" not in "".join(traceback.format_exception(err))

    err = _reject(
        "MALFORMED_FRAME",
        protocol.encode_frame,
        {"payload": {"deep": HostileValuesMapping()}},
    )
    assert "secret-hostile-values" not in "".join(traceback.format_exception(err))


def test_r6_b3_encode_frame_hostile_sequence_traversal_is_malformed() -> None:
    class HostileIterList(list):
        def __iter__(self):
            raise RuntimeError("secret-hostile-list-iter")

    err = _reject("MALFORMED_FRAME", protocol.encode_frame, {"a": HostileIterList()})
    assert "secret-hostile-list-iter" not in "".join(traceback.format_exception(err))


def test_r6_b3_encode_frame_hostile_materialization_is_malformed() -> None:
    err = _reject(
        "MALFORMED_FRAME", protocol.encode_frame, HostileMaterializeMapping()
    )
    assert "secret-hostile" not in "".join(traceback.format_exception(err))


def test_r6_b3_encode_frame_unpaired_surrogate_is_malformed() -> None:
    err = _reject("MALFORMED_FRAME", protocol.encode_frame, {"k": "\ud800"})
    assert "surrogate" not in "".join(traceback.format_exception(err))


def test_r6_b3_encode_frame_never_converts_base_exceptions() -> None:
    with pytest.raises(KeyboardInterrupt):
        protocol.encode_frame(InterruptingMapping())


@pytest.mark.parametrize(
    "field",
    [
        "startup_timeout_seconds",
        "turn_timeout_seconds",
        "cancel_grace_seconds",
    ],
)
def test_r6_b5_parse_submit_huge_float_limit_is_invalid_request(field: str) -> None:
    payload = valid_submit_payload()
    payload["request"] = dict(payload["request"])
    payload["request"]["limits"] = {field: 10**10000}
    err = _reject("INVALID_REQUEST", protocol.parse_submit, payload)
    assert err.code == protocol.INVALID_REQUEST
    # Must not become INTERNAL and must not leak the huge decimal.
    assert "INTERNAL" not in err.message
    assert "1" + "0" * 50 not in err.message


# -- D1: the v3 no-close Session contract ------------------------------------
#
# One optional ``session_id`` replaces the ``session_reuse``/``ars_session_id``
# pair, ``session_close`` leaves the operation set, and only ``api_version`` 3
# is served. These pin the *boundary*: what a frame may say, and what it may not.


def _v3_request_fields() -> dict:
    """A minimal valid v3 request object (no Session field at all)."""
    return {
        "owner": "team",
        "namespace": "team/ns",
        "agent_id": "native-agent",
        "expected_binding_hash": None,
        "input_refs": [],
        "requested_model": "m",
        "requested_effort": "high",
        "grant_ref": "grant:1",
        "grant_hash": "h" * 8,
        "grant_role_hash": "r" * 8,
        "grant_capabilities": ["read"],
        "mcp_snapshot_hashes": [],
        "credential_refs": [],
        "limits": {},
        "evidence_policy_hash": "e" * 8,
        "recovery_policy_hash": "y" * 8,
    }


def _v3_submit_payload(**request_overrides) -> dict:
    request = _v3_request_fields()
    request.update(request_overrides)
    return {
        "request": request,
        "prompt_text": "hello",
        "workspace_root": "/work",
    }


def test_v3_submit_without_session_id_creates_a_new_session() -> None:
    command = protocol.parse_submit(_v3_submit_payload())
    assert command.request.session_id is None


def test_v3_submit_with_session_id_is_existing_only_reuse() -> None:
    command = protocol.parse_submit(_v3_submit_payload(session_id="sess-abc"))
    assert command.request.session_id == "sess-abc"


def test_v3_submit_rejects_the_retired_session_reuse_field() -> None:
    with pytest.raises(protocol.ProtocolError) as err:
        protocol.parse_submit(_v3_submit_payload(session_reuse="none"))
    assert err.value.code == protocol.INVALID_REQUEST


def test_v3_submit_rejects_the_retired_ars_session_id_field() -> None:
    with pytest.raises(protocol.ProtocolError) as err:
        protocol.parse_submit(_v3_submit_payload(ars_session_id="sess-abc"))
    assert err.value.code == protocol.INVALID_REQUEST


@pytest.mark.parametrize(
    "bad",
    ["", "../escape", "a/b", "a b", ".hidden", "-leading", "sess\x00"],
)
def test_v3_submit_validates_session_id_grammar_before_storage(bad: str) -> None:
    """Grammar is a wire fact: nothing may reach the store to find out."""
    with pytest.raises(protocol.ProtocolError) as err:
        protocol.parse_submit(_v3_submit_payload(session_id=bad))
    assert err.value.code == protocol.INVALID_REQUEST
    # The offending value never reaches the bounded message (stable-error rule).
    assert not bad or bad not in err.value.message


def test_session_close_is_not_an_operation() -> None:
    assert "session_close" not in protocol.OPERATIONS
    assert protocol.OPERATIONS == frozenset(
        {
            "server_info",
            "submit",
            "run_status",
            "run_events",
            "run_cancel",
            "session_status",
            "session_list",
        }
    )


# -- B1: absent creates; present null is not a create ------------------------
#
# "Absent" and "present null" are different caller statements. Absent says "I
# have no Session"; present null says "my Session is the null value", which is
# not a Session. Collapsing them lets a caller that *meant* to reuse — and whose
# id-producing code returned None — silently start a second conversation.


def test_absent_session_id_is_the_only_thing_that_creates() -> None:
    payload = _v3_submit_payload()
    assert "session_id" not in payload["request"]
    command = protocol.parse_submit(payload)
    assert command.request.session_id is None


def test_a_present_null_session_id_is_refused_before_any_side_effect() -> None:
    request = _v3_request_fields()
    request["session_id"] = None
    with pytest.raises(protocol.ProtocolError) as err:
        protocol.parse_submit(
            {"request": request, "prompt_text": "hello", "workspace_root": "/work"}
        )
    assert err.value.code == protocol.INVALID_REQUEST
    assert "session_id" in err.value.message


def test_the_session_field_is_not_in_the_generic_nullable_set() -> None:
    """Structural: it needs its own rule, or absent and null collapse again."""
    assert "session_id" not in protocol._NULLABLE_REQUEST_FIELDS


# --- the daemon-configured per-Run event-ledger budget ----------------------


def _budget_policy(ceiling: int):
    from agent_run_supervisor.native_acp import spec as spec_module

    return spec_module.EventBudgetPolicy(max_run_event_budget_bytes=ceiling)


def test_parse_submit_admits_the_default_four_gibibyte_event_budget() -> None:
    payload = valid_submit_payload()
    payload["request"]["limits"] = {"max_event_bytes": 1024 * 1024, "max_events": 4096}
    limits = protocol.parse_submit(payload).request.limits
    assert limits.max_event_bytes * limits.max_events == 4 * 1024 * 1024 * 1024

    payload["request"]["limits"] = {"max_event_bytes": 1024 * 1024, "max_events": 4097}
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


def test_parse_submit_judges_the_budget_against_the_injected_ceiling() -> None:
    exact = 4096 * 100
    payload = valid_submit_payload()
    payload["request"]["limits"] = {"max_event_bytes": 4096, "max_events": 100}

    at_ceiling = protocol.parse_submit(payload, budget_policy=_budget_policy(exact))
    assert at_ceiling.request.limits.max_events == 100

    with pytest.raises(protocol.ProtocolError) as err:
        protocol.parse_submit(payload, budget_policy=_budget_policy(exact - 1))
    assert err.value.code == protocol.INVALID_REQUEST


def test_parse_submit_admits_beyond_the_default_under_a_raised_ceiling() -> None:
    eight_gib = 8 * 1024 * 1024 * 1024
    payload = valid_submit_payload()
    payload["request"]["limits"] = {"max_event_bytes": 1024 * 1024, "max_events": 8192}

    _reject("INVALID_REQUEST", protocol.parse_submit, payload)
    raised = protocol.parse_submit(payload, budget_policy=_budget_policy(eight_gib))
    assert raised.request.limits.max_events == 8192


def test_the_ceiling_is_never_a_caller_supplied_limit_key() -> None:
    assert "event_budget_policy" not in protocol._LIMIT_FIELDS
    assert "max_run_event_budget_bytes" not in protocol._LIMIT_FIELDS
    payload = valid_submit_payload()
    payload["request"]["limits"] = {"event_budget_policy": {"max_run_event_budget_bytes": 1}}
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)
    payload["request"]["limits"] = {"max_run_event_budget_bytes": 8 * 1024**3}
    _reject("INVALID_REQUEST", protocol.parse_submit, payload)


def test_individual_limit_bounds_are_refused_under_any_ceiling() -> None:
    from agent_run_supervisor.native_acp import spec as spec_module

    huge = _budget_policy(spec_module.STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES)
    for limits in (
        {"max_event_bytes": 1024 * 1024 + 1},
        {"max_event_bytes": 255},
        {"max_events": 1_000_001},
    ):
        payload = valid_submit_payload()
        payload["request"]["limits"] = limits
        with pytest.raises(protocol.ProtocolError) as err:
            protocol.parse_submit(payload, budget_policy=huge)
        assert err.value.code == protocol.INVALID_REQUEST

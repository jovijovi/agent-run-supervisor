"""Versioned bounded NDJSON frame protocol for the ``arsd`` UDS ingress (v1).

Pure protocol layer: framing and byte caps, envelope validation, the
closed v1 operation/error sets, and the submit wire mapping onto the
untouched ``native_acp.spec`` dataclasses. No sockets, no I/O, no daemon
state. Error messages are bounded and never echo caller payload text.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, Mapping

from ..native_acp.spec import AgentRunRequest, InputRef, NativeSpecError, RunLimits

ARSD_API_VERSION = 1
SUPPORTED_API_VERSIONS = (ARSD_API_VERSION,)

MAX_FRAME_BYTES = 1_048_576
MAX_PROMPT_BYTES = 262_144
MAX_REQUEST_ID_CHARS = 128
MAX_ERROR_MESSAGE_CHARS = 512

UNSUPPORTED_API_VERSION = "UNSUPPORTED_API_VERSION"
UNKNOWN_OP = "UNKNOWN_OP"
MALFORMED_FRAME = "MALFORMED_FRAME"
FRAME_TOO_LARGE = "FRAME_TOO_LARGE"
INVALID_REQUEST = "INVALID_REQUEST"
UNAUTHENTICATED_PEER = "UNAUTHENTICATED_PEER"
PEER_UID_DENIED = "PEER_UID_DENIED"
OWNER_MISMATCH = "OWNER_MISMATCH"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
SUBMISSION_INDETERMINATE = "SUBMISSION_INDETERMINATE"
UNKNOWN_RUN = "UNKNOWN_RUN"
UNKNOWN_SESSION = "UNKNOWN_SESSION"
SESSION_BUSY = "SESSION_BUSY"
CAPACITY_EXHAUSTED = "CAPACITY_EXHAUSTED"
EVENT_BACKLOG_EXCEEDED = "EVENT_BACKLOG_EXCEEDED"
SHUTTING_DOWN = "SHUTTING_DOWN"
INTERNAL = "INTERNAL"

ERROR_CODES_V1 = frozenset(
    {
        UNSUPPORTED_API_VERSION,
        UNKNOWN_OP,
        MALFORMED_FRAME,
        FRAME_TOO_LARGE,
        INVALID_REQUEST,
        UNAUTHENTICATED_PEER,
        PEER_UID_DENIED,
        OWNER_MISMATCH,
        IDEMPOTENCY_CONFLICT,
        SUBMISSION_INDETERMINATE,
        UNKNOWN_RUN,
        UNKNOWN_SESSION,
        SESSION_BUSY,
        CAPACITY_EXHAUSTED,
        EVENT_BACKLOG_EXCEEDED,
        SHUTTING_DOWN,
        INTERNAL,
    }
)

OPERATIONS_V1 = frozenset(
    {
        "server_info",
        "submit",
        "run_status",
        "run_events",
        "run_cancel",
        "session_status",
        "session_list",
        "session_close",
    }
)

_ENVELOPE_KEYS = frozenset({"api_version", "op", "request_id", "payload"})
_SUBMIT_KEYS = frozenset(
    {"request", "prompt_text", "workspace_root", "cwd", "retry_of_run_id"}
)
_REQUEST_ID_RE = re.compile(rf"[A-Za-z0-9._-]{{1,{MAX_REQUEST_ID_CHARS}}}")

_REQUEST_FIELDS = dataclasses.fields(AgentRunRequest)
_REQUEST_FIELD_NAMES = frozenset(field.name for field in _REQUEST_FIELDS)
_REQUIRED_REQUEST_FIELDS = tuple(
    field.name
    for field in _REQUEST_FIELDS
    if field.default is dataclasses.MISSING
    and field.default_factory is dataclasses.MISSING
)
_NULLABLE_REQUEST_FIELDS = frozenset({"ars_session_id", "expected_binding_hash"})
_STRING_TUPLE_REQUEST_FIELDS = frozenset(
    {"grant_capabilities", "mcp_snapshot_hashes", "credential_refs"}
)
_LIMIT_FIELDS = frozenset(field.name for field in dataclasses.fields(RunLimits))
_INT_LIMIT_FIELDS = frozenset({"max_stderr_bytes", "max_event_bytes", "max_events"})
_INPUT_REF_FIELDS = frozenset(field.name for field in dataclasses.fields(InputRef))


class ProtocolError(Exception):
    """A v1 protocol violation carrying a code from the closed error set."""

    def __init__(self, code: str, message: str) -> None:
        if code not in ERROR_CODES_V1:
            raise ValueError(f"unknown v1 error code: {code!r}")
        bounded = _bound_message(message)
        super().__init__(bounded)
        self.code = code
        self.message = bounded


@dataclasses.dataclass(frozen=True)
class ParsedRequest:
    op: str
    request_id: str
    payload: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class SubmitCommand:
    request: AgentRunRequest
    prompt_text: str
    workspace_root: str
    cwd: str | None
    retry_of_run_id: str | None


def _bound_message(message: str) -> str:
    return str(message)[:MAX_ERROR_MESSAGE_CHARS]


def _invalid(message: str) -> ProtocolError:
    return ProtocolError(INVALID_REQUEST, message)


def _require_closed_keys(
    mapping: Mapping[str, Any], allowed: frozenset[str], where: str
) -> None:
    # Caller-controlled key names never reach the message (stable-error rule).
    if any(key not in allowed for key in mapping):
        raise _invalid(f"unknown {where} fields present")


def encode_frame(payload: Mapping[str, Any]) -> bytes:
    if not isinstance(payload, Mapping):
        raise ProtocolError(MALFORMED_FRAME, "frame payload must be a JSON object")
    text = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    data = text.encode("utf-8") + b"\n"
    if len(data) > MAX_FRAME_BYTES:
        raise ProtocolError(FRAME_TOO_LARGE, f"frame exceeds {MAX_FRAME_BYTES} bytes")
    return data


def decode_frame(data: bytes | bytearray | memoryview | str) -> dict[str, Any]:
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError(FRAME_TOO_LARGE, f"frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProtocolError(MALFORMED_FRAME, "frame is not one JSON object line") from None
    if not isinstance(payload, dict):
        raise ProtocolError(MALFORMED_FRAME, "frame is not one JSON object line")
    return payload


def parse_request(frame: Mapping[str, Any]) -> ParsedRequest:
    if not isinstance(frame, Mapping):
        raise ProtocolError(MALFORMED_FRAME, "request frame must be a JSON object")
    api_version = frame.get("api_version")
    if (
        isinstance(api_version, bool)
        or not isinstance(api_version, int)
        or api_version not in SUPPORTED_API_VERSIONS
    ):
        supported = ", ".join(str(version) for version in SUPPORTED_API_VERSIONS)
        raise ProtocolError(
            UNSUPPORTED_API_VERSION, f"unsupported api_version; supported: {supported}"
        )
    request_id = frame.get("request_id")
    if not isinstance(request_id, str) or _REQUEST_ID_RE.fullmatch(request_id) is None:
        raise _invalid(
            f"request_id must be 1..{MAX_REQUEST_ID_CHARS} characters from [A-Za-z0-9._-]"
        )
    _require_closed_keys(frame, _ENVELOPE_KEYS, "envelope")
    op = frame.get("op")
    if not isinstance(op, str) or op not in OPERATIONS_V1:
        raise ProtocolError(UNKNOWN_OP, "unknown v1 op")
    payload = frame.get("payload", {})
    if not isinstance(payload, dict):
        raise _invalid("payload must be a JSON object")
    return ParsedRequest(op=op, request_id=request_id, payload=payload)


def build_result(request_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"request_id": request_id, "result": dict(result)}


def build_error(request_id: str | None, code: str, message: str) -> dict[str, Any]:
    if code not in ERROR_CODES_V1:
        raise ValueError(f"unknown v1 error code: {code!r}")
    return {
        "request_id": request_id,
        "error": {"code": code, "message": _bound_message(message)},
    }


def _parse_limits(value: Any) -> RunLimits:
    if not isinstance(value, dict):
        raise _invalid("limits must be a JSON object")
    _require_closed_keys(value, _LIMIT_FIELDS, "limits")
    for name, number in value.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise _invalid(f"limit {name} must be a number")
        if name in _INT_LIMIT_FIELDS and not isinstance(number, int):
            raise _invalid(f"limit {name} must be an integer")
    return RunLimits(**value)


def _parse_input_refs(value: Any) -> tuple[InputRef, ...]:
    if not isinstance(value, list):
        raise _invalid("input_refs must be a JSON array")
    refs = []
    for item in value:
        if not isinstance(item, dict):
            raise _invalid("input_refs items must be JSON objects")
        _require_closed_keys(item, _INPUT_REF_FIELDS, "input_refs item")
        missing = sorted(_INPUT_REF_FIELDS - set(item))
        if missing:
            raise _invalid(f"input_refs item missing fields: {', '.join(missing)}")
        refs.append(InputRef(ref=item["ref"], content_hash=item["content_hash"]))
    return tuple(refs)


def _parse_string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise _invalid(f"request field {name} must be an array of strings")
    return tuple(value)


def _parse_request_object(value: Any) -> AgentRunRequest:
    if not isinstance(value, dict):
        raise _invalid("request must be a JSON object")
    _require_closed_keys(value, _REQUEST_FIELD_NAMES, "request")
    missing = sorted(name for name in _REQUIRED_REQUEST_FIELDS if name not in value)
    if missing:
        raise _invalid(f"request missing fields: {', '.join(missing)}")
    kwargs: dict[str, Any] = {}
    try:
        for name, item in value.items():
            if name == "limits":
                kwargs[name] = _parse_limits(item)
            elif name == "input_refs":
                kwargs[name] = _parse_input_refs(item)
            elif name in _STRING_TUPLE_REQUEST_FIELDS:
                kwargs[name] = _parse_string_tuple(item, name)
            elif name == "schema_version":
                if isinstance(item, bool) or not isinstance(item, int):
                    raise _invalid("schema_version must be an integer")
                kwargs[name] = item
            elif name in _NULLABLE_REQUEST_FIELDS:
                if item is not None and not isinstance(item, str):
                    raise _invalid(f"request field {name} must be a string or null")
                kwargs[name] = item
            else:
                kwargs[name] = item
        return AgentRunRequest(**kwargs)
    except NativeSpecError as err:
        # Spec validator text can repr caller values; only the class name is
        # safe, and the chain is suppressed so exc_info logging cannot render
        # the raw cause either.
        raise ProtocolError(
            INVALID_REQUEST,
            f"submit request failed spec validation ({type(err).__name__})",
        ) from None


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise _invalid(f"{key} must be a non-empty string or null")
    return value


def parse_submit(payload: Mapping[str, Any]) -> SubmitCommand:
    if not isinstance(payload, Mapping):
        raise _invalid("submit payload must be a JSON object")
    _require_closed_keys(payload, _SUBMIT_KEYS, "submit")
    for key in ("request", "prompt_text", "workspace_root"):
        if key not in payload:
            raise _invalid(f"submit requires {key}")
    prompt_text = payload["prompt_text"]
    if not isinstance(prompt_text, str):
        raise _invalid("prompt_text must be a string")
    prompt_bytes = len(prompt_text.encode("utf-8"))
    if prompt_bytes > MAX_PROMPT_BYTES:
        raise _invalid(f"prompt_text is {prompt_bytes} bytes (max {MAX_PROMPT_BYTES})")
    workspace_root = payload["workspace_root"]
    if not isinstance(workspace_root, str) or not workspace_root:
        raise _invalid("workspace_root must be a non-empty string")
    cwd = _optional_text(payload, "cwd")
    retry_of_run_id = _optional_text(payload, "retry_of_run_id")
    request = _parse_request_object(payload["request"])
    return SubmitCommand(
        request=request,
        prompt_text=prompt_text,
        workspace_root=workspace_root,
        cwd=cwd,
        retry_of_run_id=retry_of_run_id,
    )

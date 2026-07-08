from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

KNOWN_SESSION_UPDATE_TYPES: frozenset[str] = frozenset(
    {
        "agent_message_chunk",
        "agent_thought_chunk",
        "available_commands_update",
        "usage_update",
        "tool_call",
        "tool_call_update",
    }
)


@dataclass
class ParseResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    final_message: str = ""
    usage: dict[str, Any] | None = None
    acpx_error: dict[str, Any] | None = None
    business_verdict: None = None
    protocol_error: bool = False
    protocol_error_reasons: list[str] = field(default_factory=list)
    truncated: bool = False
    truncate_reason: str | None = None
    bytes_consumed: int = 0
    unknown_update_types: list[str] = field(default_factory=list)
    permission_request_count: int = 0
    permission_denied_count: int = 0


_EFFECT_EVENT_TYPES: frozenset[str] = frozenset(
    {"tool_started", "tool_updated", "tool_completed"}
)


def has_observed_effect(result: ParseResult) -> bool:
    """Whether the stream shows the AGENT actually produced anything.

    True when the assembled agent message has non-whitespace text or any tool
    activity was observed. Run bookkeeping alone (initialize, prompt echo,
    thought chunks, usage/command updates, ``stopReason``) does not count: an
    exit-0 stream without an observed effect is the fail-closed ``no_op``
    signal, never a success.
    """
    if result.final_message.strip():
        return True
    return any(event.get("type") in _EFFECT_EVENT_TYPES for event in result.events)


def _flag_protocol_error(result: ParseResult, reason: str) -> None:
    result.protocol_error = True
    result.protocol_error_reasons.append(reason)


def _is_jsonrpc_envelope(record: Any) -> bool:
    return isinstance(record, dict) and record.get("jsonrpc") == "2.0"


def _summarize_keys(value: Any, prefix: str = "") -> list[str]:
    summaries: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            path = f"{prefix}.{key}" if prefix else key
            summaries.append(f"{path}:{type(sub).__name__}")
            if isinstance(sub, (dict, list)) and len(summaries) < 50:
                summaries.extend(_summarize_keys(sub, path))
    elif isinstance(value, list):
        for index, sub in enumerate(value[:5]):
            path = f"{prefix}[{index}]"
            summaries.append(f"{path}:{type(sub).__name__}")
            if isinstance(sub, (dict, list)) and len(summaries) < 50:
                summaries.extend(_summarize_keys(sub, path))
    return summaries


def _emit_run_started(result: ParseResult, record: dict[str, Any]) -> None:
    result.events.append(
        {
            "type": "run_started",
            "method": record.get("method"),
            "id": record.get("id"),
        }
    )


def _emit_run_completed(result: ParseResult, record: dict[str, Any]) -> None:
    inner = record.get("result")
    if isinstance(inner, dict):
        stop_reason = inner.get("stopReason")
        usage = inner.get("usage")
        if isinstance(usage, dict):
            result.usage = usage
    else:
        stop_reason = None
    result.events.append({"type": "run_completed", "stop_reason": stop_reason})


def _emit_session_update(result: ParseResult, record: dict[str, Any]) -> None:
    params = record.get("params")
    if not isinstance(params, dict):
        _flag_protocol_error(result, "session/update missing params")
        return
    update = params.get("update")
    if not isinstance(update, dict):
        _flag_protocol_error(result, "session/update missing update mapping")
        return
    update_type = update.get("sessionUpdate")
    if update_type == "agent_message_chunk":
        content = update.get("content")
        text = ""
        if isinstance(content, dict) and content.get("type") == "text":
            text = content.get("text", "") or ""
        result.final_message += text
        result.events.append(
            {"type": "agent_message_delta", "text_length": len(text)}
        )
    elif update_type == "agent_thought_chunk":
        result.events.append({"type": "agent_thought_delta"})
    elif update_type == "usage_update":
        usage_payload = {
            key: value
            for key, value in update.items()
            if key != "sessionUpdate" and isinstance(key, str)
        }
        result.usage = usage_payload
        result.events.append({"type": "usage_updated"})
    elif update_type == "available_commands_update":
        result.events.append({"type": "available_commands_updated"})
    elif update_type == "tool_call":
        result.events.append(
            {
                "type": "tool_started",
                "tool_call_id": update.get("toolCallId"),
                "kind": update.get("kind"),
            }
        )
    elif update_type == "tool_call_update":
        status = update.get("status")
        event_type = "tool_completed" if status in {"completed", "failed"} else "tool_updated"
        result.events.append(
            {
                "type": event_type,
                "tool_call_id": update.get("toolCallId"),
                "status": status,
            }
        )
    else:
        if isinstance(update_type, str):
            result.unknown_update_types.append(update_type)
            result.events.append(
                {
                    "type": "unknown_update",
                    "update_type": update_type,
                    "key_summary": ",".join(_summarize_keys(update)),
                }
            )
        else:
            _flag_protocol_error(result, "session/update has non-string sessionUpdate type")


def _emit_request_permission(result: ParseResult, record: dict[str, Any]) -> None:
    params = record.get("params")
    tool_call_id = None
    options: list[Any] = []
    if isinstance(params, dict):
        tool_call = params.get("toolCall")
        if isinstance(tool_call, dict):
            tool_call_id = tool_call.get("toolCallId")
        raw_options = params.get("options")
        if isinstance(raw_options, list):
            options = raw_options
    result.permission_request_count += 1
    result.events.append(
        {
            "type": "permission_requested",
            "tool_call_id": tool_call_id,
            "option_ids": [
                opt.get("optionId")
                for opt in options
                if isinstance(opt, dict)
            ],
        }
    )


def _emit_request_permission_response(result: ParseResult, record: dict[str, Any]) -> None:
    inner = record.get("result")
    if not isinstance(inner, dict):
        return
    outcome = inner.get("outcome")
    if not isinstance(outcome, dict):
        return
    option_id = outcome.get("optionId")
    if option_id and "reject" in str(option_id).lower():
        result.permission_denied_count += 1
        result.events.append(
            {
                "type": "permission_denied",
                "option_id": option_id,
            }
        )


def _record_acpx_error(result: ParseResult, record: dict[str, Any]) -> None:
    error = record.get("error")
    if isinstance(error, dict):
        result.acpx_error = error
        result.events.append(
            {
                "type": "run_failed",
                "code": error.get("code"),
                "acpx_code": (error.get("data") or {}).get("acpxCode")
                if isinstance(error.get("data"), dict)
                else None,
            }
        )


def _consume_record(result: ParseResult, record: dict[str, Any]) -> None:
    method = record.get("method")
    has_error = "error" in record
    has_result = "result" in record

    if has_error:
        _record_acpx_error(result, record)
        return

    if method == "initialize":
        _emit_run_started(result, record)
        return
    if method == "session/new":
        result.events.append({"type": "session_new_requested"})
        return
    if method == "session/set_model":
        result.events.append({"type": "session_model_set"})
        return
    if method == "session/prompt":
        result.events.append({"type": "session_prompt_sent"})
        return
    if method == "session/update":
        _emit_session_update(result, record)
        return
    if method == "session/request_permission":
        _emit_request_permission(result, record)
        return
    if method is None and has_result and "outcome" in (record.get("result") or {}):
        _emit_request_permission_response(result, record)
        return

    if method is None and has_result:
        inner = record.get("result") or {}
        if isinstance(inner, dict) and (
            "stopReason" in inner or "usage" in inner
        ):
            _emit_run_completed(result, record)
            return
        result.events.append({"type": "rpc_result", "id": record.get("id")})
        return

    if method is None:
        return

    result.unknown_update_types.append(str(method))
    result.events.append(
        {
            "type": "unknown_update",
            "update_type": str(method),
            "key_summary": ",".join(_summarize_keys(record.get("params") or {})),
        }
    )


def parse_acpx_stdout_bytes(
    payload: bytes,
    max_output_bytes: int | None = None,
) -> ParseResult:
    result = ParseResult()
    if max_output_bytes is not None and len(payload) > max_output_bytes:
        truncated_payload = payload[:max_output_bytes]
        result.truncated = True
        result.truncate_reason = "max_output_bytes"
        _flag_protocol_error(result, "stdout exceeded max_output_bytes")
        _drive_lines(_iter_lines(truncated_payload), result)
        return result
    _drive_lines(_iter_lines(payload), result)
    return result


def parse_acpx_stdout(path: Path) -> ParseResult:
    return parse_acpx_stdout_bytes(Path(path).read_bytes())


class ManagementParseError(ValueError):
    """Raised when management-command output is not a single safe JSON object.

    Fails closed for empty output, non-JSON, JSON that is not an object, and a
    JSON-RPC *turn* stream record accidentally fed to the management path. A
    JSON-RPC *error* envelope (``{"jsonrpc":..., "error":{...}}``) is **not** an
    error here — it is surfaced as a management ``error`` summary so the caller
    can classify the acpx failure code.
    """


# Management responses we summarize only carry an allow-list of scalar identity
# and lifecycle fields. Everything else (model catalogs, event-log paths, agent
# command lines, message bodies) is bulk/untrusted and is deliberately dropped.
def summarize_management_json(payload: bytes | str) -> dict[str, Any]:
    """Summarize a single acpx management-command JSON object.

    Management commands (``sessions new/ensure/show``, ``status``,
    ``sessions close``, ``cancel``) emit exactly one JSON object — never a
    JSON-RPC NDJSON stream. This returns a safe, allow-listed summary and never
    echoes bulk payload. Use :func:`parse_acpx_stdout_bytes` for prompt turns.
    """
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ManagementParseError(f"management output is not UTF-8: {exc}") from exc
    else:
        text = payload
    text = text.strip()
    if not text:
        raise ManagementParseError("management output is empty")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManagementParseError(
            f"management output is not a single JSON object: {exc}",
        ) from exc
    if not isinstance(obj, dict):
        raise ManagementParseError("management output must be a JSON object")

    if "error" in obj:
        error = obj.get("error")
        code = None
        acpx_code = None
        if isinstance(error, dict):
            code = error.get("code")
            data = error.get("data")
            if isinstance(data, dict):
                acpx_code = data.get("acpxCode")
        return _management_summary(
            kind="error",
            obj=obj,
            code=code,
            acpx_code=acpx_code,
        )

    if obj.get("jsonrpc") is not None:
        # A JSON-RPC *method/result* record (no error) is a turn-stream line, not
        # a management response. Refuse so streams never masquerade as management.
        raise ManagementParseError(
            "management output is a JSON-RPC stream record, not a management object",
        )

    schema = obj.get("schema")
    action = obj.get("action")
    if schema == "acpx.session.v1":
        kind = "acpx.session.v1"
    elif isinstance(action, str):
        kind = action
    else:
        kind = "unknown"
    return _management_summary(kind=kind, obj=obj)


def _management_summary(
    *,
    kind: str,
    obj: dict[str, Any],
    code: Any = None,
    acpx_code: Any = None,
) -> dict[str, Any]:
    def _bool_or_none(value: Any) -> bool | None:
        return value if isinstance(value, bool) else None

    return {
        "kind": kind,
        "acpx_record_id": obj.get("acpxRecordId"),
        # `show` spells the id `acpSessionId`; everything else `acpxSessionId`.
        "acpx_session_id": obj.get("acpxSessionId") or obj.get("acpSessionId"),
        "session_name": obj.get("name") if isinstance(obj.get("name"), str) else None,
        "created": _bool_or_none(obj.get("created")),
        "closed": _bool_or_none(obj.get("closed")),
        "status": obj.get("status") if isinstance(obj.get("status"), str) else None,
        "cancelled": _bool_or_none(obj.get("cancelled")),
        "code": code,
        "acpx_code": acpx_code,
        # Structural evidence only: top-level key names + value *types*, never
        # the values themselves.
        "top_level_keys": sorted(
            f"{key}:{type(value).__name__}" for key, value in obj.items()
        ),
    }


def _iter_lines(payload: bytes) -> Iterable[tuple[int, bytes]]:
    line_number = 0
    for raw in payload.splitlines():
        line_number += 1
        if not raw.strip():
            continue
        yield line_number, raw


def _drive_line(result: ParseResult, line_number: int, raw: bytes) -> bool:
    """Consume one non-blank NDJSON line into ``result``.

    Returns ``True`` when parsing must halt (invalid JSON or a non-jsonrpc
    envelope), mirroring the fatal-line handling of the batch parser. A protocol
    error flagged *inside* :func:`_consume_record` does not halt the stream.
    """
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _flag_protocol_error(result, f"line {line_number}: invalid JSON: {exc}")
        return True
    if not _is_jsonrpc_envelope(record):
        _flag_protocol_error(result, f"line {line_number}: non-jsonrpc envelope")
        return True
    _consume_record(result, record)
    result.bytes_consumed += len(raw)
    return False


def _drive_lines(lines: Iterable[tuple[int, bytes]], result: ParseResult) -> None:
    for line_number, raw in lines:
        if _drive_line(result, line_number, raw):
            return


@dataclass
class IncrementalParseState:
    """Live NDJSON parse state for consuming acpx stdout while it streams.

    Holds the accumulating :class:`ParseResult` plus a byte buffer for the
    partial trailing line at a reader boundary. Once a fatal line halts parsing
    (``_stopped``), further input is ignored, mirroring the batch parser which
    stops at the first malformed line.
    """

    result: ParseResult = field(default_factory=ParseResult)
    _buffer: bytes = b""
    _line_number: int = 0
    _stopped: bool = False


def consume_acpx_line(
    state: IncrementalParseState, line: bytes | str
) -> list[dict[str, Any]]:
    """Consume one complete JSON-RPC NDJSON record (no trailing newline).

    Returns only the events newly emitted by this record. Blank lines and input
    after a fatal line are no-ops.
    """
    raw = line.encode("utf-8") if isinstance(line, str) else line
    state._line_number += 1
    if state._stopped or not raw.strip():
        return []
    result = state.result
    before = len(result.events)
    if _drive_line(result, state._line_number, raw):
        state._stopped = True
    return result.events[before:]


def feed_acpx_bytes(
    state: IncrementalParseState, chunk: bytes
) -> list[dict[str, Any]]:
    """Buffer streamed bytes and parse only newline-terminated records.

    A partial trailing line (no newline yet) is retained and emits nothing until
    a later chunk completes its record. Returns the concatenation of events
    emitted by every complete record in this chunk.
    """
    state._buffer += chunk
    emitted: list[dict[str, Any]] = []
    while True:
        newline_index = state._buffer.find(b"\n")
        if newline_index == -1:
            break
        line = state._buffer[:newline_index]
        state._buffer = state._buffer[newline_index + 1 :]
        if line.endswith(b"\r"):  # tolerate CRLF line endings like ``splitlines``
            line = line[:-1]
        emitted.extend(consume_acpx_line(state, line))
    return emitted



def finish_acpx_bytes(state: IncrementalParseState) -> list[dict[str, Any]]:
    """Flush the final unterminated NDJSON record at stream EOF, if present."""
    if not state._buffer:
        return []
    line = state._buffer
    state._buffer = b""
    if line.endswith(b"\r"):
        line = line[:-1]
    return consume_acpx_line(state, line)

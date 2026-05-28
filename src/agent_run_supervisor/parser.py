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


def _iter_lines(payload: bytes) -> Iterable[tuple[int, bytes]]:
    line_number = 0
    for raw in payload.splitlines():
        line_number += 1
        if not raw.strip():
            continue
        yield line_number, raw


def _drive_lines(lines: Iterable[tuple[int, bytes]], result: ParseResult) -> None:
    for line_number, raw in lines:
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _flag_protocol_error(result, f"line {line_number}: invalid JSON: {exc}")
            return
        if not _is_jsonrpc_envelope(record):
            _flag_protocol_error(result, f"line {line_number}: non-jsonrpc envelope")
            return
        _consume_record(result, record)
        result.bytes_consumed += len(raw)

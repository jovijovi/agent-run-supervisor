from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_run_supervisor.parser import (
    ManagementParseError,
    ParseResult,
    parse_acpx_stdout,
    parse_acpx_stdout_bytes,
    summarize_management_json,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "acpx-0.12.0"


def _read(name: str, filename: str = "stdout.ndjson") -> bytes:
    return (FIXTURES / name / filename).read_bytes()


def test_success_codex_sentinel_assembles_final_message_from_chunks() -> None:
    result = parse_acpx_stdout_bytes(_read("success-codex-sentinel"))

    assert isinstance(result, ParseResult)
    assert result.protocol_error is False
    assert result.final_message == "CODEX_ACPX_OK"
    assert result.business_verdict is None


def test_success_codex_sentinel_captures_usage_update() -> None:
    result = parse_acpx_stdout_bytes(_read("success-codex-sentinel"))

    assert result.usage is not None
    total_tokens = result.usage.get("totalTokens")
    assert isinstance(total_tokens, int)
    assert total_tokens > 0


def test_success_codex_sentinel_emits_run_started_and_completed_events() -> None:
    result = parse_acpx_stdout_bytes(_read("success-codex-sentinel"))

    event_types = [event["type"] for event in result.events]
    assert "run_started" in event_types
    assert "run_completed" in event_types
    assert "agent_message_delta" in event_types
    assert "usage_updated" in event_types


def test_permission_denied_fixture_records_permission_request_and_denial() -> None:
    result = parse_acpx_stdout_bytes(_read("permission-denied-codex-read"))

    event_types = [event["type"] for event in result.events]
    assert "permission_requested" in event_types
    assert "permission_denied" in event_types
    assert result.protocol_error is False


def test_permission_denied_fixture_assembles_partial_final_message() -> None:
    result = parse_acpx_stdout_bytes(_read("permission-denied-codex-read"))

    assert result.final_message.endswith("READ_DONE")


def test_parser_records_permission_request_and_rejection_protocol_events() -> None:
    payload = b"\n".join(
        [
            b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}',
            b'{"jsonrpc":"2.0","id":1,"method":"session/request_permission","params":{"sessionId":"s","toolCall":{"toolCallId":"call-1"},"options":[{"optionId":"reject_once"}]}}',
            b'{"jsonrpc":"2.0","id":1,"result":{"outcome":{"outcome":"selected","optionId":"reject_once"}}}',
        ]
    ) + b"\n"

    result = parse_acpx_stdout_bytes(payload)

    event_types = [event["type"] for event in result.events]
    assert "permission_requested" in event_types
    assert "permission_denied" in event_types
    assert result.permission_request_count == 1
    assert result.permission_denied_count == 1
    assert result.protocol_error is False


def test_usage_error_fixture_is_recognized_as_error_envelope() -> None:
    result = parse_acpx_stdout_bytes(_read("usage-error-invalid-flag"))

    assert result.protocol_error is False
    assert result.acpx_error is not None
    assert result.acpx_error["data"]["acpxCode"] == "USAGE"


def test_timeout_fixture_records_timeout_acpx_code() -> None:
    result = parse_acpx_stdout_bytes(_read("timeout-hanging-agent"))

    assert result.acpx_error is not None
    assert result.acpx_error["data"]["acpxCode"] == "TIMEOUT"


def test_runtime_error_fixture_records_runtime_acpx_code() -> None:
    result = parse_acpx_stdout_bytes(_read("runtime-error-agent"))

    assert result.acpx_error is not None
    assert result.acpx_error["data"]["acpxCode"] == "RUNTIME"


def test_management_no_session_fixture_records_no_session_acpx_code() -> None:
    result = parse_acpx_stdout_bytes(_read("management-no-session-exit4"))

    assert result.acpx_error is not None
    assert result.acpx_error["data"]["acpxCode"] == "NO_SESSION"


def test_management_status_no_session_json_fails_as_protocol_error_when_replayed_as_exec() -> None:
    payload = (FIXTURES / "management-status-no-session-exit0" / "stdout.json").read_bytes()

    result = parse_acpx_stdout_bytes(payload)

    assert result.protocol_error is True
    assert any(
        "non-jsonrpc" in reason.lower() or "jsonrpc" in reason.lower()
        for reason in result.protocol_error_reasons
    )


def test_malformed_ndjson_yields_protocol_error() -> None:
    payload = b'{"jsonrpc":"2.0","id":0}\nnot-json\n'

    result = parse_acpx_stdout_bytes(payload)

    assert result.protocol_error is True


def test_unknown_session_update_keeps_only_summary() -> None:
    payload = (
        b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n'
        b'{"jsonrpc":"2.0","method":"session/update",'
        b'"params":{"sessionId":"abc","update":{"sessionUpdate":"new_unknown_type","payload":{"secret":"sk-XYZ"}}}}\n'
    )

    result = parse_acpx_stdout_bytes(payload)

    summaries = [event for event in result.events if event["type"] == "unknown_update"]
    assert summaries, result.events
    summary = summaries[0]
    assert summary["update_type"] == "new_unknown_type"
    assert "sk-XYZ" not in str(summary)
    assert "payload" in summary.get("key_summary", "")


def test_parser_enforces_max_output_bytes_and_truncates() -> None:
    payload = (
        b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n'
        b'{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"abc","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"' + (b"A" * 1000) + b'"}}}}\n'
    )

    result = parse_acpx_stdout_bytes(payload, max_output_bytes=200)

    assert result.truncated is True
    assert result.truncate_reason == "max_output_bytes"
    assert result.protocol_error is True


def test_business_verdict_is_always_none_in_v0_1a() -> None:
    payload = _read("success-codex-sentinel")
    result = parse_acpx_stdout_bytes(payload)

    assert result.business_verdict is None


def test_parse_acpx_stdout_accepts_path(tmp_path: Path) -> None:
    fixture = FIXTURES / "success-codex-sentinel" / "stdout.ndjson"

    result = parse_acpx_stdout(fixture)

    assert result.final_message == "CODEX_ACPX_OK"
    assert result.protocol_error is False


# --- S1c management-command JSON summarizer --------------------------------
#
# Management commands emit a single JSON object (NOT a JSON-RPC NDJSON stream),
# so they get a dedicated summarizer. It extracts only an allow-list of scalar
# identity/lifecycle fields plus a top-level key/type summary — it never echoes
# bulk/untrusted payload (model lists, event-log paths, agent command lines).


def test_summarize_session_ensured_created_extracts_ids_and_name() -> None:
    summary = summarize_management_json(_read("session-new-named", "stdout.json"))

    assert summary["kind"] == "session_ensured"
    assert summary["created"] is True
    assert isinstance(summary["acpx_session_id"], str) and summary["acpx_session_id"]
    assert summary["acpx_record_id"] == summary["acpx_session_id"]
    assert summary["session_name"] == "s1a-session-contract"


def test_summarize_session_ensured_existing_reports_created_false() -> None:
    summary = summarize_management_json(_read("session-ensure-existing", "stdout.json"))

    assert summary["kind"] == "session_ensured"
    assert summary["created"] is False


def test_summarize_session_show_normalizes_acp_session_id_and_closed() -> None:
    summary = summarize_management_json(_read("session-show-open", "stdout.json"))

    assert summary["kind"] == "acpx.session.v1"
    # `show` spells it `acpSessionId` (no x); the summary normalizes the key.
    assert isinstance(summary["acpx_session_id"], str) and summary["acpx_session_id"]
    assert summary["closed"] is False
    assert summary["session_name"] == "s1a-session-contract"


def test_summarize_session_show_omits_bulk_untrusted_payload() -> None:
    summary = summarize_management_json(_read("session-show-open", "stdout.json"))
    serialized = json.dumps(summary)

    # No model catalog, event-log path, agent command line, or cwd value leaks.
    assert "gpt-5.5" not in serialized
    assert "@agentclientprotocol" not in serialized
    assert ".stream.ndjson" not in serialized
    assert "/home/ecs-user" not in serialized
    # And the summary does not carry those bulk fields as keys.
    assert "availableModels" not in summary
    assert "eventLog" not in summary
    assert "messages" not in summary


def test_summarize_status_snapshot_reports_alive_status() -> None:
    summary = summarize_management_json(_read("session-status-after-turns", "stdout.json"))

    assert summary["kind"] == "status_snapshot"
    assert summary["status"] == "alive"


def test_summarize_status_snapshot_reports_no_session() -> None:
    summary = summarize_management_json(
        _read("management-status-no-session-exit0", "stdout.json")
    )

    assert summary["kind"] == "status_snapshot"
    assert summary["status"] == "no-session"


def test_summarize_session_closed() -> None:
    summary = summarize_management_json(_read("session-close-named", "stdout.json"))

    assert summary["kind"] == "session_closed"
    assert isinstance(summary["acpx_session_id"], str) and summary["acpx_session_id"]


def test_summarize_cancel_result_reports_cancelled_flag() -> None:
    summary = summarize_management_json(_read("session-cancel-no-active", "stdout.json"))

    assert summary["kind"] == "cancel_result"
    assert summary["cancelled"] is False


def test_summarize_surfaces_jsonrpc_error_envelope_as_management_error() -> None:
    # A single jsonrpc error line (missing session, exit 4) is a management error,
    # not a turn stream; the summarizer surfaces the acpxCode for classification.
    summary = summarize_management_json(_read("management-no-session-exit4", "stdout.ndjson"))

    assert summary["kind"] == "error"
    assert summary["acpx_code"] == "NO_SESSION"


def test_summarize_refuses_ndjson_prompt_stream() -> None:
    # Multi-line JSON-RPC turn stream must NOT be parsed as a management object.
    with pytest.raises(ManagementParseError):
        summarize_management_json(_read("session-prompt-turn1", "stdout.ndjson"))


def test_summarize_refuses_jsonrpc_method_record() -> None:
    record = b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}'
    with pytest.raises(ManagementParseError):
        summarize_management_json(record)


def test_summarize_refuses_non_json() -> None:
    with pytest.raises(ManagementParseError):
        summarize_management_json(b"not json at all")


def test_summarize_refuses_empty_output() -> None:
    with pytest.raises(ManagementParseError):
        summarize_management_json(b"   \n  ")


def test_summarize_refuses_json_array() -> None:
    with pytest.raises(ManagementParseError):
        summarize_management_json(b'["not", "an", "object"]')


def test_parser_handles_thought_chunk_and_tool_events() -> None:
    payload = b"\n".join(
        [
            b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}',
            b'{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s","update":{"sessionUpdate":"agent_thought_chunk"}}}',
            b'{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s","update":{"sessionUpdate":"tool_call","toolCallId":"t1","kind":"read"}}}',
            b'{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s","update":{"sessionUpdate":"tool_call_update","toolCallId":"t1","status":"completed"}}}',
            b'{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s","update":{"sessionUpdate":"available_commands_update"}}}',
        ]
    ) + b"\n"

    result = parse_acpx_stdout_bytes(payload)
    event_types = [event["type"] for event in result.events]

    assert "agent_thought_delta" in event_types
    assert "tool_started" in event_types
    assert "tool_completed" in event_types
    assert "available_commands_updated" in event_types


def test_parser_flags_missing_session_update_fields() -> None:
    payload = (
        b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n'
        b'{"jsonrpc":"2.0","method":"session/update","params":null}\n'
        b'{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s"}}\n'
        b'{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s","update":{"sessionUpdate":123}}}\n'
    )

    result = parse_acpx_stdout_bytes(payload)

    assert result.protocol_error is True
    assert any("params" in reason for reason in result.protocol_error_reasons)
    assert any("update mapping" in reason for reason in result.protocol_error_reasons)
    assert any("non-string sessionUpdate" in reason for reason in result.protocol_error_reasons)


def test_parser_records_unknown_rpc_method_with_key_summary() -> None:
    payload = (
        b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n'
        b'{"jsonrpc":"2.0","method":"future/method","params":{"nested":{"list":[1,2]}}}\n'
    )

    result = parse_acpx_stdout_bytes(payload)
    unknown = [event for event in result.events if event["type"] == "unknown_update"]

    assert unknown
    assert unknown[0]["update_type"] == "future/method"
    assert "nested.list" in unknown[0]["key_summary"]


def test_parser_run_completed_with_non_dict_result() -> None:
    payload = b'{"jsonrpc":"2.0","id":1,"result":"done"}\n'

    result = parse_acpx_stdout_bytes(payload)

    assert [event["type"] for event in result.events] == ["rpc_result"]


def test_parser_skips_blank_lines_in_stream() -> None:
    payload = b'\n\n{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n\n'

    result = parse_acpx_stdout_bytes(payload)

    assert result.protocol_error is False
    assert [event["type"] for event in result.events] == ["run_started"]


def test_parser_permission_response_ignores_non_dict_outcomes() -> None:
    payload = b"\n".join(
        [
            b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}',
            b'{"jsonrpc":"2.0","id":1,"result":"not-a-mapping"}',
            b'{"jsonrpc":"2.0","id":2,"result":{"outcome":"missing"}}',
        ]
    ) + b"\n"

    result = parse_acpx_stdout_bytes(payload)

    assert result.permission_denied_count == 0
    assert "permission_denied" not in [event["type"] for event in result.events]


def test_summarize_management_unknown_kind_for_unrecognized_object() -> None:
    summary = summarize_management_json(b'{"unexpected": true}')

    assert summary["kind"] == "unknown"


def test_summarize_management_utf8_decode_error() -> None:
    with pytest.raises(ManagementParseError) as excinfo:
        summarize_management_json(b"\xff\xfe")

    assert "UTF-8" in str(excinfo.value)


def test_summarize_management_error_without_dict_payload() -> None:
    summary = summarize_management_json(b'{"error": "plain-string"}')

    assert summary["kind"] == "error"
    assert summary["code"] is None
    assert summary["acpx_code"] is None

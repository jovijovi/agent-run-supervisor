from __future__ import annotations

from pathlib import Path

import pytest

from agent_run_supervisor.parser import (
    ParseResult,
    parse_acpx_stdout,
    parse_acpx_stdout_bytes,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "acpx-0.10.0"


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
    assert result.usage.get("totalTokens") == 14292


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

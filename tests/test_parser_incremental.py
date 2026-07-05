"""Incremental (live) NDJSON parser tests.

PR1 live event streaming: the supervisor must be able to consume acpx stdout
NDJSON one record at a time *while the child is still running*, and that live
projection must agree with the post-hoc batch parse. These tests pin:

- a partial line at the stream-reader boundary emits NO event until its newline
  completes the JSON record (event-first, but never on half a record), and
- the incremental parse of a full fixture equals ``parse_acpx_stdout_bytes``.
"""
from __future__ import annotations

from pathlib import Path

from agent_run_supervisor.parser import (
    IncrementalParseState,
    consume_acpx_line,
    feed_acpx_bytes,
    finish_acpx_bytes,
    parse_acpx_stdout_bytes,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "acpx-0.10.0"


def _read(name: str, filename: str = "stdout.ndjson") -> bytes:
    return (FIXTURES / name / filename).read_bytes()


def test_partial_line_emits_no_event_until_newline_completes_record() -> None:
    state = IncrementalParseState()

    # Feed the first record split across a reader boundary: the first chunk is a
    # syntactically incomplete JSON object with no trailing newline.
    first_half = b'{"jsonrpc":"2.0","id":0,"meth'
    events = feed_acpx_bytes(state, first_half)
    assert events == []
    # An incomplete record is NOT a protocol error — we simply have not seen the
    # newline yet.
    assert state.result.protocol_error is False
    assert state.result.events == []

    # The rest of the record plus its newline completes exactly one record.
    second_half = b'od":"initialize","params":{}}\n'
    events = feed_acpx_bytes(state, second_half)
    assert [event["type"] for event in events] == ["run_started"]
    assert state.result.protocol_error is False


def test_consume_acpx_line_returns_only_newly_produced_events() -> None:
    state = IncrementalParseState()

    first = consume_acpx_line(
        state, b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}'
    )
    assert [event["type"] for event in first] == ["run_started"]

    second = consume_acpx_line(
        state,
        b'{"jsonrpc":"2.0","method":"session/update","params":'
        b'{"sessionId":"abc","update":{"sessionUpdate":"agent_message_chunk",'
        b'"content":{"type":"text","text":"hi"}}}}',
    )
    assert [event["type"] for event in second] == ["agent_message_delta"]
    # State accumulates across lines just like the batch parser.
    assert state.result.final_message == "hi"


def test_incremental_feed_agrees_with_batch_parse_for_sentinel() -> None:
    payload = _read("success-codex-sentinel")

    state = IncrementalParseState()
    feed_acpx_bytes(state, payload)
    batch = parse_acpx_stdout_bytes(payload)

    assert state.result.events == batch.events
    assert state.result.final_message == batch.final_message
    assert state.result.usage == batch.usage
    assert state.result.protocol_error == batch.protocol_error


def test_incremental_feed_across_arbitrary_chunk_boundaries_matches_batch() -> None:
    payload = _read("session-prompt-turn1")

    # Drip the payload one byte at a time; record boundaries must be honored
    # regardless of how the reader chunked the stream.
    state = IncrementalParseState()
    collected: list[dict] = []
    for index in range(len(payload)):
        collected.extend(feed_acpx_bytes(state, payload[index : index + 1]))

    batch = parse_acpx_stdout_bytes(payload)
    assert collected == batch.events
    assert state.result.events == batch.events


def test_finish_flushes_final_unterminated_record_at_eof() -> None:
    state = IncrementalParseState()
    record = b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}'

    assert feed_acpx_bytes(state, record) == []
    events = finish_acpx_bytes(state)

    assert [event["type"] for event in events] == ["run_started"]
    assert state._buffer == b""


def test_incremental_halts_after_first_bad_record_like_batch() -> None:
    state = IncrementalParseState()
    feed_acpx_bytes(state, b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n')
    feed_acpx_bytes(state, b"not-json\n")
    # A record after the fatal line must be ignored, mirroring the batch parser
    # which stops at the first malformed line.
    feed_acpx_bytes(
        state,
        b'{"jsonrpc":"2.0","method":"session/update","params":'
        b'{"sessionId":"abc","update":{"sessionUpdate":"agent_message_chunk",'
        b'"content":{"type":"text","text":"late"}}}}\n',
    )

    assert state.result.protocol_error is True
    assert state.result.final_message == ""
    assert [event["type"] for event in state.result.events] == ["run_started"]

"""C7: per-Run bounded single-writer event stream (monotonic seq, queue
timeout signal, byte-cap truncation preserving critical families)."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from agent_run_supervisor.native_acp.event_writer import (
    EventWriter,
    EventWriterOverflow,
)


class RecordingHandle:
    """Duck-typed RunHandle: records appended NDJSON records."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def append_ndjson(self, name: str, record: dict) -> None:
        self.records.append((name, dict(record)))


class GatedHandle(RecordingHandle):
    """Blocks every append until the gate is released (queue-pressure tap)."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()

    def append_ndjson(self, name: str, record: dict) -> None:
        self.gate.wait(timeout=30)
        super().append_ndjson(name, record)


def test_seq_is_monotonic_from_one() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()
        for index in range(5):
            await writer.emit({"type": "tool_updated", "status": f"s{index}"})
        await writer.close()
        sequences = [record["seq"] for _, record in handle.records]
        assert sequences == [1, 2, 3, 4, 5]
        assert all(name == "normalized-events.jsonl" for name, _ in handle.records)

    asyncio.run(case())


def test_byte_cap_truncates_but_preserves_family() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=256)
        await writer.start()
        await writer.emit({"type": "permission_mediation", "decision": "deny", "reason": "r"})
        await writer.emit(
            {"type": "run_failed", "detail": "x" * 10_000}
        )
        await writer.emit({"type": "usage_updated"})
        await writer.close()
        records = [record for _, record in handle.records]
        assert records[0]["type"] == "permission_mediation"
        assert "truncated" not in records[0]
        oversized = records[1]
        assert oversized["type"] == "run_failed"  # family preserved
        assert oversized["truncated"] is True
        assert oversized["truncate_reason"] == "max_event_bytes"
        assert "detail" not in oversized
        assert len(json.dumps(oversized)) <= 256
        assert records[2] == {"seq": 3, "type": "usage_updated"}

    asyncio.run(case())


def test_queue_full_producer_timeout_signals_controlled_failure() -> None:
    async def case() -> None:
        handle = GatedHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            queue_maxsize=1,
            producer_timeout_seconds=0.2,
        )
        await writer.start()
        try:
            with pytest.raises(EventWriterOverflow):
                # The gated sink never drains; the bounded queue must fill and
                # the producer must receive a controlled failure signal, not
                # an unbounded buffer or a silent drop.
                for _ in range(10):
                    await writer.emit({"type": "agent_message_delta", "text_length": 1})
            assert writer.overflowed is True
        finally:
            handle.gate.set()
            await writer.close()

    asyncio.run(case())


def test_close_flushes_pending_events() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()
        for index in range(3):
            await writer.emit({"type": "tool_updated", "status": str(index)})
        await writer.close()
        assert len(handle.records) == 3

    asyncio.run(case())


def test_emit_after_close_is_refused() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()
        await writer.close()
        with pytest.raises(EventWriterOverflow):
            await writer.emit({"type": "usage_updated"})

    asyncio.run(case())


def test_emit_nowait_from_sync_context_writes_in_order() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()
        # Sync producers (SDK callback context) use the non-blocking variant.
        writer.emit_nowait({"type": "run_started"})
        writer.emit_nowait({"type": "usage_updated"})
        await writer.close()
        assert [record["type"] for _, record in handle.records] == [
            "run_started",
            "usage_updated",
        ]

    asyncio.run(case())


def test_emit_nowait_full_queue_is_a_controlled_signal() -> None:
    async def case() -> None:
        handle = GatedHandle()
        writer = EventWriter(handle, max_event_bytes=65536, queue_maxsize=1)
        await writer.start()
        try:
            with pytest.raises(EventWriterOverflow):
                for _ in range(10):
                    writer.emit_nowait({"type": "usage_updated"})
            assert writer.overflowed is True
        finally:
            handle.gate.set()
            await writer.close()

    asyncio.run(case())


def test_event_writer_constructor_rejects_invalid_max_events_bounds() -> None:
    handle = RecordingHandle()
    with pytest.raises(ValueError):
        EventWriter(handle, max_event_bytes=65536, max_events=0)
    with pytest.raises(ValueError):
        EventWriter(handle, max_event_bytes=65536, max_events=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        EventWriter(handle, max_event_bytes=255, max_events=10)
    with pytest.raises(ValueError):
        EventWriter(handle, max_event_bytes=65536, max_events=1_000_001)


def test_max_events_allows_exactly_n_then_overflow_without_append() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536, max_events=3)
        await writer.start()
        for index in range(3):
            await writer.emit({"type": "usage_updated", "n": index})
        await writer.close()
        assert len(handle.records) == 3

        writer2 = EventWriter(handle, max_event_bytes=65536, max_events=2)
        await writer2.start()
        await writer2.emit({"type": "a"})
        await writer2.emit({"type": "b"})
        before = len(handle.records)
        with pytest.raises(EventWriterOverflow):
            await writer2.emit({"type": "c"})
        assert writer2.overflowed is True
        assert len(handle.records) == before
        with pytest.raises(EventWriterOverflow):
            writer2.emit_nowait({"type": "d"})
        assert len(handle.records) == before
        await writer2.close()

    asyncio.run(case())


def test_max_events_reservation_holds_under_queue_timeout_path() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        # No consumer started: the queue stays full so the second emit hits the
        # producer timeout path after reserving a slot.
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            max_events=2,
            queue_maxsize=1,
            producer_timeout_seconds=0.05,
        )
        await writer.emit({"type": "first"})
        with pytest.raises(EventWriterOverflow):
            await writer.emit({"type": "blocked"})
        assert writer.overflowed is True
        before = len(handle.records)
        with pytest.raises(EventWriterOverflow):
            writer.emit_nowait({"type": "after-overflow"})
        assert len(handle.records) == before

    asyncio.run(case())

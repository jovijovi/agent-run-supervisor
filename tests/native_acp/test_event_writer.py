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


class FailingAppendHandle:
    """Append raises after ``fail_after`` successful writes (consumer-failure tap)."""

    def __init__(self, *, fail_after: int = 0) -> None:
        self.records: list[tuple[str, dict]] = []
        self.calls = 0
        self.fail_after = fail_after
        self.error = OSError("injected consumer append failure")

    def append_ndjson(self, name: str, record: dict) -> None:
        self.calls += 1
        if self.calls > self.fail_after:
            raise self.error
        self.records.append((name, dict(record)))


def test_r4_b3_close_when_consumer_failed_queue_full_terminates_promptly() -> None:
    """Failed consumer + saturated queue must not hang close on sentinel put."""

    async def case() -> None:
        import warnings

        handle = FailingAppendHandle(fail_after=0)
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            queue_maxsize=2,
            max_events=100,
        )
        await writer.start()
        await writer.emit({"type": "first"})
        consumer = writer._consumer
        assert consumer is not None
        await asyncio.wait({consumer}, timeout=2.0)
        assert consumer.done()
        # Saturate the queue after the consumer died — no drain can free a slot.
        writer._queue.put_nowait({"type": "stuck-1"})
        writer._queue.put_nowait({"type": "stuck-2"})
        assert writer._queue.full()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
                await asyncio.wait_for(writer.close(), 1.0)
            # Deterministic cleanup: no pending consumer task reference.
            assert writer._consumer is None
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            assert pending == []

    asyncio.run(case())


def test_r4_b3_close_when_consumer_failed_queue_not_full_surfaces_error() -> None:
    async def case() -> None:
        import warnings

        handle = FailingAppendHandle(fail_after=0)
        writer = EventWriter(handle, max_event_bytes=65536, queue_maxsize=8)
        await writer.start()
        await writer.emit({"type": "first"})
        consumer = writer._consumer
        assert consumer is not None
        await asyncio.wait({consumer}, timeout=2.0)
        assert consumer.done()
        assert not writer._queue.full()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
                await asyncio.wait_for(writer.close(), 1.0)
            assert writer._consumer is None
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)

    asyncio.run(case())


def test_r4_b3_close_idempotent_after_successful_flush() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()
        await writer.emit({"type": "usage_updated"})
        await writer.close()
        assert len(handle.records) == 1
        await writer.close()  # idempotent; must not raise or leak
        assert writer._consumer is None

    asyncio.run(case())


def test_r4_b3_close_preserves_normal_flush_order() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536, queue_maxsize=4)
        await writer.start()
        for index in range(4):
            await writer.emit({"type": "tool_updated", "n": index})
        await writer.close()
        assert [record["n"] for _, record in handle.records] == [0, 1, 2, 3]
        assert writer._consumer is None

    asyncio.run(case())


def test_r5_b2_can_accept_requires_live_consumer_and_not_full_queue() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536, queue_maxsize=2)
        assert writer.can_accept is False  # no consumer yet
        await writer.start()
        assert writer.can_accept is True
        consumer = writer._consumer
        assert consumer is not None
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
        assert consumer.done()
        assert writer.can_accept is False
        with pytest.raises(EventWriterOverflow, match="consumer cancelled"):
            await writer.close()

        writer2 = EventWriter(
            RecordingHandle(), max_event_bytes=65536, queue_maxsize=1
        )
        await writer2.start()
        writer2._queue.put_nowait({"type": "pad"})
        assert writer2._queue.full()
        assert writer2.can_accept is False
        # Drain so close can finish.
        writer2._queue.get_nowait()
        await writer2.close()

    asyncio.run(case())


def test_r5_b2_can_accept_never_leaks_task_inspection_exceptions() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()

        class BoomTask:
            def done(self):
                raise RuntimeError("task probe boom")

        writer._consumer = BoomTask()  # type: ignore[assignment]
        assert writer.can_accept is False
        writer._consumer = None
        await writer.close()

    asyncio.run(case())


def test_r5_b2_consumer_queue_healthy_ignores_reserved_budget() -> None:
    async def case() -> None:
        writer = EventWriter(
            RecordingHandle(), max_event_bytes=65536, max_events=1, queue_maxsize=4
        )
        await writer.start()
        await writer.emit({"type": "only"})
        assert writer.can_accept is False  # reserved budget exhausted
        assert writer.consumer_queue_healthy is True  # consumer/queue still safe
        await writer.close()

    asyncio.run(case())


def test_r6_b1_emit_awaited_persists_before_return() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()
        await writer.emit_awaited({"type": "session_prompt_sent"})
        assert any(
            record.get("type") == "session_prompt_sent" for _, record in handle.records
        )
        await writer.close()

    asyncio.run(case())


def test_r6_b1_emit_awaited_consumer_failure_does_not_hang() -> None:
    async def case() -> None:
        import warnings

        handle = FailingAppendHandle(fail_after=0)
        writer = EventWriter(
            handle, max_event_bytes=65536, queue_maxsize=2, producer_timeout_seconds=0.5
        )
        await writer.start()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(EventWriterOverflow):
                await asyncio.wait_for(
                    writer.emit_awaited({"type": "session_prompt_sent"}), 1.0
                )
            assert writer.overflowed is True
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            # Consumer task may still be referenced until close; close cleans it.
            with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
                await writer.close()
            assert writer._consumer is None
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            assert pending == []

    asyncio.run(case())


def test_r6_b1_emit_awaited_queue_timeout_overflow() -> None:
    async def case() -> None:
        handle = GatedHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            queue_maxsize=1,
            producer_timeout_seconds=0.1,
        )
        await writer.start()
        try:
            # Live gated consumer holds the only queue slot after taking pad.
            await writer.emit({"type": "pad"})
            # Wait until consumer has dequeued pad and blocked in append.
            for _ in range(50):
                if writer._queue.empty():
                    break
                await asyncio.sleep(0.01)
            # Refill the single slot while consumer is still blocked.
            writer.emit_nowait({"type": "fill"})
            assert writer._queue.full()
            with pytest.raises(EventWriterOverflow):
                await asyncio.wait_for(
                    writer.emit_awaited({"type": "session_prompt_sent"}), 1.0
                )
            assert writer.overflowed is True
        finally:
            handle.gate.set()
            await writer.close()

    asyncio.run(case())


def test_r6_b1_emit_awaited_respects_max_events() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536, max_events=1)
        await writer.start()
        await writer.emit_awaited({"type": "only"})
        with pytest.raises(EventWriterOverflow):
            await writer.emit_awaited({"type": "session_prompt_sent"})
        assert writer.overflowed is True
        await writer.close()

    asyncio.run(case())


def test_r6_b1_emit_awaited_cancellation_cleans_pending() -> None:
    async def case() -> None:
        import warnings

        handle = GatedHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            queue_maxsize=1,
            producer_timeout_seconds=5.0,
        )
        await writer.start()
        await writer.emit({"type": "pad"})  # fill queue; consumer gated
        task = asyncio.ensure_future(
            writer.emit_awaited({"type": "session_prompt_sent"})
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(asyncio.CancelledError):
                await task
            handle.gate.set()
            await writer.close()
            assert writer._consumer is None
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                t
                for t in asyncio.all_tasks()
                if not t.done() and t is not asyncio.current_task()
            ]
            assert pending == []

    asyncio.run(case())


def test_r6_b1_byte_cap_uses_utf8_encoded_bytes() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        # Three multi-byte characters: char count 3, UTF-8 byte count 9.
        writer = EventWriter(handle, max_event_bytes=256)
        await writer.start()
        await writer.emit({"type": "agent_message_delta", "text": "你" * 200})
        await writer.close()
        record = handle.records[0][1]
        assert record["truncated"] is True
        assert record["truncate_reason"] == "max_event_bytes"
        rendered = json.dumps(record, sort_keys=True, ensure_ascii=False)
        assert len(rendered.encode("utf-8")) <= 256

    asyncio.run(case())


def test_r6_residual_emit_awaited_before_start_fails_promptly() -> None:
    async def case() -> None:
        import warnings

        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536, max_events=3)
        assert writer._consumer is None
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(EventWriterOverflow):
                await asyncio.wait_for(
                    writer.emit_awaited({"type": "session_prompt_sent"}), 0.5
                )
            assert writer.overflowed is True
            # Reservation for the failed emit must be released (not enqueued).
            assert writer._reserved == 0
            assert writer._queue.empty()
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            assert pending == []
        await writer.close()

    asyncio.run(case())


def test_r6_residual_emit_awaited_after_consumer_done_fails_promptly() -> None:
    async def case() -> None:
        import warnings

        handle = FailingAppendHandle(fail_after=0)
        writer = EventWriter(handle, max_event_bytes=65536, queue_maxsize=4)
        await writer.start()
        # Kill the consumer via a failed append, then clear overflow so the
        # residual path is "consumer already done" rather than closed/overflowed.
        with pytest.raises(EventWriterOverflow):
            await writer.emit_awaited({"type": "first"})
        consumer = writer._consumer
        assert consumer is not None and consumer.done()
        writer.overflowed = False
        reserved_before = writer._reserved
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(EventWriterOverflow):
                await asyncio.wait_for(
                    writer.emit_awaited({"type": "session_prompt_sent"}), 0.5
                )
            assert writer.overflowed is True
            assert writer._reserved == reserved_before  # not-enqueued reservation released
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            # Consumer task still referenced until close; no put/ack leaks.
            assert all(task is consumer for task in pending) or pending == []
        with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
            await writer.close()
        assert writer._consumer is None
        pending = [
            task
            for task in asyncio.all_tasks()
            if not task.done() and task is not asyncio.current_task()
        ]
        assert pending == []

    asyncio.run(case())


# -- R14: EventWriter.close ownership under cancellation ----------------------


def test_r14_close_cancel_while_sentinel_pending_retains_ownership() -> None:
    """Full queue + blocked append: cancel close once/repeatedly; ownership holds."""

    async def case() -> None:
        import warnings

        handle = GatedHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            queue_maxsize=1,
            producer_timeout_seconds=5.0,
        )
        await writer.start()
        await writer.emit({"type": "blocked-append"})
        # Wait until consumer is inside the gated to_thread append.
        for _ in range(200):
            if not handle.gate.is_set() and writer._queue.empty():
                # Consumer dequeued; may still be racing into append.
                await asyncio.sleep(0.01)
                break
            await asyncio.sleep(0.01)
        # Refill so sentinel enqueue must wait behind a full queue.
        writer.emit_nowait({"type": "queued-behind-append"})
        assert writer._queue.full()
        consumer = writer._consumer
        assert consumer is not None and not consumer.done()

        close_task = asyncio.ensure_future(writer.close())
        await asyncio.sleep(0.05)
        assert not close_task.done()
        close_task.cancel()
        await asyncio.sleep(0.05)
        assert not close_task.done(), "owned close must remain pending under cancel"
        assert writer._consumer is consumer
        assert not consumer.done()
        close_task.cancel()  # repeated cancellation
        await asyncio.sleep(0.05)
        assert not close_task.done()
        assert writer._consumer is consumer

        # No terminal/release surrogate can proceed while ownership is live:
        # the close task and consumer are still pending (join not settled).
        assert writer._close_task is not None and not writer._close_task.done()

        handle.gate.set()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(asyncio.CancelledError):
                await close_task
            assert writer._consumer is None
            assert writer._close_task is not None and writer._close_task.done()
            types = [record["type"] for _, record in handle.records]
            assert "blocked-append" in types
            assert "queued-behind-append" in types
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            assert pending == []

        # Idempotent second close observes settled success; no second sentinel.
        await writer.close()
        assert writer._consumer is None

    asyncio.run(case())


def test_r14_close_cancel_while_consumer_in_to_thread_append() -> None:
    """Cancel while consumer is inside controlled blocking append (to_thread)."""

    async def case() -> None:
        import warnings

        handle = GatedHandle()
        writer = EventWriter(handle, max_event_bytes=65536, queue_maxsize=4)
        await writer.start()
        await writer.emit({"type": "in-flight-append"})
        for _ in range(200):
            if writer._queue.empty():
                break
            await asyncio.sleep(0.01)

        close_task = asyncio.ensure_future(writer.close())
        await asyncio.sleep(0.05)
        assert not close_task.done()
        consumer = writer._consumer
        assert consumer is not None and not consumer.done()
        close_task.cancel()
        close_task.cancel()
        await asyncio.sleep(0.05)
        assert not close_task.done()
        assert writer._consumer is consumer

        before = len(handle.records)
        handle.gate.set()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(asyncio.CancelledError):
                await close_task
            assert writer._consumer is None
            assert len(handle.records) >= before + 0  # append settled
            assert any(
                record.get("type") == "in-flight-append" for _, record in handle.records
            )
            # No post-close append after cancellation propagated.
            after = len(handle.records)
            await asyncio.sleep(0.05)
            assert len(handle.records) == after
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            assert pending == []

    asyncio.run(case())


def test_r14_close_consumer_failure_race_is_controlled_overflow() -> None:
    async def case() -> None:
        import warnings

        handle = FailingAppendHandle(fail_after=0)
        writer = EventWriter(
            handle, max_event_bytes=65536, queue_maxsize=2, producer_timeout_seconds=0.5
        )
        await writer.start()
        await writer.emit({"type": "first"})
        consumer = writer._consumer
        assert consumer is not None
        await asyncio.wait({consumer}, timeout=2.0)
        assert consumer.done()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
                await asyncio.wait_for(writer.close(), 1.0)
            assert "injected consumer append failure" not in str(
                caught
            )  # no raw text via warnings
            assert writer._consumer is None
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            assert pending == []

        # Idempotent second close re-raises the settled failure; no new consumer.
        with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
            await writer.close()
        assert writer._consumer is None
        assert writer._close_task is not None
        # Still a single owned close task.
        first = writer._close_task
        with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
            await writer.close()
        assert writer._close_task is first

    asyncio.run(case())


def test_r14_close_cancel_races_failure_still_propagates_cancel() -> None:
    """Caller cancel racing a close failure: observe failure, raise CancelledError."""

    class GatedFailingHandle:
        """Block in append, then raise — cancel can land while ownership is live."""

        def __init__(self) -> None:
            self.gate = threading.Event()
            self.entered = threading.Event()

        def append_ndjson(self, name: str, record: dict) -> None:
            del name, record
            self.entered.set()
            assert self.gate.wait(timeout=30)
            raise OSError("injected consumer append failure")

    async def case() -> None:
        import warnings

        handle = GatedFailingHandle()
        writer = EventWriter(handle, max_event_bytes=65536, queue_maxsize=4)
        await writer.start()
        await writer.emit({"type": "will-fail"})
        for _ in range(200):
            if handle.entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert handle.entered.is_set()

        close_task = asyncio.ensure_future(writer.close())
        for _ in range(100):
            if writer._close_task is not None:
                break
            await asyncio.sleep(0)
        assert writer._close_task is not None
        assert not close_task.done()
        close_task.cancel()
        await asyncio.sleep(0.05)
        assert not close_task.done(), "close must stay pending while append blocked"
        handle.gate.set()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(asyncio.CancelledError):
                await close_task
            # Owned close settled a pipeline failure (observed via task result).
            assert writer._close_task.done()
            with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
                writer._close_task.result()
            assert writer._consumer is None
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)

    asyncio.run(case())

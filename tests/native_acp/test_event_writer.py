"""C7: per-Run bounded single-writer event stream (monotonic seq, queue
timeout signal, byte-cap truncation preserving critical families)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading

import pytest

from agent_run_supervisor.native_acp.event_writer import (
    EventWriter,
    EventWriterOverflow,
    QueuePolicy,
)


def fixed(capacity: int, *, timeout: float = 5.0) -> QueuePolicy:
    """A single-rung queue policy: exactly this capacity, and no expansion.

    These tests pin the writer's *bounded* behavior — sequencing, truncation,
    close ownership, consumer-failure handling — so they hold the queue at one
    known size. The dynamic ladder has its own suite
    (``test_evidence_queue_policy.py``); mixing the two in here would make
    every assertion depend on how many rungs a burst happened to earn.
    """
    return QueuePolicy(
        initial_event_capacity=capacity,
        max_event_capacity=capacity,
        initial_queued_bytes=8 * 1024 * 1024,
        max_queued_bytes=8 * 1024 * 1024,
        producer_timeout_seconds=timeout,
    )


class RecordingHandle:
    """Duck-typed RunHandle: records appended NDJSON records."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def append_text(self, name: str, value: str) -> None:
        self.records.append((name, json.loads(value)))


class GatedHandle(RecordingHandle):
    """Blocks every append until the gate is released (queue-pressure tap)."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.entered = threading.Event()

    def append_text(self, name: str, value: str) -> None:
        self.entered.set()
        self.gate.wait(timeout=30)
        super().append_text(name, value)


class ManualClock:
    """Monotonic clock whose scheduled callbacks run only when the test asks."""

    class Handle:
        def __init__(self, when, callback, args) -> None:
            self.when = when
            self.callback = callback
            self.args = args
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    def __init__(self) -> None:
        self.value = 0.0
        self.handles: list[ManualClock.Handle] = []

    def now(self) -> float:
        return self.value

    def call_at(self, when, callback, *args):
        handle = self.Handle(when, callback, args)
        self.handles.append(handle)
        return handle

    def advance_without_firing(self, seconds: float) -> None:
        self.value += seconds


async def _wait_thread_event(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 5), "controlled sink was never entered"


async def _wait_cancellation_absorbed(task: asyncio.Future) -> None:
    await asyncio.sleep(0)
    assert not task.done(), "caller cancellation escaped the owned close join"


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


def test_records_reach_the_stream_verbatim_under_their_ordinal(tmp_path) -> None:
    """The writer bounds and sequences; it does not rewrite field content.

    Driven through the **real** ``EventStore`` handle, so the assertion is on
    the bytes that land in ``events.jsonl`` rather than on a dict the writer
    happened to hand back. Child-authored strings — a tool call id here — reach
    the stream exactly as the normalizer produced them.
    """
    from agent_run_supervisor.event_store import EventStore

    async def case() -> None:
        handle = EventStore(tmp_path / "runs").create_run("run-verbatim")
        writer = EventWriter(handle, max_event_bytes=65536, filename="events.jsonl")
        await writer.start()
        await writer.emit(
            {"type": "tool_started", "tool_call_id": "call-writer-sentinel-6a12"}
        )
        await writer.emit({"type": "tool_updated", "status": "ok"})
        await writer.close()

        records = [
            json.loads(line)
            for line in (handle.run_dir / "events.jsonl").read_text().splitlines()
        ]
        assert [record["seq"] for record in records] == [1, 2]
        assert records[0]["tool_call_id"] == "call-writer-sentinel-6a12"
        assert all("withheld" not in record for record in records)

    asyncio.run(case())


def test_dynamic_child_keys_are_carried_without_suppressing_the_record() -> None:
    """A record with agent-chosen keys keeps its ordinal, family, and fields.

    Two dynamic keys used to be able to collapse onto one replacement token and
    suppress the whole enclosing record. With no replacement token there is no
    collision to arbitrate.
    """

    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()
        await writer.emit({"type": "tool_started", "alpha-6a12": 1, "beta-6a12": 2})
        await writer.emit({"type": "tool_updated", "status": "ok"})
        await writer.close()

        first, second = (record for _name, record in handle.records)
        assert first["seq"] == 1 and second["seq"] == 2
        assert first["type"] == "tool_started"
        assert first["alpha-6a12"] == 1
        assert first["beta-6a12"] == 2
        assert "withheld_reason" not in first

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
            policy=fixed(1, timeout=0.2),
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
            with pytest.raises(EventWriterOverflow):
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


def test_emit_ordered_from_sync_context_writes_in_order() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()
        # Sync producers (SDK callback context) use the non-blocking variant.
        writer.emit_ordered({"type": "run_started"})
        writer.emit_ordered({"type": "usage_updated"})
        await writer.close()
        assert [record["type"] for _, record in handle.records] == [
            "run_started",
            "usage_updated",
        ]

    asyncio.run(case())


def test_emit_ordered_full_queue_waits_then_signals_controlled_failure() -> None:
    """A full queue is a bounded *wait*, and only then a controlled failure.

    The sync producer surface is the SDK callback path. Failing it the instant
    the queue is full turns an ordinary burst into a dead Run, so it now hands
    back a bounded wait instead — and a sink that never drains still ends in
    the same controlled overflow, never a silent drop and never an unbounded
    buffer.
    """

    async def case() -> None:
        handle = GatedHandle()
        writer = EventWriter(
            handle, max_event_bytes=65536, policy=fixed(1, timeout=0.2)
        )
        await writer.start()
        try:
            # Park the consumer inside the gated durable append. V3 keeps that
            # in-flight ticket charged against the single admitted slot.
            await writer.emit({"type": "pad"})
            await _wait_thread_event(handle.entered)
            pending = writer.emit_ordered({"type": "usage_updated"})
            assert pending is not None, "a capacity-1 queue must defer"
            assert writer.overflowed is False  # deferral is not yet a failure
            with pytest.raises(EventWriterOverflow):
                await pending
            assert writer.overflowed is True
        finally:
            handle.gate.set()
            with pytest.raises(EventWriterOverflow):
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
            writer2.emit_ordered({"type": "d"})
        assert len(handle.records) == before
        with pytest.raises(EventWriterOverflow, match="max_events"):
            await writer2.close()

    asyncio.run(case())


def test_max_events_acceptance_count_holds_under_queue_timeout_path() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        # No consumer started: the queue stays full so the second emit hits the
        # producer timeout path after reserving a slot.
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            max_events=2,
            policy=fixed(1, timeout=0.05),
        )
        await writer.emit({"type": "first"})
        with pytest.raises(EventWriterOverflow):
            await writer.emit({"type": "blocked"})
        assert writer.overflowed is True
        before = len(handle.records)
        with pytest.raises(EventWriterOverflow):
            writer.emit_ordered({"type": "after-overflow"})
        assert len(handle.records) == before

    asyncio.run(case())


class FailingAppendHandle:
    """Append raises after ``fail_after`` successful writes (consumer-failure tap)."""

    def __init__(self, *, fail_after: int = 0) -> None:
        self.records: list[tuple[str, dict]] = []
        self.calls = 0
        self.fail_after = fail_after
        self.error = OSError("injected consumer append failure")

    def append_text(self, name: str, value: str) -> None:
        self.calls += 1
        if self.calls > self.fail_after:
            raise self.error
        self.records.append((name, json.loads(value)))


def test_r4_b3_close_when_consumer_failed_with_admitted_prefix_terminates() -> None:
    """Sink failure settles the whole admitted prefix and close joins it."""

    async def case() -> None:
        import warnings

        handle = FailingAppendHandle(fail_after=0)
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            policy=fixed(2),
            max_events=100,
        )
        await writer.start()
        assert writer.emit_ordered({"type": "first"}) is None
        assert writer.emit_ordered({"type": "second"}) is None
        pending = writer.emit_ordered({"type": "third"})
        assert pending is not None
        consumer = writer._consumer
        assert consumer is not None
        await asyncio.wait({consumer}, timeout=2.0)
        assert consumer.done()
        with pytest.raises(EventWriterOverflow):
            await pending
        assert writer.admitted_count == 0
        assert writer.waiting == 0

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
        writer = EventWriter(handle, max_event_bytes=65536, policy=fixed(8))
        await writer.start()
        await writer.emit({"type": "first"})
        consumer = writer._consumer
        assert consumer is not None
        await asyncio.wait({consumer}, timeout=2.0)
        assert consumer.done()
        assert writer.admitted_count == 0
        assert writer.waiting == 0

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
        writer = EventWriter(handle, max_event_bytes=65536, policy=fixed(4))
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
        writer = EventWriter(handle, max_event_bytes=65536, policy=fixed(2))
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
            GatedHandle(), max_event_bytes=65536, policy=fixed(1)
        )
        await writer2.start()
        await writer2.emit({"type": "pad"})
        handle2 = writer2._handle
        await _wait_thread_event(handle2.entered)
        assert writer2.admitted_count == 1
        assert writer2.can_accept is False
        handle2.gate.set()
        await writer2.close()

    asyncio.run(case())


def test_r5_b2_can_accept_is_false_after_owned_consumer_is_joined() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65536)
        await writer.start()

        await writer.close()
        assert writer._consumer is None
        assert writer.can_accept is False

    asyncio.run(case())


def test_r5_b2_consumer_queue_healthy_ignores_accepted_event_budget() -> None:
    async def case() -> None:
        writer = EventWriter(
            RecordingHandle(), max_event_bytes=65536, max_events=1, policy=fixed(4)
        )
        await writer.start()
        await writer.emit({"type": "only"})
        assert writer.can_accept is False  # accepted-event budget exhausted
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
            handle, max_event_bytes=65536, policy=fixed(2, timeout=0.5)
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
            policy=fixed(1, timeout=0.1),
        )
        await writer.start()
        try:
            # The in-flight append continues to hold the only admitted slot.
            await writer.emit({"type": "pad"})
            await _wait_thread_event(handle.entered)
            with pytest.raises(EventWriterOverflow):
                await asyncio.wait_for(
                    writer.emit_awaited({"type": "session_prompt_sent"}), 1.0
                )
            assert writer.overflowed is True
        finally:
            handle.gate.set()
            with pytest.raises(EventWriterOverflow):
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
        with pytest.raises(EventWriterOverflow, match="max_events"):
            await writer.close()

    asyncio.run(case())


def test_r6_b1_emit_awaited_cancellation_does_not_remove_ticket() -> None:
    async def case() -> None:
        import warnings

        handle = GatedHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            policy=fixed(1, timeout=5.0),
        )
        await writer.start()
        await writer.emit({"type": "pad"})
        await _wait_thread_event(handle.entered)
        task = asyncio.ensure_future(
            writer.emit_awaited({"type": "session_prompt_sent"})
        )
        for _ in range(20):
            if writer.waiting == 1:
                break
            await asyncio.sleep(0)
        assert writer.waiting == 1
        task.cancel()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(asyncio.CancelledError):
                await task
            handle.gate.set()
            await writer.close()
            assert writer._consumer is None
            assert [record["type"] for _, record in handle.records] == [
                "pad",
                "session_prompt_sent",
            ]
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


def test_byte_cap_measures_the_bytes_that_are_actually_persisted(tmp_path) -> None:
    """The cap governs the durable NDJSON line, not a different serialization.

    The run handle serializes with ``json.dumps(..., sort_keys=True)`` — ASCII
    escaped — and appends a newline, and the record it receives already carries
    ``seq``. A writer that measured a non-escaped, seq-less rendering would be
    measuring a string nobody persists: a field of non-ASCII characters costs
    three bytes there and six on disk, so a record can pass the cap and land
    over it, untruncated and unaccounted.

    Driven through the real ``EventStore`` so the assertion is on the bytes in
    the file rather than on any in-process rendering of them.
    """
    from agent_run_supervisor.event_store import EventStore

    async def case() -> None:
        handle = EventStore(tmp_path / "runs").create_run("run-byte-boundary")
        writer = EventWriter(handle, max_event_bytes=256, filename="events.jsonl")
        await writer.start()
        # 40 non-ASCII characters: 120 UTF-8 bytes unescaped, 240 escaped.
        await writer.emit({"type": "tool_updated", "tool_call_id": "\u4e2d" * 40})
        await writer.close()

        raw = (handle.run_dir / "events.jsonl").read_bytes()
        record = json.loads(raw)
        assert len(raw) <= 256, (
            f"persisted line is {len(raw)} bytes, over the 256-byte cap"
        )
        assert record["truncated"] is True
        assert record["truncate_reason"] == "max_event_bytes"
        assert record["type"] == "tool_updated"  # family preserved
        assert record["seq"] == 1

    asyncio.run(case())


def test_a_record_that_fits_the_persisted_cap_is_not_truncated(tmp_path) -> None:
    """The measurement must not over-truncate either: a record that fits on
    disk keeps every field it was given."""
    from agent_run_supervisor.event_store import EventStore

    async def case() -> None:
        handle = EventStore(tmp_path / "runs").create_run("run-byte-fits")
        writer = EventWriter(handle, max_event_bytes=256, filename="events.jsonl")
        await writer.start()
        await writer.emit({"type": "tool_updated", "tool_call_id": "\u4e2d" * 20})
        await writer.close()

        raw = (handle.run_dir / "events.jsonl").read_bytes()
        record = json.loads(raw)
        assert len(raw) <= 256
        assert "truncated" not in record
        assert record["tool_call_id"] == "\u4e2d" * 20

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
            assert writer._accepted == 0
            assert writer.admitted_count == 0
            assert writer.waiting == 0
            assert not any(issubclass(w.category, ResourceWarning) for w in caught)
            pending = [
                task
                for task in asyncio.all_tasks()
                if not task.done() and task is not asyncio.current_task()
            ]
            assert pending == []
        with pytest.raises(EventWriterOverflow, match="no consumer"):
            await writer.close()

    asyncio.run(case())


def test_r6_residual_emit_awaited_after_consumer_done_fails_promptly() -> None:
    async def case() -> None:
        import warnings

        handle = FailingAppendHandle(fail_after=0)
        writer = EventWriter(handle, max_event_bytes=65536, policy=fixed(4))
        await writer.start()
        # Kill the consumer via a failed append. Failure is absorbing: a later
        # emit observes the same ledger truth and cannot reset it.
        with pytest.raises(EventWriterOverflow):
            await writer.emit_awaited({"type": "first"})
        consumer = writer._consumer
        assert consumer is not None and consumer.done()
        accepted_before = writer._accepted
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(EventWriterOverflow):
                await asyncio.wait_for(
                    writer.emit_awaited({"type": "session_prompt_sent"}), 0.5
                )
            assert writer.overflowed is True
            assert writer._accepted == accepted_before
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


def test_r14_close_cancel_with_pending_ticket_retains_ownership() -> None:
    """Blocked append + pending ticket: repeated close cancellation only observes."""

    async def case() -> None:
        import warnings

        handle = GatedHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65536,
            policy=fixed(1, timeout=5.0),
        )
        await writer.start()
        await writer.emit({"type": "blocked-append"})
        await _wait_thread_event(handle.entered)
        observation = writer.emit_ordered({"type": "queued-behind-append"})
        assert observation is not None
        assert writer.waiting == 1
        consumer = writer._consumer
        assert consumer is not None and not consumer.done()

        close_task = asyncio.ensure_future(writer.close())
        try:
            assert writer._close_task is not None and not writer._close_task.done()
            close_task.cancel()
            await _wait_cancellation_absorbed(close_task)
            assert not close_task.done()
            assert writer._consumer is consumer

            close_task.cancel()
            await _wait_cancellation_absorbed(close_task)
            assert not close_task.done()
            assert writer._consumer is consumer

            handle.gate.set()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with pytest.raises(asyncio.CancelledError):
                    await close_task
                await observation
                assert writer._consumer is None
                assert writer._close_task is not None and writer._close_task.done()
                assert [record["type"] for _, record in handle.records] == [
                    "blocked-append",
                    "queued-behind-append",
                ]
                assert not any(
                    issubclass(w.category, ResourceWarning) for w in caught
                )

            # Idempotent second close replays settled success; no control token
            # or consumer is created a second time.
            await writer.close()
            assert writer._consumer is None
        finally:
            handle.gate.set()

    asyncio.run(case())


def test_r14_close_cancel_while_consumer_in_to_thread_append() -> None:
    """Cancel while consumer is inside controlled blocking append (to_thread)."""

    async def case() -> None:
        import warnings

        handle = GatedHandle()
        writer = EventWriter(handle, max_event_bytes=65536, policy=fixed(4))
        await writer.start()
        await writer.emit({"type": "in-flight-append"})
        await _wait_thread_event(handle.entered)

        close_task = asyncio.ensure_future(writer.close())
        try:
            assert not close_task.done()
            consumer = writer._consumer
            assert consumer is not None and not consumer.done()
            close_task.cancel()
            close_task.cancel()
            await _wait_cancellation_absorbed(close_task)
            assert not close_task.done()
            assert writer._consumer is consumer

            handle.gate.set()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with pytest.raises(asyncio.CancelledError):
                    await close_task
                assert writer._consumer is None
                assert [record["type"] for _, record in handle.records] == [
                    "in-flight-append"
                ]
                assert not any(
                    issubclass(w.category, ResourceWarning) for w in caught
                )
        finally:
            handle.gate.set()

    asyncio.run(case())


def test_r14_close_consumer_failure_race_is_controlled_overflow() -> None:
    async def case() -> None:
        import warnings

        handle = FailingAppendHandle(fail_after=0)
        writer = EventWriter(
            handle, max_event_bytes=65536, policy=fixed(2, timeout=0.5)
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

        def append_text(self, name: str, value: str) -> None:
            del name, value
            self.entered.set()
            assert self.gate.wait(timeout=30)
            raise OSError("injected consumer append failure")

    async def case() -> None:
        import warnings

        handle = GatedFailingHandle()
        writer = EventWriter(handle, max_event_bytes=65536, policy=fixed(4))
        await writer.start()
        await writer.emit({"type": "will-fail"})
        await _wait_thread_event(handle.entered)

        close_task = asyncio.ensure_future(writer.close())
        try:
            assert writer._close_task is not None
            assert not close_task.done()
            close_task.cancel()
            await _wait_cancellation_absorbed(close_task)
            assert not close_task.done(), "close must stay pending while append blocked"
            handle.gate.set()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with pytest.raises(asyncio.CancelledError):
                    await close_task
                # Owned close settled a pipeline failure (observed via result).
                assert writer._close_task.done()
                with pytest.raises(
                    EventWriterOverflow, match="event evidence append failed"
                ):
                    writer._close_task.result()
                assert writer._consumer is None
                assert not any(
                    issubclass(w.category, ResourceWarning) for w in caught
                )
        finally:
            handle.gate.set()

    asyncio.run(case())


# -- EventWriter V3 bounded serial ledger ------------------------------------


def test_v3_actual_sequence_wire_defeats_synthetic_sequence_inversion(tmp_path) -> None:
    """A seq-1 record that fits is never truncated because max_events is wider."""
    from agent_run_supervisor.event_store import EventStore, ndjson_line

    async def case() -> None:
        event = None
        cap = 0
        for pad_length in range(128, 1024):
            candidate = {"type": "tool_updated", "pad": "x" * pad_length}
            actual = len(ndjson_line({**candidate, "seq": 1}).encode("utf-8"))
            synthetic = len(
                ndjson_line({**candidate, "seq": 1_000_000}).encode("utf-8")
            )
            if actual >= 256 and synthetic > actual:
                event = candidate
                cap = actual
                break
        assert event is not None

        handle = EventStore(tmp_path / "runs").create_run("run-v3-actual-seq")
        writer = EventWriter(
            handle,
            max_event_bytes=cap,
            max_events=1_000_000,
            filename="events.jsonl",
        )
        await writer.start()
        await writer.emit(event)
        await writer.close()

        expected = ndjson_line({**event, "seq": 1}).encode("utf-8")
        actual = (handle.run_dir / "events.jsonl").read_bytes()
        assert actual == expected
        assert len(actual) == cap
        assert b'"truncated"' not in actual
        assert writer.last_seq == 1

    asyncio.run(case())


def test_v3_deadline_first_pump_expires_head_when_ack_runs_before_late_timer() -> None:
    """A durable ack cannot admit an overdue head while its timer is delayed."""

    async def case() -> None:
        handle = GatedHandle()
        clock = ManualClock()
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            policy=fixed(1, timeout=5.0),
        )
        writer._now = clock.now
        writer._call_at = clock.call_at
        await writer.start()
        await writer.emit({"type": "in-flight"})
        await _wait_thread_event(handle.entered)

        pending = None
        ordinal = 0
        while pending is None:
            ordinal += 1
            pending = writer.emit_ordered({"type": "pending", "ordinal": ordinal})
            assert ordinal < 4, "the fixed rung must eventually defer"

        clock.advance_without_firing(5.0)
        handle.gate.set()  # durable ack calls pump before the delayed timer callback
        with pytest.raises(EventWriterOverflow, match="producer timeout"):
            await pending
        with pytest.raises(EventWriterOverflow):
            await writer.close()
        assert not any(record.get("ordinal") == ordinal for _, record in handle.records)

    asyncio.run(case())


def test_v3_phase_observers_project_synchronous_and_existing_ticket_state() -> None:
    """Pre-pump and late observers both see the same ledger-owned phase facts."""

    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65_536)

        # _submit installs its observer before the first pump; admission can then
        # complete synchronously without losing the wakeup.
        assert writer._submit({"type": "before-observer-return"}, "admission") is None
        ticket = writer._admitted[0]
        await writer._observe(ticket, "admission")

        await writer.start()
        await writer.close()
        # Registering after durable completion is an immediate value projection.
        await writer._observe(ticket, "persistence")
        assert handle.records[0][1]["type"] == "before-observer-return"

    asyncio.run(case())


def test_v3_cancelling_observer_never_cancels_or_removes_accepted_ticket() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            policy=fixed(1, timeout=5.0),
        )
        await writer.emit({"type": "first"})
        observation = writer.emit_ordered({"type": "survives-caller-cancel"})
        assert observation is not None

        caller = asyncio.ensure_future(observation)
        await asyncio.sleep(0)  # start the observer edge; no wall-clock race
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        await writer.start()
        await writer.close()
        assert [record["type"] for _, record in handle.records] == [
            "first",
            "survives-caller-cancel",
        ]
        assert writer.last_seq == 2

    asyncio.run(case())


def test_v3_in_flight_append_stays_charged_until_durable_ack() -> None:
    from agent_run_supervisor.event_store import ndjson_line

    async def case() -> None:
        handle = GatedHandle()
        writer = EventWriter(handle, max_event_bytes=65_536, policy=fixed(4))
        await writer.start()
        event = {"type": "held-through-fsync", "text": "three bytes: \u4e2d"}
        await writer.emit(event)
        await _wait_thread_event(handle.entered)
        try:
            expected_bytes = len(ndjson_line({**event, "seq": 1}).encode("utf-8"))
            assert writer.queued_bytes == expected_bytes
            assert writer.admitted_count == 1
            assert writer.last_seq == 0
        finally:
            handle.gate.set()
            with contextlib.suppress(EventWriterOverflow):
                await writer.close()

        assert writer.queued_bytes == 0
        assert writer.admitted_count == 0
        assert writer.last_seq == 1

    asyncio.run(case())


def test_v3_sink_failure_settles_every_admitted_persistence_observer() -> None:
    async def case() -> None:
        writer = EventWriter(
            FailingAppendHandle(fail_after=0),
            max_event_bytes=65_536,
            policy=fixed(4),
        )
        await writer.start()
        observations = [
            writer._submit({"type": "must-settle", "n": index}, "persistence")
            for index in range(4)
        ]
        assert all(observation is not None for observation in observations)

        outcomes = await asyncio.gather(*observations, return_exceptions=True)
        assert len(outcomes) == 4
        assert all(isinstance(outcome, EventWriterOverflow) for outcome in outcomes)
        with pytest.raises(EventWriterOverflow, match="event evidence append failed"):
            await writer.close()

    asyncio.run(case())


def test_v3_close_cutoff_is_synchronous_and_cannot_drop_prior_evidence() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65_536)
        await writer.start()
        writer.emit_ordered({"type": "before-close"})

        closing = writer.close()  # cutoff is established by this call, not its await
        accepted_after_cutoff = False
        try:
            writer.emit_ordered({"type": "after-close"})
            accepted_after_cutoff = True
        except EventWriterOverflow:
            pass
        await closing

        assert accepted_after_cutoff is False
        assert [record["type"] for _, record in handle.records] == ["before-close"]

    asyncio.run(case())


def test_v3_timed_out_close_still_joins_and_observes_live_consumer() -> None:
    async def case() -> None:
        handle = GatedHandle()
        clock = ManualClock()
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            policy=fixed(1, timeout=5.0),
        )
        writer._now = clock.now
        writer._call_at = clock.call_at
        await writer.start()
        await writer.emit({"type": "lower-prefix"})
        await _wait_thread_event(handle.entered)

        pending = None
        while pending is None:
            pending = writer.emit_ordered({"type": "must-expire"})
        clock.advance_without_firing(5.0)

        close_task = asyncio.ensure_future(writer.close())
        await asyncio.sleep(0)
        assert not close_task.done(), "close must join the blocked lower prefix"
        consumer = writer._consumer
        assert consumer is not None and not consumer.done()

        handle.gate.set()
        with pytest.raises(EventWriterOverflow):
            await close_task
        assert writer._consumer is None
        assert consumer.done()
        with pytest.raises(EventWriterOverflow):
            await pending

    asyncio.run(case())


def test_v3_empty_no_consumer_is_clean_but_nonempty_settles_failed_persistence() -> None:
    async def case() -> None:
        empty = EventWriter(RecordingHandle(), max_event_bytes=65_536)
        await empty.close()
        assert empty.last_seq == 0

        nonempty = EventWriter(RecordingHandle(), max_event_bytes=65_536)
        assert nonempty.emit_ordered({"type": "accepted-without-consumer"}) is None
        persistence = nonempty._observe(nonempty._admitted[0], "persistence")
        with pytest.raises(EventWriterOverflow, match="no consumer"):
            await nonempty.close()
        with pytest.raises(EventWriterOverflow, match="no consumer"):
            await persistence
        assert nonempty.last_seq == 0

    asyncio.run(case())


def test_v3_event_writer_calls_text_seam_with_canonical_string() -> None:
    class TextOnlyHandle:
        def __init__(self) -> None:
            self.values: list[tuple[str, str]] = []

        def append_text(self, name: str, value: str) -> None:
            assert isinstance(value, str)
            self.values.append((name, value))

    async def case() -> None:
        from agent_run_supervisor.event_store import ndjson_line

        handle = TextOnlyHandle()
        writer = EventWriter(handle, max_event_bytes=65_536)
        await writer.start()
        await writer.emit({"type": "canonical-text"})
        await writer.close()
        assert handle.values == [
            ("normalized-events.jsonl", ndjson_line({"type": "canonical-text", "seq": 1}))
        ]

    asyncio.run(case())


def test_v3_consumer_task_creation_failure_closes_unowned_coroutine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed task handoff leaves no coroutine outside ledger ownership."""

    async def case() -> None:
        captured = []

        def fail_create_task(coroutine, *, name=None):
            del name
            captured.append(coroutine)
            raise RuntimeError("injected consumer task creation failure")

        monkeypatch.setattr(asyncio, "create_task", fail_create_task)
        writer = EventWriter(RecordingHandle(), max_event_bytes=65_536)
        with pytest.raises(EventWriterOverflow, match="consumer could not start"):
            await writer.start()

        assert len(captured) == 1
        try:
            assert captured[0].cr_frame is None, "unowned consumer coroutine stayed open"
        finally:
            # Keep the RED run warning-free even when the assertion proves the
            # old implementation left the coroutine open.
            captured[0].close()
        assert writer.overflowed is True
        with pytest.raises(EventWriterOverflow, match="consumer could not start"):
            await writer.close()

    asyncio.run(case())

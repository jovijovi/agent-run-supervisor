"""Dynamic per-Run evidence queue policy (bounded, growable, ordered).

The Run's evidence queue is the one place where a healthy high-frequency Turn
and a broken persistence sink look the same from the producer side. The policy
pinned here keeps them distinguishable:

- capacity grows only through the approved ladder, only for a **live** consumer
  that is **making persistence progress**, and never past the maximum;
- event count and queued bytes are two independent ceilings;
- a producer that cannot be admitted waits **boundedly and in wire order**
  rather than failing immediately or buffering without limit;
- a stalled or failed consumer is never hidden by expansion — it reaches the
  bounded overflow signal that makes the Run fail closed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from types import SimpleNamespace

import pytest

from agent_run_supervisor.event_store import ndjson_line
from agent_run_supervisor.native_acp.run_task import RunTask, _PendingEmit
from agent_run_supervisor.native_acp.event_writer import (
    DEFAULT_PRODUCER_TIMEOUT_SECONDS,
    DEFAULT_QUEUE_POLICY,
    INITIAL_EVENT_CAPACITY,
    INITIAL_QUEUED_BYTES,
    MAX_EVENT_CAPACITY,
    MAX_QUEUED_BYTES,
    QUEUE_GROWTH_FACTOR,
    EventWriter,
    EventWriterOverflow,
    QueuePolicy,
)

MIB = 1024 * 1024


class RecordingHandle:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def append_text(self, name: str, value: str) -> None:
        self.records.append((name, json.loads(value)))


class GatedHandle(RecordingHandle):
    """Every append blocks until the gate opens (stalled-consumer tap)."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.entered = threading.Event()

    def append_text(self, name: str, value: str) -> None:
        self.entered.set()
        assert self.gate.wait(timeout=30)
        super().append_text(name, value)


class SlowHandle(RecordingHandle):
    """Appends succeed but take real time (slow-but-progressing sink)."""

    def __init__(self, delay: float = 0.002) -> None:
        super().__init__()
        self.delay = delay

    def append_text(self, name: str, value: str) -> None:
        import time

        time.sleep(self.delay)
        super().append_text(name, value)


async def _wait_thread_event(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 5), "controlled sink was never entered"


# -- approved policy ---------------------------------------------------------


def test_approved_ladder_is_the_shared_constant_set() -> None:
    assert INITIAL_EVENT_CAPACITY == 1024
    assert QUEUE_GROWTH_FACTOR == 2
    assert MAX_EVENT_CAPACITY == 8192
    assert INITIAL_QUEUED_BYTES == 8 * MIB
    assert MAX_QUEUED_BYTES == 64 * MIB
    assert DEFAULT_PRODUCER_TIMEOUT_SECONDS == 5.0

    assert DEFAULT_QUEUE_POLICY.event_capacities() == (1024, 2048, 4096, 8192)
    assert DEFAULT_QUEUE_POLICY.queued_byte_budgets() == (
        8 * MIB,
        16 * MIB,
        32 * MIB,
        64 * MIB,
    )
    assert DEFAULT_QUEUE_POLICY.producer_timeout_seconds == 5.0


def test_a_new_writer_starts_at_the_initial_rung() -> None:
    writer = EventWriter(RecordingHandle(), max_event_bytes=65_536)
    assert writer.queue_capacity == INITIAL_EVENT_CAPACITY
    assert writer.queued_byte_budget == INITIAL_QUEUED_BYTES
    assert writer.expansions == 0
    assert writer.queued_bytes == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_event_capacity": 0},
        {"growth_factor": 1},
        {"max_event_capacity": 512},  # below the initial rung
        {"max_event_capacity": 1500},  # not reachable by the growth factor
        {"max_queued_bytes": 3 * INITIAL_QUEUED_BYTES},  # ladder length mismatch
        {"initial_queued_bytes": 0},
        {"producer_timeout_seconds": 0},
        {"producer_timeout_seconds": -1.0},
    ],
    ids=[
        "zero-initial-capacity",
        "growth-factor-one",
        "max-below-initial",
        "max-not-on-the-ladder",
        "byte-ladder-length-mismatch",
        "zero-initial-bytes",
        "zero-timeout",
        "negative-timeout",
    ],
)
def test_policy_validation_refuses_incoherent_configuration(kwargs) -> None:
    with pytest.raises(ValueError):
        QueuePolicy(**kwargs)


def test_a_single_bounded_event_always_fits_the_initial_byte_budget() -> None:
    # No-deadlock invariant: the per-event cap can never exceed the smallest
    # queued byte budget, so an empty queue always has room for one event.
    from agent_run_supervisor.native_acp.spec import LIMIT_MAX_EVENT_BYTES_MAX

    assert LIMIT_MAX_EVENT_BYTES_MAX < INITIAL_QUEUED_BYTES


# -- growth ------------------------------------------------------------------


def _ladder_policy(**overrides) -> QueuePolicy:
    kwargs = dict(
        initial_event_capacity=4,
        max_event_capacity=32,
        growth_factor=2,
        initial_queued_bytes=4096,
        max_queued_bytes=32_768,
        producer_timeout_seconds=5.0,
    )
    kwargs.update(overrides)
    return QueuePolicy(**kwargs)


def test_burst_grows_through_the_ladder_for_a_progressing_consumer() -> None:
    async def case() -> None:
        handle = SlowHandle(delay=0.002)
        policy = _ladder_policy()
        writer = EventWriter(
            handle, max_event_bytes=65_536, max_events=1000, policy=policy
        )
        await writer.start()
        capacities: list[int] = []
        for index in range(200):
            await writer.emit({"type": "tool_updated", "n": index})
            capacities.append(writer.queue_capacity)
        await writer.close()

        assert writer.expansions >= 1
        assert max(capacities) == policy.max_event_capacity
        # Only approved rungs are ever occupied, and capacity is monotonic.
        assert set(capacities) <= set(policy.event_capacities())
        assert capacities == sorted(capacities)
        # Backpressure is never a drop: every event is persisted, in order.
        assert [record["n"] for _, record in handle.records] == list(range(200))
        assert [record["seq"] for _, record in handle.records] == list(
            range(1, 201)
        )

    asyncio.run(case())


def test_the_production_policy_ladder_is_walked_under_a_real_burst() -> None:
    """The approved ladder, not just a miniature of it, expands under load.

    Driven with the shipped :data:`DEFAULT_QUEUE_POLICY` so the numbers under
    test are the ones a Run actually gets: a burst past the 1024 initial rung,
    against a sink slow enough to fall behind, must expand rather than fail —
    and must still persist every event, in order.
    """

    async def case() -> None:
        handle = SlowHandle(delay=0.0002)
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            max_events=6000,
            policy=DEFAULT_QUEUE_POLICY,
        )
        await writer.start()
        total = 4 * INITIAL_EVENT_CAPACITY
        for index in range(total):
            await writer.emit({"type": "tool_updated", "n": index})
        assert writer.expansions >= 1
        assert writer.queue_capacity > INITIAL_EVENT_CAPACITY
        assert writer.queue_capacity in DEFAULT_QUEUE_POLICY.event_capacities()
        assert writer.queued_byte_budget in DEFAULT_QUEUE_POLICY.queued_byte_budgets()
        await writer.close()
        assert [record["n"] for _, record in handle.records] == list(range(total))

    asyncio.run(case())


def test_expansion_requires_persistence_progress_not_just_pressure() -> None:
    """A stalled consumer must not be hidden by expansion."""

    async def case() -> None:
        handle = GatedHandle()
        policy = _ladder_policy(producer_timeout_seconds=0.2)
        writer = EventWriter(
            handle, max_event_bytes=65_536, max_events=1000, policy=policy
        )
        await writer.start()
        try:
            with pytest.raises(EventWriterOverflow):
                for index in range(200):
                    await writer.emit({"type": "usage_updated", "n": index})
            # Nothing was ever appended, so no rung was ever earned.
            assert writer.expansions == 0
            assert writer.queue_capacity == policy.initial_event_capacity
            assert writer.overflowed is True
        finally:
            # Releasing the gate lets the live consumer flush what it did
            # accept: the overflow signal is the producer's, not the sink's.
            handle.gate.set()
            with pytest.raises(EventWriterOverflow, match="producer timeout"):
                await writer.close()

    asyncio.run(case())


def test_expansion_refuses_once_the_consumer_is_gone() -> None:
    class FailingHandle(RecordingHandle):
        def append_text(self, name: str, value: str) -> None:
            del name, value
            raise OSError("injected consumer append failure")

    async def case() -> None:
        writer = EventWriter(
            FailingHandle(),
            max_event_bytes=65_536,
            policy=_ladder_policy(producer_timeout_seconds=0.2),
        )
        await writer.start()
        with pytest.raises(EventWriterOverflow):
            for _ in range(200):
                await writer.emit({"type": "usage_updated"})
        assert writer.expansions == 0
        with pytest.raises(EventWriterOverflow):
            await writer.close()

    asyncio.run(case())


# -- two independent ceilings ------------------------------------------------


def test_queued_byte_ceiling_binds_while_the_count_ceiling_is_far_away() -> None:
    async def case() -> None:
        handle = GatedHandle()
        policy = QueuePolicy(
            initial_event_capacity=64,
            max_event_capacity=256,
            initial_queued_bytes=2048,
            max_queued_bytes=8192,
            producer_timeout_seconds=0.2,
        )
        writer = EventWriter(
            handle, max_event_bytes=1024, max_events=1000, policy=policy
        )
        # No consumer: admit synchronously until the current byte rung, not the
        # much larger count rung, forces one ticket into the pending FIFO.
        pending = None
        for index in range(64):
            pending = writer.emit_ordered(
                {"type": "usage_updated", "pad": "x" * 400, "n": index}
            )
            if pending is not None:
                break
        assert pending is not None
        assert writer.admitted_count < policy.initial_event_capacity
        assert writer.queued_bytes <= policy.initial_queued_bytes
        assert writer.waiting == 1
        assert writer.overflowed is False
        with pytest.raises(EventWriterOverflow, match="no consumer"):
            await writer.close()
        with pytest.raises(EventWriterOverflow):
            await pending

    asyncio.run(case())


def test_event_count_ceiling_binds_while_the_byte_ceiling_is_far_away() -> None:
    async def case() -> None:
        policy = QueuePolicy(
            initial_event_capacity=4,
            max_event_capacity=4,
            initial_queued_bytes=1024 * 1024,
            max_queued_bytes=1024 * 1024,
            producer_timeout_seconds=0.2,
        )
        writer = EventWriter(RecordingHandle(), max_event_bytes=65_536, policy=policy)
        for _ in range(policy.initial_event_capacity):
            assert writer.emit_ordered({"type": "usage_updated"}) is None
        pending = writer.emit_ordered({"type": "usage_updated"})
        assert pending is not None
        assert writer.admitted_count == 4
        assert writer.waiting == 1
        assert writer.queued_bytes < policy.initial_queued_bytes
        assert writer.overflowed is False
        with pytest.raises(EventWriterOverflow, match="no consumer"):
            await writer.close()
        with pytest.raises(EventWriterOverflow):
            await pending

    asyncio.run(case())


# -- bound + size before admission -------------------------------------------


def test_events_are_bounded_and_sized_before_admission() -> None:
    """The accounted size is the durable line's size, measured before admission.

    Not a second rendering of the record, and not the pre-truncation size the
    consumer would have discarded: the number the byte budget spends has to be
    the number of bytes the stream will actually hold.
    """

    async def case() -> None:
        writer = EventWriter(
            RecordingHandle(),
            max_event_bytes=256,
            max_events=500,
            policy=_ladder_policy(),
        )
        # No consumer: the admitted entry stays queued and its accounted size
        # must be the *bounded* size, not the original 10 KiB.
        await writer.emit({"type": "run_failed", "detail": "x" * 10_000})
        assert writer.admitted_count == 1
        assert 0 < writer.queued_bytes <= 256

        # The exact contract: the store's own serialization of the truncated
        # record carries its actual accepted sequence and newline.
        expected = ndjson_line(
            {
                "type": "run_failed",
                "truncated": True,
                "truncate_reason": "max_event_bytes",
                "seq": 1,
            }
        )
        assert writer.queued_bytes == len(expected.encode("utf-8"))
        ticket = writer._admitted[0]
        assert ticket.wire == expected
        assert ticket.nbytes == writer.queued_bytes
        with pytest.raises(EventWriterOverflow, match="no consumer"):
            await writer.close()

    asyncio.run(case())


# -- ordered bounded waiting -------------------------------------------------


def test_deferred_admission_preserves_wire_order() -> None:
    async def case() -> None:
        handle = GatedHandle()
        # A stalled consumer earns no expansion, so the queue stays at its
        # single initial slot while three producers queue up behind it.
        policy = QueuePolicy(
            initial_event_capacity=1,
            max_event_capacity=8,
            initial_queued_bytes=1024 * 1024,
            max_queued_bytes=8 * 1024 * 1024,
            producer_timeout_seconds=10.0,
        )
        writer = EventWriter(handle, max_event_bytes=65_536, policy=policy)
        await writer.start()
        await writer.emit({"type": "e", "n": 0})
        await _wait_thread_event(handle.entered)
        observations = [
            writer.emit_ordered({"type": "e", "n": index})
            for index in (1, 2, 3, 4)
        ]
        assert all(observation is not None for observation in observations)
        assert writer.waiting == 4

        handle.gate.set()
        await asyncio.gather(*observations)
        await writer.close()
        assert [record["n"] for _, record in handle.records] == [0, 1, 2, 3, 4]

    asyncio.run(case())


def test_cancelling_an_admission_observer_loses_neither_event_nor_budget() -> None:
    """Cancellation changes only the disposable observer edge."""

    async def case() -> None:
        handle = GatedHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            max_events=2,
            policy=QueuePolicy(
                initial_event_capacity=1,
                max_event_capacity=1,
                initial_queued_bytes=1024 * 1024,
                max_queued_bytes=1024 * 1024,
                producer_timeout_seconds=10.0,
            ),
        )
        await writer.start()
        await writer.emit({"type": "pad"})
        await _wait_thread_event(handle.entered)
        observation = writer.emit_ordered({"type": "deferred"})
        assert observation is not None
        assert writer.waiting == 1
        task = asyncio.ensure_future(observation)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert writer._accepted == 2
        assert writer.waiting == 1

        handle.gate.set()
        await writer.close()
        assert [record["type"] for _, record in handle.records] == [
            "pad",
            "deferred",
        ]

    asyncio.run(case())


def test_emit_ordered_is_synchronous_on_the_fast_path() -> None:
    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65_536)
        await writer.start()
        assert writer.emit_ordered({"type": "run_started"}) is None
        assert writer.emit_ordered({"type": "usage_updated"}) is None
        await writer.close()
        assert [record["type"] for _, record in handle.records] == [
            "run_started",
            "usage_updated",
        ]

    asyncio.run(case())


def test_emit_ordered_returns_a_bounded_wait_when_the_queue_is_full() -> None:
    async def case() -> None:
        handle = GatedHandle()
        policy = QueuePolicy(
            initial_event_capacity=1,
            max_event_capacity=1,
            initial_queued_bytes=1024 * 1024,
            max_queued_bytes=1024 * 1024,
            producer_timeout_seconds=0.2,
        )
        writer = EventWriter(handle, max_event_bytes=65_536, policy=policy)
        await writer.start()
        await writer.emit({"type": "pad"})
        await _wait_thread_event(handle.entered)
        pending = writer.emit_ordered({"type": "must-wait"})
        assert pending is not None
        with pytest.raises(EventWriterOverflow):
            await pending
        assert writer.overflowed is True
        handle.gate.set()
        with pytest.raises(EventWriterOverflow, match="producer timeout"):
            await writer.close()

    asyncio.run(case())


class FirstAppendGatedHandle(RecordingHandle):
    """Only the *first* append blocks; the rest run at full speed.

    That is what opens the window this pins: the consumer sits inside one slow
    append while the queue saturates at its logical rung, and then drains
    quickly once released.
    """

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def append_text(self, name: str, value: str) -> None:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            assert self.gate.wait(timeout=30)
        super().append_text(name, value)


def _tiered_policy() -> QueuePolicy:
    """A two-rung policy used to prove close over a deferred FIFO."""
    return QueuePolicy(
        initial_event_capacity=2,
        max_event_capacity=4,
        growth_factor=2,
        initial_queued_bytes=1024,
        max_queued_bytes=2048,
        producer_timeout_seconds=5.0,
    )


def test_close_never_acknowledges_a_producer_it_will_not_persist() -> None:
    """Healthy close drains accepted tickets before its private stop token."""

    async def case() -> None:
        handle = FirstAppendGatedHandle()
        writer = EventWriter(
            handle, max_event_bytes=65_536, max_events=10, policy=_tiered_policy()
        )
        await writer.start()
        await writer.emit({"type": "e1"})
        await _wait_thread_event(handle.entered)
        assert writer.emit_ordered({"type": "e2"}) is None
        pending = writer.emit_ordered({"type": "e3"})
        assert pending is not None, "e3 must defer at the logical rung"

        producer = asyncio.ensure_future(pending)
        close_task = asyncio.ensure_future(writer.close())
        assert writer.waiting == 1

        handle.gate.set()
        await asyncio.wait_for(asyncio.gather(producer, close_task), timeout=10)

        persisted = [record["type"] for _, record in handle.records]
        assert persisted == ["e1", "e2", "e3"], (
            f"acknowledged producer was not persisted before close: {persisted}"
        )
        assert [record["seq"] for _, record in handle.records] == [1, 2, 3]
        assert writer.admitted_count == 0
        assert writer.waiting == 0

    asyncio.run(case())


def test_close_with_no_consumer_to_drain_fails_its_tickets_and_fails_close() -> None:
    """The other legal outcome: fail the tickets *and* fail close, loudly.

    A ticket that can never be drained must not be quietly abandoned while
    close reports success. This is the standing guard on the other half of the
    rule — every acknowledged event is persisted before a clean close, or the
    producer and close both fail. Failure drain cannot turn a dead consumer
    into either a hang or a silent clean close.
    """

    async def case() -> None:
        handle = GatedHandle()
        writer = EventWriter(
            handle, max_event_bytes=65_536, max_events=10, policy=_tiered_policy()
        )
        await writer.start()
        writer.emit_ordered({"type": "e1"})
        writer.emit_ordered({"type": "e2"})
        pending = writer.emit_ordered({"type": "e3"})
        assert pending is not None, "e3 must defer at the logical rung"

        consumer = writer._consumer
        assert consumer is not None
        consumer.cancel()
        handle.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        with pytest.raises(EventWriterOverflow):
            await pending
        with pytest.raises(EventWriterOverflow, match="consumer cancelled"):
            await asyncio.wait_for(writer.close(), 10)
        assert "e3" not in [record["type"] for _, record in handle.records]

    asyncio.run(case())


def test_close_without_a_consumer_fails_when_evidence_is_still_queued() -> None:
    """A close that can never drain must not report success over queued events.

    Nothing was ever started, so the admitted ticket has no path
    to the stream. Returning cleanly here says "everything accepted was
    persisted" about an event that was not. The no-consumer branch therefore
    settles its persistence phase as failed and refuses clean close.
    """

    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65_536)
        # Deliberately no start(): nothing can durably acknowledge this ticket.
        await writer.emit({"type": "queued-without-consumer"})
        assert writer.admitted_count == 1

        with pytest.raises(EventWriterOverflow):
            await writer.close()

        assert handle.records == [], "nothing was persisted"
        # Once persistence is impossible, V3 totally settles the ticket and
        # releases its internal count/byte accounting without claiming absence.
        assert writer.admitted_count == 0
        assert writer.queued_bytes == 0

    asyncio.run(case())


def test_close_without_a_consumer_is_clean_when_nothing_was_accepted() -> None:
    """The honest no-op stays a no-op.

    A writer that was constructed and never used — the ordinary early-failure
    cleanup path — has nothing unpersisted to answer for, so close must not
    manufacture a failure.
    """

    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(handle, max_event_bytes=65_536)
        assert writer.admitted_count == 0
        assert writer.waiting == 0
        await writer.close()
        assert handle.records == []

    asyncio.run(case())


def test_close_without_a_consumer_fails_the_observer_and_the_close_together() -> None:
    """A ticket and a queued event share one outcome: both fail, close fails.

    The producer already learned it would never be admitted. Close reporting
    success alongside that is the contradiction: the queued event ahead of the
    ticket is just as unpersistable.
    """

    async def case() -> None:
        handle = RecordingHandle()
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            policy=QueuePolicy(
                initial_event_capacity=1,
                max_event_capacity=1,
                initial_queued_bytes=1024 * 1024,
                max_queued_bytes=1024 * 1024,
                producer_timeout_seconds=5.0,
            ),
        )
        await writer.emit({"type": "fills-the-slot"})
        pending = writer.emit_ordered({"type": "waits-forever"})
        assert pending is not None
        producer = asyncio.ensure_future(pending)
        await asyncio.sleep(0)
        assert writer.waiting == 1

        with pytest.raises(EventWriterOverflow):
            await writer.close()
        with pytest.raises(EventWriterOverflow):
            await producer
        assert handle.records == []

    asyncio.run(case())


# -- deferred-admission deadlines are the ticket's, not the awaiter's --------


class PacedHandle(RecordingHandle):
    """Appends that take a scripted amount of real time, in call order.

    Each append blocks its worker thread for the next scripted delay, so the
    drain loop advances on a schedule the test controls to the tenth of a
    second. That is what makes the deadline arithmetic below deterministic
    rather than a race.
    """

    def __init__(self, delays: list[float]) -> None:
        super().__init__()
        self.delays = list(delays)
        self.entered = threading.Event()

    def append_text(self, name: str, value: str) -> None:
        self.entered.set()
        delay = self.delays.pop(0) if self.delays else 0.0
        if delay:
            import time as _time

            _time.sleep(delay)
        super().append_text(name, value)


def _paced_policy(timeout: float) -> QueuePolicy:
    """A fixed two-slot rung: no growth, and room for two waiting tickets.

    The rung has to be saturable (so both tickets defer) while the waiting-set
    ceiling — the policy maximum — still admits two of them. A single-slot rung
    would refuse the second ticket outright as pipeline saturation, which is a
    different rule from the one under test.
    """
    return QueuePolicy(
        initial_event_capacity=2,
        max_event_capacity=2,
        initial_queued_bytes=1024 * 1024,
        max_queued_bytes=1024 * 1024,
        producer_timeout_seconds=timeout,
    )


async def _two_tickets_from_one_batch(writer, handle):
    """Park the consumer, saturate the rung, then take two tickets at once.

    Both tickets are created back to back in one synchronous burst — the shape
    a single SDK callback produces when it emits a normalized event and a
    mediation event together — so they share one creation instant.
    """
    await writer.start()
    await writer.emit({"type": "e0"})
    await _wait_thread_event(handle.entered)
    assert writer.emit_ordered({"type": "f1"}) is None, "the rung must saturate"

    first = writer.emit_ordered({"type": "e1"})
    second = writer.emit_ordered({"type": "e2"})
    assert first is not None and second is not None, "both must defer"
    assert writer.waiting == 2
    return first, second


async def _settle_batch(pending):
    """Run the production batch settlement and report per-ticket timing.

    ``_settle_admissions`` needs no Run state, so an uninitialised instance is
    enough — and if that ever stops being true this raises rather than passing
    a silent ``None`` around.
    """
    loop = asyncio.get_running_loop()
    origin = loop.time()
    observed: list[dict] = []

    def watch(index: int, awaitable):
        # Each runner owns its own row: they run concurrently, so "the last
        # row appended" is not this runner's row.
        row: dict = {"index": index}
        observed.append(row)

        async def runner() -> None:
            row["started_at"] = loop.time() - origin
            try:
                await awaitable
            except BaseException as exc:
                row["outcome"] = type(exc).__name__
                row["settled_at"] = loop.time() - origin
                raise
            row["outcome"] = "ok"
            row["settled_at"] = loop.time() - origin

        return runner()

    ctx = SimpleNamespace(
        pipeline_error=None, pipeline_error_rank=None, emit_ordinal=len(pending)
    )
    task = object.__new__(RunTask)
    await task._settle_admissions(
        ctx,
        [
            _PendingEmit(index + 1, watch(index, item))
            for index, item in enumerate(pending)
        ],
    )
    observed.sort(key=lambda row: row["index"])
    return ctx, observed, loop.time() - origin


def test_a_deferred_ticket_expires_from_its_own_creation_not_its_await() -> None:
    """The producer timeout belongs to the ticket, not to whoever awaits it.

    Two tickets are taken in one burst. The first is admitted comfortably
    inside the window. The second is admitted only long after its own window
    closed — and a settlement that hands each ticket a fresh timeout when it
    gets around to awaiting it will accept that, reporting success for a
    producer that waited far longer than the configured bound. The bound has to
    be measured from ticket creation, or it is not a bound at all.
    """

    timeout = 1.0

    async def case() -> None:
        # e0's append parks the consumer and gates ticket 1; the next append
        # delays ticket 2 past the window both tickets were created with.
        handle = PacedHandle([0.6, 0.7])
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            max_events=50,
            policy=_paced_policy(timeout),
        )
        first, second = await _two_tickets_from_one_batch(writer, handle)
        ctx, observed, elapsed = await _settle_batch([first, second])

        # The first ticket is the control: it really was admitted in time.
        assert observed[0]["outcome"] == "ok", observed
        assert observed[0]["settled_at"] < timeout, observed

        # The second is the defect: admitted only around 1.3s after creation.
        assert not (
            observed[1]["outcome"] == "ok" and observed[1]["settled_at"] > timeout
        ), (
            "a deferred producer was accepted "
            f"{observed[1]['settled_at']:.3f}s after its ticket was created, "
            f"past its {timeout}s window: {observed}"
        )
        # Exceeding the window is the existing fail-closed signal, not silence.
        assert isinstance(ctx.pipeline_error, EventWriterOverflow), ctx.pipeline_error
        assert elapsed < timeout + 0.5, f"settlement ran to {elapsed:.3f}s"

        with contextlib.suppress(EventWriterOverflow):
            await writer.close()

    asyncio.run(case())


def test_a_ticket_window_does_not_restart_when_the_await_starts_late() -> None:
    """The deadline half, isolated from the concurrency half.

    Settling a batch concurrently is not on its own enough: there is a real gap
    between a ticket being taken inside a synchronous callback and anything
    awaiting it, and a window measured from the await would silently extend by
    that gap. Here nothing watches the ticket for most of its window, and the
    ticket must still expire on its own schedule.
    """

    timeout = 1.0

    async def case() -> None:
        handle = PacedHandle([0.6, 0.7])
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            max_events=50,
            policy=_paced_policy(timeout),
        )
        loop = asyncio.get_running_loop()
        await writer.start()
        await writer.emit({"type": "e0"})
        await _wait_thread_event(handle.entered)
        assert writer.emit_ordered({"type": "f1"}) is None

        origin = loop.time()
        # The first ticket absorbs the ~0.6s admission and is watched normally.
        # The second is deliberately left unwatched for most of its window; its
        # event is not admitted until ~1.3s, which measured from the await
        # would look comfortably on time.
        first = writer.emit_ordered({"type": "held-1"})
        pending = writer.emit_ordered({"type": "held-2"})
        assert first is not None and pending is not None
        first_task = asyncio.ensure_future(first)
        await asyncio.sleep(0.8)

        with pytest.raises(EventWriterOverflow):
            await pending
        assert loop.time() - origin < timeout + 0.35, "the window was extended"
        await first_task  # admitted in time; consumed so nothing is abandoned

        with contextlib.suppress(EventWriterOverflow):
            await writer.close()

    asyncio.run(case())


def test_a_settled_batch_starts_together_and_still_persists_in_order() -> None:
    """Positive control: concurrency does not disturb admission order.

    Both tickets fit inside their windows here, so nothing fails. What this
    pins is that they are *started* together — a serial settlement cannot start
    the second until the first has been admitted — while the writer's pending
    FIFO still decides who is admitted first, so the stream order is the
    original wire order.
    """

    timeout = 2.0

    async def case() -> None:
        handle = PacedHandle([0.4, 0.4])
        writer = EventWriter(
            handle,
            max_event_bytes=65_536,
            max_events=50,
            policy=_paced_policy(timeout),
        )
        first, second = await _two_tickets_from_one_batch(writer, handle)
        ctx, observed, _elapsed = await _settle_batch([first, second])

        assert [row["outcome"] for row in observed] == ["ok", "ok"], observed
        assert ctx.pipeline_error is None
        # Started together: the second ticket does not wait for the first to be
        # admitted (which does not happen until ~0.4s) before its wait begins.
        assert observed[1]["started_at"] < 0.2, observed
        # Admitted in ticket order all the same.
        assert observed[0]["settled_at"] <= observed[1]["settled_at"], observed

        await writer.close()
        assert [record["type"] for _, record in handle.records] == [
            "e0",
            "f1",
            "e1",
            "e2",
        ]
        assert [record["seq"] for _, record in handle.records] == [1, 2, 3, 4]

    asyncio.run(case())


def test_settlement_records_the_first_failure_in_ticket_order() -> None:
    """Which failure is recorded must not depend on who finished first.

    Settling concurrently means outcomes arrive in completion order. The Run's
    recorded cause has to stay the first failure in *ticket* order, or the same
    Run would attribute its evidence-pipeline failure differently from one
    execution to the next.
    """

    class FirstTicketError(RuntimeError):
        pass

    class SecondTicketError(RuntimeError):
        pass

    async def case() -> None:
        async def slow_failure() -> None:
            await asyncio.sleep(0.05)
            raise FirstTicketError("ticket 1")

        async def fast_failure() -> None:
            raise SecondTicketError("ticket 2")

        ctx = SimpleNamespace(
            pipeline_error=None, pipeline_error_rank=None, emit_ordinal=2
        )
        run_task = object.__new__(RunTask)
        await run_task._settle_admissions(
            ctx,
            [
                _PendingEmit(1, slow_failure()),
                _PendingEmit(2, fast_failure()),
            ],
        )
        assert isinstance(ctx.pipeline_error, FirstTicketError), ctx.pipeline_error

    asyncio.run(case())


def test_settlement_cancellation_joins_without_cancelling_observers() -> None:
    """Caller cancellation waits for ledger observations, then propagates."""

    async def case() -> None:
        completed: list[int] = []
        started = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()

        def blocking(index: int):
            async def runner() -> None:
                started[index].set()
                await release.wait()
                completed.append(index)

            return runner()

        ctx = SimpleNamespace(
            pipeline_error=None, pipeline_error_rank=None, emit_ordinal=2
        )
        run_task = object.__new__(RunTask)
        settling = asyncio.ensure_future(
            run_task._settle_admissions(
                ctx,
                [
                    _PendingEmit(1, blocking(0)),
                    _PendingEmit(2, blocking(1)),
                ],
            )
        )
        await asyncio.gather(*(event.wait() for event in started))

        settling.cancel()
        for _ in range(20):
            if settling.cancelling() == 0:
                break
            await asyncio.sleep(0)
        assert settling.cancelling() == 0
        assert not settling.done()
        assert completed == []

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await settling
        assert sorted(completed) == [0, 1]
        assert ctx.pipeline_error is None
        pending_tasks = [
            task
            for task in asyncio.all_tasks()
            if not task.done() and task is not asyncio.current_task()
        ]
        assert pending_tasks == []

    asyncio.run(case())


def test_the_waiting_producer_set_is_itself_bounded() -> None:
    """No unbounded buffer: the SDK spawns one task per notification frame.

    The locked SDK's dispatcher creates a task per incoming notification and
    never applies transport backpressure, so the set of producers *waiting* for
    admission has to carry its own ceiling. Past it, admission fails closed
    instead of accumulating callbacks without limit.
    """

    async def case() -> None:
        handle = GatedHandle()
        policy = QueuePolicy(
            initial_event_capacity=4,
            max_event_capacity=4,
            initial_queued_bytes=1024 * 1024,
            max_queued_bytes=1024 * 1024,
            producer_timeout_seconds=30.0,
        )
        writer = EventWriter(
            handle, max_event_bytes=65_536, max_events=1000, policy=policy
        )
        await writer.start()
        await writer.emit({"type": "pad", "n": 0})
        await _wait_thread_event(handle.entered)
        for index in range(1, policy.initial_event_capacity):
            assert writer.emit_ordered({"type": "pad", "n": index}) is None
        assert writer.admitted_count == policy.initial_event_capacity

        observations = [
            writer.emit_ordered({"type": "waiting", "n": index})
            for index in range(policy.max_event_capacity)
        ]
        assert all(pending is not None for pending in observations)
        assert writer.waiting == policy.max_event_capacity

        # One past the waiting ceiling: refused now, not queued for later.
        with pytest.raises(EventWriterOverflow):
            writer.emit_ordered({"type": "over-the-ceiling"})
        assert writer.overflowed is True

        # Saturation is a whole-pipeline failure: an admission was lost, so
        # holding the rest in order buys nothing and every ticket fails.
        for pending in observations:
            with pytest.raises(EventWriterOverflow):
                await pending
        handle.gate.set()
        with pytest.raises(EventWriterOverflow, match="saturated"):
            await writer.close()

    asyncio.run(case())


def test_v3_run_task_selects_earliest_failure_across_concurrent_batches() -> None:
    """Completion order and batch ownership cannot change failure attribution."""

    class EarlierFailure(RuntimeError):
        pass

    class LaterFailure(RuntimeError):
        pass

    async def case() -> None:
        release_earlier = asyncio.Event()
        later_finished = asyncio.Event()

        async def earlier_failure() -> None:
            await release_earlier.wait()
            raise EarlierFailure("earlier emission")

        async def later_failure() -> None:
            later_finished.set()
            raise LaterFailure("later emission")

        ctx = SimpleNamespace(
            pipeline_error=None,
            pipeline_error_rank=None,
            emit_ordinal=2,
        )
        run_task = object.__new__(RunTask)
        earlier_batch = asyncio.create_task(
            run_task._settle_admissions(
                ctx, [_PendingEmit(1, earlier_failure())]
            )
        )
        later_batch = asyncio.create_task(
            run_task._settle_admissions(
                ctx, [_PendingEmit(2, later_failure())]
            )
        )

        await later_finished.wait()
        await later_batch
        assert isinstance(ctx.pipeline_error, LaterFailure)

        release_earlier.set()
        await earlier_batch
        assert isinstance(ctx.pipeline_error, EarlierFailure)
        assert ctx.pipeline_error_rank[0] == 1

    asyncio.run(case())

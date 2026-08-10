"""Per-Run bounded serial event ledger.

The event loop owns every piece of EventWriter truth: accepted sequence
numbers, canonical NDJSON strings, exact UTF-8 byte charges, admission and
persistence phases, absolute producer deadlines, durable acknowledgements,
failure ordering, and the close result.  The one consumer owns only the
blocking call to ``RunHandle.append_text``.

Producer awaitables are deliberately weaker than ledger state.  They observe a
phase through a value-only edge; cancelling or abandoning an observation can
neither cancel a ticket nor release its count or byte charge.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from agent_run_supervisor.event_store import ndjson_line

from .spec import (
    LIMIT_MAX_EVENT_BYTES_MAX,
    LIMIT_MAX_EVENT_BYTES_MIN,
    LIMIT_MAX_EVENTS_MAX,
)

EVENTS_FILENAME = "normalized-events.jsonl"

# Fixed production policy.  Generic ledger logic reads these values only
# through QueuePolicy; focused tests may provide a smaller policy.
INITIAL_EVENT_CAPACITY = 1024
QUEUE_GROWTH_FACTOR = 2
MAX_EVENT_CAPACITY = 8192
INITIAL_QUEUED_BYTES = 8 * 1024 * 1024
MAX_QUEUED_BYTES = 64 * 1024 * 1024
DEFAULT_PRODUCER_TIMEOUT_SECONDS = 5.0

DEFAULT_MAX_EVENTS = 10_000


class EventWriterOverflow(RuntimeError):
    """Controlled evidence-pipeline failure.

    ``ordinal`` and ``code`` are stable ordering data for RunTask.  The public
    message is deliberately sanitized and contains no sink or payload text.
    """

    def __init__(self, message: str, *, ordinal: int = 0, code: str = "UNKNOWN"):
        super().__init__(message)
        self.ordinal = ordinal
        self.code = code


def _require_positive_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _ladder(initial: int, maximum: int, factor: int, label: str) -> tuple[int, ...]:
    """Return an exact multiplicative ladder, refusing a rounded top rung."""
    rungs = [initial]
    value = initial
    while value < maximum:
        value *= factor
        rungs.append(value)
    if rungs[-1] != maximum:
        raise ValueError(f"{label} maximum is not reachable by the growth factor")
    return tuple(rungs)


@dataclass(frozen=True)
class QueuePolicy:
    """Per-Run admitted and pending bounds plus one constant timeout."""

    initial_event_capacity: int = INITIAL_EVENT_CAPACITY
    max_event_capacity: int = MAX_EVENT_CAPACITY
    growth_factor: int = QUEUE_GROWTH_FACTOR
    initial_queued_bytes: int = INITIAL_QUEUED_BYTES
    max_queued_bytes: int = MAX_QUEUED_BYTES
    producer_timeout_seconds: float = DEFAULT_PRODUCER_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        _require_positive_int("initial_event_capacity", self.initial_event_capacity)
        _require_positive_int("max_event_capacity", self.max_event_capacity)
        _require_positive_int("initial_queued_bytes", self.initial_queued_bytes)
        _require_positive_int("max_queued_bytes", self.max_queued_bytes)
        if (
            isinstance(self.growth_factor, bool)
            or not isinstance(self.growth_factor, int)
            or self.growth_factor < 2
        ):
            raise ValueError("growth_factor must be an integer of at least 2")
        if (
            isinstance(self.producer_timeout_seconds, bool)
            or not isinstance(self.producer_timeout_seconds, (int, float))
            or self.producer_timeout_seconds <= 0
        ):
            raise ValueError("producer_timeout_seconds must be positive")
        if self.max_event_capacity < self.initial_event_capacity:
            raise ValueError("max_event_capacity is below initial_event_capacity")
        if self.max_queued_bytes < self.initial_queued_bytes:
            raise ValueError("max_queued_bytes is below initial_queued_bytes")
        if len(self.event_capacities()) != len(self.queued_byte_budgets()):
            raise ValueError(
                "event-capacity and queued-byte ladders must have the same "
                "number of rungs; one expansion lifts both ceilings"
            )

    def event_capacities(self) -> tuple[int, ...]:
        return _ladder(
            self.initial_event_capacity,
            self.max_event_capacity,
            self.growth_factor,
            "event capacity",
        )

    def queued_byte_budgets(self) -> tuple[int, ...]:
        return _ladder(
            self.initial_queued_bytes,
            self.max_queued_bytes,
            self.growth_factor,
            "queued byte budget",
        )


DEFAULT_QUEUE_POLICY = QueuePolicy()


class _WriterState(enum.Enum):
    NEW = enum.auto()
    RUNNING = enum.auto()
    CLOSING = enum.auto()
    FAILING = enum.auto()
    CLOSED_OK = enum.auto()
    CLOSED_FAILED = enum.auto()


class _ConsumerState(enum.Enum):
    NOT_STARTED = enum.auto()
    RUNNING = enum.auto()
    EXITED_CLOSE_STOP = enum.auto()
    EXITED_FAILURE_DRAIN = enum.auto()
    EXITED_ERROR = enum.auto()


class _TicketState(enum.Enum):
    PENDING = enum.auto()
    ADMITTED = enum.auto()
    PERSISTED = enum.auto()
    EXPIRED = enum.auto()
    FAILED = enum.auto()
    FAILED_UNPERSISTED = enum.auto()


class _FailureCode(enum.Enum):
    """Exhaustive categorical failure vocabulary and fixed tie order."""

    SERIALIZATION = (10, "event evidence serialization failed")
    MARKER_TOO_LARGE = (20, "bounded event marker exceeds max_event_bytes")
    MAX_EVENTS = (30, "event writer max_events exceeded")
    SATURATED_COUNT = (
        40,
        "evidence pipeline saturated: waiting producers past the bounded ceiling",
    )
    SATURATED_BYTES = (
        50,
        "evidence pipeline saturated: retained bytes past the bounded ceiling",
    )
    PRODUCER_TIMEOUT = (60, "event queue stayed full past the producer timeout")
    LEDGER_INVARIANT = (70, "event writer ledger invariant failed")
    CONSUMER_START = (80, "event writer consumer could not start")
    APPEND_FAILED = (90, "event evidence append failed")
    CONSUMER_CANCELLED = (100, "event writer consumer cancelled")
    UNEXPECTED_CONSUMER_EXIT = (
        110,
        "event writer consumer ended before persistence",
    )
    CLOSE_INCONSISTENCY = (120, "event writer close could not certify persistence")
    NO_CONSUMER = (
        130,
        "event writer closed with unpersisted evidence and no consumer",
    )

    @property
    def rank(self) -> int:
        return self.value[0]

    @property
    def message(self) -> str:
        return self.value[1]


@dataclass(frozen=True, order=True)
class _FailureKey:
    ordinal: int
    category: int
    code: _FailureCode = field(compare=False)


@dataclass(frozen=True)
class _Outcome:
    ok: bool
    ordinal: int
    code: _FailureCode | None = None


@dataclass(eq=False)
class _Ticket:
    seq: int
    wire: str
    nbytes: int
    deadline: float
    state: _TicketState = _TicketState.PENDING
    admission: _Outcome | None = None
    persistence: _Outcome | None = None
    admission_edge: asyncio.Future[_Outcome] | None = None
    persistence_edge: asyncio.Future[_Outcome] | None = None


class _Observation:
    """Disposable, cancellation-powerless view of one ledger phase."""

    def __init__(self, edge: asyncio.Future[_Outcome]):
        self._edge = edge

    async def _wait(self) -> None:
        # Shield is the isolation boundary: caller cancellation affects the
        # wrapper task only; the value-only ledger edge remains live.
        outcome = await asyncio.shield(self._edge)
        if not outcome.ok:
            assert outcome.code is not None
            raise _overflow(outcome.code, outcome.ordinal)

    def __await__(self):
        return self._wait().__await__()


class _CloseJoin(asyncio.Future[None]):
    """Value-only close edge whose cancellation cannot release ownership.

    ``Task.cancel()`` normally forwards cancellation to the Future it is
    awaiting.  This edge records that request but deliberately stays pending.
    Only after the owned close task settles does it become cancelled, so even a
    caller cancelled before its coroutine's first instruction cannot escape
    the consumer join.  A non-cancelled edge stores only ``None`` and projects
    the writer's cached controlled failure while being awaited.
    """

    def __init__(self, writer: EventWriter, task: asyncio.Task[None]):
        super().__init__()
        self._writer = writer
        self._cancel_requested = False
        self._cancel_message: str | None = None
        task.add_done_callback(self._owned_done)
        if task.done():
            self._owned_done(task)

    def cancel(self, msg: str | None = None) -> bool:
        if self.done():
            return False
        self._cancel_requested = True
        self._cancel_message = msg
        return True

    def _owned_done(self, task: asyncio.Task[None]) -> None:
        self._writer._cache_close_outcome(task)
        if self.done():
            return
        if self._cancel_requested:
            super().cancel(self._cancel_message)
        else:
            self.set_result(None)

    def __await__(self):
        yield from super().__await__()
        self._raise_cached_failure()
        return None

    def result(self) -> None:
        super().result()
        self._raise_cached_failure()
        return None

    def _raise_cached_failure(self) -> None:
        failure = self._writer._close_failure
        if failure is not None:
            raise EventWriterOverflow(
                str(failure), ordinal=failure.ordinal, code=failure.code
            )


def _overflow(code: _FailureCode, ordinal: int) -> EventWriterOverflow:
    return EventWriterOverflow(code.message, ordinal=ordinal, code=code.name)


_EVENT_FAMILY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def bounded_event_family(value: Any) -> str:
    """Return the one bounded marker-family projection used by every event."""
    if isinstance(value, str) and _EVENT_FAMILY_RE.fullmatch(value) is not None:
        return value
    return "unknown_update"


_WAIT = object()
_CLOSE_STOP = object()
_FAILURE_DRAIN_COMPLETE = object()


class EventWriter:
    """One event-loop-owned bounded ledger and one serial durable consumer."""

    def __init__(
        self,
        handle: Any,
        *,
        max_event_bytes: int,
        max_events: int = DEFAULT_MAX_EVENTS,
        policy: QueuePolicy | None = None,
        filename: str = EVENTS_FILENAME,
    ) -> None:
        if (
            isinstance(max_event_bytes, bool)
            or not isinstance(max_event_bytes, int)
            or max_event_bytes < LIMIT_MAX_EVENT_BYTES_MIN
            or max_event_bytes > LIMIT_MAX_EVENT_BYTES_MAX
        ):
            raise ValueError("max_event_bytes out of bounds")
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or max_events < 1
            or max_events > LIMIT_MAX_EVENTS_MAX
        ):
            raise ValueError("max_events out of bounds")
        if policy is not None and not isinstance(policy, QueuePolicy):
            raise ValueError("policy must be a QueuePolicy")

        self._handle = handle
        self._max_event_bytes = max_event_bytes
        self._max_events = max_events
        self._policy = DEFAULT_QUEUE_POLICY if policy is None else policy
        self._capacities = self._policy.event_capacities()
        self._byte_budgets = self._policy.queued_byte_budgets()
        self._tier = 0
        self._filename = filename

        self._state = _WriterState.NEW
        self._consumer_state = _ConsumerState.NOT_STARTED
        self._consumer: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._close_failure: EventWriterOverflow | None = None
        self._close_succeeded = False

        self._pending: deque[_Ticket] = deque()
        self._admitted: deque[_Ticket] = deque()
        self._pending_bytes = 0
        self._queued_bytes = 0
        self._persisted_bytes = 0
        self._accepted = 0
        self._admitted_high = 0
        self._persisted = 0
        self._growth_mark = 0
        self._in_flight: _Ticket | None = None

        self._primary_failure: _FailureKey | None = None
        self._drain_then_exit = False
        self._stop_requested = False

        self._consumer_wakeup = asyncio.Event()
        self._pending_empty = asyncio.Event()
        self._pending_empty.set()

        self._head_timer: Any = None
        self._head_timer_generation = 0
        self._last_deadline: float | None = None

        # Injectable monotonic scheduling seams.  The production default still
        # resolves the running loop at the operation, not at construction.
        self._now: Callable[[], float] = (
            lambda: asyncio.get_running_loop().time()
        )
        self._call_at: Callable[..., Any] = (
            lambda when, callback, *args: asyncio.get_running_loop().call_at(
                when, callback, *args
            )
        )

    # -- observable ledger state -----------------------------------------

    @property
    def last_seq(self) -> int:
        """The contiguous crash-durably acknowledged high-water mark."""
        return self._persisted

    @property
    def queue_capacity(self) -> int:
        return self._capacities[self._tier]

    @property
    def queued_byte_budget(self) -> int:
        return self._byte_budgets[self._tier]

    @property
    def queued_bytes(self) -> int:
        """Exact bytes admitted but not yet durably acknowledged."""
        return self._queued_bytes

    @property
    def admitted_count(self) -> int:
        """Admitted-unacknowledged count, including the in-flight head."""
        return len(self._admitted)

    @property
    def expansions(self) -> int:
        return self._tier

    @property
    def waiting(self) -> int:
        return len(self._pending)

    @property
    def overflowed(self) -> bool:
        """Compatibility projection; primary failure is the sole truth."""
        return self._primary_failure is not None

    @property
    def can_accept(self) -> bool:
        """Whether a live healthy consumer has immediate logical room."""
        try:
            return (
                self._state is _WriterState.RUNNING
                and self._primary_failure is None
                and self._accepted < self._max_events
                and not self._pending
                and self._consumer_live()
                and len(self._admitted) < self.queue_capacity
                and self._queued_bytes < self.queued_byte_budget
            )
        except Exception:
            return False

    @property
    def consumer_queue_healthy(self) -> bool:
        """Live consumer and immediate room, intentionally ignoring max_events."""
        try:
            return (
                self._state is _WriterState.RUNNING
                and self._primary_failure is None
                and not self._pending
                and self._consumer_live()
                and len(self._admitted) < self.queue_capacity
                and self._queued_bytes < self.queued_byte_budget
            )
        except Exception:
            return False

    # -- consumer ownership ----------------------------------------------

    async def start(self) -> None:
        if self._consumer_state is _ConsumerState.RUNNING:
            return
        if self._consumer_state is not _ConsumerState.NOT_STARTED:
            raise self._current_or_closed_failure()
        if self._state is not _WriterState.NEW:
            raise self._current_or_closed_failure()

        gate = asyncio.Event()
        consumer_coro = self._consumer_main(gate)
        try:
            consumer = asyncio.create_task(
                consumer_coro, name="event-writer-consumer"
            )
        except Exception as exc:
            # Task ownership never transferred.  Close the coroutine locally so
            # the controlled start failure leaves no unowned async object.
            consumer_coro.close()
            self._consumer_state = _ConsumerState.EXITED_ERROR
            self._poison(
                _FailureCode.CONSUMER_START,
                self._lowest_unpersisted_ordinal(),
                persistence_impossible=True,
            )
            raise self._primary_overflow() from exc

        # Attach ownership before the body may cross its gate.  Cancellation
        # before the coroutine's first instruction is therefore still observed.
        self._consumer = consumer
        self._consumer_state = _ConsumerState.RUNNING
        self._state = _WriterState.RUNNING
        consumer.add_done_callback(self._on_consumer_done)
        gate.set()
        self._consumer_wakeup.set()

    def _consumer_live(self) -> bool:
        consumer = self._consumer
        return (
            self._consumer_state is _ConsumerState.RUNNING
            and consumer is not None
            and not consumer.done()
        )

    def _on_consumer_done(self, task: asyncio.Task[None]) -> None:
        cancelled = task.cancelled()
        unexpected: BaseException | None = None
        if not cancelled:
            try:
                unexpected = task.exception()
            except BaseException as exc:  # result retrieval itself is total
                unexpected = exc

        if self._consumer_state is _ConsumerState.RUNNING:
            code = (
                _FailureCode.CONSUMER_CANCELLED
                if cancelled
                else _FailureCode.UNEXPECTED_CONSUMER_EXIT
            )
            self._poison(
                code,
                self._lowest_unpersisted_ordinal(),
                persistence_impossible=True,
            )
            self._consumer_state = _ConsumerState.EXITED_ERROR
        elif unexpected is not None:
            self._poison(
                _FailureCode.UNEXPECTED_CONSUMER_EXIT,
                self._lowest_unpersisted_ordinal(),
                persistence_impossible=True,
            )
            self._consumer_state = _ConsumerState.EXITED_ERROR
        self._pending_empty.set()
        self._consumer_wakeup.set()

    async def _consumer_main(self, gate: asyncio.Event) -> None:
        try:
            await gate.wait()
            while True:
                action = await self._next_consumer_action()
                if action is _CLOSE_STOP:
                    if not (
                        self._state is _WriterState.CLOSING
                        and not self._pending
                        and self._primary_failure is None
                    ):
                        self._poison(
                            _FailureCode.CLOSE_INCONSISTENCY,
                            self._lowest_unpersisted_ordinal(),
                            persistence_impossible=True,
                        )
                        self._consumer_state = _ConsumerState.EXITED_ERROR
                    else:
                        self._stop_requested = False
                        self._consumer_state = _ConsumerState.EXITED_CLOSE_STOP
                    return
                if action is _FAILURE_DRAIN_COMPLETE:
                    self._consumer_state = _ConsumerState.EXITED_FAILURE_DRAIN
                    return

                ticket = action
                if not isinstance(ticket, _Ticket):
                    self._poison(
                        _FailureCode.LEDGER_INVARIANT,
                        self._lowest_unpersisted_ordinal(),
                        persistence_impossible=True,
                    )
                    self._consumer_state = _ConsumerState.EXITED_ERROR
                    return
                self._in_flight = ticket
                try:
                    await asyncio.to_thread(
                        self._handle.append_text,
                        self._filename,
                        ticket.wire,
                    )
                except asyncio.CancelledError:
                    self._poison(
                        _FailureCode.CONSUMER_CANCELLED,
                        ticket.seq,
                        persistence_impossible=True,
                    )
                    self._consumer_state = _ConsumerState.EXITED_ERROR
                    raise
                except Exception:
                    self._poison(
                        _FailureCode.APPEND_FAILED,
                        ticket.seq,
                        persistence_impossible=True,
                    )
                    self._consumer_state = _ConsumerState.EXITED_ERROR
                    return

                self._durable_ack(ticket)
        except asyncio.CancelledError:
            if self._consumer_state is _ConsumerState.RUNNING:
                self._poison(
                    _FailureCode.CONSUMER_CANCELLED,
                    self._lowest_unpersisted_ordinal(),
                    persistence_impossible=True,
                )
                self._consumer_state = _ConsumerState.EXITED_ERROR
            raise
        except Exception:
            self._poison(
                _FailureCode.LEDGER_INVARIANT,
                self._lowest_unpersisted_ordinal(),
                persistence_impossible=True,
            )
            self._consumer_state = _ConsumerState.EXITED_ERROR
        finally:
            if self._consumer_state is _ConsumerState.RUNNING:
                self._poison(
                    _FailureCode.UNEXPECTED_CONSUMER_EXIT,
                    self._lowest_unpersisted_ordinal(),
                    persistence_impossible=True,
                )
                self._consumer_state = _ConsumerState.EXITED_ERROR
            self._pending_empty.set()
            self._consumer_wakeup.set()

    async def _next_consumer_action(self) -> object:
        """Clear, synchronously recheck, then await the single wakeup edge."""
        while True:
            action = self._peek_consumer_action()
            if action is not _WAIT:
                return action
            self._consumer_wakeup.clear()
            action = self._peek_consumer_action()
            if action is not _WAIT:
                continue
            await self._consumer_wakeup.wait()

    def _peek_consumer_action(self) -> object:
        if self._admitted:
            return self._admitted[0]
        if self._state is _WriterState.FAILING or self._drain_then_exit:
            return _FAILURE_DRAIN_COMPLETE
        if self._stop_requested:
            return _CLOSE_STOP
        return _WAIT

    # -- canonical acceptance and observations ---------------------------

    def _prepare(self, event: Mapping[str, Any], seq: int) -> tuple[str, int]:
        record = dict(event)
        record["seq"] = seq
        full_wire = ndjson_line(record)
        full_nbytes = len(full_wire.encode("utf-8"))
        if full_nbytes <= self._max_event_bytes:
            return full_wire, full_nbytes

        marker = {
            "seq": seq,
            "type": bounded_event_family(event.get("type")),
            "truncated": True,
            "truncate_reason": "max_event_bytes",
        }
        marker_wire = ndjson_line(marker)
        marker_nbytes = len(marker_wire.encode("utf-8"))
        if marker_nbytes > self._max_event_bytes:
            raise _MarkerTooLarge()
        return marker_wire, marker_nbytes

    def _submit(
        self, event: Mapping[str, Any], observed_phase: str
    ) -> Awaitable[None] | None:
        if observed_phase not in {"admission", "persistence"}:
            raise ValueError("observed_phase must be admission or persistence")
        self._require_open()
        self._pump()
        self._require_open()

        candidate_seq = self._accepted + 1
        try:
            wire, nbytes = self._prepare(event, candidate_seq)
        except _MarkerTooLarge as exc:
            self._poison(
                _FailureCode.MARKER_TOO_LARGE,
                candidate_seq,
                event_not_accepted=True,
            )
            raise self._primary_overflow() from exc
        except Exception as exc:
            self._poison(
                _FailureCode.SERIALIZATION,
                candidate_seq,
                event_not_accepted=True,
            )
            raise self._primary_overflow() from exc

        # Canonical rendering may take time.  Re-enter the same deadline-first
        # pump before any bounds check or acceptance commit.
        self._pump()
        self._require_open()

        if self._accepted >= self._max_events:
            self._poison(
                _FailureCode.MAX_EVENTS,
                candidate_seq,
                event_not_accepted=True,
            )
            raise self._primary_overflow()
        if len(self._pending) >= self._policy.max_event_capacity:
            self._poison(
                _FailureCode.SATURATED_COUNT,
                candidate_seq,
                event_not_accepted=True,
            )
            raise self._primary_overflow()
        if (
            self._pending_bytes + self._queued_bytes + nbytes
            > self._policy.max_queued_bytes
        ):
            self._poison(
                _FailureCode.SATURATED_BYTES,
                candidate_seq,
                event_not_accepted=True,
            )
            raise self._primary_overflow()

        deadline = self._now() + self._policy.producer_timeout_seconds
        # QueuePolicy supplies one constant timeout and acceptance is FIFO on a
        # monotonic clock.  Therefore deadlines must be non-decreasing; the one
        # head timer is valid only under this explicitly checked invariant.
        if self._last_deadline is not None and deadline < self._last_deadline:
            self._poison(
                _FailureCode.LEDGER_INVARIANT,
                candidate_seq,
                event_not_accepted=True,
            )
            raise self._primary_overflow()

        ticket = _Ticket(candidate_seq, wire, nbytes, deadline)
        observation = self._observe(ticket, observed_phase)

        # Atomic non-awaiting acceptance commit.
        self._accepted = candidate_seq
        self._last_deadline = deadline
        self._pending.append(ticket)
        self._pending_bytes += nbytes
        self._pending_empty.clear()
        if not self._verify_invariants(candidate_seq):
            raise self._primary_overflow()

        self._pump()
        outcome = (
            ticket.admission
            if observed_phase == "admission"
            else ticket.persistence
        )
        if outcome is not None and not outcome.ok:
            assert outcome.code is not None
            raise _overflow(outcome.code, outcome.ordinal)
        if observed_phase == "admission" and outcome is not None and outcome.ok:
            return None
        return observation

    def _observe(self, ticket: _Ticket, phase: str) -> Awaitable[None]:
        if phase not in {"admission", "persistence"}:
            raise ValueError("phase must be admission or persistence")
        outcome = ticket.admission if phase == "admission" else ticket.persistence
        edge_name = "admission_edge" if phase == "admission" else "persistence_edge"
        edge = getattr(ticket, edge_name)
        if edge is None:
            edge = asyncio.get_running_loop().create_future()
            setattr(ticket, edge_name, edge)
        if outcome is not None and not edge.done():
            edge.set_result(outcome)
        return _Observation(edge)

    def _settle_phase(
        self, ticket: _Ticket, phase: str, ok: bool, code: _FailureCode | None = None
    ) -> None:
        name = "admission" if phase == "admission" else "persistence"
        if getattr(ticket, name) is not None:
            return
        outcome = _Outcome(ok=ok, ordinal=ticket.seq, code=code)
        setattr(ticket, name, outcome)
        edge = (
            ticket.admission_edge
            if phase == "admission"
            else ticket.persistence_edge
        )
        if edge is not None and not edge.done():
            edge.set_result(outcome)

    # -- deadline-first ledger pump --------------------------------------

    def _room(self, nbytes: int) -> bool:
        return (
            len(self._admitted) + 1 <= self.queue_capacity
            and self._queued_bytes + nbytes <= self.queued_byte_budget
        )

    def _can_grow(self) -> bool:
        return (
            self._consumer_live()
            and self._primary_failure is None
            and self._tier < len(self._capacities) - 1
            and self._persisted > self._growth_mark
        )

    def _pump(self) -> None:
        if self._state not in {
            _WriterState.NEW,
            _WriterState.RUNNING,
            _WriterState.CLOSING,
        }:
            self._cancel_head_timer()
            return

        while self._pending:
            head = self._pending[0]
            # This comparison is first on every actual iteration.  The timer is
            # only a wakeup; an ack/submit/close callback that beats a delayed
            # timer still observes expiry before room, growth, or admission.
            try:
                now = self._now()
            except Exception:
                self._poison(
                    _FailureCode.LEDGER_INVARIANT,
                    head.seq,
                    persistence_impossible=not self._consumer_live(),
                )
                return
            if now >= head.deadline:
                self._poison(
                    _FailureCode.PRODUCER_TIMEOUT,
                    head.seq,
                    timeout_victim=head,
                )
                return

            if self._room(head.nbytes):
                self._pending.popleft()
                self._pending_bytes -= head.nbytes
                head.state = _TicketState.ADMITTED
                self._admitted.append(head)
                self._queued_bytes += head.nbytes
                self._admitted_high = head.seq
                self._settle_phase(head, "admission", True)
                self._consumer_wakeup.set()
                continue

            if self._can_grow():
                self._tier += 1
                self._growth_mark = self._persisted
                continue
            break

        if self._pending:
            self._pending_empty.clear()
        else:
            self._pending_empty.set()
        self._rearm_head_timer()
        self._verify_invariants(self._lowest_unpersisted_ordinal())

    def _cancel_head_timer(self) -> None:
        self._head_timer_generation += 1
        handle = self._head_timer
        self._head_timer = None
        if handle is not None:
            with contextlib.suppress(Exception):
                handle.cancel()

    def _rearm_head_timer(self) -> None:
        self._cancel_head_timer()
        if not self._pending or self._state not in {
            _WriterState.NEW,
            _WriterState.RUNNING,
            _WriterState.CLOSING,
        }:
            return
        generation = self._head_timer_generation
        try:
            self._head_timer = self._call_at(
                self._pending[0].deadline, self._on_head_timer, generation
            )
        except Exception:
            self._poison(
                _FailureCode.LEDGER_INVARIANT,
                self._pending[0].seq,
                persistence_impossible=not self._consumer_live(),
            )

    def _on_head_timer(self, generation: int) -> None:
        if generation != self._head_timer_generation:
            return
        self._head_timer = None
        self._pump()

    def _durable_ack(self, ticket: _Ticket) -> None:
        if (
            not self._admitted
            or self._admitted[0] is not ticket
            or self._in_flight is not ticket
            or ticket.state is not _TicketState.ADMITTED
            or ticket.seq != self._persisted + 1
        ):
            self._poison(
                _FailureCode.LEDGER_INVARIANT,
                ticket.seq,
                persistence_impossible=True,
            )
            return

        self._admitted.popleft()
        self._queued_bytes -= ticket.nbytes
        self._persisted_bytes += ticket.nbytes
        self._persisted = ticket.seq
        ticket.state = _TicketState.PERSISTED
        self._settle_phase(ticket, "persistence", True)
        self._in_flight = None
        if not self._verify_invariants(ticket.seq):
            return
        self._pump()
        self._consumer_wakeup.set()

    # -- one absorbing failure funnel ------------------------------------

    def _poison(
        self,
        code: _FailureCode,
        ordinal: int,
        *,
        timeout_victim: _Ticket | None = None,
        persistence_impossible: bool = False,
        event_not_accepted: bool = False,
    ) -> None:
        del event_not_accepted  # documents acceptance accounting at call sites
        ordinal = max(1, ordinal)
        key = _FailureKey(ordinal, code.rank, code)
        if self._primary_failure is None or key < self._primary_failure:
            self._primary_failure = key
        if self._state is not _WriterState.CLOSED_FAILED:
            self._state = _WriterState.FAILING
        self._stop_requested = False
        self._cancel_head_timer()

        while self._pending:
            ticket = self._pending.popleft()
            self._pending_bytes -= ticket.nbytes
            ticket.state = (
                _TicketState.EXPIRED
                if ticket is timeout_victim
                else _TicketState.FAILED
            )
            self._settle_phase(ticket, "admission", False, code)
            self._settle_phase(ticket, "persistence", False, code)
        self._pending_bytes = 0
        self._pending_empty.set()

        cannot_persist = (
            persistence_impossible
            or self._consumer_state is not _ConsumerState.RUNNING
        )
        if cannot_persist:
            while self._admitted:
                ticket = self._admitted.popleft()
                ticket.state = _TicketState.FAILED_UNPERSISTED
                self._settle_phase(ticket, "persistence", False, code)
            self._queued_bytes = 0
            self._in_flight = None
        else:
            # An admission-layer failure must not discard the already-admitted
            # lower prefix.  The live consumer drains it and then exits.
            self._drain_then_exit = True
        self._consumer_wakeup.set()

    def _primary_overflow(self) -> EventWriterOverflow:
        failure = self._primary_failure
        if failure is None:
            return EventWriterOverflow(
                "event writer close could not certify persistence",
                ordinal=self._lowest_unpersisted_ordinal(),
                code=_FailureCode.CLOSE_INCONSISTENCY.name,
            )
        return _overflow(failure.code, failure.ordinal)

    def _current_or_closed_failure(self) -> EventWriterOverflow:
        if self._primary_failure is not None:
            return self._primary_overflow()
        return EventWriterOverflow(
            "event writer is closed or overflowed; the run must finalize",
            ordinal=self._accepted + 1,
            code="CLOSED",
        )

    def _require_open(self) -> None:
        if self._state not in {_WriterState.NEW, _WriterState.RUNNING}:
            raise self._current_or_closed_failure()
        if self._primary_failure is not None:
            raise self._primary_overflow()

    def _lowest_unpersisted_ordinal(self) -> int:
        return self._persisted + 1 if self._accepted > self._persisted else self._accepted + 1

    def _verify_invariants(self, ordinal: int) -> bool:
        try:
            assert 0 <= self._persisted <= self._admitted_high <= self._accepted
            assert len(self._admitted) <= self.queue_capacity
            assert self._queued_bytes == sum(ticket.nbytes for ticket in self._admitted)
            assert self._pending_bytes == sum(ticket.nbytes for ticket in self._pending)
            assert len(self._pending) <= self._policy.max_event_capacity
            assert self._queued_bytes <= self.queued_byte_budget
            assert (
                self._queued_bytes + self._pending_bytes
                <= self._policy.max_queued_bytes
            )
            if self._in_flight is not None:
                assert self._admitted and self._admitted[0] is self._in_flight
            return True
        except Exception:
            self._poison(
                _FailureCode.LEDGER_INVARIANT,
                ordinal,
                persistence_impossible=True,
            )
            return False

    # -- producer surfaces ------------------------------------------------

    async def emit(self, event: Mapping[str, Any]) -> None:
        observation = self._submit(event, "admission")
        if observation is not None:
            await observation

    def emit_ordered(self, event: Mapping[str, Any]) -> Awaitable[None] | None:
        return self._submit(event, "admission")

    async def emit_awaited(self, event: Mapping[str, Any]) -> None:
        if not self._consumer_live():
            ordinal = self._accepted + 1
            self._poison(
                _FailureCode.NO_CONSUMER,
                ordinal,
                persistence_impossible=True,
                event_not_accepted=True,
            )
            raise self._primary_overflow()
        observation = self._submit(event, "persistence")
        assert observation is not None
        await observation

    # -- synchronous close cutoff and owned join -------------------------

    def close(self) -> Awaitable[None]:
        """Establish the cutoff now, then return a cancellation-safe join."""
        if self._close_task is None:
            if self._state in {_WriterState.NEW, _WriterState.RUNNING}:
                self._state = _WriterState.CLOSING
                self._pump()
            self._close_task = asyncio.get_running_loop().create_task(
                self._close_owned(), name="event-writer-close"
            )
            self._close_task.add_done_callback(self._on_close_done)
        return _CloseJoin(self, self._close_task)

    async def _close_owned(self) -> None:
        consumer = self._consumer
        if self._consumer_state is _ConsumerState.NOT_STARTED:
            if self._accepted == 0 and self._primary_failure is None:
                self._cancel_head_timer()
                self._state = _WriterState.CLOSED_OK
                return
            self._poison(
                _FailureCode.NO_CONSUMER,
                self._lowest_unpersisted_ordinal(),
                persistence_impossible=True,
            )
            self._state = _WriterState.CLOSED_FAILED
            raise self._primary_overflow()

        if consumer is None:
            self._poison(
                _FailureCode.CLOSE_INCONSISTENCY,
                self._lowest_unpersisted_ordinal(),
                persistence_impossible=True,
            )
        else:
            while self._pending:
                self._pending_empty.clear()
                self._pump()
                if not self._pending:
                    break
                await self._pending_empty.wait()

            if self._state is _WriterState.CLOSING and self._primary_failure is None:
                # Private control state, not a producer ticket: no sequence,
                # byte charge, capacity slot, deadline, or observer ownership.
                self._stop_requested = True
                self._consumer_wakeup.set()

            try:
                await asyncio.shield(consumer)
            except asyncio.CancelledError:
                if not consumer.done():
                    raise
            except Exception:
                # The done callback and consumer finally own failure truth.
                pass

            # Retrieve the outcome here as well as in the callback; result()
            # retrieval is idempotent and closes callback-order races.
            if consumer.done():
                if consumer.cancelled() and self._consumer_state is _ConsumerState.RUNNING:
                    self._poison(
                        _FailureCode.CONSUMER_CANCELLED,
                        self._lowest_unpersisted_ordinal(),
                        persistence_impossible=True,
                    )
                    self._consumer_state = _ConsumerState.EXITED_ERROR
                elif not consumer.cancelled():
                    with contextlib.suppress(BaseException):
                        consumer.result()
            self._consumer = None

        self._cancel_head_timer()
        clean = self._primary_failure is None and (
            (
                self._consumer_state is _ConsumerState.NOT_STARTED
                and self._accepted == 0
            )
            or (
                self._consumer_state is _ConsumerState.EXITED_CLOSE_STOP
                and self._persisted == self._accepted
            )
        )
        if clean:
            self._state = _WriterState.CLOSED_OK
            return

        if self._primary_failure is None:
            self._poison(
                _FailureCode.CLOSE_INCONSISTENCY,
                self._lowest_unpersisted_ordinal(),
                persistence_impossible=True,
            )
        self._state = _WriterState.CLOSED_FAILED
        raise self._primary_overflow()

    def _on_close_done(self, task: asyncio.Task[None]) -> None:
        self._cache_close_outcome(task)

    def _cache_close_outcome(self, task: asyncio.Task[None]) -> None:
        if self._close_succeeded or self._close_failure is not None:
            # The task's result was already retrieved and cached.
            return
        try:
            task.result()
        except asyncio.CancelledError:
            if self._primary_failure is None:
                self._poison(
                    _FailureCode.CLOSE_INCONSISTENCY,
                    self._lowest_unpersisted_ordinal(),
                    persistence_impossible=True,
                )
            self._close_failure = self._primary_overflow()
            self._state = _WriterState.CLOSED_FAILED
        except EventWriterOverflow as exc:
            self._close_failure = EventWriterOverflow(
                str(exc), ordinal=exc.ordinal, code=exc.code
            )
        except Exception:
            self._poison(
                _FailureCode.CLOSE_INCONSISTENCY,
                self._lowest_unpersisted_ordinal(),
                persistence_impossible=True,
            )
            self._close_failure = self._primary_overflow()
            self._state = _WriterState.CLOSED_FAILED
        else:
            self._close_succeeded = True


class _MarkerTooLarge(Exception):
    pass

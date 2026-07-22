"""Per-Run bounded single-writer event stream (R9).

One writer owns each Run's ``normalized-events.jsonl``: monotonic ``seq``
starting at 1, a bounded queue whose producer timeout is a controlled
run-failure signal (evidence is never silently dropped and never unbounded),
a finite ``max_events`` reservation, and a per-event byte cap that truncates
to a marker while preserving the event's family. The writer appends through
the run handle it is given — it constructs no store.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

from .spec import (
    LIMIT_MAX_EVENT_BYTES_MAX,
    LIMIT_MAX_EVENT_BYTES_MIN,
    LIMIT_MAX_EVENTS_MAX,
)

EVENTS_FILENAME = "normalized-events.jsonl"

DEFAULT_QUEUE_MAXSIZE = 256
DEFAULT_PRODUCER_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_EVENTS = 10_000


class EventWriterOverflow(RuntimeError):
    """Bounded-evidence violation: the Run must fail in a controlled way."""


class EventWriter:
    def __init__(
        self,
        handle: Any,
        *,
        max_event_bytes: int,
        max_events: int = DEFAULT_MAX_EVENTS,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        producer_timeout_seconds: float = DEFAULT_PRODUCER_TIMEOUT_SECONDS,
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
        self._handle = handle
        self._max_event_bytes = max_event_bytes
        self._max_events = max_events
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._producer_timeout = producer_timeout_seconds
        self._filename = filename
        self._consumer: asyncio.Task[None] | None = None
        self._seq = 0
        self._reserved = 0
        self._closed = False
        self.overflowed = False

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def can_accept(self) -> bool:
        return (
            not self._closed
            and not self.overflowed
            and self._reserved < self._max_events
        )

    async def start(self) -> None:
        if self._consumer is None:
            self._consumer = asyncio.ensure_future(self._drain())

    def _reserve_slot(self) -> None:
        """Reserve one event slot synchronously (single event-loop safe)."""
        if self._closed or self.overflowed:
            raise EventWriterOverflow(
                "event writer is closed or overflowed; the run must finalize"
            )
        if self._reserved >= self._max_events:
            self.overflowed = True
            raise EventWriterOverflow("event writer max_events exceeded")
        self._reserved += 1

    def _release_slot(self) -> None:
        if self._reserved > 0:
            self._reserved -= 1

    async def emit(self, event: Mapping[str, Any]) -> None:
        """Enqueue one event; a full queue past the timeout is a controlled
        run-failure signal, never an unbounded buffer or a silent drop."""
        self._reserve_slot()
        try:
            await asyncio.wait_for(
                self._queue.put(dict(event)), self._producer_timeout
            )
        except asyncio.TimeoutError:
            self._release_slot()
            self.overflowed = True
            raise EventWriterOverflow(
                "event queue stayed full past the producer timeout"
            ) from None

    def emit_nowait(self, event: Mapping[str, Any]) -> None:
        """Sync-context variant (SDK callback paths): a full queue is an
        immediate controlled failure signal instead of a wait."""
        self._reserve_slot()
        try:
            self._queue.put_nowait(dict(event))
        except asyncio.QueueFull:
            self._release_slot()
            self.overflowed = True
            raise EventWriterOverflow("event queue is full") from None

    async def close(self) -> None:
        """Flush queued events and stop the consumer.

        If the consumer has already failed and the queue is full, awaiting
        ``queue.put(None)`` alone would hang forever. Race the sentinel enqueue
        against consumer completion: a finished/failed consumer cancels any
        pending put and surfaces its exception promptly; a successful sentinel
        enqueue awaits the consumer normally. The consumer reference is always
        cleared; append failures are never swallowed.
        """
        if self._closed:
            return
        self._closed = True
        consumer = self._consumer
        if consumer is None:
            return
        put_task: asyncio.Task[None] | None = None
        try:
            put_task = asyncio.ensure_future(self._queue.put(None))
            done, _pending = await asyncio.wait(
                {put_task, consumer},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if consumer in done:
                if put_task is not None and not put_task.done():
                    put_task.cancel()
                    try:
                        await put_task
                    except asyncio.CancelledError:
                        pass
                # Re-raise consumer failure (or complete successfully).
                await consumer
                return
            # Sentinel enqueue won: finish put, then drain/await consumer.
            await put_task
            await consumer
        finally:
            self._consumer = None

    async def _drain(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            self._seq += 1
            record = self._bounded(item)
            record["seq"] = self._seq
            await asyncio.to_thread(
                self._handle.append_ndjson, self._filename, record
            )

    def _bounded(self, event: dict[str, Any]) -> dict[str, Any]:
        rendered = json.dumps(event, sort_keys=True)
        # +32 headroom covers the seq key added after bounding.
        if len(rendered) + 32 <= self._max_event_bytes:
            return event
        # Truncate to a marker while preserving the family, so lifecycle,
        # permission, and error events keep their meaning under the cap.
        return {
            "type": event.get("type") or "unknown_update",
            "truncated": True,
            "truncate_reason": "max_event_bytes",
        }

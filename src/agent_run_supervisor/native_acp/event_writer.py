"""Per-Run bounded single-writer event stream (R9).

One writer owns each Run's ``normalized-events.jsonl``: monotonic ``seq``
starting at 1, a bounded queue whose producer timeout is a controlled
run-failure signal (evidence is never silently dropped and never unbounded),
and a per-event byte cap that truncates to a marker while preserving the
event's family. The writer appends through the run handle it is given — it
constructs no store.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

EVENTS_FILENAME = "normalized-events.jsonl"

DEFAULT_QUEUE_MAXSIZE = 256
DEFAULT_PRODUCER_TIMEOUT_SECONDS = 5.0


class EventWriterOverflow(RuntimeError):
    """Bounded-evidence violation: the Run must fail in a controlled way."""


class EventWriter:
    def __init__(
        self,
        handle: Any,
        *,
        max_event_bytes: int,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        producer_timeout_seconds: float = DEFAULT_PRODUCER_TIMEOUT_SECONDS,
        filename: str = EVENTS_FILENAME,
    ) -> None:
        self._handle = handle
        self._max_event_bytes = max_event_bytes
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._producer_timeout = producer_timeout_seconds
        self._filename = filename
        self._consumer: asyncio.Task[None] | None = None
        self._seq = 0
        self._closed = False
        self.overflowed = False

    @property
    def last_seq(self) -> int:
        return self._seq

    async def start(self) -> None:
        if self._consumer is None:
            self._consumer = asyncio.ensure_future(self._drain())

    async def emit(self, event: Mapping[str, Any]) -> None:
        """Enqueue one event; a full queue past the timeout is a controlled
        run-failure signal, never an unbounded buffer or a silent drop."""
        if self._closed or self.overflowed:
            raise EventWriterOverflow(
                "event writer is closed or overflowed; the run must finalize"
            )
        try:
            await asyncio.wait_for(
                self._queue.put(dict(event)), self._producer_timeout
            )
        except asyncio.TimeoutError:
            self.overflowed = True
            raise EventWriterOverflow(
                "event queue stayed full past the producer timeout"
            ) from None

    async def close(self) -> None:
        """Flush queued events and stop the consumer."""
        if self._closed:
            return
        self._closed = True
        if self._consumer is not None:
            await self._queue.put(None)
            await self._consumer
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

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field
from typing import Any

from agent_run_supervisor.event_store import RunHandle
from agent_run_supervisor.parser import (
    IncrementalParseState,
    ParseResult,
    feed_acpx_bytes,
    finish_acpx_bytes,
)
from agent_run_supervisor.redaction import RedactionReport, redact_text


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


@dataclass
class LiveEventSink:
    """Persist acpx stdout and safe normalized events as stdout arrives.

    The sink is deliberately a projection writer: raw stdout is redacted before
    persistence, normalized events contain structural fields only, and
    ``progress.json`` never includes raw agent text.
    """

    handle: RunHandle
    redaction_report: RedactionReport
    state: IncrementalParseState | None = None
    max_output_bytes: int | None = None
    last_seq: int = 0
    event_count: int = 0
    started: bool = False
    bytes_seen: int = 0
    _finished: bool = False
    _disabled: bool = False
    _parsing_closed: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = IncrementalParseState()

    @property
    def result(self) -> ParseResult:
        assert self.state is not None
        return self.state.result

    def feed(self, chunk: bytes) -> None:
        """Consume one streamed stdout chunk/line and persist safe live artifacts."""
        with self._lock:
            if self._disabled:
                return
            self.started = True
            text = chunk.decode("utf-8", errors="replace")
            redacted, report = redact_text(text, location="acpx_stdout")
            if self._disabled:
                return
            self.redaction_report.merge(report)
            if self._disabled:
                return
            self.handle.append_text("acpx-stdout.ndjson", redacted)

            parse_chunk = self._bounded_parse_chunk(chunk)
            assert self.state is not None
            if parse_chunk and not self._parsing_closed:
                for event in feed_acpx_bytes(self.state, parse_chunk):
                    if self._disabled:
                        return
                    self._append_event(event)
            if not self._disabled:
                self.write_progress("running")

    def disable(self) -> None:
        """Stop accepting future live chunks before a fallback reparse/finalization."""
        with self._lock:
            self._disabled = True

    def _bounded_parse_chunk(self, chunk: bytes) -> bytes:
        if self.max_output_bytes is None:
            return chunk
        remaining = self.max_output_bytes - self.bytes_seen
        self.bytes_seen += len(chunk)
        if remaining <= 0:
            self._mark_truncated()
            return b""
        if len(chunk) > remaining:
            self._mark_truncated()
            return chunk[:remaining]
        return chunk

    def _mark_truncated(self) -> None:
        result = self.result
        if not result.truncated:
            result.truncated = True
            result.truncate_reason = "max_output_bytes"
            result.protocol_error = True
            result.protocol_error_reasons.append("stdout exceeded max_output_bytes")

    def _append_event(self, event: dict[str, Any]) -> None:
        self.last_seq += 1
        self.event_count += 1
        payload = dict(event)
        payload["seq"] = self.last_seq
        self.handle.append_ndjson("normalized-events.jsonl", payload)

    def write_progress(self, state: str) -> None:
        self.handle.write_json(
            "progress.json",
            {
                "schema_version": 1,
                "state": state,
                "last_seq": self.last_seq,
                "event_count": self.event_count,
                "updated_at": _utc_now_iso(),
            },
        )

    def finish(self, state: str) -> ParseResult:
        with self._lock:
            if not self._finished:
                assert self.state is not None
                for event in finish_acpx_bytes(self.state):
                    self._append_event(event)
                self._finished = True
            self.write_progress(state)
            return self.result

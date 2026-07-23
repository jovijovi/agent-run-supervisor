"""Typed local AF_UNIX client for the arsd v1 protocol."""

from __future__ import annotations

import contextlib
import itertools
import socket
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from . import protocol

__all__ = [
    "ArsdClient",
    "ArsdClientError",
    "ArsdFollowSubscription",
    "ERROR_CODE_TO_EXCEPTION",
]

# Local, stable exception text only — never echo untrusted remote ``message``.
_GENERIC_ERROR_MESSAGE = "arsd protocol error"


def _local_message(message: str | None = None) -> str:
    """Bound a *local* message. Remote server text must never reach here."""
    text = "" if message is None else str(message)
    return text[: protocol.MAX_ERROR_MESSAGE_CHARS] or _GENERIC_ERROR_MESSAGE


class ArsdClientError(Exception):
    """Fail-closed client error. Exception text never echoes raw payloads."""

    code: str = protocol.INTERNAL

    def __init__(self, code: str | None = None, message: str | None = None) -> None:
        resolved = code or type(self).code
        if resolved not in protocol.ERROR_CODES_V1 and resolved != "CLIENT":
            resolved = protocol.INTERNAL
        # Only caller-supplied *local* messages are accepted; remote wire
        # messages are discarded by raise_for_error_code / _raise_error_frame.
        safe = _local_message(message)
        super().__init__(f"{resolved}: {safe}")
        self.code = resolved
        self.message = safe


def _make_error_type(code: str) -> type[ArsdClientError]:
    name = "Arsd" + "".join(part.capitalize() for part in code.split("_")) + "Error"
    return type(name, (ArsdClientError,), {"code": code, "__module__": __name__})


ERROR_CODE_TO_EXCEPTION: dict[str, type[ArsdClientError]] = {
    code: _make_error_type(code) for code in sorted(protocol.ERROR_CODES_V1)
}

# Stable named aliases for the closed v1 set (import-friendly).
ArsdUnsupportedApiVersionError = ERROR_CODE_TO_EXCEPTION[protocol.UNSUPPORTED_API_VERSION]
ArsdUnknownOpError = ERROR_CODE_TO_EXCEPTION[protocol.UNKNOWN_OP]
ArsdMalformedFrameError = ERROR_CODE_TO_EXCEPTION[protocol.MALFORMED_FRAME]
ArsdFrameTooLargeError = ERROR_CODE_TO_EXCEPTION[protocol.FRAME_TOO_LARGE]
ArsdInvalidRequestError = ERROR_CODE_TO_EXCEPTION[protocol.INVALID_REQUEST]
ArsdUnauthenticatedPeerError = ERROR_CODE_TO_EXCEPTION[protocol.UNAUTHENTICATED_PEER]
ArsdPeerUidDeniedError = ERROR_CODE_TO_EXCEPTION[protocol.PEER_UID_DENIED]
ArsdOwnerMismatchError = ERROR_CODE_TO_EXCEPTION[protocol.OWNER_MISMATCH]
ArsdIdempotencyConflictError = ERROR_CODE_TO_EXCEPTION[protocol.IDEMPOTENCY_CONFLICT]
ArsdSubmissionIndeterminateError = ERROR_CODE_TO_EXCEPTION[
    protocol.SUBMISSION_INDETERMINATE
]
ArsdUnknownRunError = ERROR_CODE_TO_EXCEPTION[protocol.UNKNOWN_RUN]
ArsdUnknownSessionError = ERROR_CODE_TO_EXCEPTION[protocol.UNKNOWN_SESSION]
ArsdSessionBusyError = ERROR_CODE_TO_EXCEPTION[protocol.SESSION_BUSY]
ArsdCapacityExhaustedError = ERROR_CODE_TO_EXCEPTION[protocol.CAPACITY_EXHAUSTED]
ArsdEventBacklogExceededError = ERROR_CODE_TO_EXCEPTION[protocol.EVENT_BACKLOG_EXCEEDED]
ArsdShuttingDownError = ERROR_CODE_TO_EXCEPTION[protocol.SHUTTING_DOWN]
ArsdInternalError = ERROR_CODE_TO_EXCEPTION[protocol.INTERNAL]

for _exc in ERROR_CODE_TO_EXCEPTION.values():
    globals()[_exc.__name__] = _exc


def raise_for_error_code(code: str, message: str | None = None) -> None:
    """Raise the typed v1 exception for ``code``.

    ``message`` must be a local stable string. Remote server ``message`` fields
    are never passed through — use the default generic text.
    """
    del message  # remote/untrusted content is intentionally discarded
    exc_type = ERROR_CODE_TO_EXCEPTION.get(code, ArsdClientError)
    raise exc_type(code, _GENERIC_ERROR_MESSAGE)


class ArsdFollowSubscription:
    """Connection-exclusive follow stream.

    ``for``-iteration yields an ephemeral generator so ``break`` drops the last
    strong reference and the generator ``finally`` closes this client. Explicit
    ``.close()`` / context exit are also supported. Never sends ``run_cancel``.
    """

    def __init__(self, client: ArsdClient, request_id: str) -> None:
        self._client = client
        self._request_id = request_id
        self._closed = False
        # Strong ref only for ``next(subscription)`` pull-style use.
        self._pull_gen: Iterator[dict[str, Any]] | None = None

    def _frames(self) -> Iterator[dict[str, Any]]:
        try:
            while True:
                if self._client.closed:
                    return
                try:
                    frame = self._client._read_frame(allow_clean_eof=True)
                except ArsdClientError:
                    self._client.close()
                    raise
                if frame is None:
                    # Clean EOF with no partial frame: normal completion.
                    return
                try:
                    yield self._client._parse_result_frame(
                        frame, expected_request_id=self._request_id
                    )
                except ArsdClientError:
                    self._client.close()
                    raise
        finally:
            self.close(from_generator=True)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        # Ephemeral generator: for-break POP_TOP drops the last strong ref and
        # runs ``finally`` deterministically without relying on a stored handle.
        return self._frames()

    def __next__(self) -> dict[str, Any]:
        if self._pull_gen is None:
            self._pull_gen = self._frames()
        return next(self._pull_gen)

    def close(self, *, from_generator: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        # Follow teardown always closes this client connection; never cancels a Run.
        self._client._release_follow(self)
        if not from_generator and self._pull_gen is not None:
            gen = self._pull_gen
            self._pull_gen = None
            with contextlib.suppress(Exception):
                gen.close()

    def __enter__(self) -> ArsdFollowSubscription:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


class ArsdClient:
    """Context-managed local caller. Never silently reconnects or replays."""

    def __init__(self, socket_path: Path | str, *, api_version: int | None = None) -> None:
        self._socket_path = Path(socket_path)
        self._api_version = (
            protocol.ARSD_API_VERSION if api_version is None else api_version
        )
        self._sock: socket.socket | None = None
        self._recv_buf = bytearray()
        self._closed = True
        self._req_counter = itertools.count(1)
        self._follow: ArsdFollowSubscription | None = None

    @property
    def closed(self) -> bool:
        return self._closed or self._sock is None

    def __enter__(self) -> ArsdClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        if self._sock is not None and not self._closed:
            raise ArsdClientError("CLIENT", "client is already connected")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self._socket_path))
        except OSError:
            sock.close()
            raise ArsdClientError(
                protocol.INTERNAL, "failed to connect to arsd socket"
            ) from None
        self._sock = sock
        self._recv_buf.clear()
        self._closed = False
        self._follow = None

    def close(self) -> None:
        follow = self._follow
        self._follow = None
        if follow is not None:
            follow._closed = True
        sock = self._sock
        self._sock = None
        self._closed = True
        self._recv_buf.clear()
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()

    def server_info(self, *, request_id: str | None = None) -> dict[str, Any]:
        return self._roundtrip("server_info", {}, request_id=request_id)

    def submit(
        self,
        *,
        request_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._roundtrip("submit", dict(payload), request_id=request_id)

    def run_status(self, run_id: str, *, request_id: str | None = None) -> dict[str, Any]:
        return self._roundtrip(
            "run_status", {"run_id": run_id}, request_id=request_id
        )

    def run_events(
        self,
        run_id: str,
        *,
        from_seq: int = 0,
        limit: int | None = None,
        follow: bool = False,
        follow_idle_seconds: float | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any] | ArsdFollowSubscription:
        payload: dict[str, Any] = {
            "run_id": run_id,
            "from_seq": from_seq,
            "follow": follow,
        }
        if limit is not None:
            payload["limit"] = limit
        if follow_idle_seconds is not None:
            payload["follow_idle_seconds"] = follow_idle_seconds
        if not follow:
            return self._roundtrip("run_events", payload, request_id=request_id)
        return self._follow_start("run_events", payload, request_id=request_id)

    def run_cancel(self, run_id: str, *, request_id: str | None = None) -> dict[str, Any]:
        return self._roundtrip(
            "run_cancel", {"run_id": run_id}, request_id=request_id
        )

    def session_status(
        self, session_id: str, *, request_id: str | None = None
    ) -> dict[str, Any]:
        return self._roundtrip(
            "session_status", {"session_id": session_id}, request_id=request_id
        )

    def session_list(self, *, request_id: str | None = None) -> dict[str, Any]:
        return self._roundtrip("session_list", {}, request_id=request_id)

    def session_close(
        self, session_id: str, *, request_id: str | None = None
    ) -> dict[str, Any]:
        return self._roundtrip(
            "session_close", {"session_id": session_id}, request_id=request_id
        )

    def _next_request_id(self) -> str:
        return f"cli-{next(self._req_counter)}"

    def _require_sock(self) -> socket.socket:
        if self._sock is None or self._closed:
            raise ArsdClientError("CLIENT", "client is not connected")
        return self._sock

    def _require_idle(self) -> None:
        if self._follow is not None:
            raise ArsdClientError(
                "CLIENT", "follow subscription is active on this connection"
            )

    def _roundtrip(
        self,
        op: str,
        payload: Mapping[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        self._require_idle()
        rid = request_id or self._next_request_id()
        self._send_request(op, rid, payload)
        frame = self._read_frame(allow_clean_eof=False)
        assert frame is not None
        return self._parse_result_frame(frame, expected_request_id=rid)

    def _follow_start(
        self,
        op: str,
        payload: Mapping[str, Any],
        *,
        request_id: str | None,
    ) -> ArsdFollowSubscription:
        self._require_idle()
        rid = request_id or self._next_request_id()
        self._send_request(op, rid, payload)
        sub = ArsdFollowSubscription(self, rid)
        self._follow = sub
        return sub

    def _release_follow(self, sub: ArsdFollowSubscription) -> None:
        if self._follow is sub:
            self._follow = None
        # Follow teardown always closes this client connection; never cancels a Run.
        if not self.closed:
            self.close()

    def _send_request(
        self, op: str, request_id: str, payload: Mapping[str, Any]
    ) -> None:
        sock = self._require_sock()
        envelope = {
            "api_version": self._api_version,
            "op": op,
            "request_id": request_id,
            "payload": dict(payload),
        }
        try:
            data = protocol.encode_frame(envelope)
        except protocol.ProtocolError as err:
            # Local encode failures use the stable code with a generic message.
            raise_for_error_code(err.code)
        try:
            sock.sendall(data)
        except OSError:
            self.close()
            raise ArsdClientError(protocol.INTERNAL, "socket write failed") from None

    def _read_frame(self, *, allow_clean_eof: bool) -> dict[str, Any] | None:
        sock = self._require_sock()
        while True:
            newline = self._recv_buf.find(b"\n")
            if newline >= 0:
                raw = bytes(self._recv_buf[: newline + 1])
                del self._recv_buf[: newline + 1]
                if len(raw) > protocol.MAX_FRAME_BYTES:
                    self.close()
                    raise ArsdFrameTooLargeError(
                        protocol.FRAME_TOO_LARGE, _GENERIC_ERROR_MESSAGE
                    )
                try:
                    return protocol.decode_frame(raw)
                except protocol.ProtocolError as err:
                    self.close()
                    raise_for_error_code(err.code)
            if len(self._recv_buf) >= protocol.MAX_FRAME_BYTES:
                self.close()
                raise ArsdFrameTooLargeError(
                    protocol.FRAME_TOO_LARGE, _GENERIC_ERROR_MESSAGE
                )
            remaining = protocol.MAX_FRAME_BYTES - len(self._recv_buf)
            try:
                chunk = sock.recv(min(65_536, remaining + 1))
            except OSError:
                self.close()
                raise ArsdClientError(protocol.INTERNAL, "socket read failed") from None
            if not chunk:
                if self._recv_buf:
                    self.close()
                    raise ArsdClientError(
                        protocol.MALFORMED_FRAME, "truncated frame at EOF"
                    )
                if allow_clean_eof:
                    return None
                self.close()
                raise ArsdClientError(protocol.INTERNAL, "socket closed by peer")
            self._recv_buf.extend(chunk)
            if len(self._recv_buf) > protocol.MAX_FRAME_BYTES and b"\n" not in self._recv_buf:
                self.close()
                raise ArsdFrameTooLargeError(
                    protocol.FRAME_TOO_LARGE, _GENERIC_ERROR_MESSAGE
                )

    def _parse_result_frame(
        self, frame: Mapping[str, Any], *, expected_request_id: str
    ) -> dict[str, Any]:
        if not isinstance(frame, Mapping):
            self.close()
            raise ArsdMalformedFrameError(
                protocol.MALFORMED_FRAME, _GENERIC_ERROR_MESSAGE
            )
        request_id = frame.get("request_id")
        has_error = "error" in frame
        if has_error:
            # Correlation first: a non-null mismatched request_id is a local
            # failure and must not be misattributed as the server error code.
            # A null request_id is the explicit v1 ``build_error(None, ...)``
            # wire shape used for connection/pre-parse denials.
            if request_id is not None and request_id != expected_request_id:
                self.close()
                raise ArsdClientError(
                    protocol.INVALID_REQUEST, "response request_id mismatch"
                )
            self._raise_error_frame(frame)
        if request_id != expected_request_id:
            self.close()
            raise ArsdClientError(
                protocol.INVALID_REQUEST, "response request_id mismatch"
            )
        result = frame.get("result")
        if not isinstance(result, dict):
            self.close()
            raise ArsdMalformedFrameError(
                protocol.MALFORMED_FRAME, _GENERIC_ERROR_MESSAGE
            )
        return result

    def _raise_error_frame(self, frame: Mapping[str, Any]) -> None:
        error = frame.get("error")
        if not isinstance(error, Mapping):
            self.close()
            raise ArsdMalformedFrameError(
                protocol.MALFORMED_FRAME, _GENERIC_ERROR_MESSAGE
            )
        code = error.get("code")
        if not isinstance(code, str) or code not in protocol.ERROR_CODES_V1:
            self.close()
            raise ArsdInternalError(protocol.INTERNAL, _GENERIC_ERROR_MESSAGE)
        # Discard untrusted remote ``message`` entirely.
        self.close()
        raise_for_error_code(code)

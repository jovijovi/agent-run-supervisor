"""Official-SDK ``Client`` implementation translating callbacks inward.

SDK model objects never cross this boundary: session updates, permission
requests, and fs requests are dumped to wire-shaped plain dicts before they
reach the internal sinks (C7's bridge/normalizer). The SDK itself is loaded
lazily via :func:`require_sdk`, so importing this module works without the
``native`` extra; *constructing* a client is SDK use and raises the typed
error instead.

Default posture is deny: permission requests resolve through the injected
handler (falling back to deny), fs handlers refuse unless injected, and the
terminal/elicitation surfaces always refuse — the client declared them
unsupported at initialize.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from . import require_sdk

UpdateSink = Callable[[str, dict[str, Any]], None]
PermissionHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
FsReadHandler = Callable[[dict[str, Any]], Awaitable[str]]
FsWriteHandler = Callable[[dict[str, Any]], Awaitable[None]]

# The only thing an identity failure is ever allowed to say. It carries no
# expected id, no observed id, and no derivative of either.
SESSION_IDENTITY_VIOLATION = "SESSION_IDENTITY_VIOLATION"


class SessionIdentityViolation(RuntimeError):
    """A callback arrived for a different external session than expected."""


class UpdateCallbackError(RuntimeError):
    """A session-update callback failed; update delivery cannot be proven."""


class NativeAcpClient:
    """Callback surface handed to ``ClientSideConnection``."""

    def __init__(
        self,
        *,
        on_update: UpdateSink,
        permission_handler: PermissionHandler | None = None,
        fs_read_handler: FsReadHandler | None = None,
        fs_write_handler: FsWriteHandler | None = None,
    ) -> None:
        self._sdk = require_sdk()
        self._on_update = on_update
        self._permission_handler = permission_handler
        self._fs_read_handler = fs_read_handler
        self._fs_write_handler = fs_write_handler
        self.expected_session_id: str | None = None
        self.identity_violation: str | None = None
        # Monotonic completed-update-callback counter + wakeup for the
        # driver's pre-response delivery barrier (the SDK resolves request
        # futures without awaiting queued notification handlers).
        self._updates_completed = 0
        self._update_event = asyncio.Event()
        self.callback_failure: str | None = None

    # -- helpers -----------------------------------------------------------

    def _require_session_id(self, session_id: Any) -> None:
        """Compare first; on unbound-or-different, record and raise. Nothing else.

        This runs at callback entry, before normalization, queueing, handler
        invocation, filesystem access, sink persistence, or the formulation of
        any response — including the unsupported-surface refusal, which is
        still a response. An unbound expectation is itself a violation, so a
        callback racing ahead of the bind cannot be serviced either.
        """
        expected = self.expected_session_id
        if expected is None or session_id != expected:
            if self.identity_violation is None:
                self.identity_violation = SESSION_IDENTITY_VIOLATION
            raise SessionIdentityViolation(SESSION_IDENTITY_VIOLATION)

    @staticmethod
    def _dump(model: Any) -> dict[str, Any]:
        dump = getattr(model, "model_dump", None)
        if callable(dump):
            return dump(by_alias=True, exclude_none=True, warnings=False)
        return dict(model)

    # -- update delivery barrier -------------------------------------------

    @property
    def updates_completed(self) -> int:
        return self._updates_completed

    async def wait_for_updates_completed(self, target: int) -> None:
        """Await the monotonic completion counter reaching ``target``.

        Fails closed if any update callback failed. The caller owns the time
        bound (the Run's turn timeout); this never polls or sleeps.
        """
        while self._updates_completed < target and self.callback_failure is None:
            self._update_event.clear()
            if self._updates_completed >= target or self.callback_failure is not None:
                break
            await self._update_event.wait()
        if self.callback_failure is not None:
            raise UpdateCallbackError(self.callback_failure)

    # -- Client protocol ---------------------------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        try:
            # Identity first: a rejected frame is never dumped, normalized, or
            # handed to the sink.
            self._require_session_id(session_id)
            self._on_update(session_id, self._dump(update))
        except Exception as exc:
            if self.callback_failure is None:
                self.callback_failure = f"session_update callback failed: {exc}"
            raise
        finally:
            # The delivery barrier must still advance or shutdown would hang —
            # but it carries no payload and services nothing.
            self._updates_completed += 1
            self._update_event.set()

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: Any,
        **kwargs: Any,
    ) -> Any:
        self._require_session_id(session_id)
        schema = self._sdk.schema
        request = {
            "session_id": session_id,
            "tool_call": self._dump(tool_call),
            "options": [self._dump(option) for option in options or []],
        }
        decision: dict[str, Any] = {"decision": "deny", "reason": "no handler"}
        if self._permission_handler is not None:
            decision = await self._permission_handler(request)
        if decision.get("decision") == "allow" and decision.get("option_id"):
            outcome = schema.AllowedOutcome(
                outcome="selected", option_id=decision["option_id"]
            )
        else:
            outcome = schema.DeniedOutcome(outcome="cancelled")
        return schema.RequestPermissionResponse(outcome=outcome)

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        self._require_session_id(session_id)
        if self._fs_read_handler is None:
            raise PermissionError("fs read is not permitted for this run")
        content = await self._fs_read_handler(
            {"session_id": session_id, "path": path, "line": line, "limit": limit}
        )
        return self._sdk.schema.ReadTextFileResponse(content=content)

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> Any:
        self._require_session_id(session_id)
        if self._fs_write_handler is None:
            raise PermissionError("fs write is not permitted for this run")
        await self._fs_write_handler(
            {"session_id": session_id, "path": path, "content": content}
        )
        return None

    # -- unsupported surfaces (declared absent at initialize) --------------
    #
    # Pinned SDK signatures, not varargs: the router dispatches by keyword from
    # each request model's field names, so the names *are* the contract — and a
    # surface that cannot name its ``session_id`` cannot check it. Identity is
    # compared before the refusal, because "terminal capability is not
    # provided" is itself a formulated response to a frame that was never ours
    # to answer.

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[Any] | None = None,
        env: list[Any] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        self._require_session_id(session_id)
        raise PermissionError("terminal capability is not provided")

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> Any:
        self._require_session_id(session_id)
        raise PermissionError("terminal capability is not provided")

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> Any:
        self._require_session_id(session_id)
        raise PermissionError("terminal capability is not provided")

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> Any:
        self._require_session_id(session_id)
        raise PermissionError("terminal capability is not provided")

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> Any:
        self._require_session_id(session_id)
        raise PermissionError("terminal capability is not provided")

    async def create_elicitation(self, message: str, mode: Any, **kwargs: Any) -> Any:
        """Session-scoped elicitation is identity-checked; request-scoped is not.

        The pinned SDK's ``ElicitationMode`` is a plain union and the router
        passes a **leaf** instance, so the id lives on the leaf's own
        ``session_id`` field — there is no wrapper to reach through. A
        request-scoped mode carries only a request id: it is simply
        unsupported, and no Session id is invented for it.
        """
        schema = self._sdk.schema
        session_scoped = (
            schema.ElicitationFormSessionMode,
            schema.ElicitationUrlSessionMode,
        )
        if isinstance(mode, session_scoped):
            self._require_session_id(mode.session_id)
        raise PermissionError("elicitation is not provided")

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        # Carries no Session id at all, so there is nothing to compare and
        # nothing may be invented.
        raise PermissionError("elicitation is not provided")

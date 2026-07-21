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

from typing import Any, Awaitable, Callable

from . import require_sdk

UpdateSink = Callable[[str, dict[str, Any]], None]
PermissionHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
FsReadHandler = Callable[[dict[str, Any]], Awaitable[str]]
FsWriteHandler = Callable[[dict[str, Any]], Awaitable[None]]


class SessionIdentityViolation(RuntimeError):
    """An update arrived for a different external session than expected."""


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

    # -- helpers -----------------------------------------------------------

    def _observe_session_id(self, session_id: str) -> None:
        if (
            self.expected_session_id is not None
            and session_id != self.expected_session_id
            and self.identity_violation is None
        ):
            self.identity_violation = (
                f"agent switched external session identity: expected "
                f"{self.expected_session_id!r}, observed {session_id!r}"
            )

    @staticmethod
    def _dump(model: Any) -> dict[str, Any]:
        dump = getattr(model, "model_dump", None)
        if callable(dump):
            return dump(by_alias=True, exclude_none=True, warnings=False)
        return dict(model)

    # -- Client protocol ---------------------------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self._observe_session_id(session_id)
        self._on_update(session_id, self._dump(update))

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: Any,
        **kwargs: Any,
    ) -> Any:
        self._observe_session_id(session_id)
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
        self._observe_session_id(session_id)
        if self._fs_read_handler is None:
            raise PermissionError("fs read is not permitted for this run")
        content = await self._fs_read_handler(
            {"session_id": session_id, "path": path, "line": line, "limit": limit}
        )
        return self._sdk.schema.ReadTextFileResponse(content=content)

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> Any:
        self._observe_session_id(session_id)
        if self._fs_write_handler is None:
            raise PermissionError("fs write is not permitted for this run")
        await self._fs_write_handler(
            {"session_id": session_id, "path": path, "content": content}
        )
        return None

    # -- unsupported surfaces (declared absent at initialize) --------------

    async def create_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("terminal capability is not provided")

    async def terminal_output(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("terminal capability is not provided")

    async def release_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("terminal capability is not provided")

    async def wait_for_terminal_exit(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("terminal capability is not provided")

    async def kill_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("terminal capability is not provided")

    async def create_elicitation(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("elicitation is not provided")

    async def complete_elicitation(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("elicitation is not provided")

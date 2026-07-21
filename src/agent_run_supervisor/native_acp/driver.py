"""NativeAcpDriver: the ACP wire/state machine over a supplied ManagedProcess.

The driver never spawns and never selects policy or profile — it receives an
already-supervised process and owns only the JSON-RPC conversation plus the
config-fidelity sequence. Every SDK/wire failure is converted into a typed
:class:`NativeDriverError`; fidelity violations surface as
:class:`ConfigFidelityError` from the machine. Timeout ownership stays with
the caller (RunTask) via ``asyncio.wait_for``.
"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agent_run_supervisor.managed_process import ManagedProcess

from . import require_sdk
from .client import NativeAcpClient
from .config_fidelity import ConfigFidelityError, ConfigFidelityMachine


class NativeDriverError(RuntimeError):
    """Controlled wire/protocol failure of the native ACP conversation."""


@dataclass(frozen=True)
class InitializeSummary:
    protocol_version: int
    agent_info: dict[str, Any]
    load_session_advertised: bool
    capabilities: dict[str, Any]


@dataclass(frozen=True)
class PromptOutcome:
    stop_reason: str
    usage: dict[str, Any] | None


def _dump(model: Any) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True, exclude_none=True, warnings=False)
    return dict(model) if model is not None else {}


def _dump_options(config_options: Any) -> list[dict[str, Any]] | None:
    if config_options is None:
        return None
    return [_dump(option) for option in config_options]


class NativeAcpDriver:
    def __init__(self, *, client: NativeAcpClient, machine: ConfigFidelityMachine) -> None:
        self._client = client
        self._machine = machine
        self._connection: Any | None = None
        self._session_id: str | None = None
        # Fired synchronously after the complete session/prompt frame has been
        # written to the transport and drained (the SDK sender resolves each
        # send only after write+drain) — the prompt-accepted boundary.
        self._prompt_frame_hooks: list[Callable[[], None]] = []

    def add_prompt_frame_hook(self, hook: Callable[[], None]) -> None:
        self._prompt_frame_hooks.append(hook)

    def _observe_stream(self, event: Any) -> None:
        direction = getattr(event, "direction", None)
        if getattr(direction, "name", "") != "OUTGOING":
            return
        message = getattr(event, "message", None) or {}
        if message.get("method") == "session/prompt":
            for hook in list(self._prompt_frame_hooks):
                hook()

    # -- lifecycle ---------------------------------------------------------

    async def open(self, proc: ManagedProcess) -> None:
        """Bind the SDK connection to the supervised process's live wire."""
        require_sdk()
        connection_module = importlib.import_module("acp.client.connection")
        self._connection = connection_module.ClientSideConnection(
            self._client,
            proc.stdin,
            proc.stdout,
            observers=[self._observe_stream],
        )

    async def close(self) -> None:
        """Shut down the SDK connection tasks (never the process)."""
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        inner = getattr(connection, "_conn", None)
        close = getattr(inner, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:
                pass

    def _require_connection(self) -> Any:
        if self._connection is None:
            raise NativeDriverError("driver is not open")
        return self._connection

    def _require_session(self) -> str:
        if self._session_id is None:
            raise NativeDriverError("no active external session")
        return self._session_id

    def _check_identity(self) -> None:
        violation = self._client.identity_violation
        if violation is not None:
            raise NativeDriverError(violation)

    async def _call(self, action: str, awaitable: Any) -> Any:
        try:
            return await awaitable
        except (asyncio.CancelledError, ConfigFidelityError, NativeDriverError):
            raise
        except Exception as exc:
            raise NativeDriverError(f"{action} failed: {exc}") from exc

    # -- ACP sequence ------------------------------------------------------

    async def initialize(
        self, *, client_capabilities: Mapping[str, Any] | None = None
    ) -> InitializeSummary:
        connection = self._require_connection()
        sdk = require_sdk()
        protocol_version = getattr(
            importlib.import_module("acp.meta"), "PROTOCOL_VERSION", 1
        )
        kwargs: dict[str, Any] = {}
        if client_capabilities is not None:
            kwargs["client_capabilities"] = sdk.schema.ClientCapabilities.model_validate(
                dict(client_capabilities)
            )
        response = await self._call(
            "initialize",
            connection.initialize(protocol_version=protocol_version, **kwargs),
        )
        capabilities = _dump(response.agent_capabilities)
        load_session = bool(
            getattr(response.agent_capabilities, "load_session", False)
        )
        return InitializeSummary(
            protocol_version=response.protocol_version,
            agent_info=_dump(response.agent_info),
            load_session_advertised=load_session,
            capabilities=capabilities,
        )

    async def new_session(self, *, cwd: str) -> str:
        connection = self._require_connection()
        response = await self._call(
            "session/new", connection.new_session(cwd=cwd)
        )
        self._machine.record_initial_options(_dump_options(response.config_options))
        self._session_id = response.session_id
        self._client.expected_session_id = response.session_id
        return response.session_id

    async def load_session(self, *, agent_session_id: str, cwd: str) -> None:
        connection = self._require_connection()
        self._client.expected_session_id = agent_session_id
        response = await self._call(
            "session/load",
            connection.load_session(cwd=cwd, session_id=agent_session_id),
        )
        self._machine.record_initial_options(_dump_options(response.config_options))
        self._session_id = agent_session_id
        self._check_identity()

    async def set_config_exact(
        self, machine: ConfigFidelityMachine | None = None
    ) -> tuple[str, str]:
        """Run set model → consume full set → rediscover effort → set effort
        → consume full set → exact readback. Zero prompt on any failure.

        ``machine`` overrides the Run's machine for the rollback sequence;
        the prompt gate always stays on the Run's own machine.
        """
        active = machine or self._machine
        connection = self._require_connection()
        session_id = self._require_session()
        model_selector = active.model_plan()
        model_response = await self._call(
            "session/set_config_option(model)",
            connection.set_config_option(
                config_id=model_selector,
                session_id=session_id,
                value=active.requested_model,
            ),
        )
        active.record_post_model_options(
            _dump_options(model_response.config_options)
        )
        effort_selector = active.effort_plan()
        effort_response = await self._call(
            "session/set_config_option(effort)",
            connection.set_config_option(
                config_id=effort_selector,
                session_id=session_id,
                value=active.requested_effort,
            ),
        )
        active.record_post_effort_options(
            _dump_options(effort_response.config_options)
        )
        self._check_identity()
        return active.require_ready()

    async def prompt_once(self, text: str) -> PromptOutcome:
        connection = self._require_connection()
        session_id = self._require_session()
        self._machine.require_ready()
        self._check_identity()
        schema = require_sdk().schema
        block = schema.TextContentBlock(type="text", text=text)
        response = await self._call(
            "session/prompt",
            connection.prompt(session_id=session_id, prompt=[block]),
        )
        usage = _dump(response.usage) if response.usage is not None else None
        self._check_identity()
        return PromptOutcome(stop_reason=response.stop_reason, usage=usage)

    async def cancel(self) -> None:
        connection = self._require_connection()
        session_id = self._require_session()
        await self._call("session/cancel", connection.cancel(session_id=session_id))

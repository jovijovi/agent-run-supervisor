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
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agent_run_supervisor.managed_process import ManagedProcess

from . import require_sdk
from .client import NativeAcpClient, UpdateCallbackError
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


class _PromptBoundaryWriter:
    """Synchronous pre-write tap around the SDK sender's StreamWriter.

    ``before_write`` runs in the sender-loop task immediately before the
    frame's bytes are handed to the real ``StreamWriter.write()``, with no
    await between the two calls — nothing else (in particular the receive
    loop) can run in that gap. Everything else delegates to the real writer.
    """

    def __init__(
        self, writer: Any, before_write: Callable[[bytes], None]
    ) -> None:
        self._writer = writer
        self._before_write = before_write

    def write(self, data: bytes) -> None:
        self._before_write(data)
        self._writer.write(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)


class NativeAcpDriver:
    def __init__(
        self,
        *,
        client: NativeAcpClient,
        machine: ConfigFidelityMachine,
        on_config_switch_started: Callable[[], None] | None = None,
    ) -> None:
        self._client = client
        self._machine = machine
        self._connection: Any | None = None
        # Supervisor for the one SDK sender-loop task this driver constructs;
        # see :meth:`_build_stdio_transport`.
        self._sender_tasks: Any | None = None
        self._session_id: str | None = None
        # Fired synchronously immediately before the FIRST
        # ``session/set_config_option`` frame of this driver — the boundary
        # after which the agent's configuration may have moved and ARS cannot
        # yet prove what it moved to. It fires before the write rather than
        # after, because a crash inside the RPC is precisely the case a
        # post-hoc record would miss.
        self._on_config_switch_started = on_config_switch_started
        self._config_switch_announced = False
        # Fired synchronously after the complete session/prompt frame has been
        # written to the transport and drained (the SDK sender resolves each
        # send only after write+drain) — the prompt-accepted boundary.
        self._prompt_frame_hooks: list[Callable[[], None]] = []
        # Wire-order count of incoming session/update frames that the locked
        # SDK will actually dispatch to ``Client.session_update`` — the same
        # ordinal domain as the client's delivery/completion counters. The
        # SDK receive loop notifies observers synchronously before creating the
        # frame's handler task and before resolving any response future, so
        # every deliverable update frame that precedes a response is counted
        # here before that response resolves. Frames the SDK silently drops
        # (schema-invalid notifications, request-shaped session/update) must
        # never be counted: a counted-but-undeliverable frame is a phantom
        # ordinal that shifts the frozen boundary past genuine current-turn
        # chunks and corrupts the pre-response delivery barrier target.
        self._updates_observed = 0
        # Locked-SDK SessionNotification model, bound at open(): the observer
        # mirrors the router's validation so "observed" means "will dispatch".
        self._session_notification: Any | None = None
        # Causal Turn boundary: the value of ``_updates_observed`` snapshotted
        # by the sender-side writer tap immediately *before* the
        # session/prompt bytes reach the real StreamWriter.write(), with no
        # await in between. Updates counted at or below this ordinal were
        # received before the prompt could have reached the agent
        # (session/load history replay, pre-prompt chatter) and cannot belong
        # to the current Turn; every current-Turn update is necessarily read
        # after the write and lands above the boundary. The SDK's outgoing
        # observer runs only after send/write/drain and must not be the
        # snapshot point: a fast agent's current-turn update can be received
        # in that window. ``None`` until the prompt frame is written.
        self._prompt_wire_boundary: int | None = None
        # One-prompt-per-driver/connection pin: the boundary is write-once,
        # so a second prompt on the same connection would inherit the first
        # boundary. Fail closed instead of silently pretending reuse works.
        self._prompt_dispatched = False

    @property
    def prompt_wire_boundary(self) -> int | None:
        return self._prompt_wire_boundary

    def add_prompt_frame_hook(self, hook: Callable[[], None]) -> None:
        self._prompt_frame_hooks.append(hook)

    def _observe_stream(self, event: Any) -> None:
        direction = getattr(getattr(event, "direction", None), "name", "")
        message = getattr(event, "message", None) or {}
        if direction == "INCOMING":
            if message.get("method") == "session/update" and (
                self._update_frame_will_dispatch(message)
            ):
                self._updates_observed += 1
            return
        if direction != "OUTGOING":
            return
        if message.get("method") == "session/prompt":
            # Prompt-accepted hooks only: the causal boundary is snapshotted
            # by the pre-write tap, never by this post-send observer.
            for hook in list(self._prompt_frame_hooks):
                hook()

    def _update_frame_will_dispatch(self, message: Mapping[str, Any]) -> bool:
        """Locked-SDK (0.12.1) dispatch predicate for one update callback.

        ``Client.session_update`` runs for an incoming session/update frame
        iff it is notification-shaped (no ``id`` — a request-shaped frame
        hits the request table and fails method-not-found) and its params
        validate as ``schema.SessionNotification``: the router validates
        inside the notification handler and ``_run_notification`` swallows the
        ValidationError, dropping the frame before any callback runs.

        Schema v1.19 widened *what validates*, not how it is decided: lenient
        deserialization salvages a malformed non-critical field or skips an
        invalid array item, so a frame 0.11.1 rejected wholesale can now
        dispatch. Mirroring the bound model is exactly what absorbs that —
        the predicate follows the SDK's own validation rather than a frozen
        idea of which frames are well-formed. Counting must mirror it
        exactly: assuming every raw frame becomes one callback lets a dropped
        pre-prompt frame become a phantom ordinal inside the frozen boundary,
        and assuming the old strict reading leaves a salvaged frame delivered
        but uncounted, which drops the boundary one ordinal low.
        """
        if "id" in message:
            return False
        model = self._session_notification
        if model is None:
            # Observers run only on a connection bound by open(); an unbound
            # model means nothing dispatches either — never count blindly.
            return False
        try:
            model.model_validate(message.get("params"))
        except Exception:
            return False
        return True

    def _snapshot_prompt_wire_boundary(self, data: bytes) -> None:
        """Pre-write tap: pin the boundary before the prompt bytes go out.

        Runs synchronously in the sender loop with no await before the real
        ``write()``; frames after the first prompt are skipped by the
        write-once guard, so post-prompt traffic costs one comparison.
        """
        if self._prompt_wire_boundary is not None:
            return
        try:
            message = json.loads(data)
        except Exception:
            return
        if isinstance(message, dict) and message.get("method") == "session/prompt":
            self._prompt_wire_boundary = self._updates_observed

    def _build_stdio_transport(self, proc: ManagedProcess) -> Any:
        """Assemble the locked SDK's own stdio transport around the tap.

        The locked SDK (0.12.1) constructs the stdio ``MessageSender``
        internally and offers no injection seam for it — ``sender_factory``,
        with the rest of the connection's pluggable queue/state/dispatcher
        machinery, was removed in the 0.12.1 connection refactor. Its remaining
        seam is one step further out: ``ClientSideConnection`` accepts a
        **message-level** ``Transport`` in place of the (writer, reader) pair,
        and then uses it verbatim.

        So ARS builds that transport out of the SDK's own parts —
        :class:`acp.task.MessageSender` and :class:`acp._transport.NdjsonTransport`
        — and threads the pre-write tap through the one place the SDK still
        leaves open, the sender's writer. No framing, buffer-limit-overrun
        handling, or receive-timeout behavior is reimplemented here, and the
        boundary is still snapshotted in the sender loop immediately before the
        prompt's bytes reach the real ``StreamWriter.write()``. A tap on the
        transport's message hand-off instead would fire one queue hop earlier
        and pull pre-prompt frames into the current Turn.

        The sender loop needs a supervisor; the connection's own is not
        reachable before it exists, so the driver owns one and shuts it down in
        :meth:`close`.
        """
        task_module = importlib.import_module("acp.task")
        transport_module = importlib.import_module("acp._transport")
        self._sender_tasks = task_module.TaskSupervisor(
            source="agent_run_supervisor.native_acp.driver"
        )
        sender = task_module.MessageSender(
            _PromptBoundaryWriter(proc.stdin, self._snapshot_prompt_wire_boundary),
            self._sender_tasks,
        )
        # No receive timeout, exactly as the SDK's own stdio path constructs it:
        # turn-level time bounds stay with the caller (RunTask).
        return transport_module.NdjsonTransport(proc.stdout, sender)

    # -- lifecycle ---------------------------------------------------------

    async def open(self, proc: ManagedProcess) -> None:
        """Bind the SDK connection to the supervised process's live wire."""
        sdk = require_sdk()
        # Bound before the connection exists so the very first observed frame
        # is already gated by the locked SDK's own validation model.
        self._session_notification = sdk.schema.SessionNotification
        connection_module = importlib.import_module("acp.client.connection")
        self._connection = connection_module.ClientSideConnection(
            self._client,
            self._build_stdio_transport(proc),
            observers=[self._observe_stream],
        )

    async def close(self) -> None:
        """Shut down the SDK connection tasks (never the process)."""
        connection = self._connection
        sender_tasks = self._sender_tasks
        self._connection = None
        self._sender_tasks = None
        try:
            if connection is None:
                return
            inner = getattr(connection, "_conn", None)
            close = getattr(inner, "close", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass
        finally:
            # The connection closes the transport, which closes the sender and
            # awaits its loop; this only guarantees the driver-owned supervisor
            # is drained even when that path did not run to completion.
            if sender_tasks is not None:
                try:
                    await sender_tasks.shutdown()
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
            # The action name is source-owned; the SDK's exception text is not.
            # It stays bounded by the caller's categorical failure handling —
            # a driver error becomes a stable detail code, never raw text in a
            # terminal.
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

    @staticmethod
    def _meta_kwargs(meta: Mapping[str, Any] | None) -> dict[str, Any]:
        """The frozen session metadata as SDK ``_meta`` keyword arguments.

        Empty/``None`` means the argument is omitted entirely, so profiles
        without metadata keep byte-identical wire frames (no ``_meta`` key at
        all, not a null one). The only source is the resolved profile: no
        caller metadata passthrough surface exists.
        """
        return dict(meta) if meta else {}

    async def new_session(
        self, *, cwd: str, meta: Mapping[str, Any] | None = None
    ) -> str:
        connection = self._require_connection()
        response = await self._call(
            "session/new", connection.new_session(cwd=cwd, **self._meta_kwargs(meta))
        )
        self._machine.record_initial_options(_dump_options(response.config_options))
        self._session_id = response.session_id
        self._client.expected_session_id = response.session_id
        return response.session_id

    async def load_session(
        self,
        *,
        agent_session_id: str,
        cwd: str,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        connection = self._require_connection()
        self._client.expected_session_id = agent_session_id
        response = await self._call(
            "session/load",
            connection.load_session(
                cwd=cwd,
                session_id=agent_session_id,
                **self._meta_kwargs(meta),
            ),
        )
        self._machine.record_initial_options(_dump_options(response.config_options))
        self._session_id = agent_session_id
        self._check_identity()

    async def set_config_exact(
        self, machine: ConfigFidelityMachine | None = None
    ) -> tuple[str, str]:
        """Run [set permission mode → consume full set → exact readback →]
        set model → consume full set → rediscover effort → set effort →
        consume full set → exact readback. Zero prompt on any failure.

        Under **model-only** fidelity the sequence ends at the exact model
        readback: no effort option is discovered and no effort
        ``set_config_option`` frame is ever written. That is a structural
        property of this method, not a value the machine happens to accept.

        ``machine`` overrides the Run's machine for the rollback sequence;
        the prompt gate always stays on the Run's own machine.
        """
        active = machine or self._machine
        connection = self._require_connection()
        session_id = self._require_session()
        self._announce_config_switch()
        if active.has_permission_mode:
            # First leg: a session whose ambient initial mode auto-allows tool
            # calls must be switched to the frozen mode, with readback proof,
            # before any model/effort work and long before any prompt.
            mode_selector = active.permission_mode_plan()
            mode_response = await self._call(
                "session/set_config_option(permission_mode)",
                connection.set_config_option(
                    config_id=mode_selector,
                    session_id=session_id,
                    value=active.required_permission_mode,
                ),
            )
            active.record_post_mode_options(
                _dump_options(mode_response.config_options)
            )
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
        if active.is_model_only:
            self._check_identity()
            return active.require_ready()
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

    def _announce_config_switch(self) -> None:
        """Record the switch boundary once, before the first set is written."""
        if self._config_switch_announced:
            return
        self._config_switch_announced = True
        if self._on_config_switch_started is not None:
            self._on_config_switch_started()

    async def prompt_once(self, text: str) -> PromptOutcome:
        connection = self._require_connection()
        session_id = self._require_session()
        if self._prompt_dispatched:
            raise NativeDriverError(
                "driver already dispatched a prompt: the prompt wire boundary "
                "is write-once, one prompt per driver/connection"
            )
        self._prompt_dispatched = True
        self._machine.require_ready()
        self._check_identity()
        schema = require_sdk().schema
        block = schema.TextContentBlock(type="text", text=text)
        response = await self._call(
            "session/prompt",
            connection.prompt(session_id=session_id, prompt=[block]),
        )
        if self._prompt_wire_boundary is None:
            # A resolved prompt response proves the frame was written, so the
            # pre-write tap must have pinned the boundary; without it the
            # current-turn gate would silently discard every chunk.
            raise NativeDriverError(
                "prompt response arrived but the prompt wire boundary was "
                "never snapshotted"
            )
        # Pre-response delivery barrier: every deliverable update frame
        # observed on the wire before this response must complete its client
        # callback before the turn returns — otherwise finalization could
        # cancel those handlers and silently lose final-message/event
        # evidence. The target and the completion counter share one ordinal
        # domain (SDK-dispatched frames only). The caller's turn timeout is the
        # only time bound.
        #
        # 0.12.1's own ``_SessionUpdateTracker`` now waits inside
        # ``prompt()`` too, but only for handlers that already *started*: it
        # registers a frame when ``session_update`` runs its first step, and
        # the receive loop resolves the response future without stepping the
        # notification tasks it created just before. A frame observed on the
        # wire whose task has not run yet is invisible to that wait and visible
        # to this one, so this barrier is the strict superset and stays.
        try:
            await self._client.wait_for_updates_completed(self._updates_observed)
        except UpdateCallbackError as exc:
            raise NativeDriverError(str(exc)) from exc
        usage = _dump(response.usage) if response.usage is not None else None
        self._check_identity()
        return PromptOutcome(stop_reason=response.stop_reason, usage=usage)

    async def cancel(self) -> None:
        connection = self._require_connection()
        session_id = self._require_session()
        await self._call("session/cancel", connection.cancel(session_id=session_id))

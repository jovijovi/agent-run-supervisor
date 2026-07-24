"""B1 delivery-ordinal contract pins (2026-07-24 focused-review WATCH).

``RunTask._update_sink`` assigns each session/update its delivery ordinal by
invocation count and compares it against the driver's prompt wire boundary.
That is sound only under two exact properties of the locked stack
(agent-client-protocol 0.11.0 on CPython asyncio):

1. ``NativeAcpClient.session_update`` runs the synchronous sink before its
   first await, so the ordinal is assigned in the notification task's first
   step.
2. The SDK dispatcher creates one notification task per frame in receive
   order, and asyncio starts task first-steps in creation order (documented
   FIFO ``call_soon`` scheduling) — even when an earlier runner is slow.
3. The driver's observed-update count (prompt wire boundary + pre-response
   delivery barrier) and the sink's delivery ordinals live in one causal
   domain: only session/update frames the SDK will actually dispatch to
   ``Client.session_update`` may be counted. SDK 0.11.0 silently drops a
   notification whose params fail ``SessionNotification`` validation
   (``_run_notification`` suppresses handler exceptions) and routes a frame
   carrying an ``id`` to the request table (method-not-found, no callback) —
   counting such a frame creates a phantom ordinal that shifts the frozen
   boundary past the first genuine current-turn chunk.

These tests pin exactly those properties — no broader protocol ordering
guarantee is claimed. An SDK/runtime upgrade that breaks any of them fails
here loudly instead of silently misnumbering ordinals.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.native_acp.client import NativeAcpClient
from agent_run_supervisor.native_acp.config_fidelity import ConfigFidelityMachine
from agent_run_supervisor.native_acp.driver import NativeAcpDriver
from agent_run_supervisor.native_acp.run_task import RunTask


def test_session_update_sink_completes_before_first_await() -> None:
    # Stepping the coroutine once must run it to completion (StopIteration)
    # with the sink already recorded: proof there is no await ahead of the
    # synchronous sink. An await inserted before the sink suspends the first
    # step instead, and this pin fails.
    recorded: list[tuple[str, dict[str, Any]]] = []
    client = NativeAcpClient(
        on_update=lambda session_id, update: recorded.append((session_id, update))
    )
    update = {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "CURRENT_TURN"},
    }
    coro = client.session_update("sess-contract-1", update)
    try:
        with pytest.raises(StopIteration):
            coro.send(None)
    finally:
        coro.close()
    assert recorded == [("sess-contract-1", update)]
    assert client.updates_completed == 1


def test_dispatcher_invokes_notification_runners_in_receive_order() -> None:
    from acp.task import (
        DefaultMessageDispatcher,
        InMemoryMessageQueue,
        InMemoryMessageStateStore,
        RpcTask,
        RpcTaskKind,
        TaskSupervisor,
    )

    async def case() -> tuple[list[int], list[int]]:
        queue = InMemoryMessageQueue()
        supervisor = TaskSupervisor(source="test.update-delivery-contract")
        started: list[int] = []
        finished: list[int] = []
        done = asyncio.Event()

        async def request_runner(message: dict[str, Any]) -> Any:
            raise AssertionError("no request frames in this contract pin")

        async def notification_runner(message: dict[str, Any]) -> None:
            ordinal = message["params"]["ordinal"]
            started.append(ordinal)
            if ordinal == 1:
                # Deliberately delayed first runner: later runners would
                # overtake it if invocation order were completion-driven.
                await asyncio.sleep(0.05)
            finished.append(ordinal)
            if len(finished) == 3:
                done.set()

        dispatcher = DefaultMessageDispatcher(
            queue=queue,
            supervisor=supervisor,
            store=InMemoryMessageStateStore(),
            request_runner=request_runner,
            notification_runner=notification_runner,
        )
        dispatcher.start()
        try:
            for ordinal in (1, 2, 3):
                await queue.publish(
                    RpcTask(
                        RpcTaskKind.NOTIFICATION,
                        {
                            "method": "session/update",
                            "params": {"ordinal": ordinal},
                        },
                    )
                )
            await asyncio.wait_for(done.wait(), 10)
        finally:
            await dispatcher.stop()
            await supervisor.shutdown()
        return started, finished

    started, finished = asyncio.run(case())
    # Invocation (first-step) order matches receive order — this is what
    # keeps invocation-count ordinals aligned with the wire-order count.
    assert started == [1, 2, 3]
    # Completion order deliberately differs, proving the pin is not
    # accidentally satisfied by completion-driven sequencing.
    assert finished == [2, 3, 1]


# -- ordinal-gap coherence regression (B1, 2026-07-24 R3) --------------------
#
# Full driver + client + real SDK connection/router/validation over a real
# in-process wire (socketpair-backed asyncio streams). The scripted peer here
# replaces only the agent *process* so the test can inject frames the L2
# fake agent must never emit; every supervised-side component stays real.

_SESSION_ID = "sess-ordinal-gap-1"

_INITIAL_OPTIONS = [
    {
        "id": "model",
        "name": "Model",
        "type": "select",
        "currentValue": "provider/base",
        "options": [
            {"value": "provider/base", "name": "Base"},
            {"value": "kimi-for-coding/k3", "name": "K3"},
        ],
    },
    {
        "id": "effort",
        "name": "Effort",
        "type": "select",
        "currentValue": "high",
        "options": [{"value": "high", "name": "High"}],
    },
]

_POST_MODEL_OPTIONS = [
    _INITIAL_OPTIONS[0],
    {
        "id": "effort",
        "name": "Effort",
        "type": "select",
        "currentValue": "high",
        "options": [
            {"value": "high", "name": "High"},
            {"value": "max", "name": "Max"},
        ],
    },
]

# Observed on the wire, then dropped by SDK 0.11.0: SessionNotification
# validation rejects the unsupported update kind and _run_notification
# suppresses the ValidationError — no session_update callback ever runs.
_PHANTOM_SDK_SUPPRESSED = {
    "jsonrpc": "2.0",
    "method": "session/update",
    "params": {
        "sessionId": _SESSION_ID,
        "update": {
            "sessionUpdate": "history_replay_v2",
            "content": {"type": "text", "text": "PHANTOM"},
        },
    },
}

# Schema-valid params, but the "id" makes it a *request*: SDK 0.11.0 routes
# it to the request table, which has no session/update entry — the frame
# earns a method-not-found error response and never a callback.
_PHANTOM_REQUEST_SHAPED = {
    "jsonrpc": "2.0",
    "id": 9001,
    "method": "session/update",
    "params": {
        "sessionId": _SESSION_ID,
        "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "PHANTOM"},
        },
    },
}

# Valid, deliverable, and deliberately not an agent_message_chunk: under the
# phantom defect this frame is what satisfies the pre-response delivery
# barrier so the Turn "completes" with the genuine chunk already lost.
_LATE_VALID_NOTIFICATION = {
    "jsonrpc": "2.0",
    "method": "session/update",
    "params": {
        "sessionId": _SESSION_ID,
        "update": {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": "LATE_VALID"},
        },
    },
}


def _frame(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


async def _wire_agent(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    phantom_frame: dict[str, Any],
    prompt_responded: asyncio.Event,
) -> None:
    """Scripted agent peer speaking real newline-delimited JSON-RPC."""
    options = {option["id"]: dict(option) for option in _INITIAL_OPTIONS}

    def respond(request_id: Any, result: dict[str, Any]) -> None:
        writer.write(_frame({"jsonrpc": "2.0", "id": request_id, "result": result}))

    while True:
        line = await reader.readline()
        if not line:
            return
        message = json.loads(line)
        method = message.get("method")
        if method is None:
            # Responses to agent-emitted frames (e.g. the SDK's
            # method-not-found error for the request-shaped phantom).
            continue
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            respond(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion", 1),
                    "agentCapabilities": {"loadSession": True},
                    "agentInfo": {"name": "wire-agent", "version": "0.0.0"},
                },
            )
        elif method == "session/new":
            # Wire order pins causality: the phantom frame precedes the
            # session/new response, so the receive loop has observed it long
            # before the prompt bytes can be written.
            writer.write(_frame(phantom_frame))
            respond(
                request_id,
                {
                    "sessionId": _SESSION_ID,
                    "configOptions": [dict(option) for option in options.values()],
                },
            )
        elif method == "session/set_config_option":
            config_id = params.get("configId")
            if config_id == "model":
                options = {
                    option["id"]: dict(option) for option in _POST_MODEL_OPTIONS
                }
            if config_id in options:
                options[config_id]["currentValue"] = params.get("value")
            respond(
                request_id,
                {"configOptions": [dict(option) for option in options.values()]},
            )
        elif method == "session/prompt":
            # One genuine current-turn chunk, then the prompt response; the
            # late valid notification is written by the test body only after
            # the driver is provably past the prompt response.
            writer.write(
                _frame(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": _SESSION_ID,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "REAL_CHUNK"},
                            },
                        },
                    }
                )
            )
            respond(
                request_id,
                {
                    "stopReason": "end_turn",
                    "usage": {"totalTokens": 3, "inputTokens": 2, "outputTokens": 1},
                },
            )
            prompt_responded.set()
        await writer.drain()


async def _ordinal_gap_case(
    phantom_frame: dict[str, Any],
) -> tuple[str, int | None, str, int]:
    driver_sock, agent_sock = socket.socketpair()
    driver_reader, driver_writer = await asyncio.open_connection(sock=driver_sock)
    agent_reader, agent_writer = await asyncio.open_connection(sock=agent_sock)

    # Production ordinal accounting: the sink assigns delivery ordinals by
    # invocation count and gates final-message ingestion through the real
    # RunTask current-turn predicate against the driver's frozen boundary.
    ctx = SimpleNamespace(driver=None, updates_delivered=0)
    final_parts: list[str] = []
    chunk_delivered = asyncio.Event()

    def sink(session_id: str, update: dict[str, Any]) -> None:
        ctx.updates_delivered += 1
        if update.get("sessionUpdate") == "agent_message_chunk":
            text = (update.get("content") or {}).get("text")
            if isinstance(text, str) and RunTask._current_turn_chunk(ctx):
                final_parts.append(text)
            if text == "REAL_CHUNK":
                chunk_delivered.set()

    client = NativeAcpClient(on_update=sink)
    machine = ConfigFidelityMachine(
        model_selector_id="model",
        effort_selector_id="effort",
        requested_model="kimi-for-coding/k3",
        requested_effort="max",
    )
    driver = NativeAcpDriver(client=client, machine=machine)
    ctx.driver = driver

    prompt_responded = asyncio.Event()
    agent_task = asyncio.ensure_future(
        _wire_agent(agent_reader, agent_writer, phantom_frame, prompt_responded)
    )
    prompt_task: asyncio.Task[Any] | None = None
    try:
        await driver.open(SimpleNamespace(stdin=driver_writer, stdout=driver_reader))
        await asyncio.wait_for(driver.initialize(), 10)
        await asyncio.wait_for(driver.new_session(cwd="/"), 10)
        await asyncio.wait_for(driver.set_config_exact(), 10)

        prompt_task = asyncio.ensure_future(driver.prompt_once("hello agent"))
        await asyncio.wait_for(prompt_responded.wait(), 10)
        await asyncio.wait_for(chunk_delivered.wait(), 10)
        # Deterministic scheduling ticks (no wall-clock wait): let the prompt
        # coroutine resume from the resolved response and reach its
        # pre-response delivery barrier before the late frame exists.
        for _ in range(25):
            await asyncio.sleep(0)
        agent_writer.write(_frame(_LATE_VALID_NOTIFICATION))
        await agent_writer.drain()

        outcome = await asyncio.wait_for(prompt_task, 10)
        # Both deliverable updates (chunk + late notification) complete their
        # callbacks before teardown, so close never cancels a live runner.
        await asyncio.wait_for(client.wait_for_updates_completed(2), 10)
        return (
            outcome.stop_reason,
            driver.prompt_wire_boundary,
            "".join(final_parts),
            ctx.updates_delivered,
        )
    finally:
        if prompt_task is not None and not prompt_task.done():
            prompt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await prompt_task
        await driver.close()
        agent_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await agent_task
        for writer in (agent_writer, driver_writer):
            with contextlib.suppress(Exception):
                writer.close()


@pytest.mark.parametrize(
    "phantom_frame",
    [_PHANTOM_SDK_SUPPRESSED, _PHANTOM_REQUEST_SHAPED],
    ids=["sdk-suppressed-malformed-update", "request-shaped-update"],
)
def test_pre_prompt_phantom_update_must_not_shift_boundary_or_lose_chunk(
    phantom_frame: dict[str, Any],
) -> None:
    # B1 ordinal gap: a pre-prompt session/update frame that the SDK observes
    # but never dispatches (schema-invalid, or request-shaped) must not be
    # counted into the frozen prompt wire boundary or the delivery-barrier
    # target. Counting it gives the first genuine current-turn chunk delivery
    # ordinal == boundary (excluded from final_message) while the later valid
    # notification satisfies the barrier — the Turn completes with the chunk
    # silently lost.
    stop_reason, boundary, final_text, delivered = asyncio.run(
        _ordinal_gap_case(phantom_frame)
    )
    assert stop_reason == "end_turn"
    assert final_text == "REAL_CHUNK", (
        f"current-turn chunk lost from final message (got {final_text!r}); "
        f"boundary={boundary} delivered={delivered}"
    )
    assert boundary == 0, (
        f"prompt wire boundary carries a phantom ordinal: {boundary} "
        "(a pre-prompt frame the SDK never delivers was counted)"
    )
    # Exactly the two deliverable updates produced callbacks.
    assert delivered == 2

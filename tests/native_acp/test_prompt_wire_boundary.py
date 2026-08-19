"""Causal pins for the prompt wire boundary against the locked ACP SDK.

``RunTask._current_turn_chunk`` classifies every delivered ``session/update``
by comparing its delivery ordinal against ``NativeAcpDriver.prompt_wire_boundary``.
That comparison *is* the replay/current-turn separation, so the instant at which
the boundary is snapshotted is a product property, not an implementation
detail:

* snapshot **too late** — after the frame is written and drained, e.g. from the
  SDK's outgoing stream observer — and a fast agent's genuine current-turn chunk
  is counted as pre-prompt and silently dropped from ``final_message``
  (pinned from the other side by
  ``test_session_switching.test_current_turn_chunk_survives_fast_agent_race_with_post_send_observer``);
* snapshot **too early** — before the frame reaches the transport at all — and a
  frame the AGENT emitted while the prompt was still queued is promoted into
  this Run's Turn.

The only instant that is neither is *inside the sender loop, immediately before
the real* ``StreamWriter.write()``, *with no await in between*: nothing else, in
particular the receive loop, can run in that gap.

SDK 0.12.1 removed the ``sender_factory`` constructor keyword the hook was
originally injected through, so these pins hold the *replacement* seam to the
same causal contract rather than to any particular injection mechanism. Both
cases drive a real ``ClientSideConnection`` — real router, real validation, real
NDJSON framing — over a socketpair wire; only the AGENT process is scripted.

The recorder is installed by wrapping the writer ``acp.task.sender.MessageSender``
is constructed with, which is the last seam before the bytes reach the wire and
sits *outside* whatever pre-write hook the driver owns. It is therefore neutral
about how the driver injects that hook.
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
from agent_run_supervisor.native_acp.config_fidelity import (
    EFFORT_NOT_APPLICABLE,
    FIDELITY_MODEL_ONLY,
    ConfigFidelityMachine,
)
from agent_run_supervisor.native_acp.driver import NativeAcpDriver

_SESSION_ID = "sess-prompt-boundary-1"

_OPTIONS = [
    {
        "id": "model",
        "name": "Model",
        "type": "select",
        "currentValue": "provider/base",
        "options": [
            {"value": "provider/base", "name": "Base"},
            {"value": "provider/target", "name": "Target"},
        ],
    }
]


def _frame(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


def _update(text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": _SESSION_ID,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            },
        },
    }


class _PromptWriteRecorder:
    """Byte-level tap around the writer ``MessageSender`` was constructed with.

    It records **after** delegating, which is the same causal instant as "just
    before the bytes went out": ``write()`` down to the real
    ``StreamWriter.write()`` is a chain of synchronous calls with no await in
    it, so nothing — in particular neither the receive loop nor an SDK outgoing
    observer, which runs only after send/write/drain — can change the driver's
    state in between. Delegating first is what keeps the recorder outside the
    driver's own pre-write hook regardless of where that hook is injected.
    """

    def __init__(self, writer: Any, state: dict[str, Any]) -> None:
        self._writer = writer
        self._state = state

    def write(self, data: bytes) -> None:
        self._writer.write(data)
        try:
            message = json.loads(data)
        except Exception:  # pragma: no cover - SDK frames are always JSON
            return
        if (
            isinstance(message, dict)
            and message.get("method") == "session/prompt"
            and self._state.get("at_prompt_write") is None
        ):
            driver = self._state["driver"]
            self._state["at_prompt_write"] = {
                "boundary": driver.prompt_wire_boundary,
                "observed": driver._updates_observed,
            }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)


def _install_prompt_write_recorder(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]
) -> None:
    """Wrap the writer every ``MessageSender`` in this test is built with."""
    from acp.task import sender as sender_module

    original_init = sender_module.MessageSender.__init__

    def recording_init(self: Any, writer: Any, supervisor: Any) -> None:
        original_init(self, _PromptWriteRecorder(writer, state), supervisor)

    monkeypatch.setattr(sender_module.MessageSender, "__init__", recording_init)


async def _scripted_agent(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    pre_prompt_updates: int,
) -> None:
    """Minimal scripted AGENT peer speaking real newline-delimited JSON-RPC."""
    options = {option["id"]: dict(option) for option in _OPTIONS}

    def respond(request_id: Any, result: dict[str, Any]) -> None:
        writer.write(_frame({"jsonrpc": "2.0", "id": request_id, "result": result}))

    while True:
        line = await reader.readline()
        if not line:
            return
        message = json.loads(line)
        method = message.get("method")
        if method is None:
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
            respond(
                request_id,
                {
                    "sessionId": _SESSION_ID,
                    "configOptions": [dict(option) for option in options.values()],
                },
            )
            # Bootstrap/history replay: written strictly before the prompt can
            # exist, so every one of these belongs at or below the boundary.
            # It follows the response because the locked SDK creates a
            # notification's handler task straight from the receive loop: a
            # deliverable frame batched *ahead* of the response would reach
            # ``session_update`` before the response binds the expected
            # external Session id, which ARS refuses fail-closed.
            for index in range(pre_prompt_updates):
                writer.write(_frame(_update(f"REPLAY_{index}")))
        elif method == "session/set_config_option":
            config_id = params.get("configId")
            if config_id in options:
                options[config_id]["currentValue"] = params.get("value")
            respond(
                request_id,
                {"configOptions": [dict(option) for option in options.values()]},
            )
        elif method == "session/prompt":
            writer.write(_frame(_update("CURRENT_TURN")))
            respond(request_id, {"stopReason": "end_turn", "usage": None})
        await writer.drain()


def _build_driver(delivered: list[str]) -> tuple[NativeAcpDriver, NativeAcpClient]:
    def sink(session_id: str, update: dict[str, Any]) -> None:
        text = (update.get("content") or {}).get("text")
        if isinstance(text, str):
            delivered.append(text)

    client = NativeAcpClient(on_update=sink)
    machine = ConfigFidelityMachine(
        model_selector_id="model",
        effort_selector_id=None,
        requested_model="provider/target",
        requested_effort=EFFORT_NOT_APPLICABLE,
        fidelity_mode=FIDELITY_MODEL_ONLY,
    )
    return NativeAcpDriver(client=client, machine=machine), client


async def _turn(
    state: dict[str, Any],
    *,
    pre_prompt_updates: int = 0,
    arm_before_open: Any = None,
) -> dict[str, Any]:
    """Drive one full ACP turn and report what the driver looked like at write."""
    driver_sock, agent_sock = socket.socketpair()
    driver_reader, driver_writer = await asyncio.open_connection(sock=driver_sock)
    agent_reader, agent_writer = await asyncio.open_connection(sock=agent_sock)

    delivered: list[str] = []
    driver, client = _build_driver(delivered)
    state["driver"] = driver

    agent_task = asyncio.ensure_future(
        _scripted_agent(
            agent_reader, agent_writer, pre_prompt_updates=pre_prompt_updates
        )
    )
    try:
        if arm_before_open is not None:
            arm_before_open(driver, agent_writer)
        await driver.open(SimpleNamespace(stdin=driver_writer, stdout=driver_reader))
        await asyncio.wait_for(driver.initialize(), 10)
        await asyncio.wait_for(driver.new_session(cwd="/"), 10)
        await asyncio.wait_for(driver.set_config_exact(), 10)
        outcome = await asyncio.wait_for(driver.prompt_once("hello agent"), 20)
        return {
            "stop_reason": outcome.stop_reason,
            "at_prompt_write": state.get("at_prompt_write"),
            "boundary": driver.prompt_wire_boundary,
            "delivered": list(delivered),
            "completed": client.updates_completed,
        }
    finally:
        await driver.close()
        agent_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await agent_task
        for writer in (agent_writer, driver_writer):
            with contextlib.suppress(Exception):
                writer.close()


@pytest.mark.parametrize("pre_prompt_updates", [0, 3])
def test_boundary_is_pinned_at_the_instant_the_prompt_bytes_reach_the_wire(
    monkeypatch: pytest.MonkeyPatch, pre_prompt_updates: int
) -> None:
    # The boundary must already be snapshotted when the prompt frame's bytes
    # are handed to the real writer, and it must equal the wire-order count of
    # deliverable updates observed up to that instant. A boundary still ``None``
    # here was taken post-write, or never taken at all; one that disagrees with
    # the observed count was taken in a different causal domain.
    state: dict[str, Any] = {}
    _install_prompt_write_recorder(monkeypatch, state)
    result = asyncio.run(_turn(state, pre_prompt_updates=pre_prompt_updates))

    at_write = result["at_prompt_write"]
    assert at_write is not None, "the prompt frame never reached the wire"
    assert at_write["boundary"] is not None, (
        "the prompt wire boundary was not snapshotted before the prompt bytes "
        "were written: the pre-send hook is missing"
    )
    assert at_write["boundary"] == at_write["observed"] == pre_prompt_updates
    # Write-once: the rest of the turn leaves it exactly where it was pinned.
    assert result["boundary"] == pre_prompt_updates
    assert result["stop_reason"] == "end_turn"
    # Replay stayed at or below the boundary; the current-turn chunk is above it.
    assert result["delivered"] == [
        f"REPLAY_{index}" for index in range(pre_prompt_updates)
    ] + ["CURRENT_TURN"]
    assert result["completed"] == pre_prompt_updates + 1


def test_update_observed_while_the_prompt_is_still_queued_stays_pre_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The AGENT emitted this frame before the prompt bytes left ARS, so it
    # cannot be an answer to the prompt. ``MessageSender.send`` is the last SDK
    # step before the frame is queued for the sender loop, so holding the prompt
    # there until the receive loop has observed one more update reproduces
    # exactly the interleaving a hook taken *before* the transport hand-off
    # would misclassify: it would freeze the boundary at 0 and promote this
    # replay frame into the Turn.
    from acp.task import sender as sender_module

    state: dict[str, Any] = {}
    _install_prompt_write_recorder(monkeypatch, state)

    observed_racer = asyncio.Event()
    original_observe = NativeAcpDriver._observe_stream

    def observe_and_signal(self: NativeAcpDriver, event: Any) -> None:
        original_observe(self, event)
        message = getattr(event, "message", None) or {}
        content = ((message.get("params") or {}).get("update") or {}).get("content")
        if isinstance(content, dict) and content.get("text") == "RACING_PRE_PROMPT":
            observed_racer.set()

    monkeypatch.setattr(NativeAcpDriver, "_observe_stream", observe_and_signal)

    original_send = sender_module.MessageSender.send

    def arm(driver: NativeAcpDriver, agent_writer: asyncio.StreamWriter) -> None:
        injected = False

        async def racing_send(self: Any, payload: dict[str, Any]) -> None:
            nonlocal injected
            if payload.get("method") == "session/prompt" and not injected:
                injected = True
                agent_writer.write(_frame(_update("RACING_PRE_PROMPT")))
                await agent_writer.drain()
                await asyncio.wait_for(observed_racer.wait(), 10)
            await original_send(self, payload)

        monkeypatch.setattr(sender_module.MessageSender, "send", racing_send)

    result = asyncio.run(_turn(state, arm_before_open=arm))

    at_write = result["at_prompt_write"]
    assert at_write is not None, "the prompt frame never reached the wire"
    assert at_write["boundary"] == 1, (
        "an update observed while the prompt was still queued was not counted "
        f"into the boundary (got {at_write['boundary']}): the snapshot ran "
        "before the frame reached the transport"
    )
    assert result["stop_reason"] == "end_turn"
    assert result["delivered"] == ["RACING_PRE_PROMPT", "CURRENT_TURN"]
    assert result["completed"] == 2

"""C5: NativeAcpDriver + config-fidelity machine over the real-framing fake
agent (spawned through ManagedProcess — never a driver mock).

Every fake-agent case asserts the zero-prompt guarantee through the fake's
method trace: a fidelity failure means the fake observed no session/prompt
frame at all.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.managed_process import (
    ManagedProcess,
    ManagedProcessLimits,
    spawn_managed_process,
)
from agent_run_supervisor.native_acp.client import NativeAcpClient
from agent_run_supervisor.native_acp.config_fidelity import (
    ConfigFidelityError,
    ConfigFidelityMachine,
)
from agent_run_supervisor.native_acp.driver import NativeAcpDriver, NativeDriverError

FAKE_AGENT_PATH = Path(__file__).with_name("fake_agent.py")

HAPPY_SCRIPT = {
    # Requested effort "max" is deliberately absent from the initial effort
    # choices and appears only in the post-model set: a happy-path success
    # therefore proves the machine binds the post-set-model option set.
    "initial_options": [
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
    ],
    "post_model_options": [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": "kimi-for-coding/k3",
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
            "options": [
                {"value": "high", "name": "High"},
                {"value": "max", "name": "Max"},
            ],
        },
    ],
    "final_message": "FAKE_AGENT_OK",
}


def _machine() -> ConfigFidelityMachine:
    return ConfigFidelityMachine(
        model_selector_id="model",
        effort_selector_id="effort",
        requested_model="kimi-for-coding/k3",
        requested_effort="max",
    )


async def _deny(_request: dict) -> dict:
    return {"decision": "deny", "reason": "test default-deny"}


async def _spawn_fake(script: dict, trace: Path) -> ManagedProcess:
    env = dict(os.environ)
    env["FAKE_AGENT_SCRIPT"] = json.dumps(script)
    env["FAKE_AGENT_TRACE"] = str(trace)
    return await spawn_managed_process(
        argv=[sys.executable, "-u", str(FAKE_AGENT_PATH)],
        cwd=Path.cwd(),
        env=env,
        limits=ManagedProcessLimits(),
    )


def _trace_methods(trace: Path) -> list[str]:
    if not trace.exists():
        return []
    return [line for line in trace.read_text(encoding="utf-8").splitlines() if line]


class _Harness:
    def __init__(self, tmp_path: Path, script: dict) -> None:
        self.tmp_path = tmp_path
        self.script = script
        self.trace = tmp_path / "fake-agent-trace.log"
        self.updates: list[tuple[str, dict]] = []
        self.machine = _machine()
        self.proc: ManagedProcess | None = None
        self.driver: NativeAcpDriver | None = None

    async def __aenter__(self) -> "_Harness":
        self.proc = await _spawn_fake(self.script, self.trace)
        client = NativeAcpClient(
            on_update=lambda session_id, update: self.updates.append(
                (session_id, update)
            ),
            permission_handler=_deny,
        )
        self.driver = NativeAcpDriver(client=client, machine=self.machine)
        await self.driver.open(self.proc)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        assert self.driver is not None and self.proc is not None
        await self.driver.close()
        self.proc.kill_group()
        await self.proc.wait()

    def methods_seen(self) -> list[str]:
        return _trace_methods(self.trace)


def test_happy_exact_sequence_reaches_ready_and_prompts(tmp_path: Path) -> None:
    async def case() -> None:
        async with _Harness(tmp_path, HAPPY_SCRIPT) as harness:
            driver = harness.driver
            summary = await asyncio.wait_for(driver.initialize(), 10)
            assert summary.load_session_advertised is True
            assert summary.agent_info["name"] == "fake-acp-agent"
            assert summary.protocol_version == 1
            session_id = await asyncio.wait_for(
                driver.new_session(cwd=str(tmp_path)), 10
            )
            assert session_id == "fake-external-session-1"
            model, effort = await asyncio.wait_for(driver.set_config_exact(), 10)
            assert (model, effort) == ("kimi-for-coding/k3", "max")
            labels = [label for label, _ in harness.machine.snapshots]
            assert labels == ["initial", "post_model", "post_effort"]
            outcome = await asyncio.wait_for(driver.prompt_once("hello agent"), 10)
            assert outcome.stop_reason == "end_turn"
            assert outcome.usage == {
                "totalTokens": 30,
                "inputTokens": 20,
                "outputTokens": 10,
            }
            chunk_updates = [
                update
                for _, update in harness.updates
                if update.get("sessionUpdate") == "agent_message_chunk"
            ]
            assert chunk_updates, harness.updates
            assert chunk_updates[0]["content"]["text"] == "FAKE_AGENT_OK"
        assert "session/prompt" in harness.methods_seen()

    asyncio.run(case())


def test_second_prompt_on_same_driver_fails_closed(tmp_path: Path) -> None:
    # B1 boundary pin (2026-07-24 focused-review WATCH): the prompt wire
    # boundary is write-once, so one driver/connection supports exactly one
    # prompt. A second prompt must be refused before any wire write instead
    # of silently inheriting the first Turn's boundary.
    async def case() -> None:
        async with _Harness(tmp_path, HAPPY_SCRIPT) as harness:
            driver = harness.driver
            await asyncio.wait_for(driver.initialize(), 10)
            await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
            await asyncio.wait_for(driver.set_config_exact(), 10)
            outcome = await asyncio.wait_for(driver.prompt_once("first prompt"), 10)
            assert outcome.stop_reason == "end_turn"
            with pytest.raises(NativeDriverError, match="one prompt per driver"):
                await asyncio.wait_for(driver.prompt_once("second prompt"), 10)
        assert harness.methods_seen().count("session/prompt") == 1

    asyncio.run(case())


def test_effort_absent_from_post_model_set_fails_closed(tmp_path: Path) -> None:
    script = dict(HAPPY_SCRIPT)
    script["post_model_options"] = [
        option
        for option in HAPPY_SCRIPT["post_model_options"]
        if option["id"] != "effort"
    ]

    async def case() -> None:
        async with _Harness(tmp_path, script) as harness:
            driver = harness.driver
            await asyncio.wait_for(driver.initialize(), 10)
            await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
            with pytest.raises(ConfigFidelityError):
                await asyncio.wait_for(driver.set_config_exact(), 10)
            # The prompt gate stays closed after the fidelity failure.
            with pytest.raises(ConfigFidelityError):
                await asyncio.wait_for(driver.prompt_once("must not send"), 10)
        assert "session/prompt" not in harness.methods_seen()

    asyncio.run(case())


def test_inexact_readback_fails_closed_without_prompt(tmp_path: Path) -> None:
    script = dict(HAPPY_SCRIPT)
    script["wrong_readback"] = {"effort": "high"}

    async def case() -> None:
        async with _Harness(tmp_path, script) as harness:
            driver = harness.driver
            await asyncio.wait_for(driver.initialize(), 10)
            await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
            with pytest.raises(ConfigFidelityError):
                await asyncio.wait_for(driver.set_config_exact(), 10)
        assert "session/prompt" not in harness.methods_seen()

    asyncio.run(case())


def test_missing_initial_options_fails_closed(tmp_path: Path) -> None:
    script = dict(HAPPY_SCRIPT)
    script["omit_initial_options"] = True

    async def case() -> None:
        async with _Harness(tmp_path, script) as harness:
            driver = harness.driver
            await asyncio.wait_for(driver.initialize(), 10)
            with pytest.raises(ConfigFidelityError):
                await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
        assert "session/prompt" not in harness.methods_seen()

    asyncio.run(case())


def test_malformed_frame_is_a_controlled_driver_error(tmp_path: Path) -> None:
    script = {"malformed_frame_on_initialize": True}

    async def case() -> None:
        async with _Harness(tmp_path, script) as harness:
            with pytest.raises(NativeDriverError):
                await asyncio.wait_for(harness.driver.initialize(), 10)
        assert "session/prompt" not in harness.methods_seen()

    asyncio.run(case())


def test_child_killed_mid_handshake_is_controlled(tmp_path: Path) -> None:
    script = dict(HAPPY_SCRIPT)
    script["exit_on_set_config"] = True

    async def case() -> None:
        async with _Harness(tmp_path, script) as harness:
            driver = harness.driver
            await asyncio.wait_for(driver.initialize(), 10)
            await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
            with pytest.raises(NativeDriverError):
                await asyncio.wait_for(driver.set_config_exact(), 10)
        assert "session/prompt" not in harness.methods_seen()

    asyncio.run(case())


def test_cancel_path_returns_cancelled_stop_reason(tmp_path: Path) -> None:
    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True

    async def case() -> None:
        async with _Harness(tmp_path, script) as harness:
            driver = harness.driver
            await asyncio.wait_for(driver.initialize(), 10)
            await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
            await asyncio.wait_for(driver.set_config_exact(), 10)
            prompt_task = asyncio.ensure_future(driver.prompt_once("long task"))
            await asyncio.sleep(0.3)
            await driver.cancel()
            outcome = await asyncio.wait_for(prompt_task, 10)
            assert outcome.stop_reason == "cancelled"

    asyncio.run(case())


def test_capability_recording_without_load_session(tmp_path: Path) -> None:
    script = dict(HAPPY_SCRIPT)
    script["load_session_advertised"] = False

    async def case() -> None:
        async with _Harness(tmp_path, script) as harness:
            summary = await asyncio.wait_for(harness.driver.initialize(), 10)
            assert summary.load_session_advertised is False

    asyncio.run(case())


# -- direct machine ordering pins (no wire) ---------------------------------


def test_machine_requires_initial_options_before_model_plan() -> None:
    machine = _machine()
    with pytest.raises(ConfigFidelityError):
        machine.model_plan()


def test_machine_rejects_null_initial_options() -> None:
    machine = _machine()
    with pytest.raises(ConfigFidelityError):
        machine.record_initial_options(None)


def test_machine_effort_step_unreachable_without_post_model_set() -> None:
    machine = _machine()
    machine.record_initial_options(
        [
            {
                "id": "model",
                "name": "Model",
                "type": "select",
                "currentValue": "provider/base",
                "options": [{"value": "kimi-for-coding/k3", "name": "K3"}],
            },
            {
                "id": "effort",
                "name": "Effort",
                "type": "select",
                "currentValue": "high",
                "options": [{"value": "max", "name": "Max"}],
            },
        ]
    )
    machine.model_plan()
    # Skipping the post-set-model consumption makes effort discovery
    # structurally impossible.
    with pytest.raises(ConfigFidelityError):
        machine.effort_plan()


def test_machine_ready_gate_requires_verified_state() -> None:
    machine = _machine()
    with pytest.raises(ConfigFidelityError):
        machine.require_ready()

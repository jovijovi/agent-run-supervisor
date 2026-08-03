"""Model-only configuration fidelity, and the separate-selector default.

Two explicit fidelity modes exist. ``separate-selectors`` is what every profile
did and still does: discover → set model → rediscover → set effort → exact
readback of the pair. ``model-only`` describes an agent whose model selector
*is* the whole configuration: there is no independent effort selector to
discover, so the sequence stops at the exact model readback, no effort RPC is
dispatched at all, and the effective effort is the shared ``N/A`` sentinel.

The Cursor evidence is one selector value, ``grok-4.5[effort=high,fast=true]``,
and it is treated as **opaque**: ARS sets it and reads it back byte-for-byte.
Nothing here parses ``high`` out of it, infers an effort from it, maps a model
name, or reads the agent's unrelated ACP ``mode`` selector as an effort.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.managed_process import (
    ManagedProcessLimits,
    spawn_managed_process,
)
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.client import NativeAcpClient
from agent_run_supervisor.native_acp.config_fidelity import (
    EFFORT_NOT_APPLICABLE,
    FIDELITY_MODEL_ONLY,
    FIDELITY_SEPARATE_SELECTORS,
    ConfigFidelityError,
    ConfigFidelityMachine,
)
from agent_run_supervisor.native_acp.driver import NativeAcpDriver
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_COMPAT_V1,
    CURSOR_NATIVE_ACP_V1,
    DEFAULT_REGISTRY,
    STANDARD_NATIVE_ACP_V1,
    AcpCompatProfile,
    AgentInstance,
    ProfileRegistry,
    ProfileValidationError,
)

from .test_run_task import HAPPY_SCRIPT, Harness, _request, _run

FAKE_AGENT_PATH = Path(__file__).with_name("fake_agent.py")
SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

# The exact Cursor selector value from the cited evidence. Opaque end to end.
CURSOR_MODEL = "grok-4.5[effort=high,fast=true]"
CURSOR_MODEL_OTHER = "grok-4.5[effort=low,fast=false]"

# A Cursor-shaped agent: one model selector, one unrelated ACP ``mode``
# selector, and no effort selector anywhere.
CURSOR_OPTIONS = [
    {
        "id": "model",
        "name": "Model",
        "type": "select",
        "currentValue": CURSOR_MODEL_OTHER,
        "options": [
            {"value": CURSOR_MODEL_OTHER, "name": "Grok 4.5 (low)"},
            {"value": CURSOR_MODEL, "name": "Grok 4.5 (high, fast)"},
        ],
    },
    {
        "id": "mode",
        "name": "Mode",
        "type": "select",
        "currentValue": "ask",
        "options": [
            {"value": "ask", "name": "Ask"},
            {"value": "agent", "name": "Agent"},
        ],
    },
]

CURSOR_SCRIPT = {
    "initial_options": CURSOR_OPTIONS,
    "post_model_options_by_value": {CURSOR_MODEL: CURSOR_OPTIONS},
    "final_message": "CURSOR_OK",
}

CURSOR_PROFILE_ID = "cursor-fake-agent-v1"
CURSOR_AGENT_ID = "cursor-fake-agent"


def _model_only_profile(**overrides) -> AcpCompatProfile:
    kwargs = dict(
        profile_id=CURSOR_PROFILE_ID,
        revision=1,
        acp_protocol_version="1",
        required_capabilities=(),
        base_allowlist=("PATH", "HOME", "FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
        requires_session_load=False,
        config_fidelity_mode=FIDELITY_MODEL_ONLY,
        effort_selector_id=None,
    )
    kwargs.update(overrides)
    return AcpCompatProfile(**kwargs)


def _model_only_entry(**overrides) -> AgentEntry:
    kwargs = dict(
        agent_id=CURSOR_AGENT_ID,
        profile_id=CURSOR_PROFILE_ID,
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
    )
    kwargs.update(overrides)
    return AgentEntry(**kwargs)


def _model_only_machine(**overrides) -> ConfigFidelityMachine:
    kwargs = dict(
        model_selector_id="model",
        effort_selector_id=None,
        requested_model=CURSOR_MODEL,
        requested_effort=EFFORT_NOT_APPLICABLE,
        fidelity_mode=FIDELITY_MODEL_ONLY,
    )
    kwargs.update(overrides)
    return ConfigFidelityMachine(**kwargs)


# -- one shared sentinel, declared once -------------------------------------


def test_the_not_applicable_effort_sentinel_is_one_source_constant() -> None:
    assert EFFORT_NOT_APPLICABLE == "N/A"
    occurrences: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == EFFORT_NOT_APPLICABLE:
                occurrences.append(f"{path.name}:{node.lineno}")
    assert occurrences == ["config_fidelity.py:" + str(_sentinel_lineno())], occurrences


def _sentinel_lineno() -> int:
    from agent_run_supervisor.native_acp import config_fidelity

    source = Path(config_fidelity.__file__).read_text(encoding="utf-8")
    for index, line in enumerate(source.splitlines(), start=1):
        if line.startswith("EFFORT_NOT_APPLICABLE"):
            return index
    raise AssertionError("EFFORT_NOT_APPLICABLE is not defined in config_fidelity")


# -- the registry and its declared modes ------------------------------------


def test_cursor_native_acp_v1_is_registered_and_model_only() -> None:
    profile = DEFAULT_REGISTRY.get("cursor-native-acp-v1")
    assert profile is CURSOR_NATIVE_ACP_V1
    assert profile.config_fidelity_mode == FIDELITY_MODEL_ONLY
    assert profile.effort_selector_id is None
    assert "cursor-native-acp-v1" in DEFAULT_REGISTRY.ids()


def test_the_cursor_profile_deviates_only_in_effort_fidelity() -> None:
    """Its one proven deviation, and nothing else.

    Everything a profile freezes other than configuration fidelity must equal
    the standard conformance contract, or the deviation would be broader than
    the evidence supports.
    """
    standard = STANDARD_NATIVE_ACP_V1.snapshot()
    cursor = CURSOR_NATIVE_ACP_V1.snapshot()
    for shared in (
        "revision",
        "acp_protocol_version",
        "required_capabilities",
        "forbidden_capabilities",
        "requires_session_load",
        "base_allowlist",
        "model_selector_id",
    ):
        assert cursor[shared] == standard[shared], shared
    assert cursor["profile_id"] == "cursor-native-acp-v1"
    assert cursor["effort_selector_id"] is None
    assert cursor["config_fidelity_mode"] == FIDELITY_MODEL_ONLY
    # No frozen session metadata, no required permission mode: the deviation is
    # effort fidelity, full stop.
    assert "session_meta" not in cursor
    assert "permission_mode_selector_id" not in cursor


def test_existing_profiles_declare_separate_selectors_unchanged() -> None:
    for profile in (STANDARD_NATIVE_ACP_V1, CLAUDE_AGENT_ACP_COMPAT_V1):
        assert profile.config_fidelity_mode == FIDELITY_SEPARATE_SELECTORS
        assert profile.effort_selector_id == "effort"


def test_adding_the_mode_did_not_move_an_existing_profile_hash() -> None:
    """Session identity must not be cut by a field that did not apply.

    ``profile_hash`` is a Session identity field, so it may move only when the
    profile's own ACP semantics move. The two existing profiles kept the
    behaviour they always had, so their snapshots — and therefore their hashes
    — must stay byte-identical to what they were before the mode existed.
    These two literals were captured from ``main`` before this change.
    """
    assert STANDARD_NATIVE_ACP_V1.profile_hash() == (
        "fcf4d46c2c072ba9bd23b198beb096cb9748e62e8168c2a48e5c76432d55f9b9"
    )
    assert CLAUDE_AGENT_ACP_COMPAT_V1.profile_hash() == (
        "c9e9258bfcc01e2962b87466c803d0a3ae25a1676936864bdbd78b75a544a241"
    )
    # The mechanism, so a future default change cannot silently move them: the
    # key appears only on a profile that actually deviates.
    assert "config_fidelity_mode" not in STANDARD_NATIVE_ACP_V1.snapshot()
    assert "config_fidelity_mode" not in CLAUDE_AGENT_ACP_COMPAT_V1.snapshot()


# -- invalid combinations refuse, at construction ---------------------------


def test_a_model_only_profile_may_not_declare_an_effort_selector() -> None:
    with pytest.raises(ProfileValidationError):
        _model_only_profile(effort_selector_id="effort")


def test_a_separate_selector_profile_must_declare_an_effort_selector() -> None:
    with pytest.raises(ProfileValidationError):
        _model_only_profile(
            config_fidelity_mode=FIDELITY_SEPARATE_SELECTORS,
            effort_selector_id=None,
        )


def test_an_unknown_fidelity_mode_is_refused() -> None:
    with pytest.raises(ProfileValidationError):
        _model_only_profile(config_fidelity_mode="model-and-vibes")


def test_a_registry_entry_may_not_hint_an_effort_selector_on_model_only() -> None:
    """An operator hint for a selector that is never set would be a fiction."""
    with pytest.raises(ProfileValidationError):
        AgentInstance(
            profile=_model_only_profile(),
            entry=_model_only_entry(effort_selector_id="reasoning_effort"),
        )


def test_the_instance_reports_the_profile_mode_and_no_effort_selector() -> None:
    instance = AgentInstance(profile=_model_only_profile(), entry=_model_only_entry())
    assert instance.config_fidelity_mode == FIDELITY_MODEL_ONLY
    assert instance.effort_selector_id is None
    assert instance.model_selector_id == "model"


# -- the machine ------------------------------------------------------------


def test_model_only_requires_the_shared_not_applicable_effort() -> None:
    with pytest.raises(ConfigFidelityError):
        _model_only_machine(requested_effort="high")
    with pytest.raises(ConfigFidelityError):
        _model_only_machine(requested_effort="")


def test_model_only_refuses_a_declared_effort_selector() -> None:
    with pytest.raises(ConfigFidelityError):
        _model_only_machine(effort_selector_id="effort")


def test_model_only_reaches_verified_on_the_exact_model_readback() -> None:
    machine = _model_only_machine()
    machine.record_initial_options(CURSOR_OPTIONS)
    assert machine.model_plan() == "model"
    machine.record_post_model_options(
        [dict(option, currentValue=CURSOR_MODEL) if option["id"] == "model" else option
         for option in CURSOR_OPTIONS]
    )
    assert machine.phase == "verified"
    assert machine.require_ready() == (CURSOR_MODEL, EFFORT_NOT_APPLICABLE)
    assert [label for label, _ in machine.snapshots] == ["initial", "post_model"]


def test_model_only_never_offers_an_effort_plan() -> None:
    machine = _model_only_machine()
    machine.record_initial_options(CURSOR_OPTIONS)
    machine.model_plan()
    machine.record_post_model_options(
        [dict(option, currentValue=CURSOR_MODEL) if option["id"] == "model" else option
         for option in CURSOR_OPTIONS]
    )
    with pytest.raises(ConfigFidelityError):
        machine.effort_plan()


def test_model_only_refuses_a_wrong_model_readback() -> None:
    machine = _model_only_machine()
    machine.record_initial_options(CURSOR_OPTIONS)
    machine.model_plan()
    with pytest.raises(ConfigFidelityError):
        machine.record_post_model_options(CURSOR_OPTIONS)  # currentValue unchanged
    assert machine.phase != "verified"
    with pytest.raises(ConfigFidelityError):
        machine.require_ready()


def test_model_only_refuses_a_missing_model_readback() -> None:
    machine = _model_only_machine()
    machine.record_initial_options(CURSOR_OPTIONS)
    machine.model_plan()
    with pytest.raises(ConfigFidelityError):
        machine.record_post_model_options(
            [option for option in CURSOR_OPTIONS if option["id"] != "model"]
        )
    with pytest.raises(ConfigFidelityError):
        machine.require_ready()


def test_the_mode_selector_is_never_read_as_an_effort() -> None:
    """``mode`` is an unrelated ACP selector, and stays one."""
    machine = _model_only_machine()
    machine.record_initial_options(CURSOR_OPTIONS)
    machine.model_plan()
    machine.record_post_model_options(
        [dict(option, currentValue=CURSOR_MODEL) if option["id"] == "model" else option
         for option in CURSOR_OPTIONS]
    )
    _model, effort = machine.require_ready()
    assert effort == EFFORT_NOT_APPLICABLE
    assert effort != "ask"


def test_separate_selectors_stays_the_default_and_the_existing_sequence() -> None:
    """Byte-for-byte the prior behaviour: same phases, same labels, same pair."""
    machine = ConfigFidelityMachine(
        model_selector_id="model",
        effort_selector_id="effort",
        requested_model="kimi-for-coding/k3",
        requested_effort="max",
    )
    assert machine.fidelity_mode == FIDELITY_SEPARATE_SELECTORS
    initial = [
        {
            "id": "model",
            "type": "select",
            "currentValue": "provider/base",
            "options": [
                {"value": "provider/base"},
                {"value": "kimi-for-coding/k3"},
            ],
        },
        {
            "id": "effort",
            "type": "select",
            "currentValue": "high",
            "options": [{"value": "high"}],
        },
    ]
    post_model = [
        {
            "id": "model",
            "type": "select",
            "currentValue": "kimi-for-coding/k3",
            "options": [
                {"value": "provider/base"},
                {"value": "kimi-for-coding/k3"},
            ],
        },
        {
            "id": "effort",
            "type": "select",
            "currentValue": "high",
            "options": [{"value": "high"}, {"value": "max"}],
        },
    ]
    post_effort = [
        post_model[0],
        dict(post_model[1], currentValue="max"),
    ]

    machine.record_initial_options(initial)
    assert machine.model_plan() == "model"
    machine.record_post_model_options(post_model)
    assert machine.phase == "post_model"
    assert machine.effort_plan() == "effort"
    machine.record_post_effort_options(post_effort)
    assert machine.phase == "verified"
    assert machine.require_ready() == ("kimi-for-coding/k3", "max")
    assert [label for label, _ in machine.snapshots] == [
        "initial",
        "post_model",
        "post_effort",
    ]


# -- the driver against a Cursor-shaped ACP server --------------------------


async def _cursor_driver_case(tmp_path: Path, script: dict, machine):
    trace = tmp_path / "fake-agent-trace.log"
    configs = tmp_path / "fake-agent-config.log"
    script = dict(script)
    script["capture_config_path"] = str(configs)
    env = dict(os.environ)
    env["FAKE_AGENT_SCRIPT"] = json.dumps(script)
    env["FAKE_AGENT_TRACE"] = str(trace)
    proc = await spawn_managed_process(
        argv=[sys.executable, "-u", str(FAKE_AGENT_PATH)],
        cwd=Path.cwd(),
        env=env,
        limits=ManagedProcessLimits(),
    )
    client = NativeAcpClient(on_update=lambda session_id, update: None)
    driver = NativeAcpDriver(client=client, machine=machine)
    try:
        await driver.open(proc)
        await asyncio.wait_for(driver.initialize(), 10)
        await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
        pair = await asyncio.wait_for(driver.set_config_exact(), 10)
        outcome = await asyncio.wait_for(driver.prompt_once("hello cursor"), 10)
        return pair, outcome, _lines(trace), _lines(configs)
    finally:
        await driver.close()
        proc.kill_group()
        await proc.wait()


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _lines_after_failure(tmp_path: Path, script: dict, machine):
    async def case():
        trace = tmp_path / "fake-agent-trace.log"
        configs = tmp_path / "fake-agent-config.log"
        script_copy = dict(script)
        script_copy["capture_config_path"] = str(configs)
        env = dict(os.environ)
        env["FAKE_AGENT_SCRIPT"] = json.dumps(script_copy)
        env["FAKE_AGENT_TRACE"] = str(trace)
        proc = await spawn_managed_process(
            argv=[sys.executable, "-u", str(FAKE_AGENT_PATH)],
            cwd=Path.cwd(),
            env=env,
            limits=ManagedProcessLimits(),
        )
        client = NativeAcpClient(on_update=lambda session_id, update: None)
        driver = NativeAcpDriver(client=client, machine=machine)
        try:
            await driver.open(proc)
            await asyncio.wait_for(driver.initialize(), 10)
            await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
            with pytest.raises(ConfigFidelityError):
                await asyncio.wait_for(driver.set_config_exact(), 10)
            return _lines(trace), _lines(configs)
        finally:
            await driver.close()
            proc.kill_group()
            await proc.wait()

    return asyncio.run(case())


def test_a_cursor_shaped_server_prompts_only_after_the_exact_model_readback(
    tmp_path: Path,
) -> None:
    pair, outcome, methods, configs = asyncio.run(
        _cursor_driver_case(tmp_path, CURSOR_SCRIPT, _model_only_machine())
    )

    assert pair == (CURSOR_MODEL, EFFORT_NOT_APPLICABLE)
    assert outcome.stop_reason == "end_turn"
    # Exactly one config RPC, and it is the model selector: no effort selector
    # was discovered, set, or read back.
    assert configs == [f"model={CURSOR_MODEL}"]
    assert methods.count("session/set_config_option") == 1
    # The model set precedes the prompt on the wire.
    assert methods.index("session/set_config_option") < methods.index("session/prompt")


def test_a_cursor_shaped_server_that_lies_about_the_readback_never_prompts(
    tmp_path: Path,
) -> None:
    script = dict(CURSOR_SCRIPT)
    script["wrong_readback"] = {"model": CURSOR_MODEL_OTHER}
    methods, configs = _lines_after_failure(
        tmp_path, script, _model_only_machine()
    )

    assert configs == [f"model={CURSOR_MODEL}"]
    assert "session/prompt" not in methods


# -- RunTask: new session, load, rollback, persisted effort -----------------


def _cursor_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: dict):
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((_model_only_profile(),))
    harness.entry = _model_only_entry()
    return harness


def _cursor_request(**overrides):
    kwargs = dict(
        agent_id=CURSOR_AGENT_ID,
        requested_model=CURSOR_MODEL,
        requested_effort=EFFORT_NOT_APPLICABLE,
    )
    kwargs.update(overrides)
    return _request(**kwargs)


def test_a_model_only_run_completes_and_persists_the_sentinel_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "fake-agent-config.log"
    script = dict(CURSOR_SCRIPT)
    script["capture_config_path"] = str(configs)
    harness = _cursor_harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.COMPLETED
    effective = json.loads((harness.run_dir() / "effective.json").read_text())
    assert effective["effective_model"] == CURSOR_MODEL
    assert effective["effective_effort"] == EFFORT_NOT_APPLICABLE
    record = harness.session_store().open_session("sess-native-1")
    assert record.last_effective_model == CURSOR_MODEL
    assert record.last_effective_effort == EFFORT_NOT_APPLICABLE
    assert _lines(configs) == [f"model={CURSOR_MODEL}"]


def test_the_launch_snapshot_does_not_invent_an_effort_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sealed evidence stays internally truthful.

    A model-only Run never discovers or sets an effort selector, so recording
    one in ``launch.json`` would describe a call that never happened.
    """
    harness = _cursor_harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.COMPLETED
    launch = json.loads((harness.run_dir() / "launch.json").read_text())
    assert launch["model_selector_id"] == "model"
    assert launch["effort_selector_id"] is None


def test_a_non_sentinel_effort_fails_before_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "fake-agent-config.log"
    script = dict(CURSOR_SCRIPT)
    script["capture_config_path"] = str(configs)
    harness = _cursor_harness(tmp_path, monkeypatch, script)

    result = _run(
        harness.task(request=_cursor_request(requested_effort="high"))
    )

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == "CONFIG_FIDELITY"
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    assert "session/prompt" not in harness.methods_seen()
    assert _lines(configs) == []
    # A clean pre-dispatch refusal leaves the Session reusable.
    assert harness.session_store().open_session("sess-native-1").state == "open"


def test_a_model_only_reuse_run_loads_and_reconfigures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load arm uses the mode too: load → set model → exact readback."""
    configs = tmp_path / "fake-agent-config.log"
    script = dict(CURSOR_SCRIPT)
    script["capture_config_path"] = str(configs)
    harness = _cursor_harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry(
        (_model_only_profile(requires_session_load=True),)
    )

    first = _run(harness.task(run_id="run-0001", request=_cursor_request()))
    assert first.status is AgentRunStatus.COMPLETED

    second = _run(harness.task(run_id="run-0002", request=_cursor_request()))

    assert second.status is AgentRunStatus.COMPLETED
    assert "session/load" in harness.methods_seen()
    assert _lines(configs) == [f"model={CURSOR_MODEL}"] * 2
    record = harness.session_store().open_session("sess-native-1")
    assert record.last_effective_effort == EFFORT_NOT_APPLICABLE


def test_a_failed_model_only_switch_rolls_back_to_the_previous_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback re-runs the same mode: model only, exact readback, no effort."""
    harness = _cursor_harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))
    harness.registry = ProfileRegistry(
        (_model_only_profile(requires_session_load=True),)
    )
    first = _run(harness.task(run_id="run-0001", request=_cursor_request()))
    assert first.status is AgentRunStatus.COMPLETED

    configs = tmp_path / "fake-agent-config.log"
    switch = dict(CURSOR_SCRIPT)
    switch["capture_config_path"] = str(configs)
    # The second Run asks for the other model and the agent lies about the
    # readback, so the switch is partial and must roll back exactly.
    switch["wrong_readback"] = {"model": CURSOR_MODEL}
    switch["post_model_options_by_value"] = {
        CURSOR_MODEL_OTHER: CURSOR_OPTIONS,
        CURSOR_MODEL: CURSOR_OPTIONS,
    }
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(switch))

    second = _run(
        harness.task(
            run_id="run-0002",
            request=_cursor_request(requested_model=CURSOR_MODEL_OTHER),
        )
    )

    assert second.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir("run-0002") / "result.json").read_text())
    assert payload["detail_code"] == "CONFIG_FIDELITY"
    assert not (harness.run_dir("run-0002") / "prompt-dispatch-started").exists()
    # Rollback proved the previous model exactly, so the Session stays usable
    # and only model selectors were ever set.
    record = harness.session_store().open_session("sess-native-1")
    assert record.state == "open"
    assert record.last_effective_model == CURSOR_MODEL
    assert record.last_effective_effort == EFFORT_NOT_APPLICABLE
    assert all(line.startswith("model=") for line in _lines(configs))


def test_the_separate_selector_run_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default path still sets both selectors, in order."""
    configs = tmp_path / "fake-agent-config.log"
    script = dict(HAPPY_SCRIPT)
    script["capture_config_path"] = str(configs)
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    assert _lines(configs) == ["model=kimi-for-coding/k3", "effort=max"]
    effective = json.loads((harness.run_dir() / "effective.json").read_text())
    assert effective["effective_model"] == "kimi-for-coding/k3"
    assert effective["effective_effort"] == "max"

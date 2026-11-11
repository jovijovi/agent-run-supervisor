"""OpenCode minimal compatibility: one bounded, identical effort re-proof.

PRD R3/R12. ``opencode-acp-compat-v1`` is the standard contract plus one
frozen term. Under it, before any prompt, a post-effort readback whose model
(and any required mode) is exact, whose effort option is a select still
advertising the requested value, and whose current effort is different earns
**exactly one** identical re-set of the same selector and value. The new
response is then judged by the unchanged strict rules. Nothing else earns a
re-set: a wrong model, a missing, non-select, or unadvertised effort, a
malformed or null response, and an RPC error all refuse before the prompt as
before. The allowance belongs to the Run's forward machine only, so a
between-Run rollback stays strict, and the profile is an explicit opt-in — the
same stale agent under any other profile fails exactly as it always did.

Every agent-facing case drives a real ``NativeAcpDriver`` or ``RunTask`` over
the in-repo fake ACP agent, spawned as a real process. The fake's
``set_config_steps`` key scripts the *n*-th set of one config id, which is what
lets one test make the first effort readback stale and the second exact.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.managed_process import (
    ManagedProcess,
    ManagedProcessLimits,
    spawn_managed_process,
)
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.client import NativeAcpClient
from agent_run_supervisor.native_acp.config_fidelity import (
    ConfigFidelityError,
    ConfigFidelityMachine,
)
from agent_run_supervisor.native_acp.driver import NativeAcpDriver, NativeDriverError
from agent_run_supervisor.native_acp.profile import DEFAULT_REGISTRY, ProfileRegistry
from agent_run_supervisor.native_acp.run_task import (
    CONFIG_PROVEN_MARKER,
    CONFIG_ROLLBACK_PROVEN_MARKER,
    CONFIG_SWITCH_STARTED_MARKER,
    DISPATCH_STARTED_MARKER,
)
from agent_run_supervisor.session import QUARANTINE_SWITCH_ROLLBACK_UNPROVEN

from .test_run_task import FAKE_AGENT_PATH, Harness, _request, _run

OPENCODE_PROFILE = "opencode-acp-compat-v1"
STANDARD_PROFILE = "standard-native-acp-v1"
CODEX_PROFILE = "codex-agent-acp-compat-v1"
REASONIX_PROFILE = "reasonix-agent-acp-compat-v1"
AGENT_ID = "reproof-agent"

INITIAL_MODEL = "provider/base"
REQUESTED_MODEL = "provider/requested-model"
PREVIOUS_EFFORT = "high"
REQUESTED_EFFORT = "max"
STALE_EFFORT = "default"

STALE = {"readback": STALE_EFFORT}
EFFORT_SET = f"effort={REQUESTED_EFFORT}"


# -- wire-shaped option sets --------------------------------------------------


def _model(current: str) -> dict:
    return {
        "id": "model",
        "name": "Model",
        "type": "select",
        "currentValue": current,
        "options": [
            {"value": INITIAL_MODEL, "name": "Base"},
            {"value": REQUESTED_MODEL, "name": "Requested"},
        ],
    }


def _effort(current, values=(STALE_EFFORT, PREVIOUS_EFFORT, REQUESTED_EFFORT)) -> dict:
    return {
        "id": "effort",
        "name": "Effort",
        "type": "select",
        "currentValue": current,
        "options": [{"value": value, "name": value} for value in values],
    }


def _mode(current: str) -> dict:
    return {
        "id": "mode",
        "name": "Mode",
        "type": "select",
        "currentValue": current,
        "options": [
            {"value": value, "name": value}
            for value in ("ask", "read-only", "agent", "agent-full-access")
        ],
    }


def _approval(current: str) -> dict:
    return {
        "id": "tool_approval",
        "name": "Tool approval",
        "type": "select",
        "currentValue": current,
        "options": [{"value": value, "name": value} for value in ("ask", "auto", "yolo")],
    }


BOOLEAN_EFFORT = {"id": "effort", "name": "Effort", "type": "boolean", "currentValue": False}
ID_LESS_EFFORT = {
    "name": "Effort",
    "type": "select",
    "currentValue": STALE_EFFORT,
    "options": [{"value": REQUESTED_EFFORT, "name": REQUESTED_EFFORT}],
}


def _options(model: str, effort: str, *, mode: str = "read-only", approval: str = "ask") -> list[dict]:
    """One option set every registered separate-selector profile can drive.

    The ``mode`` and ``tool_approval`` selectors are inert for profiles that
    declare no permission mode, and carry the values the Codex (read grant) and
    Reasonix profiles require once set, so the opt-out cases can reuse it.
    """
    return [_model(model), _effort(effort), _mode(mode), _approval(approval)]


def _script(*, effort_steps=None, capture: Path | None = None) -> dict:
    """The observed OpenCode shape: the requested model resets effort to ``default``."""
    script: dict = {
        "initial_options": _options(INITIAL_MODEL, PREVIOUS_EFFORT, mode="agent", approval="auto"),
        "post_model_options_by_value": {
            REQUESTED_MODEL: _options(REQUESTED_MODEL, STALE_EFFORT),
            INITIAL_MODEL: _options(INITIAL_MODEL, PREVIOUS_EFFORT),
        },
        "final_message": "REPROOF_AGENT_OK",
    }
    if effort_steps is not None:
        script["set_config_steps"] = {"effort": effort_steps}
    if capture is not None:
        script["capture_config_path"] = str(capture)
    return script


# -- RunTask harness ----------------------------------------------------------


def _entry(profile_id: str) -> AgentEntry:
    return AgentEntry(
        agent_id=AGENT_ID,
        profile_id=profile_id,
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
        env_passthrough=("FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
    )


def _harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: dict,
    profile_id: str = OPENCODE_PROFILE,
) -> Harness:
    """A RunTask harness bound to one **registered** source profile."""
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((DEFAULT_REGISTRY.get(profile_id),))
    harness.entry = _entry(profile_id)
    return harness


def _create(model: str = REQUESTED_MODEL, effort: str = REQUESTED_EFFORT):
    return _request(
        agent_id=AGENT_ID,
        session_id=None,
        requested_model=model,
        requested_effort=effort,
        grant_capabilities=("read",),
    )


def _reuse(session_id: str, model: str = REQUESTED_MODEL, effort: str = REQUESTED_EFFORT):
    return _request(
        agent_id=AGENT_ID,
        session_id=session_id,
        requested_model=model,
        requested_effort=effort,
        grant_capabilities=("read",),
    )


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _payload(harness: Harness, run_id: str) -> dict:
    return json.loads((harness.run_dir(run_id) / "result.json").read_text())


def _effective(harness: Harness, run_id: str) -> dict:
    return json.loads((harness.run_dir(run_id) / "effective.json").read_text())


def _labels(effective: dict) -> list[str]:
    return [row["label"] for row in effective["discovery_snapshots"]]


def _current(effective: dict, label: str, option_id: str):
    rows = [row for row in effective["discovery_snapshots"] if row["label"] == label]
    assert len(rows) == 1, label
    option = next((item for item in rows[0]["options"] if item.get("id") == option_id), None)
    return None if option is None else option.get("currentValue")


def _event_types(harness: Harness, run_id: str) -> list[str]:
    path = harness.run_dir(run_id) / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["type"] for line in path.read_text().splitlines() if line]


def _set_env(monkeypatch: pytest.MonkeyPatch, script: dict) -> None:
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))


def _assert_zero_prompt(harness: Harness, run_id: str) -> None:
    assert not (harness.run_dir(run_id) / DISPATCH_STARTED_MARKER).exists()
    assert "session/prompt" not in harness.methods_seen()


# -- machine unit: the eligibility matrix ------------------------------------


def _machine(*, allowed: bool, mode: str | None = None) -> ConfigFidelityMachine:
    kwargs: dict = dict(
        model_selector_id="model",
        effort_selector_id="effort",
        requested_model=REQUESTED_MODEL,
        requested_effort=REQUESTED_EFFORT,
    )
    if mode is not None:
        kwargs.update(permission_mode_selector_id="mode", required_permission_mode=mode)
    if allowed:
        kwargs["effort_reproof_allowed"] = True
    return ConfigFidelityMachine(**kwargs)


def _to_effort_planned(machine: ConfigFidelityMachine, mode: str | None = None) -> None:
    machine.record_initial_options(_options(INITIAL_MODEL, PREVIOUS_EFFORT, mode="agent"))
    if mode is not None:
        machine.permission_mode_plan()
        machine.record_post_mode_options(_options(INITIAL_MODEL, PREVIOUS_EFFORT, mode=mode))
    machine.model_plan()
    machine.record_post_model_options(_options(REQUESTED_MODEL, STALE_EFFORT, mode=mode or "read-only"))
    assert machine.effort_plan() == "effort"


@pytest.mark.parametrize("mode", [None, "ask"], ids=["modeless", "with-mode"])
def test_machine_grants_exactly_one_reproof_for_a_stale_advertised_effort(mode) -> None:
    machine = _machine(allowed=True, mode=mode)
    _to_effort_planned(machine, mode)

    machine.record_post_effort_options(_options(REQUESTED_MODEL, STALE_EFFORT, mode=mode or "read-only"))

    assert machine.phase == "effort_reproof_planned"
    # A caller that ignores the pending state still cannot reach the prompt.
    with pytest.raises(ConfigFidelityError, match="prompt is unreachable"):
        machine.require_ready()
    assert machine.effort_reproof_plan() == "effort"
    machine.record_post_effort_reproof_options(
        _options(REQUESTED_MODEL, REQUESTED_EFFORT, mode=mode or "read-only")
    )
    assert machine.require_ready() == (REQUESTED_MODEL, REQUESTED_EFFORT)
    labels = [label for label, _ in machine.snapshots]
    assert labels[-2:] == ["post_effort", "post_effort_reproof"]
    # Both readbacks are retained exactly as the agent reported them.
    post_effort = dict(machine.snapshots)["post_effort"]
    reproof = dict(machine.snapshots)["post_effort_reproof"]
    assert [o["currentValue"] for o in post_effort if o["id"] == "effort"] == [STALE_EFFORT]
    assert [o["currentValue"] for o in reproof if o["id"] == "effort"] == [REQUESTED_EFFORT]


def test_machine_judges_the_reproof_readback_strictly_with_no_second_allowance() -> None:
    machine = _machine(allowed=True)
    _to_effort_planned(machine)
    machine.record_post_effort_options(_options(REQUESTED_MODEL, STALE_EFFORT))
    machine.effort_reproof_plan()

    with pytest.raises(ConfigFidelityError) as excinfo:
        machine.record_post_effort_reproof_options(_options(REQUESTED_MODEL, STALE_EFFORT))

    assert str(excinfo.value) == (
        "effort readback mismatch: requested 'max', effective 'default'"
    )
    assert machine.phase != "verified"
    with pytest.raises(ConfigFidelityError):
        machine.effort_reproof_plan()
    assert [label for label, _ in machine.snapshots][-2:] == [
        "post_effort",
        "post_effort_reproof",
    ]


def test_machine_reproof_readback_with_a_moved_model_is_refused() -> None:
    machine = _machine(allowed=True)
    _to_effort_planned(machine)
    machine.record_post_effort_options(_options(REQUESTED_MODEL, STALE_EFFORT))
    machine.effort_reproof_plan()
    with pytest.raises(ConfigFidelityError, match="model readback mismatch after effort set"):
        machine.record_post_effort_reproof_options(_options(INITIAL_MODEL, REQUESTED_EFFORT))


_INELIGIBLE = {
    "wrong-model": (None, [_model(INITIAL_MODEL), _effort(STALE_EFFORT)]),
    "missing-effort": (None, [_model(REQUESTED_MODEL)]),
    "non-select-effort": (None, [_model(REQUESTED_MODEL), BOOLEAN_EFFORT]),
    "unadvertised-effort": (
        None,
        [_model(REQUESTED_MODEL), _effort(STALE_EFFORT, values=(STALE_EFFORT, PREVIOUS_EFFORT))],
    ),
    "missing-effort-value": (None, [_model(REQUESTED_MODEL), _effort(None)]),
    "mode-mismatch": ("ask", [_model(REQUESTED_MODEL), _effort(STALE_EFFORT), _mode("agent")]),
    "mode-missing": ("ask", [_model(REQUESTED_MODEL), _effort(STALE_EFFORT)]),
}


@pytest.mark.parametrize("case", sorted(_INELIGIBLE))
def test_machine_ineligible_readbacks_keep_todays_byte_identical_refusal(case: str) -> None:
    mode, options = _INELIGIBLE[case]
    messages = []
    for allowed in (False, True):
        machine = _machine(allowed=allowed, mode=mode)
        _to_effort_planned(machine, mode)
        with pytest.raises(ConfigFidelityError) as excinfo:
            machine.record_post_effort_options(options)
        assert machine.phase == "effort_planned", case
        messages.append(str(excinfo.value))
    assert messages[0] == messages[1], case


def test_machine_without_the_allowance_refuses_a_stale_effort_as_before() -> None:
    machine = _machine(allowed=False)
    _to_effort_planned(machine)
    with pytest.raises(ConfigFidelityError) as excinfo:
        machine.record_post_effort_options(_options(REQUESTED_MODEL, STALE_EFFORT))
    assert str(excinfo.value) == (
        "effort readback mismatch: requested 'max', effective 'default'"
    )
    assert [label for label, _ in machine.snapshots][-1] == "post_effort"


def test_machine_null_post_effort_options_never_reach_eligibility() -> None:
    messages = []
    for allowed in (False, True):
        machine = _machine(allowed=allowed)
        _to_effort_planned(machine)
        with pytest.raises(ConfigFidelityError) as excinfo:
            machine.record_post_effort_options(None)
        messages.append(str(excinfo.value))
    assert messages[0] == messages[1]
    assert "advertised no config options at post-set-effort" in messages[0]


def test_machine_exact_readback_consumes_no_allowance() -> None:
    machine = _machine(allowed=True)
    _to_effort_planned(machine)
    machine.record_post_effort_options(_options(REQUESTED_MODEL, REQUESTED_EFFORT))
    assert machine.require_ready() == (REQUESTED_MODEL, REQUESTED_EFFORT)
    assert "post_effort_reproof" not in [label for label, _ in machine.snapshots]


def test_machine_reproof_legs_refuse_outside_their_phase() -> None:
    machine = _machine(allowed=True)
    _to_effort_planned(machine)
    with pytest.raises(ConfigFidelityError):
        machine.effort_reproof_plan()
    with pytest.raises(ConfigFidelityError):
        machine.record_post_effort_reproof_options(_options(REQUESTED_MODEL, REQUESTED_EFFORT))
    disabled = _machine(allowed=False)
    _to_effort_planned(disabled)
    with pytest.raises(ConfigFidelityError):
        disabled.effort_reproof_plan()


# -- L2: the real driver over the fake ACP agent -----------------------------


async def _deny(_request: dict) -> dict:
    return {"decision": "deny", "reason": "test default-deny"}


def _drive(
    tmp_path: Path,
    script: dict,
    machine: ConfigFidelityMachine,
    *,
    prompt: bool,
):
    """Run initialize → session/new → set_config_exact [→ prompt] once.

    Returns ``(outcome, methods)``, where ``outcome`` is the pair or the raised
    exception, so a refusal is asserted without hiding a hang.
    """
    trace = tmp_path / "driver-trace.log"
    env = dict(os.environ)
    env["FAKE_AGENT_SCRIPT"] = json.dumps(script)
    env["FAKE_AGENT_TRACE"] = str(trace)

    async def case():
        proc: ManagedProcess = await spawn_managed_process(
            argv=[sys.executable, "-u", str(FAKE_AGENT_PATH)],
            cwd=tmp_path,
            env=env,
            limits=ManagedProcessLimits(),
        )
        client = NativeAcpClient(on_update=lambda *_: None, permission_handler=_deny)
        driver = NativeAcpDriver(client=client, machine=machine)
        try:
            await driver.open(proc)
            await asyncio.wait_for(driver.initialize(), 10)
            await asyncio.wait_for(driver.new_session(cwd=str(tmp_path)), 10)
            try:
                outcome = await asyncio.wait_for(driver.set_config_exact(), 10)
            except (ConfigFidelityError, NativeDriverError) as exc:
                return exc
            if prompt:
                await asyncio.wait_for(driver.prompt_once("hello agent"), 10)
            return outcome
        finally:
            await driver.close()
            proc.kill_group()
            await proc.wait()

    outcome = asyncio.run(case())
    methods = [line for line in _lines(trace) if line]
    return outcome, methods


def test_driver_recovers_a_stale_effort_with_one_identical_set_then_prompts(
    tmp_path: Path,
) -> None:
    configs = tmp_path / "configs.log"
    machine = _machine(allowed=True)
    outcome, methods = _drive(
        tmp_path, _script(effort_steps=[STALE], capture=configs), machine, prompt=True
    )
    assert outcome == (REQUESTED_MODEL, REQUESTED_EFFORT)
    assert _lines(configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET, EFFORT_SET]
    assert methods.count("session/set_config_option") == 3
    assert methods.index("session/prompt") > max(
        index for index, method in enumerate(methods) if method == "session/set_config_option"
    )
    assert [label for label, _ in machine.snapshots] == [
        "initial",
        "post_model",
        "post_effort",
        "post_effort_reproof",
    ]


def test_driver_with_a_required_mode_reproves_only_the_effort(tmp_path: Path) -> None:
    configs = tmp_path / "configs.log"
    machine = _machine(allowed=True, mode="ask")
    script = _script(effort_steps=[STALE], capture=configs)
    script["post_model_options_by_value"][REQUESTED_MODEL] = _options(
        REQUESTED_MODEL, STALE_EFFORT, mode="ask"
    )
    outcome, methods = _drive(tmp_path, script, machine, prompt=True)
    assert outcome == (REQUESTED_MODEL, REQUESTED_EFFORT)
    assert _lines(configs) == ["mode=ask", f"model={REQUESTED_MODEL}", EFFORT_SET, EFFORT_SET]
    assert "session/prompt" in methods


def test_driver_stale_twice_exhausts_the_allowance_with_zero_prompt(tmp_path: Path) -> None:
    configs = tmp_path / "configs.log"
    machine = _machine(allowed=True)
    outcome, methods = _drive(
        tmp_path, _script(effort_steps=[STALE, STALE], capture=configs), machine, prompt=True
    )
    assert isinstance(outcome, ConfigFidelityError)
    assert str(outcome) == "effort readback mismatch: requested 'max', effective 'default'"
    assert _lines(configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET, EFFORT_SET]
    assert "session/prompt" not in methods


def test_driver_mode_mismatch_earns_no_reproof(tmp_path: Path) -> None:
    configs = tmp_path / "configs.log"
    machine = _machine(allowed=True, mode="ask")
    script = _script(
        effort_steps=[
            {"reply_options": [_model(REQUESTED_MODEL), _effort(STALE_EFFORT), _mode("agent")]}
        ],
        capture=configs,
    )
    script["post_model_options_by_value"][REQUESTED_MODEL] = _options(
        REQUESTED_MODEL, STALE_EFFORT, mode="ask"
    )
    outcome, methods = _drive(tmp_path, script, machine, prompt=True)
    assert isinstance(outcome, ConfigFidelityError)
    assert _lines(configs).count(EFFORT_SET) == 1
    assert "session/prompt" not in methods


@pytest.mark.parametrize(
    "steps,expected_effort_sets",
    [([{"error": "effort set refused"}], 1), ([STALE, {"error": "effort set refused"}], 2)],
    ids=["rpc-error-first-set", "rpc-error-reproof-set"],
)
def test_driver_rpc_errors_are_driver_failures_not_retry_authority(
    tmp_path: Path, steps, expected_effort_sets: int
) -> None:
    configs = tmp_path / "configs.log"
    machine = _machine(allowed=True)
    outcome, methods = _drive(
        tmp_path, _script(effort_steps=steps, capture=configs), machine, prompt=True
    )
    assert isinstance(outcome, NativeDriverError)
    assert _lines(configs).count(EFFORT_SET) == expected_effort_sets
    assert "session/prompt" not in methods


# -- RunTask: the registered profile over the fake ACP agent ----------------


def test_ordinary_create_and_load_set_effort_once_and_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_configs = tmp_path / "configs-1.log"
    harness = _harness(tmp_path, monkeypatch, _script(capture=first_configs))
    first = _run(harness.task(run_id="run-0001", request=_create()))
    assert first.status is AgentRunStatus.COMPLETED, _payload(harness, "run-0001")
    assert _lines(first_configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET]
    assert _labels(_effective(harness, "run-0001")) == ["initial", "post_model", "post_effort"]
    assert (harness.run_dir("run-0001") / DISPATCH_STARTED_MARKER).exists()

    second_configs = tmp_path / "configs-2.log"
    _set_env(monkeypatch, _script(capture=second_configs))
    second = _run(
        harness.task(run_id="run-0002", request=_reuse(first.session_id), seed_session=False)
    )
    assert second.status is AgentRunStatus.COMPLETED, _payload(harness, "run-0002")
    assert _lines(second_configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET]
    assert _labels(_effective(harness, "run-0002")) == ["initial", "post_model", "post_effort"]
    assert "session_load_requested" in _event_types(harness, "run-0002")
    assert "session_new_requested" not in _event_types(harness, "run-0002")
    assert harness.methods_seen().count("session/prompt") == 2


def test_stale_default_on_create_is_recovered_by_two_identical_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "configs.log"
    harness = _harness(tmp_path, monkeypatch, _script(effort_steps=[STALE], capture=configs))

    result = _run(harness.task(run_id="run-0001", request=_create()))

    payload = _payload(harness, "run-0001")
    assert result.status is AgentRunStatus.COMPLETED, payload
    assert payload["final_message"] == "REPROOF_AGENT_OK"
    assert _lines(configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET, EFFORT_SET]
    run_dir = harness.run_dir("run-0001")
    for marker in (CONFIG_SWITCH_STARTED_MARKER, CONFIG_PROVEN_MARKER, DISPATCH_STARTED_MARKER):
        assert (run_dir / marker).exists(), marker
    effective = _effective(harness, "run-0001")
    assert _labels(effective) == ["initial", "post_model", "post_effort", "post_effort_reproof"]
    assert _current(effective, "post_effort", "effort") == STALE_EFFORT
    assert _current(effective, "post_effort_reproof", "effort") == REQUESTED_EFFORT
    assert effective["effective_effort"] == REQUESTED_EFFORT
    record = harness.session_store().open_session(result.session_id)
    assert record.last_effective_model == REQUESTED_MODEL
    assert record.last_effective_effort == REQUESTED_EFFORT
    assert record.quarantine is None


def test_stale_default_on_load_is_recovered_without_a_new_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, _script())
    first = _run(
        harness.task(run_id="run-0001", request=_create(INITIAL_MODEL, PREVIOUS_EFFORT))
    )
    assert first.status is AgentRunStatus.COMPLETED, _payload(harness, "run-0001")

    configs = tmp_path / "configs-2.log"
    _set_env(monkeypatch, _script(effort_steps=[STALE], capture=configs))
    second = _run(
        harness.task(run_id="run-0002", request=_reuse(first.session_id), seed_session=False)
    )

    assert second.status is AgentRunStatus.COMPLETED, _payload(harness, "run-0002")
    assert _lines(configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET, EFFORT_SET]
    events = _event_types(harness, "run-0002")
    assert "session_load_requested" in events
    assert "session_new_requested" not in events
    assert harness.methods_seen().count("session/new") == 1
    effective = _effective(harness, "run-0002")
    assert _labels(effective)[-2:] == ["post_effort", "post_effort_reproof"]
    record = harness.session_store().open_session(first.session_id)
    assert record.last_effective_effort == REQUESTED_EFFORT
    assert record.quarantine is None
    assert record.native_session_epoch is None


def test_exhausted_allowance_on_create_fails_closed_and_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "configs.log"
    harness = _harness(
        tmp_path, monkeypatch, _script(effort_steps=[STALE, STALE], capture=configs)
    )

    result = _run(harness.task(run_id="run-0001", request=_create()))

    assert result.status is AgentRunStatus.FAILED
    assert _payload(harness, "run-0001")["detail_code"] == "CONFIG_FIDELITY"
    assert _lines(configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET, EFFORT_SET]
    _assert_zero_prompt(harness, "run-0001")
    run_dir = harness.run_dir("run-0001")
    assert (run_dir / CONFIG_SWITCH_STARTED_MARKER).exists()
    assert not (run_dir / CONFIG_PROVEN_MARKER).exists()
    effective = _effective(harness, "run-0001")
    assert _labels(effective)[-2:] == ["post_effort", "post_effort_reproof"]
    assert _current(effective, "post_effort", "effort") == STALE_EFFORT
    assert _current(effective, "post_effort_reproof", "effort") == STALE_EFFORT
    # A create has no previously proven pair to roll back to.
    record = harness.session_store().open_session(result.session_id)
    assert record.quarantine["reason_code"] == QUARANTINE_SWITCH_ROLLBACK_UNPROVEN
    assert "config_rollback_failed" in _event_types(harness, "run-0001")


def test_exhausted_allowance_on_load_rolls_back_strictly_and_stays_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, _script())
    first = _run(
        harness.task(run_id="run-0001", request=_create(INITIAL_MODEL, PREVIOUS_EFFORT))
    )
    assert first.status is AgentRunStatus.COMPLETED, _payload(harness, "run-0001")

    configs = tmp_path / "configs-2.log"
    _set_env(monkeypatch, _script(effort_steps=[STALE, STALE], capture=configs))
    second = _run(
        harness.task(run_id="run-0002", request=_reuse(first.session_id), seed_session=False)
    )

    assert second.status is AgentRunStatus.FAILED
    assert _payload(harness, "run-0002")["detail_code"] == "CONFIG_FIDELITY"
    assert not (harness.run_dir("run-0002") / DISPATCH_STARTED_MARKER).exists()
    # Two forward sets of the requested effort, then one strict rollback leg.
    assert _lines(configs) == [
        f"model={REQUESTED_MODEL}",
        EFFORT_SET,
        EFFORT_SET,
        f"model={INITIAL_MODEL}",
        f"effort={PREVIOUS_EFFORT}",
    ]
    assert (harness.run_dir("run-0002") / CONFIG_ROLLBACK_PROVEN_MARKER).exists()
    assert "config_rollback_proven" in _event_types(harness, "run-0002")
    record = harness.session_store().open_session(first.session_id)
    assert record.quarantine is None
    assert (record.last_effective_model, record.last_effective_effort) == (
        INITIAL_MODEL,
        PREVIOUS_EFFORT,
    )

    _set_env(monkeypatch, _script())
    third = _run(
        harness.task(
            run_id="run-0003",
            request=_reuse(first.session_id, INITIAL_MODEL, PREVIOUS_EFFORT),
            seed_session=False,
        )
    )
    assert third.status is AgentRunStatus.COMPLETED, _payload(harness, "run-0003")


def test_rollback_receives_no_allowance_and_a_stale_rollback_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, _script())
    first = _run(
        harness.task(run_id="run-0001", request=_create(INITIAL_MODEL, PREVIOUS_EFFORT))
    )
    assert first.status is AgentRunStatus.COMPLETED, _payload(harness, "run-0001")

    configs = tmp_path / "configs-2.log"
    # The third effort set is the rollback's: a stale readback there would be
    # "eligible" if the rollback machine held an allowance, and a fourth set
    # would follow. It must not.
    _set_env(
        monkeypatch,
        _script(effort_steps=[STALE, STALE, {"readback": STALE_EFFORT}], capture=configs),
    )
    second = _run(
        harness.task(run_id="run-0002", request=_reuse(first.session_id), seed_session=False)
    )

    assert second.status is AgentRunStatus.FAILED
    assert _payload(harness, "run-0002")["detail_code"] == "CONFIG_FIDELITY"
    assert _lines(configs) == [
        f"model={REQUESTED_MODEL}",
        EFFORT_SET,
        EFFORT_SET,
        f"model={INITIAL_MODEL}",
        f"effort={PREVIOUS_EFFORT}",
    ]
    _assert_zero_prompt_after_first(harness)
    assert "config_rollback_failed" in _event_types(harness, "run-0002")
    record = harness.session_store().open_session(first.session_id)
    assert record.quarantine["reason_code"] == QUARANTINE_SWITCH_ROLLBACK_UNPROVEN
    assert record.quarantine["source_run_id"] == "run-0002"

    # The quarantine is never cleared: the next Run is refused before spawn.
    later = tmp_path / "configs-3.log"
    _set_env(monkeypatch, _script(capture=later))
    third = _run(
        harness.task(run_id="run-0003", request=_reuse(first.session_id), seed_session=False)
    )
    assert third.status is AgentRunStatus.FAILED
    assert _lines(later) == []
    assert harness.session_store().open_session(first.session_id).quarantine == record.quarantine


def _assert_zero_prompt_after_first(harness: Harness) -> None:
    assert not (harness.run_dir("run-0002") / DISPATCH_STARTED_MARKER).exists()
    assert harness.methods_seen().count("session/prompt") == 1


_NO_RETRY_AUTHORITY = {
    "wrong-model": {"reply_options": [_model(INITIAL_MODEL), _effort(STALE_EFFORT)]},
    "missing-effort": {"reply_options": [_model(REQUESTED_MODEL)]},
    "non-select-effort": {"reply_options": [_model(REQUESTED_MODEL), BOOLEAN_EFFORT]},
    "unadvertised-effort": {
        "reply_options": [
            _model(REQUESTED_MODEL),
            _effort(STALE_EFFORT, values=(STALE_EFFORT, PREVIOUS_EFFORT)),
        ]
    },
    "malformed-option": {"reply_options": [_model(REQUESTED_MODEL), ID_LESS_EFFORT]},
    "null-options": {"reply_options": None},
    "rpc-error": {"error": "effort set refused"},
}


@pytest.mark.parametrize("case", sorted(_NO_RETRY_AUTHORITY))
def test_refusals_earn_no_reproof_and_never_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    configs = tmp_path / "configs.log"
    harness = _harness(
        tmp_path,
        monkeypatch,
        _script(effort_steps=[_NO_RETRY_AUTHORITY[case]], capture=configs),
    )

    result = _run(harness.task(run_id="run-0001", request=_create()))

    assert result.status is AgentRunStatus.FAILED, case
    assert _payload(harness, "run-0001")["detail_code"] == "CONFIG_FIDELITY", case
    assert _lines(configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET], case
    _assert_zero_prompt(harness, "run-0001")
    labels = _labels(_effective(harness, "run-0001"))
    assert "post_effort_reproof" not in labels, case


def test_rpc_error_on_the_reproof_set_fails_with_zero_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "configs.log"
    harness = _harness(
        tmp_path,
        monkeypatch,
        _script(effort_steps=[STALE, {"error": "effort set refused"}], capture=configs),
    )

    result = _run(harness.task(run_id="run-0001", request=_create()))

    assert result.status is AgentRunStatus.FAILED
    assert _payload(harness, "run-0001")["detail_code"] == "CONFIG_FIDELITY"
    assert _lines(configs) == [f"model={REQUESTED_MODEL}", EFFORT_SET, EFFORT_SET]
    _assert_zero_prompt(harness, "run-0001")
    labels = _labels(_effective(harness, "run-0001"))
    assert labels[-1] == "post_effort"


@pytest.mark.parametrize("profile_id", [STANDARD_PROFILE, CODEX_PROFILE, REASONIX_PROFILE])
def test_other_profiles_refuse_the_same_stale_agent_exactly_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile_id: str
) -> None:
    """Opt-out is the status quo: one effort set, then ``CONFIG_FIDELITY``."""
    configs = tmp_path / "configs.log"
    harness = _harness(
        tmp_path,
        monkeypatch,
        _script(effort_steps=[STALE], capture=configs),
        profile_id=profile_id,
    )

    result = _run(harness.task(run_id="run-0001", request=_create()))

    assert result.status is AgentRunStatus.FAILED, profile_id
    assert _payload(harness, "run-0001")["detail_code"] == "CONFIG_FIDELITY"
    assert _lines(configs).count(EFFORT_SET) == 1, profile_id
    _assert_zero_prompt(harness, "run-0001")
    labels = _labels(_effective(harness, "run-0001"))
    assert labels[-1] == "post_effort"
    assert "post_effort_reproof" not in labels

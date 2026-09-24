"""Parameterized configuration fidelity and the registered Cursor profile.

``cursor-native-acp-v1`` revision 4 negotiates
``clientCapabilities._meta.parameterizedModelPicker = true`` on ``initialize``.
With it, the agent advertises a base-model ``model`` selector plus independent,
model-dependent parameter selectors; without it, only a variants catalog.

The request keeps its existing shape: one model literal and the ``N/A`` effort.
Under parameterized fidelity the literal is ``base[id=value,...]`` — the whole
configuration. ARS sets ``base`` on the model selector, consumes the complete
post-set-model set, requires the request to name exactly the parameters that set
advertises, sets each one on its own advertised selector, and releases the
prompt only after a final whole-configuration readback. The literal itself is
never sent, and nothing in source knows a parameter id, value, or domain: the
ids and values below are fixture data shaped like the cited evidence.

Covered here: the grammar, the machine, the profile identity, and — over a real
stdio ACP child — ``session/new``, real ``session/load`` of the same external
Session, cross-Run parameter and base-model switching, and partial-setting
failure with exact rollback, unprovable rollback, and the create-path window.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import launch_permissions as lp
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.config_fidelity import (
    EFFORT_NOT_APPLICABLE,
    FIDELITY_PARAMETERIZED,
    ConfigFidelityError,
    ConfigFidelityMachine,
    compose_parameterized_model,
    parse_parameterized_model,
)
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_COMPAT_V1,
    CODEX_AGENT_ACP_COMPAT_V1,
    CURSOR_NATIVE_ACP_V1,
    REASONIX_AGENT_ACP_COMPAT_V1,
    STANDARD_NATIVE_ACP_V1,
    AcpCompatProfile,
    AgentInstance,
    ProfileRegistry,
    ProfileValidationError,
)
from agent_run_supervisor.native_acp.run_task import (
    CONFIG_PROVEN_MARKER,
    CONFIG_ROLLBACK_PROVEN_MARKER,
    DISPATCH_STARTED_MARKER,
)

from .test_run_task import FAKE_AGENT_PATH, HAPPY_SCRIPT, Harness, _request, _run

CURSOR_AGENT_ID = "cursor-registered"

BASE = "grok-4.7"
OTHER_BASE = "sonnet-9"
REQUESTED = "grok-4.7[context=500k,reasoning_effort=xhigh,fast=false]"
SWITCHED = "grok-4.7[context=256k,reasoning_effort=xhigh,fast=true]"
OTHER_REQUESTED = "sonnet-9[thinking=true]"
# What the same agent advertises when the negotiation is absent.
VARIANT = "grok-4.5[effort=high,fast=true]"

REQUESTED_SETS = [
    f"model={BASE}",
    "context=500k",
    "reasoning_effort=xhigh",
    "fast=false",
]
SWITCHED_SETS = [
    f"model={BASE}",
    "context=256k",
    "reasoning_effort=xhigh",
    "fast=true",
]

CURSOR_R3_HASH = "9ec329a6ac5844ea9df789344fbaeeab7ec2cca7b704da66f470a118a68063e4"
CURSOR_R4_HASH = "cfcae1463d8e5487bd9e3fef512e5c19a16baf26db16f34ffe4323a7dd1f2395"

RUN_ONE_NONCE = "PARAMETERIZED-CONTINUITY-NONCE-7d20"
RUN_TWO_PROMPT = "repeat the token from earlier in this conversation"


# -- fixture option sets -----------------------------------------------------


def _select(option_id: str, current: str, values, category: str) -> dict:
    return {
        "id": option_id,
        "name": option_id,
        "category": category,
        "type": "select",
        "currentValue": current,
        "options": [{"value": value, "name": value} for value in values],
    }


def _mode(current: str) -> dict:
    return _select("mode", current, ("agent", "plan", "ask"), "mode")


def _model(current: str) -> dict:
    return _select("model", current, (BASE, OTHER_BASE), "model")


def _grok_parameters(context="256k", effort="high", fast="true") -> list[dict]:
    """Defaults deliberately differ from every request, so each leg moves."""
    return [
        _select("context", context, ("256k", "500k"), "model_config"),
        _select(
            "reasoning_effort",
            effort,
            ("low", "medium", "high", "xhigh"),
            "thought_level",
        ),
        _select("fast", fast, ("false", "true"), "model_config"),
    ]


def _grok_set(mode: str, **parameters) -> list[dict]:
    return [_mode(mode), _model(BASE), *_grok_parameters(**parameters)]


def _sonnet_set(mode: str, thinking: str = "false") -> list[dict]:
    return [
        _mode(mode),
        _model(OTHER_BASE),
        _select("thinking", thinking, ("false", "true"), "thought_level"),
    ]


def _script(required_mode: str = "ask", initial_mode: str = "agent") -> dict:
    """One agent, two catalogs: which one it serves is decided by ``initialize``.

    Setting a model swaps to that model's own set at its default parameter
    values, which is how the evidenced agent resets parameters on a model set.
    """
    variants_model = _select("model", VARIANT, (VARIANT,), "model")
    return {
        "initial_options": [_mode(initial_mode), variants_model],
        "post_model_options_by_value": {
            VARIANT: [_mode(required_mode), variants_model]
        },
        "parameterized_picker": {
            "initial_options": _grok_set(initial_mode),
            "post_model_options_by_value": {
                BASE: _grok_set(required_mode),
                OTHER_BASE: _sonnet_set(required_mode),
            },
        },
        "final_message": "CURSOR_OK",
    }


def _entry(**overrides) -> AgentEntry:
    kwargs = dict(
        agent_id=CURSOR_AGENT_ID,
        profile_id=CURSOR_NATIVE_ACP_V1.profile_id,
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
        env_passthrough=("FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
    )
    kwargs.update(overrides)
    return AgentEntry(**kwargs)


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: dict) -> Harness:
    """The **registered** profile, exactly as a deployment resolves it."""
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((CURSOR_NATIVE_ACP_V1,))
    harness.entry = _entry()
    return harness


def _continuity_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kw):
    """An agent that keeps Session state in the operator's own stable home."""
    home = tmp_path / "operator-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    script = _script(**kw)
    script["config_home_env"] = [lp.CURSOR_CONFIG_DIR_ENV, "HOME"]
    script["nonce_memory"] = True
    return _harness(tmp_path, monkeypatch, script), script


def _create(requested_model: str = REQUESTED, **overrides):
    kwargs = dict(
        agent_id=CURSOR_AGENT_ID,
        session_id=None,
        requested_model=requested_model,
        requested_effort=EFFORT_NOT_APPLICABLE,
    )
    kwargs.update(overrides)
    return _request(**kwargs)


def _reuse(session_id: str, requested_model: str = REQUESTED, **overrides):
    return _create(requested_model, session_id=session_id, **overrides)


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _json(run_dir: Path, name: str) -> dict:
    return json.loads((run_dir / name).read_text())


def _events(run_dir: Path) -> list[str]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line).get("type")
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _capture(script: dict, tmp_path: Path) -> Path:
    configs = tmp_path / "fake-agent-config.log"
    script["capture_config_path"] = str(configs)
    return configs


# -- the literal grammar -----------------------------------------------------


@pytest.mark.parametrize(
    ("literal", "base", "parameters"),
    [
        (REQUESTED, BASE, (("context", "500k"), ("reasoning_effort", "xhigh"), ("fast", "false"))),
        (BASE, BASE, ()),
        ("m[a=b]", "m", (("a", "b"),)),
        # No trimming or normalization: a space is part of the value.
        ("m[a= b]", "m", (("a", " b"),)),
    ],
)
def test_a_literal_splits_into_its_parts_and_recomposes_byte_for_byte(
    literal, base, parameters
) -> None:
    parsed = parse_parameterized_model(literal)
    assert parsed.base == base
    assert parsed.parameters == parameters
    assert compose_parameterized_model(parsed.base, parsed.parameters) == literal


@pytest.mark.parametrize(
    "literal",
    [
        "",
        "m[]",
        "m[a=b",
        "[a=b]",
        "m[a=b]]",
        "m[a=[b]]",
        "m[a]",
        "m[=b]",
        "m[a=]",
        "m[a=b=c]",
        "m[a=b,]",
        "m[a=b,a=c]",
        "m]x",
    ],
)
def test_a_malformed_literal_is_refused(literal: str) -> None:
    with pytest.raises(ConfigFidelityError):
        parse_parameterized_model(literal)


# -- the machine -------------------------------------------------------------


def _machine(requested: str = REQUESTED, **overrides) -> ConfigFidelityMachine:
    """Exactly the machine RunTask builds for a read-only-grant Cursor Run."""
    kwargs = dict(
        model_selector_id="model",
        effort_selector_id=None,
        requested_model=requested,
        requested_effort=EFFORT_NOT_APPLICABLE,
        fidelity_mode=FIDELITY_PARAMETERIZED,
        permission_mode_selector_id="mode",
        required_permission_mode="ask",
    )
    kwargs.update(overrides)
    return ConfigFidelityMachine(**kwargs)


def _through_model(machine: ConfigFidelityMachine, post_model: list[dict]) -> None:
    machine.record_initial_options(_grok_set("agent"))
    assert machine.permission_mode_plan() == "mode"
    machine.record_post_mode_options(_grok_set("ask"))
    assert machine.model_plan() == "model"
    assert machine.model_selector_value == BASE
    machine.record_post_model_options(post_model)


def test_the_machine_sets_each_parameter_then_proves_the_whole_configuration() -> None:
    machine = _machine()
    _through_model(machine, _grok_set("ask"))
    legs = []
    current = {"context": "256k", "effort": "high", "fast": "true"}
    while machine.has_pending_parameter:
        config_id, value = machine.parameter_plan()
        legs.append((config_id, value))
        current["effort" if config_id == "reasoning_effort" else config_id] = value
        machine.record_post_parameter_options(_grok_set("ask", **current))
    assert legs == [("context", "500k"), ("reasoning_effort", "xhigh"), ("fast", "false")]
    assert machine.phase == "verified"
    assert machine.require_ready() == (REQUESTED, EFFORT_NOT_APPLICABLE)
    assert [label for label, _ in machine.snapshots] == [
        "initial",
        "post_mode",
        "post_model",
        "post_parameter",
        "post_parameter",
        "post_parameter",
    ]


def test_a_bare_base_with_no_advertised_parameter_verifies_at_the_model_readback() -> None:
    machine = _machine(BASE)
    _through_model(machine, [_mode("ask"), _model(BASE)])
    assert not machine.has_pending_parameter
    assert machine.require_ready() == (BASE, EFFORT_NOT_APPLICABLE)


def test_an_advertised_parameter_the_request_omits_refuses_at_the_model_readback() -> None:
    """Omitting ``fast`` would run at an unproven agent default."""
    machine = _machine("grok-4.7[context=500k,reasoning_effort=xhigh]")
    with pytest.raises(ConfigFidelityError, match="not requested"):
        _through_model(machine, _grok_set("ask"))
    with pytest.raises(ConfigFidelityError):
        machine.parameter_plan()
    # The refused Run still keeps the set the agent actually advertised.
    assert [label for label, _ in machine.snapshots][-1] == "post_model"


def test_a_requested_parameter_the_model_does_not_advertise_refuses() -> None:
    machine = _machine("grok-4.7[context=500k,reasoning_effort=xhigh,fast=false,verbosity=low]")
    with pytest.raises(ConfigFidelityError, match="not advertised"):
        _through_model(machine, _grok_set("ask"))


def test_an_unadvertised_parameter_value_refuses_before_its_set() -> None:
    machine = _machine("grok-4.7[context=1m,reasoning_effort=xhigh,fast=false]")
    _through_model(machine, _grok_set("ask"))
    with pytest.raises(ConfigFidelityError, match="not advertised"):
        machine.parameter_plan()
    with pytest.raises(ConfigFidelityError):
        machine.require_ready()


def test_an_unadvertised_base_model_refuses_before_its_set() -> None:
    machine = _machine("grok-9[context=500k]")
    machine.record_initial_options(_grok_set("agent"))
    machine.permission_mode_plan()
    machine.record_post_mode_options(_grok_set("ask"))
    with pytest.raises(ConfigFidelityError, match="not advertised"):
        machine.model_plan()


def test_a_parameter_readback_mismatch_never_verifies() -> None:
    machine = _machine()
    _through_model(machine, _grok_set("ask"))
    machine.parameter_plan()
    with pytest.raises(ConfigFidelityError, match="readback mismatch"):
        machine.record_post_parameter_options(_grok_set("ask"))  # context stays 256k
    with pytest.raises(ConfigFidelityError):
        machine.require_ready()


@pytest.mark.parametrize(
    ("label", "final_set"),
    [
        ("an earlier parameter silently moved", _grok_set("ask", context="256k", effort="xhigh", fast="false")),
        ("the base model moved", [_mode("ask"), _model(OTHER_BASE), *_grok_parameters("500k", "xhigh", "false")]),
        ("the permission mode moved", _grok_set("agent", context="500k", effort="xhigh", fast="false")),
        ("a new parameter appeared", _grok_set("ask", context="500k", effort="xhigh", fast="false") + [_select("verbosity", "low", ("low", "high"), "model_config")]),
    ],
)
def test_the_final_whole_configuration_readback_refuses_any_drift(
    label: str, final_set: list[dict]
) -> None:
    machine = _machine()
    _through_model(machine, _grok_set("ask"))
    machine.parameter_plan()
    machine.record_post_parameter_options(_grok_set("ask", context="500k"))
    machine.parameter_plan()
    machine.record_post_parameter_options(
        _grok_set("ask", context="500k", effort="xhigh")
    )
    machine.parameter_plan()
    with pytest.raises(ConfigFidelityError):
        machine.record_post_parameter_options(final_set)
    with pytest.raises(ConfigFidelityError):
        machine.require_ready()


@pytest.mark.parametrize("owned", ["model", "mode"])
def test_a_parameter_may_not_name_a_selector_the_machine_owns(owned: str) -> None:
    """The mode is grant-derived and profile-owned; no request may set it."""
    with pytest.raises(ConfigFidelityError):
        _machine(f"grok-4.7[{owned}=agent]")


def test_parameterized_requires_the_not_applicable_effort_and_no_effort_leg() -> None:
    with pytest.raises(ConfigFidelityError):
        _machine(requested_effort="xhigh")
    with pytest.raises(ConfigFidelityError):
        _machine(effort_selector_id="reasoning_effort")
    machine = _machine()
    _through_model(machine, _grok_set("ask"))
    with pytest.raises(ConfigFidelityError):
        machine.effort_plan()


# -- the registered profile --------------------------------------------------


def test_cursor_revision_4_negotiates_the_picker_and_declares_parameterized() -> None:
    profile = CURSOR_NATIVE_ACP_V1
    assert profile.revision == 4
    assert profile.config_fidelity_mode == FIDELITY_PARAMETERIZED
    assert profile.effort_selector_id is None
    assert profile.client_capabilities_meta_payload() == {
        "parameterizedModelPicker": True
    }
    # A fresh copy per call: no Run can mutate what the next one sends.
    profile.client_capabilities_meta_payload()["parameterizedModelPicker"] = False
    assert profile.client_capabilities_meta_payload() == {
        "parameterizedModelPicker": True
    }


def test_the_cursor_profile_deviates_only_in_its_proven_terms() -> None:
    standard = STANDARD_NATIVE_ACP_V1.snapshot()
    cursor = CURSOR_NATIVE_ACP_V1.snapshot()
    for shared in (
        "acp_protocol_version",
        "required_capabilities",
        "forbidden_capabilities",
        "requires_session_load",
        "base_allowlist",
        "model_selector_id",
    ):
        assert cursor[shared] == standard[shared], shared
    assert set(cursor) - set(standard) == {
        "config_fidelity_mode",
        "permission_mode_selector_id",
        "permission_mode_policy_id",
        "client_capabilities_meta",
    }
    assert cursor["effort_selector_id"] is None
    assert cursor["config_fidelity_mode"] == FIDELITY_PARAMETERIZED
    assert cursor["client_capabilities_meta"] == {"parameterizedModelPicker": True}


def test_only_the_cursor_profile_identity_moved() -> None:
    """``profile_hash`` is Session identity; only Cursor's may move.

    Revision-3 Sessions recorded a variants literal this revision cannot
    restore, so they are refused by the ordinary profile-binding mismatch —
    deliberately, with no compatibility or migration logic.
    """
    assert {
        profile.profile_id: profile.profile_hash()
        for profile in (
            STANDARD_NATIVE_ACP_V1,
            CLAUDE_AGENT_ACP_COMPAT_V1,
            CODEX_AGENT_ACP_COMPAT_V1,
            REASONIX_AGENT_ACP_COMPAT_V1,
        )
    } == {
        "standard-native-acp-v1": "fcf4d46c2c072ba9bd23b198beb096cb9748e62e8168c2a48e5c76432d55f9b9",
        "claude-agent-acp-compat-v1": "c9e9258bfcc01e2962b87466c803d0a3ae25a1676936864bdbd78b75a544a241",
        "codex-agent-acp-compat-v1": "de3c26137e30319336c271710d47e235fd895ce43253c364782f6b007900b309",
        "reasonix-agent-acp-compat-v1": "f4ec5820964391fe6e8bd269bbbf5cef553f842a03f71165745de27bb500ff68",
    }
    assert CURSOR_NATIVE_ACP_V1.profile_hash() == CURSOR_R4_HASH
    assert CURSOR_R4_HASH != CURSOR_R3_HASH
    for profile in (STANDARD_NATIVE_ACP_V1, CLAUDE_AGENT_ACP_COMPAT_V1):
        assert "client_capabilities_meta" not in profile.snapshot()
        assert profile.client_capabilities_meta_payload() is None


@pytest.mark.parametrize("meta", ['{"b":1, "a":2}', "[]", "null", "", "{not json"])
def test_client_capabilities_meta_must_be_canonical_json_object_text(meta: str) -> None:
    with pytest.raises(ProfileValidationError):
        AcpCompatProfile(
            profile_id="meta-check-v1",
            revision=1,
            acp_protocol_version="1",
            client_capabilities_meta=meta,
        )


def test_an_entry_may_not_hint_an_effort_selector_on_a_parameterized_profile() -> None:
    with pytest.raises(ProfileValidationError):
        AgentInstance(
            profile=CURSOR_NATIVE_ACP_V1,
            entry=_entry(effort_selector_id="reasoning_effort"),
        )
    instance = AgentInstance(profile=CURSOR_NATIVE_ACP_V1, entry=_entry())
    assert instance.effort_selector_id is None


# -- session/new over a real ACP child ---------------------------------------


def test_a_new_session_negotiates_the_picker_and_proves_every_parameter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    configs = _capture(script, tmp_path)
    capabilities = tmp_path / "client-capabilities.jsonl"
    script["capture_client_capabilities_path"] = str(capabilities)
    harness = _harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_create()))

    run_dir = harness.run_dir()
    assert result.status is AgentRunStatus.COMPLETED, _json(run_dir, "result.json")
    # The negotiation rides the grant-derived capabilities (as the locked SDK
    # serializes them, defaults omitted); it widens nothing.
    assert [json.loads(line) for line in _lines(capabilities)] == [
        {
            "fs": {"readTextFile": True},
            "auth": {},
            "_meta": {"parameterizedModelPicker": True},
        }
    ]
    # Mode first, then the base model, then one set per parameter, in request
    # order. The literal itself never reaches the wire.
    assert _lines(configs) == ["mode=ask", *REQUESTED_SETS]
    assert f"model={REQUESTED}" not in _lines(configs)
    methods = harness.methods_seen()
    assert methods.count("session/set_config_option") == 5
    assert methods.index("session/prompt") > max(
        index for index, name in enumerate(methods) if name == "session/set_config_option"
    )
    # Requested, sealed, and observed evidence agree.
    spec = _json(run_dir, "spec.json")
    assert spec["runtime"]["model_id"] == REQUESTED
    assert spec["runtime"]["effort"] == EFFORT_NOT_APPLICABLE
    launch = _json(run_dir, "launch.json")
    assert launch["model_selector_id"] == "model"
    assert launch["effort_selector_id"] is None
    effective = _json(run_dir, "effective.json")
    assert effective["effective_model"] == REQUESTED
    assert effective["effective_effort"] == EFFORT_NOT_APPLICABLE
    assert [row["label"] for row in effective["discovery_snapshots"]] == [
        "initial",
        "post_mode",
        "post_model",
        "post_parameter",
        "post_parameter",
        "post_parameter",
    ]
    final = {
        option["id"]: option["currentValue"]
        for option in effective["discovery_snapshots"][-1]["options"]
    }
    assert final == {
        "mode": "ask",
        "model": BASE,
        "context": "500k",
        "reasoning_effort": "xhigh",
        "fast": "false",
    }
    assert (run_dir / CONFIG_PROVEN_MARKER).exists()
    record = harness.session_store().open_session(result.session_id)
    assert record.last_effective_model == REQUESTED
    assert record.last_effective_effort == EFFORT_NOT_APPLICABLE


def test_other_profiles_keep_byte_identical_initialize_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = tmp_path / "client-capabilities.jsonl"
    script = dict(HAPPY_SCRIPT)
    script["capture_client_capabilities_path"] = str(capabilities)
    harness = Harness(tmp_path, monkeypatch, script)

    assert _run(harness.task()).status is AgentRunStatus.COMPLETED
    (captured,) = [json.loads(line) for line in _lines(capabilities)]
    assert "_meta" not in captured


def test_an_agent_that_ignores_the_negotiation_is_never_configured_from_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No silent fallback: the variants catalog never satisfies the request."""
    script = _script()
    del script["parameterized_picker"]
    configs = _capture(script, tmp_path)
    harness = _harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_create()))

    assert result.status is AgentRunStatus.FAILED
    assert _json(harness.run_dir(), "result.json")["detail_code"] == "CONFIG_FIDELITY"
    assert _lines(configs) == ["mode=ask"]
    assert "session/prompt" not in harness.methods_seen()
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists()


def test_a_request_omitting_an_advertised_parameter_sets_no_parameter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    configs = _capture(script, tmp_path)
    harness = _harness(tmp_path, monkeypatch, script)

    result = _run(
        harness.task(request=_create("grok-4.7[context=500k,reasoning_effort=xhigh]"))
    )

    assert result.status is AgentRunStatus.FAILED
    assert _json(harness.run_dir(), "result.json")["detail_code"] == "CONFIG_FIDELITY"
    assert _lines(configs) == ["mode=ask", f"model={BASE}"]
    assert "session/prompt" not in harness.methods_seen()


def test_a_malformed_literal_refuses_before_any_acp_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, _script())

    result = _run(harness.task(request=_create("grok-4.7[context=500k")))

    assert result.status is AgentRunStatus.FAILED
    assert _json(harness.run_dir(), "result.json")["detail_code"] == "CONFIG_FIDELITY"
    assert harness.methods_seen() == []
    assert result.session_id is None or not (
        Path(harness.session_store().base_dir) / str(result.session_id)
    ).exists()


def test_a_create_path_partial_setting_failure_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A create has no previously proven configuration to roll back to."""
    script = _script()
    script["reject_set_config_values"] = ["xhigh"]
    configs = _capture(script, tmp_path)
    harness = _harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_create()))

    run_dir = harness.run_dir()
    assert result.status is AgentRunStatus.FAILED
    assert _json(run_dir, "result.json")["detail_code"] == "CONFIG_FIDELITY"
    assert _lines(configs) == ["mode=ask", f"model={BASE}", "context=500k", "reasoning_effort=xhigh"]
    assert "session/prompt" not in harness.methods_seen()
    assert "config_rollback_failed" in _events(run_dir)
    assert harness.session_store().open_session(result.session_id).quarantine is not None


# -- session/load: same external Session, restoration, switching -------------


def _first_run(harness: Harness):
    first = _run(
        harness.task(run_id="run-0001", prompt_text=RUN_ONE_NONCE, request=_create())
    )
    assert first.status is AgentRunStatus.COMPLETED, _json(
        harness.run_dir("run-0001"), "result.json"
    )
    return first


def _second_run(harness: Harness, session_id: str, requested_model: str):
    return _run(
        harness.task(
            run_id="run-0002",
            prompt_text=RUN_TWO_PROMPT,
            request=_reuse(session_id, requested_model),
            seed_session=False,
        )
    )


@pytest.mark.parametrize(
    ("requested", "expected_sets"),
    [
        (REQUESTED, REQUESTED_SETS),
        (SWITCHED, SWITCHED_SETS),
        (OTHER_REQUESTED, [f"model={OTHER_BASE}", "thinking=true"]),
    ],
    ids=("restore", "switch-parameters", "switch-base-model"),
)
def test_a_reuse_run_loads_the_same_external_session_and_proves_its_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
    expected_sets: list[str],
) -> None:
    """Run 2 is a new process on a real ``session/load`` of Run 1's Session.

    The agent answers Run 2 out of the Session state it kept itself, which it
    can only find under the external id Run 1 minted — so the load carried
    that id unchanged. Every Run re-sets and re-proves the whole configuration,
    whether it restores Run 1's or switches parameters or base model.
    """
    harness, script = _continuity_harness(tmp_path, monkeypatch)
    configs = _capture(script, tmp_path)
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
    first = _first_run(harness)

    second = _second_run(harness, first.session_id, requested)

    run_dir = harness.run_dir("run-0002")
    payload = _json(run_dir, "result.json")
    assert second.status is AgentRunStatus.COMPLETED, payload
    assert RUN_ONE_NONCE in payload["final_message"]
    families = _events(run_dir)
    assert "session_load_requested" in families
    assert "session_new_requested" not in families
    assert _lines(configs) == [
        "mode=ask",
        *REQUESTED_SETS,
        "mode=ask",
        *expected_sets,
    ]
    assert _json(run_dir, "effective.json")["effective_model"] == requested
    record = harness.session_store().open_session(first.session_id)
    assert second.session_id == first.session_id
    assert record.agent_session_id == harness.external_id
    assert record.last_effective_model == requested
    assert record.last_effective_effort == EFFORT_NOT_APPLICABLE
    assert record.quarantine is None


def test_a_partial_parameter_switch_rolls_back_to_the_proven_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, script = _continuity_harness(tmp_path, monkeypatch)
    configs = _capture(script, tmp_path)
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
    first = _first_run(harness)

    # Run 2's last leg is rejected after two parameters already moved.
    switch = dict(script, reject_set_config_values=["true"])
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(switch))
    second = _second_run(harness, first.session_id, SWITCHED)

    run_dir = harness.run_dir("run-0002")
    assert second.status is AgentRunStatus.FAILED
    assert _json(run_dir, "result.json")["detail_code"] == "CONFIG_FIDELITY"
    events = _events(run_dir)
    assert "config_rollback_started" in events
    assert "config_rollback_proven" in events
    assert "config_rollback_failed" not in events
    assert (run_dir / CONFIG_ROLLBACK_PROVEN_MARKER).exists()
    assert not (run_dir / DISPATCH_STARTED_MARKER).exists()
    # The forward switch, then the rollback re-running the whole sequence for
    # Run 1's proven literal.
    assert _lines(configs) == [
        "mode=ask",
        *REQUESTED_SETS,
        "mode=ask",
        *SWITCHED_SETS,
        "mode=ask",
        *REQUESTED_SETS,
    ]
    assert harness.methods_seen().count("session/prompt") == 1
    record = harness.session_store().open_session(first.session_id)
    assert record.quarantine is None
    assert record.last_effective_model == REQUESTED

    # Reusable in fact: Run 3 loads the same Session and proves Run 1's literal.
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
    third = _run(
        harness.task(
            run_id="run-0003",
            request=_reuse(first.session_id, REQUESTED),
            seed_session=False,
        )
    )
    assert third.status is AgentRunStatus.COMPLETED
    assert _lines(configs)[-5:] == ["mode=ask", *REQUESTED_SETS]


def test_an_unprovable_rollback_quarantines_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, script = _continuity_harness(tmp_path, monkeypatch)
    configs = _capture(script, tmp_path)
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
    first = _first_run(harness)

    # The forward context set reads back as asked, then the last leg is
    # rejected; the rollback's context set then reads back the wrong value.
    switch = dict(
        script,
        reject_set_config_values=["true"],
        wrong_readback={"context": "256k"},
    )
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(switch))
    second = _second_run(harness, first.session_id, SWITCHED)

    run_dir = harness.run_dir("run-0002")
    assert second.status is AgentRunStatus.FAILED
    assert _json(run_dir, "result.json")["detail_code"] == "CONFIG_FIDELITY"
    assert "config_rollback_failed" in _events(run_dir)
    assert not (run_dir / CONFIG_ROLLBACK_PROVEN_MARKER).exists()
    assert not (run_dir / DISPATCH_STARTED_MARKER).exists()
    assert _lines(configs) == [
        "mode=ask",
        *REQUESTED_SETS,
        "mode=ask",
        *SWITCHED_SETS,
        "mode=ask",
        f"model={BASE}",
        "context=500k",
    ]
    record = harness.session_store().open_session(first.session_id)
    assert record.quarantine is not None
    assert record.last_effective_model == REQUESTED

"""Grant-driven Cursor permission mode: ``ask`` for read-only grants, else ``agent``.

The registered ``cursor-native-acp-v1`` (revision 3) declares Cursor's ACP
``mode`` selector together with one closed, source-owned, grant-driven
permission-mode policy: a Run whose frozen ``grant_capabilities`` are exactly a
subset of ``{read, search}`` requires mode ``ask``, and every other valid grant
requires mode ``agent``. The profile owns the closed policy; the Run's immutable
grant supplies the per-Run input; no generic runtime path branches on an agent
name.

Sequencing is the existing mode leg of the exact-fidelity machine: mode is set
and exact-read-back **before** the model, the mode is re-proven **after** the
model set, and any missing selector, unavailable target, rejected set, wrong
readback, or post-model drift fails pre-Prompt as ``CONFIG_FIDELITY`` with zero
prompt. The required mode is recomputed from the frozen grant on every Run,
including real ``session/load`` reuse Runs.

Honest scope, pinned in the authority docs this change touches: ``ask`` is a
**cooperative** agent-side mitigation — not an OS sandbox, not a strong
hostile-agent boundary. ACP permission mediation and the post-completion
violation detector are unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.config_fidelity import (
    EFFORT_NOT_APPLICABLE,
    ConfigFidelityError,
    ConfigFidelityMachine,
)
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_COMPAT_V1,
    CURSOR_NATIVE_ACP_V1,
    STANDARD_NATIVE_ACP_V1,
    ProfileRegistry,
    ProfileValidationError,
)
from agent_run_supervisor.native_acp.run_task import DISPATCH_STARTED_MARKER

from .test_model_only_fidelity import (
    CURSOR_MODEL,
    CURSOR_MODEL_OTHER,
    _lines,
    _model_only_profile,
)
from .test_run_task import FAKE_AGENT_PATH, Harness, _request, _run

REGISTERED_PROFILE_ID = "cursor-native-acp-v1"
CURSOR_AGENT_ID = "cursor-registered"

READ_ONLY_ASK_POLICY_ID = "read-only-grant-ask-else-agent-v1"

# Captured from ``main`` before this change. The first two must not move; the
# third is the revision-2 identity this change deliberately retires.
STANDARD_HASH = "fcf4d46c2c072ba9bd23b198beb096cb9748e62e8168c2a48e5c76432d55f9b9"
CLAUDE_HASH = "c9e9258bfcc01e2962b87466c803d0a3ae25a1676936864bdbd78b75a544a241"
CURSOR_REVISION_2_HASH = (
    "fcde03cc3414e3372343b48ca82cdfd528199f6db7b31afce9630aa8a248d9bd"
)


def _required_mode_for(profile, capabilities):
    """The per-Run required mode, asked of the profile.

    ``getattr`` keeps this module importable against the pre-change source, so
    every test here fails on its own subject rather than at collection.
    """
    accessor = getattr(profile, "required_permission_mode_for", None)
    assert accessor is not None, (
        "profiles must answer required_permission_mode_for(grant_capabilities)"
    )
    return accessor(capabilities)


def _options(mode_current: str, model_current: str = CURSOR_MODEL_OTHER):
    """A Cursor-shaped option set: one model selector, one ``mode`` selector."""
    return [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": model_current,
            "options": [
                {"value": CURSOR_MODEL_OTHER, "name": "Grok 4.5 (low)"},
                {"value": CURSOR_MODEL, "name": "Grok 4.5 (high, fast)"},
            ],
        },
        {
            "id": "mode",
            "name": "Mode",
            "type": "select",
            "currentValue": mode_current,
            "options": [
                {"value": "ask", "name": "Ask"},
                {"value": "agent", "name": "Agent"},
            ],
        },
    ]


def _script_for(required_mode: str, *, initial_mode: str) -> dict:
    """A Cursor-shaped agent whose ambient initial mode is not trusted.

    ``initial_mode`` deliberately differs from the required mode in the happy
    paths, so the ``ask``/``agent`` line in the config capture is a real switch
    with a real readback — never the agent's own default passing by accident.
    The post-model set keeps the required mode current, modelling a model
    switch with no mode side effect.
    """
    return {
        "initial_options": _options(initial_mode),
        "post_model_options_by_value": {CURSOR_MODEL: _options(required_mode)},
        "final_message": "CURSOR_OK",
    }


def _entry(**overrides) -> AgentEntry:
    kwargs = dict(
        agent_id=CURSOR_AGENT_ID,
        profile_id=REGISTERED_PROFILE_ID,
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
        env_passthrough=("FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
    )
    kwargs.update(overrides)
    return AgentEntry(**kwargs)


def _registered_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: dict
) -> Harness:
    """The **registered** profile, exactly as a deployment resolves it."""
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((CURSOR_NATIVE_ACP_V1,))
    harness.entry = _entry()
    return harness


def _create_request(grant=("read",), **overrides):
    kwargs = dict(
        agent_id=CURSOR_AGENT_ID,
        session_id=None,
        requested_model=CURSOR_MODEL,
        requested_effort=EFFORT_NOT_APPLICABLE,
        grant_capabilities=tuple(grant),
    )
    kwargs.update(overrides)
    return _request(**kwargs)


def _reuse_request(session_id: str, grant=("read",), **overrides):
    return _create_request(grant=grant, session_id=session_id, **overrides)


def _result_payload(harness: Harness, run_id: str = "run-0001") -> dict:
    return json.loads((harness.run_dir(run_id) / "result.json").read_text())


def _effective(harness: Harness, run_id: str = "run-0001") -> dict:
    return json.loads((harness.run_dir(run_id) / "effective.json").read_text())


def _snapshot_mode(effective: dict, label: str) -> str | None:
    rows = [
        row for row in effective["discovery_snapshots"] if row["label"] == label
    ]
    assert rows, f"missing discovery snapshot {label!r}"
    by_id = {option["id"]: option for option in rows[0]["options"]}
    mode = by_id.get("mode")
    return None if mode is None else mode.get("currentValue")


def _events(harness: Harness, run_id: str = "run-0001") -> list[str]:
    path = harness.run_dir(run_id) / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line).get("type")
        for line in path.read_text().splitlines()
        if line.strip()
    ]


# -- the profile contract: revision 3 owns the closed policy -----------------


def test_cursor_revision_3_declares_the_grant_driven_mode_policy() -> None:
    profile = CURSOR_NATIVE_ACP_V1
    assert profile.revision == 3
    assert profile.permission_mode_selector_id == "mode"
    # Grant-driven, not static: the per-Run required mode is computed from the
    # frozen grant, so no single literal may be frozen here.
    assert profile.required_permission_mode is None
    assert getattr(profile, "permission_mode_policy_id", None) == (
        READ_ONLY_ASK_POLICY_ID
    )
    snapshot = profile.snapshot()
    assert snapshot["permission_mode_selector_id"] == "mode"
    assert snapshot["permission_mode_policy_id"] == READ_ONLY_ASK_POLICY_ID
    # A key for a value that does not exist would be a fiction in identity
    # material — and Claude's static emission must stay byte-identical.
    assert "required_permission_mode" not in snapshot


def test_only_the_cursor_profile_identity_moved() -> None:
    """``profile_hash`` is Session identity; only Cursor's may move."""
    assert STANDARD_NATIVE_ACP_V1.profile_hash() == STANDARD_HASH
    assert CLAUDE_AGENT_ACP_COMPAT_V1.profile_hash() == CLAUDE_HASH
    assert CURSOR_NATIVE_ACP_V1.profile_hash() != CURSOR_REVISION_2_HASH
    # The revision-3 identity, pinned: existing revision-2 Sessions are refused
    # by the existing profile-binding mismatch — deliberately, with no
    # compatibility or migration logic.
    assert CURSOR_NATIVE_ACP_V1.profile_hash() == (
        "9ec329a6ac5844ea9df789344fbaeeab7ec2cca7b704da66f470a118a68063e4"
    )


def test_read_only_grants_require_ask() -> None:
    for grant in (
        ("read",),
        ("search",),
        ("read", "search"),
        ("search", "read"),
        (),
    ):
        assert _required_mode_for(CURSOR_NATIVE_ACP_V1, grant) == "ask", grant


def test_every_other_valid_grant_requires_agent() -> None:
    for grant in (
        ("read", "search", "write", "execute"),
        ("write",),
        ("execute",),
        ("terminal",),
        ("delete",),
        ("move",),
        # The exact-subset rule cannot silently broaden: any capability outside
        # {read, search} — even a "mostly read" grant — is the agent class.
        ("read", "fetch"),
        ("switch_mode",),
        ("other",),
    ):
        assert _required_mode_for(CURSOR_NATIVE_ACP_V1, grant) == "agent", grant


def test_a_text_grant_is_refused_not_read_as_letters() -> None:
    """``str`` is an iterable of characters; that must never reach the rule.

    A text value would compute from letters and land on the permissive
    ``agent`` answer, so the policy path refuses toward zero prompt instead.
    """
    with pytest.raises(ConfigFidelityError):
        _required_mode_for(CURSOR_NATIVE_ACP_V1, "read")


def test_static_and_absent_declarations_answer_unchanged() -> None:
    """Claude stays frozen-static and grant-independent; standard stays modeless."""
    for grant in (("read",), ("read", "write", "execute")):
        assert _required_mode_for(CLAUDE_AGENT_ACP_COMPAT_V1, grant) == "default"
        assert _required_mode_for(STANDARD_NATIVE_ACP_V1, grant) is None


def test_the_policy_declaration_set_is_closed_and_paired() -> None:
    # A policy with no selector cannot be set, so it proves nothing.
    with pytest.raises((ProfileValidationError, TypeError)):
        _model_only_profile(permission_mode_policy_id=READ_ONLY_ASK_POLICY_ID)
    # Static mode and grant-driven policy are two different contracts; a
    # profile declares exactly one.
    with pytest.raises((ProfileValidationError, TypeError)):
        _model_only_profile(
            permission_mode_selector_id="mode",
            required_permission_mode="ask",
            permission_mode_policy_id=READ_ONLY_ASK_POLICY_ID,
        )
    # The policy set is closed source: unknown ids refuse at construction.
    with pytest.raises((ProfileValidationError, TypeError)):
        _model_only_profile(
            permission_mode_selector_id="mode",
            permission_mode_policy_id="ask-everything-v9",
        )
    # A selector with neither declaration proves nothing (pre-existing rule).
    with pytest.raises(ProfileValidationError):
        _model_only_profile(permission_mode_selector_id="mode")


# -- machine order, driven by the registered profile's computed mode ---------


def _registered_mode_machine(grant) -> ConfigFidelityMachine:
    """Exactly the machine RunTask builds for a Cursor Run with this grant."""
    return ConfigFidelityMachine(
        model_selector_id="model",
        effort_selector_id=None,
        requested_model=CURSOR_MODEL,
        requested_effort=EFFORT_NOT_APPLICABLE,
        fidelity_mode=CURSOR_NATIVE_ACP_V1.config_fidelity_mode,
        permission_mode_selector_id=CURSOR_NATIVE_ACP_V1.permission_mode_selector_id,
        required_permission_mode=_required_mode_for(CURSOR_NATIVE_ACP_V1, grant),
    )


def test_the_model_leg_is_structurally_unreachable_before_the_mode_proof() -> None:
    machine = _registered_mode_machine(("read",))
    machine.record_initial_options(_options("agent"))
    with pytest.raises(ConfigFidelityError):
        machine.model_plan()
    assert machine.permission_mode_plan() == "mode"
    machine.record_post_mode_options(_options("ask"))
    assert machine.model_plan() == "model"
    machine.record_post_model_options(_options("ask", model_current=CURSOR_MODEL))
    assert machine.phase == "verified"
    assert machine.require_ready() == (CURSOR_MODEL, EFFORT_NOT_APPLICABLE)
    assert [label for label, _ in machine.snapshots] == [
        "initial",
        "post_mode",
        "post_model",
    ]


def test_mode_drift_after_the_model_set_never_verifies() -> None:
    """A model-set side effect that moves the mode is refused, not tolerated."""
    machine = _registered_mode_machine(("read",))
    machine.record_initial_options(_options("agent"))
    machine.permission_mode_plan()
    machine.record_post_mode_options(_options("ask"))
    machine.model_plan()
    with pytest.raises(ConfigFidelityError):
        machine.record_post_model_options(
            _options("agent", model_current=CURSOR_MODEL)
        )
    with pytest.raises(ConfigFidelityError):
        machine.require_ready()


# -- end to end: the registered profile over a real ACP child ----------------


def test_a_read_grant_sets_ask_before_the_model_and_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "fake-agent-config.log"
    script = _script_for("ask", initial_mode="agent")
    script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_create_request(grant=("read",))))

    assert result.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    # The mode switch precedes the model switch, and both precede the prompt.
    assert _lines(configs) == ["mode=ask", f"model={CURSOR_MODEL}"]
    methods = harness.methods_seen()
    assert methods.count("session/set_config_option") == 2
    assert methods.index("session/set_config_option") < methods.index(
        "session/prompt"
    )
    # Exact readback right after the mode set, and re-proof after the model
    # set, both persisted as effective evidence.
    effective = _effective(harness)
    labels = [row["label"] for row in effective["discovery_snapshots"]]
    assert labels == ["initial", "post_mode", "post_model"]
    assert _snapshot_mode(effective, "initial") == "agent"
    assert _snapshot_mode(effective, "post_mode") == "ask"
    assert _snapshot_mode(effective, "post_model") == "ask"
    assert effective["effective_model"] == CURSOR_MODEL
    assert effective["effective_effort"] == EFFORT_NOT_APPLICABLE


def test_a_read_search_grant_sets_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "fake-agent-config.log"
    script = _script_for("ask", initial_mode="agent")
    script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, script)

    result = _run(
        harness.task(request=_create_request(grant=("read", "search")))
    )

    assert result.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    assert _lines(configs) == ["mode=ask", f"model={CURSOR_MODEL}"]


def test_a_writable_grant_sets_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "fake-agent-config.log"
    script = _script_for("agent", initial_mode="ask")
    script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, script)

    result = _run(
        harness.task(
            request=_create_request(grant=("read", "search", "write", "execute"))
        )
    )

    assert result.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    assert _lines(configs) == ["mode=agent", f"model={CURSOR_MODEL}"]
    effective = _effective(harness)
    assert _snapshot_mode(effective, "post_mode") == "agent"
    assert _snapshot_mode(effective, "post_model") == "agent"


def test_a_non_read_search_control_grant_sets_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``read+fetch`` is not a read-only grant class; the rule cannot broaden."""
    configs = tmp_path / "fake-agent-config.log"
    script = _script_for("agent", initial_mode="ask")
    script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, script)

    result = _run(
        harness.task(request=_create_request(grant=("read", "fetch")))
    )

    assert result.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    assert _lines(configs)[0] == "mode=agent"


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("missing mode selector", "missing"),
        ("required mode not advertised", "domain"),
        ("mode set rejected", "rejected"),
        ("wrong post-set readback", "readback"),
        ("mode drift after the model set", "drift"),
    ],
)
def test_mode_failures_refuse_pre_prompt_as_config_fidelity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str, mutate: str
) -> None:
    script = _script_for("ask", initial_mode="agent")
    if mutate == "missing":
        script["initial_options"] = [
            option
            for option in script["initial_options"]
            if option["id"] != "mode"
        ]
    elif mutate == "domain":
        script["initial_options"] = [
            script["initial_options"][0],
            {
                "id": "mode",
                "name": "Mode",
                "type": "select",
                "currentValue": "agent",
                "options": [
                    {"value": "agent", "name": "Agent"},
                    {"value": "plan", "name": "Plan"},
                ],
            },
        ]
    elif mutate == "rejected":
        script["reject_set_config_values"] = ["ask"]
    elif mutate == "readback":
        script["wrong_readback"] = {"mode": "agent"}
    else:
        # The model set silently restores the agent's own mode: the exact model
        # readback succeeds while the mode re-proof must fail.
        script["post_model_options_by_value"] = {
            CURSOR_MODEL: _options("agent")
        }

    harness = _registered_harness(tmp_path, monkeypatch, script)
    result = _run(harness.task(request=_create_request(grant=("read",))))

    assert result.status is AgentRunStatus.FAILED, label
    assert _result_payload(harness)["detail_code"] == "CONFIG_FIDELITY", label
    assert "session/prompt" not in harness.methods_seen(), label
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists(), label


def test_reuse_recomputes_the_mode_from_each_runs_own_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create and real load both run the leg; the grant decides per Run.

    Run 1 (``session/new``, read-only grant) proves ``ask``. Run 2 reuses the
    Session Run 1 actually published (nothing seeded), reaches a real
    ``session/load``, and its own writable grant proves ``agent`` — same
    Session, different Run, recomputed and re-proven. Reuse never falls back
    to ``session/new``.
    """
    configs = tmp_path / "fake-agent-config.log"
    script = _script_for("ask", initial_mode="agent")
    script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, script)

    first = _run(
        harness.task(run_id="run-0001", request=_create_request(grant=("read",)))
    )
    assert first.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    assert first.session_id

    second_script = _script_for("agent", initial_mode="ask")
    second_script["capture_config_path"] = str(configs)
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(second_script))

    second = _run(
        harness.task(
            run_id="run-0002",
            request=_reuse_request(
                first.session_id, grant=("read", "search", "write", "execute")
            ),
            seed_session=False,
        )
    )

    assert second.status is AgentRunStatus.COMPLETED, _result_payload(
        harness, "run-0002"
    )
    assert _lines(configs) == [
        "mode=ask",
        f"model={CURSOR_MODEL}",
        "mode=agent",
        f"model={CURSOR_MODEL}",
    ]
    families = _events(harness, "run-0002")
    assert "session_load_requested" in families
    assert "session_new_requested" not in families
    effective = _effective(harness, "run-0002")
    assert _snapshot_mode(effective, "post_mode") == "agent"
    assert _snapshot_mode(effective, "post_model") == "agent"


def test_the_mode_leg_keeps_model_only_fidelity_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No effort selector appears anywhere: no RPC, no seal, ``N/A`` effective."""
    configs = tmp_path / "fake-agent-config.log"
    script = _script_for("ask", initial_mode="agent")
    script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_create_request(grant=("read",))))

    assert result.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    assert all(
        line.startswith(("mode=", "model=")) for line in _lines(configs)
    )
    launch = json.loads((harness.run_dir() / "launch.json").read_text())
    assert launch["effort_selector_id"] is None
    effective = _effective(harness)
    assert effective["effective_effort"] == EFFORT_NOT_APPLICABLE
    record = harness.session_store().open_session(result.session_id)
    assert record.last_effective_effort == EFFORT_NOT_APPLICABLE


def test_rollback_after_partial_switch_uses_the_failing_runs_grant_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed mid-switch reuse Run rolls back under its *own* grant's mode.

    Run 1 (``session/new``, read-only grant) proves ``ask`` and publishes the
    Session. Run 2 reuses it through a real ``session/load`` under the other
    grant class, proves ``agent``, and only then has its model set rejected —
    a partial switch with the permission mode already moved. The rollback must
    re-prove the mode computed from **Run 2's sealed grant** (``agent``) while
    restoring Run 1's proven model, leaving the Session un-quarantined and
    genuinely reusable, with zero prompt for the failed Run.

    This is the regression tripwire for the rollback wiring itself: a policy
    profile freezes no ``required_permission_mode`` literal, so a rollback
    that read the static attribute instead of asking
    ``required_permission_mode_for(grant)`` would hand the fidelity machine a
    mode selector with no required value — an unprovable rollback, a
    ``config_rollback_failed`` event, and a quarantined Session, all of which
    the assertions below refuse.
    """
    configs = tmp_path / "fake-agent-config.log"
    first_script = _script_for("ask", initial_mode="agent")
    first_script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, first_script)

    first = _run(
        harness.task(run_id="run-0001", request=_create_request(grant=("read",)))
    )
    assert first.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    assert first.session_id

    # The agent as Run 1 left it: mode ``ask``, model proven. Run 2's model
    # switch is rejected only after the mode leg has already moved the mode.
    second_script = {
        "initial_options": _options("ask", model_current=CURSOR_MODEL),
        "reject_set_config_values": [CURSOR_MODEL_OTHER],
        "capture_config_path": str(configs),
        "final_message": "CURSOR_UNREACHED",
    }
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(second_script))

    second = _run(
        harness.task(
            run_id="run-0002",
            request=_reuse_request(
                first.session_id,
                grant=("read", "search", "write", "execute"),
                requested_model=CURSOR_MODEL_OTHER,
            ),
            seed_session=False,
        )
    )

    assert second.status is AgentRunStatus.FAILED
    assert _result_payload(harness, "run-0002")["detail_code"] == "CONFIG_FIDELITY"

    events = _events(harness, "run-0002")
    assert "session_load_requested" in events
    assert "config_rollback_started" in events
    assert "config_rollback_proven" in events and (
        "config_rollback_failed" not in events
    ), (
        "the rollback must derive its required mode from the failing Run's "
        f"sealed grant, not the profile's static literal; events: {events}"
    )

    # The partial switch had really begun: the failed Run's own evidence shows
    # the mode already moved to ``agent`` before the model set was rejected.
    effective = _effective(harness, "run-0002")
    labels = [row["label"] for row in effective["discovery_snapshots"]]
    assert labels == ["initial", "post_mode"]
    assert _snapshot_mode(effective, "post_mode") == "agent"

    # The whole wire history: Run 2's rollback re-proves ``agent`` — Run 2's
    # grant-derived mode, not Run 1's ``ask`` — then restores Run 1's model.
    assert _lines(configs) == [
        "mode=ask",
        f"model={CURSOR_MODEL}",
        "mode=agent",
        f"model={CURSOR_MODEL_OTHER}",
        "mode=agent",
        f"model={CURSOR_MODEL}",
    ]

    # Zero prompt for the failed Run: the only prompt on the wire is Run 1's.
    assert harness.methods_seen().count("session/prompt") == 1
    assert not (harness.run_dir("run-0002") / DISPATCH_STARTED_MARKER).exists()

    # Exact rollback proven ⇒ the previously proven pair is restored and the
    # Session carries no quarantine evidence.
    record = harness.session_store().open_session(first.session_id)
    assert record.quarantine is None
    assert record.last_effective_model == CURSOR_MODEL
    assert record.last_effective_effort == EFFORT_NOT_APPLICABLE

    # Reusable in fact, not just by flag: Run 3 loads the same Session as the
    # rollback left it (mode ``agent``, model restored) and its own read-only
    # grant re-proves ``ask``.
    third_script = {
        "initial_options": _options("agent", model_current=CURSOR_MODEL),
        "capture_config_path": str(configs),
        "final_message": "CURSOR_OK",
    }
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(third_script))

    third = _run(
        harness.task(
            run_id="run-0003",
            request=_reuse_request(first.session_id, grant=("read",)),
            seed_session=False,
        )
    )
    assert third.status is AgentRunStatus.COMPLETED, _result_payload(
        harness, "run-0003"
    )
    assert "session_load_requested" in _events(harness, "run-0003")
    assert _lines(configs)[6:] == ["mode=ask", f"model={CURSOR_MODEL}"]

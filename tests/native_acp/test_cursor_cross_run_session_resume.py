"""Cross-Run Session continuity for the registered Cursor profile.

GOAL contract 3 and PRD R4: v1 is process-per-Run, and same-Session continuity
is one external AGENT Session id plus a real ``session/load``. Run 1 creates and
uses the external Session, its process exits, and Run 2 starts a **new** process,
loads the stored id, proves exact model-only fidelity, and prompts in the same
conversation.

That contract is a claim about state ARS does **not** own. The external AGENT is
the conversation authority (GOAL *Authority split*), its configuration and
Session state are operator/AGENT-owned, and PRD R8 says ARS never manages or
relocates them. So the contract holds only while the configuration root the
child is launched with is *the agent's own*, stable across Runs.

These tests are the regression for the failure class where ARS repointed that
root at per-Run scratch: a profile selecting a launch-permission policy whose
environment key is the agent's whole configuration directory relocated the
agent's Session state into the Run directory and then deleted it with the Run,
so Run 2's real ``session/load`` had no configured Session to answer with and
the Run failed pre-dispatch with ``CONFIG_FIDELITY``.

The contract tests run the **registered** ``cursor-native-acp-v1``, not a
test-local copy of it. That is the point: the profile *as registered* is what a
deployment resolves, and every suite that missed this built its own.

Scope, because "same Session" names two different things here. The **external
AGENT** Session id is what these tests pin as continuous: two real child
processes use one id, the second reaches its prompt through a real
``session/load``, and the agent answers out of state it kept itself. The **ARS**
Session record is only the controller's own record naming that external id, and
the harness seeds Run 2's record the way an earlier Run would have left it (see
``_two_runs``). That record's binding to the external id is therefore arranged
here, not derived from Run 1's own record — record lineage is not what this file
pins.
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
from agent_run_supervisor.native_acp.config_fidelity import EFFORT_NOT_APPLICABLE
from agent_run_supervisor.native_acp.profile import (
    CURSOR_NATIVE_ACP_V1,
    DEFAULT_REGISTRY,
    AcpCompatProfile,
    AgentInstance,
    ProfileRegistry,
)
from agent_run_supervisor.native_acp.spec import resolve_run_environment

from .test_model_only_fidelity import CURSOR_MODEL, CURSOR_SCRIPT
from agent_run_supervisor.native_acp.run_task import DISPATCH_STARTED_MARKER
from .test_run_task import FAKE_AGENT_PATH, Harness, _request, _run

REGISTERED_PROFILE_ID = "cursor-native-acp-v1"
CURSOR_AGENT_ID = "cursor-registered"
# ARS's own record id, which is what a reuse request names. The external AGENT
# Session id is a different value: the agent mints it, and ARS only ever stores
# it in this record's binding.
REUSED_ARS_SESSION_ID = "sess-native-1"

# Planted by Run 1 and recalled by Run 2. The recall answer is produced by the
# agent out of state it persisted itself, so it can only survive if the agent's
# own configuration home survived Run 1's process.
RUN_ONE_NONCE = "CURSOR-CONTINUITY-NONCE-4e91c2"
RUN_TWO_PROMPT = "repeat the token from earlier in this conversation"


def _entry(**overrides) -> AgentEntry:
    """The operator half: which command this agent is, and what it needs.

    ``env_passthrough`` carries the fake agent's own script/trace names exactly
    the way an operator carries an agent's own environment names. The profile
    stays untouched, because which command an agent is was never a profile fact.
    """
    kwargs = dict(
        agent_id=CURSOR_AGENT_ID,
        profile_id=REGISTERED_PROFILE_ID,
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
        env_passthrough=("FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
    )
    kwargs.update(overrides)
    return AgentEntry(**kwargs)


def _script() -> dict:
    """A Cursor-shaped ACP agent that keeps its state in its config home.

    The home resolves the way a real CLI resolves one: an override variable if
    it is set, otherwise a directory under the operator's ``HOME``. Which of the
    two the child ends up using is decided entirely by what ARS projects, which
    is exactly what these tests are about.
    """
    script = dict(CURSOR_SCRIPT)
    script["config_home_env"] = [lp.CURSOR_CONFIG_DIR_ENV, "HOME"]
    script["nonce_memory"] = True
    return script


def _operator_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An operator-owned ``HOME``, stable across Runs and never ARS's to touch."""
    home = tmp_path / "operator-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _request_for(**overrides):
    kwargs = dict(
        agent_id=CURSOR_AGENT_ID,
        requested_model=CURSOR_MODEL,
        requested_effort=EFFORT_NOT_APPLICABLE,
    )
    kwargs.update(overrides)
    return _request(**kwargs)


def _new_session_request():
    """Run 1: the only intent that may open a Session with ``session/new``."""
    return _request_for(session_id=None)


def _reuse_request(session_id: str):
    """Run 2: a reuse request naming the ARS record bound to the external id.

    ``session_id`` names ARS's record and nothing else. The external id the
    child is actually sent lives in that record's binding, so no reuse request
    ever carries it.
    """
    return _request_for(session_id=session_id)


def _payload(run_dir: Path) -> dict:
    return json.loads((run_dir / "result.json").read_text())


def _event_types(run_dir: Path) -> list[str]:
    return [
        json.loads(line).get("type")
        for line in (run_dir / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _two_runs(harness: Harness):
    """Run 1 creates the Session and plants the token; Run 2 loads and recalls.

    Run 2 reuses **the Session identity Run 1 actually returned and persisted** —
    nothing is seeded. That is the whole point: if Run 1 fails to publish a
    durable bound record, or publishes one Run 2 cannot bind to, Run 2 must fail
    rather than quietly succeed against a fixture-arranged record.
    """
    first = _run(
        harness.task(
            run_id="run-0001",
            prompt_text=RUN_ONE_NONCE,
            request=_new_session_request(),
        )
    )
    assert first.session_id, "run 1 published no Session identity to continue"
    second = _run(
        harness.task(
            run_id="run-0002",
            prompt_text=RUN_TWO_PROMPT,
            request=_reuse_request(first.session_id),
            seed_session=False,
        )
    )
    return first, second


def test_run_two_loads_the_same_external_session_and_prompts_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core contract, end to end, over two real processes.

    Run 1 opens the external Session and plants a token in it. Its process exits
    and is reaped. Run 2 starts a *new* process, sends the stored external id
    through a real ``session/load``, proves the exact model readback, and
    prompts — and the answer carries Run 1's token, so the agent really did
    continue the same conversation out of its own state.

    So what is pinned is the whole continuity chain: Run 1 publishes a durable
    bound Session, Run 2 names *that* identity, one external id crosses two
    distinct child processes, the second really loads, agent-owned state
    survives in the operator's stable configuration home, and the token comes
    back out of it.
    """
    home = _operator_home(tmp_path, monkeypatch)
    harness = Harness(tmp_path, monkeypatch, _script())
    harness.registry = ProfileRegistry((CURSOR_NATIVE_ACP_V1,))
    harness.entry = _entry()

    first, second = _two_runs(harness)

    assert first.status is AgentRunStatus.COMPLETED, _payload(harness.run_dir("run-0001"))
    payload = _payload(harness.run_dir("run-0002"))
    assert second.status is AgentRunStatus.COMPLETED, payload
    families = _event_types(harness.run_dir("run-0002"))
    # Real load, and never a new session on a reuse path (PRD R4).
    assert "session_load_requested" in families
    assert "session_new_requested" not in families
    # The conversation continued: Run 1's token came back out of the agent's
    # own state, across a process boundary.
    assert RUN_ONE_NONCE in payload["final_message"]
    # Model-only fidelity is unchanged and exact, with no effort RPC.
    effective = json.loads((harness.run_dir("run-0002") / "effective.json").read_text())
    assert effective["effective_model"] == CURSOR_MODEL
    assert effective["effective_effort"] == EFFORT_NOT_APPLICABLE
    # Run 2 ran under Run 1's own Session, and that Session is still there and
    # still reusable, so a third Run continues the same external thread.
    assert second.session_id == first.session_id
    record = harness.session_store().open_session(first.session_id)
    assert record.agent_session_id == harness.external_id
    assert record.quarantine is None
    # The agent's configuration home is the operator's, untouched by ARS: it
    # still holds the Session state, and nothing was created under either Run.
    assert (home / "sessions").is_dir()
    for run_id in ("run-0001", "run-0002"):
        assert not (harness.run_dir(run_id) / lp.LAUNCH_PERMISSION_DIRNAME).exists()


def test_the_registered_cursor_profile_repoints_no_agent_owned_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrow structural reason the contract above can hold.

    ARS may not manage or relocate AGENT configuration, cache, or Session state
    (PRD R8, GOAL *Filesystem boundary*). A launch-permission policy whose
    environment key is the agent's **whole** configuration root does relocate
    it — into a directory ARS creates per Run and deletes once the child is
    reaped — so no such policy may be selected by a profile that also has to
    honour ``session/load`` across Runs.

    Asserted on the profile, on the resolved instance, on a real environment
    projection, and on sealed launch evidence, so neither the profile nor the
    Run path can reintroduce it quietly.
    """
    assert CURSOR_NATIVE_ACP_V1.launch_permission_policy_id is None
    assert "launch_permission_policy_id" not in CURSOR_NATIVE_ACP_V1.snapshot()

    entry = _entry()
    assert AgentInstance(
        profile=CURSOR_NATIVE_ACP_V1, entry=entry
    ).launch_permission_policy_id is None

    projection = resolve_run_environment(
        arsd_env={"PATH": "/usr/bin", "HOME": "/home/operator"},
        profile=CURSOR_NATIVE_ACP_V1,
        entry=entry,
    ).value_blind_projection()
    sources = {item.name: item.source for item in projection.names}
    # No ephemeral configuration root under any source class, and nothing at all
    # riding the launch-permission layer.
    assert lp.CURSOR_CONFIG_DIR_ENV not in sources
    assert lp.ENV_SOURCE_LAUNCH_PERMISSION not in set(sources.values())

    harness = Harness(tmp_path, monkeypatch, _script())
    harness.registry = ProfileRegistry((CURSOR_NATIVE_ACP_V1,))
    harness.entry = entry
    result = _run(
        harness.task(run_id="run-0001", request=_new_session_request())
    )

    assert result.status is AgentRunStatus.COMPLETED
    launch = json.loads((harness.run_dir("run-0001") / "launch.json").read_text())
    assert "launch_permission_policy_id" not in launch
    assert "launch_permission_digest" not in launch
    assert not (harness.run_dir("run-0001") / lp.LAUNCH_PERMISSION_DIRNAME).exists()


def test_no_registered_profile_relocates_an_agent_configuration_root() -> None:
    """Whole-configuration-root keys stay unselected, registry-wide.

    Every launch-permission policy registered today owns an agent's whole
    configuration directory rather than a permission-only file, so selecting one
    is incompatible with the ``session/load`` continuity every registered profile
    requires. Stated over the registry rather than over one profile, so a second
    profile cannot pick it up quietly.
    """
    selected = {
        profile_id: DEFAULT_REGISTRY.get(profile_id).launch_permission_policy_id
        for profile_id in DEFAULT_REGISTRY.ids()
    }
    assert selected == {profile_id: None for profile_id in DEFAULT_REGISTRY.ids()}
    # The mechanism itself is untouched and still registered, for a future
    # profile with evidence for a permission-only backend.
    assert lp.POLICY_DENY_WRITE_AND_SHELL_V1 in lp.LAUNCH_PERMISSION_POLICY_IDS


def test_a_config_root_repointed_per_run_cannot_carry_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure class itself, pinned so it cannot come back unnoticed.

    Same agent, same two-Run flow, but the profile selects a launch-permission
    policy whose key is the agent's whole configuration root. Run 1 still
    succeeds — a fresh configuration root is fine for ``session/new``, which is
    why deployment acceptance saw Run 1 pass. Run 2 gets a *different, empty*
    root, so its real ``session/load`` has no configured Session to answer with
    and the Run fails before any prompt.

    Expressed over an explicitly selecting profile, so the mechanism stays under
    test while no shipped profile selects it.
    """
    _operator_home(tmp_path, monkeypatch)
    selecting = AcpCompatProfile(
        profile_id=REGISTERED_PROFILE_ID,
        revision=CURSOR_NATIVE_ACP_V1.revision,
        acp_protocol_version="1",
        required_capabilities=CURSOR_NATIVE_ACP_V1.required_capabilities,
        requires_session_load=True,
        config_fidelity_mode=CURSOR_NATIVE_ACP_V1.config_fidelity_mode,
        effort_selector_id=None,
        launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
    )
    harness = Harness(tmp_path, monkeypatch, _script())
    harness.registry = ProfileRegistry((selecting,))
    # No operator declaration of the configuration home: a selecting profile
    # owns that key, so the registry refuses to let an operator name it.
    harness.entry = _entry()

    first, second = _two_runs(harness)

    assert first.status is AgentRunStatus.COMPLETED, _payload(harness.run_dir("run-0001"))
    # Run 1's material — and everything the child wrote into it — is gone with
    # the Run, which is exactly what leaves Run 2 nothing to load.
    assert not (harness.run_dir("run-0001") / lp.LAUNCH_PERMISSION_DIRNAME).exists()

    payload = _payload(harness.run_dir("run-0002"))
    assert second.status is AgentRunStatus.FAILED, payload
    assert payload["detail_code"] == "CONFIG_FIDELITY"
    families = _event_types(harness.run_dir("run-0002"))
    assert "session_load_requested" in families
    assert "session_new_requested" not in families
    assert not (harness.run_dir("run-0002") / "prompt-dispatch-started").exists()


# -- negative controls: continuity fails closed, before any wire work --------
#
# The positive test proves Run 2 continues Run 1's Session. These prove the
# other half: when that binding is missing, unusable, or belongs to a different
# identity, Run 2 fails *before* it can prompt and *without* falling back to
# creating a replacement Session. Without these, a broken Run 1 publication
# could still look green.


def _first_run(harness: Harness):
    first = _run(
        harness.task(
            run_id="run-0001",
            prompt_text=RUN_ONE_NONCE,
            request=_new_session_request(),
        )
    )
    assert first.status is AgentRunStatus.COMPLETED
    return first


def _second_run(harness: Harness, session_id: str):
    return _run(
        harness.task(
            run_id="run-0002",
            prompt_text=RUN_TWO_PROMPT,
            request=_reuse_request(session_id),
            seed_session=False,
        )
    )


def _cursor_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    _operator_home(tmp_path, monkeypatch)
    harness = Harness(tmp_path, monkeypatch, _script())
    harness.registry = ProfileRegistry((CURSOR_NATIVE_ACP_V1,))
    harness.entry = _entry()
    return harness


def _assert_failed_before_the_wire(harness: Harness, second) -> None:
    assert second.status is AgentRunStatus.FAILED
    run_dir = harness.run_dir("run-0002")
    # An absent event stream is the strongest form of this proof: the Run was
    # refused before it opened one. When a stream does exist, it must still
    # carry neither a prompt nor a replacement Session.
    events = run_dir / "events.jsonl"
    families = _event_types(run_dir) if events.exists() else []
    assert "session_new_requested" not in families
    assert "session_prompt_sent" not in families
    assert not (run_dir / DISPATCH_STARTED_MARKER).exists()


def test_a_missing_binding_fails_run_two_without_creating_a_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _cursor_harness(tmp_path, monkeypatch)
    first = _first_run(harness)

    (Path(harness.session_store().base_dir) / first.session_id / "session.json").unlink()

    _assert_failed_before_the_wire(harness, _second_run(harness, first.session_id))


def test_a_corrupt_binding_fails_run_two_without_creating_a_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _cursor_harness(tmp_path, monkeypatch)
    first = _first_run(harness)

    record = Path(harness.session_store().base_dir) / first.session_id / "session.json"
    record.write_bytes(b"{ this record was never finished")

    _assert_failed_before_the_wire(harness, _second_run(harness, first.session_id))


def test_a_drifted_binding_fails_run_two_without_creating_a_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structurally valid, but it belongs to a different Run identity."""
    harness = _cursor_harness(tmp_path, monkeypatch)
    first = _first_run(harness)

    record = Path(harness.session_store().base_dir) / first.session_id / "session.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["workspace_hash"] = "9" * 64
    record.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    _assert_failed_before_the_wire(harness, _second_run(harness, first.session_id))


def test_an_unbound_binding_fails_run_two_without_creating_a_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record that names no external Session cannot be loaded, only refused."""
    harness = _cursor_harness(tmp_path, monkeypatch)
    first = _first_run(harness)

    record = Path(harness.session_store().base_dir) / first.session_id / "session.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    del payload["agent_session_id"]
    record.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    _assert_failed_before_the_wire(harness, _second_run(harness, first.session_id))

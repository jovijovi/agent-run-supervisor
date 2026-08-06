"""Hermetic contract tests for the opt-in real-agent acceptance harness.

The acceptance module is skip-by-default and needs a real installed agent, an
operator-authored registry entry, and a controller-authorized boundary. Its
*assertion logic* must not be: a gate whose conditions are tautological, whose
case matrix silently drops a required variant, or whose evidence bundle can be
complete while a leg never ran would pass while proving nothing.

This suite loads that module and drives its pure helpers against **synthetic
fixtures only** — no daemon, no spawn, no model call, no opt-in, and no
reference to any production home. Every credential-shaped byte here is a
placeholder the test creates itself.

It also pins the re-point: the harness must express its legs against a registry
entry, and must not have grown a replacement for the retired artifact,
interpreter, or credential-root layers under another name.
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.session import derive_session_id_for_run

_ACCEPTANCE_PATH = Path(__file__).resolve().parent / "test_codex_socket_acceptance.py"


def _load_acceptance():
    """Import the opt-in module for inspection without collecting it twice.

    Loaded by path under its own name, so pytest's own import of the module
    (which the default skip then empties) is untouched. Registering it in
    ``sys.modules`` first is required: ``dataclasses`` resolves a class's module
    to interpret its annotations.
    """
    name = "acp_socket_acceptance_contract_view"
    spec = importlib.util.spec_from_file_location(name, _ACCEPTANCE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


acceptance = _load_acceptance()

# Captured at import, before any test patches ``arsd_client.ArsdClient``. Reading
# them lazily would read the *stand-in's* signature — which accepts anything —
# and the contract check would silently become a tautology.
_REAL_ARSD_CLIENT = acceptance.arsd_client.ArsdClient
_REAL_INIT_SIGNATURE = inspect.signature(_REAL_ARSD_CLIENT.__init__)
_REAL_SUBMIT_SIGNATURE = inspect.signature(_REAL_ARSD_CLIENT.submit)
_REAL_RUN_EVENTS_SIGNATURE = inspect.signature(_REAL_ARSD_CLIENT.run_events)
_REAL_RUN_STATUS_SIGNATURE = inspect.signature(_REAL_ARSD_CLIENT.run_status)


def _entry(**overrides) -> AgentEntry:
    kwargs = dict(
        agent_id="acceptance-agent",
        profile_id="standard-native-acp-v1",
        command="/opt/example/bin/some-acp-adapter",
        env_overlay=(("SOME_AGENT_HOME", "/tmp/ephemeral-acceptance-home"),),
    )
    kwargs.update(overrides)
    return AgentEntry(**kwargs)


# --- the opt-in gate stays opt-in --------------------------------------------


def test_the_gate_is_closed_by_default(monkeypatch):
    monkeypatch.delenv(acceptance._GATE, raising=False)
    assert acceptance._acceptance_ready() is False


def test_the_gate_needs_every_declared_input(monkeypatch):
    monkeypatch.setenv(acceptance._GATE, "1")
    for name in acceptance._REQUIRED_ENV:
        monkeypatch.setenv(name, "x")
    assert acceptance._acceptance_ready() is True
    for name in acceptance._REQUIRED_ENV:
        monkeypatch.delenv(name)
        assert acceptance._acceptance_ready() is False, f"{name} is not load-bearing"
        monkeypatch.setenv(name, "x")


def test_the_gate_requires_the_agents_file_and_the_agent_id():
    """After the reset a leg cannot be assembled from source constants at all."""
    assert "ARS_ACP_ACCEPTANCE_AGENTS_FILE" in acceptance._REQUIRED_ENV
    assert "ARS_ACP_ACCEPTANCE_AGENT_ID" in acceptance._REQUIRED_ENV


def test_the_gate_names_no_retired_input():
    retired = (
        "BINDING_ROOT",
        "TRUSTED_UID",
        "REAL_CREDENTIAL_ROOT",
        "SERVICE_UID",
        "GENERATION",
    )
    for name in acceptance._REQUIRED_ENV:
        for banned in retired:
            assert banned not in name, f"{name} still asks for a retired input"


def test_module_is_skipped_when_the_gate_is_shut():
    assert acceptance.pytestmark.mark.name == "skipif"


# --- the ephemeral-home invariant --------------------------------------------


def test_an_entry_without_an_overlay_home_is_refused():
    """Real ``session/new``/``session/load`` persist state under the home."""
    assert acceptance._ephemeral_home_declared(_entry(env_overlay=())) is False


def test_the_invoking_users_own_home_is_not_an_ephemeral_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/operator")
    assert (
        acceptance._ephemeral_home_declared(
            _entry(env_overlay=(("SOME_AGENT_HOME", "/home/operator"),))
        )
        is False
    )
    assert (
        acceptance._ephemeral_home_declared(
            _entry(env_overlay=(("SOME_AGENT_HOME", "/tmp/leg-home"),))
        )
        is True
    )


def test_the_harness_never_authors_a_registry_file():
    """An acceptance harness that writes the fixture tests the fixture."""
    text = _ACCEPTANCE_PATH.read_text(encoding="utf-8")
    for banned in ("write_registry", "write_text(", "registry_fixtures", "mkstemp"):
        assert banned not in text, f"the harness authors registry state via {banned!r}"


def test_the_harness_reads_the_agents_file_through_the_production_reader():
    text = _ACCEPTANCE_PATH.read_text(encoding="utf-8")
    assert "agent_registry.load_agents_file" in text
    assert "tomllib" not in text


# --- the leg matrix ----------------------------------------------------------


def test_every_positive_leg_is_declared_exactly_once():
    legs = acceptance.POSITIVE_LEGS
    assert len(set(legs)) == len(legs)
    assert set(legs) >= {
        "p1_exact_config_and_evidence",
        "p2_continuity_and_b1_boundary",
        "p3_permission_denied_before_effect",
        acceptance.P4_CANCEL_LEG,
        acceptance.P4_TIMEOUT_LEG,
    }


def test_the_real_agent_acp_legs_survived_the_reset():
    """R5: deleting these would silently drop the only real-agent evidence."""
    text = _ACCEPTANCE_PATH.read_text(encoding="utf-8")
    assert "session/new" in text and "session/load" in text
    assert "p3_permission_denied_before_effect" in acceptance.POSITIVE_LEGS


def test_p4_declares_both_sublegs_distinctly():
    outcomes = acceptance.P4_EXPECTED_OUTCOMES
    assert set(outcomes) == {acceptance.P4_CANCEL_LEG, acceptance.P4_TIMEOUT_LEG}
    codes = {row["detail_code"] for row in outcomes.values()}
    assert codes == {"SUPERVISOR_CANCELLED", "TURN_TIMEOUT"}
    for row in outcomes.values():
        # The fixed terminal-table row for a dispatched Turn with no
        # trustworthy ACP terminal — never harness opinion.
        assert row["status"] == "unknown"
        assert row["retryable"] is False
        assert row["session_quarantined"] is True


def test_the_timeout_bound_cannot_fire_before_dispatch():
    assert 0 < acceptance.P4_TURN_TIMEOUT_SECONDS < acceptance.RUN_TIMEOUT_SECONDS


def test_every_negative_family_keeps_its_full_variant_count():
    declared: dict[str, list[str]] = {}
    for case in acceptance.NEGATIVE_CASES:
        declared.setdefault(case.family, []).append(case.case_id)
    for family, members in acceptance.NEGATIVE_FAMILIES.items():
        assert sorted(declared[family]) == sorted(members), family


def test_negative_case_ids_are_unique():
    ids = [case.case_id for case in acceptance.NEGATIVE_CASES]
    assert len(set(ids)) == len(ids)


def test_every_negative_case_names_a_refusal_that_still_exists():
    """A case whose code no longer exists would pass while proving nothing."""
    from agent_run_supervisor.native_acp.observation import OBSERVATION_REFUSALS

    live = set(OBSERVATION_REFUSALS) | {
        "AGENT_NOT_REGISTERED",
        "AGENT_ID_INVALID",
        "SESSION_NOT_FOUND_FOR_REUSE",
        "SESSION_RECORD_INVALID",
        "SESSION_EXTERNAL_ID_MISSING",
        "SESSION_BINDING_MISMATCH",
        "COMMAND_NOT_FOUND",
        "COMMAND_NOT_EXECUTABLE",
        "SPAWN_FAILED",
    }
    for case in acceptance.NEGATIVE_CASES:
        assert case.detail_code in live, case.case_id


def test_the_retired_negative_families_are_gone():
    """They went with the layer they tested; there is nothing left to test."""
    text = _ACCEPTANCE_PATH.read_text(encoding="utf-8")
    for banned in (
        "RUNTIME_IDENTITY_MISMATCH",
        "tampered_adapter_entry",
        "swapped_node_binary",
        "credential_root_mode",
        "credential_root_symlink",
        "credential_refs_missing",
    ):
        assert banned not in text, f"a retired family survives as {banned!r}"


def test_the_harness_names_no_retired_module():
    text = _ACCEPTANCE_PATH.read_text(encoding="utf-8")
    for banned in ("runtime_binding", "BindingReader", "TrustedOwnership"):
        assert banned not in text


# --- evidence completeness ---------------------------------------------------


def test_the_evidence_bundle_starts_empty():
    """A bundle pre-seeded with a leg would be complete without the leg running."""
    assert set(acceptance.EVIDENCE) == {"legs"}


def test_a_leg_result_is_bound_to_the_reviewed_sha():
    recorded: dict[str, object] = {}
    acceptance.EVIDENCE["legs"] = recorded
    acceptance._record("leg-x", {"commit_sha": "abc123"}, {"status": "completed"})
    assert recorded["leg-x"] == {"status": "completed", "commit_sha": "abc123"}
    acceptance.EVIDENCE["legs"] = {}


def test_the_completeness_assertion_covers_every_declared_leg():
    text = _ACCEPTANCE_PATH.read_text(encoding="utf-8")
    assert "set(POSITIVE_LEGS) | {case.case_id for case in NEGATIVE_CASES}" in text


def test_the_daemon_receives_the_snapshot_and_never_a_path():
    """The registry is read once, by the daemon's own startup, and handed on."""
    import inspect

    signature = inspect.signature(acceptance.EphemeralDaemon.__init__)
    assert "agents" in signature.parameters
    assert "binding_root" not in signature.parameters
    assert "agents_file" not in signature.parameters


# --- the harness actually speaks the contracts it depends on ------------------
#
# Everything below drives the **real** leg driver against stand-ins that enforce
# the real contracts: calls are bound through the genuine ``ArsdClient``
# signatures and every submit payload is parsed by the genuine
# ``protocol.parse_submit``. A harness that speaks a different contract fails
# here exactly as it would against a live daemon — with no daemon, no spawn, no
# credential read, and no model call.

ACCEPTANCE_ENV = {
    "ARS_ACP_ACCEPTANCE_SOCKET_DIR": "/tmp/acceptance-sockets",
    "ARS_ACP_ACCEPTANCE_SUPERVISOR_ROOT": "/tmp/acceptance-root",
    "ARS_ACP_ACCEPTANCE_WORKSPACE_PARENT": "/tmp/acceptance-ws",
    "ARS_ACP_ACCEPTANCE_OWNER": "acceptance-owner",
    "ARS_ACP_ACCEPTANCE_NAMESPACE": "acceptance/ns",
    "ARS_ACP_ACCEPTANCE_CALLER_MAPPING": "1000:acceptance-principal:o:n",
    "ARS_ACP_ACCEPTANCE_COMMIT_SHA": "0" * 40,
    "ARS_ACP_ACCEPTANCE_AGENTS_FILE": "/tmp/acceptance-agents.toml",
    "ARS_ACP_ACCEPTANCE_AGENT_ID": "acceptance-agent",
    "ARS_ACP_ACCEPTANCE_MODEL": "provider/model",
    "ARS_ACP_ACCEPTANCE_EFFORT": "high",
}


@pytest.fixture()
def acceptance_env(monkeypatch):
    for name, value in ACCEPTANCE_ENV.items():
        monkeypatch.setenv(name, value)


def _no_running_loop() -> bool:
    """True when this call is *not* inside a running asyncio loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


class _RecordingClient:
    """Enforces the real ``ArsdClient`` contract and records what was asked.

    Two properties are load-bearing. Every method binds through the genuine
    signature, so a call shaped for a different contract raises the same
    ``TypeError`` the real client raises. And every blocking call asserts it is
    running with **no** asyncio loop in this thread: the real client is
    synchronous, so driving it from inside the daemon's own coroutine would
    block the loop that has to answer it.
    """

    instances: list["_RecordingClient"] = []

    def __init__(self, socket_path, *, api_version=None) -> None:
        _REAL_INIT_SIGNATURE.bind(self, socket_path, api_version=api_version)
        self.socket_path = socket_path
        self.calls: list[str] = []
        self.submits: list[dict] = []
        self.events: dict[str, list[dict]] = {}
        self.terminals: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.status_error: Exception | None = None
        self._run_seq = 0
        _RecordingClient.instances.append(self)

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        self.calls.append("connect")

    def close(self) -> None:
        self.calls.append("close")

    def __enter__(self) -> "_RecordingClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- operations --------------------------------------------------------

    def _guard(self, name: str) -> None:
        assert _no_running_loop(), (
            f"{name} is a blocking call and ran inside an asyncio loop; the "
            "daemon it is talking to needs that loop to answer"
        )
        self.calls.append(name)

    def submit(self, **kwargs) -> dict:
        from agent_run_supervisor.arsd import protocol

        # The real keyword-only contract: request_id + payload, nothing else.
        bound = _REAL_SUBMIT_SIGNATURE.bind(self, **kwargs)
        self._guard("submit")
        payload = dict(bound.arguments["payload"])
        # The real parser, so the envelope is proven rather than assumed.
        command = protocol.parse_submit(payload)
        self._run_seq += 1
        run_id = f"run-{self._run_seq}"
        self.submits.append(
            {
                "request_id": bound.arguments["request_id"],
                "payload": payload,
                "command": command,
                "run_id": run_id,
            }
        )
        # The real ack shape: the Session a create bound, or the one a reuse
        # named. A caller reads it to name the Session on the next submit.
        session_id = command.request.session_id
        if session_id is None:
            session_id = derive_session_id_for_run(run_id)
        return {
            "run_id": run_id,
            "session_id": session_id,
            "accepted_at": "2026-08-02T00:00:00+00:00",
        }

    def run_status(self, run_id: str, *, request_id: str | None = None) -> dict:
        _REAL_RUN_STATUS_SIGNATURE.bind(self, run_id, request_id=request_id)
        self._guard("run_status")
        if self.status_error is not None:
            raise self.status_error
        result = self.terminals.get(run_id)
        if result is None:
            return {"run_id": run_id, "state": "accepted"}
        return {"run_id": run_id, "result": result}

    def run_events(self, run_id: str, **kwargs) -> dict:
        _REAL_RUN_EVENTS_SIGNATURE.bind(self, run_id, **kwargs)
        self._guard("run_events")
        return {"events": list(self.events.get(run_id, ())), "exhausted": True}

    def session_status(self, session_id: str, *, request_id: str | None = None) -> dict:
        self._guard("session_status")
        # The real projection: identity plus optional quarantine evidence, and
        # no lifecycle state — a stub that invents one would let a caller that
        # still reads `state` pass against a daemon that never sends it.
        return self.sessions.get(
            session_id, {"session_id": session_id, "quarantine": None}
        )

    def run_cancel(self, run_id: str, *, request_id: str | None = None) -> dict:
        self._guard("run_cancel")
        return {"run_id": run_id}


class _StubDaemon:
    """The daemon seam only: a real supervisor root, and no server at all."""

    def __init__(self, supervisor_root: Path) -> None:
        self.supervisor_root = supervisor_root
        self.socket_path = supervisor_root / "stub.sock"
        self.hosted_calls = 0

    @staticmethod
    def _cm(daemon):
        import contextlib

        @contextlib.contextmanager
        def hosted(policy):
            assert _no_running_loop(), "the daemon host must not nest inside a loop"
            daemon.hosted_calls += 1
            yield

        return hosted

    @contextlib.asynccontextmanager
    async def serving(self, policy):
        """Kept so the pre-repair control flow reaches its real defect.

        Without it the driver would fail on this stub's shape instead of on the
        contract under test, and the RED would prove nothing.
        """
        self.hosted_calls += 1
        yield None


def _stub_daemon(tmp_path: Path) -> _StubDaemon:
    daemon = _StubDaemon(tmp_path / "root")
    daemon.hosted = _StubDaemon._cm(daemon)
    return daemon


def _terminal(status: str = "completed", **fields) -> dict:
    body = {
        "status": status,
        "detail_code": None,
        "retryable": False,
        "final_message": "",
    }
    body.update(fields)
    return body


@pytest.fixture()
def recording_client(monkeypatch):
    _RecordingClient.instances.clear()
    monkeypatch.setattr(acceptance.arsd_client, "ArsdClient", _RecordingClient)
    monkeypatch.setattr(acceptance, "POLL_INTERVAL", 0.0)
    return _RecordingClient


# --- G3-L3-01: the harness can actually submit -------------------------------


def _prime_terminals(client_cls, *, count: int, status: str = "completed") -> None:
    """Answer every run the driver is about to create, in order."""

    def terminals_for(instance):
        for index in range(1, count + 1):
            instance.terminals[f"run-{index}"] = _terminal(status)

    client_cls._prime = terminals_for


def test_l3_01_a_positive_leg_submits_the_real_client_envelope(
    tmp_path, acceptance_env, recording_client
):
    """RED before the repair: ``submit(**payload)`` cannot bind the real call.

    ``_submit_payload`` returns the submit *payload* — ``request``,
    ``prompt_text``, ``workspace_root``. ``ArsdClient.submit`` takes keyword-only
    ``request_id`` and ``payload``. Splatting one into the other raises
    ``TypeError`` before a single byte reaches the socket.
    """
    daemon = _stub_daemon(tmp_path)
    original_init = _RecordingClient.__init__

    def init(self, socket_path, *, api_version=None):
        original_init(self, socket_path, api_version=api_version)
        for index in range(1, 4):
            self.terminals[f"run-{index}"] = _terminal("completed")

    recording_client.__init__ = init

    outcome = acceptance._drive_positive_leg(
        daemon, "p1_exact_config_and_evidence", tmp_path / "ws"
    )

    client = _RecordingClient.instances[-1]
    assert len(client.submits) == 1
    submitted = client.submits[0]
    assert submitted["request_id"], "every submit carries an explicit request id"
    assert set(submitted["payload"]) <= {
        "request",
        "prompt_text",
        "workspace_root",
        "cwd",
        "retry_of_run_id",
    }
    assert submitted["command"].request.session_id is None  # a create
    assert outcome["status"] == "completed"
    recording_client.__init__ = original_init


def test_l3_01_the_client_connection_is_opened_and_closed(
    tmp_path, acceptance_env, recording_client
):
    daemon = _stub_daemon(tmp_path)
    original_init = _RecordingClient.__init__

    def init(self, socket_path, *, api_version=None):
        original_init(self, socket_path, api_version=api_version)
        for index in range(1, 4):
            self.terminals[f"run-{index}"] = _terminal("completed")

    recording_client.__init__ = init
    acceptance._drive_positive_leg(
        daemon, "p1_exact_config_and_evidence", tmp_path / "ws"
    )
    client = _RecordingClient.instances[-1]
    assert client.calls[0] == "connect"
    assert client.calls[-1] == "close"
    assert daemon.hosted_calls == 1
    recording_client.__init__ = original_init


def test_l3_01_the_connection_is_closed_even_when_the_leg_raises(
    tmp_path, acceptance_env, recording_client
):
    """A leg that blows up must not leave the caller connection open."""
    daemon = _stub_daemon(tmp_path)
    original_init = _RecordingClient.__init__

    def init(self, socket_path, *, api_version=None):
        original_init(self, socket_path, api_version=api_version)
        self.status_error = RuntimeError("status exploded")

    recording_client.__init__ = init
    with pytest.raises(RuntimeError):
        acceptance._drive_positive_leg(
            daemon, "p1_exact_config_and_evidence", tmp_path / "ws"
        )
    client = _RecordingClient.instances[-1]
    assert client.calls[-1] == "close"
    recording_client.__init__ = original_init


# --- G3-L3-02: p2 is two ordered Runs, or it is not continuity ---------------


def _seed_first_run_session(daemon: _StubDaemon, run_id: str, workspace: Path) -> str:
    """The record Run 1's own admission leaves behind, with its real external id."""
    from agent_run_supervisor.native_acp import storage
    from agent_run_supervisor.native_acp.spec import resolve_workspace_binding

    workspace.mkdir(parents=True, exist_ok=True)
    binding = resolve_workspace_binding(root=workspace)
    store = storage.native_session_store(daemon.supervisor_root)
    external = "external-acp-session-from-the-agent"
    storage.create_native_session(
        store,
        session_id=derive_session_id_for_run(run_id),
        profile_id="standard-native-acp-v1",
        profile_revision=1,
        profile_hash="0" * 64,
        owner=ACCEPTANCE_ENV["ARS_ACP_ACCEPTANCE_OWNER"],
        namespace=ACCEPTANCE_ENV["ARS_ACP_ACCEPTANCE_NAMESPACE"],
        workspace_hash=binding.workspace_hash,
        effective_cwd=binding.effective_cwd,
        matched_root=binding.canonical_root,
        agent_id=ACCEPTANCE_ENV["ARS_ACP_ACCEPTANCE_AGENT_ID"],
        agent_session_id=external,
    )
    return external


def _drive_p2(tmp_path, recording_client, *, second_run_events):
    daemon = _stub_daemon(tmp_path)
    workspace = tmp_path / "ws"
    original_init = _RecordingClient.__init__

    def init(self, socket_path, *, api_version=None):
        original_init(self, socket_path, api_version=api_version)
        self.terminals["run-1"] = _terminal("completed")
        self.terminals["run-2"] = _terminal("completed")
        self.events["run-1"] = [{"type": "session_new_requested"}]
        self.events["run-2"] = list(second_run_events)

    recording_client.__init__ = init
    try:
        _seed_first_run_session(daemon, "run-1", workspace)
        outcome = acceptance._drive_positive_leg(
            daemon, acceptance.P2_CONTINUITY_LEG, workspace
        )
    finally:
        recording_client.__init__ = original_init
    return daemon, _RecordingClient.instances[-1], outcome


def test_l3_02_the_continuity_leg_drives_two_ordered_runs(
    tmp_path, acceptance_env, recording_client
):
    """The leg needs two ordered Runs: one create, then one reuse.

    A single submit could only ever create a Session, so it could not reach
    ``session/load`` at all — the one thing this leg exists to prove.
    """
    _daemon, client, _outcome = _drive_p2(
        tmp_path,
        recording_client,
        second_run_events=[{"type": "session_load_requested"}],
    )

    assert len(client.submits) == 2, "continuity needs two ordered Runs"
    first, second = client.submits
    assert first["command"].request.session_id is None  # a create
    assert second["command"].request.session_id is not None  # existing-only reuse
    # The second Run reuses the identity the *first* Run produced, not a
    # constant the harness invented before either Run existed.
    reused = second["command"].request.session_id
    assert reused == derive_session_id_for_run(first["run_id"])
    assert first["request_id"] != second["request_id"]


def test_l3_02_both_runs_are_awaited_to_terminal(
    tmp_path, acceptance_env, recording_client
):
    _daemon, client, outcome = _drive_p2(
        tmp_path,
        recording_client,
        second_run_events=[{"type": "session_load_requested"}],
    )
    assert client.calls.count("run_status") >= 2
    assert outcome["status"] == "completed"
    assert outcome["continuity"]["runs"] == 2


def test_l3_02_the_second_run_must_actually_load_the_session(
    tmp_path, acceptance_env, recording_client
):
    """Continuity is the observed ACP path, never the leg's name."""
    _daemon, _client, outcome = _drive_p2(
        tmp_path,
        recording_client,
        second_run_events=[{"type": "session_load_requested"}],
    )
    assert outcome["continuity"]["session_loaded"] is True
    assert outcome["continuity"]["session_recreated"] is False


def test_l3_02_a_second_run_that_recreates_the_session_fails_the_leg(
    tmp_path, acceptance_env, recording_client
):
    """Silent re-creation is the exact failure this leg has to catch."""
    with pytest.raises(AssertionError):
        _drive_p2(
            tmp_path,
            recording_client,
            second_run_events=[{"type": "session_new_requested"}],
        )


def test_l3_02_a_second_run_with_no_session_event_fails_the_leg(
    tmp_path, acceptance_env, recording_client
):
    with pytest.raises(AssertionError):
        _drive_p2(tmp_path, recording_client, second_run_events=[])


def test_l3_02_the_continuity_evidence_is_recorded_structurally(
    tmp_path, acceptance_env, recording_client
):
    """Recorded as checked facts, not as a leg name or a sentence."""
    _daemon, _client, outcome = _drive_p2(
        tmp_path,
        recording_client,
        second_run_events=[{"type": "session_load_requested"}],
    )
    continuity = outcome["continuity"]
    assert set(continuity) == {
        "runs",
        "session_loaded",
        "session_recreated",
        "session_quarantined",
    }
    assert all(isinstance(value, (int, bool, str)) for value in continuity.values())
    # No external session id, no model text, no agent self-report.
    serialized = json.dumps(outcome, sort_keys=True)
    assert "external-acp-session-from-the-agent" not in serialized

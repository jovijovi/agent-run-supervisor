"""C9 L2: session/load continuity + controlled cross-Run model/effort
switching with exact rollback or quarantine (PRD R4)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import profile as profile_module
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.profile import (
    AcpCompatProfile,
    ProfileRegistry,
)
from agent_run_supervisor.native_acp.run_task import RunTask
from agent_run_supervisor.native_acp.spec import (
    AgentRunRequest,
    InputRef,
    RunLimits,
    RunSpecAssembler,
    resolve_run_environment,
)
from agent_run_supervisor.session import SessionNotFoundError
from agent_run_supervisor.session import QUARANTINE_DISPATCH_OBSERVATION_LOST

FAKE_AGENT_PATH = Path(__file__).with_name("fake_agent.py")

# The external Session id the fake agent returns from ``session/new``; every
# script in this module leaves it at its default.
EXTERNAL_SESSION_ID = "fake-external-session-1"

BASE_SET = [
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
        "options": [
            {"value": "high", "name": "High"},
            {"value": "max", "name": "Max"},
        ],
    },
]

K3_SET = [
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
]

K3_SET_WITHOUT_EFFORT = [option for option in K3_SET if option["id"] != "effort"]

FIRST_RUN_SCRIPT = {
    "initial_options": BASE_SET,
    "post_model_options_by_value": {"provider/base": BASE_SET},
    "final_message": "RUN1_OK",
}

HAPPY_SWITCH_SCRIPT = {
    "initial_options": BASE_SET,
    "post_model_options_by_value": {
        "kimi-for-coding/k3": K3_SET,
        "provider/base": BASE_SET,
    },
    "final_message": "RUN2_OK",
}


def _profile() -> AcpCompatProfile:
    """ACP-v1 conformance only. The fake agent's command is an operator fact."""
    return AcpCompatProfile(
        profile_id="fake-agent-v1",
        revision=1,
        acp_protocol_version="1",
        required_capabilities=(),
        base_allowlist=("PATH", "HOME", "FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
        requires_session_load=True,
    )


def _entry() -> AgentEntry:
    return AgentEntry(
        agent_id="fake-agent",
        profile_id="fake-agent-v1",
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
    )


def _request(model: str, effort: str, session_id: str = "sess-switch-1", **overrides):
    kwargs = dict(
        owner="hermes",
        namespace="hermes/doc-check",
        agent_id="fake-agent",
        session_id=session_id,
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),),
        requested_model=model,
        requested_effort=effort,
        grant_ref="grant:doc-check-1",
        grant_hash="sha256:" + "b" * 64,
        grant_role_hash="sha256:" + "c" * 64,
        grant_capabilities=("read",),
        mcp_snapshot_hashes=(),
        credential_refs=(),
        limits=RunLimits(),
        evidence_policy_hash="sha256:" + "d" * 64,
        recovery_policy_hash="sha256:" + "e" * 64,
    )
    kwargs.update(overrides)
    return AgentRunRequest(**kwargs)


class SwitchHarness:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.root = tmp_path / ".agent-run-supervisor"
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir(exist_ok=True)
        self.registry = ProfileRegistry((_profile(),))
        self.entry = _entry()

    def seed_bound_session(self, request: AgentRunRequest) -> None:
        """Arrange the precondition a reuse Run requires (B1 / PRD R4).

        A reuse request opens its Session **existing-only** and needs a stored
        external id, so no Run may conjure the record it reuses. The harness
        therefore creates exactly the record an earlier Run would have left
        behind — identity computed by the production assembler and written
        through the production store seam, so a mismatch here would be a real
        refusal rather than a fixture convenience. A record that already exists
        (including one a test quarantined on purpose) is left untouched.
        """
        if request.session_id is None:
            return
        session_id = request.session_id
        store = storage.native_session_store(self.root)
        try:
            store.open_session(session_id)
            return
        except SessionNotFoundError:
            pass
        assembler = RunSpecAssembler(request)
        instance = assembler.resolve_agent(self.entry, registry=self.registry)
        assembler.bind_workspace(root=self.workspace, cwd=None)
        assembler.resolve_launch(
            environment=resolve_run_environment(
                arsd_env=dict(os.environ), profile=instance.profile, entry=self.entry
            )
        )
        spec = assembler.seal(
            run_id="run-seed-0",
            submitted_at="2026-07-21T00:00:00+00:00",
            retry_of_run_id=None,
        )
        storage.create_native_session(
            store,
            session_id=session_id,
            profile_id=spec.agent.profile_id,
            profile_revision=spec.agent.profile_revision,
            profile_hash=spec.agent.profile_hash,
            owner=spec.identity.owner,
            namespace=spec.identity.namespace,
            workspace_hash=spec.workspace.workspace_hash,
            effective_cwd=spec.workspace.cwd,
            matched_root=spec.workspace.canonical_root,
            agent_id=spec.agent.agent_id,
            session_epoch=spec.agent.session_epoch,
            agent_session_id=EXTERNAL_SESSION_ID,
        )

    def run(self, run_id: str, script: dict, request: AgentRunRequest, **overrides):
        self.monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
        self.monkeypatch.setenv("FAKE_AGENT_TRACE", str(self.trace_path(run_id)))
        self.seed_bound_session(request)
        task = RunTask(
            request=request,
            prompt_text=f"prompt for {run_id}",
            run_id=run_id,
            workspace_root=self.workspace,
            registry=self.registry,
            agent_entry=self.entry,
            supervisor_root=self.root,
            submitted_at="2026-07-21T00:00:00+00:00",
            **overrides,
        )

        async def case():
            return await asyncio.wait_for(task.run(), 60)

        return asyncio.run(case())

    def trace_path(self, run_id: str) -> Path:
        return self.tmp_path / f"trace-{run_id}.log"

    def methods(self, run_id: str) -> list[str]:
        path = self.trace_path(run_id)
        if not path.exists():
            return []
        return [line for line in path.read_text().splitlines() if line]

    def record(self, session_id: str = "sess-switch-1"):
        return storage.native_session_store(self.root).open_session(session_id)

    def prepare_session(self) -> None:
        # The bound record is seeded first (see ``seed_bound_session``), so this
        # first Run exercises the real ``session/load`` continuity path rather
        # than creating a Session through the Run path — which fail-closed
        # reuse no longer permits.
        result = self.run("run-0001", FIRST_RUN_SCRIPT, _request("provider/base", "high"))
        assert result.status is AgentRunStatus.COMPLETED
        assert "session/load" in self.methods("run-0001")
        assert "session/new" not in self.methods("run-0001")
        record = self.record()
        assert record.agent_session_id == EXTERNAL_SESSION_ID
        assert record.last_effective_model == "provider/base"
        assert record.last_effective_effort == "high"


def _events(harness, run_id: str) -> list[dict]:
    path = harness.root / "native-runs" / run_id / "events.jsonl"
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


def _initialize_evidence(harness, run_id: str) -> dict:
    path = harness.root / "native-runs" / run_id / "initialize_evidence.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_a11_agent_self_report_drift_across_two_runs_warns_and_never_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A11 on the production path: an agent upgrade costs no ARS action.

    Two Runs of one Session against the same registered command, with the agent
    reporting a different name and version the second time. That is exactly what
    happens when an operator upgrades the agent behind an unchanged command, and
    it must not refuse: the second Run completes, records the drift as a
    non-authoritative policy warning, and reuses the Session through a real
    ``session/load``.

    Driven end to end through ``RunTask`` rather than ``judge_initialize``,
    because the defect this pins was that nothing loaded the previous
    observation on the production path at all.
    """
    harness = SwitchHarness(tmp_path, monkeypatch)

    first = dict(FIRST_RUN_SCRIPT)
    first["agent_info"] = {"name": "fake-acp-agent", "version": "1.0.0"}
    assert harness.run(
        "run-0001", first, _request("provider/base", "high")
    ).status is AgentRunStatus.COMPLETED
    assert not [
        event
        for event in _events(harness, "run-0001")
        if event["type"] == "policy_warning"
    ], "a first Run has nothing to have drifted from"

    second = dict(HAPPY_SWITCH_SCRIPT)
    second["agent_info"] = {"name": "fake-acp-agent", "version": "2.0.0"}
    result = harness.run("run-0002", second, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.COMPLETED
    assert "session/load" in harness.methods("run-0002")
    assert "session/new" not in harness.methods("run-0002")

    warnings = [
        event
        for event in _events(harness, "run-0002")
        if event["type"] == "policy_warning"
    ]
    assert [warning["code"] for warning in warnings] == ["AGENT_SELF_REPORT_CHANGED"]
    assert warnings[0]["authoritative"] is False

    evidence = _initialize_evidence(harness, "run-0002")
    assert evidence["authoritative"] is False
    assert evidence["refusal"] is None
    assert evidence["observed"]["agent_info"]["version"] == "2.0.0"


def test_a11_capability_drift_across_two_runs_warns_and_never_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advertised capabilities may change between Runs of one Session."""
    harness = SwitchHarness(tmp_path, monkeypatch)

    assert harness.run(
        "run-0001", FIRST_RUN_SCRIPT, _request("provider/base", "high")
    ).status is AgentRunStatus.COMPLETED

    # A real capability the SDK models, so the drift survives validation rather
    # than being dropped as an unknown key on the way in.
    second = dict(HAPPY_SWITCH_SCRIPT)
    second["agent_capabilities"] = {"sessionCapabilities": {"fork": {}}}
    result = harness.run("run-0002", second, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.COMPLETED
    codes = [
        event["code"]
        for event in _events(harness, "run-0002")
        if event["type"] == "policy_warning"
    ]
    assert "ADVERTISED_CAPABILITIES_CHANGED" in codes


def test_a11_a_stable_agent_emits_no_drift_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paired guard: the warning means something only if silence is possible."""
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()
    assert harness.run(
        "run-0002", HAPPY_SWITCH_SCRIPT, _request("kimi-for-coding/k3", "max")
    ).status is AgentRunStatus.COMPLETED
    assert not [
        event
        for event in _events(harness, "run-0002")
        if event["type"] == "policy_warning"
    ]


ENV_SENTINEL = "ArS-SeNtInEl-projected-value-4c9b"


def _sentinel_entry() -> AgentEntry:
    """The registered command, plus one projected environment value to leak."""
    return AgentEntry(
        agent_id="fake-agent",
        profile_id="fake-agent-v1",
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
        env_overlay=(("AGENT_TOKEN", ENV_SENTINEL),),
    )


def _session_root_text(harness) -> str:
    """Every byte under the Session root, as text."""
    root = harness.root / "native-sessions"
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def test_a1_the_session_observation_is_written_as_the_agent_reported_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1 — ``session.json`` records the self-report, verbatim and bounded.

    ``agentInfo`` and the advertised capability *keys* are child-controlled free
    text that ``judge_initialize`` deliberately does not gate — a self-report is
    evidence, never identity, so there is nothing to check it against. It is
    also the one Run sink whose lifetime exceeds the Run, which is why what
    lands there is asserted directly rather than inferred.

    The Session root is scanned, not the Run root: this is the sink the
    Run-scoped proofs do not cover.
    """
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.entry = _sentinel_entry()

    script = dict(FIRST_RUN_SCRIPT)
    script["agent_info"] = {"name": f"agent-{ENV_SENTINEL}", "version": ENV_SENTINEL}
    result = harness.run("run-0001", script, _request("provider/base", "high"))
    assert result.status is AgentRunStatus.COMPLETED

    record = harness.record()
    assert record.native_last_agent_info_name == f"agent-{ENV_SENTINEL}"
    assert record.native_last_agent_info_version == ENV_SENTINEL
    assert ENV_SENTINEL in _session_root_text(harness)
    # The advertised capability *keys* come from the SDK's own capability
    # model, so they are a closed set rather than child-chosen free text.
    assert record.native_last_advertised_capabilities == [
        "loadSession",
        "mcpCapabilities",
        "promptCapabilities",
    ]


def test_a1_self_report_drift_is_reported_across_two_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observation is evidence, so drift is a warning and never a refusal."""
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.entry = _sentinel_entry()

    first = dict(FIRST_RUN_SCRIPT)
    first["agent_info"] = {"name": "fake-acp-agent", "version": "1.0.0"}
    assert harness.run(
        "run-0001", first, _request("provider/base", "high")
    ).status is AgentRunStatus.COMPLETED

    second = dict(HAPPY_SWITCH_SCRIPT)
    second["agent_info"] = {"name": "fake-acp-agent", "version": "2.0.0"}
    assert harness.run(
        "run-0002", second, _request("kimi-for-coding/k3", "max")
    ).status is AgentRunStatus.COMPLETED

    warnings = [
        event
        for event in _events(harness, "run-0002")
        if event["type"] == "policy_warning"
    ]
    assert [warning["code"] for warning in warnings] == ["AGENT_SELF_REPORT_CHANGED"]
    assert ENV_SENTINEL not in _session_root_text(harness)


def test_a11_the_persisted_observation_is_never_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recorded, and load-bearing for nothing.

    The Session record carries the previous observation so drift can be
    *reported*. It must not become an identity field, a gate, or an epoch: a Run
    whose agent reports something new still binds, and the operator's epoch is
    untouched.
    """
    from agent_run_supervisor.session import (
    QUARANTINE_DISPATCH_OBSERVATION_LOST,
        LEGACY_SESSION_IDENTITY_FIELDS,
        validate_native_binding,
    )

    harness = SwitchHarness(tmp_path, monkeypatch)
    first = dict(FIRST_RUN_SCRIPT)
    first["agent_info"] = {"name": "fake-acp-agent", "version": "1.0.0"}
    harness.run("run-0001", first, _request("provider/base", "high"))

    record = harness.record()
    assert record.native_last_agent_info_version == "1.0.0"
    assert record.native_session_epoch is None
    for retired in LEGACY_SESSION_IDENTITY_FIELDS:
        assert getattr(record, retired) is None

    # The identity gate does not read it: a record whose observation differs
    # from anything still binds.
    from agent_run_supervisor.native_acp.spec import resolve_workspace_binding

    validate_native_binding(
        record,
        profile=_profile(),
        workspace_result=resolve_workspace_binding(root=harness.workspace),
        owner="hermes",
        namespace="hermes/doc-check",
        expected_agent_id="fake-agent",
    )


def test_a11_no_observation_is_ever_hashed_or_bumped_into_an_epoch() -> None:
    """Structural: recording an observation must not grow into a gate."""
    from pathlib import Path as _Path

    from agent_run_supervisor import session as session_mod
    from agent_run_supervisor.native_acp import observation, run_task

    for module in (session_mod, observation, run_task):
        text = _Path(module.__file__).read_text(encoding="utf-8")
        for banned in (
            "agent_info_hash",
            "observation_hash",
            "bump_epoch",
            "session_epoch + 1",
            "session_epoch += 1",
        ):
            assert banned not in text, f"{module.__name__} carries {banned!r}"


def test_load_reuse_happy_path_keeps_external_id_and_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    result = harness.run("run-0002", HAPPY_SWITCH_SCRIPT, _request("kimi-for-coding/k3", "max"))
    assert result.status is AgentRunStatus.COMPLETED

    methods = harness.methods("run-0002")
    assert "session/load" in methods
    assert "session/new" not in methods  # no new-session event on the load path
    assert "session/prompt" in methods

    record = harness.record()
    assert record.agent_session_id == "fake-external-session-1"  # unchanged
    assert record.last_effective_model == "kimi-for-coding/k3"
    assert record.last_effective_effort == "max"
    assert record.quarantine is None

    payload = json.loads(
        (harness.root / "native-runs" / "run-0002" / "result.json").read_text()
    )
    assert payload["status"] == "completed"


def test_load_replay_history_never_enters_current_final_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B1 (2026-07-24): official adapters replay conversation history as
    # agent_message_chunk updates before session/prompt. Only chunks causally
    # belonging to the current prompt/Turn may reach this Run's final_message
    # (PRD R9).
    #
    # Replay separation extends that to the whole Run: history is not this
    # Run's execution, so it produces no per-event evidence either. It is not
    # discarded silently — one bounded summary records the aggregate — but a
    # replayed chunk is never an ``agent_message_delta`` of this Run. See
    # ``test_session_replay_backpressure.py`` for the full contract.
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["replay_on_load"] = ["HISTORY_ASSISTANT_ONE ", "HISTORY_ASSISTANT_TWO "]
    result = harness.run(
        "run-0002", script, _request("kimi-for-coding/k3", "max")
    )
    assert result.status is AgentRunStatus.COMPLETED

    run_dir = harness.root / "native-runs" / "run-0002"
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["final_message"] == "RUN2_OK"
    assert payload["truncated"] is False

    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]
    delta_lengths = sorted(
        event["text_length"]
        for event in events
        if event["type"] == "agent_message_delta"
    )
    assert delta_lengths == [len("RUN2_OK")]
    summaries = [
        event for event in events if event["type"] == "session_replay_summary"
    ]
    assert len(summaries) == 1
    assert summaries[0]["updates"] == 2
    assert summaries[0]["by_kind"] == {"agent_message_chunk": 2}


def test_load_replay_never_consumes_current_final_message_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B1 (2026-07-24): replayed history must not eat the bounded
    # final-message budget of the current Turn — accumulation and truncation
    # operate on current-Turn chunks only.
    from agent_run_supervisor.result import MAX_FINAL_MESSAGE_BYTES

    current_text = "C" * 40_000
    replay_text = "H" * 40_000
    assert len(replay_text) + len(current_text) > MAX_FINAL_MESSAGE_BYTES
    assert len(current_text) < MAX_FINAL_MESSAGE_BYTES

    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["replay_on_load"] = [replay_text]
    script["final_message"] = current_text
    result = harness.run(
        "run-0002", script, _request("kimi-for-coding/k3", "max")
    )
    assert result.status is AgentRunStatus.COMPLETED

    payload = json.loads(
        (harness.root / "native-runs" / "run-0002" / "result.json").read_text()
    )
    assert payload["final_message"] == current_text
    assert payload["truncated"] is False
    assert payload["truncate_reason"] is None


def test_current_turn_chunk_survives_fast_agent_race_with_post_send_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B1 happens-before (2026-07-24 focused review): SDK 0.12.1 still notifies
    # the outgoing stream observer only after the transport send has written
    # and drained the session/prompt frame. A fast agent can have its genuine
    # current-turn update processed by the receive loop inside that window; a
    # boundary snapshotted by the post-send observer then counts that update
    # as pre-prompt and final_message silently loses the chunk. Force exactly
    # that interleaving: hold the prompt send's return (the SDK request
    # coroutine's post-write suspension point) until the receive loop has
    # observed one more incoming session/update than existed when the prompt
    # bytes went out.
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    from acp.task import sender as sender_module

    from agent_run_supervisor.native_acp.driver import NativeAcpDriver

    state: dict[str, object] = {
        "incoming": 0,
        "baseline": None,
        "raced": asyncio.Event(),
    }
    original_send = sender_module.MessageSender.send

    async def racing_send(self, payload):
        if payload.get("method") != "session/prompt":
            await original_send(self, payload)
            return
        state["baseline"] = state["incoming"]
        await original_send(self, payload)
        # The prompt frame is written+drained; the SDK request coroutine sits
        # exactly here while the receive loop processes the agent's reply.
        await asyncio.wait_for(state["raced"].wait(), 30)

    original_observe = NativeAcpDriver._observe_stream

    def observe_and_signal(self, event):
        original_observe(self, event)
        direction = getattr(getattr(event, "direction", None), "name", "")
        if direction != "INCOMING":
            return
        if (event.message or {}).get("method") != "session/update":
            return
        state["incoming"] = state["incoming"] + 1
        baseline = state["baseline"]
        if baseline is not None and state["incoming"] > baseline:
            state["raced"].set()

    monkeypatch.setattr(sender_module.MessageSender, "send", racing_send)
    monkeypatch.setattr(NativeAcpDriver, "_observe_stream", observe_and_signal)

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["replay_on_load"] = ["HISTORY_ASSISTANT_ONE "]
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))
    assert result.status is AgentRunStatus.COMPLETED

    payload = json.loads(
        (harness.root / "native-runs" / "run-0002" / "result.json").read_text()
    )
    # The forced race must not cost the Run its genuine current-turn chunk,
    # and replay must stay excluded.
    assert payload["final_message"] == "RUN2_OK"
    assert payload["truncated"] is False


def test_silent_new_on_load_is_detected_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["silent_new_on_load"] = True
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.FAILED
    assert "session/prompt" not in harness.methods("run-0002")
    payload = json.loads(
        (harness.root / "native-runs" / "run-0002" / "result.json").read_text()
    )
    assert payload["detail_code"] == "SILENT_SESSION_RECREATION"
    record = harness.record()
    assert record.quarantine is None  # the real session was never prompted
    assert record.last_effective_model == "provider/base"


def test_load_capability_missing_fails_and_escalates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["load_session_advertised"] = False
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads(
        (harness.root / "native-runs" / "run-0002" / "result.json").read_text()
    )
    # An agent that does not advertise ``loadSession`` fails a *declared
    # contract term* of a profile whose session semantics require a real load,
    # so it lands inside the closed five-member observation-refusal set rather
    # than adding a sixth code of its own.
    assert payload["detail_code"] == "CAPABILITY_MISSING"
    assert "session/load" not in harness.methods("run-0002")
    assert "session/prompt" not in harness.methods("run-0002")
    assert harness.record().quarantine is None


def test_set_model_rejected_rolls_back_and_reopens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["reject_set_config_values"] = ["kimi-for-coding/k3"]
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.FAILED
    assert "session/prompt" not in harness.methods("run-0002")
    record = harness.record()
    assert record.quarantine is None  # rollback proven ⇒ session re-opened
    assert record.last_effective_model == "provider/base"
    assert record.last_effective_effort == "high"


def test_effort_missing_post_model_rolls_back_and_reopens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["post_model_options_by_value"] = {
        "kimi-for-coding/k3": K3_SET_WITHOUT_EFFORT,
        "provider/base": BASE_SET,
    }
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.FAILED
    record = harness.record()
    assert record.quarantine is None
    assert record.last_effective_model == "provider/base"
    events = (harness.root / "native-runs" / "run-0002" / "events.jsonl").read_text()
    assert "config_rollback_proven" in events
    # Observed partial changes are recorded as evidence: the failure-path
    # effective.json carries the discovery snapshots gathered before failing.
    effective = json.loads(
        (harness.root / "native-runs" / "run-0002" / "effective.json").read_text()
    )
    labels = [snapshot["label"] for snapshot in effective["discovery_snapshots"]]
    assert "initial" in labels
    assert "post_model" in labels


def test_inexact_readback_rolls_back_and_reopens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["wrong_readback"] = {"effort": "high"}
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.FAILED
    assert "session/prompt" not in harness.methods("run-0002")
    record = harness.record()
    assert record.quarantine is None
    assert record.last_effective_effort == "high"


def test_unprovable_rollback_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["post_model_options_by_value"] = {
        "kimi-for-coding/k3": K3_SET_WITHOUT_EFFORT,
        "provider/base": BASE_SET,
    }
    script["reject_set_config_values"] = ["provider/base"]
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.FAILED
    record = harness.record()
    assert record.quarantine is not None
    assert record.quarantine["source_run_id"] == "run-0002"
    events = (harness.root / "native-runs" / "run-0002" / "events.jsonl").read_text()
    assert "config_rollback_failed" in events


def test_quarantined_session_refuses_new_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()
    storage.native_session_store(harness.root).mark_quarantined(
        "sess-switch-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-0002"
    )

    result = harness.run(
        "run-0003", HAPPY_SWITCH_SCRIPT, _request("kimi-for-coding/k3", "max")
    )
    assert result.status is AgentRunStatus.FAILED
    # Refused before any agent spawn: the fake never ran.
    assert harness.methods("run-0003") == []
    assert harness.record().quarantine is not None


def test_retry_of_run_id_leaves_original_records_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["post_model_options_by_value"] = {
        "kimi-for-coding/k3": K3_SET_WITHOUT_EFFORT,
        "provider/base": BASE_SET,
    }
    script["reject_set_config_values"] = ["provider/base"]
    failed = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))
    assert failed.status is AgentRunStatus.FAILED
    original_result = harness.root / "native-runs" / "run-0002" / "result.json"
    original_bytes = original_result.read_bytes()
    quarantined_record = harness.record()

    successor = harness.run(
        "run-0004",
        HAPPY_SWITCH_SCRIPT,
        _request("kimi-for-coding/k3", "max", session_id="sess-switch-2"),
        retry_of_run_id="run-0002",
    )
    assert successor.status is AgentRunStatus.COMPLETED
    spec = json.loads(
        (harness.root / "native-runs" / "run-0004" / "spec.json").read_text()
    )
    assert spec["retry_of_run_id"] == "run-0002"
    # The original terminal fact and quarantined record are untouched.
    assert original_result.read_bytes() == original_bytes
    record = harness.record()
    assert record == quarantined_record

"""C8 L2: RunTask vertical over the fake agent — admission, double markers,
finalization, write-once terminal facts, and the seeded-legacy isolation
vertical (the L2 half of the C6 regression)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.event_store import EventStore, RunHandle
from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import profile as profile_module
from agent_run_supervisor.native_acp import launch_permissions, storage
from agent_run_supervisor.native_acp.driver import NativeAcpDriver
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.profile import (
    AcpCompatProfile,
    ProfileRegistry,
)
from agent_run_supervisor.native_acp.run_task import (
    DISPATCH_STARTED_MARKER,
    NativeRunTaskError,
    RunTask,
)
from agent_run_supervisor.native_acp.spec import (
    AgentRunRequest,
    InputRef,
    RunLimits,
    RunSpecAssembler,
    resolve_run_environment,
)
from agent_run_supervisor.session import SessionNotFoundError, SessionStore
from agent_run_supervisor.session import QUARANTINE_DISPATCH_OBSERVATION_LOST, derive_session_id_for_run

FAKE_AGENT_PATH = Path(__file__).with_name("fake_agent.py")
TEST_PROFILE_ID = "fake-agent-v1"
TEST_AGENT_ID = "fake-agent"

HAPPY_SCRIPT = {
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
            "options": [
                {"value": "high", "name": "High"},
                {"value": "max", "name": "Max"},
            ],
        },
    ],
    "final_message": "FAKE_AGENT_OK",
}


def _test_profile(**overrides) -> AcpCompatProfile:
    """An ACP-v1 conformance profile for the fake agent.

    It freezes ACP semantics only. Which command the fake agent is, and which
    environment names it needs, are operator facts — carried by the registry
    entry below, exactly as a real deployment carries them.
    """
    kwargs = dict(
        profile_id=TEST_PROFILE_ID,
        revision=1,
        acp_protocol_version="1",
        required_capabilities=(),
        base_allowlist=("PATH", "HOME", "FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
        requires_session_load=False,
    )
    kwargs.update(overrides)
    return AcpCompatProfile(**kwargs)


def _test_entry(**overrides) -> AgentEntry:
    kwargs = dict(
        agent_id=TEST_AGENT_ID,
        profile_id=TEST_PROFILE_ID,
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
    )
    kwargs.update(overrides)
    return AgentEntry(**kwargs)


def _request(**overrides) -> AgentRunRequest:
    kwargs = dict(
        owner="hermes",
        namespace="hermes/doc-check",
        agent_id=TEST_AGENT_ID,
        session_id="sess-native-1",
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),),
        requested_model="kimi-for-coding/k3",
        requested_effort="max",
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


DEFAULT_EXTERNAL_ID = "fake-external-session-1"


def _seed_bound_session(kwargs: dict, external_id: str) -> None:
    """Arrange the precondition a reuse Run requires (B1 / PRD R4).

    A reuse request now opens its Session **existing-only** and needs a stored
    external id, so no Run can conjure the record it reuses. The harness
    therefore creates exactly the record an earlier Run would have left behind,
    with the identical identity field set this Run will compute — a mismatch
    here is a real refusal, not a fixture convenience. A record the test seeded
    itself is left untouched.
    """
    request = kwargs["request"]
    if request.session_id is None:
        return
    session_id = request.session_id
    root = kwargs.get("supervisor_root")
    store = kwargs.get("session_store") if root is None else (
        storage.native_session_store(root)
    )
    if not isinstance(store, SessionStore):
        # No usable store handed to this construction — the RunTask constructor
        # is the thing under test and will refuse on its own terms.
        return
    try:
        store.open_session(session_id)
        return
    except SessionNotFoundError:
        pass
    entry = kwargs["agent_entry"]
    assembler = RunSpecAssembler(request)
    instance = assembler.resolve_agent(entry, registry=kwargs["registry"])
    assembler.bind_workspace(root=kwargs["workspace_root"], cwd=kwargs.get("cwd"))
    # A profile that selects a launch-permission policy requires its material
    # before a launch can be sealed, so the seeder supplies real material —
    # the same invariant production obeys, not a fixture exemption. The
    # capabilities here are the seeder's own: no Session identity field derives
    # from the launch snapshot, so this throwaway seal only has to be valid.
    policy_id = instance.launch_permission_policy_id
    seed_root: Path | None = None
    material = None
    if policy_id is not None:
        seed_root = Path(tempfile.mkdtemp(prefix="ars-seed-launch-permission-"))
        material = launch_permissions.materialize(
            policy_id, capabilities=("read",), run_dir=seed_root
        )
    try:
        assembler.resolve_launch(
            environment=resolve_run_environment(
                arsd_env=dict(os.environ),
                profile=instance.profile,
                entry=entry,
                launch_permission=() if material is None else material.env_pairs,
            ),
            launch_permission=material,
        )
        spec = assembler.seal(
            run_id="run-seed-0",
            submitted_at="2026-07-21T00:00:00+00:00",
            retry_of_run_id=None,
        )
    finally:
        if material is not None:
            launch_permissions.discard(material)
        if seed_root is not None:
            shutil.rmtree(seed_root, ignore_errors=True)
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
        agent_session_id=external_id,
    )


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        script: dict,
    ) -> None:
        self.root = tmp_path / ".agent-run-supervisor"
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir(exist_ok=True)
        self.trace = tmp_path / "fake-agent-trace.log"
        monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
        monkeypatch.setenv("FAKE_AGENT_TRACE", str(self.trace))
        self.registry = ProfileRegistry((_test_profile(),))
        self.entry = _test_entry()
        self.external_id = script.get("session_id", DEFAULT_EXTERNAL_ID)

    def task(
        self,
        *,
        run_id: str = "run-0001",
        request=None,
        seed_session: bool = True,
        **overrides,
    ) -> RunTask:
        """Build one RunTask.

        ``seed_session`` arranges the already-existing record a *reuse* request
        needs, for tests whose subject is something other than where that record
        came from. A test that is about continuity itself must pass
        ``seed_session=False`` and name the Session its own first Run published —
        otherwise the seeder can hide a broken publication behind a green load.
        """
        kwargs = dict(
            request=request or _request(),
            prompt_text="hello agent",
            run_id=run_id,
            workspace_root=self.workspace,
            registry=self.registry,
            agent_entry=self.entry,
            supervisor_root=self.root,
            submitted_at="2026-07-21T00:00:00+00:00",
        )
        kwargs.update(overrides)
        if seed_session:
            _seed_bound_session(kwargs, self.external_id)
        return RunTask(**kwargs)

    def run_dir(self, run_id: str = "run-0001") -> Path:
        return self.root / "native-runs" / run_id

    def session_store(self) -> SessionStore:
        return storage.native_session_store(self.root)

    def methods_seen(self) -> list[str]:
        if not self.trace.exists():
            return []
        return [line for line in self.trace.read_text().splitlines() if line]


def _run(task: RunTask):
    async def case():
        return await asyncio.wait_for(task.run(), 60)

    return asyncio.run(case())


@pytest.mark.parametrize(
    ("session_id", "expected_method"),
    [(None, "session/new"), ("sess-native-1", "session/load")],
    ids=("new", "load"),
)
def test_symlink_workspace_is_canonical_on_the_agent_session_wire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_id: str | None,
    expected_method: str,
) -> None:
    capture = tmp_path / "session-cwd.jsonl"
    script = dict(HAPPY_SCRIPT)
    script["capture_cwd_path"] = str(capture)
    harness = Harness(tmp_path, monkeypatch, script)
    real_cwd = harness.workspace / "nested"
    real_cwd.mkdir()
    linked_workspace = tmp_path / "workspace-link"
    linked_workspace.symlink_to(harness.workspace, target_is_directory=True)

    result = _run(
        harness.task(
            request=_request(session_id=session_id),
            workspace_root=linked_workspace,
            cwd=str(linked_workspace / "nested"),
        )
    )

    assert result.status is AgentRunStatus.COMPLETED
    captured = [json.loads(line) for line in capture.read_text().splitlines()]
    assert captured == [{"method": expected_method, "cwd": str(real_cwd.resolve())}]
    spec = json.loads((harness.run_dir() / "spec.json").read_text())
    assert spec["workspace"]["canonical_root"] == str(harness.workspace.resolve())
    assert spec["workspace"]["cwd"] == str(real_cwd.resolve())


def test_happy_vertical_produces_full_artifact_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    run_dir = harness.run_dir()
    for artifact in (
        "spec.json",
        "launch.json",
        "effective.json",
        "events.jsonl",
        "result.json",
        "progress.json",
        "prompt-dispatch-started",
        "prompt-accepted",
        "stderr.log",
        "redaction-report.json",
    ):
        assert (run_dir / artifact).exists(), artifact

    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["status"] == "completed"
    assert payload["stop_reason"] == "end_turn"
    assert payload["final_message"] == "FAKE_AGENT_OK"
    assert payload["retryable"] is False
    assert payload["business_verdict"] is None

    dispatch = json.loads((run_dir / "prompt-dispatch-started").read_text())
    accepted = json.loads((run_dir / "prompt-accepted").read_text())
    assert dispatch["marker"] == "prompt-dispatch-started"
    assert accepted["marker"] == "prompt-accepted"
    assert dispatch["ordinal"] < accepted["ordinal"]

    effective = json.loads((run_dir / "effective.json").read_text())
    assert effective["effective_model"] == "kimi-for-coding/k3"
    assert effective["effective_effort"] == "max"
    assert effective["agent_session_id"] == "fake-external-session-1"
    assert effective["load_session_advertised"] is True

    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]
    sequences = [event["seq"] for event in events]
    assert sequences == sorted(sequences)
    families = {event["type"] for event in events}
    assert "run_started" in families
    assert "agent_message_delta" in families
    assert "FAKE_AGENT_OK" not in (run_dir / "events.jsonl").read_text()

    store = harness.session_store()
    record = store.open_session("sess-native-1")
    assert record.agent_session_id == "fake-external-session-1"
    assert record.quarantine is None
    assert record.last_effective_model == "kimi-for-coding/k3"
    assert record.last_effective_effort == "max"
    assert not (harness.root / "native-sessions" / "sess-native-1" / "lock.json").exists()

    progress = json.loads((run_dir / "progress.json").read_text())
    assert progress["state"] == "completed"
    assert "session/prompt" in harness.methods_seen()


def test_zero_update_turn_still_creates_prompt_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = dict(HAPPY_SCRIPT)
    script["final_message"] = ""
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())
    # The marker means only local frame write + drain: no first agent update
    # is required for it to exist.
    assert (harness.run_dir() / "prompt-accepted").exists()
    assert result.status is AgentRunStatus.COMPLETED


def test_observation_lost_finalizes_unknown_despite_both_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)

    original = NativeAcpDriver.prompt_once

    async def torn_prompt(self, text: str):
        for hook in list(self._prompt_frame_hooks):
            hook()
        raise RuntimeError("observation torn mid-turn")

    monkeypatch.setattr(NativeAcpDriver, "prompt_once", torn_prompt)
    task = harness.task()
    result = _run(task)
    monkeypatch.setattr(NativeAcpDriver, "prompt_once", original)

    run_dir = harness.run_dir()
    assert (run_dir / "prompt-dispatch-started").exists()
    assert (run_dir / "prompt-accepted").exists()
    payload = json.loads((run_dir / "result.json").read_text())
    # Both markers present, still unknown: the marker never upgrades certainty.
    assert result.status is AgentRunStatus.UNKNOWN
    assert payload["status"] == "unknown"
    assert payload["retryable"] is False
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None
    assert record.quarantine["source_run_id"] == "run-0001"
    assert not (harness.root / "native-sessions" / "sess-native-1" / "lock.json").exists()


def test_kill_after_dispatch_fails_and_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = dict(HAPPY_SCRIPT)
    script["exit_before_prompt_response"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    run_dir = harness.run_dir()
    assert (run_dir / "prompt-dispatch-started").exists()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["status"] == "failed"
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None


def test_spawn_failure_is_pre_dispatch_failed_session_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    harness.entry = _test_entry(command="/nonexistent/native-agent-binary")
    result = _run(harness.task())
    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    # Classified from the errno ARS observed, never from a pre-flight check.
    assert payload["detail_code"] == "COMMAND_NOT_FOUND"
    run_dir = harness.run_dir()
    assert not (run_dir / "prompt-dispatch-started").exists()
    assert not (run_dir / "prompt-accepted").exists()
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is None  # session stays active on 0-Turn failure
    assert not (harness.root / "native-sessions" / "sess-native-1" / "lock.json").exists()


def test_fidelity_failure_is_zero_turn_failed_session_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A discovery-time fidelity failure: the model selector is missing from the
    # advertised set, so no set_config_option is ever dispatched. Nothing can
    # have partially switched, so the reused Session stays active — a *post*-set
    # failure on a reused Session is the separate rollback/quarantine row.
    script = dict(HAPPY_SCRIPT)
    script["initial_options"] = [
        option for option in HAPPY_SCRIPT["initial_options"] if option["id"] != "model"
    ]
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())
    assert result.status is AgentRunStatus.FAILED
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    assert "session/prompt" not in harness.methods_seen()
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is None
    assert record.last_effective_model is None


def test_injected_normalizer_exception_is_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.events import NativeAcpEventNormalizer

    def boom(self, update):
        raise RuntimeError("normalizer exploded")

    monkeypatch.setattr(NativeAcpEventNormalizer, "normalize_update", boom)
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    result = _run(harness.task())
    # Controlled terminal state, never propagation.
    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["status"] == "failed"


def test_write_once_terminal_facts_never_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    _run(harness.task())
    run_dir = harness.run_dir()
    for artifact in (
        "spec.json",
        "launch.json",
        "effective.json",
        "result.json",
        "prompt-dispatch-started",
        "prompt-accepted",
    ):
        path = run_dir / artifact
        before = path.read_bytes()
        with pytest.raises(FileExistsError):
            storage.write_once_json(path, {"attacker": True})
        assert path.read_bytes() == before, artifact


def test_concurrent_finalizers_produce_exactly_one_winner(tmp_path: Path) -> None:
    target = tmp_path / "result.json"

    async def case() -> list:
        def attempt(index: int):
            return storage.write_once_json(target, {"winner": index})

        results = await asyncio.gather(
            asyncio.to_thread(attempt, 1),
            asyncio.to_thread(attempt, 2),
            return_exceptions=True,
        )
        return results

    results = asyncio.run(case())
    winners = [entry for entry in results if not isinstance(entry, Exception)]
    losers = [entry for entry in results if isinstance(entry, FileExistsError)]
    assert len(winners) == 1
    assert len(losers) == 1


def test_seeded_legacy_roots_stay_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    legacy_sessions = harness.root / "sessions" / "sess-native-1"
    legacy_sessions.mkdir(parents=True)
    (legacy_sessions / "session.json").write_bytes(b"{ poisoned legacy record")
    legacy_runs = harness.root / "runs" / "run-0001"
    legacy_runs.mkdir(parents=True)
    (legacy_runs / "result.json").write_bytes(b'{"status": "completed"}')

    def snapshot(root: Path):
        return {
            str(path.relative_to(root)): (
                path.read_bytes() if path.is_file() else None
            )
            for path in sorted(root.rglob("*"))
        }

    before_sessions = snapshot(harness.root / "sessions")
    before_runs = snapshot(harness.root / "runs")

    result = _run(harness.task())
    assert result.status is AgentRunStatus.COMPLETED

    assert snapshot(harness.root / "sessions") == before_sessions
    assert snapshot(harness.root / "runs") == before_runs
    assert (harness.root / "native-runs" / "run-0001" / "result.json").exists()

    # The kill-after-dispatch branch writes quarantine state only into the
    # native-sessions record.
    script = dict(HAPPY_SCRIPT)
    script["exit_before_prompt_response"] = True
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
    request = _request(session_id="sess-native-2")
    result2 = _run(harness.task(run_id="run-0002", request=request))
    assert result2.status is AgentRunStatus.FAILED
    assert snapshot(harness.root / "sessions") == before_sessions
    assert snapshot(harness.root / "runs") == before_runs
    record = harness.session_store().open_session("sess-native-2")
    assert record.quarantine is not None


def test_constructor_refuses_non_native_rooted_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    from agent_run_supervisor.event_store import EventStore

    legacy_session_store = SessionStore(base_dir=harness.root / "sessions")
    legacy_event_store = EventStore(base_dir=harness.root / "runs")
    with pytest.raises(NativeRunTaskError):
        RunTask(
            request=_request(),
            prompt_text="x",
            run_id="run-bad",
            workspace_root=harness.workspace,
            registry=harness.registry,
            session_store=legacy_session_store,
            event_store=legacy_event_store,
        )


def test_retry_of_run_id_never_mutates_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    _run(harness.task())
    original_result = harness.run_dir("run-0001") / "result.json"
    before = original_result.read_bytes()

    request = _request(session_id="sess-native-3")
    result = _run(
        harness.task(
            run_id="run-0002", request=request, retry_of_run_id="run-0001"
        )
    )
    assert result.status is AgentRunStatus.COMPLETED
    spec = json.loads((harness.run_dir("run-0002") / "spec.json").read_text())
    assert spec["retry_of_run_id"] == "run-0001"
    assert original_result.read_bytes() == before


def test_delayed_update_dispatch_is_captured_before_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The SDK receive loop resolves the prompt response future without waiting
    # for a queued session/update handler to *finish*. This injects a dispatch
    # delay into the client callback (the exact raced hop) and proves the turn
    # still captures the final message and normalized event BEFORE the terminal
    # result is written, rather than by a lucky scheduling order.
    #
    # This is the whole-stack pin and it deliberately leaves both barriers in
    # place, so it does not attribute the outcome to either. Attribution to
    # ARS's own barrier is the next test's job: the locked SDK also waits here
    # (0.12.1 ``_SessionUpdateTracker``), and a delay injected *inside*
    # ``session_update`` is registered with that tracker before it starts, so
    # this case alone would stay green with ARS's barrier removed.
    from agent_run_supervisor.native_acp.client import NativeAcpClient

    original_session_update = NativeAcpClient.session_update

    async def delayed_session_update(self, session_id, update, **kwargs):
        await asyncio.sleep(0.3)
        return await original_session_update(self, session_id, update, **kwargs)

    monkeypatch.setattr(NativeAcpClient, "session_update", delayed_session_update)

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED, result.payload
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "FAKE_AGENT_OK"
    events = [
        json.loads(line)
        for line in (harness.run_dir() / "events.jsonl").read_text().splitlines()
    ]
    assert any(event["type"] == "agent_message_delta" for event in events)


def test_ars_delivery_barrier_alone_captures_a_delayed_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same raced hop as the test above, with the locked SDK's own wait taken
    # out of the path so ARS's observed-versus-completed barrier is the only
    # thing that can preserve the delayed final update.
    #
    # 0.12.1's ``ClientSideConnection.prompt`` awaits
    # ``_SessionUpdateTracker.wait``, which covers every handler that already
    # ran its first step — which a delay injected inside ``session_update``
    # always has. Left in, it satisfies the whole-stack assertion by itself and
    # masks a regression in ARS's barrier. Neutralised, the turn returns as soon
    # as the prompt response resolves, finalization runs, and ``driver.close()``
    # cancels the still-sleeping notification task: the chunk reaches no sink,
    # ``final_message`` is empty and no ``agent_message_delta`` is written unless
    # ARS waited for the completion counter itself.
    #
    # ARS keeps its own barrier because it is the strict superset: the SDK's
    # wait cannot see a frame whose handler task the receive loop created but
    # has not yet stepped.
    from acp.client.connection import _SessionUpdateTracker

    from agent_run_supervisor.native_acp.client import NativeAcpClient

    original_session_update = NativeAcpClient.session_update

    async def delayed_session_update(self, session_id, update, **kwargs):
        await asyncio.sleep(0.3)
        return await original_session_update(self, session_id, update, **kwargs)

    monkeypatch.setattr(NativeAcpClient, "session_update", delayed_session_update)

    sdk_waits: list[str] = []

    async def neutralised_sdk_wait(self, session_id):
        # Reached, recorded, and returns without awaiting anything.
        sdk_waits.append(session_id)

    monkeypatch.setattr(_SessionUpdateTracker, "wait", neutralised_sdk_wait)

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    result = _run(harness.task())

    # If the SDK ever stops routing the prompt through this seam, the
    # neutralisation is silently a no-op and this case would quietly weaken
    # back into the whole-stack one — so prove the seam was on the path.
    assert sdk_waits, "the SDK-side prompt wait was never reached"

    assert result.status is AgentRunStatus.COMPLETED, result.payload
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "FAKE_AGENT_OK"
    events = [
        json.loads(line)
        for line in (harness.run_dir() / "events.jsonl").read_text().splitlines()
    ]
    assert any(event["type"] == "agent_message_delta" for event in events)


def test_fs_read_serves_workspace_file_never_supervisor_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Controller reproduction: workspace and supervisor cwd each contain
    # same.txt; the mediated relative read must return the workspace-bound
    # file, never the supervisor-cwd one.
    script = dict(HAPPY_SCRIPT)
    script["fs_read_path"] = "same.txt"
    harness = Harness(tmp_path, monkeypatch, script)
    (harness.workspace / "same.txt").write_text("WORKSPACE_TRUTH", encoding="utf-8")
    decoy_cwd = tmp_path / "decoy-supervisor-cwd"
    decoy_cwd.mkdir()
    (decoy_cwd / "same.txt").write_text("SUPERVISOR_DECOY", encoding="utf-8")
    monkeypatch.chdir(decoy_cwd)

    result = _run(harness.task())
    assert result.status is AgentRunStatus.COMPLETED, result.payload
    assert "FS_CONTENT:WORKSPACE_TRUTH" in result.payload["final_message"]
    assert "SUPERVISOR_DECOY" not in result.payload["final_message"]
    events = (harness.run_dir() / "events.jsonl").read_text(encoding="utf-8")
    assert '"requested_op": "fs_read"' in events.replace("'", '"') or "fs_read" in events
    assert '"decision": "allow"' in events or "allow" in events


def test_fs_read_outside_workspace_is_denied_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    script = dict(HAPPY_SCRIPT)
    script["fs_read_path"] = str(outside)
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task())
    assert result.status is AgentRunStatus.COMPLETED, result.payload
    assert "FS_DENIED" in result.payload["final_message"]
    assert "OUTSIDE_SECRET" not in result.payload["final_message"]
    events = (harness.run_dir() / "events.jsonl").read_text(encoding="utf-8")
    assert "fs_read" in events
    assert "deny" in events


async def _wait_until(condition, *, timeout: float = 30.0, message: str = "") -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    pytest.fail(message or "condition never became true")


def _assert_pid_gone_sync(pid: int, *, timeout: float = 15.0) -> None:
    import os
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"child {pid} survived cancellation")


def _recording_spawn(monkeypatch: pytest.MonkeyPatch) -> list:
    from agent_run_supervisor.native_acp import run_task as run_task_module

    spawned: list = []
    real_spawn = run_task_module.spawn_managed_process

    async def wrapper(**kwargs):
        proc = await real_spawn(**kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(run_task_module, "spawn_managed_process", wrapper)
    return spawned


def test_cancellation_before_dispatch_finalizes_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = dict(HAPPY_SCRIPT)
    script["hang_on_set_config"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)

    async def case() -> None:
        task = asyncio.ensure_future(harness.task().run())
        await _wait_until(lambda: bool(spawned), message="child never spawned")
        await asyncio.sleep(0.5)  # sit inside the hanging set_config await
        task.cancel()
        # Cancellation is never hidden, and finalization is bounded.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 30)

    asyncio.run(case())

    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["detail_code"] == "SUPERVISOR_CANCELLED"
    assert payload["retryable"] is False
    assert not (run_dir / "prompt-dispatch-started").exists()
    assert not (run_dir / "prompt-accepted").exists()
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is None  # 0-Turn cancel keeps the session reusable
    assert not (
        harness.root / "native-sessions" / "sess-native-1" / "lock.json"
    ).exists()
    _assert_pid_gone_sync(spawned[0].pid)


def test_cancellation_after_dispatch_is_conservative_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"

    async def case() -> None:
        task = asyncio.ensure_future(harness.task().run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 30)

    asyncio.run(case())

    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    # Dispatched with no reliable ACP terminal: PRD R5 — unknown, never a
    # cancelled/completed overclaim.
    assert payload["status"] == "unknown"
    assert payload["status"] not in ("cancelled", "completed")
    assert payload["detail_code"] == "SUPERVISOR_CANCELLED"
    assert payload["retryable"] is False
    assert (run_dir / "prompt-dispatch-started").exists()
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None
    assert not (
        harness.root / "native-sessions" / "sess-native-1" / "lock.json"
    ).exists()
    _assert_pid_gone_sync(spawned[0].pid)


def test_repeated_cancellation_not_hidden_and_emergency_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.event_writer import EventWriter

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"

    async def case() -> None:
        entered_close = asyncio.Event()

        async def hanging_close(self) -> None:
            entered_close.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(EventWriter, "close", hanging_close)
        task = asyncio.ensure_future(harness.task().run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        task.cancel()  # repeated cancellation while finalization is stuck
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 30)

    asyncio.run(case())

    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    # Emergency path stays conservative: dispatched + no reliable terminal
    # is unknown/quarantined/retryable=false, never completed.
    assert payload["status"] == "unknown"
    assert payload["retryable"] is False
    assert payload["detail_code"] == "EMERGENCY_FINALIZE"
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None
    assert not (
        harness.root / "native-sessions" / "sess-native-1" / "lock.json"
    ).exists()
    _assert_pid_gone_sync(spawned[0].pid)


def test_a_create_leaves_one_durable_reusable_session_at_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: the Run terminates and the Session survives it."""
    from agent_run_supervisor.session import derive_session_id_for_run

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    request = _request(session_id=None)
    result = _run(harness.task(request=request))
    assert result.status is AgentRunStatus.COMPLETED

    store = harness.session_store()
    records = store.list_records()
    assert len(records) == 1
    created = records[0]
    assert created.session_kind == "native"
    # Named by the deterministic prospective id, fully bound, and untouched by
    # the Run reaching its terminal.
    assert created.session_id == derive_session_id_for_run(result.run_id)
    assert created.session_id == result.session_id
    assert created.agent_session_id
    assert created.quarantine is None
    assert result.session_quarantined is False
    # The lease is released, so the next Run can take it.
    assert not (Path(store.base_dir) / created.session_id / "lock.json").exists()


# --- Slice 3b: prepared RunHandle admission handoff -----------------------


def _create_run_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = EventStore.create_run

    def spying(self: EventStore, run_id: str) -> RunHandle:
        calls.append(run_id)
        return original(self, run_id)

    monkeypatch.setattr(EventStore, "create_run", spying)
    return calls


def _precreated_handle(harness: Harness, run_id: str = "run-0001") -> RunHandle:
    return storage.native_event_store(harness.root).create_run(run_id)


def test_prepared_handle_skips_create_run_and_keeps_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    handle = _precreated_handle(harness)
    calls = _create_run_spy(monkeypatch)

    result = _run(harness.task(prepared_handle=handle))

    assert result.status is AgentRunStatus.COMPLETED
    assert calls == []  # the admission-side exclusive create is never repeated
    assert result.run_dir == handle.run_dir
    for artifact in (
        "spec.json",
        "launch.json",
        "effective.json",
        "events.jsonl",
        "result.json",
        "progress.json",
        "prompt-dispatch-started",
        "prompt-accepted",
        "stderr.log",
        "redaction-report.json",
    ):
        assert (handle.run_dir / artifact).exists(), artifact
    payload = json.loads((handle.run_dir / "result.json").read_text())
    assert payload["status"] == "completed"


def test_without_prepared_handle_exactly_one_exclusive_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    calls = _create_run_spy(monkeypatch)
    result = _run(harness.task())
    assert result.status is AgentRunStatus.COMPLETED
    assert calls == ["run-0001"]


def test_without_prepared_handle_create_failure_path_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The control flow is unchanged — occupied run dir, failed exclusive
    # create, no run_dir on the result — but the projection is now categorical.
    # The store's own message names the absolute path it refused to clobber,
    # which is exactly the class of text WP2.5 keeps out of a caller-visible
    # payload; the stable code carries the diagnosis instead.
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    _precreated_handle(harness)  # occupy the run dir: exclusive create fails
    result = _run(harness.task())
    assert result.status is AgentRunStatus.FAILED
    assert result.run_dir is None
    assert result.payload["error"] == "run reservation failed"
    assert result.payload["detail_code"] == "RUN_RESERVATION_FAILED"
    blob = json.dumps(result.payload)
    assert "already exists" not in blob
    assert "EventStore" not in blob
    assert str(harness.run_dir()) not in blob
    assert str(tmp_path) not in blob


def test_prepared_handle_wrong_type_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    with pytest.raises(NativeRunTaskError, match="RunHandle"):
        harness.task(prepared_handle="run-0001")


def test_prepared_handle_must_match_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    other = _precreated_handle(harness, "run-0002")
    with pytest.raises(NativeRunTaskError, match="run_id"):
        harness.task(prepared_handle=other)


def test_prepared_handle_arbitrary_directory_injection_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    foreign = tmp_path / "elsewhere" / "run-0001"
    foreign.mkdir(parents=True)
    handle = RunHandle(run_id="run-0001", run_dir=foreign)
    with pytest.raises(NativeRunTaskError, match="event-store run directory"):
        harness.task(prepared_handle=handle)


def test_prepared_handle_symlinked_run_dir_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    handle = _precreated_handle(harness)
    foreign = tmp_path / "foreign-target"
    foreign.mkdir()
    (foreign / "sentinel.txt").write_text("foreign content stays intact")
    before = sorted(entry.name for entry in foreign.iterdir())

    # Replace the reserved final run-id entry with a link to a foreign dir:
    # both sides of a naive resolve()-comparison collapse to the same target.
    handle.run_dir.rmdir()
    handle.run_dir.symlink_to(foreign, target_is_directory=True)

    with pytest.raises(NativeRunTaskError, match="symlink"):
        harness.task(prepared_handle=handle)

    assert (foreign / "sentinel.txt").read_text() == "foreign content stays intact"
    assert sorted(entry.name for entry in foreign.iterdir()) == before


def test_prepared_handle_missing_or_file_path_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    absent = RunHandle(run_id="run-0001", run_dir=harness.run_dir())
    with pytest.raises(NativeRunTaskError, match="missing or not a directory"):
        harness.task(prepared_handle=absent)

    harness.run_dir().parent.mkdir(parents=True, exist_ok=True)
    harness.run_dir().write_text("not a directory")
    occupied = RunHandle(run_id="run-0001", run_dir=harness.run_dir())
    with pytest.raises(NativeRunTaskError, match="missing or not a directory"):
        harness.task(prepared_handle=occupied)


def test_prepared_handle_keeps_native_store_root_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    bad_events = EventStore(base_dir=tmp_path / "not-native")
    handle = bad_events.create_run("run-0001")
    with pytest.raises(NativeRunTaskError, match="native-runs"):
        harness.task(
            prepared_handle=handle,
            supervisor_root=None,
            session_store=storage.native_session_store(harness.root),
            event_store=bad_events,
        )


def test_max_events_overflow_pre_dispatch_never_sends_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    result = _run(harness.task(request=_request(limits=RunLimits(max_events=1))))
    assert result.status is AgentRunStatus.FAILED
    assert "session/prompt" not in harness.methods_seen()
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["status"] == "failed"
    assert payload["retryable"] is False
    # Terminal evidence outside the event stream remains possible.
    assert (harness.run_dir() / "result.json").is_file()


def test_max_events_overflow_after_dispatch_fails_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    # Generous enough for startup + prompt-sent evidence; tight enough that
    # in-turn updates overflow the sealed max_events budget.
    result = _run(harness.task(request=_request(limits=RunLimits(max_events=4))))
    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["status"] == "failed"
    assert payload["retryable"] is False
    assert payload.get("detail_code") == "EVIDENCE_PIPELINE"
    assert (harness.run_dir() / "result.json").is_file()
    # Prompt was attempted (dispatch path), but evidence overflow controlled the verdict.
    assert (harness.run_dir() / "prompt-dispatch-started").exists()
    assert "session/prompt" in harness.methods_seen()


def test_r4_b2_hang_overflow_timeout_must_not_persist_retryable_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-terminating prompt + max_events overflow + supervisor timeout.

    B2 (2026-07-24): the escalated timeout without an ACP terminal is
    unknown, and unknown uncertainty stays the strongest status under an
    evidence-pipeline failure — never a retryable timed_out.
    """
    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(
        harness.task(
            request=_request(
                # Allows dispatch, then overflows on post-dispatch evidence while
                # the prompt hangs until the sealed turn timeout.
                limits=RunLimits(max_events=3, turn_timeout_seconds=0.5)
            )
        )
    )
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["status"] != "timed_out"
    assert payload["status"] == "unknown"
    assert payload["retryable"] is False
    assert payload.get("detail_code") == "TURN_TIMEOUT"
    assert (harness.run_dir() / "prompt-dispatch-started").exists()
    assert result.status is AgentRunStatus.UNKNOWN
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None


def test_b2_dispatched_timeout_escalated_kill_is_unknown_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2 (2026-07-24): dispatched prompt + supervisor turn timeout +
    escalated kill + no trustworthy ACP terminal must publish
    unknown/quarantined/retryable=false — never a retryable timed_out the
    quarantined Session then refuses."""
    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(
        harness.task(request=_request(limits=RunLimits(turn_timeout_seconds=0.5)))
    )

    assert result.status is AgentRunStatus.UNKNOWN, result.payload
    run_dir = harness.run_dir()
    assert (run_dir / "prompt-dispatch-started").exists()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["status"] == "unknown"
    assert payload["retryable"] is False
    assert payload["origin"] == "supervisor"
    assert payload["stop_reason"] is None
    assert payload["detail_code"] == "TURN_TIMEOUT"
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None
    assert record.quarantine["source_run_id"] == "run-0001"
    assert not (
        harness.root / "native-sessions" / "sess-native-1" / "lock.json"
    ).exists()

    # Same-Session reuse stays refused before any spawn/prompt (also pinned
    # by test_session_switching.py::test_quarantined_session_refuses_new_runs).
    methods_before = harness.methods_seen()
    retry = _run(harness.task(run_id="run-0002"))
    assert retry.status is AgentRunStatus.FAILED
    assert harness.methods_seen() == methods_before


def test_r4_b3_writer_close_failure_on_completed_is_evidence_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An otherwise-completed Run whose writer close/consumer fails must not
    persist completed — fail closed as non-retryable EVIDENCE_PIPELINE."""
    import warnings

    from agent_run_supervisor.native_acp.event_writer import EventWriter

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    real_close = EventWriter.close

    async def close_then_fail(self):
        await real_close(self)
        raise OSError("injected consumer append failure")

    monkeypatch.setattr(EventWriter, "close", close_then_fail)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _run(harness.task())

    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert result.status is AgentRunStatus.FAILED
    assert payload["status"] == "failed"
    assert payload["status"] != "completed"
    assert payload["retryable"] is False
    assert payload.get("detail_code") == "EVIDENCE_PIPELINE"
    # Bounded cleanup: no asyncio resource warnings from undrained writers.
    assert not any(issubclass(w.category, ResourceWarning) for w in caught)


# -- R5 B1: bounded Native result at ingestion ---------------------------------


def test_r5_b1_final_message_accumulator_bounds_many_chunks() -> None:
    from agent_run_supervisor.native_acp.run_task import FinalMessageAccumulator
    from agent_run_supervisor.result import MAX_FINAL_MESSAGE_BYTES

    acc = FinalMessageAccumulator()
    chunk = ("A" * 4096) + "\x00\x01\x07"
    for _ in range(64):
        acc.ingest(chunk)
    assert acc.retained_bytes <= MAX_FINAL_MESSAGE_BYTES
    assert acc.truncated is True
    assert acc.discarded_chunks >= 1
    assert len(acc.text().encode("utf-8")) <= MAX_FINAL_MESSAGE_BYTES
    # Discarded bodies are not retained — only count + bounded parts.
    assert sum(len(p) for p in acc._parts) == len(acc.text())


def test_r5_b1_large_message_truncates_and_fits_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import warnings

    from agent_run_supervisor.arsd import protocol
    from agent_run_supervisor.native_acp.run_task import FinalMessageAccumulator
    from agent_run_supervisor.result import (
        MAX_FINAL_MESSAGE_BYTES,
        MAX_NATIVE_RESULT_SERIALIZED_BYTES,
        native_result_serialized_size,
        validate_native_terminal_result,
    )

    # Flood many large/control-character chunks at ingestion without blowing
    # the spawn environment (ARG_MAX). Keep only bounded accumulator state.
    real_ingest = FinalMessageAccumulator.ingest
    flood = ("x\x00\x01" * 512)

    def ingest_flood(self, text: str) -> None:
        for _ in range(80):
            real_ingest(self, flood)

    monkeypatch.setattr(FinalMessageAccumulator, "ingest", ingest_flood)
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _run(harness.task())
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert result.status is AgentRunStatus.COMPLETED
    assert payload["truncated"] is True
    assert payload["truncate_reason"] == "max_final_message_bytes"
    assert len(payload["final_message"].encode("utf-8")) <= MAX_FINAL_MESSAGE_BYTES
    size = native_result_serialized_size(payload)
    assert size <= MAX_NATIVE_RESULT_SERIALIZED_BYTES
    assert size < protocol.MAX_FRAME_BYTES
    assert validate_native_terminal_result(payload, run_id="run-0001") is not None
    frame = protocol.encode_frame(
        protocol.build_result(
            "st-1",
            {"run_id": "run-0001", "result": payload},
        )
    )
    assert len(frame) <= protocol.MAX_FRAME_BYTES
    assert not any(issubclass(w.category, ResourceWarning) for w in caught)


def test_r5_b1_oversized_optional_fields_coerce_to_minimal_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.result import (
        MAX_NATIVE_RESULT_SERIALIZED_BYTES,
        enforce_native_result_ceiling,
        native_result_serialized_size,
        validate_native_terminal_result,
    )

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    result = _run(harness.task())
    payload = dict(result.payload)
    # Force a ceiling miss via an enormous optional extension field.
    payload["failure_reason"] = "Z" * (MAX_NATIVE_RESULT_SERIALIZED_BYTES)
    assert native_result_serialized_size(payload) > MAX_NATIVE_RESULT_SERIALIZED_BYTES
    coerced = enforce_native_result_ceiling(
        payload,
        run_id="run-0001",
        run_dir=harness.run_dir(),
        session_id="sess-native-1",
    )
    assert coerced["status"] == "failed"
    assert coerced["retryable"] is False
    assert coerced["detail_code"] == "EVIDENCE_PIPELINE"
    assert native_result_serialized_size(coerced) <= MAX_NATIVE_RESULT_SERIALIZED_BYTES
    assert validate_native_terminal_result(coerced, run_id="run-0001") is not None


# -- R5 B2: zero-prompt evidence gate -----------------------------------------


def test_r5_b2_consumer_dead_before_dispatch_never_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.event_writer import EventWriter

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    real_start = EventWriter.start

    async def start_then_kill_consumer(self):
        await real_start(self)
        consumer = self._consumer
        assert consumer is not None
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(EventWriter, "start", start_then_kill_consumer)
    prompt_calls = {"n": 0}
    original = NativeAcpDriver.prompt_once

    async def counting_prompt(self, text):
        prompt_calls["n"] += 1
        return await original(self, text)

    monkeypatch.setattr(NativeAcpDriver, "prompt_once", counting_prompt)
    result = _run(harness.task())
    assert prompt_calls["n"] == 0
    assert "session/prompt" not in harness.methods_seen()
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["status"] == "failed"
    assert payload["retryable"] is False
    assert payload.get("detail_code") == "EVIDENCE_PIPELINE"
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    assert result.status is AgentRunStatus.FAILED


def test_r5_b2_writer_cannot_accept_before_dispatch_never_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A false writer admission gate prevents the prompt without mutating it."""
    from agent_run_supervisor.native_acp.event_writer import EventWriter

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    real_can = EventWriter.can_accept.fget

    def can_accept(self):
        if getattr(self, "_r5_simulate_queue_full", False):
            # Mirror the exact-full gate without hanging the consumer on close.
            return False
        return real_can(self)

    monkeypatch.setattr(EventWriter, "can_accept", property(can_accept))

    real_dispatch = RunTask._dispatch

    async def dispatch_with_full_queue(self, ctx, turn_timeout):
        assert ctx.writer is not None
        # Queue-policy tests exercise real ledger saturation. This RunTask
        # boundary test pins only the pre-prompt reaction to can_accept=False.
        ctx.writer._r5_simulate_queue_full = True
        return await real_dispatch(self, ctx, turn_timeout)

    monkeypatch.setattr(RunTask, "_dispatch", dispatch_with_full_queue)

    prompt_calls = {"n": 0}
    original = NativeAcpDriver.prompt_once

    async def counting_prompt(self, text):
        prompt_calls["n"] += 1
        return await original(self, text)

    monkeypatch.setattr(NativeAcpDriver, "prompt_once", counting_prompt)
    result = _run(harness.task())
    assert prompt_calls["n"] == 0
    assert "session/prompt" not in harness.methods_seen()
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["retryable"] is False
    assert payload["status"] == "failed"
    assert payload.get("detail_code") == "EVIDENCE_PIPELINE"
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    assert result.status is AgentRunStatus.FAILED


def test_r5_b2_session_prompt_sent_enqueue_fail_skips_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-check passes; session_prompt_sent enqueue fails → prompt call count 0."""
    from agent_run_supervisor.native_acp.event_writer import (
        EventWriter,
        EventWriterOverflow,
    )

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    prompt_calls = {"n": 0}
    original_prompt = NativeAcpDriver.prompt_once

    async def counting_prompt(self, text):
        prompt_calls["n"] += 1
        return await original_prompt(self, text)

    monkeypatch.setattr(NativeAcpDriver, "prompt_once", counting_prompt)

    real_emit = EventWriter.emit_awaited

    async def emit_fail_prompt_sent(self, event):
        if event.get("type") == "session_prompt_sent":
            raise EventWriterOverflow("injected session_prompt_sent enqueue failure")
        return await real_emit(self, event)

    monkeypatch.setattr(EventWriter, "emit_awaited", emit_fail_prompt_sent)
    result = _run(harness.task())
    assert prompt_calls["n"] == 0
    assert "session/prompt" not in harness.methods_seen()
    assert (harness.run_dir() / "prompt-dispatch-started").exists()
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    # Marker written ⇒ conservative unknown/quarantine, never overclaim predispatch.
    assert payload["status"] == "unknown"
    assert payload["retryable"] is False
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None
    assert result.status is AgentRunStatus.UNKNOWN


def test_r6_b1_session_prompt_sent_persisted_before_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Await barrier: durable session_prompt_sent exists before prompt_once."""
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    seen: dict[str, object] = {}
    original = NativeAcpDriver.prompt_once

    async def prompt_after_barrier(self, text):
        events_path = harness.run_dir() / "events.jsonl"
        assert events_path.is_file()
        lines = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(row.get("type") == "session_prompt_sent" for row in lines)
        seen["prompted"] = True
        return await original(self, text)

    monkeypatch.setattr(NativeAcpDriver, "prompt_once", prompt_after_barrier)
    result = _run(harness.task())
    assert seen.get("prompted") is True
    assert result.status is AgentRunStatus.COMPLETED


def test_r6_b1_append_failure_after_marker_skips_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Durable append failure after marker → prompt call count 0, unknown."""
    from agent_run_supervisor.event_store import RunHandle

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    prompt_calls = {"n": 0}
    original_prompt = NativeAcpDriver.prompt_once

    async def counting_prompt(self, text):
        prompt_calls["n"] += 1
        return await original_prompt(self, text)

    monkeypatch.setattr(NativeAcpDriver, "prompt_once", counting_prompt)

    real_append = RunHandle.append_text

    def boom_prompt_sent(self, name, value):
        record = json.loads(value)
        if record.get("type") == "session_prompt_sent":
            raise OSError("injected session_prompt_sent append failure")
        return real_append(self, name, value)

    monkeypatch.setattr(RunHandle, "append_text", boom_prompt_sent)

    result = _run(harness.task())
    assert prompt_calls["n"] == 0
    assert "session/prompt" not in harness.methods_seen()
    assert (harness.run_dir() / "prompt-dispatch-started").exists()
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["status"] == "unknown"
    assert payload["retryable"] is False
    assert result.status is AgentRunStatus.UNKNOWN


# -- R5 B3: quarantine-before-result + durable fence --------------------------


def test_r5_b3_fence_before_result_and_failure_retains_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as dt

    from agent_run_supervisor.session import (
    QUARANTINE_DISPATCH_OBSERVATION_LOST,
        QUARANTINE_PENDING_JSON,
        SessionQuarantinedError,
        SessionStore,
    )

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)

    real_mark = SessionStore.mark_quarantined
    calls = {"n": 0}

    def boom_mark(self, session_id, **kwargs):
        calls["n"] += 1
        # Fence must already exist before the state write attempt.
        session_dir = Path(self.base_dir) / session_id
        assert (session_dir / QUARANTINE_PENDING_JSON).is_file()
        raise OSError("injected mark_quarantined failure")

    monkeypatch.setattr(SessionStore, "mark_quarantined", boom_mark)
    result = _run(
        harness.task(
            request=_request(limits=RunLimits(turn_timeout_seconds=0.3))
        )
    )
    run_dir = harness.run_dir()
    assert not (run_dir / "result.json").exists()
    session_dir = harness.root / "native-sessions" / "sess-native-1"
    assert (session_dir / QUARANTINE_PENDING_JSON).is_file()
    assert (session_dir / "lock.json").is_file()
    # Force TTL expiry: fence still denies acquire.
    later = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
    store = harness.session_store()
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock(
            "sess-native-1", "hermes", refuse_quarantined=True, now=later
        )
    # Later successful reconciliation quarantine clears fence.
    monkeypatch.setattr(SessionStore, "mark_quarantined", real_mark)
    store.mark_quarantined(
        "sess-native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-0001", now=later
    )
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    assert store.open_session("sess-native-1").quarantine is not None
    assert calls["n"] >= 1
    assert result.payload.get("error") or result.status is AgentRunStatus.FAILED


def test_r5_b3_happy_completed_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    result = _run(harness.task())
    assert result.status is AgentRunStatus.COMPLETED
    session_dir = harness.root / "native-sessions" / "sess-native-1"
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    assert harness.session_store().open_session("sess-native-1").quarantine is None
    assert not (session_dir / "lock.json").exists()


def test_r7_b2_forged_invalid_existing_result_quarantines_no_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FileExists with invalid artifact must fence/quarantine and not release."""
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    handle = _precreated_handle(harness)
    # Plant invalid forged terminal before the task publishes.
    (handle.run_dir / "result.json").write_text(
        json.dumps({"run_id": "run-0001", "status": "completed", "retryable": False}),
        encoding="utf-8",
    )
    result = _run(harness.task(prepared_handle=handle))
    session_dir = harness.root / "native-sessions" / "sess-native-1"
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None or (
        session_dir / QUARANTINE_PENDING_JSON
    ).is_file()
    assert result.status is not AgentRunStatus.COMPLETED
    # Must not look like a clean successful lease release.
    assert (session_dir / "lock.json").exists() or record.quarantine is not None


def test_r7_b3_awaited_event_fsync_failure_skips_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File fsync failure during durable append → prompt call count 0."""
    import os
    import stat

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    prompt_calls = {"n": 0}
    original_prompt = NativeAcpDriver.prompt_once

    async def counting_prompt(self, text):
        prompt_calls["n"] += 1
        return await original_prompt(self, text)

    monkeypatch.setattr(NativeAcpDriver, "prompt_once", counting_prompt)

    real_fsync = os.fsync
    real_append = RunHandle.append_text
    state = {"boom": False}

    def boom_prompt_sent_append(self, name, value):
        if (
            not state["boom"]
            and isinstance(value, str)
            and "session_prompt_sent" in value
        ):
            state["boom"] = True

            def boom_fsync(fd: int) -> None:
                st = os.fstat(fd)
                if stat.S_ISREG(st.st_mode):
                    raise OSError(5, "injected append fsync failure")
                return real_fsync(fd)

            monkeypatch.setattr(os, "fsync", boom_fsync)
        return real_append(self, name, value)

    monkeypatch.setattr(RunHandle, "append_text", boom_prompt_sent_append)
    result = _run(harness.task())
    assert prompt_calls["n"] == 0
    assert "session/prompt" not in harness.methods_seen()
    assert result.status in {AgentRunStatus.FAILED, AgentRunStatus.UNKNOWN}


# -- R8 B2 emergency finalize trusts only Native terminal reader --------------


def test_r8_b2_emergency_invalid_forged_result_no_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVALID forged terminal: fence/quarantine, no lease release, no raw status."""
    from agent_run_supervisor.native_acp.event_writer import EventWriter
    from agent_run_supervisor.native_acp.run_task import _RunContext
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    forged = {
        "run_id": "run-0001",
        "status": "completed",
        "retryable": False,
        "malicious_secret": "LEAKCANARY-FORGED-STATUS",
    }
    captured: dict[str, object] = {}

    async def case():
        entered_close = asyncio.Event()

        async def hanging_close(self) -> None:
            entered_close.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(EventWriter, "close", hanging_close)
        task_obj = harness.task()
        real_finalize = task_obj._finalize

        async def capture_and_hang(ctx):
            captured["ctx"] = ctx
            captured["task"] = task_obj
            return await real_finalize(ctx)

        monkeypatch.setattr(task_obj, "_finalize", capture_and_hang)
        task = asyncio.ensure_future(task_obj.run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        ctx = captured["ctx"]
        assert isinstance(ctx, _RunContext)
        (ctx.handle.run_dir / "result.json").write_text(
            json.dumps(forged), encoding="utf-8"
        )
        await task_obj._emergency_cleanup(ctx)
        result = task_obj._result_after_emergency(ctx)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 30)
        return result

    result = asyncio.run(case())
    session_dir = harness.root / "native-sessions" / "sess-native-1"
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None or (
        session_dir / QUARANTINE_PENDING_JSON
    ).is_file()
    assert (session_dir / "lock.json").exists()
    assert result.status is AgentRunStatus.FAILED
    assert result.payload.get("status") == "failed"
    assert result.payload.get("status") != "completed"
    assert "malicious_secret" not in result.payload
    assert "LEAKCANARY-FORGED-STATUS" not in json.dumps(result.payload)
    assert "FORGED" not in json.dumps(result.payload)
    _assert_pid_gone_sync(spawned[0].pid)


def test_r8_b2_emergency_trusted_releases_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TRUSTED emergency-written terminal may release the Session lease once."""
    from agent_run_supervisor.native_acp.event_writer import EventWriter
    from agent_run_supervisor.native_acp.run_task import _RunContext

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    releases: list[str] = []
    real_release = SessionStore.release_lock
    captured: dict[str, object] = {}

    def tracking_release(self, session_id, token):
        releases.append(session_id)
        return real_release(self, session_id, token)

    monkeypatch.setattr(SessionStore, "release_lock", tracking_release)

    async def case():
        entered_close = asyncio.Event()

        async def hanging_close(self) -> None:
            entered_close.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(EventWriter, "close", hanging_close)
        task_obj = harness.task()
        real_finalize = task_obj._finalize

        async def capture_and_hang(ctx):
            captured["ctx"] = ctx
            return await real_finalize(ctx)

        monkeypatch.setattr(task_obj, "_finalize", capture_and_hang)
        task = asyncio.ensure_future(task_obj.run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        ctx = captured["ctx"]
        assert isinstance(ctx, _RunContext)
        # ABSENT path: emergency writes TRUSTED unknown then releases once.
        assert not (ctx.handle.run_dir / "result.json").exists()
        await task_obj._emergency_cleanup(ctx)
        result = task_obj._result_after_emergency(ctx)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 30)
        return result

    result = asyncio.run(case())
    assert result.status is AgentRunStatus.UNKNOWN
    assert result.payload.get("detail_code") == "EMERGENCY_FINALIZE"
    assert result.payload.get("status") == "unknown"
    assert releases == ["sess-native-1"]
    assert not (
        harness.root / "native-sessions" / "sess-native-1" / "lock.json"
    ).exists()
    _assert_pid_gone_sync(spawned[0].pid)


def test_r8_b2_emergency_quarantine_failure_keeps_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.event_writer import EventWriter
    from agent_run_supervisor.native_acp.run_task import _RunContext

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    releases: list[str] = []
    real_release = SessionStore.release_lock
    captured: dict[str, object] = {}

    def tracking_release(self, session_id, token):
        releases.append(session_id)
        return real_release(self, session_id, token)

    def boom_mark(self, session_id, **kwargs):
        raise OSError("injected quarantine failure")

    monkeypatch.setattr(SessionStore, "release_lock", tracking_release)
    monkeypatch.setattr(SessionStore, "mark_quarantined", boom_mark)

    async def case():
        entered_close = asyncio.Event()

        async def hanging_close(self) -> None:
            entered_close.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(EventWriter, "close", hanging_close)
        task_obj = harness.task()
        real_finalize = task_obj._finalize

        async def capture_and_hang(ctx):
            captured["ctx"] = ctx
            return await real_finalize(ctx)

        monkeypatch.setattr(task_obj, "_finalize", capture_and_hang)
        task = asyncio.ensure_future(task_obj.run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        ctx = captured["ctx"]
        assert isinstance(ctx, _RunContext)
        (ctx.handle.run_dir / "result.json").write_text(
            json.dumps(
                {
                    "run_id": "run-0001",
                    "status": "completed",
                    "retryable": False,
                }
            ),
            encoding="utf-8",
        )
        await task_obj._emergency_cleanup(ctx)
        result = task_obj._result_after_emergency(ctx)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 30)
        return result

    result = asyncio.run(case())
    assert releases == []
    assert (
        harness.root / "native-sessions" / "sess-native-1" / "lock.json"
    ).exists()
    assert result.status is AgentRunStatus.FAILED
    assert result.payload.get("status") == "failed"
    _assert_pid_gone_sync(spawned[0].pid)


# -- R8 residual: sanitized in-memory finalize failure payloads ---------------


_CANARY_EXC = "LEAKCANARY-EXC-/tmp/secret-path-OSError"


def test_r8_residual_finalize_failure_payload_omits_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outer `_finalize` catch returns fixed codes; never str(exc)/paths/secrets."""
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)

    async def boom_inner(self, ctx):
        raise RuntimeError(_CANARY_EXC)

    monkeypatch.setattr(RunTask, "_finalize_inner", boom_inner)
    result = _run(harness.task())
    assert result.status is AgentRunStatus.FAILED
    assert result.payload["status"] == "failed"
    assert result.payload["error"] == "finalization failed"
    assert result.payload["detail_code"] == "FINALIZATION_FAILED"
    blob = json.dumps(result.payload)
    assert _CANARY_EXC not in blob
    assert "LEAKCANARY" not in blob
    assert "/tmp/secret-path" not in blob
    assert "RuntimeError" not in blob
    assert "OSError" not in blob


def test_r9_b2_finalize_inner_failure_invokes_emergency_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected `_finalize_inner` failure must emergency-clean before return."""
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    spawned = _recording_spawn(monkeypatch)
    cleanup_calls: list[str] = []
    real_emergency = RunTask._emergency_cleanup

    async def tracking_emergency(self, ctx):
        cleanup_calls.append("emergency")
        return await real_emergency(self, ctx)

    async def boom_inner(self, ctx):
        # Fail before driver close / child reap — the pre-reap gap.
        raise RuntimeError(_CANARY_EXC)

    monkeypatch.setattr(RunTask, "_emergency_cleanup", tracking_emergency)
    monkeypatch.setattr(RunTask, "_finalize_inner", boom_inner)
    result = _run(harness.task())
    assert cleanup_calls == ["emergency"]
    assert result.status is AgentRunStatus.FAILED
    assert result.payload["status"] == "failed"
    assert result.payload["error"] == "finalization failed"
    assert result.payload["detail_code"] == "FINALIZATION_FAILED"
    blob = json.dumps(result.payload)
    assert _CANARY_EXC not in blob
    assert "RuntimeError" not in blob
    assert spawned, "child process was never spawned"
    _assert_pid_gone_sync(spawned[0].pid)


def test_r8_residual_disposition_failure_payload_omits_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disposition failure returns fixed codes; no durable terminal; no release."""
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON, SessionStore

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)

    def boom_mark(self, session_id, **kwargs):
        raise OSError(_CANARY_EXC)

    monkeypatch.setattr(SessionStore, "mark_quarantined", boom_mark)
    result = _run(
        harness.task(
            request=_request(limits=RunLimits(turn_timeout_seconds=0.3))
        )
    )
    assert result.status is AgentRunStatus.FAILED
    assert result.payload["status"] == "failed"
    assert result.payload["error"] == "session disposition failed"
    assert result.payload["detail_code"] == "SESSION_DISPOSITION_FAILED"
    blob = json.dumps(result.payload)
    assert _CANARY_EXC not in blob
    assert "LEAKCANARY" not in blob
    assert "/tmp/secret-path" not in blob
    assert "OSError" not in blob
    run_dir = harness.run_dir()
    assert not (run_dir / "result.json").exists()
    session_dir = harness.root / "native-sessions" / "sess-native-1"
    assert (session_dir / QUARANTINE_PENDING_JSON).is_file()
    assert (session_dir / "lock.json").is_file()


def test_r10_b2_emergency_wait_reaps_before_release_and_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Emergency SIGKILL must await ManagedProcess.wait/reap before release/return."""
    from agent_run_supervisor.managed_process import ManagedProcess
    from agent_run_supervisor.native_acp.driver import NativeAcpDriver
    from agent_run_supervisor.native_acp.run_task import _RunContext

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    order: list[str] = []
    real_wait = ManagedProcess.wait
    real_release = SessionStore.release_lock
    real_kill = ManagedProcess.kill_group
    captured: dict[str, object] = {}

    async def tracking_wait(self):
        order.append("wait")
        return await real_wait(self)

    def tracking_release(self, session_id, token):
        order.append("release")
        return real_release(self, session_id, token)

    def tracking_kill(self, *, reason: str = "kill_group") -> None:
        order.append("kill")
        return real_kill(self, reason=reason)

    monkeypatch.setattr(ManagedProcess, "wait", tracking_wait)
    monkeypatch.setattr(SessionStore, "release_lock", tracking_release)
    monkeypatch.setattr(ManagedProcess, "kill_group", tracking_kill)

    async def case():
        entered_close = asyncio.Event()

        async def hanging_driver_close(self) -> None:
            # Hang before finalize's terminate/wait so emergency reaps a live child.
            entered_close.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(NativeAcpDriver, "close", hanging_driver_close)
        task_obj = harness.task()
        real_finalize = task_obj._finalize

        async def capture_and_hang(ctx):
            captured["ctx"] = ctx
            return await real_finalize(ctx)

        monkeypatch.setattr(task_obj, "_finalize", capture_and_hang)
        task = asyncio.ensure_future(task_obj.run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        ctx = captured["ctx"]
        assert isinstance(ctx, _RunContext)
        assert not (ctx.handle.run_dir / "result.json").exists()
        assert ctx.proc is not None
        live_pid = ctx.proc.pid
        # Child must still be alive before emergency containment.
        import os

        os.kill(live_pid, 0)
        order.clear()
        await task_obj._emergency_cleanup(ctx)
        order.append("returned")
        result = task_obj._result_after_emergency(ctx)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 30)
        return result, live_pid

    result, live_pid = asyncio.run(case())
    assert "kill" in order
    assert "wait" in order
    assert "release" in order
    assert "returned" in order
    assert order.index("kill") < order.index("wait")
    assert order.index("wait") < order.index("release")
    assert order.index("wait") < order.index("returned")
    assert result.payload.get("detail_code") == "EMERGENCY_FINALIZE"
    assert spawned, "child process was never spawned"
    _assert_pid_gone_sync(live_pid)


def test_r10b_b2a_cancel_while_dedicated_reap_pending_keeps_reap_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller cancel during shielded reap must not cancel the dedicated wait task.

    Proves the real ManagedProcess.wait() operation stays live under a dedicated
    task, release/return happen only after reap, and CancelledError propagates
    only after emergency cleanup completes.
    """
    from agent_run_supervisor.managed_process import ManagedProcess
    from agent_run_supervisor.native_acp.driver import NativeAcpDriver
    from agent_run_supervisor.native_acp.run_task import _RunContext

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    real_wait = ManagedProcess.wait
    real_release = SessionStore.release_lock
    order: list[str] = []
    entered_wait = asyncio.Event()
    release_wait = asyncio.Event()
    reap_task_box: dict[str, asyncio.Task | None] = {"task": None}
    captured: dict[str, object] = {}
    in_direct_emergency = {"reusable": False}

    async def gated_wait(self):
        if in_direct_emergency["reusable"]:
            reap_task_box["task"] = asyncio.current_task()
            order.append("wait_entered")
            entered_wait.set()
            await release_wait.wait()
            order.append("wait_finished")
        return await real_wait(self)

    def tracking_release(self, session_id, token):
        if in_direct_emergency["reusable"]:
            order.append("release")
        return real_release(self, session_id, token)

    monkeypatch.setattr(ManagedProcess, "wait", gated_wait)
    monkeypatch.setattr(SessionStore, "release_lock", tracking_release)

    async def case():
        entered_close = asyncio.Event()
        release_close = asyncio.Event()

        async def hanging_driver_close(self) -> None:
            entered_close.set()
            await release_close.wait()

        monkeypatch.setattr(NativeAcpDriver, "close", hanging_driver_close)
        task_obj = harness.task()
        real_finalize = task_obj._finalize

        async def capture_and_hang(ctx):
            captured["ctx"] = ctx
            return await real_finalize(ctx)

        monkeypatch.setattr(task_obj, "_finalize", capture_and_hang)
        run_task = asyncio.ensure_future(task_obj.run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        # First cancel exits drive; finalize then parks in driver.close so this
        # test can own a direct emergency_cleanup observation.
        run_task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        ctx = captured["ctx"]
        assert isinstance(ctx, _RunContext)
        assert ctx.proc is not None
        live_pid = ctx.proc.pid

        in_direct_emergency["reusable"] = True
        cleanup = asyncio.ensure_future(task_obj._emergency_cleanup(ctx))
        await asyncio.wait_for(entered_wait.wait(), 30)
        reap = reap_task_box["task"]
        assert reap is not None
        assert reap is not cleanup
        assert not reap.done()
        assert not reap.cancelled()
        assert "release" not in order

        cleanup.cancel()
        await asyncio.sleep(0.05)
        assert not reap.done(), "dedicated reap task must survive caller cancellation"
        assert not reap.cancelled()
        assert not cleanup.done(), "cleanup must stay pending until reap finishes"
        assert "release" not in order

        release_wait.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cleanup, 30)
        order.append("cleanup_cancelled")
        in_direct_emergency["reusable"] = False

        assert reap.done()
        assert not reap.cancelled()
        assert "wait_finished" in order
        assert "release" in order
        assert order.index("wait_finished") < order.index("release")
        assert order.index("release") < order.index("cleanup_cancelled")

        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, 30)
        return live_pid

    live_pid = asyncio.run(case())
    assert spawned
    _assert_pid_gone_sync(live_pid)


def test_r10c_b2a_wait_exception_retries_until_proven_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordinary wait failure must retry fail-closed until a proven reap.

    First wait raises; the replacement wait stays blocked → cleanup remains
    pending with no release/result. Caller cancellation during that wait is
    absorbed. Only after the replacement wait succeeds may disposition run,
    and CancelledError propagates only after cleanup completes.
    """
    from agent_run_supervisor.managed_process import ManagedProcess
    from agent_run_supervisor.native_acp.driver import NativeAcpDriver
    from agent_run_supervisor.native_acp.run_task import _RunContext

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    real_wait = ManagedProcess.wait
    real_release = SessionStore.release_lock
    order: list[str] = []
    wait_calls = {"n": 0}
    second_entered = asyncio.Event()
    release_second = asyncio.Event()
    in_direct_emergency = {"reusable": False}
    captured: dict[str, object] = {}

    async def flaky_wait(self):
        if not in_direct_emergency["reusable"]:
            return await real_wait(self)
        wait_calls["n"] += 1
        if wait_calls["n"] == 1:
            order.append("wait1_failed")
            raise OSError("injected transient wait failure")
        order.append("wait2_entered")
        second_entered.set()
        await release_second.wait()
        order.append("wait2_finished")
        return await real_wait(self)

    def tracking_release(self, session_id, token):
        if in_direct_emergency["reusable"]:
            order.append("release")
        return real_release(self, session_id, token)

    monkeypatch.setattr(ManagedProcess, "wait", flaky_wait)
    monkeypatch.setattr(SessionStore, "release_lock", tracking_release)

    async def case():
        entered_close = asyncio.Event()
        release_close = asyncio.Event()
        cleanup = None
        run_task = None
        live_pid = None

        async def hanging_driver_close(self) -> None:
            entered_close.set()
            await release_close.wait()

        monkeypatch.setattr(NativeAcpDriver, "close", hanging_driver_close)
        task_obj = harness.task()
        real_finalize = task_obj._finalize

        async def capture_and_hang(ctx):
            captured["ctx"] = ctx
            return await real_finalize(ctx)

        monkeypatch.setattr(task_obj, "_finalize", capture_and_hang)
        try:
            run_task = asyncio.ensure_future(task_obj.run())
            await _wait_until(marker.exists, message="dispatch marker never appeared")
            run_task.cancel()
            await asyncio.wait_for(entered_close.wait(), 30)
            ctx = captured["ctx"]
            assert isinstance(ctx, _RunContext)
            assert ctx.proc is not None
            assert ctx.lock is not None
            live_pid = ctx.proc.pid

            in_direct_emergency["reusable"] = True
            cleanup = asyncio.ensure_future(task_obj._emergency_cleanup(ctx))
            await asyncio.wait_for(second_entered.wait(), 30)
            assert wait_calls["n"] == 2
            assert "wait1_failed" in order
            assert not cleanup.done(), (
                "cleanup must remain pending until replacement wait proves reap"
            )
            assert "release" not in order
            assert not (ctx.handle.run_dir / "result.json").exists()
            assert ctx.lock is not None

            cleanup.cancel()
            await asyncio.sleep(0.05)
            assert not cleanup.done(), (
                "caller cancel during retry/reap must not return cleanup early"
            )
            assert "release" not in order
            assert not (ctx.handle.run_dir / "result.json").exists()

            release_second.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(cleanup, 30)
            order.append("cleanup_cancelled")

            assert "wait2_finished" in order
            assert "release" in order
            assert (ctx.handle.run_dir / "result.json").exists()
            assert order.index("wait1_failed") < order.index("wait2_entered")
            assert order.index("wait2_finished") < order.index("release")
            assert order.index("release") < order.index("cleanup_cancelled")

            release_close.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(run_task, 30)
            return live_pid
        finally:
            in_direct_emergency["reusable"] = False
            release_second.set()
            release_close.set()
            if cleanup is not None and not cleanup.done():
                cleanup.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(cleanup, 5)
            if run_task is not None and not run_task.done():
                run_task.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(run_task, 5)

    live_pid = asyncio.run(case())
    assert spawned
    assert live_pid is not None
    _assert_pid_gone_sync(live_pid)


def _install_caller_cancel_when_target_done_race(
    monkeypatch: pytest.MonkeyPatch,
    *,
    module,
    task_name_prefix: str,
) -> dict[str, object]:
    """Test seam: when a named shielded task is already done, deliver caller cancel.

    Forces the CancelledError source-race where ``target.done()`` and
    ``current_task().cancelling()`` are both true in the same except arm.
    """
    real_shield = module.asyncio.shield
    state: dict[str, object] = {"armed": True, "fired": False}

    def racing_shield(aw):
        async def _race():
            fut = aw if isinstance(aw, asyncio.Future) else asyncio.ensure_future(aw)
            name = fut.get_name() if isinstance(fut, asyncio.Task) else ""
            if not (state["armed"] and name.startswith(task_name_prefix)):
                return await real_shield(fut)
            while not fut.done():
                try:
                    await real_shield(fut)
                    break
                except asyncio.CancelledError:
                    if fut.done():
                        break
                    raise
            assert fut.done()
            state["armed"] = False
            state["fired"] = True
            me = asyncio.current_task()
            assert me is not None
            me.cancel()
            assert me.cancelling() > 0
            # Suspend once so the loop delivers the queued cancellation at this
            # checkpoint — exactly one CancelledError reaches the except arm.
            # Raising CancelledError by hand here would leave the queued
            # delivery pending and double-deliver on some CPython versions.
            await asyncio.sleep(0)
            raise AssertionError("queued caller cancellation was not delivered")

        return _race()

    monkeypatch.setattr(module.asyncio, "shield", racing_shield)
    return state


def test_r10d_b2a_caller_cancel_when_reap_already_done_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller cancel + reap already done must absorb/observe success, not retry."""
    import agent_run_supervisor.native_acp.run_task as run_task_mod
    from agent_run_supervisor.managed_process import ManagedProcess
    from agent_run_supervisor.native_acp.driver import NativeAcpDriver
    from agent_run_supervisor.native_acp.run_task import _RunContext

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    real_wait = ManagedProcess.wait
    real_release = SessionStore.release_lock
    order: list[str] = []
    wait_calls = {"n": 0}
    in_direct_emergency = {"reusable": False}
    captured: dict[str, object] = {}
    cleanup_box: dict[str, asyncio.Task | None] = {"task": None}
    race = _install_caller_cancel_when_target_done_race(
        monkeypatch,
        module=run_task_mod,
        task_name_prefix="emergency-reap:",
    )

    async def instant_wait(self):
        if not in_direct_emergency["reusable"]:
            return await real_wait(self)
        wait_calls["n"] += 1
        order.append("wait_finished")
        return await real_wait(self)

    def tracking_release(self, session_id, token):
        if in_direct_emergency["reusable"]:
            cleanup = cleanup_box["task"]
            if cleanup is not None and race["fired"]:
                race["cancelling_at_release"] = cleanup.cancelling()
            order.append("release")
        return real_release(self, session_id, token)

    monkeypatch.setattr(ManagedProcess, "wait", instant_wait)
    monkeypatch.setattr(SessionStore, "release_lock", tracking_release)

    async def case():
        entered_close = asyncio.Event()
        release_close = asyncio.Event()

        async def hanging_driver_close(self) -> None:
            entered_close.set()
            await release_close.wait()

        monkeypatch.setattr(NativeAcpDriver, "close", hanging_driver_close)
        task_obj = harness.task()
        real_finalize = task_obj._finalize

        async def capture_and_hang(ctx):
            captured["ctx"] = ctx
            return await real_finalize(ctx)

        monkeypatch.setattr(task_obj, "_finalize", capture_and_hang)
        run_task = asyncio.ensure_future(task_obj.run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        run_task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        ctx = captured["ctx"]
        assert isinstance(ctx, _RunContext)
        assert ctx.proc is not None
        live_pid = ctx.proc.pid

        in_direct_emergency["reusable"] = True
        cleanup = asyncio.ensure_future(task_obj._emergency_cleanup(ctx))
        cleanup_box["task"] = cleanup
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cleanup, 30)
        order.append("cleanup_cancelled")
        in_direct_emergency["reusable"] = False

        assert race["fired"] is True
        assert wait_calls["n"] == 1, (
            "successful reap misclassified as target-cancel must not be retried"
        )
        assert race.get("cancelling_at_release") == 0, (
            "caller cancel must be absorbed before disposition/release"
        )
        assert "wait_finished" in order
        assert "release" in order
        assert order.index("wait_finished") < order.index("release")
        assert order.index("release") < order.index("cleanup_cancelled")
        assert (ctx.handle.run_dir / "result.json").exists()

        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, 30)
        return live_pid

    live_pid = asyncio.run(case())
    assert spawned
    _assert_pid_gone_sync(live_pid)


def test_r10d_b2a_caller_cancel_when_retry_delay_already_done_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller cancel + retry-delay already done must absorb before continuing."""
    import agent_run_supervisor.native_acp.run_task as run_task_mod
    from agent_run_supervisor.managed_process import ManagedProcess
    from agent_run_supervisor.native_acp.driver import NativeAcpDriver
    from agent_run_supervisor.native_acp.run_task import _RunContext

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    real_wait = ManagedProcess.wait
    real_release = SessionStore.release_lock
    order: list[str] = []
    wait_calls = {"n": 0}
    in_direct_emergency = {"reusable": False}
    captured: dict[str, object] = {}
    cleanup_box: dict[str, asyncio.Task | None] = {"task": None}
    race = _install_caller_cancel_when_target_done_race(
        monkeypatch,
        module=run_task_mod,
        task_name_prefix="emergency-reap-delay:",
    )

    async def flaky_wait(self):
        if not in_direct_emergency["reusable"]:
            return await real_wait(self)
        wait_calls["n"] += 1
        if wait_calls["n"] == 1:
            order.append("wait1_failed")
            raise OSError("injected transient wait failure")
        cleanup = cleanup_box["task"]
        if cleanup is not None and race["fired"]:
            race["cancelling_at_wait2"] = cleanup.cancelling()
        order.append("wait2_finished")
        return await real_wait(self)

    def tracking_release(self, session_id, token):
        if in_direct_emergency["reusable"]:
            order.append("release")
        return real_release(self, session_id, token)

    monkeypatch.setattr(ManagedProcess, "wait", flaky_wait)
    monkeypatch.setattr(SessionStore, "release_lock", tracking_release)

    async def case():
        entered_close = asyncio.Event()
        release_close = asyncio.Event()

        async def hanging_driver_close(self) -> None:
            entered_close.set()
            await release_close.wait()

        monkeypatch.setattr(NativeAcpDriver, "close", hanging_driver_close)
        task_obj = harness.task()
        real_finalize = task_obj._finalize

        async def capture_and_hang(ctx):
            captured["ctx"] = ctx
            return await real_finalize(ctx)

        monkeypatch.setattr(task_obj, "_finalize", capture_and_hang)
        run_task = asyncio.ensure_future(task_obj.run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        run_task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        ctx = captured["ctx"]
        assert isinstance(ctx, _RunContext)
        assert ctx.proc is not None
        live_pid = ctx.proc.pid

        in_direct_emergency["reusable"] = True
        cleanup = asyncio.ensure_future(task_obj._emergency_cleanup(ctx))
        cleanup_box["task"] = cleanup
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cleanup, 30)
        order.append("cleanup_cancelled")
        in_direct_emergency["reusable"] = False

        assert race["fired"] is True
        assert race.get("cancelling_at_wait2") == 0, (
            "caller cancel must be absorbed during delay race before the next reap"
        )
        assert wait_calls["n"] == 2
        assert "wait1_failed" in order
        assert "wait2_finished" in order
        assert "release" in order
        assert order.index("wait1_failed") < order.index("wait2_finished")
        assert order.index("wait2_finished") < order.index("release")
        assert order.index("release") < order.index("cleanup_cancelled")
        assert (ctx.handle.run_dir / "result.json").exists()

        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, 30)
        return live_pid

    live_pid = asyncio.run(case())
    assert spawned
    _assert_pid_gone_sync(live_pid)


def test_r10_b3_pre_dispatch_failure_reason_is_categorical_not_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persisted failure_reason must not carry paths/secrets/exception text."""
    import secrets

    canary_dir = "/tmp/" + "leakcanary-dir-" + secrets.token_hex(4)
    canary_secret = "sk-" + secrets.token_hex(12)
    canary_cls = "Hostile" + "SpawnError"
    hostile = f"{canary_cls}: missing binary under {canary_dir}; token={canary_secret}"

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)

    async def boom_spawn(**kwargs):
        del kwargs
        raise OSError(hostile)

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.spawn_managed_process",
        boom_spawn,
    )
    result = _run(harness.task())
    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text(encoding="utf-8"))
    assert payload.get("detail_code") == "SPAWN_FAILED"
    reason = payload.get("failure_reason")
    assert isinstance(reason, str) and reason
    blob = json.dumps(payload)
    assert canary_dir not in blob
    assert canary_secret not in blob
    assert canary_cls not in blob
    assert hostile not in blob
    assert "OSError" not in blob
    assert reason == "spawn failed"


def test_r10_b3_run_exception_failure_reason_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import secrets

    canary_path = "/var/" + "leakcanary-" + secrets.token_hex(4) + "/cred.json"
    canary = f"boom at {canary_path}"

    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)

    async def boom_drive(self, ctx):
        del ctx
        raise RuntimeError(canary)

    monkeypatch.setattr(RunTask, "_drive", boom_drive)
    result = _run(harness.task())
    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text(encoding="utf-8"))
    assert payload.get("detail_code") == "RUN_EXCEPTION"
    reason = payload.get("failure_reason")
    assert reason == "run exception"
    blob = json.dumps(payload)
    assert canary_path not in blob
    assert canary not in blob
    assert "RuntimeError" not in blob


# -- R14: finalize cancel/timeout waits for EventWriter close ownership --------


def test_r14_finalize_cancel_waits_for_event_writer_close_before_emergency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finalize cancellation must not emergency-publish/release while append lives.

    EventWriter.close ownership alone must keep emergency terminal + Session
    lease release from proceeding until the controlled append unblocks and
    close settles; then conservative emergency disposition completes.
    """
    import threading

    from agent_run_supervisor.native_acp.event_writer import EventWriter

    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)
    spawned = _recording_spawn(monkeypatch)
    marker = harness.run_dir() / "prompt-dispatch-started"
    result_path = harness.run_dir() / "result.json"
    lock_path = harness.root / "native-sessions" / "sess-native-1" / "lock.json"

    release_append = threading.Event()
    entered_append = threading.Event()
    entered_close = asyncio.Event()
    real_close = EventWriter.close
    real_append = RunHandle.append_text
    closing = {"on": False}

    def gated_append(self, name, value):
        if closing["on"] and name == "events.jsonl":
            entered_append.set()
            assert release_append.wait(timeout=60), "append gate was not released"
        return real_append(self, name, value)

    async def close_with_blocked_append(self):
        closing["on"] = True
        # Ensure the consumer must finish a gated append before healthy stop.
        pad = asyncio.ensure_future(self.emit({"type": "r14_finalize_pad"}))
        for _ in range(400):
            if entered_append.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered_append.is_set(), "controlled append never entered"
        entered_close.set()
        try:
            return await real_close(self)
        finally:
            if not pad.done():
                pad.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pad

    monkeypatch.setattr(RunHandle, "append_text", gated_append)
    monkeypatch.setattr(EventWriter, "close", close_with_blocked_append)

    async def case() -> None:
        task = asyncio.ensure_future(harness.task().run())
        await _wait_until(marker.exists, message="dispatch marker never appeared")
        task.cancel()
        await asyncio.wait_for(entered_close.wait(), 30)
        await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, entered_append.wait),
            30,
        )
        # While append/close ownership is live: no emergency terminal, lease held.
        assert not result_path.exists()
        assert lock_path.exists()
        task.cancel()
        task.cancel()
        await asyncio.sleep(0.1)
        assert not result_path.exists()
        assert lock_path.exists()
        assert not task.done()

        release_append.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 60)

    asyncio.run(case())

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "unknown"
    assert payload["retryable"] is False
    assert payload["detail_code"] == "EMERGENCY_FINALIZE"
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is not None
    assert not lock_path.exists()
    _assert_pid_gone_sync(spawned[0].pid)


# -- A4-S2: grant→agent permission coupling and fail-closed backstop ---------


def _run_events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]


OPENCODE_STYLE_ROGUE_EDIT_UPDATES = [
    # The exact reviewed A4-S2 evidence shape: tool_started(kind=edit) →
    # in_progress → completed, with zero permission mediation; OpenCode's
    # completed update carries no kind (correlate by toolCallId).
    {
        "sessionUpdate": "tool_call",
        "toolCallId": "call-edit-1",
        "title": "Write sentinel",
        "kind": "edit",
        "status": "pending",
    },
    {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "call-edit-1",
        "status": "in_progress",
    },
    {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "call-edit-1",
        "status": "completed",
    },
]


def test_unmediated_write_family_completion_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = dict(HAPPY_SCRIPT)
    script["prompt_tool_updates"] = OPENCODE_STYLE_ROGUE_EDIT_UPDATES
    script["final_message"] = "Done. File created."
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())

    # The honest backstop: a write-family tool completed under a read-only
    # grant with no mediation — the Run must never persist completed.
    assert result.status is AgentRunStatus.FAILED
    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["status"] == "failed"
    assert payload["detail_code"] == "PERMISSION_VIOLATION"
    assert payload["stop_reason"] == "end_turn"
    assert payload["origin"] == "acp"
    assert payload["retryable"] is False
    events = _run_events(run_dir)
    violations = [e for e in events if e.get("type") == "permission_violation"]
    assert violations, "expected durable permission_violation evidence"
    assert violations[0]["kind"] == "edit"
    assert violations[0]["tool_call_id"] == "call-edit-1"
    assert violations[0]["required_capability"] == "write"
    progress = json.loads((run_dir / "progress.json").read_text())
    assert progress["state"] == "failed"
    # The fail-closed terminal is itself a TRUSTED Native terminal shape.
    state = storage.read_native_terminal_result(
        run_dir / "result.json", run_id="run-0001"
    )
    assert state.kind is storage.NativeTerminalKind.TRUSTED


def test_unmediated_write_family_completion_emits_no_run_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Honest durable stream for the backstop: a Run whose permission
    # violation already forces FAILED must not first record a contradictory
    # run_completed lifecycle fact — exactly one run_failed terminal event.
    script = dict(HAPPY_SCRIPT)
    script["prompt_tool_updates"] = OPENCODE_STYLE_ROGUE_EDIT_UPDATES
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    events = _run_events(harness.run_dir())
    families = [e.get("type") for e in events]
    assert "run_completed" not in families
    run_failed = [e for e in events if e.get("type") == "run_failed"]
    assert len(run_failed) == 1
    assert run_failed[0]["code"] == "FAILED"


@pytest.mark.parametrize("kind", ["edit", "delete", "move", "execute"])
def test_read_only_grant_denies_write_family_asks_before_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # The prevention path in hermetic form: when an agent routes a write-family
    # operation through session/request_permission, the frozen read-only grant
    # denies BEFORE the side effect — sentinel absent, deny evidence durable.
    sentinel_name = f"ARS_{kind.upper()}_SENTINEL_MUST_NOT_EXIST"
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {"kind": kind, "path": sentinel_name}
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    assert not (harness.workspace / sentinel_name).exists()
    assert list(harness.workspace.iterdir()) == []
    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["status"] == "completed"
    assert payload["final_message"] == "ASK_DENIED"
    events = _run_events(run_dir)
    mediation = [e for e in events if e.get("type") == "permission_mediation"]
    write_denies = [
        e
        for e in mediation
        if e.get("decision") == "deny"
        and e.get("requested_op") == f"permission:{kind}"
    ]
    assert write_denies, "expected a write-family deny mediation event"
    assert write_denies[0]["tool_call_id"] == "perm-call-1"
    assert not [e for e in events if e.get("type") == "permission_violation"]
    # The healthy mediated-deny turn keeps exactly its completed lifecycle
    # fact: run_completed present, no run_failed.
    assert [e for e in events if e.get("type") == "run_completed"]
    assert not [e for e in events if e.get("type") == "run_failed"]


def test_read_ask_is_client_mediated_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The allow half of the mediated path: a read-like ask under the read
    # grant, declaring a workspace-internal location, selects a real allow
    # option id on the wire.
    inside = (tmp_path / "workspace" / "doc.md").resolve()
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {
        "kind": "read",
        "locations": [{"path": str(inside)}],
    }
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "ASK_ALLOWED"
    events = _run_events(harness.run_dir())
    allows = [
        e
        for e in events
        if e.get("type") == "permission_mediation"
        and e.get("decision") == "allow"
        and e.get("requested_op") == "permission:read"
    ]
    assert allows, "expected an allow mediation event for the read-like ask"


def test_permission_read_outside_location_is_denied_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The read grant is present, but the only path the protocol declares is
    # outside the bound workspace. Through the real ACP callback chain that is
    # a refusal, and a refusal the agent honors is a healthy completed Run.
    outside = (tmp_path / "outside.txt").resolve()
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {
        "kind": "read",
        "locations": [{"path": str(outside)}],
    }
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "ASK_DENIED"
    events = _run_events(harness.run_dir())
    denies = [
        e
        for e in events
        if e.get("type") == "permission_mediation"
        and e.get("requested_op") == "permission:read"
        and e.get("decision") == "deny"
    ]
    assert denies, "expected a read deny mediation event"
    assert denies[0]["tool_call_id"] == "perm-call-1"
    # The categorical reason travels; the refused path never does.
    assert str(outside) not in json.dumps(events)
    assert not [e for e in events if e.get("type") == "permission_violation"]


def test_permission_read_without_locations_is_denied_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An adapter that declares no location supplies no containment evidence at
    # all. Default-deny is the whole contract; it is not a lane ARS widens.
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {"kind": "search"}
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "ASK_DENIED"
    events = _run_events(harness.run_dir())
    assert [
        e
        for e in events
        if e.get("type") == "permission_mediation"
        and e.get("requested_op") == "permission:search"
        and e.get("decision") == "deny"
        and e.get("reason") == "read-like permission request has no usable locations"
    ]


def test_tool_completion_after_permission_deny_fails_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARS denied the ask and the agent reported that same call completed. ARS
    # cannot undo the side effect; it must not let the Run read as if the
    # denial held.
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {
        "kind": "edit",
        "status_after_response": "completed",
    }
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["status"] == "failed"
    assert payload["detail_code"] == "PERMISSION_VIOLATION"
    assert payload["retryable"] is False
    assert payload["stop_reason"] == "end_turn"
    assert payload["origin"] == "acp"
    # A recorded exit signal only exists because ARS awaited the child's exit
    # status: the fixture blocks on stdin until ARS stops it, so the violation
    # terminal was published after a proven reap, not over a live child.
    assert payload["signal"] == signal.SIGTERM
    events = _run_events(run_dir)
    violations = [e for e in events if e.get("type") == "permission_violation"]
    assert violations, "expected durable permission_violation evidence"
    assert violations[0]["violation_class"] == "completed_after_deny"
    assert violations[0]["tool_call_id"] == "perm-call-1"
    assert violations[0]["kind"] == "edit"
    assert "required_capability" not in violations[0]
    # The lifecycle stream stays honest: no run_completed for a failed Run.
    assert "run_completed" not in [e.get("type") for e in events]
    assert len([e for e in events if e.get("type") == "run_failed"]) == 1
    assert json.loads((run_dir / "progress.json").read_text())["state"] == "failed"
    state = storage.read_native_terminal_result(
        run_dir / "result.json", run_id="run-0001"
    )
    assert state.kind is storage.NativeTerminalKind.TRUSTED
    # A permission violation is a trustworthy failed Run, not a continuity
    # doubt: the Session stays reusable.
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is None
    assert result.session_quarantined is not True


def test_failed_tool_after_permission_deny_remains_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The honored-refusal control for the same wire shape: the denied call
    # reports failed, which is exactly what a cooperating agent does.
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {
        "kind": "edit",
        "status_after_response": "failed",
    }
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["status"] == "completed"
    assert payload["final_message"] == "ASK_DENIED"
    assert payload.get("detail_code") is None
    events = _run_events(harness.run_dir())
    assert not [e for e in events if e.get("type") == "permission_violation"]
    assert [e for e in events if e.get("type") == "run_completed"]
    assert not [e for e in events if e.get("type") == "run_failed"]
    record = harness.session_store().open_session("sess-native-1")
    assert record.quarantine is None


OUTSIDE_READ_TOKEN = "ARS_OUTSIDE_READ_TOKEN_MUST_NOT_LEAK"


def _outside_target(tmp_path: Path) -> Path:
    outside = tmp_path / "outside-read-target.txt"
    outside.write_text(OUTSIDE_READ_TOKEN, encoding="utf-8")
    return outside


def test_read_tool_completion_after_denied_host_read_fails_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The second mediated read path, end to end over the real ACP wire: a
    # structured read call is open, ARS refuses the ``fs/read_text_file`` it
    # issued, and the agent reports that same call completed. ARS cannot undo
    # whatever the agent did instead — it can only refuse to persist a Run that
    # reads as if the refusal held.
    outside = _outside_target(tmp_path)
    script = dict(HAPPY_SCRIPT)
    script["fs_read_tool"] = {
        "tool_call_id": "read-call-1",
        "path": str(outside),
        "status_after_response": "completed",
    }
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["status"] == "failed"
    assert payload["detail_code"] == "PERMISSION_VIOLATION"
    assert payload["stop_reason"] == "end_turn"
    assert payload["origin"] == "acp"
    assert payload["retryable"] is False

    events = _run_events(run_dir)
    denials = [
        event
        for event in events
        if event.get("type") == "permission_mediation"
        and event.get("requested_op") == "fs_read"
        and event.get("decision") == "deny"
    ]
    assert len(denials) == 1
    assert denials[0]["tool_call_id"] == "read-call-1"
    violations = [e for e in events if e.get("type") == "permission_violation"]
    assert len(violations) == 1
    assert violations[0]["violation_class"] == "completed_after_deny"
    assert violations[0]["tool_call_id"] == "read-call-1"
    assert violations[0]["kind"] == "read"
    assert "required_capability" not in violations[0]
    assert "run_completed" not in [e.get("type") for e in events]
    assert len([e for e in events if e.get("type") == "run_failed"]) == 1

    # The refused content never became evidence: the fixture asked for a token
    # outside the workspace, was refused, and neither the durable stream nor
    # the terminal carries it. Structural evidence — ids, kinds, categorical
    # reasons — is all that survives.
    assert OUTSIDE_READ_TOKEN not in json.dumps(events)
    assert OUTSIDE_READ_TOKEN not in (run_dir / "result.json").read_text()
    assert str(outside) not in json.dumps(events)
    assert "FS_DENIED" in payload["final_message"]

    # The terminal was published over a proven-reaped child, not a live one.
    assert payload["signal"] == signal.SIGTERM
    identity = json.loads((run_dir / "effective.json").read_text())["process_identity"]
    _assert_pid_gone_sync(identity["pid"])
    assert json.loads((run_dir / "progress.json").read_text())["state"] == "failed"
    state = storage.read_native_terminal_result(
        run_dir / "result.json", run_id="run-0001"
    )
    assert state.kind is storage.NativeTerminalKind.TRUSTED
    # A permission violation is a trustworthy failed Run, not continuity doubt.
    assert harness.session_store().open_session("sess-native-1").quarantine is None
    assert result.session_quarantined is not True


def test_failed_read_tool_after_denied_host_read_remains_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The honored-refusal control for the identical wire flow: the read call
    # that was refused reports failed, which is what a cooperating agent does.
    # The Run completes normally and the deny mediation is still durable.
    outside = _outside_target(tmp_path)
    script = dict(HAPPY_SCRIPT)
    script["fs_read_tool"] = {
        "tool_call_id": "read-call-1",
        "path": str(outside),
        "status_after_response": "failed",
    }
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED, result.payload
    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["status"] == "completed"
    assert payload.get("detail_code") is None
    assert "FS_DENIED" in payload["final_message"]
    assert OUTSIDE_READ_TOKEN not in (run_dir / "result.json").read_text()

    events = _run_events(run_dir)
    assert not [e for e in events if e.get("type") == "permission_violation"]
    denials = [
        event
        for event in events
        if event.get("type") == "permission_mediation"
        and event.get("requested_op") == "fs_read"
        and event.get("decision") == "deny"
    ]
    assert len(denials) == 1
    assert denials[0]["tool_call_id"] == "read-call-1"
    assert [e for e in events if e.get("type") == "run_completed"]
    assert not [e for e in events if e.get("type") == "run_failed"]
    assert harness.session_store().open_session("sess-native-1").quarantine is None


def test_workspace_read_tool_completion_is_not_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ordinary allowed lane, with the same structured lifecycle: a
    # workspace-internal read is allowed, the call completes, and nothing about
    # that is a contradiction. Correlation must not turn every completed read
    # into a violation just because a host read happened.
    script = dict(HAPPY_SCRIPT)
    script["fs_read_tool"] = {
        "tool_call_id": "read-call-1",
        "path": "inside.txt",
        "status_after_response": "completed",
    }
    harness = Harness(tmp_path, monkeypatch, script)
    (harness.workspace / "inside.txt").write_text("INSIDE_OK", encoding="utf-8")

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED, result.payload
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert "FS_CONTENT:INSIDE_OK" in payload["final_message"]
    events = _run_events(harness.run_dir())
    assert not [e for e in events if e.get("type") == "permission_violation"]
    allows = [
        event
        for event in events
        if event.get("type") == "permission_mediation"
        and event.get("requested_op") == "fs_read"
        and event.get("decision") == "allow"
    ]
    assert len(allows) == 1
    assert [e for e in events if e.get("type") == "run_completed"]


def test_registered_permission_env_reaches_spawned_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Launch binding must reach the real spawned process environment (not only
    # serialization). It can no longer be proven by echoing the value into the
    # final message: every projected environment value is guarded out of every
    # ARS sink, so the value would be erased before it reached ``result.json``
    # and the assertion would prove the guard, not the delivery. The child
    # therefore reports delivery to a test-owned file outside the Run tree and
    # answers ARS with a value-free marker.
    mediation_id = "ask-privileged-tool-families-v1"
    key, binding_value = profile_module.MEDIATION_BINDINGS[mediation_id][0]
    probe_path = tmp_path / "child-env-probe.txt"
    script = dict(HAPPY_SCRIPT)
    script["env_probe"] = {"name": key, "path": str(probe_path)}
    harness = Harness(tmp_path, monkeypatch, script)
    harness.entry = _test_entry(mediation_id=mediation_id)
    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    assert probe_path.read_text(encoding="utf-8") == binding_value
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "ENV_PROBE:PRESENT"
    # B2b: the launch record now names the binding it *selected* and never its
    # source-owned pairs, so the mediation value is durable nowhere.
    launch_payload = json.loads((harness.run_dir() / "launch.json").read_text())
    assert launch_payload["mediation_id"] == mediation_id
    assert "permission_env" not in launch_payload
    assert binding_value not in json.dumps(launch_payload)
    names = {item["name"] for item in launch_payload["env"]["names"]}
    assert key in names


# -- B4: frozen permission mode + execute mediation over a Claude-shaped Run --

CLAUDE_SHAPED_PERMISSION_OPTIONS = [
    # The official Claude adapter advertises the always-scoped option FIRST.
    {"optionId": "allow_always", "name": "Always allow", "kind": "allow_always"},
    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
]


def _mode_option(current: str, values=("default", "acceptEdits", "bypassPermissions")):
    return {
        "id": "mode",
        "name": "Permission mode",
        "type": "select",
        "currentValue": current,
        "options": [{"value": value, "name": value} for value in values],
    }


def _claude_shaped_script(initial_mode: str = "bypassPermissions", **overrides) -> dict:
    script = {
        "initial_options": [
            _mode_option(initial_mode),
            {
                "id": "model",
                "name": "Model",
                "type": "select",
                "currentValue": "claude-fable-5[1m]",
                "options": [
                    {"value": "claude-fable-5[1m]", "name": "Fable"},
                    {"value": "opus[1m]", "name": "Opus"},
                ],
            },
            {
                "id": "effort",
                "name": "Effort",
                "type": "select",
                "currentValue": "max",
                "options": [{"value": "max", "name": "Max"}],
            },
        ],
        "final_message": "FAKE_AGENT_OK",
    }
    script.update(overrides)
    return script


CLAUDE_COMPAT_PROFILE_ID = "fake-claude-compat-v1"


def harness_methods(harness: Harness) -> list[str]:
    """The ACP methods the fake agent actually observed, in wire order."""
    return harness.methods_seen()


def _claude_shaped_profile(**overrides) -> AcpCompatProfile:
    """The compat profile's shape: a frozen mode selector plus frozen ``_meta``.

    This is the surviving ACP-semantic deviation — the one thing
    ``claude-agent-acp-compat-v1`` exists to carry — driven here over the fake
    agent rather than over any real adapter.
    """
    kwargs = dict(
        profile_id=CLAUDE_COMPAT_PROFILE_ID,
        revision=1,
        acp_protocol_version="1",
        required_capabilities=(),
        base_allowlist=("PATH", "HOME", "FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
        requires_session_load=False,
        permission_mode_selector_id="mode",
        required_permission_mode="default",
    )
    kwargs.update(overrides)
    return AcpCompatProfile(**kwargs)


def _claude_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: dict,
    *,
    profile: AcpCompatProfile | None = None,
) -> Harness:
    harness = Harness(tmp_path, monkeypatch, script)
    resolved = profile or _claude_shaped_profile()
    harness.registry = ProfileRegistry((resolved,))
    harness.entry = _test_entry(profile_id=resolved.profile_id)
    return harness


def _claude_request(**overrides) -> AgentRunRequest:
    kwargs = dict(requested_model="opus[1m]", requested_effort="max")
    kwargs.update(overrides)
    return _request(**kwargs)


def test_hostile_initial_permission_mode_is_switched_to_exact_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _claude_harness(tmp_path, monkeypatch, _claude_shaped_script())
    result = _run(harness.task(request=_claude_request()))

    assert result.status is AgentRunStatus.COMPLETED
    run_dir = harness.run_dir()
    effective = json.loads((run_dir / "effective.json").read_text())
    labels = [row["label"] for row in effective["discovery_snapshots"]]
    assert labels == ["initial", "post_mode", "post_model", "post_effort"]
    initial = {
        row["id"]: row
        for row in effective["discovery_snapshots"][0]["options"]
    }
    assert initial["mode"]["currentValue"] == "bypassPermissions"
    for label in ("post_mode", "post_effort"):
        snapshot = [
            row for row in effective["discovery_snapshots"] if row["label"] == label
        ][0]
        modes = {row["id"]: row for row in snapshot["options"]}
        assert modes["mode"]["currentValue"] == "default"
    assert effective["effective_model"] == "opus[1m]"
    assert effective["effective_effort"] == "max"


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("missing selector", "missing"),
        ("value outside domain", "domain"),
        ("inexact readback", "readback"),
    ],
)
def test_permission_mode_failures_refuse_before_any_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str, mutate: str
) -> None:
    script = _claude_shaped_script()
    if mutate == "missing":
        script["initial_options"] = [
            option for option in script["initial_options"] if option["id"] != "mode"
        ]
    elif mutate == "domain":
        script["initial_options"][0] = _mode_option(
            "bypassPermissions", values=("acceptEdits", "bypassPermissions")
        )
    else:
        script["wrong_readback"] = {"mode": "bypassPermissions"}

    harness = _claude_harness(tmp_path, monkeypatch, script)
    result = _run(harness.task(request=_claude_request()))

    assert result.status is AgentRunStatus.FAILED, label
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == "CONFIG_FIDELITY", label
    # Zero Turn: the fake agent never observed a prompt frame.
    assert "session/prompt" not in harness_methods(harness), label
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists(), label
    # The refusal still leaves the observed discovery evidence on disk.
    effective = json.loads((harness.run_dir() / "effective.json").read_text())
    labels = [row["label"] for row in effective["discovery_snapshots"]]
    assert labels[0] == "initial", label
    if mutate == "readback":
        # The mode the agent actually reported back is durable evidence.
        assert labels == ["initial", "post_mode"], label
        post_mode = {
            row["id"]: row for row in effective["discovery_snapshots"][1]["options"]
        }
        assert post_mode["mode"]["currentValue"] == "bypassPermissions", label


def test_execute_ask_is_denied_and_the_side_effect_never_happens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "ARS_B4_EXECUTE_MARKER_MUST_NOT_EXIST"
    choice = tmp_path / "permission-choice.txt"
    script = _claude_shaped_script()
    script["ask_permission"] = {
        "kind": "execute",
        "path": sentinel,
        "options": CLAUDE_SHAPED_PERMISSION_OPTIONS,
        "allow_option_ids": ["allow", "allow_always"],
        "choice_path": str(choice),
    }
    harness = _claude_harness(tmp_path, monkeypatch, script)
    # Frozen read-only grant: execute is not granted.
    result = _run(harness.task(request=_claude_request(grant_capabilities=("read",))))

    assert result.status is AgentRunStatus.COMPLETED
    assert not (harness.workspace / sentinel).exists()
    assert list(harness.workspace.iterdir()) == []
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "ASK_DENIED"
    # A denied decision is answered as a cancelled outcome, so the agent is
    # handed no option id at all — in particular never the always-scoped one.
    assert choice.read_text() == ""
    events = _run_events(harness.run_dir())
    denies = [
        e
        for e in events
        if e.get("type") == "permission_mediation"
        and e.get("requested_op") == "permission:execute"
        and e.get("decision") == "deny"
    ]
    assert denies, "expected an execute deny mediation event"


def test_execute_ask_allow_once_permits_only_the_granted_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "ars-b4-allow-marker"
    choice = tmp_path / "permission-choice.txt"
    script = _claude_shaped_script()
    script["ask_permission"] = {
        "kind": "execute",
        "path": str(tmp_path / "workspace" / marker),
        "content": "ARS_B4_ALLOW",
        "options": CLAUDE_SHAPED_PERMISSION_OPTIONS,
        "allow_option_ids": ["allow", "allow_always"],
        "choice_path": str(choice),
    }
    harness = _claude_harness(tmp_path, monkeypatch, script)
    result = _run(
        harness.task(request=_claude_request(grant_capabilities=("read", "execute")))
    )

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "ASK_ALLOWED"
    # The once-scoped option, never the always-scoped one the agent listed
    # first: an always grant would install a session rule that outlives the
    # mediated call.
    assert choice.read_text() == "allow"
    # Exactly the one granted inert side effect, nothing else.
    assert (harness.workspace / marker).read_text() == "ARS_B4_ALLOW"
    assert [path.name for path in harness.workspace.iterdir()] == [marker]
    events = _run_events(harness.run_dir())
    allows = [
        e
        for e in events
        if e.get("type") == "permission_mediation"
        and e.get("requested_op") == "permission:execute"
        and e.get("decision") == "allow"
    ]
    assert len(allows) == 1
    assert not [e for e in events if e.get("type") == "permission_violation"]


# -- B5: frozen session metadata on session/new AND session/load -------------

FROZEN_CLAUDE_SESSION_META_TEXT = (
    '{"claudeCode":{"options":{"settingSources":[],'
    '"tools":{"preset":"claude_code","type":"preset"}}}}'
)
FROZEN_CLAUDE_SESSION_META = {
    "claudeCode": {
        "options": {
            "settingSources": [],
            "tools": {"type": "preset", "preset": "claude_code"},
        }
    }
}


def _captured_meta(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_claude_shaped_run_sends_the_frozen_metadata_on_session_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "session-meta.jsonl"
    script = _claude_shaped_script(capture_meta_path=str(capture))
    profile = _claude_shaped_profile(session_meta=FROZEN_CLAUDE_SESSION_META_TEXT)
    harness = _claude_harness(tmp_path, monkeypatch, script, profile=profile)
    # Non-reuse intent is the only path that reaches ``session/new``.
    result = _run(
        harness.task(
            request=_claude_request(session_id=None)
        )
    )

    assert result.status is AgentRunStatus.COMPLETED
    captured = _captured_meta(capture)
    assert [row["method"] for row in captured] == ["session/new"]
    assert captured[0]["meta_present"] is True
    assert captured[0]["meta"] == FROZEN_CLAUDE_SESSION_META
    # The expected metadata is durable in launch.json before any wire call.
    launch = json.loads((harness.run_dir() / "launch.json").read_text())
    assert launch["session_meta"] == FROZEN_CLAUDE_SESSION_META


def test_claude_shaped_reused_session_sends_the_same_metadata_on_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "session-meta.jsonl"
    script = _claude_shaped_script(capture_meta_path=str(capture))
    profile = _claude_shaped_profile(
        requires_session_load=True,
        session_meta=FROZEN_CLAUDE_SESSION_META_TEXT,
    )
    harness = _claude_harness(tmp_path, monkeypatch, script, profile=profile)
    # First Run: non-reuse, so it creates its Session and calls session/new.
    # Second Run: reuse against an already-bound record, so it loads.
    first = _run(
        harness.task(
            run_id="run-0001",
            request=_claude_request(session_id=None),
        )
    )
    assert first.status is AgentRunStatus.COMPLETED
    second = _run(harness.task(run_id="run-0002", request=_claude_request()))
    assert second.status is AgentRunStatus.COMPLETED

    captured = _captured_meta(capture)
    assert [row["method"] for row in captured] == ["session/new", "session/load"]
    # Load is not "resume with whatever the session had": the same frozen
    # metadata must be re-sent, or ambient setting sources come back.
    assert all(row["meta_present"] is True for row in captured)
    assert captured[0]["meta"] == captured[1]["meta"] == FROZEN_CLAUDE_SESSION_META
    # Same external session identity across the load.
    first_effective = json.loads(
        (harness.run_dir("run-0001") / "effective.json").read_text()
    )
    second_effective = json.loads(
        (harness.run_dir("run-0002") / "effective.json").read_text()
    )
    assert (
        first_effective["agent_session_id"]
        == second_effective["agent_session_id"]
        == "fake-external-session-1"
    )


def test_profiles_without_metadata_send_no_meta_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "session-meta.jsonl"
    script = dict(HAPPY_SCRIPT)
    script["capture_meta_path"] = str(capture)
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    captured = _captured_meta(capture)
    assert captured
    # The argument is omitted entirely — no `_meta` key on the wire at all,
    # not a null one — so legacy frames stay byte-identical.
    assert all(row["meta_present"] is False for row in captured)
    assert all(row["meta"] is None for row in captured)
    launch = json.loads((harness.run_dir() / "launch.json").read_text())
    assert "session_meta" not in launch


def test_post_load_mode_fidelity_failure_refuses_before_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A reused Session must not inherit trust: if the reloaded session cannot
    # be proven back to the frozen permission mode, the Run ends zero-Turn.
    capture = tmp_path / "session-meta.jsonl"
    profile = _claude_shaped_profile(
        requires_session_load=True,
        session_meta=FROZEN_CLAUDE_SESSION_META_TEXT,
    )
    harness = _claude_harness(
        tmp_path,
        monkeypatch,
        _claude_shaped_script(capture_meta_path=str(capture)),
        profile=profile,
    )
    first = _run(harness.task(run_id="run-0001", request=_claude_request()))
    assert first.status is AgentRunStatus.COMPLETED

    hostile = _claude_shaped_script(
        capture_meta_path=str(capture), wrong_readback={"mode": "bypassPermissions"}
    )
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(hostile))
    second = _run(harness.task(run_id="run-0002", request=_claude_request()))

    assert second.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir("run-0002") / "result.json").read_text())
    assert payload["detail_code"] == "CONFIG_FIDELITY"
    assert not (harness.run_dir("run-0002") / DISPATCH_STARTED_MARKER).exists()
    # The load still carried the frozen metadata before the refusal.
    captured = _captured_meta(capture)
    assert captured[-1]["method"] == "session/load"
    assert captured[-1]["meta"] == FROZEN_CLAUDE_SESSION_META


# -- Reasonix: registered static approval fidelity on new and load -----------


def _reasonix_shaped_script(**overrides) -> dict:
    script = {
        "initial_options": [
            {
                "id": "model",
                "name": "Model",
                "type": "select",
                "currentValue": "reasonix/base",
                "options": [
                    {"value": "reasonix/base", "name": "Base"},
                    {"value": "reasonix/target", "name": "Target"},
                ],
            },
            {
                "id": "effort",
                "name": "Effort",
                "type": "select",
                "currentValue": "auto",
                "options": [
                    {"value": "auto", "name": "Auto"},
                    {"value": "high", "name": "High"},
                ],
            },
            {
                "id": "work_mode",
                "name": "Work mode",
                "type": "select",
                "currentValue": "balanced",
                "options": [
                    {"value": "balanced", "name": "Balanced"},
                    {"value": "delivery", "name": "Delivery"},
                ],
            },
            {
                "id": "tool_approval",
                "name": "Tool approval",
                "type": "select",
                "currentValue": "yolo",
                "options": [
                    {"value": "ask", "name": "Ask"},
                    {"value": "auto", "name": "Auto"},
                    {"value": "yolo", "name": "Yolo"},
                ],
            },
        ],
        "final_message": "REASONIX_COMPAT_OK",
    }
    script.update(overrides)
    return script


def _reasonix_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: dict
) -> Harness:
    harness = Harness(tmp_path, monkeypatch, script)
    profile = profile_module.DEFAULT_REGISTRY.get("reasonix-agent-acp-compat-v1")
    harness.registry = ProfileRegistry((profile,))
    harness.entry = _test_entry(
        profile_id=profile.profile_id,
        env_passthrough=("FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
    )
    return harness


def _reasonix_request(**overrides) -> AgentRunRequest:
    kwargs = dict(requested_model="reasonix/target", requested_effort="high")
    kwargs.update(overrides)
    return _request(**kwargs)


@pytest.mark.parametrize(
    ("session_id", "expected_session_method"),
    [(None, "session/new"), ("sess-native-1", "session/load")],
    ids=("new", "load"),
)
def test_registered_reasonix_sets_ask_then_model_then_effort_before_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_id: str | None,
    expected_session_method: str,
) -> None:
    capture = tmp_path / "reasonix-config-order.txt"
    script = _reasonix_shaped_script(capture_config_path=str(capture))
    harness = _reasonix_harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_reasonix_request(session_id=session_id)))

    assert result.status is AgentRunStatus.COMPLETED
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "tool_approval=ask",
        "model=reasonix/target",
        "effort=high",
    ]
    methods = harness.methods_seen()
    assert methods == [
        "initialize",
        expected_session_method,
        "session/set_config_option",
        "session/set_config_option",
        "session/set_config_option",
        "session/prompt",
    ]
    effective = json.loads((harness.run_dir() / "effective.json").read_text())
    snapshots = {
        row["label"]: {option["id"]: option for option in row["options"]}
        for row in effective["discovery_snapshots"]
    }
    assert list(snapshots) == ["initial", "post_mode", "post_model", "post_effort"]
    assert snapshots["initial"]["tool_approval"]["currentValue"] == "yolo"
    assert snapshots["post_mode"]["tool_approval"]["currentValue"] == "ask"
    assert snapshots["post_effort"]["tool_approval"]["currentValue"] == "ask"
    assert "work_mode" not in capture.read_text(encoding="utf-8")


@pytest.mark.parametrize("hostile_readback", ["auto", "yolo"])
def test_registered_reasonix_refuses_auto_or_yolo_readback_before_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hostile_readback: str,
) -> None:
    capture = tmp_path / "reasonix-hostile-readback.txt"
    script = _reasonix_shaped_script(
        capture_config_path=str(capture),
        wrong_readback={"tool_approval": hostile_readback},
    )
    harness = _reasonix_harness(tmp_path, monkeypatch, script)

    result = _run(
        harness.task(request=_reasonix_request(session_id=None))
    )

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == "CONFIG_FIDELITY"
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "tool_approval=ask"
    ]
    assert "session/prompt" not in harness.methods_seen()
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists()


def test_registered_reasonix_refuses_a_missing_approval_selector_before_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _reasonix_shaped_script()
    script["initial_options"] = [
        option
        for option in script["initial_options"]
        if option["id"] != "tool_approval"
    ]
    harness = _reasonix_harness(tmp_path, monkeypatch, script)

    result = _run(
        harness.task(request=_reasonix_request(session_id=None))
    )

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == "CONFIG_FIDELITY"
    assert "session/set_config_option" not in harness.methods_seen()
    assert "session/prompt" not in harness.methods_seen()


def test_registered_reasonix_load_without_config_options_is_zero_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fake agent stores external Session state under this configured root.
    # The seeded ARS record has no corresponding external file, so session/load
    # succeeds but reports configOptions=null: the real load-only failure shape.
    missing_agent_state = tmp_path / "reasonix-empty-state"
    script = _reasonix_shaped_script(config_home_env=["REASONIX_STATE_HOME"])
    harness = _reasonix_harness(tmp_path, monkeypatch, script)
    harness.entry = _test_entry(
        profile_id="reasonix-agent-acp-compat-v1",
        env_passthrough=("FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
        env_overlay=(("REASONIX_STATE_HOME", str(missing_agent_state)),),
    )

    result = _run(harness.task(request=_reasonix_request()))

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == "CONFIG_FIDELITY"
    assert harness.methods_seen() == ["initialize", "session/load"]
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists()

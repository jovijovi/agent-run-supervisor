"""C8 L2: RunTask vertical over the fake agent — admission, double markers,
finalization, write-once terminal facts, and the seeded-legacy isolation
vertical (the L2 half of the C6 regression)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import profile as profile_module
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.driver import NativeAcpDriver
from agent_run_supervisor.native_acp.profile import AgentProfile, ProfileRegistry
from agent_run_supervisor.native_acp.run_task import (
    NativeRunTaskError,
    RunTask,
)
from agent_run_supervisor.native_acp.spec import AgentRunRequest, InputRef, RunLimits
from agent_run_supervisor.session import SessionStore

FAKE_AGENT_PATH = Path(__file__).with_name("fake_agent.py")

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


def _test_profile() -> AgentProfile:
    return AgentProfile(
        profile_id="fake-agent-1.0",
        revision=1,
        executable_key="python-fake",
        argv_template=(str(FAKE_AGENT_PATH),),
        env_allowlist=("PATH", "HOME", "FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
        credential_slots=(),
        model_selector_id="model",
        effort_selector_id="effort",
        default_model="kimi-for-coding/k3",
        default_effort="max",
        allowed_efforts=("low", "medium", "high", "max"),
        requires_session_load=False,
        config_schema={"selectors": {"model": "string", "effort": "string"}},
    )


def _request(**overrides) -> AgentRunRequest:
    kwargs = dict(
        owner="hermes",
        namespace="hermes/doc-check",
        profile_id="fake-agent-1.0",
        session_reuse="reuse",
        ars_session_id="sess-native-1",
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
        monkeypatch.setitem(
            profile_module._REGISTERED_EXECUTABLES,
            "python-fake",
            Path(sys.executable),
        )
        monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
        monkeypatch.setenv("FAKE_AGENT_TRACE", str(self.trace))
        self.registry = ProfileRegistry((_test_profile(),))

    def task(self, *, run_id: str = "run-0001", request=None, **overrides) -> RunTask:
        kwargs = dict(
            request=request or _request(),
            prompt_text="hello agent",
            run_id=run_id,
            workspace_root=self.workspace,
            registry=self.registry,
            supervisor_root=self.root,
            submitted_at="2026-07-21T00:00:00+00:00",
        )
        kwargs.update(overrides)
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
    assert record.state == "open"
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
    assert record.state == "quarantined"
    assert record.quarantined_by_run_id == "run-0001"
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
    assert record.state == "quarantined"


def test_spawn_failure_is_pre_dispatch_failed_session_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    monkeypatch.setitem(
        profile_module._REGISTERED_EXECUTABLES,
        "python-fake",
        Path("/nonexistent/native-agent-binary"),
    )
    result = _run(harness.task())
    assert result.status is AgentRunStatus.FAILED
    run_dir = harness.run_dir()
    assert not (run_dir / "prompt-dispatch-started").exists()
    assert not (run_dir / "prompt-accepted").exists()
    record = harness.session_store().open_session("sess-native-1")
    assert record.state == "open"  # session stays active on 0-Turn failure
    assert not (harness.root / "native-sessions" / "sess-native-1" / "lock.json").exists()


def test_fidelity_failure_is_zero_turn_failed_session_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = dict(HAPPY_SCRIPT)
    script["post_model_options"] = [
        option for option in HAPPY_SCRIPT["initial_options"] if option["id"] != "effort"
    ]
    harness = Harness(tmp_path, monkeypatch, script)
    result = _run(harness.task())
    assert result.status is AgentRunStatus.FAILED
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    assert "session/prompt" not in harness.methods_seen()
    record = harness.session_store().open_session("sess-native-1")
    assert record.state == "open"
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
    request = _request(ars_session_id="sess-native-2")
    result2 = _run(harness.task(run_id="run-0002", request=request))
    assert result2.status is AgentRunStatus.FAILED
    assert snapshot(harness.root / "sessions") == before_sessions
    assert snapshot(harness.root / "runs") == before_runs
    record = harness.session_store().open_session("sess-native-2")
    assert record.state == "quarantined"


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

    request = _request(ars_session_id="sess-native-3")
    result = _run(
        harness.task(
            run_id="run-0002", request=request, retry_of_run_id="run-0001"
        )
    )
    assert result.status is AgentRunStatus.COMPLETED
    spec = json.loads((harness.run_dir("run-0002") / "spec.json").read_text())
    assert spec["retry_of_run_id"] == "run-0001"
    assert original_result.read_bytes() == before


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
    assert record.state == "open"  # 0-Turn cancel keeps the session reusable
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
    # Dispatched with no reliable ACP terminal: conservative, never completed.
    assert payload["status"] == "cancelled"
    assert payload["status"] != "completed"
    assert payload["detail_code"] == "SUPERVISOR_CANCELLED"
    assert payload["retryable"] is False
    assert (run_dir / "prompt-dispatch-started").exists()
    record = harness.session_store().open_session("sess-native-1")
    assert record.state == "quarantined"
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
    assert record.state == "quarantined"
    assert not (
        harness.root / "native-sessions" / "sess-native-1" / "lock.json"
    ).exists()
    _assert_pid_gone_sync(spawned[0].pid)


def test_session_reuse_none_uses_ephemeral_record_closed_at_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    request = _request(session_reuse="none", ars_session_id=None)
    result = _run(harness.task(request=request))
    assert result.status is AgentRunStatus.COMPLETED
    store = harness.session_store()
    records = store.list_records()
    assert len(records) == 1
    ephemeral = records[0]
    assert ephemeral.session_kind == "native"
    assert ephemeral.state == "closed"

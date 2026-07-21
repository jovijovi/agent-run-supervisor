"""C9 L2: session/load continuity + controlled cross-Run model/effort
switching with exact rollback or quarantine (PRD R4)."""

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
from agent_run_supervisor.native_acp.profile import AgentProfile, ProfileRegistry
from agent_run_supervisor.native_acp.run_task import RunTask
from agent_run_supervisor.native_acp.spec import AgentRunRequest, InputRef, RunLimits

FAKE_AGENT_PATH = Path(__file__).with_name("fake_agent.py")

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


def _profile() -> AgentProfile:
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
        requires_session_load=True,
        config_schema={"selectors": {"model": "string", "effort": "string"}},
    )


def _request(model: str, effort: str, session_id: str = "sess-switch-1", **overrides):
    kwargs = dict(
        owner="hermes",
        namespace="hermes/doc-check",
        profile_id="fake-agent-1.0",
        session_reuse="reuse",
        ars_session_id=session_id,
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
        monkeypatch.setitem(
            profile_module._REGISTERED_EXECUTABLES,
            "python-fake",
            Path(sys.executable),
        )
        self.registry = ProfileRegistry((_profile(),))

    def run(self, run_id: str, script: dict, request: AgentRunRequest, **overrides):
        self.monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script))
        self.monkeypatch.setenv("FAKE_AGENT_TRACE", str(self.trace_path(run_id)))
        task = RunTask(
            request=request,
            prompt_text=f"prompt for {run_id}",
            run_id=run_id,
            workspace_root=self.workspace,
            registry=self.registry,
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
        result = self.run("run-0001", FIRST_RUN_SCRIPT, _request("provider/base", "high"))
        assert result.status is AgentRunStatus.COMPLETED
        record = self.record()
        assert record.agent_session_id == "fake-external-session-1"
        assert record.last_effective_model == "provider/base"
        assert record.last_effective_effort == "high"


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
    assert record.state == "open"

    payload = json.loads(
        (harness.root / "native-runs" / "run-0002" / "result.json").read_text()
    )
    assert payload["status"] == "completed"


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
    assert record.state == "open"  # the real session was never prompted
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
    assert payload["detail_code"] == "LOAD_SESSION_UNADVERTISED"
    assert "session/load" not in harness.methods("run-0002")
    assert "session/prompt" not in harness.methods("run-0002")
    assert harness.record().state == "open"


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
    assert record.state == "open"  # rollback proven ⇒ session re-opened
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
    assert record.state == "open"
    assert record.last_effective_model == "provider/base"
    events = (harness.root / "native-runs" / "run-0002" / "events.jsonl").read_text()
    assert "config_rollback_proven" in events


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
    assert record.state == "open"
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
    assert record.state == "quarantined"
    assert record.quarantined_by_run_id == "run-0002"
    events = (harness.root / "native-runs" / "run-0002" / "events.jsonl").read_text()
    assert "config_rollback_failed" in events


def test_quarantined_session_refuses_new_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()
    storage.native_session_store(harness.root).mark_quarantined(
        "sess-switch-1", reason="prior uncertainty", run_id="run-0002"
    )

    result = harness.run(
        "run-0003", HAPPY_SWITCH_SCRIPT, _request("kimi-for-coding/k3", "max")
    )
    assert result.status is AgentRunStatus.FAILED
    # Refused before any agent spawn: the fake never ran.
    assert harness.methods("run-0003") == []
    assert harness.record().state == "quarantined"


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

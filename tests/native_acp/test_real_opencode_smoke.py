"""C10: opt-in real OpenCode 1.18.4 B-grade smokes (Stage-1 exit evidence).

Skips unless ``ARS_NATIVE_SMOKE=1`` (never runs in CI). Each smoke drives the
new Native ACP implementation (RunTask → ManagedProcess → SDK driver) against
the registered real OpenCode 1.18.4 in a disposable empty workspace under a
fresh temp root outside any tracked worktree. Artifacts may contain model
output and stay out of git; the test prints their paths and, when
``ARS_NATIVE_SMOKE_EVIDENCE_DIR`` is set, writes sanitized structural
summaries there. Failures are real-world evidence to triage — never papered
over.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("acp")

pytestmark = pytest.mark.skipif(
    os.environ.get("ARS_NATIVE_SMOKE") != "1",
    reason="opt-in real OpenCode smoke; set ARS_NATIVE_SMOKE=1",
)

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.run_task import NativeRunResult, RunTask
from agent_run_supervisor.native_acp.spec import AgentRunRequest, InputRef, RunLimits

REQUIRED_MODEL = "kimi-for-coding/k3"
REQUIRED_EFFORT = "max"
# Chair-approved second model from the Phase-A zero-prompt capability probe:
# the exact advertised ID whose post-set-model option set carries the effort
# selector with literal offered values (high|max). Never a guess or alias;
# real usability is proven by the S3 model-switch acceptance itself.
SECOND_MODEL = "deepseek/deepseek-v4-pro"
SECOND_EFFORT = "high"

RUN_TIMEOUT_SECONDS = 900


def _fresh_root(tag: str) -> tuple[Path, Path]:
    base = Path(tempfile.mkdtemp(prefix=f"ars-native-smoke-{tag}-"))
    workspace = base / "workspace"
    workspace.mkdir()
    return base / "supervisor", workspace


def _request(model: str, effort: str, session_id: str) -> AgentRunRequest:
    return AgentRunRequest(
        owner="hermes",
        namespace="hermes/native-smoke",
        profile_id="opencode-1.18.4",
        session_reuse="reuse",
        ars_session_id=session_id,
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "0" * 64),),
        requested_model=model,
        requested_effort=effort,
        grant_ref="grant:native-smoke",
        grant_hash="sha256:" + "1" * 64,
        grant_role_hash="sha256:" + "2" * 64,
        grant_capabilities=("read",),
        mcp_snapshot_hashes=(),
        credential_refs=("kimi-for-coding", "deepseek"),
        limits=RunLimits(),
        evidence_policy_hash="sha256:" + "3" * 64,
        recovery_policy_hash="sha256:" + "4" * 64,
    )


def _run(
    root: Path,
    workspace: Path,
    run_id: str,
    request: AgentRunRequest,
    prompt: str,
) -> NativeRunResult:
    task = RunTask(
        request=request,
        prompt_text=prompt,
        run_id=run_id,
        workspace_root=workspace,
        supervisor_root=root,
    )

    async def case() -> NativeRunResult:
        return await asyncio.wait_for(task.run(), RUN_TIMEOUT_SECONDS)

    result = asyncio.run(case())
    print(f"[smoke] {run_id}: status={result.status.value} artifacts={result.run_dir}")
    return result


def _events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]


def _effective(run_dir: Path) -> dict:
    return json.loads((run_dir / "effective.json").read_text(encoding="utf-8"))


def _assert_pid_gone(pid: int, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"agent process {pid} survived the run (operator: pgrep -f opencode)")


def _evidence(name: str, payload: dict) -> None:
    evidence_dir = os.environ.get("ARS_NATIVE_SMOKE_EVIDENCE_DIR")
    if not evidence_dir:
        return
    target = Path(evidence_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def _spec_effective_equality(run_dir: Path) -> None:
    spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
    effective = _effective(run_dir)
    assert spec["runtime"]["model_id"] == effective["effective_model"]
    assert spec["runtime"]["effort"] == effective["effective_effort"]
    assert spec["runtime"]["config_fidelity"] == "exact"


def test_s1_readonly_exact_config_new_session() -> None:
    root, workspace = _fresh_root("s1")
    assert sorted(entry.name for entry in workspace.iterdir()) == []
    marker = "ARS_NATIVE_SMOKE_S1_OK"
    result = _run(
        root,
        workspace,
        "run-s1",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, "smoke-s1"),
        f"Reply with exactly {marker} and nothing else. Do not use any tools. "
        "Do not read or write any files.",
    )
    run_dir = result.run_dir
    assert result.status is AgentRunStatus.COMPLETED, result.payload
    payload = result.payload
    assert payload["stop_reason"] == "end_turn"
    assert marker in payload["final_message"]
    assert payload["retryable"] is False

    effective = _effective(run_dir)
    assert effective["effective_model"] == REQUIRED_MODEL  # literal K3
    assert effective["effective_effort"] == REQUIRED_EFFORT  # literal max
    # G6 checkpoint: loadSession advertised at the first real handshake.
    assert effective["load_session_advertised"] is True
    labels = [snapshot["label"] for snapshot in effective["discovery_snapshots"]]
    assert labels[:3] == ["initial", "post_model", "post_effort"]
    _spec_effective_equality(run_dir)

    events = _events(run_dir)
    assert sum(1 for event in events if event["type"] == "session_prompt_sent") == 1
    assert (run_dir / "prompt-dispatch-started").exists()
    assert (run_dir / "prompt-accepted").exists()
    redaction = json.loads((run_dir / "redaction-report.json").read_text())
    assert redaction["matches"] == []

    # Primary no-change evidence: direct pre/post listing of the disposable
    # known-empty workspace.
    assert sorted(entry.name for entry in workspace.iterdir()) == []
    identity = effective["process_identity"]
    _assert_pid_gone(identity["pid"])

    record = storage.native_session_store(root).open_session("smoke-s1")
    assert record.agent_session_id
    assert record.state == "open"

    _evidence(
        "s1-readonly",
        {
            "run_id": "run-s1",
            "status": payload["status"],
            "stop_reason": payload["stop_reason"],
            "final_message_matches_marker": marker in payload["final_message"],
            "effective_model": effective["effective_model"],
            "effective_effort": effective["effective_effort"],
            "load_session_advertised": effective["load_session_advertised"],
            "discovery_snapshot_labels": labels,
            "prompt_sent_events": 1,
            "markers_present": True,
            "redaction_matches": [],
            "workspace_pre_post_empty": True,
            "agent_pid_reaped": True,
            "agent_session_id_bound": bool(record.agent_session_id),
            "artifact_dir": str(run_dir),
        },
    )


def test_s2_session_load_nonce_continuity() -> None:
    root, workspace = _fresh_root("s2")
    nonce = "NONCE-" + secrets.token_hex(8)
    r1 = _run(
        root,
        workspace,
        "run-s2a",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, "smoke-s2"),
        f"Remember this token exactly: {nonce}. Reply with exactly STORED "
        "and nothing else. Do not use any tools.",
    )
    assert r1.status is AgentRunStatus.COMPLETED, r1.payload
    store = storage.native_session_store(root)
    external_id = store.open_session("smoke-s2").agent_session_id
    assert external_id

    r2 = _run(
        root,
        workspace,
        "run-s2b",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, "smoke-s2"),
        "Reply with the exact token I asked you to remember earlier in this "
        "conversation, and nothing else. Do not use any tools.",
    )
    assert r2.status is AgentRunStatus.COMPLETED, r2.payload

    families = [event["type"] for event in _events(r2.run_dir)]
    assert "session_load_requested" in families
    assert "session_new_requested" not in families
    # Historical-token continuity across process-per-Run: the recall answer
    # must contain the planted nonce.
    assert nonce in r2.payload["final_message"]
    record = store.open_session("smoke-s2")
    assert record.agent_session_id == external_id  # external ID unchanged

    _evidence(
        "s2-load-continuity",
        {
            "nonce_planted_run": "run-s2a",
            "nonce_recalled_run": "run-s2b",
            "nonce": nonce,
            "nonce_recalled": nonce in r2.payload["final_message"],
            "external_session_id_unchanged": record.agent_session_id == external_id,
            "r2_used_session_load": "session_load_requested" in families,
            "r2_never_called_session_new": "session_new_requested" not in families,
            "artifact_dirs": [str(r1.run_dir), str(r2.run_dir)],
        },
    )


def test_s3_model_switch_with_exact_readback() -> None:
    """Real between-Run model switching on the same external session
    (chair-approved acceptance replacing the earlier named-gap skip):
    K3/max baseline → deepseek-v4-pro/high → K3/max, all three Runs on one
    external Agent Session through process-per-Run session/load."""
    root, workspace = _fresh_root("s3m")
    store = storage.native_session_store(root)
    assert sorted(entry.name for entry in workspace.iterdir()) == []

    r1 = _run(
        root,
        workspace,
        "run-s3m-a",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, "smoke-s3m"),
        "Reply with exactly S3M_BASELINE_OK and nothing else. Do not use any tools.",
    )
    assert r1.status is AgentRunStatus.COMPLETED, r1.payload
    _spec_effective_equality(r1.run_dir)
    record = store.open_session("smoke-s3m")
    external_id = record.agent_session_id
    assert external_id
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        REQUIRED_EFFORT,
    )

    # Real model-ID switch with an offered literal effort, exact-readback
    # gated before the prompt.
    r2 = _run(
        root,
        workspace,
        "run-s3m-b",
        _request(SECOND_MODEL, SECOND_EFFORT, "smoke-s3m"),
        "Reply with exactly S3M_SWITCH_OK and nothing else. Do not use any tools.",
    )
    assert r2.status is AgentRunStatus.COMPLETED, r2.payload
    effective2 = _effective(r2.run_dir)
    assert effective2["effective_model"] == SECOND_MODEL
    assert effective2["effective_effort"] == SECOND_EFFORT
    _spec_effective_equality(r2.run_dir)
    families2 = [event["type"] for event in _events(r2.run_dir)]
    assert "session_load_requested" in families2
    assert "session_new_requested" not in families2
    record = store.open_session("smoke-s3m")
    assert record.agent_session_id == external_id
    assert (record.last_effective_model, record.last_effective_effort) == (
        SECOND_MODEL,
        SECOND_EFFORT,
    )

    # Switch back to literal K3/max: the switch-back proof.
    r3 = _run(
        root,
        workspace,
        "run-s3m-c",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, "smoke-s3m"),
        "Reply with exactly S3M_RETURN_OK and nothing else. Do not use any tools.",
    )
    assert r3.status is AgentRunStatus.COMPLETED, r3.payload
    effective3 = _effective(r3.run_dir)
    assert effective3["effective_model"] == REQUIRED_MODEL
    assert effective3["effective_effort"] == REQUIRED_EFFORT
    _spec_effective_equality(r3.run_dir)
    families3 = [event["type"] for event in _events(r3.run_dir)]
    assert "session_load_requested" in families3
    assert "session_new_requested" not in families3
    record = store.open_session("smoke-s3m")
    assert record.agent_session_id == external_id  # unchanged across all three
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        REQUIRED_EFFORT,
    )

    for result in (r1, r2, r3):
        assert (result.run_dir / "prompt-dispatch-started").exists()
        assert (result.run_dir / "prompt-accepted").exists()
        identity = _effective(result.run_dir)["process_identity"]
        _assert_pid_gone(identity["pid"])
    assert sorted(entry.name for entry in workspace.iterdir()) == []

    _evidence(
        "s3-model-switch",
        {
            "chair_approved_second_model": SECOND_MODEL,
            "offered_effort_used": SECOND_EFFORT,
            "baseline_pair": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "switched_pair": [SECOND_MODEL, SECOND_EFFORT],
            "switch_back_pair": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "exact_readback_before_each_prompt": True,
            "external_session_id_unchanged": True,
            "reuse_runs_used_session_load_only": True,
            "last_effective_commits_verified": True,
            "markers_present_each_run": True,
            "workspace_pre_post_empty": True,
            "children_reaped": True,
            "artifact_dirs": [str(r1.run_dir), str(r2.run_dir), str(r3.run_dir)],
        },
    )


def test_s3_effort_switch_with_exact_readback() -> None:
    """Real between-Run effort switching on the same external session
    (max→high→max on K3, each exact-readback gated, last_effective
    committed, session/load reuse) — complements the model-switch
    acceptance above."""
    root, workspace = _fresh_root("s3")
    store = storage.native_session_store(root)

    r1 = _run(
        root,
        workspace,
        "run-s3a",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, "smoke-s3"),
        "Reply with exactly S3_BASELINE_OK and nothing else. Do not use any tools.",
    )
    assert r1.status is AgentRunStatus.COMPLETED, r1.payload
    record = store.open_session("smoke-s3")
    external_id = record.agent_session_id
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        REQUIRED_EFFORT,
    )

    # Real effort switch max→high with exact readback on the loaded session.
    r2 = _run(
        root,
        workspace,
        "run-s3b",
        _request(REQUIRED_MODEL, "high", "smoke-s3"),
        "Reply with exactly S3_SWITCH_OK and nothing else. Do not use any tools.",
    )
    assert r2.status is AgentRunStatus.COMPLETED, r2.payload
    effective2 = _effective(r2.run_dir)
    assert effective2["effective_model"] == REQUIRED_MODEL
    assert effective2["effective_effort"] == SECOND_EFFORT
    _spec_effective_equality(r2.run_dir)
    record = store.open_session("smoke-s3")
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        SECOND_EFFORT,
    )
    families2 = [event["type"] for event in _events(r2.run_dir)]
    assert "session_load_requested" in families2
    assert "session_new_requested" not in families2

    # Switch back to literal max: a second real effort switch, exact again.
    r3 = _run(
        root,
        workspace,
        "run-s3c",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, "smoke-s3"),
        "Reply with exactly S3_RETURN_OK and nothing else. Do not use any tools.",
    )
    assert r3.status is AgentRunStatus.COMPLETED, r3.payload
    effective3 = _effective(r3.run_dir)
    assert effective3["effective_model"] == REQUIRED_MODEL
    assert effective3["effective_effort"] == REQUIRED_EFFORT
    _spec_effective_equality(r3.run_dir)
    record = store.open_session("smoke-s3")
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        REQUIRED_EFFORT,
    )
    assert record.agent_session_id == external_id

    _evidence(
        "s3-effort-switch",
        {
            "baseline_pair": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "switched_pair": [REQUIRED_MODEL, "high"],
            "switch_back_pair": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "exact_readback_each_run": True,
            "last_effective_commits_verified": True,
            "external_session_id_unchanged": True,
            "session_load_reused": True,
            "complements": "s3-model-switch acceptance",
            "artifact_dirs": [str(r1.run_dir), str(r2.run_dir), str(r3.run_dir)],
        },
    )

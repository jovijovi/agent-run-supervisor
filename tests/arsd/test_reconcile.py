"""Slice 4 — idempotent startup reconciliation (plan §8).

Seeded native-store fixtures only. No socket listen, no real AGENT, no acpx,
no prompt/ACP. Retransmit checks drive the existing ArsdHandlers seam.
"""

from __future__ import annotations

import ast
import asyncio
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.arsd import admission, handlers, protocol, server
from agent_run_supervisor.event_store import atomic_write_json
from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.run_task import DISPATCH_STARTED_MARKER
from agent_run_supervisor.result import build_result_payload
from agent_run_supervisor.session import (
    LOCK_JSON,
    SESSION_JSON,
    STATE_OPEN,
    STATE_QUARANTINED,
    SessionQuarantinedError,
)

T0 = dt.datetime(2026, 7, 22, 12, 0, 0, tzinfo=dt.timezone.utc)
T_EXPIRED = dt.datetime(2026, 7, 22, 10, 0, 0, tzinfo=dt.timezone.utc)

RECONCILE_MODULE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agent_run_supervisor"
    / "arsd"
    / "reconcile.py"
)

NATIVE_SESSION_KWARGS = dict(
    profile_id="fake-agent-1.0",
    profile_revision=1,
    profile_hash="a" * 64,
    owner="hermes",
    namespace="hermes/doc-check",
    workspace_hash="b" * 64,
    effective_cwd="/tmp/ws",
    matched_root="/tmp",
)


def run_async(coro):
    return asyncio.run(asyncio.wait_for(coro, 30))


def principal_a() -> server.Principal:
    return server.Principal(
        principal_id="principal-a",
        owner_namespaces=frozenset({("hermes", "hermes/doc-check")}),
    )


def caller_for(principal: server.Principal) -> server.AuthenticatedCaller:
    return server.AuthenticatedCaller(
        principal=principal,
        peer_credentials=server.PeerCredentials(pid=4242, uid=1000, gid=1000),
    )


def valid_wire_request(**overrides) -> dict:
    request = {
        "owner": "hermes",
        "namespace": "hermes/doc-check",
        "profile_id": "fake-agent-1.0",
        "session_reuse": "reuse",
        "ars_session_id": "sess-reuse-1",
        "expected_binding_hash": None,
        "input_refs": [
            {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64},
        ],
        "requested_model": "kimi-for-coding/k3",
        "requested_effort": "max",
        "grant_ref": "grant:doc-check-1",
        "grant_hash": "sha256:" + "b" * 64,
        "grant_role_hash": "sha256:" + "c" * 64,
        "grant_capabilities": ["read"],
        "mcp_snapshot_hashes": [],
        "credential_refs": [],
        "limits": {},
        "evidence_policy_hash": "sha256:" + "d" * 64,
        "recovery_policy_hash": "sha256:" + "e" * 64,
    }
    request.update(overrides)
    return request


def submit_payload(**overrides) -> dict:
    payload = {
        "request": valid_wire_request(),
        "prompt_text": "run the doc check",
        "workspace_root": "/tmp/ws",
        "cwd": None,
        "retry_of_run_id": None,
    }
    payload.update(overrides)
    return payload


def submit_command(payload: dict | None = None) -> protocol.SubmitCommand:
    return protocol.parse_submit(payload or submit_payload())


def derived(principal_id: str, request_id: str) -> str:
    return admission.derive_run_id(
        admission.AdmissionKey(principal_id=principal_id, request_id=request_id)
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    )


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    out: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        out[rel] = path.read_bytes() if path.is_file() else None
    return out


def _seed_lock(session_dir: Path, *, expires_at: str, reclaimable: bool = False) -> bytes:
    payload = {
        "token": "lock-token-seed",
        "owner": "hermes",
        "acquired_at": T_EXPIRED.isoformat(),
        "expires_at": expires_at,
        "host": "host",
        "pid": 9,
        "process_start": "1",
        "boot_id": "boot",
        "holder_kind": "native_agent",
        "reclaimable": reclaimable,
    }
    data = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    (session_dir / LOCK_JSON).write_bytes(data)
    return data


def _seed_session(
    store,
    session_id: str,
    *,
    now: dt.datetime = T0,
) -> Path:
    storage.create_native_session(
        store, session_id=session_id, now=now, **NATIVE_SESSION_KWARGS
    )
    return store.base_dir / session_id


def _seed_submission(
    run_dir: Path,
    *,
    request_id: str,
    run_id: str,
    session_reuse: str = "reuse",
    ars_session_id: str | None = "sess-reuse-1",
    payload: dict | None = None,
) -> bytes:
    command = submit_command(
        payload
        or submit_payload(
            request=valid_wire_request(
                session_reuse=session_reuse,
                ars_session_id=ars_session_id,
            )
        )
    )
    digest = admission.compute_request_digest(command)
    artifact = {
        "schema_version": admission.SUBMISSION_SCHEMA_VERSION,
        "principal_id": "principal-a",
        "request_id": request_id,
        "run_id": run_id,
        "retry_of_run_id": None,
        "api_version": protocol.ARSD_API_VERSION,
        "accepted_at": "2026-07-22T00:00:00+00:00",
        "peer": {"pid": 1, "uid": 1, "gid": 1},
        "owner": "hermes",
        "namespace": "hermes/doc-check",
        "session_reuse": session_reuse,
        "ars_session_id": ars_session_id,
        "profile_id": "fake-agent-1.0",
        "request_digest": digest.value,
        "prompt_sha256": digest.prompt_sha256,
        "prompt_bytes": digest.prompt_bytes,
    }
    path = storage.write_once_json(run_dir / "submission.json", artifact)
    return path.read_bytes()


def _seed_spec(
    run_dir: Path,
    *,
    run_id: str,
    reuse: str = "reuse",
    ars_session_id: str | None = "sess-spec-1",
) -> None:
    _write_json(
        run_dir / "spec.json",
        {
            "schema_version": 1,
            "identity": {"owner": "hermes", "namespace": "hermes/doc-check"},
            "session": {
                "reuse": reuse,
                "ars_session_id": ars_session_id,
                "expected_binding_hash": None,
            },
            "run_id": run_id,
        },
    )


def _seed_marker(run_dir: Path, run_id: str) -> None:
    storage.write_once_json(
        run_dir / DISPATCH_STARTED_MARKER,
        {
            "marker": DISPATCH_STARTED_MARKER,
            "run_id": run_id,
            "ordinal": 1,
            "created_at": "2026-07-22T00:00:01+00:00",
        },
    )


def _seed_result(
    run_dir: Path,
    *,
    run_id: str,
    status: str,
    detail_code: str | None = None,
    session_id: str | None = None,
) -> bytes:
    status_enum = AgentRunStatus(status)
    payload = build_result_payload(
        run_id=run_id,
        status=status_enum,
        origin="supervisor",
        detail_code=detail_code,
        retryable=_RETRYABLE_DEFAULT[status_enum],
        exit_code=None,
        signal=None,
        stop_reason=None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=run_dir,
        raw_event_path="events.jsonl",
    )
    if session_id is not None:
        payload["session_id"] = session_id
    path = storage.write_once_json(run_dir / "result.json", payload)
    return path.read_bytes()


class SpyFactory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, *, command, run_id, prepared_handle, submitted_at):
        self.calls.append(run_id)

        class _Never:
            async def run(self):
                await asyncio.Event().wait()

        return _Never()


class HandlerHarness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.session_store = storage.native_session_store(root)
        self.event_store = storage.native_event_store(root)
        self.factory = SpyFactory()
        self.handlers = handlers.ArsdHandlers(
            session_store=self.session_store,
            event_store=self.event_store,
            run_task_factory=self.factory,
            max_concurrent_runs=4,
        )

    async def submit(self, request_id: str, payload: dict | None = None):
        return await self.handlers(
            caller_for(principal_a()),
            protocol.ParsedRequest(
                op="submit",
                request_id=request_id,
                payload=payload or submit_payload(),
            ),
        )

    async def aclose(self) -> None:
        await self.handlers.aclose()


def _import_reconcile():
    from agent_run_supervisor.arsd import reconcile as mod

    return mod


# ---------------------------------------------------------------------------
# Convergence rows
# ---------------------------------------------------------------------------


def test_pre_dispatch_reuse_session_submission_only_is_failed_reusable(
    tmp_path: Path,
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-pre-reuse-1"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    submission_bytes = _seed_submission(run_dir, request_id="pre-reuse-1", run_id=run_id)
    session_before = (sessions.base_dir / session_id / SESSION_JSON).read_bytes()

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    assert result["origin"] == "supervisor"
    assert result["retryable"] is _RETRYABLE_DEFAULT[AgentRunStatus.FAILED]
    assert (run_dir / "submission.json").read_bytes() == submission_bytes
    assert (sessions.base_dir / session_id / SESSION_JSON).read_bytes() == session_before
    assert sessions.open_session(session_id).state == STATE_OPEN


def test_pre_dispatch_ephemeral_from_submission(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-pre-eph-1"
    session_id = f"{run_id}-ephemeral"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(
        run_dir,
        request_id="pre-eph-1",
        run_id=run_id,
        session_reuse="none",
        ars_session_id=None,
    )
    session_before = (sessions.base_dir / session_id / SESSION_JSON).read_bytes()

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    assert (sessions.base_dir / session_id / SESSION_JSON).read_bytes() == session_before
    assert sessions.open_session(session_id).state == STATE_OPEN


def test_pre_dispatch_spec_only_reuse_session(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-spec-only-1"
    session_id = "sess-spec-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_spec(run_dir, run_id=run_id, reuse="reuse", ars_session_id=session_id)
    session_before = (sessions.base_dir / session_id / SESSION_JSON).read_bytes()

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    assert result["status"] == "failed"
    assert (sessions.base_dir / session_id / SESSION_JSON).read_bytes() == session_before


def test_bare_run_dir_failed_and_unexposed(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    events = storage.native_event_store(root)
    storage.native_session_store(root)
    run_id = "run-bare-1"
    run_dir = events.create_run(run_id).run_dir

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    assert "session_id" not in result or result.get("session_id") in (None, "")

    async def case():
        harness = HandlerHarness(root)
        try:
            # Bare identity has no ownership binding and must stay unexposed.
            with pytest.raises(protocol.ProtocolError) as unknown:
                await harness.handlers(
                    caller_for(principal_a()),
                    protocol.ParsedRequest(
                        op="run_status",
                        request_id="status-1",
                        payload={"run_id": run_id},
                    ),
                )
            assert unknown.value.code == protocol.UNKNOWN_RUN
        finally:
            await harness.aclose()

    run_async(case())


def test_dispatched_no_result_quarantines_and_writes_unknown(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-disp-1"
    session_id = "sess-reuse-1"
    session_dir = _seed_session(sessions, session_id)
    lock_bytes = _seed_lock(
        session_dir, expires_at=(T0 + dt.timedelta(hours=1)).isoformat()
    )
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="disp-1", run_id=run_id)
    _seed_marker(run_dir, run_id)

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "unknown"
    assert result["detail_code"] == "RECONCILED_UNKNOWN"
    assert result["origin"] == "supervisor"
    assert result["retryable"] is False
    record = sessions.open_session(session_id)
    assert record.state == STATE_QUARANTINED
    assert record.quarantined_by_run_id == run_id
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "unknown"
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes


def test_dispatched_ephemeral_session_unknown(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-disp-eph-1"
    session_id = f"{run_id}-ephemeral"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(
        run_dir,
        request_id="disp-eph-1",
        run_id=run_id,
        session_reuse="none",
        ars_session_id=None,
    )
    _seed_marker(run_dir, run_id)

    reconcile.reconcile(root)

    assert sessions.open_session(session_id).state == STATE_QUARANTINED
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["detail_code"] == "RECONCILED_UNKNOWN"


def test_missing_session_dir_is_non_fatal(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    events = storage.native_event_store(root)
    storage.native_session_store(root)
    run_id = "run-missing-sess-1"
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="missing-sess-1", run_id=run_id)
    _seed_marker(run_dir, run_id)

    with caplog.at_level(logging.WARNING):
        reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["detail_code"] == "RECONCILED_UNKNOWN"
    assert any("sess-reuse-1" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Repair / preserve / idempotency
# ---------------------------------------------------------------------------


def test_repair_unknown_result_quarantines_preserving_result_bytes(
    tmp_path: Path,
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-repair-1"
    session_id = "sess-reuse-1"
    session_dir = _seed_session(sessions, session_id)
    lock_bytes = _seed_lock(
        session_dir, expires_at=(T0 + dt.timedelta(hours=1)).isoformat()
    )
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="repair-1", run_id=run_id)
    _seed_marker(run_dir, run_id)
    result_bytes = _seed_result(
        run_dir,
        run_id=run_id,
        status="unknown",
        detail_code="OBSERVATION_LOST",
        session_id=session_id,
    )
    atomic_write_json(
        run_dir / "progress.json",
        {
            "schema_version": 1,
            "state": "running",
            "last_seq": 3,
            "event_count": 3,
            "updated_at": "2026-07-22T00:00:02+00:00",
        },
    )

    reconcile.reconcile(root)

    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert sessions.open_session(session_id).state == STATE_QUARANTINED
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "unknown"
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes


def test_existing_non_unknown_result_bytes_preserved(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-keep-1"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="keep-1", run_id=run_id)
    _seed_marker(run_dir, run_id)
    result_bytes = _seed_result(
        run_dir, run_id=run_id, status="completed", session_id=session_id
    )
    session_before = (sessions.base_dir / session_id / SESSION_JSON).read_bytes()

    reconcile.reconcile(root)

    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert (sessions.base_dir / session_id / SESSION_JSON).read_bytes() == session_before


def test_second_pass_changes_zero_bytes(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)

    # Mix of dispositions in one root.
    reuse = "sess-reuse-1"
    _seed_session(sessions, reuse)
    held = _seed_session(sessions, "sess-held")
    expired = _seed_session(sessions, "sess-expired")
    quarantined = _seed_session(sessions, "sess-already-q")
    sessions.mark_quarantined("sess-already-q", reason="prior", run_id="old", now=T0)
    held_lock = _seed_lock(
        held, expires_at=(T0 + dt.timedelta(hours=2)).isoformat(), reclaimable=False
    )
    expired_lock = _seed_lock(
        expired, expires_at=T_EXPIRED.isoformat(), reclaimable=False
    )
    q_lock = _seed_lock(
        quarantined, expires_at=(T0 + dt.timedelta(hours=1)).isoformat()
    )

    # pre-dispatch submission
    r1 = events.create_run("run-mix-pre").run_dir
    _seed_submission(r1, request_id="mix-pre", run_id="run-mix-pre")
    # dispatched no result
    r2 = events.create_run("run-mix-disp").run_dir
    _seed_submission(r2, request_id="mix-disp", run_id="run-mix-disp")
    _seed_marker(r2, "run-mix-disp")
    # bare
    events.create_run("run-mix-bare")
    # preserve completed
    r4 = events.create_run("run-mix-done").run_dir
    _seed_submission(r4, request_id="mix-done", run_id="run-mix-done")
    _seed_marker(r4, "run-mix-done")
    _seed_result(r4, run_id="run-mix-done", status="completed")

    reconcile.reconcile(root)
    after_first = {
        "runs": _tree_snapshot(events.base_dir),
        "sessions": _tree_snapshot(sessions.base_dir),
    }
    reconcile.reconcile(root)
    after_second = {
        "runs": _tree_snapshot(events.base_dir),
        "sessions": _tree_snapshot(sessions.base_dir),
    }
    assert after_second == after_first
    assert (held / LOCK_JSON).read_bytes() == held_lock
    assert (expired / LOCK_JSON).read_bytes() == expired_lock
    assert (quarantined / LOCK_JSON).read_bytes() == q_lock


# ---------------------------------------------------------------------------
# Fault injection convergence
# ---------------------------------------------------------------------------


def test_fault_after_quarantine_before_progress_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-fault-qp"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="fault-qp", run_id=run_id)
    _seed_marker(run_dir, run_id)

    real_ensure = reconcile._ensure_terminal_progress

    def boom(*args, **kwargs):
        raise RuntimeError("injected crash after quarantine before progress")

    monkeypatch.setattr(reconcile, "_ensure_terminal_progress", boom)
    with pytest.raises(RuntimeError, match="injected crash"):
        reconcile.reconcile(root)
    assert sessions.open_session(session_id).state == STATE_QUARANTINED
    assert not (run_dir / "result.json").exists()
    assert not (run_dir / "progress.json").exists()

    monkeypatch.setattr(reconcile, "_ensure_terminal_progress", real_ensure)
    reconcile.reconcile(root)
    assert json.loads((run_dir / "result.json").read_text())["detail_code"] == (
        "RECONCILED_UNKNOWN"
    )
    assert json.loads((run_dir / "progress.json").read_text())["state"] == "unknown"


def test_fault_after_progress_before_result_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-fault-pr"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="fault-pr", run_id=run_id)
    _seed_marker(run_dir, run_id)

    real_write = storage.write_once_json
    calls = {"n": 0}

    def boom(path, payload):
        path = Path(path)
        if path.name == "result.json":
            calls["n"] += 1
            raise RuntimeError("injected crash after progress before result")
        return real_write(path, payload)

    monkeypatch.setattr(storage, "write_once_json", boom)
    monkeypatch.setattr(reconcile, "storage", storage)
    with pytest.raises(RuntimeError, match="injected crash"):
        reconcile.reconcile(root)
    assert sessions.open_session(session_id).state == STATE_QUARANTINED
    assert (run_dir / "progress.json").is_file()
    assert json.loads((run_dir / "progress.json").read_text())["state"] == "unknown"
    assert not (run_dir / "result.json").exists()

    monkeypatch.setattr(storage, "write_once_json", real_write)
    monkeypatch.setattr(reconcile, "storage", storage)
    reconcile.reconcile(root)
    assert json.loads((run_dir / "result.json").read_text())["detail_code"] == (
        "RECONCILED_UNKNOWN"
    )


def test_quarantine_failure_blocks_dispatched_progress_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic quarantine failure must not leave an irreversible unknown terminal."""
    from agent_run_supervisor.session import SessionStore

    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-qfail-disp"
    session_id = "sess-reuse-1"
    session_dir = _seed_session(sessions, session_id)
    lock_bytes = _seed_lock(
        session_dir, expires_at=(T0 + dt.timedelta(hours=1)).isoformat()
    )
    run_dir = events.create_run(run_id).run_dir
    submission_bytes = _seed_submission(
        run_dir, request_id="qfail-disp", run_id=run_id
    )
    _seed_marker(run_dir, run_id)
    marker_bytes = (run_dir / DISPATCH_STARTED_MARKER).read_bytes()
    session_before = (session_dir / SESSION_JSON).read_bytes()

    real_mark = SessionStore.mark_quarantined

    def boom(self, session_id, *, reason, run_id, now=None):
        raise OSError("injected quarantine I/O failure")

    monkeypatch.setattr(SessionStore, "mark_quarantined", boom)
    with pytest.raises(OSError, match="injected quarantine I/O failure"):
        reconcile.reconcile(root)

    assert not (run_dir / "progress.json").exists()
    assert not (run_dir / "result.json").exists()
    assert (run_dir / "submission.json").read_bytes() == submission_bytes
    assert (run_dir / DISPATCH_STARTED_MARKER).read_bytes() == marker_bytes
    assert (session_dir / SESSION_JSON).read_bytes() == session_before
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes
    assert sessions.open_session(session_id).state == STATE_OPEN

    monkeypatch.setattr(SessionStore, "mark_quarantined", real_mark)
    reconcile.reconcile(root)
    assert sessions.open_session(session_id).state == STATE_QUARANTINED
    assert json.loads((run_dir / "progress.json").read_text())["state"] == "unknown"
    result = json.loads((run_dir / "result.json").read_text())
    assert result["detail_code"] == "RECONCILED_UNKNOWN"
    assert result["status"] == "unknown"
    assert (run_dir / "submission.json").read_bytes() == submission_bytes
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes


def test_quarantine_failure_on_unknown_repair_leaves_result_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Result-present unknown repair must not advance progress if quarantine fails."""
    from agent_run_supervisor.session import SessionStore

    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-qfail-repair"
    session_id = "sess-reuse-1"
    session_dir = _seed_session(sessions, session_id)
    lock_bytes = _seed_lock(
        session_dir, expires_at=(T0 + dt.timedelta(hours=1)).isoformat()
    )
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="qfail-repair", run_id=run_id)
    _seed_marker(run_dir, run_id)
    result_bytes = _seed_result(
        run_dir,
        run_id=run_id,
        status="unknown",
        detail_code="OBSERVATION_LOST",
        session_id=session_id,
    )
    atomic_write_json(
        run_dir / "progress.json",
        {
            "schema_version": 1,
            "state": "running",
            "last_seq": 3,
            "event_count": 3,
            "updated_at": "2026-07-22T00:00:02+00:00",
        },
    )
    progress_before = (run_dir / "progress.json").read_bytes()
    session_before = (session_dir / SESSION_JSON).read_bytes()

    real_mark = SessionStore.mark_quarantined

    def boom(self, session_id, *, reason, run_id, now=None):
        raise RuntimeError("injected quarantine validation failure")

    monkeypatch.setattr(SessionStore, "mark_quarantined", boom)
    with pytest.raises(RuntimeError, match="injected quarantine validation failure"):
        reconcile.reconcile(root)

    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert (run_dir / "progress.json").read_bytes() == progress_before
    assert (session_dir / SESSION_JSON).read_bytes() == session_before
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes
    assert sessions.open_session(session_id).state == STATE_OPEN

    monkeypatch.setattr(SessionStore, "mark_quarantined", real_mark)
    reconcile.reconcile(root)
    assert sessions.open_session(session_id).state == STATE_QUARANTINED
    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert json.loads((run_dir / "progress.json").read_text())["state"] == "unknown"
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes


# ---------------------------------------------------------------------------
# Retransmit recovery via handler seam
# ---------------------------------------------------------------------------


def test_retransmit_after_reconciled_submission_returns_identity(
    tmp_path: Path,
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    events = storage.native_event_store(root)
    storage.native_session_store(root)
    request_id = "retx-sub-1"
    run_id = derived("principal-a", request_id)
    payload = submit_payload()
    run_dir = events.create_run(run_id).run_dir
    submission_bytes = _seed_submission(
        run_dir, request_id=request_id, run_id=run_id, payload=payload
    )

    reconcile.reconcile(root)
    result_bytes = (run_dir / "result.json").read_bytes()
    assert (
        json.loads(result_bytes)["detail_code"] == "RECONCILED_PRE_DISPATCH"
    )

    async def case():
        harness = HandlerHarness(root)
        try:
            reply = await harness.submit(request_id, payload)
            assert reply == {
                "run_id": run_id,
                "accepted_at": "2026-07-22T00:00:00+00:00",
            }
            assert harness.factory.calls == []
            assert (run_dir / "submission.json").read_bytes() == submission_bytes
            assert (run_dir / "result.json").read_bytes() == result_bytes
        finally:
            await harness.aclose()

    run_async(case())


def test_retransmit_after_reconciled_bare_stays_indeterminate(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    events = storage.native_event_store(root)
    storage.native_session_store(root)
    request_id = "retx-bare-1"
    run_id = derived("principal-a", request_id)
    events.create_run(run_id)
    reconcile.reconcile(root)

    async def case():
        harness = HandlerHarness(root)
        try:
            with pytest.raises(protocol.ProtocolError) as exc:
                await harness.submit(request_id)
            assert exc.value.code == protocol.SUBMISSION_INDETERMINATE
            assert harness.factory.calls == []
        finally:
            await harness.aclose()

    run_async(case())


def test_retransmit_preserves_existing_dispatched_terminal(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    events = storage.native_event_store(root)
    storage.native_session_store(root)
    request_id = "retx-done-1"
    run_id = derived("principal-a", request_id)
    payload = submit_payload()
    run_dir = events.create_run(run_id).run_dir
    submission_bytes = _seed_submission(
        run_dir, request_id=request_id, run_id=run_id, payload=payload
    )
    _seed_marker(run_dir, run_id)
    result_bytes = _seed_result(run_dir, run_id=run_id, status="completed")

    reconcile.reconcile(root)
    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert (run_dir / "submission.json").read_bytes() == submission_bytes

    async def case():
        harness = HandlerHarness(root)
        try:
            reply = await harness.submit(request_id, payload)
            assert reply["run_id"] == run_id
            assert harness.factory.calls == []
            assert (run_dir / "result.json").read_bytes() == result_bytes
        finally:
            await harness.aclose()

    run_async(case())


# ---------------------------------------------------------------------------
# Lock / quarantine / legacy isolation
# ---------------------------------------------------------------------------


def test_seeded_locks_byte_identical_after_reconcile(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    held = _seed_session(sessions, "sess-held")
    expired = _seed_session(sessions, "sess-expired")
    already_q = _seed_session(sessions, "sess-q")
    sessions.mark_quarantined("sess-q", reason="prior", run_id="old", now=T0)
    locks = {
        held: _seed_lock(held, expires_at=(T0 + dt.timedelta(hours=1)).isoformat()),
        expired: _seed_lock(expired, expires_at=T_EXPIRED.isoformat()),
        already_q: _seed_lock(
            already_q, expires_at=(T0 + dt.timedelta(minutes=30)).isoformat()
        ),
    }
    run_dir = events.create_run("run-lock-check").run_dir
    _seed_submission(run_dir, request_id="lock-check", run_id="run-lock-check")

    reconcile.reconcile(root)

    for session_dir, expected in locks.items():
        assert (session_dir / LOCK_JSON).read_bytes() == expected


def test_quarantined_session_refuses_new_lease(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run("run-q-lease").run_dir
    _seed_submission(run_dir, request_id="q-lease", run_id="run-q-lease")
    _seed_marker(run_dir, "run-q-lease")

    reconcile.reconcile(root)
    assert sessions.open_session(session_id).state == STATE_QUARANTINED
    with pytest.raises(SessionQuarantinedError):
        sessions.acquire_lock(
            session_id,
            "hermes",
            required_state=STATE_OPEN,
            reclaimable=False,
            now=T0,
        )


def test_poisoned_legacy_same_ids_untouched(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-poison-1"
    session_id = "sess-poison-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(
        run_dir,
        request_id="poison-1",
        run_id=run_id,
        ars_session_id=session_id,
    )
    _seed_marker(run_dir, run_id)

    poison = b"{ this is deliberately not parseable json"
    legacy_sessions = root / "sessions" / session_id
    legacy_runs = root / "runs" / run_id
    legacy_sessions.mkdir(parents=True)
    legacy_runs.mkdir(parents=True)
    (legacy_sessions / "session.json").write_bytes(poison)
    (legacy_runs / "result.json").write_bytes(poison)
    legacy_snap = {
        "sessions": _tree_snapshot(root / "sessions"),
        "runs": _tree_snapshot(root / "runs"),
    }

    reconcile.reconcile(root)

    assert {
        "sessions": _tree_snapshot(root / "sessions"),
        "runs": _tree_snapshot(root / "runs"),
    } == legacy_snap
    assert sessions.open_session(session_id).state == STATE_QUARANTINED


# ---------------------------------------------------------------------------
# Structural assertions
# ---------------------------------------------------------------------------


def _call_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _attr_and_name_refs(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_reconcile_module_has_no_lock_mutation_or_prompt_acp_sites() -> None:
    assert RECONCILE_MODULE.is_file(), "reconcile.py must exist for Slice 4"
    tree = ast.parse(RECONCILE_MODULE.read_text(encoding="utf-8"))
    calls = _call_names(tree)
    refs = _attr_and_name_refs(tree)

    forbidden_lock = {
        "acquire_lock",
        "release_lock",
        "update_lock_holder",
        "unlink",
    }
    assert not (calls & forbidden_lock), calls & forbidden_lock

    forbidden_prompt_acp = {
        "prompt_once",
        "prompt",
        "NativeAcpDriver",
        "RunTask",
        "session_new",
        "new_session",
        "load_session",
        "set_config_exact",
    }
    assert not (refs & forbidden_prompt_acp), refs & forbidden_prompt_acp

    # SessionStore mutation beyond mark_quarantined is forbidden.
    # open_session / detect_stale_locks are read-only and permitted.
    forbidden_session_mut = {
        "mark_closed",
        "create_session",
        "create_native_session",
        "bind_agent_session",
        "commit_last_effective",
    }
    assert not (calls & forbidden_session_mut), calls & forbidden_session_mut
    assert "mark_quarantined" in calls or "mark_quarantined" in refs

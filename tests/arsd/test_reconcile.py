"""Slice 4 — idempotent startup reconciliation (plan §8).

Seeded native-store fixtures only. No socket listen, no real AGENT, no acpx,
no prompt/ACP. Retransmit checks drive the existing ArsdHandlers seam.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

import reconcile_fixtures as rf

from agent_run_supervisor.arsd import admission, handlers, protocol, server
from agent_run_supervisor.event_store import atomic_write_json
from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.run_task import (
    CONFIG_PROVEN_MARKER,
    CONFIG_ROLLBACK_PROVEN_MARKER,
    CONFIG_SWITCH_STARTED_MARKER,
    DISPATCH_STARTED_MARKER,
    PROMPT_ACCEPTED_MARKER,
)
from agent_run_supervisor.result import build_result_payload
from agent_run_supervisor.session import (
    LOCK_JSON,
    SESSION_JSON,
    SessionQuarantinedError,
)
from agent_run_supervisor.session import QUARANTINE_DISPATCH_OBSERVATION_LOST, derive_session_id_for_run

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
    profile_id="fake-agent-v1",
    profile_revision=1,
    agent_id="fake-agent",
    profile_hash="a" * 64,
    owner="hermes",
    namespace="hermes/doc-check",
    workspace_hash="b" * 64,
    effective_cwd="/tmp/ws",
    matched_root="/tmp",
    agent_session_id="external-fake-agent-1",
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
        "agent_id": "fake-agent",
        "session_id": "sess-reuse-1",
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
    if request.get("session_id") is None:
        # A create omits the field; an explicit null is refused on the wire.
        request.pop("session_id", None)
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
    session_id: str | None = "sess-reuse-1",
    payload: dict | None = None,
) -> bytes:
    command = submit_command(
        payload
        or submit_payload(request=valid_wire_request(session_id=session_id))
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
        "session_id": session_id,
        "agent_id": "fake-agent",
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
    session_id: str | None = "sess-spec-1",
) -> None:
    # The exact shape RunTask seals and writes: a Spec that cannot answer the
    # immutable request, grant, owner, namespace, agent, profile, Session
    # binding, and referenced launch hash is not authority for anything.
    _write_json(
        run_dir / "spec.json",
        rf.spec_payload(run_id=run_id, session_id=session_id),
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
    if status_enum is AgentRunStatus.COMPLETED:
        origin, stop_reason = "acp", "end_turn"
        resolved_detail = detail_code
    elif status_enum is AgentRunStatus.CANCELLED:
        origin, stop_reason = "acp", "cancelled"
        resolved_detail = detail_code
    else:
        origin, stop_reason = "supervisor", None
        resolved_detail = detail_code
    payload = build_result_payload(
        run_id=run_id,
        status=status_enum,
        origin=origin,
        detail_code=resolved_detail,
        retryable=_RETRYABLE_DEFAULT[status_enum],
        exit_code=None,
        signal=None,
        stop_reason=stop_reason,
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
    assert sessions.open_session(session_id).quarantine is None


def test_pre_dispatch_ephemeral_from_submission(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-pre-eph-1"
    session_id = derive_session_id_for_run(run_id)
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(
        run_dir,
        request_id="pre-eph-1",
        run_id=run_id,
        session_id=None,
    )
    session_before = (sessions.base_dir / session_id / SESSION_JSON).read_bytes()

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    assert (sessions.base_dir / session_id / SESSION_JSON).read_bytes() == session_before
    assert sessions.open_session(session_id).quarantine is None


def test_pre_dispatch_spec_only_reuse_session(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-spec-only-1"
    session_id = "sess-spec-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_spec(run_dir, run_id=run_id, session_id=session_id)
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
    assert record.quarantine is not None
    assert record.quarantine["source_run_id"] == run_id
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "unknown"
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes


def test_dispatched_ephemeral_session_unknown(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-disp-eph-1"
    session_id = derive_session_id_for_run(run_id)
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(
        run_dir,
        request_id="disp-eph-1",
        run_id=run_id,
        session_id=None,
    )
    _seed_marker(run_dir, run_id)

    reconcile.reconcile(root)

    assert sessions.open_session(session_id).quarantine is not None
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["detail_code"] == "RECONCILED_UNKNOWN"


def test_missing_session_dir_refuses_to_listen(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Row 6: a possibly dispatched Run with no actionable Session attribution.

    No terminal may be fabricated without a durable, trustworthy Session
    attribution, and reconciliation never creates the record it is missing —
    so the daemon refuses to listen instead of inventing either.
    """
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    events = storage.native_event_store(root)
    sessions = storage.native_session_store(root)
    run_id = "run-missing-sess-1"
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="missing-sess-1", run_id=run_id)
    _seed_marker(run_dir, run_id)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(reconcile.ReconciliationError) as err:
            reconcile.reconcile(root)

    assert reconcile.REFUSE_UNATTRIBUTABLE_DISPATCH in str(err.value)
    assert not (run_dir / "result.json").exists()
    assert not (run_dir / "progress.json").exists()
    assert sessions.list_records() == []
    # The refusal names a stable rule and no session id.
    assert all("sess-reuse-1" not in str(err.value) for _ in (0,))


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
    assert sessions.open_session(session_id).quarantine is not None
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
    sessions.mark_quarantined("sess-already-q", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="old", now=T0)
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
    assert sessions.open_session(session_id).quarantine is not None
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
    assert sessions.open_session(session_id).quarantine is not None
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

    def boom(self, session_id, *, reason_code, run_id, now=None):
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
    assert sessions.open_session(session_id).quarantine is None

    monkeypatch.setattr(SessionStore, "mark_quarantined", real_mark)
    reconcile.reconcile(root)
    assert sessions.open_session(session_id).quarantine is not None
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

    def boom(self, session_id, *, reason_code, run_id, now=None):
        raise RuntimeError("injected quarantine validation failure")

    monkeypatch.setattr(SessionStore, "mark_quarantined", boom)
    with pytest.raises(RuntimeError, match="injected quarantine validation failure"):
        reconcile.reconcile(root)

    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert (run_dir / "progress.json").read_bytes() == progress_before
    assert (session_dir / SESSION_JSON).read_bytes() == session_before
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes
    assert sessions.open_session(session_id).quarantine is None

    monkeypatch.setattr(SessionStore, "mark_quarantined", real_mark)
    reconcile.reconcile(root)
    assert sessions.open_session(session_id).quarantine is not None
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
            # The duplicate returns the same identity facts, including the
            # Session the original attempt named.
            assert reply == {
                "run_id": run_id,
                "session_id": "sess-reuse-1",
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
    sessions.mark_quarantined("sess-q", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="old", now=T0)
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
    assert sessions.open_session(session_id).quarantine is not None
    with pytest.raises(SessionQuarantinedError):
        sessions.acquire_lock(
            session_id,
            "hermes",
            refuse_quarantined=True,
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
        session_id=session_id,
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
    assert sessions.open_session(session_id).quarantine is not None


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
        "create_session",
        "create_native_session",
        "commit_last_effective",
    }
    assert not (calls & forbidden_session_mut), calls & forbidden_session_mut
    assert "mark_quarantined" in calls or "mark_quarantined" in refs


# ---------------------------------------------------------------------------
# Corrupt / untrusted existing terminal evidence (Codex R1 / B3)
# ---------------------------------------------------------------------------


def _raw_result_bytes(run_dir: Path, raw: bytes) -> Path:
    path = run_dir / "result.json"
    # Simulate crash between exclusive create and durable write (empty/truncated).
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        if raw:
            os.write(fd, raw)
    finally:
        os.close(fd)
    return path


def test_crash_between_exclusive_create_and_write_not_treated_terminal(
    tmp_path: Path,
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-empty-result"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="empty-result", run_id=run_id)
    _seed_marker(run_dir, run_id)
    _raw_result_bytes(run_dir, b"")

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    message = str(err.value).lower()
    assert (
        "untrusted" in message
        or "terminal" in message
        or "corrupt" in message
        or "invalid" in message
    )
    # Quarantine first for dispatched runs; never invent progress / overwrite result.
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()
    assert (run_dir / "result.json").read_bytes() == b""


def test_invalid_json_result_with_marker_quarantines_then_fails_startup(
    tmp_path: Path,
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-bad-json"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="bad-json", run_id=run_id)
    _seed_marker(run_dir, run_id)
    bad = b'{"status": "completed", "run_id":'
    (run_dir / "result.json").write_bytes(bad)

    with pytest.raises(reconcile.ReconciliationError):
        reconcile.reconcile(root)
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()
    assert (run_dir / "result.json").read_bytes() == bad


def test_symlink_result_json_never_treated_terminal(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-symlink-result"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="symlink-result", run_id=run_id)
    _seed_marker(run_dir, run_id)
    target = run_dir / "elsewhere.json"
    payload = {
        "run_id": run_id,
        "status": "completed",
        "retryable": False,
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    (run_dir / "result.json").symlink_to(target)

    with pytest.raises(reconcile.ReconciliationError):
        reconcile.reconcile(root)
    assert sessions.open_session(session_id).quarantine is not None
    assert (run_dir / "result.json").is_symlink()
    assert not (run_dir / "progress.json").exists()


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "an", "object"],
        {"status": "completed", "retryable": False},  # missing run_id
        {"run_id": "wrong-id", "status": "completed", "retryable": False},
        {"run_id": "run-schema", "status": "not-a-status", "retryable": False},
        {"run_id": "run-schema", "status": "unknown", "retryable": True},
        {"run_id": "run-schema", "status": "completed", "retryable": 0},
        {"run_id": "run-schema", "status": "failed", "retryable": True},
    ],
)
def test_invalid_result_schema_never_treated_terminal(
    tmp_path: Path, payload: object
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-schema"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="schema", run_id=run_id)
    _seed_marker(run_dir, run_id)
    raw = json.dumps(payload).encode("utf-8")
    (run_dir / "result.json").write_bytes(raw)

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    assert isinstance(err.value, reconcile.ReconciliationError)
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()
    assert (run_dir / "result.json").read_bytes() == raw


def test_pre_dispatch_corrupt_result_fails_without_inventing_session_quarantined(
    tmp_path: Path,
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-pre-corrupt"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="pre-corrupt", run_id=run_id)
    # No dispatch marker — pre-dispatch corrupt evidence.
    (run_dir / "result.json").write_bytes(b"{not-json")
    session_before = (sessions.base_dir / session_id / SESSION_JSON).read_bytes()

    with pytest.raises(reconcile.ReconciliationError):
        reconcile.reconcile(root)
    assert (sessions.base_dir / session_id / SESSION_JSON).read_bytes() == session_before
    assert sessions.open_session(session_id).quarantine is None
    assert not (run_dir / "progress.json").exists()


def test_valid_existing_terminal_remains_write_once_idempotent(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-valid-keep"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="valid-keep", run_id=run_id)
    _seed_marker(run_dir, run_id)
    result_bytes = _seed_result(
        run_dir, run_id=run_id, status="completed", session_id=session_id
    )
    session_before = (sessions.base_dir / session_id / SESSION_JSON).read_bytes()

    reconcile.reconcile(root)
    reconcile.reconcile(root)
    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert (sessions.base_dir / session_id / SESSION_JSON).read_bytes() == session_before


def test_symlink_result_rejected_without_reading_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-symlink-noread"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="symlink-noread", run_id=run_id)
    _seed_marker(run_dir, run_id)

    secret = "sk-live-" + "RESULTTARGET"
    target = run_dir / "hostile-target.json"
    target.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "retryable": False,
                "secret": secret,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (run_dir / "result.json").symlink_to(target)

    path_reads: list[str] = []
    real_read_text = Path.read_text

    def tracking_read_text(self, *args, **kwargs):
        path_reads.append(str(self))
        if self.resolve() == target.resolve() or self.name == "hostile-target.json":
            raise AssertionError("must not path-read symlink target")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking_read_text)

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    assert secret not in str(err.value)
    assert sessions.open_session(session_id).quarantine is not None
    assert not any("hostile-target" in p for p in path_reads)
    assert not (run_dir / "progress.json").exists()


def test_result_path_swap_to_symlink_cannot_become_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deterministic TOCTOU: path becomes symlink at the open/read seam."""
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-swap-result"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="swap-result", run_id=run_id)
    _seed_marker(run_dir, run_id)

    result_path = run_dir / "result.json"
    target = run_dir / "swapped-target.json"
    valid = {
        "run_id": run_id,
        "status": "completed",
        "retryable": False,
    }
    target.write_text(json.dumps(valid, sort_keys=True), encoding="utf-8")
    # Start as a regular file whose bytes would be trusted if followed after swap.
    result_path.write_bytes(target.read_bytes())

    real_open = os.open
    real_read_text = Path.read_text

    def swap_to_symlink() -> None:
        if result_path.exists() and not result_path.is_symlink():
            result_path.unlink()
            result_path.symlink_to(target)

    def open_hook(path, flags, mode=0o777, *args, dir_fd=None, **kwargs):
        if dir_fd is None:
            try:
                resolved = Path(os.fspath(path)).resolve()
            except OSError:
                resolved = None
            if (
                resolved == result_path.resolve()
                and (flags & os.O_CREAT) == 0
            ):
                swap_to_symlink()
            return real_open(path, flags, mode, *args, **kwargs)
        return real_open(path, flags, mode, *args, dir_fd=dir_fd, **kwargs)

    def read_text_hook(self, *args, **kwargs):
        if Path(self).resolve() == result_path.resolve():
            swap_to_symlink()
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_hook)
    monkeypatch.setattr(Path, "read_text", read_text_hook)

    with pytest.raises(reconcile.ReconciliationError):
        reconcile.reconcile(root)
    assert sessions.open_session(session_id).quarantine is not None
    assert result_path.is_symlink()
    assert not (run_dir / "progress.json").exists()


def test_oversized_terminal_result_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-oversize"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="oversize", run_id=run_id)
    _seed_marker(run_dir, run_id)

    # Just over 1 MiB of JSON-ish bytes; must not be treated as trusted terminal.
    oversized = b'{"run_id":"' + run_id.encode() + b'","status":"completed","retryable":false,"pad":"'
    oversized += b"x" * (1 * 1024 * 1024)
    oversized += b'"}'
    assert len(oversized) > 1 * 1024 * 1024
    (run_dir / "result.json").write_bytes(oversized)

    # Prove we do not rely on unbounded Path.read_text success.
    real_read_text = Path.read_text
    path_reads: list[int] = []

    def tracking_read_text(self, *args, **kwargs):
        data = real_read_text(self, *args, **kwargs)
        if Path(self).name == "result.json":
            path_reads.append(len(data))
        return data

    monkeypatch.setattr(Path, "read_text", tracking_read_text)

    with pytest.raises(reconcile.ReconciliationError):
        reconcile.reconcile(root)
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()
    # After GREEN, validator must not path-read the oversized artifact.
    assert path_reads == []


def test_terminal_result_read_oserror_is_untrusted_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-read-fail"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="read-fail", run_id=run_id)
    _seed_marker(run_dir, run_id)
    result_bytes = _seed_result(
        run_dir, run_id=run_id, status="completed", session_id=session_id
    )

    real_open = os.open
    real_close = os.close
    real_read = os.read
    opened: list[int] = []
    closed: list[int] = []
    result_fds: list[int] = []
    # Descriptor numbers are reused after close, and every classified artifact
    # now travels through the same bounded reader — so the injection tracks the
    # *live* result descriptor, never a stale number that a later submission or
    # spec read would inherit.
    live_result_fds: set[int] = set()

    def tracking_open(path, flags, mode=0o777, *args, dir_fd=None, **kwargs):
        if dir_fd is None:
            fd = real_open(path, flags, mode, *args, **kwargs)
        else:
            fd = real_open(path, flags, mode, *args, dir_fd=dir_fd, **kwargs)
        opened.append(fd)
        try:
            if Path(os.fspath(path)).name == "result.json":
                result_fds.append(fd)
                live_result_fds.add(fd)
        except TypeError:
            pass
        return fd

    def boom_read(fd: int, n: int) -> bytes:
        if fd in live_result_fds:
            raise OSError(5, "injected result read failure")
        return real_read(fd, n)

    def tracking_close(fd: int) -> None:
        live_result_fds.discard(fd)
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(reconcile.os, "open", tracking_open)
    monkeypatch.setattr(reconcile.os, "read", boom_read)
    monkeypatch.setattr(reconcile.os, "close", tracking_close)

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    assert type(err.value) is reconcile.ReconciliationError
    assert "injected" not in str(err.value)
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()
    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert result_fds
    assert set(result_fds) <= set(closed)


def test_terminal_result_close_oserror_is_untrusted_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-close-fail"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="close-fail", run_id=run_id)
    _seed_marker(run_dir, run_id)
    result_bytes = _seed_result(
        run_dir, run_id=run_id, status="completed", session_id=session_id
    )

    real_open = os.open
    real_close = os.close
    result_fds: set[int] = set()
    closed_ok: set[int] = set()

    def tracking_open(path, flags, mode=0o777, *args, dir_fd=None, **kwargs):
        if dir_fd is None:
            fd = real_open(path, flags, mode, *args, **kwargs)
        else:
            fd = real_open(path, flags, mode, *args, dir_fd=dir_fd, **kwargs)
        try:
            if Path(os.fspath(path)).name == "result.json":
                result_fds.add(fd)
        except TypeError:
            pass
        return fd

    def boom_close(fd: int) -> None:
        if fd in result_fds and fd not in closed_ok:
            # Close the descriptor for real so it cannot leak, then surface OSError.
            real_close(fd)
            closed_ok.add(fd)
            raise OSError(5, "injected result close failure")
        return real_close(fd)

    monkeypatch.setattr(reconcile.os, "open", tracking_open)
    monkeypatch.setattr(reconcile.os, "close", boom_close)

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    assert type(err.value) is reconcile.ReconciliationError
    assert "injected" not in str(err.value)
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()
    assert (run_dir / "result.json").read_bytes() == result_bytes
    assert result_fds <= closed_ok


# ---------------------------------------------------------------------------
# R4 / B1 — trustworthy Native terminal result (complete shape)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["completed", "failed", "cancelled", "timed_out", "unknown"],
)
def test_r4_b1_complete_native_terminal_statuses_are_trusted(
    tmp_path: Path, status: str
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = f"run-native-{status}"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id=f"native-{status}", run_id=run_id)
    if status == "unknown":
        _seed_marker(run_dir, run_id)
    result_bytes = _seed_result(
        run_dir, run_id=run_id, status=status, session_id=session_id
    )
    session_before = (sessions.base_dir / session_id / SESSION_JSON).read_bytes()

    reconcile.reconcile(root)
    assert (run_dir / "result.json").read_bytes() == result_bytes
    if status == "unknown":
        assert sessions.open_session(session_id).quarantine is not None
        assert (run_dir / "progress.json").is_file()
    else:
        assert (sessions.base_dir / session_id / SESSION_JSON).read_bytes() == session_before


def test_r4_b1_minimal_runner_error_is_untrusted_and_refuses_startup(
    tmp_path: Path,
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-runner-error"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="runner-error", run_id=run_id)
    _seed_marker(run_dir, run_id)
    # Minimal shape that matched the old status/retryable-only gate.
    raw = json.dumps(
        {
            "run_id": run_id,
            "status": "runner_error",
            "retryable": True,
        }
    ).encode("utf-8")
    (run_dir / "result.json").write_bytes(raw)

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    message = str(err.value)
    assert "untrusted" in message.lower() or "terminal" in message.lower()
    assert "runner_error" not in message
    assert "True" not in message
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()
    assert (run_dir / "result.json").read_bytes() == raw


def _complete_terminal(run_dir: Path, run_id: str, status: AgentRunStatus) -> dict:
    """Build a structurally complete payload; may still be semantically INVALID."""
    return build_result_payload(
        run_id=run_id,
        status=status,
        origin="supervisor",
        detail_code=None if status is AgentRunStatus.COMPLETED else "PROBE",
        retryable=_RETRYABLE_DEFAULT[status],
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


def _legal_trusted_terminal(
    run_dir: Path, run_id: str, status: AgentRunStatus
) -> dict:
    """Legal trusted Native terminal matching real emitter grammar."""
    if status is AgentRunStatus.COMPLETED:
        origin, stop_reason, detail_code = "acp", "end_turn", None
    elif status is AgentRunStatus.CANCELLED:
        origin, stop_reason, detail_code = "acp", "cancelled", None
    elif status is AgentRunStatus.UNKNOWN:
        origin, stop_reason, detail_code = (
            "supervisor",
            None,
            "RECONCILED_UNKNOWN",
        )
    elif status is AgentRunStatus.TIMED_OUT:
        origin, stop_reason, detail_code = "supervisor", None, None
    else:
        origin, stop_reason, detail_code = (
            "supervisor",
            None,
            "RECONCILED_PRE_DISPATCH",
        )
    return build_result_payload(
        run_id=run_id,
        status=status,
        origin=origin,
        detail_code=detail_code,
        retryable=_RETRYABLE_DEFAULT[status],
        exit_code=None,
        signal=None,
        stop_reason=stop_reason,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=run_dir,
        raw_event_path="events.jsonl",
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        "drop_origin",
        "origin_cli",
        "retryable_int",
        "truncated_int",
        "completed_with_error_code",
        "failed_without_error_code",
        "drop_stderr_path",
    ],
)
def test_r4_b1_incomplete_or_mistyped_terminal_is_untrusted(
    tmp_path: Path, corrupt: str
) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-mistyped"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="mistyped", run_id=run_id)
    _seed_marker(run_dir, run_id)
    status = (
        AgentRunStatus.FAILED
        if corrupt == "failed_without_error_code"
        else AgentRunStatus.COMPLETED
    )
    payload = _complete_terminal(run_dir, run_id, status)
    if corrupt == "drop_origin":
        del payload["origin"]
    elif corrupt == "origin_cli":
        payload["origin"] = "cli"
    elif corrupt == "retryable_int":
        payload["retryable"] = 0
    elif corrupt == "truncated_int":
        payload["truncated"] = 1
    elif corrupt == "completed_with_error_code":
        payload["error_code"] = "FAILED"
    elif corrupt == "failed_without_error_code":
        payload["error_code"] = None
    elif corrupt == "drop_stderr_path":
        del payload["stderr_path"]
    raw = json.dumps(payload).encode("utf-8")
    (run_dir / "result.json").write_bytes(raw)

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    assert "cli" not in str(err.value)
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()


def test_r13_b2_semantically_invalid_terminal_is_invalid_and_quarantines(
    tmp_path: Path,
) -> None:
    """Forged completed+supervisor+no-stop is INVALID; reconcile fences uncertainty."""
    from agent_run_supervisor.native_acp.storage import NativeTerminalKind

    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-semantic-invalid"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="semantic-invalid", run_id=run_id)
    _seed_marker(run_dir, run_id)
    # Structurally complete but semantically forged overclaim.
    payload = _complete_terminal(run_dir, run_id, AgentRunStatus.COMPLETED)
    assert payload["origin"] == "supervisor"
    assert payload["stop_reason"] is None
    raw = json.dumps(payload).encode("utf-8")
    (run_dir / "result.json").write_bytes(raw)

    terminal = storage.read_native_terminal_result(
        run_dir / "result.json", run_id=run_id
    )
    assert terminal.kind is NativeTerminalKind.INVALID

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    message = str(err.value).lower()
    assert "untrusted" in message or "terminal" in message
    assert "completed" not in message
    assert sessions.open_session(session_id).quarantine is not None
    assert not (run_dir / "progress.json").exists()
    # First-fact preserved; no rewrite/reuse of the forged terminal.
    assert (run_dir / "result.json").read_bytes() == raw


def test_r13_b2_legal_trusted_terminals_remain_trusted_through_reconcile(
    tmp_path: Path,
) -> None:
    """Valid emitter-shaped terminals stay TRUSTED and are not quarantined away."""
    from agent_run_supervisor.native_acp.storage import NativeTerminalKind

    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-semantic-legal"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="semantic-legal", run_id=run_id)
    # No dispatch marker: completed ACP terminal must not force quarantine.
    payload = _legal_trusted_terminal(run_dir, run_id, AgentRunStatus.COMPLETED)
    raw = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    (run_dir / "result.json").write_bytes(raw)

    terminal = storage.read_native_terminal_result(
        run_dir / "result.json", run_id=run_id
    )
    assert terminal.kind is NativeTerminalKind.TRUSTED

    reconcile.reconcile(root)
    assert sessions.open_session(session_id).quarantine is None
    assert (
        storage.read_native_terminal_result(
            run_dir / "result.json", run_id=run_id
        ).kind
        is NativeTerminalKind.TRUSTED
    )


def test_r5_b3_reconcile_converges_quarantine_pending_fence(
    tmp_path: Path,
) -> None:
    """Interrupted fence (open session.json) refuses lease; reconcile quarantines."""
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-fence-1"
    session_id = "sess-fence-1"
    session_dir = _seed_session(sessions, session_id)
    _seed_lock(session_dir, expires_at=(T0 + dt.timedelta(hours=1)).isoformat())
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="fence-1", run_id=run_id, session_id=session_id)
    _seed_marker(run_dir, run_id)
    sessions.write_quarantine_pending(
        session_id, reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id=run_id, now=T0
    )
    assert (session_dir / QUARANTINE_PENDING_JSON).is_file()
    assert sessions.open_session(session_id).quarantine is None
    with pytest.raises(SessionQuarantinedError):
        sessions.acquire_lock(
            session_id, "hermes", refuse_quarantined=True, now=T0
        )

    reconcile.reconcile(root)
    assert sessions.open_session(session_id).quarantine is not None
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    result = json.loads((run_dir / "result.json").read_text())
    assert result["status"] == "unknown"
    assert result["retryable"] is False


# ---------------------------------------------------------------------------
# WP1.5 — named regressions for each resolved ambiguous tree (plan §7.3)
# ---------------------------------------------------------------------------


def _fixture_root(tmp_path: Path):
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    return root, sessions, Path(events.base_dir)


def test_row3_trusted_unknown_without_actionable_attribution_refuses(
    tmp_path: Path,
) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root,
        "run-row3",
        terminal="trusted_unknown",
        dispatch=True,
        spec="corrupt",
        submission="corrupt",
        session_id="sess-row3",
    )
    before = run_dir.joinpath("result.json").read_bytes()

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)

    assert reconcile.REFUSE_UNATTRIBUTABLE_UNKNOWN_TERMINAL in str(err.value)
    # The terminal stays immutable and no substitute Session is invented.
    assert run_dir.joinpath("result.json").read_bytes() == before
    assert sessions.list_records() == []


def test_row7_valid_spec_with_corrupt_submission_is_pre_dispatch(
    tmp_path: Path,
) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root,
        "run-row7",
        spec="valid",
        launch="absent",
        submission="corrupt",
        session_id="sess-row7",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-row7")

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    # Scoped by the Spec; the lower-priority submission is irrelevant even when
    # corrupt, and the Session stays reusable.
    assert result["session_id"] == "sess-row7"
    assert sessions.open_session("sess-row7").quarantine is None


def test_row8_valid_spec_with_corrupt_launch_refuses(tmp_path: Path) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root,
        "run-row8",
        spec="valid",
        launch="corrupt",
        submission="valid",
        session_id="sess-row8",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-row8")

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)

    assert reconcile.REFUSE_CORRUPT_LAUNCH in str(err.value)
    assert not (run_dir / "result.json").exists()
    assert sessions.open_session("sess-row8").quarantine is None


def test_row8_launch_whose_hash_disagrees_with_its_spec_is_corrupt(
    tmp_path: Path,
) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root, "run-row8-hash", spec="valid", session_id="sess-row8h"
    )
    # Structurally fine and correctly sealed *for itself*, but it is not the
    # launch this Spec sealed.
    rf.write_document(
        run_dir / "launch.json",
        state="valid",
        payload=rf.launch_payload(command="/usr/bin/false"),
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-row8h")

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    assert reconcile.REFUSE_CORRUPT_LAUNCH in str(err.value)


def test_row5_dispatch_with_corrupt_spec_falls_back_to_the_submission(
    tmp_path: Path,
) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root,
        "run-row5-fallback",
        dispatch=True,
        spec="corrupt",
        submission="valid",
        session_id="sess-row5f",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-row5f")

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "unknown"
    assert result["retryable"] is False
    assert result["session_id"] == "sess-row5f"
    assert sessions.open_session("sess-row5f").quarantine is not None


@pytest.mark.parametrize("degraded", ["submission", "launch"], ids=["submission", "launch"])
def test_row5_dispatch_with_valid_spec_wins_over_degraded_evidence(
    tmp_path: Path, degraded: str
) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root,
        "run-row5-spec",
        dispatch=True,
        spec="valid",
        launch="corrupt" if degraded == "launch" else "valid",
        submission="corrupt" if degraded == "submission" else "valid",
        session_id="sess-row5s",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-row5s")

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "unknown"
    assert result["session_id"] == "sess-row5s"
    assert sessions.open_session("sess-row5s").quarantine is not None


def test_row11_all_absent_with_corrupt_submission_refuses(tmp_path: Path) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(runs_root, "run-row11", submission="corrupt")

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)

    assert reconcile.REFUSE_CORRUPT_SUBMISSION in str(err.value)
    assert not (run_dir / "result.json").exists()


def test_row11_all_absent_bare_reservation_invents_no_ownership(
    tmp_path: Path,
) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(runs_root, "run-row11-bare")

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    assert result.get("session_id") in (None, "")
    assert sessions.list_records() == []


def test_row10_launch_without_its_spec_refuses(tmp_path: Path) -> None:
    root, _sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root, "run-row10", spec="absent", launch="valid", submission="valid"
    )

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)

    assert reconcile.REFUSE_LAUNCH_WITHOUT_SPEC in str(err.value)
    assert not (run_dir / "result.json").exists()


def test_row9_corrupt_spec_is_never_rehabilitated_by_a_valid_submission(
    tmp_path: Path,
) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root,
        "run-row9",
        spec="corrupt",
        launch="valid",
        submission="valid",
        session_id="sess-row9",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-row9")

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)

    assert reconcile.REFUSE_CORRUPT_SPEC in str(err.value)
    assert not (run_dir / "result.json").exists()
    assert sessions.open_session("sess-row9").quarantine is None


@pytest.mark.parametrize(
    "session_quarantined", ["owner_mismatch", "namespace_mismatch", "id_mismatch"]
)
def test_a_non_matching_session_record_is_not_actionable(
    tmp_path: Path, session_quarantined: str
) -> None:
    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    rf.build_run(
        runs_root,
        "run-not-actionable",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-not-actionable",
    )
    rf.build_session(
        sessions, state=session_quarantined, session_id="sess-not-actionable"
    )

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    assert reconcile.REFUSE_UNATTRIBUTABLE_DISPATCH in str(err.value)


# ---------------------------------------------------------------------------
# Strict artifact validation: recomputed seals and exact production shapes
# ---------------------------------------------------------------------------


def _rewrite_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8"
    )


def _classified_run(tmp_path: Path, run_id: str, **build):
    root, sessions, runs_root = _fixture_root(tmp_path)
    session_id = build.pop("session_id", f"sess-{run_id}")
    run_dir = rf.build_run(runs_root, run_id, session_id=session_id, **build)
    rf.build_session(sessions, state="matching_open", session_id=session_id)
    reconcile = _import_reconcile()
    return run_dir, sessions, reconcile


def test_an_untampered_spec_and_launch_stay_valid(tmp_path: Path) -> None:
    """Positive control for the strict validators below."""
    run_dir, sessions, reconcile = _classified_run(
        tmp_path, "run-strict-ok", spec="valid", launch="valid", submission="valid"
    )
    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.spec is storage.JsonDocumentKind.VALID
    assert facts.launch is storage.JsonDocumentKind.VALID
    assert facts.submission is storage.JsonDocumentKind.VALID
    assert facts.actionable is True


def test_spec_mutation_behind_an_unchanged_hash_is_corrupt(tmp_path: Path) -> None:
    run_dir, sessions, reconcile = _classified_run(
        tmp_path, "run-spec-tamper", spec="valid", launch="valid"
    )
    payload = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
    embedded = payload["spec_hash"]
    payload["runtime"]["model_id"] = "tampered/model"
    _rewrite_json(run_dir / "spec.json", payload)

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert payload["spec_hash"] == embedded  # the tamperer left the seal alone
    assert facts.spec is storage.JsonDocumentKind.CORRUPT
    assert facts.attribution is None
    assert facts.actionable is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.__setitem__("unknown_field", "planted"),
        lambda p: p.pop("execution_grant"),
        lambda p: p["identity"].pop("namespace"),
    ],
    ids=["unknown_field", "missing_block", "missing_nested_field"],
)
def test_spec_shape_drift_is_corrupt_even_with_a_consistent_hash(
    tmp_path: Path, mutate
) -> None:
    """A self-consistent document that is not the production projection."""
    from agent_run_supervisor.native_acp.spec import spec_hash_of_payload

    run_dir, sessions, reconcile = _classified_run(
        tmp_path, "run-spec-shape", spec="valid", launch="valid"
    )
    payload = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
    mutate(payload)
    payload["spec_hash"] = spec_hash_of_payload(payload)  # re-sealed by the tamperer
    _rewrite_json(run_dir / "spec.json", payload)

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.spec is storage.JsonDocumentKind.CORRUPT
    assert facts.attribution is None


def test_launch_body_tampering_behind_an_unchanged_seal_is_corrupt(
    tmp_path: Path,
) -> None:
    run_dir, sessions, reconcile = _classified_run(
        tmp_path, "run-launch-tamper", spec="valid", launch="valid"
    )
    payload = json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))
    embedded = payload["launch_spec_hash"]
    payload["executable"] = "/bin/false"
    _rewrite_json(run_dir / "launch.json", payload)

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert payload["launch_spec_hash"] == embedded
    assert facts.launch is storage.JsonDocumentKind.CORRUPT


def test_launch_resealed_by_a_tamperer_still_fails_the_spec_reference(
    tmp_path: Path,
) -> None:
    """Self-consistency is not enough: the Spec seals the launch it sealed."""
    from agent_run_supervisor.native_acp.spec import launch_hash_of_payload

    run_dir, sessions, reconcile = _classified_run(
        tmp_path, "run-launch-reseal", spec="valid", launch="valid"
    )
    payload = json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))
    payload["executable"] = "/bin/false"
    payload["launch_spec_hash"] = launch_hash_of_payload(payload)
    _rewrite_json(run_dir / "launch.json", payload)

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.launch is storage.JsonDocumentKind.CORRUPT


def test_launch_unknown_field_is_corrupt(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp.spec import launch_hash_of_payload

    run_dir, sessions, reconcile = _classified_run(
        tmp_path, "run-launch-shape", spec="absent", launch="valid"
    )
    payload = json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))
    payload["unknown_field"] = "planted"
    payload["launch_spec_hash"] = launch_hash_of_payload(payload)
    _rewrite_json(run_dir / "launch.json", payload)

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.launch is storage.JsonDocumentKind.CORRUPT


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("agent_id"),
        lambda p: p.__setitem__("unknown_field", "planted"),
        lambda p: p.__setitem__("agent_id", ""),
        lambda p: p["peer"].__setitem__("extra", 1),
        lambda p: p["peer"].pop("gid"),
    ],
    ids=["missing_agent_id", "unknown_field", "empty_agent_id", "peer_extra", "peer_missing"],
)
def test_submission_shape_drift_is_corrupt_and_unattributable(
    tmp_path: Path, mutate
) -> None:
    """With no Spec this document would be attribution authority — so it must
    be exactly what the writer emits or nothing at all."""
    run_dir, sessions, reconcile = _classified_run(
        tmp_path, "run-submission-shape", spec="absent", submission="valid"
    )
    payload = json.loads((run_dir / "submission.json").read_text(encoding="utf-8"))
    mutate(payload)
    _rewrite_json(run_dir / "submission.json", payload)

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.submission is storage.JsonDocumentKind.CORRUPT
    assert facts.attribution is None
    assert facts.actionable is False


# ---------------------------------------------------------------------------
# The strict validators accept the writers' whole value domain
# ---------------------------------------------------------------------------


def _production_spec_payload(run_id: str, request) -> dict[str, Any]:
    """The exact projection ``RunSpecAssembler.seal`` produces for a request."""
    from agent_run_supervisor.native_acp.spec import (
        AgentRunSpec,
        RunIdentity,
        SpecSession,
        spec_hash,
    )

    spec = dataclasses.replace(
        AgentRunSpec.for_golden_fixture(),
        identity=RunIdentity(owner=request.owner, namespace=request.namespace),
        session=SpecSession(
            session_id=request.session_id,
            expected_binding_hash=request.expected_binding_hash,
        ),
        input_refs=request.input_refs,
        run_id=run_id,
    )
    payload = spec.to_dict()
    payload["spec_hash"] = spec_hash(spec)
    return json.loads(json.dumps(payload))


def _production_submission(run_id: str, command) -> dict[str, Any]:
    return admission.build_submission_artifact(
        key=admission.AdmissionKey(
            principal_id="principal-a", request_id="req-domain"
        ),
        run_id=run_id,
        command=command,
        digest=admission.compute_request_digest(command),
        accepted_at="2026-07-30T00:00:00+00:00",
        peer={"pid": 1, "uid": 1000, "gid": 1000},
    )


def test_an_empty_input_refs_spec_converges_through_row_7(tmp_path: Path) -> None:
    """A Run admitted with no input refs is not corrupt evidence.

    The wire parser accepts ``input_refs: []`` and the writer seals it, so a
    crash between the ordered spec.json and launch.json writes must converge on
    the pre-dispatch row rather than wedging startup on row 9.
    """
    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)
    run_id = "run-empty-refs"
    command = submit_command(
        submit_payload(request=valid_wire_request(input_refs=[]))
    )
    assert command.request.input_refs == ()
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    _write_json(run_dir / "spec.json", _production_spec_payload(run_id, command.request))

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.spec is storage.JsonDocumentKind.VALID
    assert reconcile.select_row(facts).row == 7

    reconcile.reconcile(root)
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"


def test_a_create_attributes_the_prospective_id_from_both_authorities(
    tmp_path: Path,
) -> None:
    """A create carries no ``session_id``, so both writers derive the same one.

    A dispatched create whose Session record already landed therefore reaches
    row 5 and is fenced, rather than row 6 leaving a possibly prompted Session
    unquarantined. The Spec and the submission must agree without conferring.
    """
    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)
    run_id = "run-create-attr"
    command = submit_command(submit_payload(request=valid_wire_request(session_id=None)))
    assert command.request.session_id is None

    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    spec_payload = _production_spec_payload(run_id, command.request)
    submission = _production_submission(run_id, command)
    assert spec_payload["session"].get("session_id") is None
    assert submission["session_id"] is None
    _write_json(run_dir / "spec.json", spec_payload)
    _write_json(run_dir / "submission.json", submission)
    _seed_marker(run_dir, run_id)
    prospective = derive_session_id_for_run(run_id)
    rf.build_session(sessions, state="matching", session_id=prospective)

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.spec is storage.JsonDocumentKind.VALID
    assert facts.submission is storage.JsonDocumentKind.VALID
    assert facts.attribution is not None
    assert facts.attribution.session_id == prospective
    assert facts.attribution.source == "spec"
    assert facts.actionable is True
    assert reconcile.select_row(facts).row == 5

    # The submission alone attributes identically — one rule, two readers.
    fallback = admission.validate_submission_artifact(submission, run_id=run_id)
    assert fallback is not None
    assert fallback.session_id == prospective

    reconcile.reconcile(root)
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "unknown"
    assert result["session_id"] == prospective
    assert sessions.open_session(prospective).quarantine is not None


def test_a_reuse_attributes_its_own_session_id(tmp_path: Path) -> None:
    """The other side of the rule: a present id is the attribution, verbatim."""
    reconcile = _import_reconcile()
    _root, sessions, runs_root = _fixture_root(tmp_path)
    run_id = "run-reuse-domain"
    command = submit_command(submit_payload(request=valid_wire_request()))
    submission = _production_submission(run_id, command)
    assert command.request.session_id is not None
    assert (
        admission.validate_submission_artifact(submission, run_id=run_id).session_id
        == command.request.session_id
    )

    # An unsafe id is not a Session at all: the document is not evidence.
    unsafe = dict(submission)
    unsafe["session_id"] = "../escape"
    assert admission.validate_submission_artifact(unsafe, run_id=run_id) is None

    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    _write_json(run_dir / "submission.json", unsafe)
    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.submission is storage.JsonDocumentKind.CORRUPT


# ---------------------------------------------------------------------------
# Session-record identity: the directory name is not identity
# ---------------------------------------------------------------------------


def test_a_conflicting_internal_session_id_is_never_actionable(
    tmp_path: Path,
) -> None:
    """A valid record inside the requested directory naming another Session."""
    from agent_run_supervisor.session import SESSION_JSON, read_native_session_record

    root, sessions, runs_root = _fixture_root(tmp_path)
    reconcile = _import_reconcile()
    run_dir = rf.build_run(
        runs_root,
        "run-id-conflict",
        dispatch=True,
        spec="valid",
        launch="valid",
        submission="valid",
        session_id="sess-requested",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-requested")
    record_path = Path(sessions.base_dir) / "sess-requested" / SESSION_JSON
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["session_id"] = "sess-conflicting"
    _rewrite_json(record_path, payload)

    assert read_native_session_record(sessions, "sess-requested") is None
    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.attribution is not None  # the Spec still attributes
    assert facts.actionable is False
    assert reconcile.select_row(facts).row == 6

    with pytest.raises(reconcile.ReconciliationError) as err:
        reconcile.reconcile(root)
    assert reconcile.REFUSE_UNATTRIBUTABLE_DISPATCH in str(err.value)
    assert not (run_dir / "result.json").exists()
    # No Session mutation of any kind.
    assert json.loads(record_path.read_text(encoding="utf-8")) == payload


# ---------------------------------------------------------------------------
# WP1.7 — no replay, no side effect, and completion before bind
# ---------------------------------------------------------------------------


def test_no_replay_call_trace_over_every_converging_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero registry, ACP, prompt, process, Session-create, and lease calls.

    A live call trace rather than a lexical scan: every forbidden entry point
    is replaced by a tripwire, and a root exercising each converging row is
    reconciled end to end.
    """
    from agent_run_supervisor.native_acp import agent_registry
    from agent_run_supervisor.native_acp.driver import NativeAcpDriver
    from agent_run_supervisor.session import SessionStore

    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)

    # Row 5: dispatched, actionable.
    rf.build_run(
        runs_root,
        "run-trace-5",
        dispatch=True,
        spec="valid",
        launch="valid",
        submission="valid",
        session_id="sess-trace-5",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-trace-5")
    # Row 2: trusted unknown, actionable.
    rf.build_run(
        runs_root,
        "run-trace-2",
        terminal="trusted_unknown",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-trace-2",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-trace-2")
    # Row 1: trusted terminal, untouched.
    rf.build_run(
        runs_root,
        "run-trace-1",
        terminal="trusted_terminal",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-trace-1",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-trace-1")
    # Row 7 and row 11: pre-dispatch, reusable.
    rf.build_run(runs_root, "run-trace-7", spec="valid", session_id="sess-trace-7")
    rf.build_session(sessions, state="matching_open", session_id="sess-trace-7")
    rf.build_run(runs_root, "run-trace-11", submission="valid", session_id="sess-t11")
    rf.build_session(sessions, state="matching_open", session_id="sess-t11")

    tripped: list[str] = []

    def tripwire(name: str):
        def fail(*args, **kwargs):
            tripped.append(name)
            raise AssertionError(f"reconciliation called {name}")

        return fail

    for owner, attr in (
        (SessionStore, "acquire_lock"),
        (SessionStore, "release_lock"),
        (SessionStore, "update_lock_holder"),
        (SessionStore, "create_native_session"),
        (SessionStore, "create_session"),
        (SessionStore, "commit_last_effective"),
        (storage, "create_native_session"),
        (agent_registry, "load_agents_file"),
        (admission, "resolve_agent_entry"),
        (NativeAcpDriver, "open"),
        (NativeAcpDriver, "initialize"),
        (NativeAcpDriver, "new_session"),
        (NativeAcpDriver, "load_session"),
        (NativeAcpDriver, "set_config_exact"),
        (NativeAcpDriver, "prompt_once"),
    ):
        monkeypatch.setattr(owner, attr, tripwire(f"{owner}.{attr}"))

    import agent_run_supervisor.managed_process as managed_process

    monkeypatch.setattr(
        managed_process, "spawn_managed_process", tripwire("spawn_managed_process")
    )

    # Negative control: the tripwires are installed and lethal, so an empty
    # trace below is evidence rather than an accident of patching nothing.
    with pytest.raises(AssertionError, match="reconciliation called"):
        SessionStore.acquire_lock(sessions, "sess-trace-5", "hermes")
    assert tripped == [f"{SessionStore}.acquire_lock"]
    tripped.clear()

    reconcile.reconcile(root)

    assert tripped == []
    # …and the converging rows still did their work.
    assert sessions.open_session("sess-trace-5").quarantine is not None
    assert sessions.open_session("sess-trace-2").quarantine is not None
    assert sessions.open_session("sess-trace-1").quarantine is None
    assert sessions.open_session("sess-trace-7").quarantine is None
    assert json.loads(
        (runs_root / "run-trace-7" / "result.json").read_text()
    )["detail_code"] == "RECONCILED_PRE_DISPATCH"


def test_no_replay_leaves_every_seeded_lock_byte_identical(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)
    rf.build_run(
        runs_root,
        "run-trace-lock",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-trace-lock",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-trace-lock")
    session_dir = Path(sessions.base_dir) / "sess-trace-lock"
    lock_bytes = _seed_lock(
        session_dir, expires_at=(T0 + dt.timedelta(hours=1)).isoformat()
    )

    reconcile.reconcile(root)

    # No lease is acquired, released, or unlinked — the holder's lease is not
    # reconciliation's to touch.
    assert (session_dir / LOCK_JSON).read_bytes() == lock_bytes


def _daemon_kwargs(tmp_path: Path, root: Path, *, stop: asyncio.Event | None = None):
    # A real, minimal agents file: the registry is parsed once at startup,
    # before reconciliation, so it has to exist. It lives in its own
    # directory so it cannot contain, or be contained by, a daemon-owned
    # surface.
    from tests.native_acp import registry_fixtures as rfx

    conf = tmp_path / "conf"
    conf.mkdir(exist_ok=True)
    agents_file = rfx.write_registry(conf)
    socket_dir = tmp_path / "run"
    socket_dir.mkdir(exist_ok=True)
    principal = server.Principal(
        principal_id="hermes-local",
        owner_namespaces=frozenset({("hermes", "hermes/doc-check")}),
    )
    return {
        "socket_path": socket_dir / "arsd.sock",
        "supervisor_root": root,
        "policy": server.CallerPolicy({os.getuid(): principal}),
        "agents_file": str(agents_file),
        "run_task_factory": SpyFactory(),
        "install_signals": False,
        "stop_event": stop,
    }


def test_before_bind_a_refusing_reconciliation_never_listens(tmp_path: Path) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)
    # Row 6: dispatched with no actionable Session attribution → refuse.
    rf.build_run(
        runs_root,
        "run-before-bind",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-absent",
    )
    kwargs = _daemon_kwargs(tmp_path, root)

    with pytest.raises(arsd_main.DaemonStartupError) as err:
        run_async(arsd_main.serve_daemon(**kwargs))

    assert reconcile.REFUSE_UNATTRIBUTABLE_DISPATCH in str(err.value)
    # The socket was never created, so nothing could have connected.
    assert not Path(kwargs["socket_path"]).exists()


def test_before_bind_reconciliation_is_complete_when_the_socket_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    root, sessions, runs_root = _fixture_root(tmp_path)
    rf.build_run(
        runs_root,
        "run-bind-order",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-bind-order",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-bind-order")

    observed: dict[str, Any] = {}
    real_start = server.ArsdServer.start

    async def observing_start(self):
        run_dir = runs_root / "run-bind-order"
        observed["result"] = (run_dir / "result.json").is_file()
        observed["progress"] = (run_dir / "progress.json").is_file()
        observed["session_quarantined"] = (
            sessions.open_session("sess-bind-order").quarantine is not None
        )
        return await real_start(self)

    monkeypatch.setattr(server.ArsdServer, "start", observing_start)

    async def case() -> None:
        stop = asyncio.Event()
        stop.set()  # bind, then stop immediately
        await arsd_main.serve_daemon(**_daemon_kwargs(tmp_path, root, stop=stop))

    run_async(case())

    # Every reconciliation write of this Run was already durable at bind time.
    assert observed["result"] is True
    assert observed["progress"] is True
    assert observed["session_quarantined"] is True


# ---------------------------------------------------------------------------
# WP1.6 — crash-convergent, idempotent fence → quarantine → progress → terminal
# ---------------------------------------------------------------------------


def _dispatched_tree(tmp_path: Path, *, run_id: str, session_id: str):
    root, sessions, runs_root = _fixture_root(tmp_path)
    run_dir = rf.build_run(
        runs_root,
        run_id,
        terminal="absent",
        dispatch=True,
        spec="valid",
        launch="valid",
        submission="valid",
        session_id=session_id,
    )
    rf.build_session(sessions, state="matching_open", session_id=session_id)
    return root, sessions, run_dir


def test_crash_injection_before_the_fence_leaves_nothing_and_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON, SessionStore

    reconcile = _import_reconcile()
    root, sessions, run_dir = _dispatched_tree(
        tmp_path, run_id="run-crash-0", session_id="sess-crash-0"
    )
    real_fence = SessionStore.write_quarantine_pending

    def boom(self, session_id, *, reason_code, run_id, now=None):
        raise OSError("injected fence write failure")

    monkeypatch.setattr(SessionStore, "write_quarantine_pending", boom)
    with pytest.raises(OSError, match="injected fence write failure"):
        reconcile.reconcile(root)

    session_dir = Path(sessions.base_dir) / "sess-crash-0"
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    assert sessions.open_session("sess-crash-0").quarantine is None
    assert not (run_dir / "progress.json").exists()
    assert not (run_dir / "result.json").exists()

    monkeypatch.setattr(SessionStore, "write_quarantine_pending", real_fence)
    reconcile.reconcile(root)
    assert sessions.open_session("sess-crash-0").quarantine is not None
    assert json.loads((run_dir / "result.json").read_text())["status"] == "unknown"


def test_crash_injection_after_the_fence_leaves_a_non_leasable_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fence lands first, so a crash before quarantine still refuses leases."""
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON, SessionStore

    reconcile = _import_reconcile()
    root, sessions, run_dir = _dispatched_tree(
        tmp_path, run_id="run-crash-1", session_id="sess-crash-1"
    )
    real_mark = SessionStore.mark_quarantined

    def boom(self, session_id, *, reason_code, run_id, now=None):
        raise OSError("injected quarantine failure")

    monkeypatch.setattr(SessionStore, "mark_quarantined", boom)
    with pytest.raises(OSError, match="injected quarantine failure"):
        reconcile.reconcile(root)

    session_dir = Path(sessions.base_dir) / "sess-crash-1"
    assert (session_dir / QUARANTINE_PENDING_JSON).is_file()
    assert sessions.open_session("sess-crash-1").quarantine is None
    with pytest.raises(SessionQuarantinedError):
        sessions.acquire_lock(
            "sess-crash-1", "hermes", refuse_quarantined=True, now=T0
        )
    assert not (run_dir / "progress.json").exists()
    assert not (run_dir / "result.json").exists()

    monkeypatch.setattr(SessionStore, "mark_quarantined", real_mark)
    reconcile.reconcile(root)
    assert sessions.open_session("sess-crash-1").quarantine is not None
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    assert json.loads((run_dir / "result.json").read_text())["status"] == "unknown"


def test_crash_injection_after_progress_before_terminal_resumes_the_same_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = _import_reconcile()
    root, sessions, run_dir = _dispatched_tree(
        tmp_path, run_id="run-crash-2", session_id="sess-crash-2"
    )
    real_write = storage.write_once_json

    def boom(path, payload):
        if Path(path).name == "result.json":
            raise RuntimeError("injected crash before the terminal write")
        return real_write(path, payload)

    monkeypatch.setattr(storage, "write_once_json", boom)
    with pytest.raises(RuntimeError, match="injected crash"):
        reconcile.reconcile(root)
    assert sessions.open_session("sess-crash-2").quarantine is not None
    assert json.loads((run_dir / "progress.json").read_text())["state"] == "unknown"
    assert not (run_dir / "result.json").exists()

    monkeypatch.setattr(storage, "write_once_json", real_write)
    reconcile.reconcile(root)
    result = json.loads((run_dir / "result.json").read_text())
    assert result["status"] == "unknown"
    assert result["detail_code"] == "RECONCILED_UNKNOWN"
    assert result["session_id"] == "sess-crash-2"


def test_crash_injection_reruns_keep_the_trusted_terminal_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 2: step 4 is skipped forever — the existing terminal is authority."""
    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)
    run_dir = rf.build_run(
        runs_root,
        "run-crash-3",
        terminal="trusted_unknown",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-crash-3",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-crash-3")
    before = (run_dir / "result.json").read_bytes()

    written: list[str] = []
    real_write = storage.write_once_json

    def tracking(path, payload):
        written.append(Path(path).name)
        return real_write(path, payload)

    monkeypatch.setattr(storage, "write_once_json", tracking)
    for _ in range(3):
        reconcile.reconcile(root)

    assert "result.json" not in written
    assert (run_dir / "result.json").read_bytes() == before
    assert sessions.open_session("sess-crash-3").quarantine is not None
    assert json.loads((run_dir / "progress.json").read_text())["state"] == "unknown"


def test_crash_injection_rerun_is_a_no_op_for_an_already_quarantined_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-quarantined Session is a no-op on every rerun — fence included."""
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON, SessionStore

    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)
    run_dir = rf.build_run(
        runs_root,
        "run-crash-4",
        terminal="trusted_unknown",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-crash-4",
    )
    rf.build_session(
        sessions, state="already_quarantined", session_id="sess-crash-4"
    )
    reconcile.reconcile(root)  # converge progress once

    fenced: list[str] = []
    real_fence = SessionStore.write_quarantine_pending

    def tracking(self, session_id, *, reason, run_id, now=None):
        fenced.append(session_id)
        return real_fence(self, session_id, reason=reason, run_id=run_id, now=now)

    monkeypatch.setattr(SessionStore, "write_quarantine_pending", tracking)
    runs_before = _tree_snapshot(runs_root)
    sessions_before = _tree_snapshot(Path(sessions.base_dir))

    reconcile.reconcile(root)

    assert fenced == []
    session_dir = Path(sessions.base_dir) / "sess-crash-4"
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    assert _tree_snapshot(runs_root) == runs_before
    assert _tree_snapshot(Path(sessions.base_dir)) == sessions_before
    assert run_dir.is_dir()


def test_crash_injection_clears_a_stale_fence_on_a_quarantined_session(
    tmp_path: Path,
) -> None:
    """A crash between fence and state still converges: the fence is cleared."""
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)
    rf.build_run(
        runs_root,
        "run-crash-5",
        terminal="trusted_unknown",
        dispatch=True,
        spec="valid",
        submission="valid",
        session_id="sess-crash-5",
    )
    rf.build_session(
        sessions, state="already_quarantined", session_id="sess-crash-5"
    )
    sessions.write_quarantine_pending(
        "sess-crash-5", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-crash-5"
    )
    session_dir = Path(sessions.base_dir) / "sess-crash-5"
    assert (session_dir / QUARANTINE_PENDING_JSON).is_file()

    reconcile.reconcile(root)

    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    assert sessions.open_session("sess-crash-5").quarantine is not None


# ---------------------------------------------------------------------------
# WP1.4 — bounded no-follow classification, strict submission, both markers
# ---------------------------------------------------------------------------


def _classify(path: Path):
    return storage.classify_json_document(path)


def test_clean_absence_is_the_only_route_to_absent(tmp_path: Path) -> None:
    kinds = storage.JsonDocumentKind
    assert _classify(tmp_path / "nothing.json").kind is kinds.ABSENT

    good = tmp_path / "good.json"
    _write_json(good, {"a": 1})
    state = _classify(good)
    assert state.kind is kinds.VALID
    assert state.payload == {"a": 1}


@pytest.mark.parametrize(
    "arrangement",
    [
        "truncated",
        "empty",
        "not_an_object",
        "not_utf8",
        "symlink",
        "directory",
        "fifo",
        "oversize",
    ],
)
def test_every_present_or_indeterminate_state_is_corrupt(
    tmp_path: Path, arrangement: str
) -> None:
    path = tmp_path / "doc.json"
    if arrangement == "truncated":
        path.write_bytes(b'{"a": ')
    elif arrangement == "empty":
        path.write_bytes(b"")
    elif arrangement == "not_an_object":
        path.write_bytes(b'["not", "an", "object"]')
    elif arrangement == "not_utf8":
        path.write_bytes(b'{"a": "\xff\xfe"}')
    elif arrangement == "symlink":
        target = tmp_path / "target.json"
        _write_json(target, {"a": 1})
        path.symlink_to(target)
    elif arrangement == "directory":
        path.mkdir()
    elif arrangement == "fifo":
        os.mkfifo(path)
    elif arrangement == "oversize":
        padding = "x" * storage.MAX_RECONCILE_JSON_BYTES
        path.write_text(json.dumps({"a": padding}), encoding="utf-8")

    assert _classify(path).kind is storage.JsonDocumentKind.CORRUPT


def test_a_read_failure_after_observed_presence_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "doc.json"
    _write_json(path, {"a": 1})
    real_read = os.read

    def boom(fd: int, n: int) -> bytes:
        raise OSError(5, "injected read failure")

    monkeypatch.setattr(os, "read", boom)
    try:
        assert _classify(path).kind is storage.JsonDocumentKind.CORRUPT
    finally:
        monkeypatch.setattr(os, "read", real_read)


def test_the_open_never_blocks_on_a_writerless_fifo(tmp_path: Path) -> None:
    # O_NONBLOCK: a reader-side open of a FIFO with no writer would block
    # forever without it, and a blocked startup never reaches the socket.
    path = tmp_path / "doc.json"
    os.mkfifo(path)
    assert _classify(path).kind is storage.JsonDocumentKind.CORRUPT


def _dispatched_run(
    tmp_path: Path, *, marker: str, arrange
) -> tuple[Any, Path, str, str]:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-marker-1"
    session_id = "sess-reuse-1"
    _seed_session(sessions, session_id)
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="marker-1", run_id=run_id)
    arrange(run_dir / marker)
    reconcile.reconcile(root)
    return sessions, run_dir, run_id, session_id


@pytest.mark.parametrize(
    "marker", [DISPATCH_STARTED_MARKER, PROMPT_ACCEPTED_MARKER], ids=["started", "accepted"]
)
@pytest.mark.parametrize(
    "arrangement", ["regular", "symlink", "directory", "malformed"]
)
def test_either_marker_in_any_shape_means_dispatch_is_present(
    tmp_path: Path, marker: str, arrangement: str
) -> None:
    """Reviewer note 8: ``lstat`` over both names, regardless of type/content."""

    def arrange(path: Path) -> None:
        if arrangement == "regular":
            storage.write_once_json(path, {"marker": path.name})
        elif arrangement == "symlink":
            elsewhere = path.parent / "marker-target"
            elsewhere.write_bytes(b"{}")
            path.symlink_to(elsewhere)
        elif arrangement == "directory":
            path.mkdir()
        elif arrangement == "malformed":
            path.write_bytes(b"not a marker document at all")

    sessions, run_dir, run_id, session_id = _dispatched_run(
        tmp_path, marker=marker, arrange=arrange
    )

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "unknown"
    assert result["detail_code"] == "RECONCILED_UNKNOWN"
    assert result["retryable"] is False
    assert sessions.open_session(session_id).quarantine is not None


def test_no_marker_at_all_stays_pre_dispatch(tmp_path: Path) -> None:
    reconcile = _import_reconcile()
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    run_id = "run-no-marker-1"
    _seed_session(sessions, "sess-reuse-1")
    run_dir = events.create_run(run_id).run_dir
    _seed_submission(run_dir, request_id="no-marker-1", run_id=run_id)

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    assert sessions.open_session("sess-reuse-1").quarantine is None


def _submission_payload(**overrides) -> dict[str, Any]:
    payload = {
        "schema_version": admission.SUBMISSION_SCHEMA_VERSION,
        "principal_id": "principal-a",
        "request_id": "req-1",
        "run_id": "run-attr-1",
        "retry_of_run_id": None,
        "api_version": protocol.ARSD_API_VERSION,
        "accepted_at": "2026-07-22T00:00:00+00:00",
        "peer": {"pid": 1, "uid": 1, "gid": 1},
        "owner": "hermes",
        "namespace": "hermes/doc-check",
        "session_id": "sess-reuse-1",
        "agent_id": "fake-agent",
        "request_digest": "sha256:" + "a" * 64,
        "prompt_sha256": "b" * 64,
        "prompt_bytes": 17,
    }
    payload.update(overrides)
    return payload


def test_strict_submission_validator_accepts_the_exact_v1_shape() -> None:
    attribution = admission.validate_submission_artifact(
        _submission_payload(), run_id="run-attr-1"
    )
    assert attribution is not None
    assert attribution.run_id == "run-attr-1"
    assert attribution.owner == "hermes"
    assert attribution.namespace == "hermes/doc-check"
    assert attribution.session_id == "sess-reuse-1"


def test_strict_submission_validator_derives_the_prospective_id() -> None:
    """A create submission names no Session; the one rule derives which."""
    attribution = admission.validate_submission_artifact(
        _submission_payload(session_id=None),
        run_id="run-attr-1",
    )
    assert attribution is not None
    assert attribution.session_id == derive_session_id_for_run("run-attr-1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": "run-somebody-else"},
        {"schema_version": 99},
        {"owner": ""},
        {"namespace": None},
        {"principal_id": ""},
        {"request_id": "not a request id"},
        {"session_id": ""},
        {"session_id": "../escape"},
        {"session_id": 17},
        {"prompt_bytes": -1},
        {"prompt_bytes": True},
        {"peer": {"pid": "1", "uid": 1, "gid": 1}},
        {"request_digest": ""},
    ],
    ids=[
        "foreign_run_id",
        "schema_version",
        "empty_owner",
        "missing_namespace",
        "empty_principal",
        "bad_request_id",
        "empty_session_id",
        "unsafe_session_id",
        "non_string_session_id",
        "negative_prompt_bytes",
        "boolean_prompt_bytes",
        "non_integer_peer",
        "empty_digest",
    ],
)
def test_strict_submission_validator_refuses_every_defect(overrides: dict) -> None:
    assert (
        admission.validate_submission_artifact(
            _submission_payload(**overrides), run_id="run-attr-1"
        )
        is None
    )


def test_submission_classification_separates_absent_from_corrupt(
    tmp_path: Path,
) -> None:
    kinds = storage.JsonDocumentKind
    run_dir = tmp_path / "run-attr-1"
    run_dir.mkdir()
    assert admission.classify_submission(run_dir, run_id="run-attr-1").kind is (
        kinds.ABSENT
    )

    (run_dir / "submission.json").write_bytes(b"{ truncated")
    assert admission.classify_submission(run_dir, run_id="run-attr-1").kind is (
        kinds.CORRUPT
    )

    (run_dir / "submission.json").unlink()
    _write_json(run_dir / "submission.json", _submission_payload())
    state = admission.classify_submission(run_dir, run_id="run-attr-1")
    assert state.kind is kinds.VALID
    assert state.attribution is not None
    assert state.attribution.session_id == "sess-reuse-1"

    # A structurally readable document that fails strict validation is corrupt,
    # never a weaker "valid enough" attribution source.
    (run_dir / "submission.json").unlink()
    _write_json(run_dir / "submission.json", _submission_payload(owner=""))
    assert admission.classify_submission(run_dir, run_id="run-attr-1").kind is (
        kinds.CORRUPT
    )


def test_r6_b3_secure_terminal_reader_maps_int_limit_and_nesting(
    tmp_path: Path,
) -> None:
    from agent_run_supervisor.native_acp import storage
    from agent_run_supervisor.native_acp.storage import NativeTerminalKind

    run_dir = tmp_path / "run_malformed"
    run_dir.mkdir()
    path = run_dir / "result.json"
    # Over-digit integer within the Native terminal read cap.
    path.write_bytes(b'{"n":' + b"1" + b"0" * 10000 + b"}")
    assert (
        storage.read_native_terminal_result(path, run_id="run_malformed").kind
        is NativeTerminalKind.INVALID
    )

    depth = 3000
    nested = b"{" + b'"a":{' * depth + b'"x":1' + b"}" * depth + b"}"
    if len(nested) < storage._MAX_TERMINAL_READ_BYTES:
        path.write_bytes(nested)
        assert (
            storage.read_native_terminal_result(path, run_id="run_malformed").kind
            is NativeTerminalKind.INVALID
        )


# --- B2: an interrupted configuration switch is not a plain pre-dispatch row ---
#
# Between publishing the bound Session record and writing the dispatch marker,
# ARS mutates the agent's configuration. A crash inside that window leaves a
# Session whose configuration ARS never proved. Reconciliation cannot ask the
# dead process what it was doing, so the Run directory has to say it: which
# configuration boundary was crossed, and nothing else.


def _seed_config_markers(run_dir: Path, *names: str) -> None:
    for ordinal, name in enumerate(names, start=1):
        storage.write_once_json(
            run_dir / name,
            {
                "marker": name,
                "run_id": run_dir.name,
                "ordinal": ordinal,
                "created_at": "2026-07-22T00:00:00+00:00",
            },
        )


def _pre_dispatch_run(tmp_path: Path, run_id: str, *markers: str):
    reconcile = _import_reconcile()
    root, sessions, runs_root = _fixture_root(tmp_path)
    _seed_session(sessions, "sess-reuse-1")
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    _seed_spec(run_dir, run_id=run_id, session_id="sess-reuse-1")
    _seed_submission(run_dir, request_id=f"{run_id}-req", run_id=run_id)
    _seed_config_markers(run_dir, *markers)
    return reconcile, root, sessions, run_dir


def test_a_crash_after_a_dispatched_switch_without_proof_quarantines(
    tmp_path: Path,
) -> None:
    """The switch may have landed and nothing proved what it landed as."""
    reconcile, root, sessions, run_dir = _pre_dispatch_run(
        tmp_path, "run-switch-unproven", CONFIG_SWITCH_STARTED_MARKER
    )

    reconcile.reconcile(root)

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["detail_code"] == "RECONCILED_PRE_DISPATCH"
    assert sessions.open_session("sess-reuse-1").quarantine is not None


def test_a_crash_before_any_switch_leaves_the_session_reusable(
    tmp_path: Path,
) -> None:
    """No set was dispatched, so the agent's configuration never moved."""
    reconcile, root, sessions, run_dir = _pre_dispatch_run(
        tmp_path, "run-switch-none"
    )

    reconcile.reconcile(root)

    assert sessions.open_session("sess-reuse-1").quarantine is None


def test_a_crash_after_proven_configuration_leaves_the_session_reusable(
    tmp_path: Path,
) -> None:
    """Exact readback proved what the agent's configuration is."""
    reconcile, root, sessions, run_dir = _pre_dispatch_run(
        tmp_path,
        "run-switch-proven",
        CONFIG_SWITCH_STARTED_MARKER,
        CONFIG_PROVEN_MARKER,
    )

    reconcile.reconcile(root)

    assert sessions.open_session("sess-reuse-1").quarantine is None


def test_a_crash_after_proven_rollback_leaves_the_session_reusable(
    tmp_path: Path,
) -> None:
    """The switch was undone with exact readback proof."""
    reconcile, root, sessions, run_dir = _pre_dispatch_run(
        tmp_path,
        "run-switch-rolled-back",
        CONFIG_SWITCH_STARTED_MARKER,
        CONFIG_ROLLBACK_PROVEN_MARKER,
    )

    reconcile.reconcile(root)

    assert sessions.open_session("sess-reuse-1").quarantine is None


def test_an_unproven_switch_marker_of_any_shape_counts_as_started(
    tmp_path: Path,
) -> None:
    """Same conservative rule as the dispatch markers: shape cannot clear it."""
    reconcile, root, sessions, run_dir = _pre_dispatch_run(
        tmp_path, "run-switch-weird"
    )
    (run_dir / CONFIG_SWITCH_STARTED_MARKER).mkdir()

    reconcile.reconcile(root)

    assert sessions.open_session("sess-reuse-1").quarantine is not None

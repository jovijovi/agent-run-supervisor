"""Slice 3c — run registry and principal-bound handlers (plan §5/§7, §15).

Drives `ArsdHandlers` with injected fakes for registry bounds, ownership,
status/events/follow/cancel, session ops, and disconnect persistence. One
real-`RunTask` path proves the SessionStore lease remains authoritative.
No socket, no real AGENT, no acpx.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import admission, handlers, protocol, server
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.session import STATE_OPEN, SessionLockError

from tests.arsd.test_admission import (
    Harness,
    caller_for,
    principal_a,
    principal_b,
    run_async,
    submit_payload,
    valid_wire_request,
)


def parsed(op: str, request_id: str, payload: dict | None = None) -> protocol.ParsedRequest:
    return protocol.ParsedRequest(op=op, request_id=request_id, payload=payload or {})


async def call(harness: Harness, caller, op: str, request_id: str, payload=None):
    return await harness.handlers(caller, parsed(op, request_id, payload))


async def expect_code(harness, caller, op, request_id, code, payload=None):
    with pytest.raises(protocol.ProtocolError) as err:
        await call(harness, caller, op, request_id, payload)
    assert err.value.code == code
    return err.value


def seed_session(store, *, session_id: str, owner="hermes", namespace="hermes/doc-check"):
    return storage.create_native_session(
        store,
        session_id=session_id,
        profile_id="fake-agent-1.0",
        profile_revision=1,
        profile_hash="a" * 64,
        owner=owner,
        namespace=namespace,
        workspace_hash="b" * 64,
        effective_cwd="/tmp/ws",
        matched_root="/tmp/ws",
    )


def seed_events(run_dir: Path, count: int) -> list[dict]:
    path = run_dir / "events.jsonl"
    events = []
    with open(path, "w", encoding="utf-8") as stream:
        for seq in range(1, count + 1):
            event = {"seq": seq, "type": "message", "text": f"e{seq}"}
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            events.append(event)
    os.chmod(path, 0o600)
    return events


class CancelFake:
    """Shapes cancel-terminal rows without a real ACP child."""

    def __init__(
        self,
        *,
        prepared_handle,
        mode: str,
        session_store=None,
        session_id: str | None = None,
    ) -> None:
        self.prepared_handle = prepared_handle
        self.mode = mode
        self.session_store = session_store
        self.session_id = session_id
        self.started = asyncio.Event()

    async def run(self):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            run_id = self.prepared_handle.run_id
            run_dir = self.prepared_handle.run_dir
            from agent_run_supervisor.exit_classifier import (
                _RETRYABLE_DEFAULT,
                AgentRunStatus,
            )
            from agent_run_supervisor.result import build_result_payload

            if self.mode == "pre-dispatch":
                status = AgentRunStatus.FAILED
                detail = "CANCELLED_PRE_DISPATCH"
                origin = "supervisor"
                stop_reason = None
            elif self.mode == "cancelled-terminal":
                status = AgentRunStatus.CANCELLED
                detail = None
                origin = "acp"
                stop_reason = "cancelled"
            elif self.mode == "no-terminal":
                status = AgentRunStatus.UNKNOWN
                detail = "CANCEL_WITHOUT_TERMINAL"
                origin = "supervisor"
                stop_reason = None
            else:
                raise RuntimeError(f"unknown cancel fake mode: {self.mode}")
            storage.write_once_json(
                run_dir / "result.json",
                build_result_payload(
                    run_id=run_id,
                    status=status,
                    origin=origin,
                    detail_code=detail,
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
                ),
            )
            if self.mode == "no-terminal":
                if self.session_store is not None and self.session_id is not None:
                    self.session_store.mark_quarantined(
                        self.session_id,
                        reason="cancel_without_trustworthy_terminal",
                        run_id=run_id,
                    )
            raise


class CancelFactory:
    def __init__(self, mode: str, *, session_store=None) -> None:
        self.mode = mode
        self.session_store = session_store
        self.handlers: handlers.ArsdHandlers | None = None
        self.runners: list[CancelFake] = []

    def __call__(self, *, command, run_id, prepared_handle, submitted_at):
        session_id = command.request.ars_session_id
        runner = CancelFake(
            prepared_handle=prepared_handle,
            mode=self.mode,
            session_store=self.session_store,
            session_id=session_id,
        )
        self.runners.append(runner)
        return runner


# --- capacity / session busy --------------------------------------------------


def test_max_concurrent_runs_capacity_exhausted(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, max_concurrent_runs=1)
        caller = caller_for(principal_a())
        try:
            await harness.submit(caller, "cap-1")
            await expect_code(
                harness,
                caller,
                "submit",
                "cap-2",
                protocol.CAPACITY_EXHAUSTED,
                submit_payload(
                    request=valid_wire_request(ars_session_id="sess-cap-2")
                ),
            )
        finally:
            await harness.aclose()

    run_async(case())


def test_second_distinct_key_on_active_session_is_busy(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, max_concurrent_runs=4)
        caller = caller_for(principal_a())
        try:
            await harness.submit(caller, "busy-1")
            await expect_code(
                harness,
                caller,
                "submit",
                "busy-2",
                protocol.SESSION_BUSY,
                submit_payload(
                    request=valid_wire_request(ars_session_id="sess-arsd-1")
                ),
            )
            assert len(harness.factory.calls) == 1
        finally:
            await harness.aclose()

    run_async(case())


def test_session_lease_remains_authoritative_for_real_runtask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registry fast-fail is an optimization; SessionStore lease is authority."""
    pytest.importorskip("acp")
    import sys

    from agent_run_supervisor.native_acp import profile as profile_module
    from agent_run_supervisor.native_acp.profile import AgentProfile, ProfileRegistry
    from agent_run_supervisor.native_acp.run_task import RunTask
    from agent_run_supervisor.arsd.protocol import parse_submit

    monkeypatch.setitem(
        profile_module._REGISTERED_EXECUTABLES, "python-fake", Path(sys.executable)
    )
    registry = ProfileRegistry(
        (
            AgentProfile(
                profile_id="fake-agent-1.0",
                revision=1,
                executable_key="python-fake",
                argv_template=("agent.py",),
                env_allowlist=("PATH",),
                credential_slots=(),
                model_selector_id="model",
                effort_selector_id="effort",
                default_model="kimi-for-coding/k3",
                default_effort="max",
                registered_models=("kimi-for-coding/k3",),
                allowed_efforts=("low", "medium", "high", "max"),
                requires_session_load=False,
                config_schema={"selectors": {}},
            ),
        )
    )
    root = tmp_path / "svroot"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    session_store = storage.native_session_store(root)
    event_store = storage.native_event_store(root)
    seed_session(session_store, session_id="sess-lease-1")
    held = session_store.acquire_lock(
        "sess-lease-1", "hermes", reclaimable=False, required_state=STATE_OPEN
    )
    try:
        with pytest.raises(SessionLockError):
            session_store.acquire_lock(
                "sess-lease-1",
                "hermes",
                reclaimable=False,
                required_state=STATE_OPEN,
            )
        run_id = admission.derive_run_id(
            admission.AdmissionKey(
                principal_id="principal-a", request_id="lease-rt-1"
            )
        )
        handle = admission.prepare_run(event_store, run_id)
        command = parse_submit(
            submit_payload(
                workspace_root=str(workspace),
                request=valid_wire_request(ars_session_id="sess-lease-1"),
            )
        )
        task = RunTask(
            request=command.request,
            prompt_text=command.prompt_text,
            run_id=run_id,
            workspace_root=workspace,
            registry=registry,
            session_store=session_store,
            event_store=event_store,
            prepared_handle=handle,
            cwd=command.cwd,
            retry_of_run_id=command.retry_of_run_id,
        )

        async def drive():
            return await task.run()

        # Held lease is authoritative even when the registry never saw the Run.
        result = run_async(drive())
        assert result.status.value in {"failed", "unknown", "runner_error"}
        assert held.token  # original holder still owns the lease
    finally:
        session_store.release_lock("sess-lease-1", held.token)


# --- ownership ----------------------------------------------------------------


def test_owner_mismatch_and_unknown_on_run_ops(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller_a = caller_for(principal_a())
        caller_b = caller_for(principal_b())
        try:
            reply = await harness.submit(caller_a, "own-1")
            run_id = reply["run_id"]
            task = harness.handlers.registry.task_for(run_id)
            if task is not None:
                await asyncio.wait({task})
            await expect_code(
                harness,
                caller_b,
                "run_status",
                "own-status",
                protocol.OWNER_MISMATCH,
                {"run_id": run_id},
            )
            await expect_code(
                harness,
                caller_b,
                "run_events",
                "own-events",
                protocol.OWNER_MISMATCH,
                {"run_id": run_id},
            )
            await expect_code(
                harness,
                caller_b,
                "run_cancel",
                "own-cancel",
                protocol.OWNER_MISMATCH,
                {"run_id": run_id},
            )
            await expect_code(
                harness,
                caller_a,
                "run_status",
                "unk-1",
                protocol.UNKNOWN_RUN,
                {"run_id": "run-" + "0" * 32},
            )
        finally:
            await harness.aclose()

    run_async(case())


def test_ownership_from_submission_without_spec(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "sub-only-1")
            run_id = reply["run_id"]
            # Pre-spec accepted Run: submission is authoritative.
            assert not (harness.run_dir(run_id) / "spec.json").exists()
            status = await call(
                harness, caller, "run_status", "st-1", {"run_id": run_id}
            )
            assert status["run_id"] == run_id
            assert status["state"] == "accepted"
            assert "result" not in status or status.get("result") is None
        finally:
            await harness.aclose()

    run_async(case())


def test_run_dir_without_ownership_binding_not_exposed(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        run_id = "run-" + "c" * 32
        harness.event_store.create_run(run_id)
        try:
            await expect_code(
                harness,
                caller,
                "run_status",
                "bare-1",
                protocol.UNKNOWN_RUN,
                {"run_id": run_id},
            )
        finally:
            await harness.aclose()

    run_async(case())


# --- status / events / follow -------------------------------------------------


def test_run_status_reads_progress_and_result(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "st-prog-1")
            run_id = reply["run_id"]
            task = harness.handlers.registry.task_for(run_id)
            await asyncio.wait({task})
            progress = {
                "schema_version": 1,
                "state": "running",
                "last_seq": 2,
                "event_count": 2,
                "updated_at": "2026-07-22T00:00:00+00:00",
            }
            (harness.run_dir(run_id) / "progress.json").write_text(
                json.dumps(progress), encoding="utf-8"
            )
            status = await call(
                harness, caller, "run_status", "st-2", {"run_id": run_id}
            )
            assert status["progress"]["state"] == "running"
            assert status["result"]["status"] == "completed"
        finally:
            await harness.aclose()

    run_async(case())


def test_run_events_snapshot_paging(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "ev-1")
            run_id = reply["run_id"]
            seed_events(harness.run_dir(run_id), 5)
            page = await call(
                harness,
                caller,
                "run_events",
                "ev-page-1",
                {"run_id": run_id, "from_seq": 2, "limit": 2},
            )
            assert [event["seq"] for event in page["events"]] == [3, 4]
            assert page["next_from_seq"] == 4
            assert page["exhausted"] is False
            rest = await call(
                harness,
                caller,
                "run_events",
                "ev-page-2",
                {"run_id": run_id, "from_seq": 4, "limit": 10},
            )
            assert [event["seq"] for event in rest["events"]] == [5]
            assert rest["exhausted"] is True
        finally:
            await harness.aclose()

    run_async(case())


def test_follow_slow_consumer_backlog_does_not_affect_run(tmp_path: Path) -> None:
    async def case():
        harness = Harness(
            tmp_path, event_follow_queue_size=2, mode="pending"
        )
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "follow-1")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            seed_events(run_dir, 1)

            stream = await call(
                harness,
                caller,
                "run_events",
                "follow-req",
                {
                    "run_id": run_id,
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": 0.05,
                },
            )
            assert hasattr(stream, "__aiter__")
            assert hasattr(stream, "aclose")

            async def flood():
                for seq in range(2, 20):
                    with open(run_dir / "events.jsonl", "a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {"seq": seq, "type": "message", "text": f"e{seq}"},
                                sort_keys=True,
                            )
                            + "\n"
                        )
                    await asyncio.sleep(0)

            async def consume():
                frames = []
                async for frame in stream:
                    frames.append(frame)
                    # Stall so the bounded queue overflows while the Run writes.
                    await asyncio.sleep(0.2)
                return frames

            consumer = asyncio.create_task(consume())
            await asyncio.sleep(0.01)
            await flood()
            with pytest.raises(protocol.ProtocolError) as err:
                await consumer
            assert err.value.code == protocol.EVENT_BACKLOG_EXCEEDED
            await stream.aclose()
            assert (run_dir / "events.jsonl").exists()
            assert harness.handlers.registry.is_registered(run_id)
            lines = (
                (run_dir / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
            )
            assert len(lines) >= 2
        finally:
            await harness.aclose()

    run_async(case())


# --- run_id / payload close-set (blocker repair) ------------------------------


@pytest.mark.parametrize(
    "run_id",
    [
        "../etc/passwd",
        "/tmp/evil",
        "..",
        "run/../../tmp",
        "run-id-with-\x00null",
        "",
    ],
)
def test_authorize_run_rejects_traversal_before_fs(tmp_path: Path, run_id: str) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        canary = tmp_path / "outside-canary.txt"
        canary.write_text("secret-canary", encoding="utf-8")
        try:
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness, caller, "run_status", "trav-1", {"run_id": run_id}
                )
            assert err.value.code == protocol.INVALID_REQUEST
            assert "passwd" not in err.value.message
            assert "evil" not in err.value.message
            assert "secret-canary" not in err.value.message
            # Outside canary untouched; event-store root has no traversal residue.
            assert canary.read_text(encoding="utf-8") == "secret-canary"
            base = Path(harness.event_store.base_dir)
            if base.exists():
                for path in base.rglob("*"):
                    assert "secret-canary" not in path.name
        finally:
            await harness.aclose()

    run_async(case())


def test_payload_field_sets_closed_without_echo(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        secret = "sk-live-" + "FIELDNAME"
        try:
            cases = [
                ("server_info", {secret: 1}),
                ("session_list", {secret: 1}),
                ("run_status", {"run_id": "run-" + "a" * 32, secret: 1}),
                ("run_cancel", {"run_id": "run-" + "a" * 32, secret: 1}),
                (
                    "run_events",
                    {"run_id": "run-" + "a" * 32, "from_seq": 0, secret: 1},
                ),
                ("session_status", {"session_id": "sess-x", secret: 1}),
                ("session_close", {"session_id": "sess-x", secret: 1}),
            ]
            for op, payload in cases:
                with pytest.raises(protocol.ProtocolError) as err:
                    await call(harness, caller, op, f"pf-{op}", payload)
                assert err.value.code == protocol.INVALID_REQUEST
                assert secret not in err.value.message
        finally:
            await harness.aclose()

    run_async(case())


def test_follow_anext_cancel_cleans_child_wait_tasks(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="pending")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "follow-cancel-1")
            stream = await call(
                harness,
                caller,
                "run_events",
                "fc-1",
                {
                    "run_id": reply["run_id"],
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": 0.05,
                },
            )
            consumer = asyncio.create_task(stream.__anext__())
            for _ in range(50):
                if getattr(stream, "_wait_tasks", None):
                    if any(not task.done() for task in list(stream._wait_tasks)):
                        break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("follow wait tasks never appeared")
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            await stream.aclose()
            assert all(task.done() for task in list(stream._wait_tasks))
            assert len(stream._wait_tasks) == 0
        finally:
            await harness.aclose()

    run_async(case())


def test_disconnect_does_not_close_unrelated_follow_stream(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="pending")
        caller = caller_for(principal_a())
        try:
            first = await harness.submit(
                caller,
                "life-1",
                submit_payload(
                    request=valid_wire_request(ars_session_id="sess-life-1")
                ),
            )
            second = await harness.submit(
                caller,
                "life-2",
                submit_payload(
                    request=valid_wire_request(ars_session_id="sess-life-2")
                ),
            )
            stream_a = await call(
                harness,
                caller,
                "run_events",
                "life-a",
                {
                    "run_id": first["run_id"],
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": 0.05,
                },
            )
            stream_b = await call(
                harness,
                caller,
                "run_events",
                "life-b",
                {
                    "run_id": second["run_id"],
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": 0.05,
                },
            )
            await harness.handlers.disconnect(caller)
            assert getattr(stream_b, "_closed", False) is False
            # stream_b remains usable as a subscription object.
            assert hasattr(stream_b, "__aiter__")
            await stream_a.aclose()
            assert getattr(stream_b, "_closed", False) is False
            await stream_b.aclose()
        finally:
            await harness.aclose()

    run_async(case())


def test_natural_follow_completion_does_not_retain_stream(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "life-done-1")
            run_id = reply["run_id"]
            task = harness.handlers.registry.task_for(run_id)
            if task is not None:
                await asyncio.wait({task})
            stream = await call(
                harness,
                caller,
                "run_events",
                "life-done",
                {
                    "run_id": run_id,
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": 0.05,
                },
            )
            # Drain until natural terminal stop.
            with contextlib.suppress(StopAsyncIteration):
                async for _frame in stream:
                    pass
            await stream.aclose()
            # No strong retention of completed streams.
            retained = list(harness.handlers._follow_streams)
            assert stream not in retained
        finally:
            await harness.aclose()

    run_async(case())


@pytest.mark.parametrize(
    "idle",
    [
        True,
        False,
        "1",
        0,
        -1,
        float("nan"),
        float("inf"),
        float("-inf"),
        3600.1,
        10_000,
        10**10000,
    ],
    ids=[
        "true",
        "false",
        "str_1",
        "zero",
        "neg",
        "nan",
        "inf",
        "ninf",
        "over_float",
        "10000",
        "huge_int_1e10000",
    ],
)
def test_r4_b5_follow_idle_seconds_rejects_non_finite_or_over_max(
    tmp_path: Path, idle
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "idle-bad-1")
            before = set(harness.handlers._follow_streams)
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness,
                    caller,
                    "run_events",
                    "idle-bad",
                    {
                        "run_id": reply["run_id"],
                        "from_seq": 0,
                        "follow": True,
                        "follow_idle_seconds": idle,
                    },
                )
            assert err.value.code == protocol.INVALID_REQUEST
            assert "follow_idle" in err.value.message
            # No stream registry entry / task created on rejection.
            assert set(harness.handlers._follow_streams) == before
        finally:
            await harness.aclose()

    run_async(case())


@pytest.mark.parametrize("idle", [0.05, 1, 3600, handlers.DEFAULT_FOLLOW_IDLE_SECONDS])
def test_r4_b5_follow_idle_seconds_accepts_finite_production_values(
    tmp_path: Path, idle
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        stream = None
        try:
            reply = await harness.submit(caller, "idle-ok-1")
            stream = await call(
                harness,
                caller,
                "run_events",
                "idle-ok",
                {
                    "run_id": reply["run_id"],
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": idle,
                },
            )
            assert stream in harness.handlers._follow_streams
            assert float(stream._idle) == float(idle)
        finally:
            if stream is not None:
                await stream.aclose()
            await harness.aclose()

    run_async(case())


def test_authorize_run_rejects_symlinked_run_dir(tmp_path: Path) -> None:
    async def case():
        from tests.arsd.test_admission import submit_command as _submit_command

        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        run_id = "run-" + "d" * 32
        outside = tmp_path / "outside-auth"
        outside.mkdir()
        digest = admission.compute_request_digest(_submit_command())
        storage.write_once_json(
            outside / "submission.json",
            {
                "schema_version": admission.SUBMISSION_SCHEMA_VERSION,
                "principal_id": "principal-a",
                "request_id": "sym-auth",
                "run_id": run_id,
                "retry_of_run_id": None,
                "api_version": protocol.ARSD_API_VERSION,
                "accepted_at": "2026-07-22T00:00:00+00:00",
                "peer": {"pid": 1, "uid": 1, "gid": 1},
                "owner": "hermes",
                "namespace": "hermes/doc-check",
                "session_reuse": "reuse",
                "ars_session_id": "sess-arsd-1",
                "profile_id": "fake-agent-1.0",
                "request_digest": digest.value,
                "prompt_sha256": digest.prompt_sha256,
                "prompt_bytes": digest.prompt_bytes,
            },
        )
        Path(harness.event_store.base_dir).mkdir(parents=True, exist_ok=True)
        link = Path(harness.event_store.base_dir) / run_id
        link.symlink_to(outside)
        try:
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness, caller, "run_status", "sym-auth", {"run_id": run_id}
                )
            assert err.value.code == protocol.UNKNOWN_RUN
            assert "outside" not in err.value.message
        finally:
            await harness.aclose()

    run_async(case())


@pytest.mark.parametrize(
    "label, body",
    [
        ("malformed_json", "{not-json\n"),
        ("non_object", '"just-a-string"\n'),
        ("bad_seq", json.dumps({"seq": "x", "type": "m"}) + "\n"),
        (
            "non_monotonic",
            json.dumps({"seq": 2, "type": "m"})
            + "\n"
            + json.dumps({"seq": 1, "type": "m"})
            + "\n",
        ),
        (
            "oversized",
            '{"seq":1,"type":"m","text":"'
            + ("x" * (handlers.MAX_EVENT_LINE_BYTES + 8))
            + '"}\n',
        ),
    ],
)
def test_events_page_corrupt_evidence_fails_closed(
    tmp_path: Path, label: str, body: str
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, f"ev-bad-{label}")
            run_dir = harness.run_dir(reply["run_id"])
            path = run_dir / "events.jsonl"
            path.write_text(body, encoding="utf-8")
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness,
                    caller,
                    "run_events",
                    f"ev-{label}",
                    {"run_id": reply["run_id"], "from_seq": 0, "limit": 10},
                )
            assert err.value.code == protocol.INTERNAL
            assert "not-json" not in err.value.message
            assert "x" * 20 not in err.value.message
            # Run remains registered; corrupt read is subscription/snapshot only.
            assert harness.handlers.registry.is_registered(reply["run_id"])
        finally:
            await harness.aclose()

    run_async(case())


# --- cancel -------------------------------------------------------------------


def test_cancel_pre_dispatch_failed_reusable(tmp_path: Path) -> None:
    async def case():
        from tests.arsd.test_admission import SpyEventStore

        root = tmp_path / "svroot"
        session_store = storage.native_session_store(root)
        real = storage.native_event_store(root)
        event_store = SpyEventStore(real.base_dir)
        factory = CancelFactory("pre-dispatch", session_store=session_store)
        seed_session(session_store, session_id="sess-arsd-1")
        h = handlers.ArsdHandlers(
            session_store=session_store,
            event_store=event_store,
            run_task_factory=factory,
            cancel_wait_seconds=5.0,
        )
        factory.handlers = h
        caller = caller_for(principal_a())
        try:
            reply = await h(
                caller,
                protocol.ParsedRequest(
                    op="submit",
                    request_id="cancel-pre-1",
                    payload=submit_payload(),
                ),
            )
            run_id = reply["run_id"]
            await factory.runners[0].started.wait()
            cancel_reply = await h(
                caller,
                parsed("run_cancel", "c1", {"run_id": run_id}),
            )
            assert cancel_reply["run_id"] == run_id
            result = json.loads(
                (Path(event_store.base_dir) / run_id / "result.json").read_text(
                    encoding="utf-8"
                )
            )
            assert result["status"] == "failed"
            assert not h.registry.is_registered(run_id)
            record = session_store.open_session("sess-arsd-1")
            assert record.state == STATE_OPEN  # reusable, not quarantined
        finally:
            await h.aclose()

    run_async(case())


def test_cancel_trustworthy_cancelled_terminal(tmp_path: Path) -> None:
    async def case():
        root = tmp_path / "svroot"
        session_store = storage.native_session_store(root)
        from tests.arsd.test_admission import SpyEventStore

        real = storage.native_event_store(root)
        event_store = SpyEventStore(real.base_dir)
        factory = CancelFactory("cancelled-terminal", session_store=session_store)
        seed_session(session_store, session_id="sess-arsd-1")
        h = handlers.ArsdHandlers(
            session_store=session_store,
            event_store=event_store,
            run_task_factory=factory,
            cancel_wait_seconds=5.0,
        )
        factory.handlers = h
        caller = caller_for(principal_a())
        try:
            reply = await h(
                caller,
                protocol.ParsedRequest(
                    op="submit",
                    request_id="cancel-ok-1",
                    payload=submit_payload(),
                ),
            )
            run_id = reply["run_id"]
            await factory.runners[0].started.wait()
            await h(caller, parsed("run_cancel", "c2", {"run_id": run_id}))
            result = json.loads(
                (Path(event_store.base_dir) / run_id / "result.json").read_text(
                    encoding="utf-8"
                )
            )
            assert result["status"] == "cancelled"
            assert result["stop_reason"] == "cancelled"
            assert not h.registry.is_registered(run_id)
            assert session_store.open_session("sess-arsd-1").state == STATE_OPEN
        finally:
            await h.aclose()

    run_async(case())


def test_cancel_without_terminal_unknown_quarantined(tmp_path: Path) -> None:
    async def case():
        root = tmp_path / "svroot"
        session_store = storage.native_session_store(root)
        from tests.arsd.test_admission import SpyEventStore

        real = storage.native_event_store(root)
        event_store = SpyEventStore(real.base_dir)
        factory = CancelFactory("no-terminal", session_store=session_store)
        seed_session(session_store, session_id="sess-arsd-1")
        h = handlers.ArsdHandlers(
            session_store=session_store,
            event_store=event_store,
            run_task_factory=factory,
            cancel_wait_seconds=5.0,
        )
        factory.handlers = h
        caller = caller_for(principal_a())
        try:
            reply = await h(
                caller,
                protocol.ParsedRequest(
                    op="submit",
                    request_id="cancel-unk-1",
                    payload=submit_payload(),
                ),
            )
            run_id = reply["run_id"]
            await factory.runners[0].started.wait()
            await h(caller, parsed("run_cancel", "c3", {"run_id": run_id}))
            result = json.loads(
                (Path(event_store.base_dir) / run_id / "result.json").read_text(
                    encoding="utf-8"
                )
            )
            assert result["status"] == "unknown"
            assert result["retryable"] is False
            assert not h.registry.is_registered(run_id)
            record = session_store.open_session("sess-arsd-1")
            assert record.state == "quarantined"
        finally:
            await h.aclose()

    run_async(case())


# --- session ops / disconnect / registry finally ------------------------------


def test_session_ops_authorized_filtered_and_quarantine_refused(
    tmp_path: Path,
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller_a = caller_for(principal_a())
        caller_b = caller_for(principal_b())
        seed_session(harness.session_store, session_id="sess-a")
        seed_session(
            harness.session_store,
            session_id="sess-b",
            owner="other",
            namespace="other/ns",
        )
        seed_session(harness.session_store, session_id="sess-q")
        harness.session_store.mark_quarantined(
            "sess-q", reason="test", run_id="run-seed"
        )
        try:
            listed = await call(harness, caller_a, "session_list", "list-1", {})
            ids = {item["session_id"] for item in listed["sessions"]}
            assert "sess-a" in ids
            assert "sess-q" in ids
            assert "sess-b" not in ids
            for item in listed["sessions"]:
                if item["session_id"] == "sess-a":
                    assert item["state"] == "active"  # native API vocabulary
                if item["session_id"] == "sess-q":
                    assert item["state"] == "quarantined"

            status = await call(
                harness,
                caller_a,
                "session_status",
                "ss-1",
                {"session_id": "sess-a"},
            )
            assert status["session_id"] == "sess-a"
            assert status["state"] == "active"

            await expect_code(
                harness,
                caller_b,
                "session_status",
                "ss-deny",
                protocol.OWNER_MISMATCH,
                {"session_id": "sess-a"},
            )
            await expect_code(
                harness,
                caller_a,
                "session_status",
                "ss-unk",
                protocol.UNKNOWN_SESSION,
                {"session_id": "sess-missing"},
            )
            await expect_code(
                harness,
                caller_a,
                "session_close",
                "sc-q",
                protocol.INVALID_REQUEST,
                {"session_id": "sess-q"},
            )
            closed = await call(
                harness,
                caller_a,
                "session_close",
                "sc-ok",
                {"session_id": "sess-a"},
            )
            assert closed["state"] == "closed"
        finally:
            await harness.aclose()

    run_async(case())


def test_disconnect_does_not_cancel_accepted_run(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "disc-1")
            run_id = reply["run_id"]
            # Disconnect tears down only connection-local state; the Run proceeds.
            await harness.handlers.disconnect(caller)
            task = harness.handlers.registry.task_for(run_id)
            assert task is not None
            await asyncio.wait({task})
            status = await call(
                harness, caller, "run_status", "disc-st", {"run_id": run_id}
            )
            assert status["result"]["status"] == "completed"
        finally:
            await harness.aclose()

    run_async(case())


def test_registry_always_deregisters_on_task_exception(tmp_path: Path) -> None:
    async def case():
        class Boom:
            def __init__(self, *, prepared_handle, mode=None):
                self.prepared_handle = prepared_handle

            async def run(self):
                raise RuntimeError("injected task failure")

        class BoomFactory:
            def __init__(self):
                self.handlers = None
                self.calls = []

            def __call__(self, *, command, run_id, prepared_handle, submitted_at):
                self.calls.append(run_id)
                return Boom(prepared_handle=prepared_handle)

        root = tmp_path / "svroot"
        session_store = storage.native_session_store(root)
        from tests.arsd.test_admission import SpyEventStore

        real = storage.native_event_store(root)
        event_store = SpyEventStore(real.base_dir)
        factory = BoomFactory()
        h = handlers.ArsdHandlers(
            session_store=session_store,
            event_store=event_store,
            run_task_factory=factory,
            cancel_wait_seconds=5.0,
        )
        factory.handlers = h
        caller = caller_for(principal_a())
        try:
            reply = await h(
                caller,
                protocol.ParsedRequest(
                    op="submit",
                    request_id="boom-1",
                    payload=submit_payload(
                        request=valid_wire_request(ars_session_id="sess-boom")
                    ),
                ),
            )
            run_id = reply["run_id"]
            task = h.registry.task_for(run_id)
            if task is not None:
                await asyncio.wait({task})
            assert not h.registry.is_registered(run_id)
        finally:
            await h.aclose()

    run_async(case())


def test_server_info_reports_version_and_limits(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, max_concurrent_runs=3)
        caller = caller_for(principal_a())
        try:
            info = await call(harness, caller, "server_info", "info-1", {})
            assert info["api_version"] == protocol.ARSD_API_VERSION
            assert "version" in info
            assert info["limits"]["max_concurrent_runs"] == 3
        finally:
            await harness.aclose()

    run_async(case())


def test_unknown_op_rejected(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            # Bypass protocol.parse_request so the handler closes the set.
            with pytest.raises(protocol.ProtocolError) as err:
                await harness.handlers(
                    caller,
                    protocol.ParsedRequest(op="not_an_op", request_id="x", payload={}),
                )
            assert err.value.code == protocol.UNKNOWN_OP
        finally:
            await harness.aclose()

    run_async(case())


def test_r5_b1_run_status_frame_encodes_truncated_native_result(tmp_path: Path) -> None:
    """A budget-truncated Native result remains queryable under MAX_FRAME_BYTES."""
    from agent_run_supervisor.exit_classifier import AgentRunStatus
    from agent_run_supervisor.result import (
        MAX_FINAL_MESSAGE_BYTES,
        build_result_payload,
        enforce_native_result_ceiling,
    )

    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "enc-trunc")
            run_id = reply["run_id"]
            task = harness.handlers.registry.task_for(run_id)
            await asyncio.wait({task})
            run_dir = harness.run_dir(run_id)
            msg = ("x\x00" * (MAX_FINAL_MESSAGE_BYTES // 2 + 100))
            final = msg.encode("utf-8")[:MAX_FINAL_MESSAGE_BYTES].decode(
                "utf-8", errors="ignore"
            )
            payload = build_result_payload(
                run_id=run_id,
                status=AgentRunStatus.COMPLETED,
                origin="acp",
                detail_code=None,
                retryable=False,
                exit_code=0,
                signal=None,
                stop_reason="end_turn",
                usage=None,
                final_message=final,
                truncated=True,
                truncate_reason="max_final_message_bytes",
                run_dir=run_dir,
                raw_event_path="events.jsonl",
            )
            payload = enforce_native_result_ceiling(
                payload, run_id=run_id, run_dir=run_dir, session_id=None
            )
            (run_dir / "result.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            status = await call(
                harness, caller, "run_status", "enc-1", {"run_id": run_id}
            )
            assert status["result"]["truncated"] is True
            assert status["result"]["truncate_reason"] == "max_final_message_bytes"
            frame = protocol.encode_frame(protocol.build_result("enc-1", status))
            assert len(frame) <= protocol.MAX_FRAME_BYTES
        finally:
            await harness.aclose()

    run_async(case())


def test_r6_b4_near_1mib_stored_event_yields_truncation_marker(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "big-ev-1")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            # Valid configured near-1MiB event on disk.
            big = {
                "seq": 1,
                "type": "agent_message_delta",
                "text": "x" * (1024 * 1024 - 128),
            }
            line = json.dumps(big, sort_keys=True, ensure_ascii=False) + "\n"
            assert len(line.encode("utf-8")) <= handlers.MAX_EVENT_LINE_BYTES
            (run_dir / "events.jsonl").write_text(line, encoding="utf-8")
            page = await call(
                harness,
                caller,
                "run_events",
                "big-page",
                {"run_id": run_id, "from_seq": 0, "limit": 10},
            )
            assert len(page["events"]) == 1
            marker = page["events"][0]
            assert marker["seq"] == 1
            assert marker["type"] == "agent_message_delta"
            assert marker["truncated"] is True
            assert marker["truncate_reason"] == "response_budget"
            assert "text" not in marker
            frame = protocol.encode_frame(
                protocol.build_result("x" * protocol.MAX_REQUEST_ID_CHARS, page)
            )
            assert len(frame) <= protocol.MAX_FRAME_BYTES
            # Raw durable evidence remains on disk.
            raw = (run_dir / "events.jsonl").read_text(encoding="utf-8")
            assert "x" * 1000 in raw
        finally:
            await harness.aclose()

    run_async(case())


def test_r6_b4_many_events_page_early_then_continue_without_loss(
    tmp_path: Path,
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "page-budget-1")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            # Many medium events that individually fit but exceed aggregate budget.
            chunk = "y" * 40_000
            events = []
            with open(run_dir / "events.jsonl", "w", encoding="utf-8") as stream:
                for seq in range(1, 40):
                    event = {"seq": seq, "type": "message", "text": chunk}
                    stream.write(json.dumps(event, sort_keys=True) + "\n")
                    events.append(seq)
            seen: list[int] = []
            cursor = 0
            pages = 0
            while True:
                page = await call(
                    harness,
                    caller,
                    "run_events",
                    f"pb-{pages}",
                    {"run_id": run_id, "from_seq": cursor, "limit": 1000},
                )
                pages += 1
                page_seqs = [event["seq"] for event in page["events"]]
                assert page_seqs
                seen.extend(page_seqs)
                frame = protocol.encode_frame(
                    protocol.build_result("x" * protocol.MAX_REQUEST_ID_CHARS, page)
                )
                assert len(frame) <= protocol.MAX_FRAME_BYTES
                if page["exhausted"]:
                    break
                assert page["next_from_seq"] == page_seqs[-1]
                cursor = page["next_from_seq"]
                assert pages < 50
            assert seen == events
            assert pages > 1
        finally:
            await harness.aclose()

    run_async(case())


def test_r6_b4_overlong_line_rejects_without_unbounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "overlong-1")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            over = b"{" + b'"a":"' + b"z" * (handlers.MAX_EVENT_LINE_BYTES + 10) + b'"}\n'
            (run_dir / "events.jsonl").write_bytes(over)
            reads = {"n": 0, "max": 0}
            real_read = os.read

            def tracking_read(fd, n):
                reads["n"] += 1
                reads["max"] = max(reads["max"], n)
                return real_read(fd, n)

            monkeypatch.setattr(os, "read", tracking_read)
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness,
                    caller,
                    "run_events",
                    "overlong",
                    {"run_id": run_id, "from_seq": 0, "limit": 10},
                )
            assert err.value.code == protocol.INTERNAL
            assert "z" * 50 not in err.value.message
            assert reads["max"] <= handlers.MAX_EVENT_LINE_BYTES + 1
        finally:
            await harness.aclose()

    run_async(case())


def test_r6_b4_symlink_and_non_regular_rejected(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "symlink-1")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            target = run_dir / "real-events.jsonl"
            target.write_text(
                json.dumps({"seq": 1, "type": "message"}) + "\n", encoding="utf-8"
            )
            link = run_dir / "events.jsonl"
            if link.exists():
                link.unlink()
            link.symlink_to(target)
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness,
                    caller,
                    "run_events",
                    "symlink",
                    {"run_id": run_id, "from_seq": 0, "limit": 10},
                )
            assert err.value.code == protocol.INTERNAL
        finally:
            await harness.aclose()

    run_async(case())


def test_r6_b4_corrupt_deep_and_int_json_typed_internal(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "corrupt-1")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            (run_dir / "events.jsonl").write_bytes(
                b'{"seq":1,"n":' + b"1" + b"0" * 10000 + b"}\n"
            )
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness,
                    caller,
                    "run_events",
                    "corrupt-int",
                    {"run_id": run_id, "from_seq": 0, "limit": 10},
                )
            assert err.value.code == protocol.INTERNAL
            assert "10000" not in err.value.message

            depth = 2000
            nested = (
                b'{"seq":2,"a":'
                + b"{" * depth
                + b'"x":1'
                + b"}" * depth
                + b"}\n"
            )
            if len(nested) <= handlers.MAX_EVENT_LINE_BYTES:
                (run_dir / "events.jsonl").write_bytes(nested)
                with pytest.raises(protocol.ProtocolError) as err2:
                    await call(
                        harness,
                        caller,
                        "run_events",
                        "corrupt-deep",
                        {"run_id": run_id, "from_seq": 0, "limit": 10},
                    )
                assert err2.value.code == protocol.INTERNAL
        finally:
            await harness.aclose()

    run_async(case())


def test_r6_b4_follow_frame_encodes_for_budget_marker(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="pending")
        caller = caller_for(principal_a())
        stream = None
        try:
            reply = await harness.submit(caller, "follow-big-1")
            run_id = reply["run_id"]
            big = {
                "seq": 1,
                "type": "agent_message_delta",
                "text": "x" * (1024 * 1024 - 128),
            }
            (harness.run_dir(run_id) / "events.jsonl").write_text(
                json.dumps(big, sort_keys=True) + "\n", encoding="utf-8"
            )
            stream = await call(
                harness,
                caller,
                "run_events",
                "follow-big",
                {
                    "run_id": run_id,
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": 0.05,
                },
            )
            stream.__aiter__()
            item = await asyncio.wait_for(stream.__anext__(), 2.0)
            assert item["events"][0]["truncated"] is True
            frame = protocol.encode_frame(
                protocol.build_result("x" * protocol.MAX_REQUEST_ID_CHARS, dict(item))
            )
            assert len(frame) <= protocol.MAX_FRAME_BYTES
        finally:
            if stream is not None:
                await stream.aclose()
            await harness.aclose()

    run_async(case())


def test_r6_residual_event_json_size_cyclic_is_sanitized_internal() -> None:
    event: dict = {"seq": 1, "type": "message", "text": "secret-payload-xyz"}
    event["self"] = event
    with pytest.raises(protocol.ProtocolError) as err:
        handlers._event_json_size(event)
    assert err.value.code == protocol.INTERNAL
    assert "secret-payload-xyz" not in err.value.message
    assert "Circular" not in err.value.message


def test_r6_residual_event_json_size_deep_is_sanitized_internal() -> None:
    node: dict = {"leaf": "secret-deep-leaf"}
    for _ in range(5000):
        node = {"n": node}
    event = {"seq": 1, "type": "message", "payload": node}
    with pytest.raises(protocol.ProtocolError) as err:
        handlers._event_json_size(event)
    assert err.value.code == protocol.INTERNAL
    assert "secret-deep-leaf" not in err.value.message
    assert "Recursion" not in err.value.message


def test_r6_residual_read_page_mutated_mapping_size_is_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "mut-size-1")
            run_id = reply["run_id"]
            seed_events(harness.run_dir(run_id), 1)

            real_size = handlers._event_json_size

            def boom_size(event):
                # Simulate adversarial/mutated Mapping during framing calc.
                raise RecursionError("injected deep structure")

            monkeypatch.setattr(handlers, "_event_json_size", boom_size)
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness,
                    caller,
                    "run_events",
                    "mut-size",
                    {"run_id": run_id, "from_seq": 0, "limit": 10},
                )
            assert err.value.code == protocol.INTERNAL
            assert "injected" not in err.value.message
            assert "Recursion" not in err.value.message
            del real_size
        finally:
            await harness.aclose()

    run_async(case())


def test_r6_residual_ensure_page_encodes_adversarial_is_internal() -> None:
    cyclic: dict = {"seq": 1, "type": "message"}
    cyclic["self"] = cyclic
    with pytest.raises(protocol.ProtocolError) as err:
        handlers._ensure_events_page_encodes(
            run_id="run-x",
            events=[cyclic],
            next_from_seq=1,
            exhausted=True,
        )
    assert err.value.code == protocol.INTERNAL
    assert "Circular" not in err.value.message


# -- R7 B2 live trusted terminal reader ---------------------------------------


def _full_result(run_dir: Path, run_id: str, *, status="completed"):
    from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
    from agent_run_supervisor.result import build_result_payload

    status_enum = AgentRunStatus(status)
    return build_result_payload(
        run_id=run_id,
        status=status_enum,
        origin="acp" if status == "completed" else "supervisor",
        detail_code=None if status == "completed" else "X",
        retryable=_RETRYABLE_DEFAULT[status_enum],
        exit_code=0 if status == "completed" else None,
        signal=None,
        stop_reason="end_turn" if status == "completed" else None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=run_dir,
        raw_event_path="events.jsonl",
    )


def test_r7_b2_run_status_rejects_symlink_oversize_malformed_compat(
    tmp_path: Path,
) -> None:
    async def case():
        harness = Harness(tmp_path, mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "r7-bad-1")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            # Symlink result
            target = run_dir / "elsewhere.json"
            target.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
            (run_dir / "result.json").symlink_to(target)
            await expect_code(
                harness,
                caller,
                "run_status",
                "r7-sym",
                protocol.INTERNAL,
                {"run_id": run_id},
            )
            assert harness.session_store.open_session("sess-arsd-1").state == "quarantined"
        finally:
            await harness.aclose()

        harness = Harness(tmp_path / "h2", mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "r7-bad-2")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            (run_dir / "result.json").write_bytes(b"{not-json")
            await expect_code(
                harness,
                caller,
                "run_status",
                "r7-mal",
                protocol.INTERNAL,
                {"run_id": run_id},
            )
            assert harness.session_store.open_session("sess-arsd-1").state == "quarantined"
        finally:
            await harness.aclose()

        harness = Harness(tmp_path / "h3", mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "r7-bad-3")
            run_id = reply["run_id"]
            run_dir = harness.run_dir(run_id)
            # Minimal compat-only shape must not be trusted as Native terminal.
            (run_dir / "result.json").write_text(
                json.dumps(
                    {"run_id": run_id, "status": "completed", "retryable": False}
                ),
                encoding="utf-8",
            )
            await expect_code(
                harness,
                caller,
                "run_status",
                "r7-compat",
                protocol.INTERNAL,
                {"run_id": run_id},
            )
            assert harness.session_store.open_session("sess-arsd-1").state == "quarantined"
        finally:
            await harness.aclose()

        harness = Harness(tmp_path / "h4", mode="complete")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "r7-ok")
            run_id = reply["run_id"]
            task = harness.handlers.registry.task_for(run_id)
            await asyncio.wait({task})
            status = await call(
                harness, caller, "run_status", "r7-ok-st", {"run_id": run_id}
            )
            assert status["result"]["status"] == "completed"
            assert "error_code" in status["result"]
        finally:
            await harness.aclose()

    run_async(case())


def test_r7_b2_cancel_invalid_terminal_quarantines_registered(
    tmp_path: Path,
) -> None:
    async def case():
        harness = Harness(tmp_path, mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "r7-can-1")
            run_id = reply["run_id"]
            assert harness.handlers.registry.is_registered(run_id)
            # Ensure the guarded task has started before cancel (otherwise
            # Task.cancel() can complete before ``_run_guarded`` runs).
            await asyncio.sleep(0.05)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"[]")
            await expect_code(
                harness,
                caller,
                "run_cancel",
                "r7-can",
                protocol.INTERNAL,
                {"run_id": run_id},
            )
            for _ in range(100):
                if not harness.handlers.registry.is_registered(run_id):
                    break
                await asyncio.sleep(0.01)
            assert not harness.handlers.registry.is_registered(run_id)
            assert harness.session_store.open_session("sess-arsd-1").state == "quarantined"
        finally:
            await harness.aclose()

    run_async(case())


def test_r7_b2_quarantine_failure_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.session import SessionStore

    async def case():
        harness = Harness(tmp_path, mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())

        def boom(self, session_id, **kwargs):
            raise OSError("injected quarantine failure")

        monkeypatch.setattr(SessionStore, "mark_quarantined", boom)
        try:
            reply = await harness.submit(caller, "r7-qf-1")
            run_id = reply["run_id"]
            (harness.run_dir(run_id) / "result.json").write_bytes(b"{")
            await expect_code(
                harness,
                caller,
                "run_status",
                "r7-qf",
                protocol.INTERNAL,
                {"run_id": run_id},
            )
        finally:
            await harness.aclose()

    run_async(case())


# -- R8 B2 active invalid terminal on idempotent submit -----------------------


def test_r8_b2_submit_registered_invalid_cancels_then_quarantines(
    tmp_path: Path,
) -> None:
    async def case():
        harness = Harness(tmp_path, mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        cancel_order: list[str] = []
        real_cancel = harness.handlers.registry.cancel

        async def tracking_cancel(run_id, wait_seconds=None):
            cancel_order.append(f"cancel:{run_id}")
            return await real_cancel(run_id, wait_seconds=wait_seconds)

        harness.handlers.registry.cancel = tracking_cancel  # type: ignore[method-assign]
        try:
            first = await harness.submit(caller, "r8-inv-1")
            run_id = first["run_id"]
            assert harness.handlers.registry.is_registered(run_id)
            await asyncio.sleep(0.05)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"[]")
            err = await expect_code(
                harness,
                caller,
                "submit",
                "r8-inv-1",
                protocol.INTERNAL,
                submit_payload(),
            )
            assert "untrusted" in err.message.lower() or "corrupt" in err.message.lower()
            assert cancel_order and cancel_order[0].startswith("cancel:")
            for _ in range(100):
                if not harness.handlers.registry.is_registered(run_id):
                    break
                await asyncio.sleep(0.01)
            assert not harness.handlers.registry.is_registered(run_id)
            assert (
                harness.session_store.open_session("sess-arsd-1").state == "quarantined"
            )
        finally:
            await harness.aclose()

    run_async(case())


def test_r8_b2_submit_invalid_cancel_failure_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case():
        harness = Harness(tmp_path, mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())

        async def boom_cancel(run_id, wait_seconds=None):
            raise OSError("injected cancel failure")

        try:
            first = await harness.submit(caller, "r8-cf-1")
            run_id = first["run_id"]
            await asyncio.sleep(0.05)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"{not-json")
            monkeypatch.setattr(harness.handlers.registry, "cancel", boom_cancel)
            await expect_code(
                harness,
                caller,
                "submit",
                "r8-cf-1",
                protocol.INTERNAL,
                submit_payload(),
            )
            assert (
                harness.session_store.open_session("sess-arsd-1").state == "quarantined"
            )
        finally:
            await harness.aclose()

    run_async(case())


def test_r8_b2_submit_invalid_quarantine_failure_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.session import SessionStore

    async def case():
        harness = Harness(tmp_path, mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())

        def boom(self, session_id, **kwargs):
            raise OSError("injected quarantine failure")

        monkeypatch.setattr(SessionStore, "mark_quarantined", boom)
        try:
            first = await harness.submit(caller, "r8-qf-1")
            run_id = first["run_id"]
            await asyncio.sleep(0.05)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"{")
            await expect_code(
                harness,
                caller,
                "submit",
                "r8-qf-1",
                protocol.INTERNAL,
                submit_payload(),
            )
        finally:
            await harness.aclose()

    run_async(case())


def test_r8_b2_trusted_and_absent_registered_idempotency_preserved(
    tmp_path: Path,
) -> None:
    async def case():
        # ABSENT + registered → accepted (same accepted_at).
        harness = Harness(tmp_path, mode="pending")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        try:
            first = await harness.submit(caller, "r8-abs-1")
            again = await harness.submit(caller, "r8-abs-1")
            assert again["run_id"] == first["run_id"]
            assert again["accepted_at"] == first["accepted_at"]
            assert harness.handlers.registry.is_registered(first["run_id"])
            assert not (harness.run_dir(first["run_id"]) / "result.json").exists()
        finally:
            await harness.aclose()

        # TRUSTED terminal → accepted without re-dispatch.
        harness = Harness(tmp_path / "trusted", mode="complete")
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        try:
            first = await harness.submit(caller, "r8-tr-1")
            task = harness.handlers.registry.task_for(first["run_id"])
            await asyncio.wait({task})
            assert not harness.handlers.registry.is_registered(first["run_id"])
            again = await harness.submit(caller, "r8-tr-1")
            assert again["run_id"] == first["run_id"]
            assert again["accepted_at"] == first["accepted_at"]
            assert len(harness.factory.calls) == 1
            terminal = admission.inspect_terminal_result(
                harness.run_dir(first["run_id"]), run_id=first["run_id"]
            )
            assert terminal.kind is storage.NativeTerminalKind.TRUSTED
        finally:
            await harness.aclose()

    run_async(case())


# -- R9 B1: cancel timeout must not fake containment / refuse while live ------


def test_r9_b1_cancel_timeout_reports_uncontained(tmp_path: Path) -> None:
    """RunRegistry.cancel must not report success when the task outlives wait."""

    async def case():
        registry = handlers.RunRegistry(max_concurrent_runs=1)
        token = await registry.reserve(session_id=None)
        release = asyncio.Event()
        started = asyncio.Event()

        async def sticky():
            started.set()
            while not release.is_set():
                try:
                    await asyncio.wait_for(release.wait(), 0.01)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    # Absorb cancel (3.11+ cancel-count) and stay registered.
                    task = asyncio.current_task()
                    if task is not None:
                        task.uncancel()
                    continue

        await registry.register(
            "run-r9-cancel",
            sticky(),
            reservation=token,
            principal_id="p",
            owner="hermes",
            namespace="hermes/doc-check",
            session_id=None,
            submitted_at="2026-07-22T00:00:00+00:00",
        )
        await started.wait()
        assert registry.is_registered("run-r9-cancel")
        ok = await registry.cancel("run-r9-cancel", wait_seconds=0.05)
        assert ok is False
        assert registry.is_registered("run-r9-cancel")
        release.set()
        task = registry.task_for("run-r9-cancel")
        assert task is not None
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(task, 1.0)

    run_async(case())


def test_r9_b1_wait_until_unregistered_awaits_task_lifecycle(
    tmp_path: Path,
) -> None:
    """Registry wait primitive uses the existing task; no busy poll / workers."""

    async def case():
        registry = handlers.RunRegistry(max_concurrent_runs=1)
        token = await registry.reserve(session_id=None)
        release = asyncio.Event()
        started = asyncio.Event()

        async def worker():
            started.set()
            await release.wait()

        await registry.register(
            "run-r9-wait",
            worker(),
            reservation=token,
            principal_id="p",
            owner="hermes",
            namespace="hermes/doc-check",
            session_id=None,
            submitted_at="2026-07-22T00:00:00+00:00",
        )
        await started.wait()
        waiter = asyncio.create_task(registry.wait_until_unregistered("run-r9-wait"))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        assert registry.is_registered("run-r9-wait")
        release.set()
        await asyncio.wait_for(waiter, 1.0)
        assert not registry.is_registered("run-r9-wait")

    run_async(case())


def test_r9_b1_wait_until_unregistered_propagates_waiter_cancellation(
    tmp_path: Path,
) -> None:
    async def case():
        registry = handlers.RunRegistry(max_concurrent_runs=1)
        token = await registry.reserve(session_id=None)
        started = asyncio.Event()

        async def worker():
            started.set()
            await asyncio.Event().wait()

        await registry.register(
            "run-r9-wcancel",
            worker(),
            reservation=token,
            principal_id="p",
            owner="hermes",
            namespace="hermes/doc-check",
            session_id=None,
            submitted_at="2026-07-22T00:00:00+00:00",
        )
        await started.wait()
        waiter = asyncio.create_task(
            registry.wait_until_unregistered("run-r9-wcancel")
        )
        await asyncio.sleep(0.02)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert registry.is_registered("run-r9-wcancel")
        task = registry.task_for("run-r9-wcancel")
        assert task is not None
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(task, 1.0)

    run_async(case())


def test_r9_b1_cancel_propagates_waiter_cancellation(tmp_path: Path) -> None:
    """Caller CancelledError must propagate from RunRegistry.cancel, not become False."""

    async def case():
        registry = handlers.RunRegistry(max_concurrent_runs=1)
        token = await registry.reserve(session_id=None)
        release = asyncio.Event()
        started = asyncio.Event()

        async def sticky():
            started.set()
            while not release.is_set():
                try:
                    await asyncio.wait_for(release.wait(), 0.01)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if task is not None:
                        task.uncancel()
                    continue

        await registry.register(
            "run-r9-ccancel",
            sticky(),
            reservation=token,
            principal_id="p",
            owner="hermes",
            namespace="hermes/doc-check",
            session_id=None,
            submitted_at="2026-07-22T00:00:00+00:00",
        )
        await started.wait()
        assert registry.is_registered("run-r9-ccancel")
        waiter = asyncio.create_task(
            registry.cancel("run-r9-ccancel", wait_seconds=30.0)
        )
        try:
            # Let cancel() enter the shielded wait_for before cancelling the waiter.
            await asyncio.sleep(0.05)
            assert not waiter.done()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert registry.is_registered("run-r9-ccancel")
        finally:
            release.set()
            if not waiter.done():
                waiter.cancel()
                with contextlib.suppress(BaseException):
                    await waiter
            task = registry.task_for("run-r9-ccancel")
            if task is not None:
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(task, 1.0)

    run_async(case())


def test_r9_b1_invalid_terminal_waits_for_unregistration_before_refusal(
    tmp_path: Path,
) -> None:
    """No caller-visible ProtocolError while the registry entry is still live."""

    async def case():
        harness = Harness(tmp_path, mode="pending", cancel_wait_seconds=0.05)
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())

        async def timeout_cancel(run_id, *, wait_seconds):
            # Bounded cancel attempt expires; task remains registered/live.
            del run_id
            await asyncio.sleep(min(float(wait_seconds), 0.05))
            return False

        try:
            reply = await harness.submit(caller, "r9-live-1")
            run_id = reply["run_id"]
            await asyncio.sleep(0.05)
            assert harness.handlers.registry.is_registered(run_id)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"[]")
            harness.handlers.registry.cancel = timeout_cancel  # type: ignore[method-assign]

            status_task = asyncio.create_task(
                call(harness, caller, "run_status", "r9-live", {"run_id": run_id})
            )
            # Past the bounded cancel attempt: handler must still be pending.
            await asyncio.sleep(0.2)
            assert not status_task.done(), (
                "containment must not return/raise while the Run is registered"
            )
            assert harness.handlers.registry.is_registered(run_id)
            assert (
                harness.session_store.open_session("sess-arsd-1").state == "quarantined"
            )

            reg_task = harness.handlers.registry.task_for(run_id)
            assert reg_task is not None
            reg_task.cancel()
            with pytest.raises(protocol.ProtocolError) as err:
                await asyncio.wait_for(status_task, 2.0)
            assert err.value.code == protocol.INTERNAL
            assert "untrusted" in err.value.message.lower() or (
                "corrupt" in err.value.message.lower()
            )
            assert not harness.handlers.registry.is_registered(run_id)
        finally:
            if "status_task" in locals() and not status_task.done():
                status_task.cancel()
                with contextlib.suppress(BaseException):
                    await status_task
            await harness.aclose()

    run_async(case())


def test_r10_b1_follow_invalid_waits_for_unregistration_before_refusal(
    tmp_path: Path,
) -> None:
    """Follow INVALID must contain-before-refuse; iterator stays pending while live."""

    async def case():
        harness = Harness(tmp_path, mode="pending", cancel_wait_seconds=0.05)
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())
        contain_calls: list[str] = []
        real_contain = harness.handlers._contain_invalid_terminal

        async def tracking_contain(*, run_id, run_dir, submission):
            contain_calls.append(run_id)
            return await real_contain(
                run_id=run_id, run_dir=run_dir, submission=submission
            )

        async def timeout_cancel(run_id, *, wait_seconds):
            del run_id
            await asyncio.sleep(min(float(wait_seconds), 0.05))
            return False

        stream = None
        consumer = None
        try:
            reply = await harness.submit(caller, "r10-follow-1")
            run_id = reply["run_id"]
            await asyncio.sleep(0.05)
            assert harness.handlers.registry.is_registered(run_id)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"[]")
            harness.handlers._contain_invalid_terminal = tracking_contain  # type: ignore[method-assign]
            harness.handlers.registry.cancel = timeout_cancel  # type: ignore[method-assign]

            stream = await call(
                harness,
                caller,
                "run_events",
                "r10-follow",
                {
                    "run_id": run_id,
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": handlers.DEFAULT_FOLLOW_IDLE_SECONDS,
                },
            )

            async def consume():
                async for _frame in stream:
                    pass

            consumer = asyncio.create_task(consume())
            # Past the bounded cancel attempt: follow must still be pending.
            await asyncio.sleep(0.2)
            assert not consumer.done(), (
                "follow must not refuse while the Run remains registered"
            )
            assert harness.handlers.registry.is_registered(run_id)
            assert contain_calls == [run_id]
            assert (
                harness.session_store.open_session("sess-arsd-1").state == "quarantined"
            )

            reg_task = harness.handlers.registry.task_for(run_id)
            assert reg_task is not None
            reg_task.cancel()
            with pytest.raises(protocol.ProtocolError) as err:
                await asyncio.wait_for(consumer, 2.0)
            assert err.value.code == protocol.INTERNAL
            assert "untrusted" in err.value.message.lower() or (
                "corrupt" in err.value.message.lower()
            )
            assert not harness.handlers.registry.is_registered(run_id)
        finally:
            if consumer is not None and not consumer.done():
                consumer.cancel()
                with contextlib.suppress(BaseException):
                    await consumer
            if stream is not None:
                await stream.aclose()
            await harness.aclose()

    run_async(case())


def test_r10_b1_follow_aclose_does_not_cancel_live_run(tmp_path: Path) -> None:
    """Ordinary follow close must never cancel the registered Run."""

    async def case():
        harness = Harness(tmp_path, mode="pending")
        caller = caller_for(principal_a())
        stream = None
        try:
            reply = await harness.submit(caller, "r10-close-1")
            run_id = reply["run_id"]
            await asyncio.sleep(0.05)
            assert harness.handlers.registry.is_registered(run_id)
            stream = await call(
                harness,
                caller,
                "run_events",
                "r10-close",
                {
                    "run_id": run_id,
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": handlers.DEFAULT_FOLLOW_IDLE_SECONDS,
                },
            )
            await stream.aclose()
            assert harness.handlers.registry.is_registered(run_id)
            task = harness.handlers.registry.task_for(run_id)
            assert task is not None
            assert not task.done()
        finally:
            if stream is not None:
                await stream.aclose()
            await harness.aclose()

    run_async(case())


def test_r10b_b1a_aclose_awaits_active_containment_without_cancelling(
    tmp_path: Path,
) -> None:
    """Once INVALID containment started, aclose must await it — never cancel it."""

    async def case():
        harness = Harness(tmp_path, mode="pending", cancel_wait_seconds=0.05)
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())

        async def timeout_cancel(run_id, *, wait_seconds):
            del run_id
            await asyncio.sleep(min(float(wait_seconds), 0.05))
            return False

        stream = None
        close_task = None
        try:
            reply = await harness.submit(caller, "r10b-b1a-1")
            run_id = reply["run_id"]
            await asyncio.sleep(0.05)
            assert harness.handlers.registry.is_registered(run_id)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"[]")
            harness.handlers.registry.cancel = timeout_cancel  # type: ignore[method-assign]

            stream = await call(
                harness,
                caller,
                "run_events",
                "r10b-b1a",
                {
                    "run_id": run_id,
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": handlers.DEFAULT_FOLLOW_IDLE_SECONDS,
                },
            )

            # Drive the watcher into INVALID containment (bounded cancel timeout).
            async def poke():
                with contextlib.suppress(protocol.ProtocolError, StopAsyncIteration):
                    async for _frame in stream:
                        pass

            poke_task = asyncio.create_task(poke())
            await asyncio.sleep(0.2)
            assert harness.handlers.registry.is_registered(run_id)
            assert (
                harness.session_store.open_session("sess-arsd-1").state == "quarantined"
            )
            # Containment is in the post-timeout wait-for-unregistration phase.
            assert getattr(stream, "_containment_active", False) is True

            close_task = asyncio.create_task(stream.aclose())
            await asyncio.sleep(0.1)
            assert not close_task.done(), (
                "aclose must remain pending while containment awaits unregistration"
            )
            assert harness.handlers.registry.is_registered(run_id)
            watcher = getattr(stream, "_watcher", None)
            assert watcher is not None
            assert not watcher.cancelled()
            assert not watcher.done()

            reg_task = harness.handlers.registry.task_for(run_id)
            assert reg_task is not None
            reg_task.cancel()
            await asyncio.wait_for(close_task, 2.0)
            assert not harness.handlers.registry.is_registered(run_id)
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(poke_task, 1.0)
        finally:
            if close_task is not None and not close_task.done():
                close_task.cancel()
                with contextlib.suppress(BaseException):
                    await close_task
            if stream is not None:
                with contextlib.suppress(BaseException):
                    await stream.aclose()
            await harness.aclose()

    run_async(case())


def test_r10c_b1a_cancel_aclose_during_containment_keeps_containment_alive(
    tmp_path: Path,
) -> None:
    """Cancelling aclose must not cancel active INVALID containment.

    Close-caller cancellation is remembered; containment/unregistration continue;
    aclose finishes bookkeeping then propagates CancelledError only after the
    Run has unregistered. Ordinary pre-containment close remains covered by
    test_r10_b1_follow_aclose_does_not_cancel_live_run.
    """

    async def case():
        harness = Harness(tmp_path, mode="pending", cancel_wait_seconds=0.05)
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())

        async def timeout_cancel(run_id, *, wait_seconds):
            del run_id
            await asyncio.sleep(min(float(wait_seconds), 0.05))
            return False

        stream = None
        close_task = None
        try:
            reply = await harness.submit(caller, "r10c-b1a-1")
            run_id = reply["run_id"]
            await asyncio.sleep(0.05)
            assert harness.handlers.registry.is_registered(run_id)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"[]")
            harness.handlers.registry.cancel = timeout_cancel  # type: ignore[method-assign]

            stream = await call(
                harness,
                caller,
                "run_events",
                "r10c-b1a",
                {
                    "run_id": run_id,
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": handlers.DEFAULT_FOLLOW_IDLE_SECONDS,
                },
            )

            async def poke():
                with contextlib.suppress(protocol.ProtocolError, StopAsyncIteration):
                    async for _frame in stream:
                        pass

            poke_task = asyncio.create_task(poke())
            await asyncio.sleep(0.2)
            assert harness.handlers.registry.is_registered(run_id)
            assert (
                harness.session_store.open_session("sess-arsd-1").state == "quarantined"
            )
            assert getattr(stream, "_containment_active", False) is True

            close_task = asyncio.create_task(stream.aclose())
            await asyncio.sleep(0.1)
            assert not close_task.done()
            watcher = getattr(stream, "_watcher", None)
            assert watcher is not None
            assert not watcher.done()
            assert not watcher.cancelled()

            close_task.cancel()
            await asyncio.sleep(0.1)
            assert not close_task.done(), (
                "cancelled aclose must stay pending until containment finishes"
            )
            assert harness.handlers.registry.is_registered(run_id)
            watcher = getattr(stream, "_watcher", None)
            assert watcher is not None
            assert not watcher.done(), "containment watcher must remain pending"
            assert not watcher.cancelled(), (
                "close-caller cancel must not cancel active containment"
            )
            assert (
                harness.session_store.open_session("sess-arsd-1").state == "quarantined"
            )

            reg_task = harness.handlers.registry.task_for(run_id)
            assert reg_task is not None
            reg_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(close_task, 2.0)
            assert not harness.handlers.registry.is_registered(run_id)
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(poke_task, 1.0)
        finally:
            if close_task is not None and not close_task.done():
                close_task.cancel()
                with contextlib.suppress(BaseException):
                    await close_task
            if stream is not None:
                with contextlib.suppress(BaseException):
                    await stream.aclose()
            await harness.aclose()

    run_async(case())


def test_r10d_b1a_cancel_aclose_when_watcher_already_done_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller cancel + containment watcher already done must absorb then propagate.

    Same-turn race: shielded watcher completion must not be classified as
    target-only cancellation when the aclose caller is cancelling().
    """
    real_shield = handlers.asyncio.shield
    real_event_set = asyncio.Event.set
    race = {
        "armed": True,
        "fired": False,
        "cancelling_at_stop": None,
        "close_task": None,
    }

    def racing_shield(aw):
        async def _race():
            fut = aw if isinstance(aw, asyncio.Future) else asyncio.ensure_future(aw)
            if not race["armed"]:
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
            race["armed"] = False
            race["fired"] = True
            me = asyncio.current_task()
            assert me is not None
            me.cancel()
            assert me.cancelling() > 0
            raise asyncio.CancelledError()

        return _race()

    def tracking_event_set(self):
        close_task = race["close_task"]
        me = asyncio.current_task()
        if (
            race["fired"]
            and close_task is not None
            and me is close_task
            and race["cancelling_at_stop"] is None
        ):
            race["cancelling_at_stop"] = me.cancelling()
        return real_event_set(self)

    monkeypatch.setattr(handlers.asyncio, "shield", racing_shield)
    monkeypatch.setattr(asyncio.Event, "set", tracking_event_set)

    async def case():
        harness = Harness(tmp_path, mode="pending", cancel_wait_seconds=0.05)
        seed_session(harness.session_store, session_id="sess-arsd-1")
        caller = caller_for(principal_a())

        async def timeout_cancel(run_id, *, wait_seconds):
            del run_id
            await asyncio.sleep(min(float(wait_seconds), 0.05))
            return False

        stream = None
        close_task = None
        try:
            reply = await harness.submit(caller, "r10d-b1a-1")
            run_id = reply["run_id"]
            await asyncio.sleep(0.05)
            assert harness.handlers.registry.is_registered(run_id)
            (harness.run_dir(run_id) / "result.json").write_bytes(b"[]")
            harness.handlers.registry.cancel = timeout_cancel  # type: ignore[method-assign]

            stream = await call(
                harness,
                caller,
                "run_events",
                "r10d-b1a",
                {
                    "run_id": run_id,
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": handlers.DEFAULT_FOLLOW_IDLE_SECONDS,
                },
            )

            async def poke():
                with contextlib.suppress(protocol.ProtocolError, StopAsyncIteration):
                    async for _frame in stream:
                        pass

            poke_task = asyncio.create_task(poke())
            await asyncio.sleep(0.2)
            assert getattr(stream, "_containment_active", False) is True
            assert harness.handlers.registry.is_registered(run_id)

            close_task = asyncio.create_task(stream.aclose())
            race["close_task"] = close_task
            await asyncio.sleep(0.05)
            assert not close_task.done()

            reg_task = harness.handlers.registry.task_for(run_id)
            assert reg_task is not None
            reg_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(close_task, 2.0)
            assert race["fired"] is True
            assert race["cancelling_at_stop"] == 0, (
                "caller cancel must be absorbed before aclose bookkeeping continues"
            )
            assert not harness.handlers.registry.is_registered(run_id)
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(poke_task, 1.0)
        finally:
            if close_task is not None and not close_task.done():
                close_task.cancel()
                with contextlib.suppress(BaseException):
                    await close_task
            if stream is not None:
                with contextlib.suppress(BaseException):
                    await stream.aclose()
            await harness.aclose()

    run_async(case())


@pytest.mark.parametrize(
    "idle",
    [
        1e-9,
        1e-323,
        0.049,
        0.049999,
    ],
    ids=["1e-9", "subnormalish", "just_below_min", "below_min_float"],
)
def test_r10_b4_follow_idle_seconds_rejects_below_minimum(
    tmp_path: Path, idle
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "idle-min-bad-1")
            before = set(harness.handlers._follow_streams)
            with pytest.raises(protocol.ProtocolError) as err:
                await call(
                    harness,
                    caller,
                    "run_events",
                    "idle-min-bad",
                    {
                        "run_id": reply["run_id"],
                        "from_seq": 0,
                        "follow": True,
                        "follow_idle_seconds": idle,
                    },
                )
            assert err.value.code == protocol.INVALID_REQUEST
            message = err.value.message
            assert "follow_idle" in message
            assert f"{handlers.MIN_FOLLOW_IDLE_SECONDS:g}" in message
            assert f"{int(handlers.MAX_FOLLOW_IDLE_SECONDS)}" in message
            assert set(harness.handlers._follow_streams) == before
        finally:
            await harness.aclose()

    run_async(case())


def test_r10_b4_follow_idle_seconds_accepts_minimum_boundary(
    tmp_path: Path,
) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        stream = None
        try:
            reply = await harness.submit(caller, "idle-min-ok-1")
            stream = await call(
                harness,
                caller,
                "run_events",
                "idle-min-ok",
                {
                    "run_id": reply["run_id"],
                    "from_seq": 0,
                    "follow": True,
                    "follow_idle_seconds": handlers.MIN_FOLLOW_IDLE_SECONDS,
                },
            )
            assert float(stream._idle) == float(handlers.MIN_FOLLOW_IDLE_SECONDS)
        finally:
            if stream is not None:
                await stream.aclose()
            await harness.aclose()

    run_async(case())

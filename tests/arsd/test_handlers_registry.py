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
            if self.mode == "pre-dispatch":
                storage.write_once_json(
                    run_dir / "result.json",
                    {
                        "run_id": run_id,
                        "status": "failed",
                        "retryable": False,
                        "stop_reason": None,
                        "origin": "supervisor",
                        "detail_code": "CANCELLED_PRE_DISPATCH",
                    },
                )
            elif self.mode == "cancelled-terminal":
                storage.write_once_json(
                    run_dir / "result.json",
                    {
                        "run_id": run_id,
                        "status": "cancelled",
                        "retryable": False,
                        "stop_reason": "cancelled",
                        "origin": "acp",
                        "detail_code": None,
                    },
                )
            elif self.mode == "no-terminal":
                storage.write_once_json(
                    run_dir / "result.json",
                    {
                        "run_id": run_id,
                        "status": "unknown",
                        "retryable": False,
                        "stop_reason": None,
                        "origin": "supervisor",
                        "detail_code": "CANCEL_WITHOUT_TERMINAL",
                    },
                )
                if self.session_store is not None and self.session_id is not None:
                    self.session_store.mark_quarantined(
                        self.session_id,
                        reason="cancel_without_trustworthy_terminal",
                        run_id=run_id,
                    )
            else:
                raise RuntimeError(f"unknown cancel fake mode: {self.mode}")
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
        ("oversized", '{"seq":1,"type":"m","text":"' + ("x" * 70000) + '"}\n'),
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

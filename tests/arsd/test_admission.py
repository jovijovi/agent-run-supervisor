"""Slice 3c — durable idempotent admission (plan §5/§6, tests §15 slice 3c).

Everything here drives the sanctioned seams only: `admission` unit contracts
(derivation, digest, submission artifact) and the full submit handshake at
the `ArsdHandlers` handler seam with injected deterministic fakes for
fault/concurrency shaping. Fakes are test-only and never acceptance
evidence. No socket, no real AGENT, no acpx.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import admission, handlers, protocol, server
from agent_run_supervisor.event_store import (
    _RUN_ID_RE,
    EventStore,
    EventStoreError,
)
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.spec import DEFAULT_EVENT_BUDGET_POLICY
from agent_run_supervisor.session import QUARANTINE_DISPATCH_OBSERVATION_LOST

from tests.native_acp import registry_fixtures as rfx

SECRET_SENTINEL = "sk-live-" + "LEAKCANARY"  # concatenated; no scannable literal


def run_async(coro):
    return asyncio.run(asyncio.wait_for(coro, 30))


# --- principals and callers -------------------------------------------------


def principal_a() -> server.Principal:
    return server.Principal(
        principal_id="principal-a",
        owner_namespaces=frozenset({("hermes", "hermes/doc-check")}),
    )


def principal_b() -> server.Principal:
    return server.Principal(
        principal_id="principal-b",
        owner_namespaces=frozenset({("other", "other/ns")}),
    )


def caller_for(principal: server.Principal) -> server.AuthenticatedCaller:
    return server.AuthenticatedCaller(
        principal=principal,
        peer_credentials=server.PeerCredentials(pid=4242, uid=1000, gid=1000),
    )


# --- wire payload helpers ---------------------------------------------------


def valid_wire_request(**overrides) -> dict:
    request = {
        "owner": "hermes",
        "namespace": "hermes/doc-check",
        "agent_id": "fake-agent",
        "session_id": "sess-arsd-1",
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
        # A create *omits* the field. Emitting an explicit null would be a
        # different caller statement, and the wire refuses it — so a helper that
        # emitted one would be testing a frame no caller should ever send.
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


def parsed_submit(request_id: str, payload: dict | None = None) -> protocol.ParsedRequest:
    return protocol.ParsedRequest(
        op="submit", request_id=request_id, payload=payload or submit_payload()
    )


# --- deterministic fakes (test-only; never product runtime) ------------------


class SpyEventStore(EventStore):
    """Real EventStore plus call counting and injectable create faults."""

    def __init__(self, base_dir: Path) -> None:
        super().__init__(base_dir)
        self.create_calls: list[str] = []
        self.fail_mode: str | None = None  # None | "error" | "after-create"

    def create_run(self, run_id: str):
        self.create_calls.append(run_id)
        if self.fail_mode == "error":
            raise EventStoreError("injected create_run failure")
        handle = super().create_run(run_id)
        if self.fail_mode == "after-create":
            raise RuntimeError("injected crash after create_run")
        return handle


class FakeRunner:
    """Deterministic stand-in shaping RunTask-visible behavior only."""

    def __init__(self, *, prepared_handle, mode: str) -> None:
        self.prepared_handle = prepared_handle
        self.mode = mode
        self.cancelled = False

    async def run(self):
        if self.mode == "complete":
            from agent_run_supervisor.exit_classifier import AgentRunStatus
            from agent_run_supervisor.result import build_result_payload

            run_id = self.prepared_handle.run_id
            run_dir = self.prepared_handle.run_dir
            storage.write_once_json(
                run_dir / "result.json",
                build_result_payload(
                    run_id=run_id,
                    status=AgentRunStatus.COMPLETED,
                    origin="acp",
                    detail_code=None,
                    retryable=False,
                    signal=None,
                    stop_reason="end_turn",
                    usage=None,
                    final_message="",
                    truncated=False,
                    truncate_reason=None,
                    run_dir=run_dir,
                    raw_event_path="events.jsonl",
                ),
            )
            return None
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return None


class SpyFactory:
    """Injected run-task factory recording handshake-ordering observations."""

    def __init__(self, mode: str = "pending") -> None:
        self.mode = mode
        self.handlers: handlers.ArsdHandlers | None = None
        self.calls: list[dict] = []

    def __call__(self, *, command, run_id, prepared_handle, submitted_at):
        registered = (
            self.handlers.registry.is_registered(run_id)
            if self.handlers is not None
            else None
        )
        self.calls.append(
            {
                "command": command,
                "run_id": run_id,
                "prepared_handle": prepared_handle,
                "submitted_at": submitted_at,
                "submission_exists_at_construction": (
                    prepared_handle.run_dir / "submission.json"
                ).exists(),
                "registered_at_construction": registered,
            }
        )
        if self.mode == "factory-raise":
            raise RuntimeError("injected factory failure")
        return FakeRunner(prepared_handle=prepared_handle, mode=self.mode)


class Harness:
    def __init__(self, tmp_path: Path, *, mode: str = "pending", **kwargs) -> None:
        self.root = tmp_path / "svroot"
        self.session_store = storage.native_session_store(self.root)
        real_store = storage.native_event_store(self.root)
        self.event_store = SpyEventStore(real_store.base_dir)
        self.factory = SpyFactory(mode=mode)
        options = dict(
            session_store=self.session_store,
            event_store=self.event_store,
            run_task_factory=self.factory,
            cancel_wait_seconds=5.0,
            # The startup snapshot every daemon hands its handlers, injected
            # factory or not. Handlers require one at construction.
            agents=rfx.snapshot(),
        )
        options.update(kwargs)
        self.handlers = handlers.ArsdHandlers(**options)
        self.factory.handlers = self.handlers

    def run_dir(self, run_id: str) -> Path:
        return Path(self.event_store.base_dir) / run_id

    def submission(self, run_id: str) -> dict:
        return json.loads(
            (self.run_dir(run_id) / "submission.json").read_text(encoding="utf-8")
        )

    async def submit(self, caller, request_id: str, payload: dict | None = None):
        return await self.handlers(caller, parsed_submit(request_id, payload))

    async def aclose(self) -> None:
        await self.handlers.aclose()


def reply_bytes(reply: dict) -> bytes:
    return protocol.encode_frame(protocol.build_result("req-echo", reply))


def expect_error(code: str):
    return pytest.raises(protocol.ProtocolError)


async def submit_expecting(harness: Harness, caller, request_id, code: str, payload=None):
    with pytest.raises(protocol.ProtocolError) as err:
        await harness.submit(caller, request_id, payload)
    assert err.value.code == code
    return err.value


# --- derivation --------------------------------------------------------------


def derived(principal_id: str, request_id: str) -> str:
    return admission.derive_run_id(
        admission.AdmissionKey(principal_id=principal_id, request_id=request_id)
    )


def test_derived_run_id_deterministic_pinned_and_store_safe(tmp_path: Path) -> None:
    run_id = derived("principal-a", "req-001")
    assert run_id == derived("principal-a", "req-001")  # restart-stable, no state
    # Pinned derivation vector: tagged, length-prefixed injective encoding.
    p = b"principal-a"
    r = b"req-001"
    material = (
        b"arsd-run-id-v1\x00"
        + len(p).to_bytes(8, "big")
        + p
        + len(r).to_bytes(8, "big")
        + r
    )
    assert run_id == "run-" + hashlib.sha256(material).hexdigest()[:32]
    assert len(run_id) == len("run-") + 32
    assert _RUN_ID_RE.match(run_id)
    # EventStore-safe end to end: the real store accepts the derived identity.
    handle = EventStore(base_dir=tmp_path / "native-runs").create_run(run_id)
    assert handle.run_id == run_id


def test_derived_run_id_distinct_across_principals_and_requests() -> None:
    base = derived("principal-a", "req-001")
    assert derived("principal-b", "req-001") != base
    assert derived("principal-a", "req-002") != base


def test_derivation_encoding_injective_at_field_boundaries() -> None:
    # Without injective encoding these boundary shifts would collide.
    assert derived("ab", "c") != derived("a", "bc")
    assert derived("a.b", "c") != derived("a", "b.c")
    assert derived("principal-a1", "1") != derived("principal-a", "11")


@pytest.mark.parametrize(
    "principal_id, request_id",
    [
        ("", "req-1"),
        ("principal-a", ""),
        ("principal-a", "bad space"),
        ("principal-a", "bad/slash"),
        ("principal-a", "bad\nline"),
        ("principal-a", "r" * (protocol.MAX_REQUEST_ID_CHARS + 1)),
    ],
)
def test_admission_key_format_guards_fail_closed(principal_id, request_id) -> None:
    with pytest.raises(ValueError):
        admission.AdmissionKey(principal_id=principal_id, request_id=request_id)


def test_admission_key_accepts_max_length_request_id() -> None:
    key = admission.AdmissionKey(
        principal_id="principal-a",
        request_id="r" * protocol.MAX_REQUEST_ID_CHARS,
    )
    assert _RUN_ID_RE.match(admission.derive_run_id(key))


# --- request digest ----------------------------------------------------------


def test_request_digest_pinned_canonical_form() -> None:
    command = submit_command()
    digest = admission.compute_request_digest(command)
    prompt = command.prompt_text.encode("utf-8")
    request_material = dataclasses.asdict(command.request)
    for name in admission._DIGEST_OMIT_WHEN_NONE:
        if request_material.get(name) is None:
            request_material.pop(name)
    material = {
        "digest_schema_version": admission.DIGEST_SCHEMA_VERSION,
        "request": request_material,
        "workspace_root": command.workspace_root,
        "cwd": command.cwd,
        "retry_of_run_id": command.retry_of_run_id,
        "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
        "prompt_bytes": len(prompt),
    }
    canonical = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert digest.value == expected
    assert digest.prompt_sha256 == hashlib.sha256(prompt).hexdigest()
    assert digest.prompt_bytes == len(prompt)


def test_request_digest_stable_under_wire_key_reordering() -> None:
    payload = submit_payload()
    reordered = json.loads(
        json.dumps({key: payload[key] for key in reversed(list(payload))})
    )
    reordered["request"] = {
        key: payload["request"][key] for key in reversed(list(payload["request"]))
    }
    first = admission.compute_request_digest(protocol.parse_submit(payload))
    second = admission.compute_request_digest(protocol.parse_submit(reordered))
    assert first.value == second.value


def test_request_digest_binds_exact_prompt_bytes_not_chars() -> None:
    ascii_cmd = submit_command(submit_payload(prompt_text="ee"))
    multibyte = submit_command(submit_payload(prompt_text="é"))
    a = admission.compute_request_digest(ascii_cmd)
    b = admission.compute_request_digest(multibyte)
    assert a.prompt_bytes == 2 and b.prompt_bytes == 2
    assert a.value != b.value  # same byte count, different bytes


_BEHAVIOR_MUTATIONS = [
    ("prompt_extra_byte", {"prompt_text": "run the doc check."}),
    ("prompt_same_length_diff_bytes", {"prompt_text": "run the doc checK"}),
    ("workspace_root", {"workspace_root": "/tmp/ws2"}),
    ("cwd", {"cwd": "subdir"}),
    ("retry_of_run_id", {"retry_of_run_id": "run-" + "f" * 32}),
    ("owner", {"request": valid_wire_request(owner="hermes2")}),
    ("namespace", {"request": valid_wire_request(namespace="hermes/other")}),
    ("agent_id", {"request": valid_wire_request(agent_id="fake-agent-2")}),
    ("session_id", {"request": valid_wire_request(session_id="sess-arsd-2")}),
    ("session_id_absent", {"request": valid_wire_request(session_id=None)}),
    (
        "expected_binding_hash",
        {"request": valid_wire_request(expected_binding_hash="sha256:" + "9" * 64)},
    ),
    (
        "input_refs",
        {
            "request": valid_wire_request(
                input_refs=[
                    {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64},
                    {"ref": "file:extra", "content_hash": "sha256:" + "1" * 64},
                ]
            )
        },
    ),
    (
        "requested_model",
        {"request": valid_wire_request(requested_model="deepseek/deepseek-v4-pro")},
    ),
    ("requested_effort", {"request": valid_wire_request(requested_effort="low")}),
    ("grant_ref", {"request": valid_wire_request(grant_ref="grant:doc-check-2")}),
    ("grant_hash", {"request": valid_wire_request(grant_hash="sha256:" + "0" * 64)}),
    (
        "grant_role_hash",
        {"request": valid_wire_request(grant_role_hash="sha256:" + "2" * 64)},
    ),
    ("grant_capabilities", {"request": valid_wire_request(grant_capabilities=[])}),
    (
        "mcp_snapshot_hashes",
        {"request": valid_wire_request(mcp_snapshot_hashes=["sha256:" + "3" * 64])},
    ),
    ("credential_refs", {"request": valid_wire_request(credential_refs=["slot-a"])}),
    (
        "limits",
        {"request": valid_wire_request(limits={"turn_timeout_seconds": 500.0})},
    ),
    (
        "evidence_policy_hash",
        {"request": valid_wire_request(evidence_policy_hash="sha256:" + "4" * 64)},
    ),
    (
        "recovery_policy_hash",
        {"request": valid_wire_request(recovery_policy_hash="sha256:" + "5" * 64)},
    ),
    # schema_version is pinned to SPEC_SCHEMA_VERSION (R4/B4); it is no longer a
    # free behavior-mutation input for digest sensitivity. Wire rejection of
    # schema_version!=1 is covered by tests/arsd/test_protocol.py (r4_b4).
]


@pytest.mark.parametrize(
    "label, overrides", _BEHAVIOR_MUTATIONS, ids=[m[0] for m in _BEHAVIOR_MUTATIONS]
)
def test_request_digest_changes_for_every_behavior_input(label, overrides) -> None:
    baseline = admission.compute_request_digest(submit_command())
    mutated = admission.compute_request_digest(
        submit_command(submit_payload(**overrides))
    )
    assert mutated.value != baseline.value, label


def test_request_digest_excludes_transport_material(tmp_path: Path) -> None:
    """Same behavior content under two request_ids: same digest, distinct runs."""

    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller = caller_for(principal_a())
        try:
            first = await harness.submit(caller, "req-1")
            task = harness.handlers.registry.task_for(first["run_id"])
            if task is not None:
                await asyncio.wait({task})
            # Same behavior payload under a different request_id (transport only).
            # First Run is terminal so SESSION_BUSY does not apply.
            second = await harness.submit(caller, "req-2")
            sub_one = harness.submission(first["run_id"])
            sub_two = harness.submission(second["run_id"])
            assert first["run_id"] != second["run_id"]
            assert sub_one["request_digest"] == sub_two["request_digest"]
        finally:
            await harness.aclose()

    run_async(case())


# --- handshake ordering and per-key concurrency -------------------------------


def test_ack_only_after_durable_submission_and_registration(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "req-ord-1")
            call = harness.factory.calls[0]
            # Ordering spy: the factory saw the durable artifact already on
            # disk and the registry not yet holding the task.
            assert call["submission_exists_at_construction"] is True
            assert call["registered_at_construction"] is False
            # The acknowledgement exists only after registration.
            assert harness.handlers.registry.is_registered(reply["run_id"])
            assert set(reply) == {"run_id", "session_id", "accepted_at"}
            assert reply["run_id"] == call["run_id"]
        finally:
            await harness.aclose()

    run_async(case())


def test_concurrent_same_key_submits_single_admission(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            replies = await asyncio.gather(
                *[harness.submit(caller, "req-conc-1") for _ in range(5)]
            )
            assert len(harness.event_store.create_calls) == 1
            assert len(harness.factory.calls) == 1
            encoded = {reply_bytes(reply) for reply in replies}
            assert len(encoded) == 1  # byte-identical original accepted fact
        finally:
            await harness.aclose()

    run_async(case())


def test_keyed_locks_serialize_and_clean_up() -> None:
    async def case():
        locks = admission.KeyedLocks()
        order: list[str] = []

        async def first():
            async with locks.hold(("p", "r")):
                order.append("first-in")
                await asyncio.sleep(0.05)
                order.append("first-out")

        async def second():
            await asyncio.sleep(0.01)
            async with locks.hold(("p", "r")):
                order.append("second-in")

        await asyncio.gather(first(), second())
        assert order == ["first-in", "first-out", "second-in"]
        assert len(locks) == 0  # no leaked per-key entries

    run_async(case())


# --- idempotent retransmission -------------------------------------------------


def test_retransmit_post_registration_returns_original_fact(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            first = await harness.submit(caller, "req-idem-1")
            again = await harness.submit(caller, "req-idem-1")
            assert reply_bytes(first) == reply_bytes(again)
            assert len(harness.event_store.create_calls) == 1
            assert len(harness.factory.calls) == 1  # no second task constructed
        finally:
            await harness.aclose()

    run_async(case())


def test_retransmit_post_terminal_returns_original_fact(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller = caller_for(principal_a())
        try:
            first = await harness.submit(caller, "req-idem-2")
            task = harness.handlers.registry.task_for(first["run_id"])
            await asyncio.wait({task})
            assert not harness.handlers.registry.is_registered(first["run_id"])
            again = await harness.submit(caller, "req-idem-2")
            assert reply_bytes(first) == reply_bytes(again)
            assert len(harness.event_store.create_calls) == 1
            assert len(harness.factory.calls) == 1  # never dispatched again
        finally:
            await harness.aclose()

    run_async(case())


def test_retransmit_never_refused_for_capacity_or_busy_session(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, max_concurrent_runs=1)
        caller = caller_for(principal_a())
        try:
            first = await harness.submit(caller, "req-full-1")
            # A distinct new key is refused at full capacity...
            await submit_expecting(
                harness,
                caller,
                "req-full-2",
                protocol.CAPACITY_EXHAUSTED,
                submit_payload(request=valid_wire_request(session_id="sess-b")),
            )
            # ...but the accepted key resolves from durable facts first: not
            # refused for capacity and not refused against its own busy session.
            again = await harness.submit(caller, "req-full-1")
            assert reply_bytes(first) == reply_bytes(again)
            assert len(harness.factory.calls) == 1
        finally:
            await harness.aclose()

    run_async(case())


def test_same_key_different_digest_is_idempotency_conflict(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller = caller_for(principal_a())
        try:
            first = await harness.submit(caller, "req-conf-1")
            changed = submit_payload(prompt_text="run the doc check, but changed")
            # While registered/terminal alike: one key never binds two contents.
            await submit_expecting(
                harness, caller, "req-conf-1", protocol.IDEMPOTENCY_CONFLICT, changed
            )
            task = harness.handlers.registry.task_for(first["run_id"])
            if task is not None:
                await asyncio.wait({task})
            await submit_expecting(
                harness, caller, "req-conf-1", protocol.IDEMPOTENCY_CONFLICT, changed
            )
            assert len(harness.factory.calls) == 1
        finally:
            await harness.aclose()

    run_async(case())


def test_cross_principal_same_request_id_distinct_identity(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        try:
            reply_a = await harness.submit(caller_for(principal_a()), "req-shared")
            # Distinct session id: native session dirs are globally keyed, and a
            # second active Run on the same id is correctly SESSION_BUSY.
            payload_b = submit_payload(
                request=valid_wire_request(
                    owner="other",
                    namespace="other/ns",
                    session_id="sess-other-1",
                )
            )
            reply_b = await harness.submit(
                caller_for(principal_b()), "req-shared", payload_b
            )
            assert reply_a["run_id"] != reply_b["run_id"]
            assert harness.submission(reply_a["run_id"])["principal_id"] == "principal-a"
            assert harness.submission(reply_b["run_id"])["principal_id"] == "principal-b"
        finally:
            await harness.aclose()

    run_async(case())


# --- atomic capacity/session reservation (blocker repair) ---------------------


def test_concurrent_capacity_refusal_creates_no_second_artifacts(
    tmp_path: Path,
) -> None:
    """Distinct keys at max=1: loser is refused before create_run/submission."""

    async def case():
        harness = Harness(tmp_path, max_concurrent_runs=1)
        caller = caller_for(principal_a())
        real_register = harness.handlers.registry.register
        first_in_register = asyncio.Event()
        release_first = asyncio.Event()
        register_entries = 0

        async def gated_register(*args, **kwargs):
            nonlocal register_entries
            register_entries += 1
            if register_entries == 1:
                first_in_register.set()
                await release_first.wait()
            return await real_register(*args, **kwargs)

        harness.handlers.registry.register = gated_register  # type: ignore[method-assign]
        try:
            t1 = asyncio.create_task(
                harness.submit(
                    caller,
                    "race-cap-1",
                    submit_payload(
                        request=valid_wire_request(session_id="sess-race-a")
                    ),
                )
            )
            await first_in_register.wait()
            # Second key races while the first holds a slot but has not committed.
            with pytest.raises(protocol.ProtocolError) as err:
                await harness.submit(
                    caller,
                    "race-cap-2",
                    submit_payload(
                        request=valid_wire_request(session_id="sess-race-b")
                    ),
                )
            assert err.value.code == protocol.CAPACITY_EXHAUSTED
            release_first.set()
            first = await t1
            # Exactly one create_run, one factory/dispatch, one submission artifact.
            assert harness.event_store.create_calls == [first["run_id"]]
            assert len(harness.factory.calls) == 1
            run_dirs = [
                path
                for path in Path(harness.event_store.base_dir).iterdir()
                if path.is_dir()
            ]
            assert [path.name for path in run_dirs] == [first["run_id"]]
            assert (harness.run_dir(first["run_id"]) / "submission.json").is_file()
            loser_id = derived("principal-a", "race-cap-2")
            assert not harness.run_dir(loser_id).exists()
        finally:
            release_first.set()
            await harness.aclose()

    run_async(case())


def test_concurrent_same_session_refusal_creates_no_second_artifacts(
    tmp_path: Path,
) -> None:
    """Same session, distinct keys: SESSION_BUSY before any second reservation."""

    async def case():
        harness = Harness(tmp_path, max_concurrent_runs=4)
        caller = caller_for(principal_a())
        real_register = harness.handlers.registry.register
        first_in_register = asyncio.Event()
        release_first = asyncio.Event()
        register_entries = 0

        async def gated_register(*args, **kwargs):
            nonlocal register_entries
            register_entries += 1
            if register_entries == 1:
                first_in_register.set()
                await release_first.wait()
            return await real_register(*args, **kwargs)

        harness.handlers.registry.register = gated_register  # type: ignore[method-assign]
        try:
            t1 = asyncio.create_task(harness.submit(caller, "race-sess-1"))
            await first_in_register.wait()
            with pytest.raises(protocol.ProtocolError) as err:
                await harness.submit(caller, "race-sess-2")
            assert err.value.code == protocol.SESSION_BUSY
            release_first.set()
            first = await t1
            assert harness.event_store.create_calls == [first["run_id"]]
            assert len(harness.factory.calls) == 1
            loser_id = derived("principal-a", "race-sess-2")
            assert not harness.run_dir(loser_id).exists()
            assert not (harness.run_dir(loser_id) / "submission.json").exists()
        finally:
            release_first.set()
            await harness.aclose()

    run_async(case())


def test_cancelled_submit_releases_reservation_before_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CancelledError after reserve must free capacity; no durable artifacts."""

    async def case():
        harness = Harness(tmp_path, max_concurrent_runs=1)
        caller = caller_for(principal_a())
        cancelled_id = derived("principal-a", "cancel-res-1")
        real_prepare = admission.prepare_run

        def boom_prepare(store, run_id):
            raise asyncio.CancelledError()

        monkeypatch.setattr(admission, "prepare_run", boom_prepare)
        with pytest.raises(asyncio.CancelledError):
            await harness.submit(
                caller,
                "cancel-res-1",
                submit_payload(
                    request=valid_wire_request(session_id="sess-cancel-1")
                ),
            )
        assert not harness.run_dir(cancelled_id).exists()
        assert harness.factory.calls == []
        monkeypatch.setattr(admission, "prepare_run", real_prepare)
        later = await harness.submit(
            caller,
            "cancel-res-2",
            submit_payload(
                request=valid_wire_request(session_id="sess-cancel-2")
            ),
        )
        assert later["run_id"] == derived("principal-a", "cancel-res-2")
        assert harness.handlers.registry.is_registered(later["run_id"])
        await harness.aclose()

    run_async(case())


def test_resolve_durable_rejects_symlinked_run_dir(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        run_id = derived("principal-a", "symlink-res-1")
        outside = tmp_path / "outside-run"
        outside.mkdir()
        # Plant a lookalike submission outside the store root.
        digest = admission.compute_request_digest(submit_command())
        storage.write_once_json(
            outside / "submission.json",
            {
                "schema_version": admission.SUBMISSION_SCHEMA_VERSION,
                "principal_id": "principal-a",
                "request_id": "symlink-res-1",
                "run_id": run_id,
                "retry_of_run_id": None,
                "api_version": protocol.ARSD_API_VERSION,
                "accepted_at": "2026-07-22T00:00:00+00:00",
                "peer": {"pid": 1, "uid": 1, "gid": 1},
                "owner": "hermes",
                "namespace": "hermes/doc-check",
                "session_id": "sess-arsd-1",
                "agent_id": "fake-agent",
                "request_digest": digest.value,
                "prompt_sha256": digest.prompt_sha256,
                "prompt_bytes": digest.prompt_bytes,
                "max_run_event_budget_bytes": 4 * 1024 * 1024 * 1024,
            },
        )
        storage.write_once_json(
            outside / "result.json",
            {"run_id": run_id, "status": "failed", "retryable": False},
        )
        Path(harness.event_store.base_dir).mkdir(parents=True, exist_ok=True)
        target = Path(harness.event_store.base_dir) / run_id
        target.symlink_to(outside)
        try:
            # Without symlink rejection this would wrongly ack the outside terminal.
            await submit_expecting(
                harness, caller, "symlink-res-1", protocol.SUBMISSION_INDETERMINATE
            )
            assert harness.factory.calls == []
            assert harness.event_store.create_calls == []
        finally:
            await harness.aclose()

    run_async(case())


# --- ownership gate before any reservation -------------------------------------


def test_owner_mismatch_precedes_any_reservation(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            foreign = submit_payload(
                request=valid_wire_request(owner="other", namespace="other/ns")
            )
            err = await submit_expecting(
                harness, caller, "req-own-1", protocol.OWNER_MISMATCH, foreign
            )
            assert "other" not in err.message  # no caller-value echo
            assert harness.event_store.create_calls == []
            assert list(Path(harness.event_store.base_dir).iterdir()) == []
            assert harness.factory.calls == []
        finally:
            await harness.aclose()

    run_async(case())


# --- duplicate creation and fault windows --------------------------------------


def test_bare_run_dir_fails_closed_indeterminate(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        run_id = derived("principal-a", "req-bare-1")
        harness.event_store.create_run(run_id)  # seeded reservation, no binding
        harness.event_store.create_calls.clear()
        try:
            await submit_expecting(
                harness, caller, "req-bare-1", protocol.SUBMISSION_INDETERMINATE
            )
            # Permanently consumed: the retransmit stays fail-closed too.
            await submit_expecting(
                harness, caller, "req-bare-1", protocol.SUBMISSION_INDETERMINATE
            )
            assert harness.event_store.create_calls == []
            assert harness.factory.calls == []  # nothing dispatched, ever
            assert not (harness.run_dir(run_id) / "submission.json").exists()
        finally:
            await harness.aclose()

    run_async(case())


def test_foreign_principal_binding_never_duplicate_matched(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        run_id = derived("principal-a", "req-foreign-1")
        handle = harness.event_store.create_run(run_id)
        harness.event_store.create_calls.clear()
        digest = admission.compute_request_digest(submit_command())
        foreign = {
            "schema_version": admission.SUBMISSION_SCHEMA_VERSION,
            "principal_id": "principal-z",
            "request_id": "req-other",
            "run_id": run_id,
            "retry_of_run_id": None,
            "api_version": protocol.ARSD_API_VERSION,
            "accepted_at": "2026-07-22T00:00:00+00:00",
            "peer": {"pid": 1, "uid": 1, "gid": 1},
            "owner": "hermes",
            "namespace": "hermes/doc-check",
            "session_id": "sess-arsd-1",
            "agent_id": "fake-agent",
            "request_digest": digest.value,
            "prompt_sha256": digest.prompt_sha256,
            "prompt_bytes": digest.prompt_bytes,
            "max_run_event_budget_bytes": 4 * 1024 * 1024 * 1024,
        }
        storage.write_once_json(handle.run_dir / "submission.json", foreign)
        try:
            # Equal digest but a foreign key binding: an integrity failure,
            # never an accepted-fact match.
            await submit_expecting(
                harness, caller, "req-foreign-1", protocol.SUBMISSION_INDETERMINATE
            )
            assert harness.factory.calls == []
        finally:
            await harness.aclose()

    run_async(case())


def test_create_run_failure_fails_closed_then_recovers(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        harness.event_store.fail_mode = "error"
        try:
            await submit_expecting(
                harness, caller, "req-create-1", protocol.INTERNAL
            )
            run_id = derived("principal-a", "req-create-1")
            assert not harness.run_dir(run_id).exists()  # nothing durable
            assert harness.factory.calls == []  # no blind dispatch
            # Retransmit re-resolves from durable facts: with nothing durable
            # the key is still fresh and admits normally once the fault clears.
            harness.event_store.fail_mode = None
            reply = await harness.submit(caller, "req-create-1")
            assert reply["run_id"] == run_id
        finally:
            await harness.aclose()

    run_async(case())


def test_submission_durability_failure_blocks_ack_and_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission must not acknowledge/dispatch when exclusive write durability fails."""
    from agent_run_supervisor import event_store as event_store_mod

    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        run_id = derived("principal-a", "req-dur-1")
        real_fsync = os.fsync
        secret = "sk-live-" + "DURFAILCANARY"

        def boom_file_fsync(fd: int) -> None:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode):
                raise OSError(5, f"injected fsync for {secret}")
            return real_fsync(fd)

        monkeypatch.setattr(event_store_mod.os, "fsync", boom_file_fsync)
        try:
            err = await submit_expecting(
                harness, caller, "req-dur-1", protocol.INTERNAL
            )
            assert secret not in str(err)
            assert harness.factory.calls == []
            assert not harness.handlers.registry.is_registered(run_id)
            # Atomic no-clobber publish: final path must not appear on fsync failure.
            submission = harness.run_dir(run_id) / "submission.json"
            assert not submission.exists()
            temps = [
                p
                for p in harness.run_dir(run_id).iterdir()
                if p.name.startswith(".tmp-excl-")
            ]
            assert temps  # uncertain temp debris remains fail-closed
        finally:
            await harness.aclose()

    run_async(case())


def test_crash_after_create_before_submission_consumes_key(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        harness.event_store.fail_mode = "after-create"
        run_id = derived("principal-a", "req-window-1")
        try:
            await submit_expecting(
                harness, caller, "req-window-1", protocol.INTERNAL
            )
            assert harness.run_dir(run_id).is_dir()
            assert not (harness.run_dir(run_id) / "submission.json").exists()
            harness.event_store.fail_mode = None
            await submit_expecting(
                harness, caller, "req-window-1", protocol.SUBMISSION_INDETERMINATE
            )
            assert harness.factory.calls == []
            assert len(harness.event_store.create_calls) == 1
        finally:
            await harness.aclose()

    run_async(case())


def test_registration_failure_finalizes_pre_dispatch_failed(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path, mode="factory-raise")
        caller = caller_for(principal_a())
        run_id = derived("principal-a", "req-reg-1")
        try:
            await submit_expecting(harness, caller, "req-reg-1", protocol.INTERNAL)
            result = json.loads(
                (harness.run_dir(run_id) / "result.json").read_text(encoding="utf-8")
            )
            # Safe pre-dispatch terminal via the existing result builder.
            assert result["status"] == "failed"
            assert result["origin"] == "supervisor"
            assert result["detail_code"] == "REGISTRATION_FAILED"
            assert result["retryable"] is False
            assert result["stop_reason"] is None
            submission = harness.submission(run_id)
            # Retransmit: original accepted identity with its terminal queryable.
            harness.factory.mode = "pending"
            reply = await harness.submit(caller, "req-reg-1")
            assert reply == {
                "run_id": run_id,
                "session_id": submission["session_id"],
                "accepted_at": submission["accepted_at"],
            }
            assert len(harness.factory.calls) == 1  # never a second construction
            assert not harness.handlers.registry.is_registered(run_id)
        finally:
            await harness.aclose()

    run_async(case())


def test_registration_failure_with_failed_finalization_stays_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case():
        harness = Harness(tmp_path, mode="factory-raise")
        caller = caller_for(principal_a())
        run_id = derived("principal-a", "req-reg-2")

        def broken_finalization(handle, run_id):
            raise RuntimeError("injected finalization failure")

        monkeypatch.setattr(
            admission, "finalize_registration_failure", broken_finalization
        )
        try:
            await submit_expecting(harness, caller, "req-reg-2", protocol.INTERNAL)
            assert not (harness.run_dir(run_id) / "result.json").exists()
            # No terminal and no registered task: fail-closed on retransmit,
            # never an auto-dispatch.
            await submit_expecting(
                harness, caller, "req-reg-2", protocol.SUBMISSION_INDETERMINATE
            )
            assert len(harness.factory.calls) == 1
        finally:
            await harness.aclose()

    run_async(case())


# --- submission artifact --------------------------------------------------------


EXPECTED_SUBMISSION_FIELDS = {
    "schema_version",
    "principal_id",
    "request_id",
    "run_id",
    "retry_of_run_id",
    "api_version",
    "accepted_at",
    "peer",
    "owner",
    "namespace",
    "session_id",
    "agent_id",
    "request_digest",
    "prompt_sha256",
    "prompt_bytes",
    "max_run_event_budget_bytes",
}


def test_submission_artifact_exact_fields_mode_and_no_secrets(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        prompt = f"do the work; token {SECRET_SENTINEL} must never persist"
        payload = submit_payload(prompt_text=prompt)
        try:
            reply = await harness.submit(caller, "req-art-1", payload)
            run_id = reply["run_id"]
            path = harness.run_dir(run_id) / "submission.json"
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            raw = path.read_text(encoding="utf-8")
            submission = json.loads(raw)
            assert set(submission) == EXPECTED_SUBMISSION_FIELDS
            assert submission["schema_version"] == admission.SUBMISSION_SCHEMA_VERSION
            assert submission["principal_id"] == "principal-a"
            assert submission["request_id"] == "req-art-1"
            assert submission["run_id"] == run_id
            assert submission["retry_of_run_id"] is None
            assert submission["api_version"] == protocol.ARSD_API_VERSION
            assert submission["accepted_at"] == reply["accepted_at"]
            assert submission["peer"] == {"pid": 4242, "uid": 1000, "gid": 1000}
            assert submission["owner"] == "hermes"
            assert submission["namespace"] == "hermes/doc-check"
            assert submission["session_id"] == "sess-arsd-1"
            assert submission["agent_id"] == "fake-agent"
            digest = admission.compute_request_digest(protocol.parse_submit(payload))
            assert submission["request_digest"] == digest.value
            assert submission["prompt_sha256"] == digest.prompt_sha256
            assert submission["prompt_bytes"] == digest.prompt_bytes
            # Field scan: no prompt text and no secret-shaped values.
            assert SECRET_SENTINEL not in raw
            assert "do the work" not in raw
        finally:
            await harness.aclose()

    run_async(case())


def test_submission_bytes_identical_after_retransmit(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "req-bytes-1")
            path = harness.run_dir(reply["run_id"]) / "submission.json"
            before = path.read_bytes()
            await harness.submit(caller, "req-bytes-1")
            assert path.read_bytes() == before
        finally:
            await harness.aclose()

    run_async(case())


# --- prepared handoff -------------------------------------------------------------


def test_factory_receives_prepared_handle_single_create_run(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "req-hand-1")
            await harness.submit(caller, "req-hand-1")
            await harness.submit(caller, "req-hand-1")
            call = harness.factory.calls[0]
            handle = call["prepared_handle"]
            assert handle.run_id == reply["run_id"]
            assert handle.run_dir == harness.run_dir(reply["run_id"])
            assert handle.run_dir.is_dir()
            # Exactly one create_run per key across all retransmissions.
            assert harness.event_store.create_calls == [reply["run_id"]]
            assert call["submitted_at"] == reply["accepted_at"]
        finally:
            await harness.aclose()

    run_async(case())


def test_production_default_factory_builds_runtask_on_native_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("acp")

    from agent_run_supervisor.native_acp import agent_registry
    from agent_run_supervisor.native_acp.profile import (
        STANDARD_NATIVE_ACP_V1,
        ProfileRegistry,
    )
    from agent_run_supervisor.native_acp.run_task import NativeRunTaskError, RunTask

    from tests.native_acp import registry_fixtures as rfx

    registry = ProfileRegistry((STANDARD_NATIVE_ACP_V1,))
    conf = tmp_path / "conf"
    conf.mkdir()
    agents = agent_registry.load_agents_file(
        rfx.write_registry(conf, entries={"fake-agent": rfx.minimal_entry()})
    )
    root = tmp_path / "svroot"
    event_store = storage.native_event_store(root)
    run_id = derived("principal-a", "req-prod-1")
    handle = admission.prepare_run(event_store, run_id)
    factory = handlers.default_run_task_factory(root, registry=registry, agents=agents)
    task = factory(
        command=submit_command(),
        run_id=run_id,
        prepared_handle=handle,
        submitted_at="2026-07-22T00:00:00+00:00",
    )
    # The production default is a real RunTask bound to the supervisor-root
    # native stores; its own prepared-handle guards accepted the handoff.
    assert isinstance(task, RunTask)
    # A handle outside the daemon's native event store is rejected by RunTask.
    foreign_store = storage.native_event_store(tmp_path / "other-root")
    foreign_handle = admission.prepare_run(foreign_store, run_id)
    with pytest.raises(NativeRunTaskError):
        factory(
            command=submit_command(),
            run_id=run_id,
            prepared_handle=foreign_handle,
            submitted_at="2026-07-22T00:00:00+00:00",
        )


# --- Session-reuse refusals that survive the reset ---------------------------
#
# The retired families went with their layer: credential-ref admission, the
# artifact/attestation identity gate, and the per-agent closed profiles that
# carried them no longer exist, so there is nothing left for those cases to be
# about. What *is* still a refusal — a Session whose identity no longer matches
# the Run's, and a quarantined Session — is kept and re-pointed onto the
# reset's own identity model.


def seeded_native_session(tmp_path: Path, session_id: str, *, agent_id="fake-agent"):
    """One already-existing Session record, written through the production seam."""
    from agent_run_supervisor.native_acp.profile import STANDARD_NATIVE_ACP_V1
    from agent_run_supervisor.native_acp.spec import resolve_workspace_binding

    profile = STANDARD_NATIVE_ACP_V1
    session_store = storage.native_session_store(tmp_path / f"svroot-{session_id}")
    binding = resolve_workspace_binding(root=tmp_path)
    storage.create_native_session(
        session_store,
        session_id=session_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        profile_hash=profile.profile_hash(),
        owner="hermes",
        namespace="hermes/doc-check",
        workspace_hash=binding.workspace_hash,
        effective_cwd=binding.effective_cwd,
        matched_root=binding.canonical_root,
        agent_id=agent_id,
        agent_session_id=f"external-{session_id}",
    )
    return session_store, binding, profile


def test_profile_hash_drift_refuses_reuse(tmp_path: Path) -> None:
    """A profile whose frozen ACP semantics changed no longer binds its Sessions."""
    from agent_run_supervisor.native_acp.profile import AcpCompatProfile
    from agent_run_supervisor.session import SessionBindingError, validate_native_binding

    store, binding, profile = seeded_native_session(tmp_path, "sess-drift")
    record = store.open_session("sess-drift")

    validate_native_binding(
        record,
        profile=profile,
        workspace_result=binding,
        owner="hermes",
        namespace="hermes/doc-check",
        expected_agent_id="fake-agent",
    )

    # A frozen ACP semantic changed without a revision bump: the profile hash
    # drifts and reuse is refused before spawn.
    drifted = AcpCompatProfile(
        profile_id=profile.profile_id,
        revision=profile.revision,
        acp_protocol_version=profile.acp_protocol_version,
        required_capabilities=("loadSession", "fs"),
    )
    assert drifted.profile_hash() != profile.profile_hash()
    with pytest.raises(SessionBindingError) as excinfo:
        validate_native_binding(
            record,
            profile=drifted,
            workspace_result=binding,
            owner="hermes",
            namespace="hermes/doc-check",
            expected_agent_id="fake-agent",
        )
    assert "profile_hash" in str(excinfo.value)


def test_reuse_under_a_different_agent_is_refused(tmp_path: Path) -> None:
    """A Session created under one agent is never loaded as another."""
    from agent_run_supervisor.session import SessionBindingError, validate_native_binding

    store, binding, profile = seeded_native_session(tmp_path, "sess-agent")
    record = store.open_session("sess-agent")
    with pytest.raises(SessionBindingError) as excinfo:
        validate_native_binding(
            record,
            profile=profile,
            workspace_result=binding,
            owner="hermes",
            namespace="hermes/doc-check",
            expected_agent_id="some-other-agent",
        )
    assert "agent_id" in str(excinfo.value)


def test_quarantined_session_refuses_reuse(tmp_path: Path) -> None:
    from agent_run_supervisor.session import (
        SessionQuarantinedError,
        validate_native_binding,
    )

    store, binding, profile = seeded_native_session(tmp_path, "sess-quarantined")
    store.write_quarantine_pending(
        "sess-quarantined",
        reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST,
        run_id="run-x",
    )
    store.mark_quarantined(
        "sess-quarantined",
        reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST,
        run_id="run-x",
    )
    record = store.open_session("sess-quarantined")
    with pytest.raises(SessionQuarantinedError):
        validate_native_binding(
            record,
            profile=profile,
            workspace_result=binding,
            owner="hermes",
            namespace="hermes/doc-check",
            expected_agent_id="fake-agent",
        )


@pytest.mark.parametrize(
    "payload_overrides",
    [
        {"env": {"SOME_NAME": "some-value"}},
        {"command": "some-agent"},
        {"argv": ["acp"]},
        {"transport": "stdio"},
    ],
)
def test_wire_injection_of_a_runtime_selection_field_is_refused(
    payload_overrides,
) -> None:
    """A5: those are not fields on the request, so the refusal is structural."""
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.parse_submit(
            submit_payload(request=valid_wire_request(**payload_overrides))
        )
    assert excinfo.value.code == protocol.INVALID_REQUEST


# --- the digest material moved, and the schema version moved with it ---------
#
# The pre-reset line kept one field out of the digest so a legacy frame would
# hash byte-identically. That compatibility measure is gone: agent identity
# replaced profile selection and the launch material became value-blind, so the
# material genuinely changed — and saying so through the schema version is the
# honest way to say it.


def test_no_request_field_is_dropped_from_the_digest() -> None:
    assert admission._DIGEST_OMIT_WHEN_NONE == ()


def test_naming_a_different_agent_changes_the_digest() -> None:
    baseline = admission.compute_request_digest(protocol.parse_submit(submit_payload()))
    other = admission.compute_request_digest(
        protocol.parse_submit(
            submit_payload(request=valid_wire_request(agent_id="other-agent"))
        )
    )
    assert other.value != baseline.value


@pytest.mark.parametrize(
    "field", ["session_id", "expected_binding_hash", "cwd", "retry_of_run_id"]
)
def test_every_null_valued_field_still_contributes(field: str) -> None:
    """No blanket null-strip: these four stay meaningful when they are null."""
    null_frame = {"session_id": None}
    baseline = submit_payload(request=valid_wire_request(**null_frame))
    if field in ("cwd", "retry_of_run_id"):
        changed = submit_payload(
            request=valid_wire_request(**null_frame), **{field: "value"}
        )
    else:
        changed = submit_payload(
            request=valid_wire_request(**{**null_frame, field: "value"})
        )
    assert admission.compute_request_digest(
        protocol.parse_submit(changed)
    ).value != admission.compute_request_digest(protocol.parse_submit(baseline)).value


def test_only_the_surface_whose_material_changed_has_moved() -> None:
    """Each version tracks exactly its own material, and nothing else.

    The Session no-close model changed the request's Session block, so every
    surface that seals it moved together to 3, while the unchanged launch
    snapshot stayed at 2. The configurable event budget then moved the
    submission record alone to 4: the durable admission evidence gained the
    ceiling that admitted the Run, and no other material changed — admission
    policy is not caller material, digest input, or sealed per-Run state.
    """
    assert admission.DIGEST_SCHEMA_VERSION == 3
    assert admission.SUBMISSION_SCHEMA_VERSION == 4
    assert protocol.ARSD_API_VERSION == 3
    assert protocol.SUPPORTED_API_VERSIONS == (3,)
    from agent_run_supervisor.native_acp.spec import (
        LAUNCH_SCHEMA_VERSION,
        SPEC_SCHEMA_VERSION,
    )

    assert SPEC_SCHEMA_VERSION == 3
    assert LAUNCH_SCHEMA_VERSION == 2


def test_agent_id_is_not_a_forbidden_runtime_selection_field() -> None:
    """It selects among operator-authored registry entries: it names no path,
    executable, argv, env, digest, or version, and the grammar runs before the
    lookup so it cannot."""
    assert "agent_id" not in admission.FORBIDDEN_RUNTIME_SELECTION_FIELDS
    assert "profile_id" in admission.FORBIDDEN_RUNTIME_SELECTION_FIELDS


@pytest.mark.parametrize("value", [7, ["x"], {"a": 1}, True, None])
def test_a_non_string_agent_id_is_an_invalid_request_never_internal(value) -> None:
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.parse_submit(
            submit_payload(request=valid_wire_request(agent_id=value))
        )
    assert excinfo.value.code == protocol.INVALID_REQUEST


# -- the strict submission validator accepts exactly what the writer emits ----


def _written_submission(run_id: str = "run-strict-1") -> dict:
    command = submit_command()
    return admission.build_submission_artifact(
        key=admission.AdmissionKey(principal_id="principal-a", request_id="req-1"),
        run_id=run_id,
        command=command,
        digest=admission.compute_request_digest(command),
        accepted_at="2026-07-22T00:00:00+00:00",
        peer={"pid": 1, "uid": 1000, "gid": 1000},
        event_budget_policy=DEFAULT_EVENT_BUDGET_POLICY,
    )


def test_the_writers_field_set_is_the_validators_field_set() -> None:
    """One named field set, so writer and verifier cannot drift apart."""
    assert set(_written_submission()) == set(admission.SUBMISSION_FIELDS)
    assert set(_written_submission()["peer"]) == set(admission.SUBMISSION_PEER_FIELDS)


def test_a_written_submission_validates_and_attributes_exactly() -> None:
    attribution = admission.validate_submission_artifact(
        _written_submission(), run_id="run-strict-1"
    )
    assert attribution is not None
    assert attribution.run_id == "run-strict-1"
    assert attribution.owner == "hermes"
    assert attribution.namespace == "hermes/doc-check"
    assert attribution.session_id == valid_wire_request()["session_id"]


def test_the_validator_accepts_the_writers_whole_value_domain() -> None:
    """Every request the parser admits must yield an acceptable submission.

    Both halves of the Session domain: a create writes ``session_id: null`` and
    the validator derives the prospective id; a reuse writes the caller's id and
    the validator attributes exactly that. One rule, and the runtime selector
    agrees with it in both directions.
    """
    for declared, expected in (
        (None, admission.derive_session_id_for_run("run-domain-1")),
        ("sess-declared", "sess-declared"),
    ):
        command = protocol.parse_submit(
            submit_payload(request=valid_wire_request(session_id=declared))
        )
        payload = admission.build_submission_artifact(
            key=admission.AdmissionKey(principal_id="principal-a", request_id="req-1"),
            run_id="run-domain-1",
            command=command,
            digest=admission.compute_request_digest(command),
            accepted_at="2026-07-22T00:00:00+00:00",
            peer={"pid": 1, "uid": 1000, "gid": 1000},
            event_budget_policy=DEFAULT_EVENT_BUDGET_POLICY,
        )
        assert payload["session_id"] == declared

        attribution = admission.validate_submission_artifact(
            payload, run_id="run-domain-1"
        )
        assert attribution is not None
        assert attribution.session_id == expected
        # The same id the runtime selector derives for this Run.
        assert attribution.session_id == admission.bound_session_id_for_run(
            run_id="run-domain-1", submission=payload
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("agent_id"),
        lambda p: p.__setitem__("agent_id", ""),
        lambda p: p.__setitem__("unknown_field", "accepted-but-not-produced"),
        lambda p: p.pop("prompt_bytes"),
        lambda p: p["peer"].__setitem__("euid", 0),
        lambda p: p["peer"].pop("uid"),
        lambda p: p.__setitem__("session_id", 17),
        lambda p: p.__setitem__("session_id", ""),
    ],
    ids=[
        "missing_agent_id",
        "empty_agent_id",
        "unknown_field",
        "missing_field",
        "peer_extra_key",
        "peer_missing_key",
        "non_string_session_id",
        "empty_session_id",
    ],
)
def test_shape_drift_from_the_writers_output_is_refused(mutate) -> None:
    payload = _written_submission()
    mutate(payload)
    assert (
        admission.validate_submission_artifact(payload, run_id="run-strict-1") is None
    )


# --- WP3.7: A13 / A5 — in-memory agent admission, zero registry I/O ----------


def _registry_snapshot(tmp_path: Path):
    from agent_run_supervisor.native_acp import agent_registry
    from tests.native_acp import registry_fixtures as fx

    path = fx.write_registry(
        tmp_path,
        entries={
            "agent-alpha": fx.full_entry(),
            "agent-beta": fx.minimal_entry(command="other-agent"),
        },
    )
    return agent_registry.load_agents_file(path), path


def test_snapshot_resolution_returns_the_operator_entry(tmp_path: Path) -> None:
    snapshot, _ = _registry_snapshot(tmp_path)
    entry = admission.resolve_agent_entry(snapshot, "agent-alpha")
    assert entry.agent_id == "agent-alpha"
    assert entry.command == "/opt/example/bin/some-agent"


def test_snapshot_resolution_refuses_an_unregistered_agent(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp import agent_registry

    snapshot, _ = _registry_snapshot(tmp_path)
    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        admission.resolve_agent_entry(snapshot, "agent-gamma")
    assert excinfo.value.rule == "AGENT_NOT_REGISTERED"


def test_snapshot_resolution_applies_the_grammar_before_the_lookup(
    tmp_path: Path,
) -> None:
    """A5: ``agent_id`` passes its grammar before it can select anything."""
    from agent_run_supervisor.native_acp import agent_registry

    snapshot, _ = _registry_snapshot(tmp_path)
    for hostile in ("../escape", "Agent-Alpha", "a" * 65, "", "a/b"):
        with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
            admission.resolve_agent_entry(snapshot, hostile)
        assert excinfo.value.rule == "AGENT_ID_INVALID"


def test_snapshot_resolution_without_a_snapshot_fails_closed() -> None:
    from agent_run_supervisor.native_acp import agent_registry

    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        admission.resolve_agent_entry(None, "agent-alpha")
    assert excinfo.value.rule == "REGISTRY_ABSENT"


def test_snapshot_resolution_performs_zero_filesystem_access(
    tmp_path: Path, monkeypatch
) -> None:
    """A13: no registry open on the Run path, ever — not even one."""
    snapshot, _ = _registry_snapshot(tmp_path)

    def refuse(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("agent admission touched the filesystem")

    for name in ("open", "stat", "lstat", "listdir", "scandir", "readlink"):
        monkeypatch.setattr(os, name, refuse)
    for _ in range(5):
        assert admission.resolve_agent_entry(snapshot, "agent-beta").command == (
            "other-agent"
        )


def test_a_mid_serve_registry_edit_has_no_effect_until_restart(tmp_path: Path) -> None:
    """The snapshot is the authority; the file on disk is not consulted again."""
    from agent_run_supervisor.native_acp import agent_registry
    from tests.native_acp import registry_fixtures as fx

    snapshot, path = _registry_snapshot(tmp_path)
    path.write_text(
        fx.registry_text(entries={"agent-alpha": fx.minimal_entry(command="rewritten")}),
        encoding="utf-8",
    )
    assert admission.resolve_agent_entry(snapshot, "agent-alpha").command == (
        "/opt/example/bin/some-agent"
    )
    assert "agent-beta" in snapshot.ids()
    # A restart is what picks the edit up, and only a restart.
    restarted = agent_registry.load_agents_file(path)
    assert restarted.get("agent-alpha").command == "rewritten"
    assert "agent-beta" not in restarted.ids()


def test_admission_module_has_no_registry_reader(tmp_path: Path) -> None:
    import inspect

    text = inspect.getsource(admission)
    assert "load_agents_file" not in text
    assert "tomllib" not in text
    assert "resolve_runtime_binding" not in text
    assert "binding_root" not in text


# --- WP3.7: the digest and the submission are value-blind --------------------


def test_forbidden_runtime_selection_fields_are_absent_from_the_request() -> None:
    from agent_run_supervisor.native_acp.spec import AgentRunRequest

    fields = {field.name for field in dataclasses.fields(AgentRunRequest)}
    for forbidden in admission.FORBIDDEN_RUNTIME_SELECTION_FIELDS:
        assert forbidden not in fields, f"request grew {forbidden!r}"


def test_digest_material_carries_no_forbidden_selection_field() -> None:
    command = submit_command()
    digest = admission.compute_request_digest(command)
    material = json.dumps(dataclasses.asdict(command.request), sort_keys=True)
    for forbidden in admission.FORBIDDEN_RUNTIME_SELECTION_FIELDS:
        assert f'"{forbidden}"' not in material
    assert digest.value.startswith("sha256:")


def test_digest_schema_version_moved_with_the_material() -> None:
    assert admission.DIGEST_SCHEMA_VERSION == 3
    assert admission.SUBMISSION_SCHEMA_VERSION == 4


def test_submission_records_the_agent_not_a_profile() -> None:
    payload = _written_submission()
    assert "agent_id" in payload
    assert "profile_id" not in payload
    assert admission.validate_submission_artifact(payload, run_id="run-strict-1")


# -- D1: the deterministic prospective Session identity ----------------------
#
# A create submission carries no ``session_id``. The Session it will create is
# named by the *same* authenticated identity that names the Run, so a repeated
# request converges on the same Session instead of creating a second one — and
# it does so with no durable pre-Session reservation of its own.


def test_prospective_session_id_is_derived_from_the_admission_key() -> None:
    key = admission.AdmissionKey(principal_id="principal-a", request_id="req-1")
    first = admission.derive_session_id(key)
    assert first == admission.derive_session_id(key)
    # Same principal, different request → a different Session.
    other = admission.derive_session_id(
        admission.AdmissionKey(principal_id="principal-a", request_id="req-2")
    )
    assert other != first
    # Same request text, different principal → a different Session.
    foreign = admission.derive_session_id(
        admission.AdmissionKey(principal_id="principal-b", request_id="req-1")
    )
    assert foreign != first


def test_prospective_session_id_is_a_safe_session_store_component() -> None:
    from agent_run_supervisor.session import is_valid_session_id

    key = admission.AdmissionKey(principal_id="principal-a", request_id="req-1")
    session_id = admission.derive_session_id(key)
    assert is_valid_session_id(session_id)
    assert admission.derive_session_id(key) != admission.derive_run_id(key)


def test_a_create_submission_records_a_null_session_id_and_derives_the_id() -> None:
    command = protocol.parse_submit(
        submit_payload(request=valid_wire_request(session_id=None))
    )
    key = admission.AdmissionKey(principal_id="principal-a", request_id="req-1")
    run_id = admission.derive_run_id(key)
    payload = admission.build_submission_artifact(
        key=key,
        run_id=run_id,
        command=command,
        digest=admission.compute_request_digest(command),
        accepted_at="2026-07-22T00:00:00+00:00",
        peer={"pid": 1, "uid": 1000, "gid": 1000},
        event_budget_policy=DEFAULT_EVENT_BUDGET_POLICY,
    )
    assert payload["session_id"] is None
    assert "session_reuse" not in payload
    assert "ars_session_id" not in payload

    attribution = admission.validate_submission_artifact(payload, run_id=run_id)
    assert attribution is not None
    assert attribution.session_id == admission.derive_session_id(key)
    assert attribution.session_id == admission.bound_session_id_for_run(
        run_id=run_id, submission=payload
    )


def test_a_reuse_submission_records_and_attributes_the_caller_session_id() -> None:
    command = protocol.parse_submit(
        submit_payload(request=valid_wire_request(session_id="sess-existing"))
    )
    key = admission.AdmissionKey(principal_id="principal-a", request_id="req-9")
    run_id = admission.derive_run_id(key)
    payload = admission.build_submission_artifact(
        key=key,
        run_id=run_id,
        command=command,
        digest=admission.compute_request_digest(command),
        accepted_at="2026-07-22T00:00:00+00:00",
        peer={"pid": 1, "uid": 1000, "gid": 1000},
        event_budget_policy=DEFAULT_EVENT_BUDGET_POLICY,
    )
    assert payload["session_id"] == "sess-existing"
    attribution = admission.validate_submission_artifact(payload, run_id=run_id)
    assert attribution is not None
    assert attribution.session_id == "sess-existing"


# -- D4 scenarios 2 and 3: one request, one dispatch, however it arrives ------


def test_a_post_terminal_duplicate_returns_the_same_facts_and_dispatches_once(
    tmp_path: Path,
) -> None:
    """Scenario 2: a lost response costs the caller nothing and the agent nothing.

    The caller retransmits after the Run is already over. It gets the same
    ``run_id`` and the same ``session_id`` back, and no second Run is
    constructed — so the prompt is never sent twice.
    """

    async def case():
        harness = Harness(tmp_path, mode="complete")
        caller = caller_for(principal_a())
        payload = submit_payload(request=valid_wire_request(session_id=None))
        try:
            first = await harness.submit(caller, "dup-1", payload)
            run_id = first["run_id"]
            await asyncio.wait({harness.handlers.registry.task_for(run_id)})
            assert not harness.handlers.registry.is_registered(run_id)

            second = await harness.submit(caller, "dup-1", payload)

            assert second == first
            assert second["session_id"] == admission.derive_session_id_for_run(run_id)
            # One construction, one run directory, one submission — ever.
            assert len(harness.factory.calls) == 1
            assert harness.event_store.create_calls == [run_id]
        finally:
            await harness.aclose()

    run_async(case())


def test_an_in_flight_duplicate_is_serialized_onto_the_same_submission(
    tmp_path: Path,
) -> None:
    """Scenario 3: the duplicate arrives while the original is still running.

    The keyed admission lock serializes the two attempts, the second resolves
    through the *same* durable submission, and exactly one Run is constructed —
    so exactly one ``session/new`` and one prompt dispatch can follow.
    """

    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        payload = submit_payload(request=valid_wire_request(session_id=None))
        real_register = harness.handlers.registry.register
        first_in_register = asyncio.Event()
        release_first = asyncio.Event()

        async def gated_register(*args, **kwargs):
            first_in_register.set()
            await release_first.wait()
            return await real_register(*args, **kwargs)

        harness.handlers.registry.register = gated_register  # type: ignore[method-assign]
        try:
            original = asyncio.create_task(harness.submit(caller, "inflight-1", payload))
            await first_in_register.wait()

            # The duplicate must block on the keyed lock, not race past it.
            duplicate = asyncio.create_task(
                harness.submit(caller, "inflight-1", payload)
            )
            await asyncio.sleep(0)
            assert not duplicate.done(), "the duplicate was not serialized"

            release_first.set()
            first = await original
            second = await duplicate

            assert second["run_id"] == first["run_id"]
            assert second["session_id"] == first["session_id"]
            assert second["accepted_at"] == first["accepted_at"]
            # One construction: only one Run can reach session/new and prompt.
            assert len(harness.factory.calls) == 1
            assert harness.event_store.create_calls == [first["run_id"]]
        finally:
            release_first.set()
            await harness.aclose()

    run_async(case())


# --- the admitting daemon's event-budget ceiling is durable Run evidence -----
#
# ``server_info`` answers "what is this daemon's ceiling *now*". Auditing which
# ceiling admitted a historical Run is a different question, and only the
# write-once submission record can answer it after a config change or restart.

FOUR_GIB = 4 * 1024 * 1024 * 1024


def _policy(ceiling: int):
    from agent_run_supervisor.native_acp import spec as spec_module

    return spec_module.EventBudgetPolicy(max_run_event_budget_bytes=ceiling)


def test_the_submission_records_the_default_event_budget_ceiling(tmp_path: Path) -> None:
    async def case():
        harness = Harness(tmp_path)
        caller = caller_for(principal_a())
        try:
            reply = await harness.submit(caller, "budget-evidence-default")
            assert harness.submission(reply["run_id"])[
                "max_run_event_budget_bytes"
            ] == FOUR_GIB
        finally:
            await harness.aclose()

    run_async(case())


def test_the_submission_records_the_effective_event_budget_ceiling(
    tmp_path: Path,
) -> None:
    """The audit answer: which ceiling admitted *this* Run."""
    ceiling = 4096 * 100

    async def case():
        harness = Harness(tmp_path, event_budget_policy=_policy(ceiling))
        caller = caller_for(principal_a())
        payload = submit_payload(
            request=valid_wire_request(
                limits={"max_event_bytes": 4096, "max_events": 100}
            )
        )
        try:
            reply = await harness.submit(caller, "budget-evidence-custom", payload)
            submission = harness.submission(reply["run_id"])
            assert submission["max_run_event_budget_bytes"] == ceiling
            # Server policy, not caller material: no wire field carries it.
            assert "max_run_event_budget_bytes" not in valid_wire_request()
        finally:
            await harness.aclose()

    run_async(case())


def test_the_request_digest_ignores_the_daemon_event_budget() -> None:
    """A ceiling change must not turn a legitimate duplicate into a conflict."""
    payload = submit_payload()
    lower = protocol.parse_submit(payload, budget_policy=_policy(FOUR_GIB))
    higher = protocol.parse_submit(payload, budget_policy=_policy(2 * FOUR_GIB))
    assert (
        admission.compute_request_digest(lower).value
        == admission.compute_request_digest(higher).value
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda doc: doc.pop("max_run_event_budget_bytes"), id="missing"),
        pytest.param(
            lambda doc: doc.update(max_run_event_budget_bytes="4294967296"), id="string"
        ),
        pytest.param(
            lambda doc: doc.update(max_run_event_budget_bytes=4294967296.0), id="float"
        ),
        pytest.param(
            lambda doc: doc.update(max_run_event_budget_bytes=True), id="bool"
        ),
        pytest.param(lambda doc: doc.update(max_run_event_budget_bytes=0), id="zero"),
        pytest.param(
            lambda doc: doc.update(max_run_event_budget_bytes=-1), id="negative"
        ),
        pytest.param(
            lambda doc: doc.update(max_run_event_budget_bytes=None), id="null"
        ),
        pytest.param(
            lambda doc: doc.update(max_run_event_budget_bytes={"bytes": 1}), id="object"
        ),
    ],
)
def test_strict_validation_refuses_a_missing_or_tampered_budget(mutate) -> None:
    doc = _written_submission()
    assert admission.validate_submission_artifact(doc, run_id="run-strict-1")
    mutate(doc)
    assert admission.validate_submission_artifact(doc, run_id="run-strict-1") is None


def test_a_tampered_budget_makes_the_submission_corrupt(tmp_path: Path) -> None:
    """Reconciliation sees CORRUPT, not a weaker 'valid enough' reading."""
    run_dir = tmp_path / "run-strict-1"
    run_dir.mkdir()
    doc = _written_submission()
    doc["max_run_event_budget_bytes"] = -1
    admission.write_submission(run_dir, doc)
    state = admission.classify_submission(run_dir, run_id="run-strict-1")
    assert state.kind is storage.JsonDocumentKind.CORRUPT
    assert state.attribution is None
    assert admission.read_submission(run_dir) is not None  # readable, not valid


def test_a_duplicate_reads_the_original_record_not_the_new_daemons_ceiling(
    tmp_path: Path,
) -> None:
    """A restart with a different ceiling never rewrites accepted Run evidence."""
    original = 4096 * 100

    async def case():
        first_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(original)
        )
        caller = caller_for(principal_a())
        payload = submit_payload(
            request=valid_wire_request(
                session_id=None, limits={"max_event_bytes": 4096, "max_events": 100}
            )
        )
        try:
            first = await first_daemon.submit(caller, "budget-dup-1", payload)
            run_id = first["run_id"]
            await asyncio.wait({first_daemon.handlers.registry.task_for(run_id)})
        finally:
            await first_daemon.aclose()

        # A second daemon over the same durable stores, started with a raised
        # ceiling. The duplicate resolves from the original record.
        second_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(8 * FOUR_GIB)
        )
        try:
            second = await second_daemon.submit(caller, "budget-dup-1", payload)
            assert second == first
            assert second_daemon.factory.calls == []  # nothing dispatched twice
            assert (
                second_daemon.submission(run_id)["max_run_event_budget_bytes"]
                == original
            )
        finally:
            await second_daemon.aclose()

    run_async(case())


def _lowered_ceiling_payload() -> dict:
    return submit_payload(
        request=valid_wire_request(
            session_id=None, limits={"max_event_bytes": 4096, "max_events": 100}
        )
    )


async def _accept_and_finish(harness: "Harness", caller, request_id: str, payload: dict):
    reply = await harness.submit(caller, request_id, payload)
    await asyncio.wait({harness.handlers.registry.task_for(reply["run_id"])})
    return reply


def test_a_lowered_ceiling_still_returns_the_original_duplicate_facts(
    tmp_path: Path,
) -> None:
    """An accepted key resolves from durable facts, whatever the ceiling is now.

    Admission policy decides what this daemon will *accept*. It cannot retract an
    acceptance that already happened: a caller retransmitting the identical
    request after a restart at a lower ceiling is asking what became of a Run
    that exists, and the answer is the one the durable record already holds. The
    alternative — refusing the retry — strands a caller whose original response
    was lost, with a Run it can no longer name.
    """
    original = 4096 * 100

    async def case():
        caller = caller_for(principal_a())
        payload = _lowered_ceiling_payload()
        first_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(original)
        )
        try:
            first = await _accept_and_finish(
                first_daemon, caller, "budget-dup-2", payload
            )
            run_id = first["run_id"]
            before = first_daemon.submission(run_id)
        finally:
            await first_daemon.aclose()

        # Restarted under a ceiling that would refuse this request as new work.
        strict_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(original - 1)
        )
        try:
            second = await strict_daemon.submit(caller, "budget-dup-2", payload)
            assert second == first
            assert second["run_id"] == run_id
            assert second["session_id"] == admission.derive_session_id_for_run(run_id)
            assert second["accepted_at"] == before["accepted_at"]
            # Nothing re-created, re-dispatched, or re-stamped.
            assert strict_daemon.factory.calls == []
            assert strict_daemon.event_store.create_calls == []
            assert strict_daemon.submission(run_id) == before
            assert before["max_run_event_budget_bytes"] == original
        finally:
            await strict_daemon.aclose()

    run_async(case())


def test_a_lowered_ceiling_still_refuses_new_work(tmp_path: Path) -> None:
    """Resolving an old acceptance is not admitting a new Run."""
    original = 4096 * 100

    async def case():
        caller = caller_for(principal_a())
        payload = _lowered_ceiling_payload()
        first_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(original)
        )
        try:
            await _accept_and_finish(first_daemon, caller, "budget-new-1", payload)
        finally:
            await first_daemon.aclose()

        strict_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(original - 1)
        )
        try:
            # A different request_id is new work, and new work is judged by the
            # ceiling this daemon runs under — before any Run or Session exists.
            await submit_expecting(
                strict_daemon, caller, "budget-new-2", protocol.INVALID_REQUEST, payload
            )
            assert strict_daemon.event_store.create_calls == []
            assert strict_daemon.factory.calls == []
            new_run_id = derived("principal-a", "budget-new-2")
            assert not strict_daemon.run_dir(new_run_id).exists()
        finally:
            await strict_daemon.aclose()

    run_async(case())


def test_a_lowered_ceiling_keeps_conflicting_material_a_conflict(
    tmp_path: Path,
) -> None:
    """Durable-first resolution never downgrades a conflict to a validation error."""
    original = 4096 * 100

    async def case():
        caller = caller_for(principal_a())
        payload = _lowered_ceiling_payload()
        first_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(original)
        )
        try:
            await _accept_and_finish(first_daemon, caller, "budget-conf-1", payload)
        finally:
            await first_daemon.aclose()

        strict_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(original - 1)
        )
        try:
            different = submit_payload(
                request=valid_wire_request(
                    session_id=None,
                    limits={"max_event_bytes": 4096, "max_events": 100},
                ),
                prompt_text="a different prompt entirely",
            )
            await submit_expecting(
                strict_daemon,
                caller,
                "budget-conf-1",
                protocol.IDEMPOTENCY_CONFLICT,
                different,
            )
            assert strict_daemon.factory.calls == []
        finally:
            await strict_daemon.aclose()

    run_async(case())


def _rewrite_submission(run_dir: Path, payload: dict) -> None:
    path = run_dir / "submission.json"
    path.unlink()
    storage.write_once_json(path, payload)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda doc: doc.pop("max_run_event_budget_bytes"), id="missing"),
        pytest.param(
            lambda doc: doc.update(max_run_event_budget_bytes=0), id="non-positive"
        ),
        pytest.param(
            lambda doc: doc.update(max_run_event_budget_bytes="4294967296"),
            id="wrong-type",
        ),
        pytest.param(
            lambda doc: doc.update(unknown_admission_key=1), id="unknown-field"
        ),
    ],
)
def test_duplicate_resolution_refuses_weak_admission_evidence(
    tmp_path: Path, mutate
) -> None:
    """The duplicate path judges the record exactly as reconciliation does.

    A completed Run is not, by itself, proof that ARS admitted it: the durable
    submission is that proof, and a record that fails strict validation is not
    evidence at all. Accepting it as a duplicate would return acceptance facts
    ARS cannot stand behind.
    """

    async def case():
        caller = caller_for(principal_a())
        payload = _lowered_ceiling_payload()
        harness = Harness(tmp_path, mode="complete")
        try:
            first = await _accept_and_finish(harness, caller, "weak-ev-1", payload)
            run_id = first["run_id"]
            record = harness.submission(run_id)
            mutate(record)
            _rewrite_submission(harness.run_dir(run_id), record)
            assert (
                admission.classify_submission(
                    harness.run_dir(run_id), run_id=run_id
                ).kind
                is storage.JsonDocumentKind.CORRUPT
            )
        finally:
            await harness.aclose()

        second_daemon = Harness(tmp_path, mode="complete")
        try:
            await submit_expecting(
                second_daemon,
                caller,
                "weak-ev-1",
                protocol.SUBMISSION_INDETERMINATE,
                payload,
            )
            assert second_daemon.factory.calls == []
            assert second_daemon.event_store.create_calls == []
        finally:
            await second_daemon.aclose()

    run_async(case())


# --- impossible budget evidence never authorizes a duplicate ----------------
#
# A stored ceiling is only evidence if the approved producer could have written
# it *for this request*. Two records can be well-formed and still be impossible:
# one names a ceiling no ``EventBudgetPolicy`` can hold, and one names a ceiling
# that would have refused the very request its digest attests. Neither can have
# come from an admission that happened, so neither may authorize a duplicate.


def _structural_max() -> int:
    from agent_run_supervisor.native_acp import spec as spec_module

    return spec_module.STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES


async def _completed_run(harness: "Harness", caller, request_id: str, payload: dict):
    reply = await harness.submit(caller, request_id, payload)
    await asyncio.wait({harness.handlers.registry.task_for(reply["run_id"])})
    return reply


def _restamp_budget(harness: "Harness", run_id: str, ceiling: int) -> dict:
    record = harness.submission(run_id)
    record["max_run_event_budget_bytes"] = ceiling
    _rewrite_submission(harness.run_dir(run_id), record)
    return record


def test_a_duplicate_is_refused_when_the_stored_ceiling_is_out_of_domain(
    tmp_path: Path,
) -> None:
    """No policy can hold it, so no daemon can have admitted under it."""
    impossible = _structural_max() + 1

    async def case():
        caller = caller_for(principal_a())
        payload = _lowered_ceiling_payload()
        harness = Harness(tmp_path, mode="complete")
        try:
            first = await _completed_run(harness, caller, "impossible-1", payload)
            run_id = first["run_id"]
            _restamp_budget(harness, run_id, impossible)
            # The strict classification alone already refuses this one.
            assert (
                admission.classify_submission(
                    harness.run_dir(run_id), run_id=run_id
                ).kind
                is storage.JsonDocumentKind.CORRUPT
            )
        finally:
            await harness.aclose()

        second = Harness(tmp_path, mode="complete")
        try:
            await submit_expecting(
                second, caller, "impossible-1", protocol.SUBMISSION_INDETERMINATE, payload
            )
            assert second.factory.calls == []
            assert second.event_store.create_calls == []
        finally:
            await second.aclose()

    run_async(case())


def test_a_duplicate_is_refused_when_the_stored_ceiling_could_not_admit_it(
    tmp_path: Path,
) -> None:
    """In-domain, positive, and still impossible: it would have refused this Run.

    The digest proves which request this record is about, and that request's
    sealed limits need more ledger bytes than the stored policy allows. A
    daemon under that policy would have refused the submission, so the record
    cannot be the acceptance it claims to be.
    """
    product = 4096 * 100

    async def case():
        caller = caller_for(principal_a())
        payload = _lowered_ceiling_payload()
        harness = Harness(tmp_path, mode="complete")
        try:
            first = await _completed_run(harness, caller, "impossible-2", payload)
            run_id = first["run_id"]
            _restamp_budget(harness, run_id, product - 1)
            # Well-formed by every field rule — the defect is the pairing.
            assert (
                admission.classify_submission(
                    harness.run_dir(run_id), run_id=run_id
                ).kind
                is storage.JsonDocumentKind.VALID
            )
        finally:
            await harness.aclose()

        second = Harness(tmp_path, mode="complete")
        try:
            await submit_expecting(
                second, caller, "impossible-2", protocol.SUBMISSION_INDETERMINATE, payload
            )
            assert second.factory.calls == []
            assert second.event_store.create_calls == []
        finally:
            await second.aclose()

    run_async(case())


@pytest.mark.parametrize(
    "stored,current",
    [
        pytest.param(4096 * 100, 4096 * 100 - 1, id="historical-above-current"),
        pytest.param(4096 * 100, 8 * FOUR_GIB, id="historical-below-current"),
        pytest.param(4096 * 100, 4096 * 100, id="historical-equals-current"),
    ],
)
def test_a_duplicate_is_accepted_when_the_stored_ceiling_did_admit_it(
    tmp_path: Path, stored: int, current: int
) -> None:
    """Positive control: a legitimate historical ceiling still resolves.

    The stored value is exactly what admitted the Run, and it keeps doing so
    whatever the daemon runs under now — that is the durable-first contract.
    """

    async def case():
        caller = caller_for(principal_a())
        payload = _lowered_ceiling_payload()
        first_daemon = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(stored)
        )
        try:
            first = await _completed_run(first_daemon, caller, "possible-1", payload)
            run_id = first["run_id"]
            assert first_daemon.submission(run_id)["max_run_event_budget_bytes"] == stored
        finally:
            await first_daemon.aclose()

        second = Harness(tmp_path, mode="complete", event_budget_policy=_policy(current))
        try:
            assert await second.submit(caller, "possible-1", payload) == first
            assert second.factory.calls == []
        finally:
            await second.aclose()

    run_async(case())


def test_a_duplicate_is_accepted_at_the_exact_structural_maximum(
    tmp_path: Path,
) -> None:
    """Positive control at the widest ceiling the producer can hold."""

    async def case():
        caller = caller_for(principal_a())
        payload = _lowered_ceiling_payload()
        harness = Harness(
            tmp_path, mode="complete", event_budget_policy=_policy(_structural_max())
        )
        try:
            first = await _completed_run(harness, caller, "possible-2", payload)
            run_id = first["run_id"]
            record = harness.submission(run_id)
            assert record["max_run_event_budget_bytes"] == _structural_max()
            assert (
                admission.classify_submission(
                    harness.run_dir(run_id), run_id=run_id
                ).kind
                is storage.JsonDocumentKind.VALID
            )
        finally:
            await harness.aclose()

        second = Harness(tmp_path, mode="complete")
        try:
            assert await second.submit(caller, "possible-2", payload) == first
            assert second.factory.calls == []
        finally:
            await second.aclose()

    run_async(case())

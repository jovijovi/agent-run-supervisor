"""Slice 3c — durable idempotent admission (plan §5/§6, tests §15 slice 3c).

Everything here drives the sanctioned seams only: `admission` unit contracts
(derivation, digest, submission artifact) and the full submit handshake at
the `ArsdHandlers` handler seam with injected deterministic fakes for
fault/concurrency shaping. Fakes are test-only and never acceptance
evidence. No socket, no real AGENT, no acpx.
"""

from __future__ import annotations

import asyncio
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
        "profile_id": "fake-agent-1.0",
        "session_reuse": "reuse",
        "ars_session_id": "sess-arsd-1",
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
            storage.write_once_json(
                self.prepared_handle.run_dir / "result.json",
                {
                    "run_id": self.prepared_handle.run_id,
                    "status": "completed",
                    "retryable": False,
                },
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
    material = {
        "digest_schema_version": admission.DIGEST_SCHEMA_VERSION,
        "request": dataclasses.asdict(command.request),
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
    ("profile_id", {"request": valid_wire_request(profile_id="fake-agent-2.0")}),
    ("session_reuse", {"request": valid_wire_request(session_reuse="none")}),
    ("ars_session_id", {"request": valid_wire_request(ars_session_id="sess-arsd-2")}),
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
    ("schema_version", {"request": valid_wire_request(schema_version=2)}),
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
            assert set(reply) == {"run_id", "accepted_at"}
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
                submit_payload(request=valid_wire_request(ars_session_id="sess-b")),
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
                    ars_session_id="sess-other-1",
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
                        request=valid_wire_request(ars_session_id="sess-race-a")
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
                        request=valid_wire_request(ars_session_id="sess-race-b")
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
                    request=valid_wire_request(ars_session_id="sess-cancel-1")
                ),
            )
        assert not harness.run_dir(cancelled_id).exists()
        assert harness.factory.calls == []
        monkeypatch.setattr(admission, "prepare_run", real_prepare)
        later = await harness.submit(
            caller,
            "cancel-res-2",
            submit_payload(
                request=valid_wire_request(ars_session_id="sess-cancel-2")
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
                "session_reuse": "reuse",
                "ars_session_id": "sess-arsd-1",
                "profile_id": "fake-agent-1.0",
                "request_digest": digest.value,
                "prompt_sha256": digest.prompt_sha256,
                "prompt_bytes": digest.prompt_bytes,
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
            "session_reuse": "reuse",
            "ars_session_id": "sess-arsd-1",
            "profile_id": "fake-agent-1.0",
            "request_digest": digest.value,
            "prompt_sha256": digest.prompt_sha256,
            "prompt_bytes": digest.prompt_bytes,
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
    "session_reuse",
    "ars_session_id",
    "profile_id",
    "request_digest",
    "prompt_sha256",
    "prompt_bytes",
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
            assert submission["session_reuse"] == "reuse"
            assert submission["ars_session_id"] == "sess-arsd-1"
            assert submission["profile_id"] == "fake-agent-1.0"
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
    import sys

    from agent_run_supervisor.native_acp import profile as profile_module
    from agent_run_supervisor.native_acp.profile import AgentProfile, ProfileRegistry
    from agent_run_supervisor.native_acp.run_task import NativeRunTaskError, RunTask

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
    event_store = storage.native_event_store(root)
    run_id = derived("principal-a", "req-prod-1")
    handle = admission.prepare_run(event_store, run_id)
    factory = handlers.default_run_task_factory(root, registry=registry)
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

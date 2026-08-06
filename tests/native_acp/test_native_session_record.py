"""C6: native session identity, quarantine, quarantine-atomic lease, and the
zero-migration byte-identity proof for legacy acpx records."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_run_supervisor import session_inspect
from agent_run_supervisor.process_liveness import ProcessIdentity
from agent_run_supervisor.session import (
    SESSION_JSON,
    QUARANTINE_DISPATCH_OBSERVATION_LOST,
    SessionBindingError,
    SessionLockError,
    SessionQuarantinedError,
    SessionRecord,
    SessionRecordInvalidError,
    SessionStore,
    _json_bytes,
    _record_to_dict,
    read_native_session_record,
    validate_native_binding,
    validate_native_session_record,
)

T0 = dt.datetime(2026, 7, 21, 0, 0, 0, tzinfo=dt.timezone.utc)

# Golden bytes for the legacy acpx record shape. Re-baselined at the Session
# no-close model: the lifecycle ``state`` key is **deleted**, so these bytes
# deliberately differ from the pre-change ones by exactly that key. Everything
# else about a legacy record is byte-for-byte unchanged, which is what these
# goldens still prove — no other key moved, gained a null, or reordered.
LEGACY_GOLDEN_MINIMAL = (
    b'{\n  "acpx_session_id": null,\n  "acpx_version": "0.12.0",\n  "adapter_agent": "codex",\n'
    b'  "created_at": "2026-07-21T00:00:00+00:00",\n  "effective_cwd": "/work/project",\n'
    b'  "matched_root": "/work",\n  "policy_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",\n'
    b'  "role_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n'
    b'  "role_id": "doc-check",\n  "schema_version": 1,\n  "session_id": "legacy-sess-1",\n'
    b'  "session_name": null,\n  "updated_at": "2026-07-21T00:00:00+00:00",\n'
    b'  "workspace_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"\n}'
)
LEGACY_GOLDEN_WITH_MCP = (
    b'{\n  "acpx_session_id": "acpx-abc",\n  "acpx_version": "0.12.0",\n  "adapter_agent": "codex",\n'
    b'  "created_at": "2026-07-21T00:00:00+00:00",\n  "effective_cwd": "/work/project",\n'
    b'  "matched_root": null,\n  "mcp_config_path": "/work/mcp.json",\n'
    b'  "mcp_config_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",\n'
    b'  "policy_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",\n'
    b'  "role_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n'
    b'  "role_id": "doc-check",\n  "schema_version": 1,\n  "session_id": "legacy-sess-2",\n'
    b'  "session_name": "named",\n  "updated_at": "2026-07-21T01:00:00+00:00",\n'
    b'  "workspace_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"\n}'
)

NATIVE_KWARGS = dict(
    agent_session_id="external-xyz",
    profile_id="opencode-1.18.4",
    profile_revision=1,
    profile_hash="e" * 64,
    owner="hermes",
    namespace="hermes/doc-check",
    workspace_hash="b" * 64,
    effective_cwd="/work/project",
    matched_root="/work",
)


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(base_dir=tmp_path / "native-root")


def _native(store: SessionStore, session_id: str = "native-1") -> SessionRecord:
    return store.create_native_session(session_id=session_id, now=T0, **NATIVE_KWARGS)


def _profile(**overrides) -> SimpleNamespace:
    values = dict(profile_id="opencode-1.18.4", revision=1, profile_hash="e" * 64)
    values.update(overrides)
    return SimpleNamespace(**values)


def _workspace(workspace_hash: str = "b" * 64) -> SimpleNamespace:
    return SimpleNamespace(workspace_hash=workspace_hash)


def _identity() -> ProcessIdentity:
    return ProcessIdentity(pid=4242, process_start="123", boot_id="boot", host="testhost")


# -- zero-migration byte identity ------------------------------------------


def test_legacy_records_serialize_byte_identical() -> None:
    minimal = SessionRecord(
        schema_version=1,
        session_id="legacy-sess-1",
        role_id="doc-check",
        role_hash="a" * 64,
        workspace_hash="b" * 64,
        policy_hash="c" * 64,
        acpx_version="0.12.0",
        adapter_agent="codex",
        effective_cwd="/work/project",
        matched_root="/work",
        created_at="2026-07-21T00:00:00+00:00",
        updated_at="2026-07-21T00:00:00+00:00",
        acpx_session_id=None,
        session_name=None,
    )
    assert _json_bytes(_record_to_dict(minimal)) == LEGACY_GOLDEN_MINIMAL

    with_mcp = SessionRecord(
        schema_version=1,
        session_id="legacy-sess-2",
        role_id="doc-check",
        role_hash="a" * 64,
        workspace_hash="b" * 64,
        policy_hash="c" * 64,
        acpx_version="0.12.0",
        adapter_agent="codex",
        effective_cwd="/work/project",
        matched_root=None,
        created_at="2026-07-21T00:00:00+00:00",
        updated_at="2026-07-21T01:00:00+00:00",
        acpx_session_id="acpx-abc",
        session_name="named",
        mcp_config_path="/work/mcp.json",
        mcp_config_sha256="d" * 64,
    )
    assert _json_bytes(_record_to_dict(with_mcp)) == LEGACY_GOLDEN_WITH_MCP


# -- native creation contract ----------------------------------------------


def test_create_native_session_round_trip_and_exact_key_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _native(store)
    assert record.session_kind == "native"
    assert record.quarantine is None
    # Creation is atomic and fully bound: the external id is there from the
    # record's first byte, and no second write completes it.
    assert record.agent_session_id == "external-xyz"

    raw = json.loads(
        (store.base_dir / "native-1" / "session.json").read_text(encoding="utf-8")
    )
    # No legacy role/policy/acpx key, no sentinel, no null-valued native key.
    assert set(raw) == {
        "schema_version",
        "session_id",
        "workspace_hash",
        "effective_cwd",
        "matched_root",
        "created_at",
        "updated_at",
        "session_kind",
        "native_profile_id",
        "native_profile_revision",
        "native_profile_hash",
        "agent_session_id",
        "owner",
        "namespace",
    }
    assert "state" not in raw
    assert "quarantine" not in raw
    assert None not in raw.values()

    reopened = store.open_session("native-1")
    assert reopened == record


def test_creation_requires_the_external_session_id(tmp_path: Path) -> None:
    """There is no unbound record, so there is nothing to bind later."""
    store = _store(tmp_path)
    for missing in (None, "", 17):
        with pytest.raises((SessionBindingError, TypeError)):
            store.create_native_session(
                session_id="native-unbound",
                agent_session_id=missing,
                **{**NATIVE_KWARGS, "now": T0},
            )
    assert not (store.base_dir / "native-unbound").exists()


def test_no_separate_binding_api_survives() -> None:
    """A second write to complete a record is exactly what cannot exist."""
    from agent_run_supervisor.native_acp import storage as storage_module

    assert not hasattr(SessionStore, "bind_agent_session")
    assert not hasattr(storage_module, "bind_agent_session")
    assert not hasattr(storage_module, "to_native_state")
    assert not hasattr(storage_module, "to_persisted_state")


def test_native_omit_when_unset_grows_with_observations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    store.commit_last_effective(
        "native-1", model="kimi-for-coding/k3", effort="max", now=T0
    )
    raw = json.loads(
        (store.base_dir / "native-1" / "session.json").read_text(encoding="utf-8")
    )
    assert raw["agent_session_id"] == "external-xyz"
    assert raw["last_effective_model"] == "kimi-for-coding/k3"
    assert raw["last_effective_effort"] == "max"
    assert "quarantine" not in raw
    assert "role_id" not in raw
    assert None not in raw.values()
    record = store.open_session("native-1")
    assert record.last_effective_model == "kimi-for-coding/k3"
    assert record.last_effective_effort == "max"


# -- native binding validation ----------------------------------------------


def test_validate_native_binding_matrix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    record = store.open_session("native-1")

    validate_native_binding(
        record,
        profile=_profile(),
        workspace_result=_workspace(),
        owner="hermes",
        namespace="hermes/doc-check",
    )

    for bad_profile in (
        _profile(profile_id="other-agent-1.0"),
        _profile(revision=2),
        _profile(profile_hash="f" * 64),
    ):
        with pytest.raises(SessionBindingError):
            validate_native_binding(
                record,
                profile=bad_profile,
                workspace_result=_workspace(),
                owner="hermes",
                namespace="hermes/doc-check",
            )

    with pytest.raises(SessionBindingError):
        validate_native_binding(
            record,
            profile=_profile(),
            workspace_result=_workspace("9" * 64),
            owner="hermes",
            namespace="hermes/doc-check",
        )
    with pytest.raises(SessionBindingError):
        validate_native_binding(
            record,
            profile=_profile(),
            workspace_result=_workspace(),
            owner="intruder",
            namespace="hermes/doc-check",
        )
    with pytest.raises(SessionBindingError):
        validate_native_binding(
            record,
            profile=_profile(),
            workspace_result=_workspace(),
            owner="hermes",
            namespace="hermes/other",
        )

    # The session/load path requires the committed external id, and a record
    # created by this store always has one — an unbound record cannot exist.
    assert record.agent_session_id == "external-xyz"
    validate_native_binding(
        record,
        profile=_profile(),
        workspace_result=_workspace(),
        owner="hermes",
        namespace="hermes/doc-check",
        for_load=True,
    )
    # The gate is still real: a record whose external id went missing is
    # refused for load rather than silently re-created.
    with pytest.raises(SessionBindingError):
        validate_native_binding(
            replace(record, agent_session_id=None),
            profile=_profile(),
            workspace_result=_workspace(),
            owner="hermes",
            namespace="hermes/doc-check",
            for_load=True,
        )

    # model/effort deltas are never a binding mismatch: a new Run's frozen
    # Spec is the legitimate switching input.
    store.commit_last_effective("native-1", model="a/b", effort="low", now=T0)
    validate_native_binding(
        store.open_session("native-1"),
        profile=_profile(),
        workspace_result=_workspace(),
        owner="hermes",
        namespace="hermes/doc-check",
        for_load=True,
    )


def test_validate_native_binding_refuses_quarantined(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-9", now=T0)
    with pytest.raises(SessionQuarantinedError):
        validate_native_binding(
            store.open_session("native-1"),
            profile=_profile(),
            workspace_result=_workspace(),
            owner="hermes",
            namespace="hermes/doc-check",
        )


# -- quarantine + lease atomicity -------------------------------------------


def test_quarantine_is_irreversible_and_refuses_reuse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    first = store.mark_quarantined(
        "native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-9", now=T0
    )
    assert first.quarantine == {
        "reason_code": QUARANTINE_DISPATCH_OBSERVATION_LOST,
        "source_run_id": "run-9",
        "recorded_at": T0.isoformat(),
    }

    # Idempotent-safe repeat: first fact wins, no error.
    again = store.mark_quarantined(
        "native-1", reason_code="UNTRUSTED_TERMINAL_EVIDENCE", run_id="run-10", now=T0
    )
    assert again.quarantine == first.quarantine

    record = store.open_session("native-1")
    with pytest.raises(SessionQuarantinedError):
        SessionStore.ensure_usable(record)
    # A quarantined Session still exists and stays queryable — it simply
    # refuses new work. There is no operation that un-quarantines it.
    assert store.open_session("native-1").session_id == "native-1"
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock("native-1", "owner", refuse_quarantined=True, now=T0)
    assert not (store.base_dir / "native-1" / "lock.json").exists()


def test_lease_race_quarantine_first_never_mints_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-1", now=T0)
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock("native-1", "owner", refuse_quarantined=True, now=T0)
    assert not (store.base_dir / "native-1" / "lock.json").exists()


def test_lease_race_lock_first_keeps_holder_lease_valid(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    lock = store.acquire_lock(
        "native-1",
        "runtask",
        refuse_quarantined=True,
        reclaimable=False,
        now=T0,
    )
    # Quarantine commits while the lease is held; it never unlinks lock.json.
    store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-2", now=T0)
    assert (store.base_dir / "native-1" / "lock.json").exists()
    # The quarantining finalizer's own lease stays valid for finalization
    # writes (holder update + release).
    store.update_lock_holder(
        "native-1",
        lock.token,
        identity=_identity(),
        holder_kind="native_agent",
        reclaimable=False,
        now=T0,
    )
    store.release_lock("native-1", lock.token)
    # Every later acquire refuses: no usable new lease on a quarantined record.
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock("native-1", "owner", refuse_quarantined=True, now=T0)


def test_expired_lock_reclamation_cannot_bypass_quarantine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    store.acquire_lock(
        "native-1", "runtask", refuse_quarantined=True, lease_seconds=1, now=T0
    )
    store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-3", now=T0)
    later = T0 + dt.timedelta(hours=2)
    lock_path = store.base_dir / "native-1" / "lock.json"
    before = lock_path.read_bytes()
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock("native-1", "other", refuse_quarantined=True, now=later)
    # The refusal neither created nor unlinked any lock: the expired lease
    # file is byte-identical.
    assert lock_path.read_bytes() == before


def test_refuse_quarantined_false_preserves_legacy_acquire_behavior(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _native(store, "native-q")
    store.mark_quarantined("native-q", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-0", now=T0)
    # Legacy default: acquire_lock reads no Session fact at all, so an acpx
    # call site keeps acquiring exactly as before.
    lock = store.acquire_lock("native-q", "legacy-owner", now=T0)
    store.release_lock("native-q", lock.token)
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock(
            "native-q", "native-owner", refuse_quarantined=True, now=T0
        )


# -- legacy inspection surface + last-effective ------------------------------


def test_inspection_reports_quarantine_rather_than_a_state(tmp_path: Path) -> None:
    """The inspection surface projects the one durable refusal, not a state."""
    store = _store(tmp_path)
    _native(store)
    assert session_inspect._read_quarantined(store, "native-1") is False
    store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-4", now=T0)
    assert session_inspect._read_quarantined(store, "native-1") is True


def test_commit_last_effective_updates_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    later = T0 + dt.timedelta(minutes=5)
    updated = store.commit_last_effective(
        "native-1", model="kimi-for-coding/k3", effort="max", now=later
    )
    assert updated.last_effective_model == "kimi-for-coding/k3"
    assert updated.last_effective_effort == "max"
    assert updated.updated_at == later.isoformat()
    reread = store.open_session("native-1")
    assert reread.last_effective_model == "kimi-for-coding/k3"
    assert reread.last_effective_effort == "max"

    store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-5", now=later)
    with pytest.raises(SessionQuarantinedError):
        store.commit_last_effective("native-1", model="a/b", effort="low", now=later)


def test_r5_b3_quarantine_pending_fence_blocks_acquire_even_after_ttl(
    tmp_path: Path,
) -> None:
    import datetime as dt

    from agent_run_supervisor.session import (
        QUARANTINE_PENDING_JSON,
        SessionQuarantinedError,
    )

    store = _store(tmp_path)
    _native(store)
    lock = store.acquire_lock(
        "native-1", "owner", refuse_quarantined=True, now=T0, lease_seconds=1
    )
    assert lock.token
    store.write_quarantine_pending(
        "native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-fence", now=T0
    )
    session_dir = tmp_path / "native-root" / "native-1"
    assert (session_dir / QUARANTINE_PENDING_JSON).is_file()
    assert store.open_session("native-1").quarantine is None
    later = T0 + dt.timedelta(seconds=3600)
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock(
            "native-1", "other", refuse_quarantined=True, now=later
        )
    # Successful quarantine clears fence.
    store.mark_quarantined(
        "native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-fence", now=later
    )
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    assert store.open_session("native-1").quarantine is not None


def test_r5_b3_mark_quarantined_clears_preexisting_fence_when_already_quarantined(
    tmp_path: Path,
) -> None:
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    store = _store(tmp_path)
    _native(store)
    store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-1", now=T0)
    session_dir = tmp_path / "native-root" / "native-1"
    # Simulate a stale fence left beside an already-quarantined record.
    (session_dir / QUARANTINE_PENDING_JSON).write_text(
        '{"schema":"ars.quarantine_pending","version":1,"run_id":"run-1",'
        '"reason":"stale","timestamp":"t"}',
        encoding="utf-8",
    )
    again = store.mark_quarantined(
        "native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-2", now=T0
    )
    assert again.quarantine["source_run_id"] == "run-1"  # first fact wins
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()


def test_r6_b2_write_quarantine_pending_uses_durable_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    from agent_run_supervisor.event_store import EventStoreError
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    store = _store(tmp_path)
    _native(store)
    real_fsync = os.fsync
    session_dir = (tmp_path / "native-root" / "native-1").resolve()

    def boom_parent(fd: int) -> None:
        st = os.fstat(fd)
        if (
            session_dir.exists()
            and stat.S_ISDIR(st.st_mode)
            and st.st_ino == session_dir.stat().st_ino
        ):
            raise OSError(5, "injected fence parent fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom_parent)
    with pytest.raises(EventStoreError):
        store.write_quarantine_pending(
            "native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-x", now=T0
        )


def test_r6_b2_mark_quarantined_clears_fence_only_after_state_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    from agent_run_supervisor.event_store import EventStoreError
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON, SESSION_JSON

    store = _store(tmp_path)
    _native(store)
    store.write_quarantine_pending(
        "native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-x", now=T0
    )
    session_dir = (tmp_path / "native-root" / "native-1").resolve()
    fence = session_dir / QUARANTINE_PENDING_JSON
    assert fence.is_file()
    order: list[str] = []
    real_replace = os.replace
    real_unlink = os.unlink
    real_fsync = os.fsync

    def tracking_replace(src, dst):
        dst_path = Path(dst)
        if dst_path.name == SESSION_JSON:
            order.append("session_replace")
        elif dst_path.name == QUARANTINE_PENDING_JSON:
            order.append("fence_replace")
        else:
            order.append("other_replace")
        return real_replace(src, dst)

    def tracking_unlink(p):
        if Path(p).name == QUARANTINE_PENDING_JSON:
            order.append("fence_unlink")
        else:
            order.append("other_unlink")
        return real_unlink(p)

    def tracking_fsync(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode) and st.st_ino == session_dir.stat().st_ino:
            order.append("session_dir_fsync")
        return real_fsync(fd)

    monkeypatch.setattr(os, "replace", tracking_replace)
    monkeypatch.setattr(os, "unlink", tracking_unlink)
    monkeypatch.setattr(os, "fsync", tracking_fsync)
    store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-x", now=T0)
    assert store.open_session("native-1").quarantine is not None
    assert not fence.exists()
    assert "session_replace" in order
    assert "fence_unlink" in order
    assert order.index("session_replace") < order.index("fence_unlink")


def test_r6_b2_mark_quarantined_session_fsync_failure_keeps_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    from agent_run_supervisor.event_store import EventStoreError
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON, SESSION_JSON

    store = _store(tmp_path)
    _native(store)
    store.write_quarantine_pending(
        "native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-x", now=T0
    )
    session_dir = (tmp_path / "native-root" / "native-1").resolve()
    real_fsync = os.fsync
    state = {"session_file_fsync_done": False}

    def boom_after_session_file(fd: int) -> None:
        st = os.fstat(fd)
        if not state["session_file_fsync_done"] and stat.S_ISREG(st.st_mode):
            # First regular-file fsync during mark is the session.json temp.
            state["session_file_fsync_done"] = True
            raise OSError(5, "injected session file fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom_after_session_file)
    with pytest.raises(EventStoreError):
        store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-x", now=T0)
    assert (session_dir / QUARANTINE_PENDING_JSON).is_file()
    assert store.open_session("native-1").quarantine is None


def test_r6_b2_fence_clear_unlink_parent_fsync_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    from agent_run_supervisor.event_store import EventStoreError
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    store = _store(tmp_path)
    _native(store)
    store.write_quarantine_pending(
        "native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-x", now=T0
    )
    session_dir = (tmp_path / "native-root" / "native-1").resolve()
    real_fsync = os.fsync
    real_unlink = os.unlink
    unlinked = {"n": 0}

    def tracking_unlink(p):
        result = real_unlink(p)
        if Path(p).name == QUARANTINE_PENDING_JSON:
            unlinked["n"] += 1
        return result

    def boom_after_fence_unlink(fd: int) -> None:
        st = os.fstat(fd)
        if (
            unlinked["n"]
            and session_dir.exists()
            and stat.S_ISDIR(st.st_mode)
            and st.st_ino == session_dir.stat().st_ino
        ):
            raise OSError(5, "injected fence unlink parent fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "unlink", tracking_unlink)
    monkeypatch.setattr(os, "fsync", boom_after_fence_unlink)
    with pytest.raises(EventStoreError):
        store.mark_quarantined("native-1", reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST, run_id="run-x", now=T0)
    # Session may already be quarantined on disk; fence clear durability failed.
    assert store.open_session("native-1").quarantine is not None


# ---------------------------------------------------------------------------
# The directory name is not identity (B1 / B4 shared record gate)
# ---------------------------------------------------------------------------


def test_a_record_validates_against_the_id_it_was_requested_under(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _native(store, "native-1")
    validate_native_session_record(record, expected_session_id="native-1")
    assert read_native_session_record(store, "native-1") is not None


def test_a_conflicting_internal_session_id_is_not_a_readable_record(
    tmp_path: Path,
) -> None:
    """A structurally valid record inside the requested directory is refused
    when its own ``session_id`` names a different Session."""
    store = _store(tmp_path)
    _native(store, "native-1")
    path = Path(store.base_dir) / "native-1" / SESSION_JSON
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session_id"] = "native-elsewhere"
    path.write_bytes(json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"))

    # The raw record still parses — this is a conflict, not corruption.
    assert store.open_session("native-1").session_id == "native-elsewhere"
    # …but neither gate accepts it.
    with pytest.raises(SessionRecordInvalidError) as err:
        validate_native_session_record(
            store.open_session("native-1"), expected_session_id="native-1"
        )
    assert "session_id_conflict" in str(err.value)
    assert read_native_session_record(store, "native-1") is None

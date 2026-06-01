"""S1b session store + lock foundation.

These tests pin the persistent-session *foundation* only — the on-disk store,
its binding contract, and a lease-based lock. They deliberately do NOT launch a
real acpx persistent session; that runtime is out of scope for S1b.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.policy import policy_hash
from agent_run_supervisor.process_liveness import LivenessProbe, ProcessIdentity
from agent_run_supervisor.role import AgentRoleSpec, load_role, role_hash
from agent_run_supervisor.session import (
    InvalidSessionIdError,
    SessionBindingError,
    SessionClosedError,
    SessionExistsError,
    SessionLockError,
    SessionNotFoundError,
    SessionStore,
)
from agent_run_supervisor.workspace import validate_effective_cwd, workspace_hash

UTC = timezone.utc
T0 = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 5, 30, 13, 0, 0, tzinfo=UTC)


def _mode_octal(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _role_at(valid_role_dict: dict[str, Any], work: Path, **overrides: Any) -> AgentRoleSpec:
    payload = dict(valid_role_dict)
    payload["workspace"] = dict(valid_role_dict["workspace"])
    payload["workspace"]["default_cwd"] = str(work)
    payload["workspace"]["allowed_roots"] = [str(work)]
    payload["workspace"]["allowed_roots_security_boundary"] = False
    payload["session"] = {"strategy": "persistent"}
    for key, value in overrides.items():
        payload[key] = value
    return load_role(payload)


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture()
def role(valid_role_dict: dict[str, Any], work_dir: Path) -> AgentRoleSpec:
    return _role_at(valid_role_dict, work_dir)


@pytest.fixture()
def workspace(role: AgentRoleSpec):
    return validate_effective_cwd(role, override=None)


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(base_dir=tmp_path / "sessions")


# --- session directory + artifact security -------------------------------


def test_create_session_creates_0700_dir_and_0600_json(store, role, workspace) -> None:
    record = store.create_session(
        session_id="sess-alpha", role=role, workspace_result=workspace, now=T0
    )

    session_dir = store.base_dir / "sess-alpha"
    assert session_dir.is_dir()
    assert _mode_octal(session_dir) == 0o700
    json_path = session_dir / "session.json"
    assert json_path.exists()
    assert _mode_octal(json_path) == 0o600
    assert record.session_id == "sess-alpha"


def test_create_session_atomic_write_leaves_no_tmp_files(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-atomic", role=role, workspace_result=workspace, now=T0
    )

    session_dir = store.base_dir / "sess-atomic"
    names = [p.name for p in session_dir.iterdir()]
    assert "session.json" in names
    assert not any(n.startswith(".tmp") or n.endswith(".tmp") for n in names)


def test_create_session_persists_binding_and_state(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-bind", role=role, workspace_result=workspace, now=T0
    )

    data = json.loads(
        (store.base_dir / "sess-bind" / "session.json").read_text(encoding="utf-8")
    )
    assert data["session_id"] == "sess-bind"
    assert data["role_id"] == role.role_id
    assert data["role_hash"] == role_hash(role)
    assert data["policy_hash"] == policy_hash(role)
    assert data["workspace_hash"] == workspace_hash(role, workspace)
    assert data["acpx_version"] == role.runner.acpx_version
    assert data["adapter_agent"] == role.runner.adapter_agent
    assert data["effective_cwd"] == str(workspace.effective_cwd)
    assert data["matched_root"] == str(workspace.matched_root)
    assert data["state"] == "open"
    assert data["created_at"] == T0.isoformat()
    assert data["updated_at"] == T0.isoformat()


def test_create_session_persists_optional_acpx_session_and_name(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-opt",
        role=role,
        workspace_result=workspace,
        acpx_session_id="acpx-123",
        session_name="nightly-review",
        now=T0,
    )

    data = json.loads(
        (store.base_dir / "sess-opt" / "session.json").read_text(encoding="utf-8")
    )
    assert data["acpx_session_id"] == "acpx-123"
    assert data["session_name"] == "nightly-review"


def test_create_session_optional_fields_default_to_null(store, role, workspace) -> None:
    record = store.create_session(
        session_id="sess-default", role=role, workspace_result=workspace, now=T0
    )
    assert record.acpx_session_id is None
    assert record.session_name is None


def test_create_session_refuses_duplicate(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-dup", role=role, workspace_result=workspace, now=T0
    )
    with pytest.raises(SessionExistsError):
        store.create_session(
            session_id="sess-dup", role=role, workspace_result=workspace, now=T0
        )


def test_create_session_is_exclusive_under_concurrency(store, role, workspace) -> None:
    barrier = threading.Barrier(2)

    def create_from_thread(index: int) -> str:
        barrier.wait(timeout=5)
        try:
            store.create_session(
                session_id="sess-concurrent",
                role=role,
                workspace_result=workspace,
                session_name=f"thread-{index}",
                now=T0,
            )
            return "created"
        except SessionExistsError:
            return "exists"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create_from_thread, range(2)))

    assert sorted(results) == ["created", "exists"]
    data = json.loads(
        (store.base_dir / "sess-concurrent" / "session.json").read_text(
            encoding="utf-8"
        )
    )
    assert data["session_name"] in {"thread-0", "thread-1"}


@pytest.mark.parametrize(
    "bad_id",
    [
        "../escape",
        "..",
        ".",
        "a/b",
        "a\\b",
        "with space",
        "",
        "sess/..",
        ".hidden",
        "tab\tname",
    ],
)
def test_create_session_rejects_unsafe_ids(store, role, workspace, bad_id: str) -> None:
    with pytest.raises(InvalidSessionIdError):
        store.create_session(
            session_id=bad_id, role=role, workspace_result=workspace, now=T0
        )
    # Fail closed: a rejected id must not create any directory.
    assert not store.base_dir.exists() or list(store.base_dir.iterdir()) == []


# --- open / round-trip ----------------------------------------------------


def test_open_session_round_trips_record(store, role, workspace) -> None:
    created = store.create_session(
        session_id="sess-rt", role=role, workspace_result=workspace, now=T0
    )

    loaded = store.open_session("sess-rt")

    assert loaded == created


def test_open_session_missing_raises(store) -> None:
    with pytest.raises(SessionNotFoundError):
        store.open_session("sess-missing")


# --- binding validation (must refuse before mutation) ---------------------


def test_validate_binding_accepts_matching_role(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-ok", role=role, workspace_result=workspace, now=T0
    )
    record = store.open_session("sess-ok")

    # Should not raise.
    store.validate_binding(record, role=role, workspace_result=workspace)


def test_validate_binding_refuses_role_hash_drift(
    store, role, workspace, valid_role_dict, work_dir
) -> None:
    store.create_session(
        session_id="sess-roledrift", role=role, workspace_result=workspace, now=T0
    )
    record = store.open_session("sess-roledrift")
    # Different description -> different role_hash, but same policy/adapter/acpx.
    drifted = _role_at(valid_role_dict, work_dir, description="totally different role")
    drifted_ws = validate_effective_cwd(drifted, override=None)

    with pytest.raises(SessionBindingError) as exc:
        store.validate_binding(record, role=drifted, workspace_result=drifted_ws)
    assert "role" in str(exc.value).lower()


def _tamper(store: SessionStore, session_id: str, **fields: Any) -> None:
    path = store.base_dir / session_id / "session.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    "field, value",
    [
        ("role_hash", "sha256:deadbeef"),
        ("policy_hash", "sha256:deadbeef"),
        ("workspace_hash", "sha256:deadbeef"),
        ("acpx_version", "9.9.9"),
        ("adapter_agent", "rogue-adapter"),
    ],
)
def test_validate_binding_refuses_each_tampered_field(
    store, role, workspace, field: str, value: str
) -> None:
    store.create_session(
        session_id="sess-tamper", role=role, workspace_result=workspace, now=T0
    )
    _tamper(store, "sess-tamper", **{field: value})
    record = store.open_session("sess-tamper")

    with pytest.raises(SessionBindingError):
        store.validate_binding(record, role=role, workspace_result=workspace)


# --- S1d lifecycle state: mark closed / ensure open / list ----------------


def test_mark_closed_transitions_state_and_preserves_created_at(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-close", role=role, workspace_result=workspace, now=T0
    )

    closed = store.mark_closed("sess-close", now=T1)

    assert closed.state == "closed"
    assert closed.created_at == T0.isoformat()
    assert closed.updated_at == T1.isoformat()
    # Persisted to disk and observable through a fresh open.
    data = json.loads(
        (store.base_dir / "sess-close" / "session.json").read_text(encoding="utf-8")
    )
    assert data["state"] == "closed"
    assert data["updated_at"] == T1.isoformat()
    assert store.open_session("sess-close").state == "closed"


def test_mark_closed_writes_atomically_with_0600_and_no_tmp(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-atomic-close", role=role, workspace_result=workspace, now=T0
    )

    store.mark_closed("sess-atomic-close", now=T1)

    session_dir = store.base_dir / "sess-atomic-close"
    assert _mode_octal(session_dir / "session.json") == 0o600
    names = [p.name for p in session_dir.iterdir()]
    assert not any(n.startswith(".tmp") or n.endswith(".tmp") for n in names)


def test_mark_closed_refuses_double_close(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-double", role=role, workspace_result=workspace, now=T0
    )
    store.mark_closed("sess-double", now=T1)

    with pytest.raises(SessionClosedError):
        store.mark_closed("sess-double", now=T1)


def test_mark_closed_requires_existing_session(store) -> None:
    with pytest.raises(SessionNotFoundError):
        store.mark_closed("sess-missing", now=T1)


def test_ensure_open_accepts_open_record(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-open", role=role, workspace_result=workspace, now=T0
    )
    record = store.open_session("sess-open")

    # Should not raise.
    store.ensure_open(record)


def test_ensure_open_refuses_closed_record(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-eo", role=role, workspace_result=workspace, now=T0
    )
    store.mark_closed("sess-eo", now=T1)
    record = store.open_session("sess-eo")

    with pytest.raises(SessionClosedError):
        store.ensure_open(record)


def test_list_records_returns_empty_when_root_missing(tmp_path) -> None:
    store = SessionStore(base_dir=tmp_path / "no-such-sessions")

    assert store.list_records() == []


def test_list_records_lists_all_records_sorted_by_id(store, role, workspace) -> None:
    store.create_session(session_id="sess-b", role=role, workspace_result=workspace, now=T0)
    store.create_session(session_id="sess-a", role=role, workspace_result=workspace, now=T0)
    store.mark_closed("sess-a", now=T1)

    records = store.list_records()

    assert [r.session_id for r in records] == ["sess-a", "sess-b"]
    by_id = {r.session_id: r for r in records}
    assert by_id["sess-a"].state == "closed"
    assert by_id["sess-b"].state == "open"


def test_list_records_ignores_dirs_without_session_json(store, role, workspace) -> None:
    store.create_session(session_id="sess-real", role=role, workspace_result=workspace, now=T0)
    # A stray directory under the sessions root with no session.json is skipped.
    (store.base_dir / "not-a-session").mkdir()
    # A stray file at the root is skipped too.
    (store.base_dir / "stray.txt").write_text("junk", encoding="utf-8")

    records = store.list_records()

    assert [r.session_id for r in records] == ["sess-real"]


# --- lock / lease ---------------------------------------------------------


def test_acquire_lock_creates_lock_json_with_fields(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-lock", role=role, workspace_result=workspace, now=T0
    )

    lock = store.acquire_lock("sess-lock", owner="worker-1", now=T0, lease_seconds=100)

    lock_path = store.base_dir / "sess-lock" / "lock.json"
    assert lock_path.exists()
    assert _mode_octal(lock_path) == 0o600
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert data["owner"] == "worker-1"
    assert data["token"] == lock.token
    assert data["acquired_at"] == T0.isoformat()
    assert data["expires_at"] == (T0 + timedelta(seconds=100)).isoformat()


def test_acquire_lock_requires_existing_session(store) -> None:
    with pytest.raises(SessionNotFoundError):
        store.acquire_lock("no-session", owner="w", now=T0)


def test_acquire_lock_rejects_nonpositive_lease_seconds(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-badlease", role=role, workspace_result=workspace, now=T0
    )

    with pytest.raises(SessionLockError) as exc:
        store.acquire_lock("sess-badlease", owner="worker", now=T0, lease_seconds=0)
    assert "lease_seconds" in str(exc.value)


def test_acquire_lock_blocks_while_unexpired(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-held", role=role, workspace_result=workspace, now=T0
    )
    store.acquire_lock("sess-held", owner="worker-1", now=T0, lease_seconds=100)

    with pytest.raises(SessionLockError):
        store.acquire_lock(
            "sess-held", owner="worker-2", now=T0 + timedelta(seconds=50), lease_seconds=100
        )


def test_acquire_lock_replaces_expired_lock(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-expire", role=role, workspace_result=workspace, now=T0
    )
    first = store.acquire_lock("sess-expire", owner="worker-1", now=T0, lease_seconds=100)

    later = T0 + timedelta(seconds=101)
    second = store.acquire_lock(
        "sess-expire", owner="worker-2", now=later, lease_seconds=100
    )

    assert second.token != first.token
    assert second.owner == "worker-2"
    data = json.loads(
        (store.base_dir / "sess-expire" / "lock.json").read_text(encoding="utf-8")
    )
    assert data["token"] == second.token
    assert data["expires_at"] == (later + timedelta(seconds=100)).isoformat()


def test_acquire_expired_lock_is_serialized_under_concurrency(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-expire-race", role=role, workspace_result=workspace, now=T0
    )
    first = store.acquire_lock(
        "sess-expire-race", owner="worker-1", now=T0, lease_seconds=100
    )
    barrier = threading.Barrier(2)
    later = T0 + timedelta(seconds=101)

    def reacquire_from_thread(index: int) -> tuple[str, str | None]:
        barrier.wait(timeout=5)
        try:
            lock = store.acquire_lock(
                "sess-expire-race",
                owner=f"worker-{index + 2}",
                now=later,
                lease_seconds=100,
            )
            return ("acquired", lock.token)
        except SessionLockError:
            return ("blocked", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reacquire_from_thread, range(2)))

    acquired_tokens = [token for status, token in results if status == "acquired"]
    blocked = [status for status, _ in results if status == "blocked"]
    assert len(acquired_tokens) == 1
    assert len(blocked) == 1

    data = json.loads(
        (store.base_dir / "sess-expire-race" / "lock.json").read_text(encoding="utf-8")
    )
    assert data["token"] == acquired_tokens[0]

    with pytest.raises(SessionLockError):
        store.release_lock("sess-expire-race", token=first.token)
    assert (store.base_dir / "sess-expire-race" / "lock.json").exists()


def test_release_lock_requires_matching_token(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-rel", role=role, workspace_result=workspace, now=T0
    )
    store.acquire_lock("sess-rel", owner="worker-1", now=T0, lease_seconds=100)

    with pytest.raises(SessionLockError):
        store.release_lock("sess-rel", **{"token": "not-the-" + "token"})
    # Wrong token must not remove the lock.
    assert (store.base_dir / "sess-rel" / "lock.json").exists()


def test_release_lock_removes_lock_and_allows_reacquire(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-cycle", role=role, workspace_result=workspace, now=T0
    )
    lock = store.acquire_lock("sess-cycle", owner="worker-1", now=T0, lease_seconds=100)

    store.release_lock("sess-cycle", token=lock.token)
    assert not (store.base_dir / "sess-cycle" / "lock.json").exists()

    # A fresh acquire while the previous lease window is still open now succeeds.
    again = store.acquire_lock(
        "sess-cycle", owner="worker-2", now=T0 + timedelta(seconds=10), lease_seconds=100
    )
    assert again.owner == "worker-2"


def test_release_lock_without_lock_raises(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-nolock", role=role, workspace_result=workspace, now=T0
    )
    with pytest.raises(SessionLockError):
        store.release_lock("sess-nolock", token="any" + "thing")


# --- W4 stale-lock detector (read-only) -----------------------------------


def test_detect_stale_locks_flags_expired_lease(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-stale", role=role, workspace_result=workspace, now=T0
    )
    store.acquire_lock("sess-stale", owner="worker-1", now=T0, lease_seconds=100)

    # now is past the lease expiry -> the lock is provably expired.
    report = store.detect_stale_locks(now=T0 + timedelta(seconds=101))

    entry = {row["session_id"]: row for row in report}["sess-stale"]
    assert entry["state"] == "open"
    assert entry["lock_present"] is True
    assert entry["lease_expired"] is True
    assert entry["tmp_debris"] == []


def test_detect_stale_locks_treats_live_lock_as_not_expired(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-live", role=role, workspace_result=workspace, now=T0
    )
    store.acquire_lock("sess-live", owner="worker-1", now=T0, lease_seconds=100)

    # now is still inside the lease window -> the lock is live, never stale.
    report = store.detect_stale_locks(now=T0 + timedelta(seconds=50))

    entry = {row["session_id"]: row for row in report}["sess-live"]
    assert entry["lock_present"] is True
    assert entry["lease_expired"] is False


def test_detect_stale_locks_reports_record_without_lock(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-nolock2", role=role, workspace_result=workspace, now=T0
    )

    report = store.detect_stale_locks(now=T0)

    entry = {row["session_id"]: row for row in report}["sess-nolock2"]
    assert entry["lock_present"] is False
    assert entry["lease_expired"] is False


def test_detect_stale_locks_lists_tmp_debris(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-debris", role=role, workspace_result=workspace, now=T0
    )
    debris = store.base_dir / "sess-debris" / (".tmp-" + "leftover")
    debris.write_text("partial", encoding="utf-8")

    report = store.detect_stale_locks(now=T0)

    entry = {row["session_id"]: row for row in report}["sess-debris"]
    assert (".tmp-" + "leftover") in entry["tmp_debris"]


def test_detect_stale_locks_is_read_only(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-ro", role=role, workspace_result=workspace, now=T0
    )
    store.acquire_lock("sess-ro", owner="worker-1", now=T0, lease_seconds=100)
    before = (store.base_dir / "sess-ro" / "lock.json").read_text(encoding="utf-8")
    record_before = store.open_session("sess-ro")

    store.detect_stale_locks(now=T0 + timedelta(seconds=500))

    # The detector never removes/replaces the lock or mutates the record.
    assert (store.base_dir / "sess-ro" / "lock.json").exists()
    assert (store.base_dir / "sess-ro" / "lock.json").read_text(encoding="utf-8") == before
    assert store.open_session("sess-ro") == record_before


def test_detect_stale_locks_empty_when_root_missing(tmp_path: Path) -> None:
    missing = SessionStore(base_dir=tmp_path / "no-sessions-here")

    assert missing.detect_stale_locks(now=T0) == []


# --- K1 process-liveness crash recovery -----------------------------------


def _identity(*, host="host-1", pid=111, start="s1", boot="boot-A") -> ProcessIdentity:
    return ProcessIdentity(pid=pid, process_start=start, boot_id=boot, host=host)


def _make_probe(identity: ProcessIdentity, *, running=None, starts=None) -> LivenessProbe:
    """Deterministic probe: ``running``/``starts`` are keyed by recorded PID."""
    running = running or {}
    starts = starts or {}
    return LivenessProbe(
        is_running=lambda pid: running.get(pid, False),
        read_start=lambda pid: starts.get(pid),
        current=lambda: identity,
    )


def _store_as(base_dir: Path, identity: ProcessIdentity, **probe_kwargs) -> SessionStore:
    return SessionStore(
        base_dir=base_dir, liveness_probe=_make_probe(identity, **probe_kwargs)
    )


def test_acquire_lock_records_holder_process_identity(
    tmp_path, role, workspace
) -> None:
    holder = _identity(host="box-A", pid=4242, start="9000", boot="boot-Z")
    store = _store_as(tmp_path / "sessions", holder)
    store.create_session(session_id="sess-id", role=role, workspace_result=workspace, now=T0)

    store.acquire_lock("sess-id", owner="worker-1", now=T0, lease_seconds=100)

    data = json.loads(
        (store.base_dir / "sess-id" / "lock.json").read_text(encoding="utf-8")
    )
    # Existing lease fields are preserved...
    assert data["owner"] == "worker-1"
    assert data["expires_at"] == (T0 + timedelta(seconds=100)).isoformat()
    # ...and the recording process identity is captured for crash recovery.
    assert data["host"] == "box-A"
    assert data["pid"] == 4242
    assert data["process_start"] == "9000"
    assert data["boot_id"] == "boot-Z"


def test_acquire_lock_default_blocks_within_ttl_even_if_holder_crashed(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    crashed = _identity(host="h", pid=111)
    _store_as(base, crashed).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    first = _store_as(base, crashed).acquire_lock(
        "sess-id", owner="dead-worker", now=T0, lease_seconds=100
    )

    # A second process whose probe proves the holder crashed, but WITHOUT opting
    # into reclamation: the default lease contract is unchanged (TTL-only).
    reviver = _store_as(base, _identity(host="h", pid=222), running={111: False})
    with pytest.raises(SessionLockError):
        reviver.acquire_lock(
            "sess-id", owner="new-worker", now=T0 + timedelta(seconds=10), lease_seconds=100
        )

    data = json.loads((base / "sess-id" / "lock.json").read_text(encoding="utf-8"))
    assert data["token"] == first.token  # lock untouched


def test_acquire_lock_reclaims_within_ttl_lease_of_crashed_holder(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    crashed = _identity(host="h", pid=111, start="s1", boot="b")
    _store_as(base, crashed).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    first = _store_as(base, crashed).acquire_lock(
        "sess-id", owner="dead-worker", now=T0, lease_seconds=100
    )

    # PID 111 is no longer running -> the prior holder provably crashed.
    reviver = _store_as(base, _identity(host="h", pid=222, start="s2", boot="b"), running={111: False})
    later = T0 + timedelta(seconds=10)  # still inside the original lease window
    second = reviver.acquire_lock(
        "sess-id", owner="new-worker", now=later, lease_seconds=100, reclaim_crashed=True
    )

    assert second.token != first.token
    assert second.owner == "new-worker"
    data = json.loads((base / "sess-id" / "lock.json").read_text(encoding="utf-8"))
    assert data["token"] == second.token
    assert data["pid"] == 222  # the reviver's identity now owns the lease
    assert data["expires_at"] == (later + timedelta(seconds=100)).isoformat()


def test_acquire_lock_refuses_within_ttl_lease_of_live_holder(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    live = _identity(host="h", pid=111, start="s1", boot="b")
    _store_as(base, live).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    _store_as(base, live).acquire_lock(
        "sess-id", owner="live-worker", now=T0, lease_seconds=100
    )

    # PID 111 is running AND its start time matches -> genuinely alive: refuse
    # even with reclaim_crashed=True (no live-session takeover). Same host+boot
    # so liveness is decided by the PID probe, not a reboot.
    reviver = _store_as(
        base, _identity(host="h", pid=222, boot="b"), running={111: True}, starts={111: "s1"}
    )
    with pytest.raises(SessionLockError):
        reviver.acquire_lock(
            "sess-id",
            owner="intruder",
            now=T0 + timedelta(seconds=10),
            lease_seconds=100,
            reclaim_crashed=True,
        )


def test_acquire_lock_refuses_within_ttl_lease_of_unknown_holder(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    holder = _identity(host="host-A", pid=111, start="s1", boot="b")
    _store_as(base, holder).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    _store_as(base, holder).acquire_lock(
        "sess-id", owner="worker", now=T0, lease_seconds=100
    )

    # A different host cannot validate the holder's PID -> UNKNOWN -> fail safe.
    reviver = _store_as(base, _identity(host="host-B", pid=222), running={111: False})
    with pytest.raises(SessionLockError):
        reviver.acquire_lock(
            "sess-id",
            owner="other-host",
            now=T0 + timedelta(seconds=10),
            lease_seconds=100,
            reclaim_crashed=True,
        )


def test_acquire_lock_reclaim_flag_still_replaces_expired_lease(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    holder = _identity(host="h", pid=111, start="s1", boot="b")
    _store_as(base, holder).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    first = _store_as(base, holder).acquire_lock(
        "sess-id", owner="worker-1", now=T0, lease_seconds=100
    )

    # Past TTL: expired-lease replacement works regardless of liveness, and even
    # when the holder would classify ALIVE (still running, start matches).
    reviver = _store_as(
        base, _identity(host="h", pid=222), running={111: True}, starts={111: "s1"}
    )
    later = T0 + timedelta(seconds=101)
    second = reviver.acquire_lock(
        "sess-id", owner="worker-2", now=later, lease_seconds=100, reclaim_crashed=True
    )
    assert second.token != first.token
    assert second.owner == "worker-2"


def _entry(report: list[dict], session_id: str) -> dict:
    return {row["session_id"]: row for row in report}[session_id]


def test_detect_stale_locks_reports_crashed_holder_as_recoverable(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    crashed = _identity(host="h", pid=111, start="s1", boot="b")
    _store_as(base, crashed).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    _store_as(base, crashed).acquire_lock(
        "sess-id", owner="dead-worker", now=T0, lease_seconds=100
    )

    detector = _store_as(base, _identity(host="h", pid=222, boot="b"), running={111: False})
    report = detector.detect_stale_locks(now=T0 + timedelta(seconds=10))

    entry = _entry(report, "sess-id")
    assert entry["lock_present"] is True
    assert entry["lease_expired"] is False  # still within TTL
    assert entry["holder_liveness"] == "crashed"
    assert entry["recoverable"] is True  # crashed -> safe to reclaim


def test_detect_stale_locks_reports_live_holder_as_not_recoverable(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    live = _identity(host="h", pid=111, start="s1", boot="b")
    _store_as(base, live).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    _store_as(base, live).acquire_lock(
        "sess-id", owner="live-worker", now=T0, lease_seconds=100
    )

    detector = _store_as(
        base, _identity(host="h", pid=222, boot="b"), running={111: True}, starts={111: "s1"}
    )
    report = detector.detect_stale_locks(now=T0 + timedelta(seconds=10))

    entry = _entry(report, "sess-id")
    assert entry["lease_expired"] is False
    assert entry["holder_liveness"] == "alive"
    assert entry["recoverable"] is False


def test_detect_stale_locks_null_liveness_when_no_lock(store, role, workspace) -> None:
    store.create_session(
        session_id="sess-nolock3", role=role, workspace_result=workspace, now=T0
    )

    entry = _entry(store.detect_stale_locks(now=T0), "sess-nolock3")
    assert entry["lock_present"] is False
    assert entry["holder_liveness"] is None
    assert entry["recoverable"] is False


def test_unreclaimable_pending_lock_stays_ttl_only_even_if_holder_crashed(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    pending = _identity(host="h", pid=111, start="s1", boot="b")
    _store_as(base, pending).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    first = _store_as(base, pending).acquire_lock(
        "sess-id",
        owner="pending-supervisor",
        now=T0,
        lease_seconds=100,
        reclaimable=False,
        holder_kind="supervisor_pending_subprocess",
    )

    # Even if the recorded supervisor PID is gone, an unreclaimable pending lock
    # might have spawned a child before holder recording failed. K1 deliberately
    # stays fail-safe: wait for TTL rather than risk taking over live work.
    reviver = _store_as(base, _identity(host="h", pid=222, boot="b"), running={111: False})
    with pytest.raises(SessionLockError):
        reviver.acquire_lock(
            "sess-id",
            owner="new-worker",
            now=T0 + timedelta(seconds=10),
            lease_seconds=100,
            reclaim_crashed=True,
        )

    entry = _entry(reviver.detect_stale_locks(now=T0 + timedelta(seconds=10)), "sess-id")
    assert entry["holder_liveness"] == "unknown"
    assert entry["recoverable"] is False
    data = json.loads((base / "sess-id" / "lock.json").read_text(encoding="utf-8"))
    assert data["token"] == first.token
    assert data["reclaimable"] is False


def _record_supervisor_with_child(base: Path, role, workspace) -> tuple[SessionStore, str]:
    supervisor = _identity(host="h", pid=111, start="s1", boot="b")
    store = _store_as(base, supervisor)
    store.create_session(session_id="sess-id", role=role, workspace_result=workspace, now=T0)
    lock = store.acquire_lock(
        "sess-id",
        owner="runtime",
        now=T0,
        lease_seconds=100,
        reclaimable=False,
        holder_kind="supervisor_pending_subprocess",
    )
    store.update_lock_holder(
        "sess-id",
        lock.token,
        identity=_identity(host="h", pid=333, start="c1", boot="b"),
        holder_kind="subprocess",
        reclaimable=True,
        now=T0 + timedelta(seconds=1),
    )
    return store, lock.token


def test_update_lock_holder_adds_child_identity_without_replacing_supervisor(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    _record_supervisor_with_child(base, role, workspace)

    data = json.loads((base / "sess-id" / "lock.json").read_text(encoding="utf-8"))
    assert data["pid"] == 111
    assert data["process_start"] == "s1"
    assert data["holder_kind"] == "supervisor_with_subprocess"
    assert data["reclaimable"] is True
    assert data["child_pid"] == 333
    assert data["child_process_start"] == "c1"
    assert data["child_holder_kind"] == "subprocess"


def test_child_exit_does_not_reclaim_while_supervisor_is_alive(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    _, token = _record_supervisor_with_child(base, role, workspace)

    detector = _store_as(
        base,
        _identity(host="h", pid=222, boot="b"),
        running={111: True, 333: False},
        starts={111: "s1"},
    )
    entry = _entry(detector.detect_stale_locks(now=T0 + timedelta(seconds=10)), "sess-id")
    assert entry["holder_liveness"] == "alive"
    assert entry["recoverable"] is False
    with pytest.raises(SessionLockError):
        detector.acquire_lock(
            "sess-id",
            owner="new-worker",
            now=T0 + timedelta(seconds=10),
            lease_seconds=100,
            reclaim_crashed=True,
        )
    data = json.loads((base / "sess-id" / "lock.json").read_text(encoding="utf-8"))
    assert data["token"] == token


def test_supervisor_exit_does_not_reclaim_while_child_is_alive(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    _, token = _record_supervisor_with_child(base, role, workspace)

    detector = _store_as(
        base,
        _identity(host="h", pid=222, boot="b"),
        running={111: False, 333: True},
        starts={333: "c1"},
    )
    entry = _entry(detector.detect_stale_locks(now=T0 + timedelta(seconds=10)), "sess-id")
    assert entry["holder_liveness"] == "alive"
    assert entry["recoverable"] is False
    with pytest.raises(SessionLockError):
        detector.acquire_lock(
            "sess-id",
            owner="new-worker",
            now=T0 + timedelta(seconds=10),
            lease_seconds=100,
            reclaim_crashed=True,
        )
    data = json.loads((base / "sess-id" / "lock.json").read_text(encoding="utf-8"))
    assert data["token"] == token


def test_composite_holder_reclaims_only_after_supervisor_and_child_crashed(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    _, token = _record_supervisor_with_child(base, role, workspace)

    reviver = _store_as(
        base,
        _identity(host="h", pid=222, start="s2", boot="b"),
        running={111: False, 333: False},
    )
    later = T0 + timedelta(seconds=10)
    entry = _entry(reviver.detect_stale_locks(now=later), "sess-id")
    assert entry["holder_liveness"] == "crashed"
    assert entry["recoverable"] is True

    second = reviver.acquire_lock(
        "sess-id", owner="new-worker", now=later, lease_seconds=100, reclaim_crashed=True
    )
    assert second.token != token
    data = json.loads((base / "sess-id" / "lock.json").read_text(encoding="utf-8"))
    assert data["token"] == second.token
    assert data["pid"] == 222
    assert "child_pid" not in data


def test_detect_stale_locks_expired_lease_is_recoverable_regardless_of_liveness(
    tmp_path, role, workspace
) -> None:
    base = tmp_path / "sessions"
    live = _identity(host="h", pid=111, start="s1", boot="b")
    _store_as(base, live).create_session(
        session_id="sess-id", role=role, workspace_result=workspace, now=T0
    )
    _store_as(base, live).acquire_lock(
        "sess-id", owner="worker", now=T0, lease_seconds=100
    )

    # Holder is "alive" but the lease has expired: TTL alone makes it recoverable.
    detector = _store_as(
        base, _identity(host="h", pid=222, boot="b"), running={111: True}, starts={111: "s1"}
    )
    entry = _entry(detector.detect_stale_locks(now=T0 + timedelta(seconds=101)), "sess-id")
    assert entry["lease_expired"] is True
    assert entry["holder_liveness"] == "alive"
    assert entry["recoverable"] is True


def test_detect_stale_locks_crashed_path_is_read_only(tmp_path, role, workspace) -> None:
    base = tmp_path / "sessions"
    crashed = _identity(host="h", pid=111, start="s1", boot="b")
    _store_as(base, crashed).create_session(
        session_id="sess-ro2", role=role, workspace_result=workspace, now=T0
    )
    _store_as(base, crashed).acquire_lock(
        "sess-ro2", owner="dead", now=T0, lease_seconds=100
    )
    lock_path = base / "sess-ro2" / "lock.json"
    before = lock_path.read_text(encoding="utf-8")

    detector = _store_as(base, _identity(host="h", pid=222, boot="b"), running={111: False})
    detector.detect_stale_locks(now=T0 + timedelta(seconds=10))

    # Detection of a crashed holder never removes/rewrites the lock or record.
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == before

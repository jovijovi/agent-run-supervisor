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
from agent_run_supervisor.role import AgentRoleSpec, load_role, role_hash
from agent_run_supervisor.session import (
    InvalidSessionIdError,
    SessionBindingError,
    SessionExistsError,
    SessionLockError,
    SessionNotFoundError,
    SessionStore,
)
from agent_run_supervisor.workspace import validate_effective_cwd, workspace_hash

UTC = timezone.utc
T0 = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


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
        store.release_lock("sess-rel", token="not-the-token")
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
        store.release_lock("sess-nolock", token="anything")

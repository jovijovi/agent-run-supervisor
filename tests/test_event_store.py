from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agent_run_supervisor.event_store import (
    DIR_MODE,
    EventStore,
    EventStoreError,
    atomic_write_json,
    exclusive_create_bytes,
    secure_mkdir,
)


def _mode_octal(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_atomic_write_json_writes_0600_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "record.json"

    atomic_write_json(path, {"a": 1, "b": "two"})

    assert _mode_octal(path) == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": "two"}
    assert not any(
        p.name.startswith(".tmp") or p.name.endswith(".tmp")
        for p in path.parent.iterdir()
    )


def test_exclusive_create_bytes_refuses_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"

    exclusive_create_bytes(path, b"{}")
    assert _mode_octal(path) == 0o600

    with pytest.raises(FileExistsError):
        exclusive_create_bytes(path, b"{}")


def test_exclusive_create_bytes_short_write_completes_all_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial os.write must not report success with truncated bytes."""
    path = tmp_path / "nested" / "artifact.bin"
    payload = b"ABCDEFGH"
    real_write = os.write
    state = {"n": 0}

    def short_then_real(fd: int, data: bytes) -> int:
        state["n"] += 1
        if state["n"] == 1 and len(data) > 1:
            return real_write(fd, data[:1])
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", short_then_real)
    exclusive_create_bytes(path, payload)
    assert path.read_bytes() == payload
    assert _mode_octal(path) == 0o600
    assert state["n"] >= 2


def test_exclusive_create_bytes_zero_write_fails_closed_leaves_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "artifact.bin"
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    with pytest.raises(EventStoreError) as err:
        exclusive_create_bytes(path, b"payload-bytes")
    message = str(err.value)
    assert "path" not in message.lower() or "exclusive" in message.lower()
    assert "payload-bytes" not in message
    assert path.exists()  # uncertain artifact; no silent unlink
    with pytest.raises(FileExistsError):
        exclusive_create_bytes(path, b"payload-bytes")


def test_exclusive_create_bytes_fsyncs_file_then_parent_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "nested" / "artifact.bin"
    payload = b"durable"
    order: list[str] = []
    real_fsync = os.fsync
    real_open = os.open
    tracked: dict[int, str] = {}

    def tracking_open(file, flags, mode=0o777, *args, **kwargs):
        fd = real_open(file, flags, mode, *args, **kwargs)
        label = "file" if Path(file) == path else "parent" if Path(file) == path.parent else str(file)
        if Path(file) == path:
            tracked[fd] = "file"
        elif Path(file) == path.parent or (
            isinstance(file, int) is False and Path(os.fspath(file)) == path.parent
        ):
            tracked[fd] = "parent"
        else:
            try:
                st = os.fstat(fd)
                if stat.S_ISDIR(st.st_mode) and Path(os.fspath(file)).resolve() == path.parent.resolve():
                    tracked[fd] = "parent"
            except OSError:
                pass
        return fd

    def tracking_fsync(fd: int) -> None:
        which = tracked.get(fd)
        if which is None:
            # Re-open path may reuse fds; classify via fstat + known inodes.
            try:
                st = os.fstat(fd)
                file_ino = path.stat().st_ino if path.exists() else None
                parent_ino = path.parent.stat().st_ino
                if stat.S_ISREG(st.st_mode) and file_ino is not None and st.st_ino == file_ino:
                    which = "file"
                elif stat.S_ISDIR(st.st_mode) and st.st_ino == parent_ino:
                    which = "parent"
            except OSError:
                which = "other"
        order.append(which or "other")
        return real_fsync(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fsync", tracking_fsync)
    exclusive_create_bytes(path, payload)
    assert path.read_bytes() == payload
    assert "file" in order
    assert "parent" in order
    assert order.index("file") < order.index("parent")


def test_exclusive_create_bytes_file_fsync_failure_leaves_uncertain_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "artifact.bin"
    real_fsync = os.fsync
    state = {"file_seen": False}

    def boom_first_file_fsync(fd: int) -> None:
        st = os.fstat(fd)
        if not state["file_seen"] and stat.S_ISREG(st.st_mode):
            state["file_seen"] = True
            raise OSError(5, "injected file fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom_first_file_fsync)
    with pytest.raises(EventStoreError) as err:
        exclusive_create_bytes(path, b"secret-bytes")
    assert "secret-bytes" not in str(err.value)
    assert path.exists()
    with pytest.raises(FileExistsError):
        exclusive_create_bytes(path, b"again")


def test_exclusive_create_bytes_refuses_symlink_final_component(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real.bin"
    target.write_bytes(b"preexisting")
    link = tmp_path / "link.bin"
    link.symlink_to(target)
    with pytest.raises((FileExistsError, EventStoreError, OSError)):
        exclusive_create_bytes(link, b"hijack")
    assert target.read_bytes() == b"preexisting"


def test_create_run_fsyncs_new_directory_entry_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(base_dir=tmp_path / "runs")
    order: list[str] = []
    real_fsync = os.fsync
    run_id = "run_durable_dir"
    expected_run = (tmp_path / "runs" / run_id).resolve()
    expected_parent = (tmp_path / "runs").resolve()

    def tracking_fsync(fd: int) -> None:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            order.append("other")
            return real_fsync(fd)
        # Match by inode against expected dirs once they exist.
        try:
            if expected_run.exists() and st.st_ino == expected_run.stat().st_ino:
                order.append("run_dir")
            elif expected_parent.exists() and st.st_ino == expected_parent.stat().st_ino:
                order.append("parent")
            else:
                order.append("other-dir")
        except OSError:
            order.append("other-dir")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", tracking_fsync)
    handle = store.create_run(run_id)
    assert handle.run_dir == expected_run
    assert _mode_octal(handle.run_dir) == DIR_MODE
    assert "run_dir" in order
    assert "parent" in order
    assert order.index("run_dir") < order.index("parent")


def test_create_run_parent_fsync_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(base_dir=tmp_path / "runs")
    run_id = "run_fsync_fail"
    real_fsync = os.fsync
    expected_parent = (tmp_path / "runs").resolve()

    def boom_parent(fd: int) -> None:
        st = os.fstat(fd)
        if (
            expected_parent.exists()
            and stat.S_ISDIR(st.st_mode)
            and st.st_ino == expected_parent.stat().st_ino
        ):
            raise OSError(5, "injected parent fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom_parent)
    with pytest.raises(EventStoreError):
        store.create_run(run_id)
    # Uncertain directory entry may remain; callers must not treat this as success.
    run_dir = tmp_path / "runs" / run_id
    if run_dir.exists():
        with pytest.raises(EventStoreError):
            store.create_run(run_id)


def test_secure_mkdir_creates_0700_dir(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "abc"

    secure_mkdir(path)

    assert path.is_dir()
    assert _mode_octal(path) == 0o700


def test_event_store_run_dir_is_mode_0700(tmp_path: Path) -> None:
    store = EventStore(base_dir=tmp_path)
    handle = store.create_run("run_test_dir")

    assert _mode_octal(handle.run_dir) == 0o700


def test_event_store_writes_final_artifact_at_0600(tmp_path: Path) -> None:
    store = EventStore(base_dir=tmp_path)
    handle = store.create_run("run_test_mode")

    handle.write_json("result.json", {"status": "completed"})

    file_path = handle.run_dir / "result.json"
    assert file_path.exists()
    assert _mode_octal(file_path) == 0o600


def test_event_store_atomic_json_write_does_not_leave_tmp_files(tmp_path: Path) -> None:
    store = EventStore(base_dir=tmp_path)
    handle = store.create_run("run_test_atomic")

    handle.write_json("metadata.json", {"hello": "world"})

    files = sorted(p.name for p in handle.run_dir.iterdir())
    assert "metadata.json" in files
    assert not any(name.endswith(".tmp") or name.startswith(".tmp") for name in files)


def test_event_store_append_stream_creates_0600_file(tmp_path: Path) -> None:
    store = EventStore(base_dir=tmp_path)
    handle = store.create_run("run_test_stream")

    handle.append_ndjson("normalized-events.jsonl", {"type": "run_started"})
    handle.append_ndjson("normalized-events.jsonl", {"type": "run_completed"})

    stream_path = handle.run_dir / "normalized-events.jsonl"
    assert _mode_octal(stream_path) == 0o600
    contents = stream_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(contents[0])["type"] == "run_started"
    assert json.loads(contents[1])["type"] == "run_completed"


def test_event_store_round_trip_can_read_json(tmp_path: Path) -> None:
    store = EventStore(base_dir=tmp_path)
    handle = store.create_run("run_test_round")

    payload = {"status": "completed", "run_id": "run_test_round"}
    handle.write_json("result.json", payload)
    loaded = handle.read_json("result.json")

    assert loaded == payload


def test_event_store_rejects_run_ids_with_path_traversal(tmp_path: Path) -> None:
    store = EventStore(base_dir=tmp_path)

    try:
        store.create_run("../escape")
    except ValueError:
        return
    raise AssertionError("create_run must reject run_id with path traversal")


def test_event_store_permission_probe_round_trip(tmp_path: Path) -> None:
    store = EventStore(base_dir=tmp_path)

    probe = store.permission_probe()

    assert probe["dir_mode_ok"] is True
    assert probe["file_mode_ok"] is True
    assert probe["atomic_write_ok"] is True


def test_r6_b2_durable_atomic_write_order_and_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.event_store import durable_atomic_write_bytes

    path = tmp_path / "nested" / "record.bin"
    payload = b"durable-secret"
    order: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace
    real_write = os.write

    def tracking_write(fd: int, data: bytes) -> int:
        order.append("write")
        return real_write(fd, data)

    def tracking_fsync(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISREG(st.st_mode):
            order.append("file_fsync")
        elif stat.S_ISDIR(st.st_mode):
            order.append("parent_fsync")
        else:
            order.append("other_fsync")
        return real_fsync(fd)

    def tracking_replace(src, dst):
        order.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "write", tracking_write)
    monkeypatch.setattr(os, "fsync", tracking_fsync)
    monkeypatch.setattr(os, "replace", tracking_replace)
    durable_atomic_write_bytes(path, payload)
    assert path.read_bytes() == payload
    assert _mode_octal(path) == 0o600
    assert "write" in order
    assert "file_fsync" in order
    assert "replace" in order
    assert "parent_fsync" in order
    assert order.index("write") < order.index("file_fsync")
    assert order.index("file_fsync") < order.index("replace")
    assert order.index("replace") < order.index("parent_fsync")
    assert "durable-secret" not in "".join(order)


def test_r6_b2_durable_atomic_write_partial_write_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.event_store import durable_atomic_write_bytes

    path = tmp_path / "artifact.bin"
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    with pytest.raises(EventStoreError) as err:
        durable_atomic_write_bytes(path, b"secret-payload")
    assert "secret-payload" not in str(err.value)
    assert not path.exists()
    assert not any(p.name.startswith(".tmp-durable-") for p in tmp_path.iterdir())


def test_r6_b2_durable_atomic_write_file_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.event_store import durable_atomic_write_bytes

    path = tmp_path / "artifact.bin"
    real_fsync = os.fsync

    def boom_file(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISREG(st.st_mode):
            raise OSError(5, "injected file fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom_file)
    with pytest.raises(EventStoreError) as err:
        durable_atomic_write_bytes(path, b"secret-bytes")
    assert "secret-bytes" not in str(err.value)
    assert not path.exists()


def test_r6_b2_durable_atomic_write_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.event_store import durable_atomic_write_bytes

    path = tmp_path / "artifact.bin"

    def boom_replace(src, dst):
        raise OSError(5, "injected replace failure")

    monkeypatch.setattr(os, "replace", boom_replace)
    with pytest.raises(EventStoreError) as err:
        durable_atomic_write_bytes(path, b"secret-bytes")
    assert "secret-bytes" not in str(err.value)
    assert not path.exists()
    assert not any(p.name.startswith(".tmp-durable-") for p in tmp_path.iterdir())


def test_r6_b2_durable_atomic_write_parent_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.event_store import durable_atomic_write_bytes

    path = tmp_path / "nested" / "artifact.bin"
    real_fsync = os.fsync
    parent = (tmp_path / "nested").resolve()

    def boom_parent(fd: int) -> None:
        st = os.fstat(fd)
        if (
            parent.exists()
            and stat.S_ISDIR(st.st_mode)
            and st.st_ino == parent.stat().st_ino
        ):
            raise OSError(5, "injected parent fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom_parent)
    with pytest.raises(EventStoreError):
        durable_atomic_write_bytes(path, b"payload")
    # Replace may have succeeded; failure is still fail-closed (no success return).


def test_r6_b2_durable_unlink_idempotent_and_parent_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.event_store import durable_unlink

    path = tmp_path / "fence.json"
    path.write_text("{}", encoding="utf-8")
    order: list[str] = []
    real_fsync = os.fsync
    real_unlink = os.unlink

    def tracking_unlink(p):
        order.append("unlink")
        return real_unlink(p)

    def tracking_fsync(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            order.append("parent_fsync")
        return real_fsync(fd)

    monkeypatch.setattr(os, "unlink", tracking_unlink)
    monkeypatch.setattr(os, "fsync", tracking_fsync)
    durable_unlink(path)
    assert not path.exists()
    assert order == ["unlink", "parent_fsync"]
    durable_unlink(path)  # missing is idempotent — may probe unlink, no parent fsync
    assert order.count("parent_fsync") == 1
    assert order[0:2] == ["unlink", "parent_fsync"]


def test_r6_b2_durable_unlink_parent_fsync_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.event_store import durable_unlink

    path = tmp_path / "fence.json"
    path.write_text("{}", encoding="utf-8")
    real_fsync = os.fsync
    parent = tmp_path.resolve()

    def boom_parent(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode) and st.st_ino == parent.stat().st_ino:
            raise OSError(5, "injected unlink parent fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom_parent)
    with pytest.raises(EventStoreError):
        durable_unlink(path)
    assert not path.exists()


def test_r6_b2_durable_unlink_nofollow_symlink_leaf(tmp_path: Path) -> None:
    from agent_run_supervisor.event_store import durable_unlink

    target = tmp_path / "real.json"
    target.write_text('{"ok":true}', encoding="utf-8")
    link = tmp_path / "fence.json"
    link.symlink_to(target)
    durable_unlink(link)
    assert not link.exists()
    assert target.read_text(encoding="utf-8") == '{"ok":true}'


def test_r6_b2_legacy_atomic_write_json_semantics_unchanged(tmp_path: Path) -> None:
    """Broad legacy helper must not silently gain durable fsync requirements."""
    path = tmp_path / "legacy.json"
    atomic_write_json(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert _mode_octal(path) == 0o600

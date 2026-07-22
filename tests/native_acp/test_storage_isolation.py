"""R7 B1 — durable Native root publication via durable_secure_mkdir."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agent_run_supervisor.event_store import DIR_MODE, EventStoreError, durable_secure_mkdir
from agent_run_supervisor.native_acp import storage


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_r7_b1_native_stores_use_durable_secure_mkdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    real = durable_secure_mkdir

    def spy(path: Path) -> Path:
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(storage, "durable_secure_mkdir", spy)
    # Re-bind module-level import used by the helpers.
    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.storage.durable_secure_mkdir", spy
    )
    root = tmp_path / "sv"
    storage.native_session_store(root)
    storage.native_event_store(root)
    assert root / "native-sessions" in calls or any(
        p.name == "native-sessions" for p in calls
    )
    assert any(p.name == "native-runs" for p in calls)
    assert _mode(root / "native-sessions") == DIR_MODE
    assert _mode(root / "native-runs") == DIR_MODE


def test_r7_b1_native_store_parent_fsync_failure_blocks_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sv"
    leaf = root / "native-runs"
    real_fsync = os.fsync
    parent = root.resolve()

    # Create root first so only native-runs is new; fail fsync of parent (root).
    root.mkdir()
    os.chmod(root, 0o700)

    def boom(fd: int) -> None:
        st = os.fstat(fd)
        if (
            parent.exists()
            and stat.S_ISDIR(st.st_mode)
            and st.st_ino == parent.stat().st_ino
        ):
            # First parent fsync after creating native-runs.
            if leaf.exists():
                raise OSError(5, "injected parent fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(EventStoreError):
        storage.native_event_store(root)


def test_r7_b1_native_store_symlink_leaf_refused(tmp_path: Path) -> None:
    root = tmp_path / "sv"
    root.mkdir()
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = root / "native-sessions"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(EventStoreError):
        storage.native_session_store(root)


def test_r7_b1_no_legacy_root_reads_or_writes(tmp_path: Path) -> None:
    root = tmp_path / "sv"
    legacy_sessions = root / "sessions"
    legacy_runs = root / "runs"
    legacy_sessions.mkdir(parents=True)
    legacy_runs.mkdir(parents=True)
    (legacy_sessions / "poison").mkdir()
    (legacy_runs / "poison").mkdir()
    poison = b'{"legacy":true}'
    (legacy_sessions / "poison" / "session.json").write_bytes(poison)
    (legacy_runs / "poison" / "result.json").write_bytes(poison)

    storage.native_session_store(root)
    storage.native_event_store(root)
    assert (legacy_sessions / "poison" / "session.json").read_bytes() == poison
    assert (legacy_runs / "poison" / "result.json").read_bytes() == poison
    assert sorted(p.name for p in root.iterdir()) == [
        "native-runs",
        "native-sessions",
        "runs",
        "sessions",
    ]

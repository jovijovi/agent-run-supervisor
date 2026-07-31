"""R7 B1 — durable Native root publication via durable_secure_mkdir."""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Descriptor identity and exact-length controls (B4 invariant 4)
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_an_untampered_document_is_valid(tmp_path: Path) -> None:
    """Positive control: the strict reader still accepts a real document."""
    state = storage.classify_json_document(_write(tmp_path / "d.json", '{"a": 1}'))
    assert state.kind is storage.JsonDocumentKind.VALID
    assert state.payload == {"a": 1}


def test_successful_short_read_after_fstat_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shrink between ``fstat`` and the read is a race, not a smaller doc.

    The truncated bytes are *valid JSON* and the read *succeeds*, so nothing
    but the exact-length control can catch this: the reader observed a size for
    this descriptor and must read exactly that many bytes.
    """
    path = _write(tmp_path / "shrink.json", '{"original": "longer"}\n')
    real_reader = storage._read_fd_capped

    def shrink_then_read(fd: int, limit: int) -> bytes:
        path.write_bytes(b"{}")
        os.lseek(fd, 0, os.SEEK_SET)
        return real_reader(fd, limit)

    monkeypatch.setattr(storage, "_read_fd_capped", shrink_then_read)
    state = storage.classify_json_document(path)
    assert state.kind is storage.JsonDocumentKind.CORRUPT
    assert state.payload is None


def test_regular_file_replacement_between_observation_and_open_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different regular inode at the same path is a different object."""
    path = _write(tmp_path / "swap.json", '{"original": true}')
    replacement = _write(tmp_path / "replacement.json", '{"planted": true}')
    real_open = os.open

    def swapping_open(target, flags, *args, **kwargs):
        if Path(os.fspath(target)).name == "swap.json":
            os.replace(replacement, path)
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    assert (
        storage.classify_json_document(path).kind
        is storage.JsonDocumentKind.CORRUPT
    )


def test_growth_during_the_read_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A size change under the descriptor fails closed in either direction.

    Scope stated honestly: the controls detect a size change, an inode change
    before the open, and a mutation landing in a later timestamp tick. A
    same-length in-place rewrite inside a single coarse clock tick is not
    observable through ``stat`` at all — no reader can catch it — while every
    ARS writer publishes through exclusive-create or atomic replace, which
    changes the inode and is therefore caught by the identity control above.
    """
    path = _write(tmp_path / "grow.json", '{"a": "one"}')
    real_reader = storage._read_fd_capped

    def grow_then_read(fd: int, limit: int) -> bytes:
        path.write_bytes(b'{"a": "one", "grown": true}')
        os.lseek(fd, 0, os.SEEK_SET)
        return real_reader(fd, limit)

    monkeypatch.setattr(storage, "_read_fd_capped", grow_then_read)
    assert (
        storage.classify_json_document(path).kind
        is storage.JsonDocumentKind.CORRUPT
    )


def test_the_terminal_reader_shares_the_same_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trusted terminal reader is the same bounded, identity-bound read."""
    run_dir = tmp_path / "run-short"
    run_dir.mkdir()
    result = run_dir / "result.json"
    payload = {
        "schema_version": 1,
        "run_id": "run-short",
        "status": "completed",
        "retryable": False,
    }
    result.write_text(json.dumps(payload) + " " * 32, encoding="utf-8")
    real_reader = storage._read_fd_capped

    def shrink_then_read(fd: int, limit: int) -> bytes:
        result.write_bytes(b"{}")
        os.lseek(fd, 0, os.SEEK_SET)
        return real_reader(fd, limit)

    monkeypatch.setattr(storage, "_read_fd_capped", shrink_then_read)
    assert (
        storage.read_native_terminal_result(result, run_id="run-short").kind
        is storage.NativeTerminalKind.INVALID
    )
    # Clean absence stays the only route to ABSENT.
    assert (
        storage.read_native_terminal_result(
            run_dir / "missing.json", run_id="run-short"
        ).kind
        is storage.NativeTerminalKind.ABSENT
    )

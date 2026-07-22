from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DIR_MODE = 0o700
FILE_MODE = 0o600

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")


class EventStoreError(RuntimeError):
    """Raised when EventStore cannot satisfy its security/integrity contract."""


@dataclass
class RunHandle:
    run_id: str
    run_dir: Path

    def write_json(self, name: str, payload: Mapping[str, Any]) -> Path:
        path = self.run_dir / name
        _atomic_write_bytes(
            path,
            json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"),
        )
        return path

    def read_json(self, name: str) -> Any:
        return json.loads((self.run_dir / name).read_text(encoding="utf-8"))

    def append_ndjson(self, name: str, record: Mapping[str, Any]) -> None:
        self.append_text(name, json.dumps(record, sort_keys=True) + "\n")

    def append_text(self, name: str, value: str) -> None:
        path = self.run_dir / name
        encoded = value.encode("utf-8")
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
            try:
                os.write(fd, encoded)
            finally:
                os.close(fd)
            os.chmod(path, FILE_MODE)
        else:
            with open(path, "ab") as stream:
                stream.write(encoded)
            os.chmod(path, FILE_MODE)

    def write_text(self, name: str, value: str) -> Path:
        return _atomic_write_path(self.run_dir / name, value.encode("utf-8"))


class EventStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def create_run(self, run_id: str) -> RunHandle:
        if not _RUN_ID_RE.match(run_id):
            raise ValueError(
                f"EventStore: run_id {run_id!r} must match {_RUN_ID_RE.pattern}",
            )
        if not self.base_dir.exists():
            self.base_dir.mkdir(parents=True, exist_ok=True)
        run_dir = self.base_dir / run_id
        if run_dir.exists():
            raise EventStoreError(f"EventStore: run_dir already exists: {run_dir}")
        try:
            run_dir.mkdir(mode=DIR_MODE)
            os.chmod(run_dir, DIR_MODE)
            # Durably publish the new run directory entry before exclusive
            # admission artifacts (submission.json) are created inside it.
            _fsync_dir(run_dir)
            _fsync_dir(self.base_dir)
        except EventStoreError:
            raise
        except OSError as exc:
            raise EventStoreError(
                "EventStore: failed to durably create run directory"
            ) from exc
        return RunHandle(run_id=run_id, run_dir=run_dir)

    def permission_probe(self) -> dict[str, bool]:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(base_dir=Path(tmp))
            handle = store.create_run("run_probe")
            handle.write_json("probe.json", {"ok": True})
            file_path = handle.run_dir / "probe.json"
            dir_ok = _mode(handle.run_dir) == DIR_MODE
            file_ok = _mode(file_path) == FILE_MODE
            handle.append_ndjson("stream.jsonl", {"event": "probe"})
            atomic_ok = file_path.exists() and not any(
                p.name.startswith(".tmp") or p.name.endswith(".tmp")
                for p in handle.run_dir.iterdir()
            )
            return {
                "dir_mode_ok": dir_ok,
                "file_mode_ok": file_ok,
                "atomic_write_ok": atomic_ok,
            }


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    """Atomically write ``payload`` as canonical JSON at ``FILE_MODE`` (0600).

    Parents are created as needed; the final file is replaced atomically so a
    reader never observes a partial write. Keys are sorted for deterministic
    bytes (so the artifact is stable for hashing/diffing).
    """
    path = Path(path)
    data = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    _atomic_write_bytes(path, data)
    return path


def durable_atomic_write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    """Crash-durable atomic JSON write at ``FILE_MODE`` (0600).

    Same canonical JSON bytes as :func:`atomic_write_json`, but durability is
    proven through the narrowly named bytes helper (temp 0600, write-all, file
    fsync, atomic replace, parent directory fsync). Uncertain failures raise
    sanitized :class:`EventStoreError` and never claim success.
    """
    path = Path(path)
    data = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    return durable_atomic_write_bytes(path, data)


def durable_atomic_write_bytes(path: Path, data: bytes) -> Path:
    """Crash-durable atomic bytes write at ``FILE_MODE`` (0600).

    Order: create temp (0600), write-all, file fsync, atomic replace, parent
    directory fsync. Failures raise sanitized :class:`EventStoreError` and
    fail closed — callers must not treat an exception as durable success.
    Does not alter legacy :func:`_atomic_write_bytes` semantics.
    """
    path = Path(path)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EventStoreError("EventStore: durable atomic write failed") from exc

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    tmp_path: Path | None = None
    fd = -1
    last_open_error: BaseException | None = None
    for _ in range(32):
        candidate = parent / f".tmp-durable-{os.getpid()}-{os.urandom(8).hex()}"
        try:
            fd = os.open(candidate, flags, FILE_MODE)
            tmp_path = candidate
            break
        except FileExistsError as exc:
            last_open_error = exc
            continue
        except OSError as exc:
            raise EventStoreError("EventStore: durable atomic write failed") from exc
    if tmp_path is None or fd < 0:
        raise EventStoreError(
            "EventStore: durable atomic write failed"
        ) from last_open_error

    primary: BaseException | None = None
    try:
        try:
            os.fchmod(fd, FILE_MODE)
        except OSError as exc:
            raise EventStoreError("EventStore: durable atomic write failed") from exc
        _write_all_durable(fd, data)
        try:
            os.fsync(fd)
        except OSError as exc:
            raise EventStoreError(
                "EventStore: durable atomic write durability failed"
            ) from exc
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            os.close(fd)
        except OSError as close_exc:
            if primary is None:
                raise EventStoreError(
                    "EventStore: durable atomic write durability failed"
                ) from close_exc
        if primary is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    try:
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise EventStoreError("EventStore: durable atomic write failed") from exc

    try:
        os.chmod(path, FILE_MODE)
    except OSError as exc:
        raise EventStoreError("EventStore: durable atomic write failed") from exc

    try:
        _fsync_dir(parent)
    except EventStoreError:
        raise
    except OSError as exc:
        raise EventStoreError(
            "EventStore: durable atomic write durability failed"
        ) from exc
    return path


def durable_unlink(path: Path) -> None:
    """Unlink ``path`` then fsync its parent directory.

    Missing paths are idempotent (no error, no parent fsync). The unlink is
    nofollow-safe for the final component (symlink leaf is removed, not its
    target). Parent directory fsync failures propagate as sanitized
    :class:`EventStoreError`.
    """
    path = Path(path)
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise EventStoreError("EventStore: durable unlink failed") from exc
    try:
        _fsync_dir(path.parent)
    except EventStoreError:
        raise
    except OSError as exc:
        raise EventStoreError("EventStore: durable unlink durability failed") from exc


def exclusive_create_bytes(path: Path, data: bytes) -> Path:
    """Create ``path`` exclusively (``O_EXCL``) at ``FILE_MODE`` (0600).

    Writes all bytes (zero-progress fails closed), fsyncs the file then the
    parent directory before returning success. Write/fsync failures leave the
    uncertain exclusive artifact in place (no silent unlink) and raise
    ``EventStoreError`` with a sanitized message. ``FileExistsError`` is
    preserved for the exclusive-create race.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, FILE_MODE)
    except FileExistsError:
        raise
    except OSError as exc:
        raise EventStoreError("EventStore: exclusive create failed") from exc
    primary: BaseException | None = None
    try:
        try:
            os.fchmod(fd, FILE_MODE)
        except OSError as exc:
            raise EventStoreError("EventStore: exclusive create failed") from exc
        _write_all(fd, data)
        try:
            os.fsync(fd)
        except OSError as exc:
            raise EventStoreError(
                "EventStore: exclusive create durability failed"
            ) from exc
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            os.close(fd)
        except OSError as close_exc:
            if primary is None:
                raise EventStoreError(
                    "EventStore: exclusive create durability failed"
                ) from close_exc
    try:
        _fsync_dir(path.parent)
    except EventStoreError:
        raise
    except OSError as exc:
        raise EventStoreError(
            "EventStore: exclusive create durability failed"
        ) from exc
    return path


def secure_mkdir(path: Path) -> Path:
    """Create ``path`` (and parents) and force ``DIR_MODE`` (0700) on the leaf."""
    path = Path(path)
    path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    os.chmod(path, DIR_MODE)
    return path


def _atomic_write_path(path: Path, data: bytes) -> Path:
    _atomic_write_bytes(path, data)
    return path


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(tmp_name, path)
        os.chmod(path, FILE_MODE)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    length = len(data)
    while offset < length:
        try:
            written = os.write(fd, data[offset:])
        except OSError as exc:
            raise EventStoreError("EventStore: exclusive create write failed") from exc
        if written <= 0:
            raise EventStoreError("EventStore: exclusive create write failed")
        offset += written


def _write_all_durable(fd: int, data: bytes) -> None:
    offset = 0
    length = len(data)
    while offset < length:
        try:
            written = os.write(fd, data[offset:])
        except OSError as exc:
            raise EventStoreError("EventStore: durable atomic write failed") from exc
        if written <= 0:
            raise EventStoreError("EventStore: durable atomic write failed")
        offset += written


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        dir_fd = os.open(path, flags)
    except OSError as exc:
        raise EventStoreError("EventStore: directory durability failed") from exc
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        raise EventStoreError("EventStore: directory durability failed") from exc
    finally:
        os.close(dir_fd)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)

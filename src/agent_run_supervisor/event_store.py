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
        """Append one NDJSON record only after a crash-durable ``append_text``."""
        self.append_text(name, json.dumps(record, sort_keys=True) + "\n")

    def append_text(self, name: str, value: str) -> None:
        """Nofollow regular-file append with write-all + file fsync (and parent).

        Opens the leaf with ``O_APPEND`` (``O_NOFOLLOW`` when available), refuses
        non-regular files, writes all bytes through short-write retries, and
        ``fsync``s the file before returning. When the file is newly created,
        the parent directory is fsynced before success. Failures raise sanitized
        :class:`EventStoreError`. Single-writer contract is unchanged.
        """
        path = self.run_dir / name
        encoded = value.encode("utf-8")
        flags_existing = os.O_WRONLY | os.O_APPEND
        flags_create = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags_existing |= os.O_NOFOLLOW
            flags_create |= os.O_NOFOLLOW
        created = False
        fd = -1
        primary: BaseException | None = None
        try:
            try:
                fd = os.open(path, flags_existing)
            except FileNotFoundError:
                try:
                    fd = os.open(path, flags_create, FILE_MODE)
                except OSError as exc:
                    raise EventStoreError("EventStore: durable append failed") from exc
                created = True
            except OSError as exc:
                raise EventStoreError("EventStore: durable append failed") from exc
            try:
                st = os.fstat(fd)
            except OSError as exc:
                raise EventStoreError("EventStore: durable append failed") from exc
            if not stat.S_ISREG(st.st_mode):
                raise EventStoreError(
                    "EventStore: durable append refused non-regular file"
                )
            try:
                os.fchmod(fd, FILE_MODE)
            except OSError as exc:
                raise EventStoreError("EventStore: durable append failed") from exc
            _write_all_append(fd, encoded)
            try:
                os.fsync(fd)
            except OSError as exc:
                raise EventStoreError(
                    "EventStore: durable append durability failed"
                ) from exc
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError as close_exc:
                    if primary is None:
                        raise EventStoreError(
                            "EventStore: durable append durability failed"
                        ) from close_exc
        if created:
            try:
                _fsync_dir(path.parent)
            except EventStoreError:
                raise
            except OSError as exc:
                raise EventStoreError(
                    "EventStore: durable append durability failed"
                ) from exc

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
    """Create ``path`` exclusively at ``FILE_MODE`` (0600) with atomic publish.

    Order: same-dir temp (0600, ``O_EXCL``/nofollow), write-all, file fsync,
    atomic no-clobber publication via same-dir ``link`` (FileExists preserved),
    unlink temp, parent directory fsync. Readers observe absent or complete
    bytes — never a partial final path. Write/fsync failures leave uncertain
    temp debris (no silent final publish) and raise sanitized
    :class:`EventStoreError`.
    """
    path = Path(path)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EventStoreError("EventStore: exclusive create failed") from exc

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    tmp_path: Path | None = None
    fd = -1
    last_open_error: BaseException | None = None
    for _ in range(32):
        candidate = parent / f".tmp-excl-{os.getpid()}-{os.urandom(8).hex()}"
        try:
            fd = os.open(candidate, flags, FILE_MODE)
            tmp_path = candidate
            break
        except FileExistsError as exc:
            last_open_error = exc
            continue
        except OSError as exc:
            raise EventStoreError("EventStore: exclusive create failed") from exc
    if tmp_path is None or fd < 0:
        raise EventStoreError(
            "EventStore: exclusive create failed"
        ) from last_open_error

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
        # On failure, leave uncertain temp debris (no final publish).

    try:
        os.link(tmp_path, path)
    except FileExistsError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    except OSError as exc:
        # Fail closed: leave uncertain temp debris; do not publish.
        raise EventStoreError("EventStore: exclusive create failed") from exc

    try:
        os.unlink(tmp_path)
    except OSError:
        # Final is published; leftover temp is non-authoritative debris.
        pass

    try:
        _fsync_dir(parent)
    except EventStoreError:
        raise
    except OSError as exc:
        raise EventStoreError(
            "EventStore: exclusive create durability failed"
        ) from exc
    return path


def durable_secure_mkdir(path: Path) -> Path:
    """Durably create ``path`` (and missing components) at ``DIR_MODE`` (0700).

    Each newly created component is mode 0700, then the new directory and its
    parent are fsynced before success is claimed. Existing components of the
    requested path below the first pre-existing external ancestor must be
    real directories (nofollow/lstat) and are forced to 0700, then the directory
    itself is fsynced before success. A lost-create ``FileExists`` race verifies
    a real directory, forces 0700, and fsyncs the raced directory and its parent
    before treating the component as durable. Symlink / non-dir components and
    any mkdir/chmod/dir-fsync failure raise sanitized :class:`EventStoreError`.
    Legacy :func:`secure_mkdir` is unchanged.
    """
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        st = None
    except OSError as exc:
        raise EventStoreError("EventStore: durable secure mkdir failed") from exc

    if st is not None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise EventStoreError(
                "EventStore: durable secure mkdir refused non-directory"
            )
        try:
            os.chmod(path, DIR_MODE)
        except OSError as exc:
            raise EventStoreError("EventStore: durable secure mkdir failed") from exc
        try:
            _fsync_dir(path)
        except EventStoreError:
            raise
        except OSError as exc:
            raise EventStoreError(
                "EventStore: durable secure mkdir durability failed"
            ) from exc
        return path

    missing: list[Path] = []
    cur = path
    while True:
        try:
            os.lstat(cur)
            break
        except FileNotFoundError:
            missing.append(cur)
            parent = cur.parent
            if parent == cur:
                raise EventStoreError("EventStore: durable secure mkdir failed")
            cur = parent
        except OSError as exc:
            raise EventStoreError("EventStore: durable secure mkdir failed") from exc

    # ``cur`` is the first existing ancestor — verify real dir, do not chmod
    # (outside or at the edge of the requested tree).
    try:
        anchor_st = os.lstat(cur)
    except OSError as exc:
        raise EventStoreError("EventStore: durable secure mkdir failed") from exc
    if stat.S_ISLNK(anchor_st.st_mode) or not stat.S_ISDIR(anchor_st.st_mode):
        raise EventStoreError(
            "EventStore: durable secure mkdir refused non-directory"
        )

    for component in reversed(missing):
        try:
            os.mkdir(component, DIR_MODE)
        except FileExistsError:
            # Lost a create race: verify, force 0700, fsync dir+parent.
            try:
                raced = os.lstat(component)
            except OSError as exc:
                raise EventStoreError(
                    "EventStore: durable secure mkdir failed"
                ) from exc
            if stat.S_ISLNK(raced.st_mode) or not stat.S_ISDIR(raced.st_mode):
                raise EventStoreError(
                    "EventStore: durable secure mkdir refused non-directory"
                )
            try:
                os.chmod(component, DIR_MODE)
            except OSError as exc:
                raise EventStoreError(
                    "EventStore: durable secure mkdir failed"
                ) from exc
            try:
                _fsync_dir(component)
                _fsync_dir(component.parent)
            except EventStoreError:
                raise
            except OSError as exc:
                raise EventStoreError(
                    "EventStore: durable secure mkdir durability failed"
                ) from exc
            continue
        except OSError as exc:
            raise EventStoreError("EventStore: durable secure mkdir failed") from exc
        try:
            os.chmod(component, DIR_MODE)
        except OSError as exc:
            raise EventStoreError("EventStore: durable secure mkdir failed") from exc
        try:
            _fsync_dir(component)
            _fsync_dir(component.parent)
        except EventStoreError:
            raise
        except OSError as exc:
            raise EventStoreError(
                "EventStore: durable secure mkdir durability failed"
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


def _write_all_append(fd: int, data: bytes) -> None:
    offset = 0
    length = len(data)
    while offset < length:
        try:
            written = os.write(fd, data[offset:])
        except OSError as exc:
            raise EventStoreError("EventStore: durable append failed") from exc
        if written <= 0:
            raise EventStoreError("EventStore: durable append failed")
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

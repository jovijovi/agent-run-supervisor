from __future__ import annotations

import json
import os
import re
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
        path = self.run_dir / name
        encoded = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
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
        run_dir.mkdir(mode=DIR_MODE)
        os.chmod(run_dir, DIR_MODE)
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


def _mode(path: Path) -> int:
    import stat

    return stat.S_IMODE(os.stat(path).st_mode)

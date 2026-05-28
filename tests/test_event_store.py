from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from agent_run_supervisor.event_store import EventStore


def _mode_octal(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


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

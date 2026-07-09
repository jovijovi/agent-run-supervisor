"""Read-only local session inspection (`session_inspect`) — caller API tests.

These tests pin the generic caller-side inspection surface: a pure local,
read-only view over an existing session's record, lease lock, turn artifacts,
and latest progress snapshot. The API must never spawn acpx/an AGENT/any
subprocess, must fail closed (never fabricate a healthy value) on corrupt or
missing artifacts, and must never surface raw prompt/output text.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.role import AgentRoleSpec, load_role
from agent_run_supervisor.session import (
    InvalidSessionIdError,
    SessionNotFoundError,
    SessionStore,
)
from agent_run_supervisor.session_inspect import (
    SessionInspection,
    TurnInfo,
    inspect_session,
    list_turns,
)
from agent_run_supervisor.workspace import validate_effective_cwd

UTC = timezone.utc
T0 = datetime(2026, 7, 9, 10, 0, 0, tzinfo=UTC)


def _role_at(valid_role_dict: dict[str, Any], work: Path) -> AgentRoleSpec:
    payload = dict(valid_role_dict)
    payload["workspace"] = dict(valid_role_dict["workspace"])
    payload["workspace"]["default_cwd"] = str(work)
    payload["workspace"]["allowed_roots"] = [str(work)]
    payload["workspace"]["allowed_roots_security_boundary"] = False
    payload["session"] = {"strategy": "persistent"}
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
def sessions_dir(tmp_path: Path) -> Path:
    return tmp_path / "sessions"


@pytest.fixture()
def store(sessions_dir: Path) -> SessionStore:
    return SessionStore(base_dir=sessions_dir)


@pytest.fixture()
def open_session(store: SessionStore, role: AgentRoleSpec):
    workspace = validate_effective_cwd(role, override=None)
    return store.create_session(
        session_id="sess-inspect", role=role, workspace_result=workspace, now=T0
    )


def _make_turn(
    sessions_dir: Path,
    session_id: str,
    turn_id: str,
    *,
    result: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
) -> Path:
    turn_dir = sessions_dir / session_id / "turns" / turn_id
    turn_dir.mkdir(parents=True)
    if result is not None:
        (turn_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    if progress is not None:
        (turn_dir / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
    return turn_dir


def _progress_payload(*, state: str = "running", last_seq: int = 3) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "state": state,
        "last_seq": last_seq,
        "event_count": last_seq,
        "updated_at": "2026-07-09T10:00:00+00:00",
    }


# --------------------------------------------------------------------------- #
# inspect_session — basic shape
# --------------------------------------------------------------------------- #


def test_inspect_missing_session_reports_not_exists(sessions_dir: Path) -> None:
    inspection = inspect_session(sessions_dir, "sess-none")

    assert isinstance(inspection, SessionInspection)
    assert inspection.session_id == "sess-none"
    assert inspection.exists is False
    assert inspection.state is None
    assert inspection.lease_held is False
    assert inspection.holder_liveness is None
    assert inspection.lease_recoverable is False
    assert inspection.turn_count == 0
    assert inspection.latest_turn_id is None
    assert inspection.latest_turn_status is None
    assert inspection.progress is None


def test_inspect_missing_sessions_root_reports_not_exists(tmp_path: Path) -> None:
    inspection = inspect_session(tmp_path / "no-root", "sess-none")
    assert inspection.exists is False


def test_inspect_rejects_unsafe_session_id(sessions_dir: Path) -> None:
    with pytest.raises(InvalidSessionIdError):
        inspect_session(sessions_dir, "../escape")


def test_inspect_open_session_without_turns(sessions_dir: Path, open_session) -> None:
    inspection = inspect_session(sessions_dir, "sess-inspect")

    assert inspection.exists is True
    assert inspection.state == "open"
    assert inspection.lease_held is False
    assert inspection.holder_liveness is None
    assert inspection.lease_recoverable is False
    assert inspection.turn_count == 0
    assert inspection.latest_turn_id is None
    assert inspection.latest_turn_status is None
    assert inspection.progress is None


def test_inspect_closed_session_reports_closed(
    sessions_dir: Path, store: SessionStore, open_session
) -> None:
    store.mark_closed("sess-inspect", now=T0 + timedelta(minutes=5))
    inspection = inspect_session(sessions_dir, "sess-inspect")
    assert inspection.exists is True
    assert inspection.state == "closed"


def test_inspect_latest_turn_status_and_progress(
    sessions_dir: Path, open_session
) -> None:
    _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T095000Z_aa000001",
        result={"status": "completed", "final_message": "done"},
    )
    _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_bb000002",
        result={"status": "no_op", "final_message": ""},
        progress=_progress_payload(state="no_op", last_seq=2),
    )

    inspection = inspect_session(sessions_dir, "sess-inspect")

    assert inspection.turn_count == 2
    assert inspection.latest_turn_id == "turn_20260709T100000Z_bb000002"
    assert inspection.latest_turn_status == "no_op"
    assert inspection.progress is not None
    assert inspection.progress.state == "no_op"
    assert inspection.progress.last_seq == 2
    assert inspection.progress.event_count == 2


def test_inspect_in_flight_turn_has_no_result_status(
    sessions_dir: Path, open_session
) -> None:
    # A turn in flight has progress.json but no result.json yet.
    _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_cc000003",
        progress=_progress_payload(state="running", last_seq=1),
    )
    inspection = inspect_session(sessions_dir, "sess-inspect")
    assert inspection.turn_count == 1
    assert inspection.latest_turn_id == "turn_20260709T100000Z_cc000003"
    assert inspection.latest_turn_status is None
    assert inspection.progress is not None
    assert inspection.progress.state == "running"


# --------------------------------------------------------------------------- #
# list_turns
# --------------------------------------------------------------------------- #


def test_list_turns_missing_session_fails_closed(sessions_dir: Path) -> None:
    with pytest.raises(SessionNotFoundError):
        list_turns(sessions_dir, "sess-none")


def test_list_turns_rejects_unsafe_session_id(sessions_dir: Path) -> None:
    with pytest.raises(InvalidSessionIdError):
        list_turns(sessions_dir, "a/b")


def test_list_turns_empty_session_returns_empty_tuple(
    sessions_dir: Path, open_session
) -> None:
    assert list_turns(sessions_dir, "sess-inspect") == ()


def test_list_turns_orders_by_turn_identity_and_reads_status(
    sessions_dir: Path, open_session
) -> None:
    dir_b = _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100500Z_bb000002",
        result={"status": "runner_error", "final_message": ""},
    )
    dir_a = _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T095900Z_aa000001",
        result={"status": "completed", "final_message": "ok"},
    )
    dir_c = _make_turn(sessions_dir, "sess-inspect", "turn_20260709T101000Z_cc000003")

    turns = list_turns(sessions_dir, "sess-inspect")

    assert isinstance(turns, tuple)
    assert [t.turn_id for t in turns] == [
        "turn_20260709T095900Z_aa000001",
        "turn_20260709T100500Z_bb000002",
        "turn_20260709T101000Z_cc000003",
    ]
    assert all(isinstance(t, TurnInfo) for t in turns)
    assert [t.status for t in turns] == ["completed", "runner_error", None]
    assert [Path(t.turn_dir) for t in turns] == [dir_a, dir_b, dir_c]


def test_list_turns_ignores_stray_files_and_unsafe_names(
    sessions_dir: Path, open_session
) -> None:
    turns_root = sessions_dir / "sess-inspect" / "turns"
    _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_dd000004",
        result={"status": "completed", "final_message": ""},
    )
    (turns_root / "stray-file.txt").write_text("noise", encoding="utf-8")
    (turns_root / ".hidden dir with spaces").mkdir()

    turns = list_turns(sessions_dir, "sess-inspect")
    assert [t.turn_id for t in turns] == ["turn_20260709T100000Z_dd000004"]


# --------------------------------------------------------------------------- #
# Lease / holder-liveness classification (mirrors detect_stale_locks semantics)
# --------------------------------------------------------------------------- #

from agent_run_supervisor.process_liveness import LivenessProbe, ProcessIdentity  # noqa: E402

_HOST = "inspect-host"
_HOLDER_PID = 4242
_HOLDER_START = "111111"


def _probe(
    *,
    running: bool | None,
    start: str | None = _HOLDER_START,
    boot_id: str = "boot-1",
) -> LivenessProbe:
    return LivenessProbe(
        is_running=lambda pid: running,
        read_start=lambda pid: start,
        current=lambda: ProcessIdentity(
            pid=_HOLDER_PID, process_start=_HOLDER_START, boot_id=boot_id, host=_HOST
        ),
    )


def _acquire(store_dir: Path, *, reclaimable: bool = True, lease_seconds: int = 3600):
    recorder = SessionStore(base_dir=store_dir, liveness_probe=_probe(running=True))
    return recorder.acquire_lock(
        "sess-inspect",
        "test-owner",
        now=T0,
        lease_seconds=lease_seconds,
        reclaimable=reclaimable,
    )


def test_inspect_lease_held_alive_holder_not_recoverable(
    sessions_dir: Path, open_session
) -> None:
    _acquire(sessions_dir)
    inspection = inspect_session(
        sessions_dir,
        "sess-inspect",
        liveness_probe=_probe(running=True),
        now=T0 + timedelta(minutes=1),
    )
    assert inspection.lease_held is True
    assert inspection.holder_liveness == "alive"
    assert inspection.lease_recoverable is False


def test_inspect_lease_crashed_holder_is_recoverable(
    sessions_dir: Path, open_session
) -> None:
    _acquire(sessions_dir)
    inspection = inspect_session(
        sessions_dir,
        "sess-inspect",
        liveness_probe=_probe(running=False),
        now=T0 + timedelta(minutes=1),
    )
    assert inspection.lease_held is True
    assert inspection.holder_liveness == "crashed"
    assert inspection.lease_recoverable is True


def test_inspect_unreclaimable_lock_reports_unknown_never_recoverable(
    sessions_dir: Path, open_session
) -> None:
    # A reclaimable=False lock (pre-subprocess supervisor hold) is never
    # liveness-classified and never recoverable within its TTL.
    _acquire(sessions_dir, reclaimable=False)
    inspection = inspect_session(
        sessions_dir,
        "sess-inspect",
        liveness_probe=_probe(running=False),
        now=T0 + timedelta(minutes=1),
    )
    assert inspection.lease_held is True
    assert inspection.holder_liveness == "unknown"
    assert inspection.lease_recoverable is False


def test_inspect_expired_lease_is_not_held_and_recoverable(
    sessions_dir: Path, open_session
) -> None:
    _acquire(sessions_dir, lease_seconds=60)
    inspection = inspect_session(
        sessions_dir,
        "sess-inspect",
        liveness_probe=_probe(running=True),
        now=T0 + timedelta(hours=2),
    )
    assert inspection.lease_held is False
    assert inspection.holder_liveness == "alive"
    assert inspection.lease_recoverable is True


def test_inspect_garbage_lock_fails_closed_as_expired_unknown(
    sessions_dir: Path, open_session
) -> None:
    (sessions_dir / "sess-inspect" / "lock.json").write_text(
        "{not json", encoding="utf-8"
    )
    inspection = inspect_session(
        sessions_dir,
        "sess-inspect",
        liveness_probe=_probe(running=True),
        now=T0,
    )
    assert inspection.lease_held is False
    assert inspection.holder_liveness == "unknown"
    assert inspection.lease_recoverable is True


def test_inspect_unknown_holder_within_ttl_blocks_recovery(
    sessions_dir: Path, open_session
) -> None:
    # An indeterminate probe means possibly-alive: never recoverable in-TTL.
    _acquire(sessions_dir)
    inspection = inspect_session(
        sessions_dir,
        "sess-inspect",
        liveness_probe=_probe(running=None),
        now=T0 + timedelta(minutes=1),
    )
    assert inspection.lease_held is True
    assert inspection.holder_liveness == "unknown"
    assert inspection.lease_recoverable is False


# --------------------------------------------------------------------------- #
# Corrupt / off-vocabulary artifacts degrade, never fabricate, never echo
# --------------------------------------------------------------------------- #


def test_inspect_corrupt_session_record_reports_state_none(
    sessions_dir: Path, open_session
) -> None:
    (sessions_dir / "sess-inspect" / "session.json").write_text(
        "{broken", encoding="utf-8"
    )
    inspection = inspect_session(sessions_dir, "sess-inspect")
    assert inspection.exists is True
    assert inspection.state is None


def test_inspect_off_vocabulary_record_state_reports_none(
    sessions_dir: Path, open_session
) -> None:
    record_path = sessions_dir / "sess-inspect" / "session.json"
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["state"] = "weird_state_token"
    record_path.write_text(json.dumps(payload), encoding="utf-8")
    inspection = inspect_session(sessions_dir, "sess-inspect")
    assert inspection.exists is True
    assert inspection.state is None


def test_inspect_corrupt_result_json_reports_status_none(
    sessions_dir: Path, open_session
) -> None:
    turn_dir = _make_turn(sessions_dir, "sess-inspect", "turn_20260709T100000Z_ee000005")
    (turn_dir / "result.json").write_text("[[[", encoding="utf-8")
    inspection = inspect_session(sessions_dir, "sess-inspect")
    assert inspection.latest_turn_status is None


def test_inspect_off_vocabulary_turn_status_never_echoed(
    sessions_dir: Path, open_session
) -> None:
    _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_ff000006",
        result={"status": "totally_made_up_status"},
    )
    inspection = inspect_session(sessions_dir, "sess-inspect")
    assert inspection.latest_turn_status is None
    turns = list_turns(sessions_dir, "sess-inspect")
    assert turns[0].status is None


def test_inspect_corrupt_progress_reports_none(
    sessions_dir: Path, open_session
) -> None:
    turn_dir = _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_aa000007",
        result={"status": "completed"},
    )
    (turn_dir / "progress.json").write_text(
        json.dumps({"schema_version": "x", "state": 3}), encoding="utf-8"
    )
    inspection = inspect_session(sessions_dir, "sess-inspect")
    assert inspection.latest_turn_status == "completed"
    assert inspection.progress is None


def test_list_turns_with_corrupt_record_still_enumerates(
    sessions_dir: Path, open_session
) -> None:
    _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_ab000008",
        result={"status": "completed"},
    )
    (sessions_dir / "sess-inspect" / "session.json").write_text(
        "{broken", encoding="utf-8"
    )
    turns = list_turns(sessions_dir, "sess-inspect")
    assert [t.turn_id for t in turns] == ["turn_20260709T100000Z_ab000008"]


# --------------------------------------------------------------------------- #
# Boundary guards: zero subprocess, zero raw content, frozen results
# --------------------------------------------------------------------------- #

import dataclasses  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path as _PathAlias  # noqa: E402

import agent_run_supervisor.session_inspect as inspect_mod  # noqa: E402


def test_inspection_never_spawns_a_subprocess(
    monkeypatch, sessions_dir: Path, open_session
) -> None:
    _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_ab00000a",
        result={"status": "completed", "final_message": "x"},
        progress=_progress_payload(),
    )
    _acquire(sessions_dir)

    def _forbidden(*args, **kwargs):
        raise AssertionError("session inspection must never spawn a process")

    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(os, "system", _forbidden)
    monkeypatch.setattr(os, "posix_spawn", _forbidden, raising=False)
    monkeypatch.setattr(os, "posix_spawnp", _forbidden, raising=False)
    monkeypatch.setattr(os, "fork", _forbidden, raising=False)
    monkeypatch.setattr(os, "execv", _forbidden, raising=False)

    inspection = inspect_session(
        sessions_dir, "sess-inspect", liveness_probe=_probe(running=True)
    )
    turns = list_turns(sessions_dir, "sess-inspect")

    assert inspection.exists is True
    assert inspection.turn_count == 1
    assert [t.turn_id for t in turns] == ["turn_20260709T100000Z_ab00000a"]


def test_module_source_never_imports_spawn_or_network_surfaces() -> None:
    src = _PathAlias(inspect_mod.__file__).read_text(encoding="utf-8")
    import_lines = [
        line for line in src.splitlines() if line.startswith(("import ", "from "))
    ]
    for line in import_lines:
        for root in ("subprocess", "socket", "http", "urllib", "requests"):
            assert root not in line, f"forbidden import {root!r}: {line!r}"
    lowered = src.lower()
    for token in ("subprocess.", "os.system(", ".popen(", "socket.socket("):
        assert token not in lowered, token


def test_inspection_results_never_carry_raw_turn_content(
    sessions_dir: Path, open_session
) -> None:
    prompt_canary = "raw-prompt-canary-text-b7c1"
    message_canary = "final-message-canary-text-e9d2"
    turn_dir = _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_ac00000b",
        result={"status": "completed", "final_message": message_canary},
        progress=_progress_payload(),
    )
    (turn_dir / "prompt.txt").write_text(prompt_canary, encoding="utf-8")
    (turn_dir / "normalized-events.jsonl").write_text(
        json.dumps({"seq": 1, "type": "agent_message", "text": message_canary}) + "\n",
        encoding="utf-8",
    )

    inspection = inspect_session(sessions_dir, "sess-inspect")
    turns = list_turns(sessions_dir, "sess-inspect")

    surface = repr(inspection) + repr(turns)
    assert message_canary not in surface
    assert prompt_canary not in surface
    # The only path-typed field is the caller-private TurnInfo.turn_dir; the
    # public SessionInspection carries no filesystem path at all.
    assert str(turn_dir) not in repr(inspection)
    assert all(isinstance(t.turn_dir, _PathAlias) for t in turns)


def test_inspection_result_types_are_frozen(sessions_dir: Path, open_session) -> None:
    inspection = inspect_session(sessions_dir, "sess-inspect")
    with pytest.raises(dataclasses.FrozenInstanceError):
        inspection.exists = False  # type: ignore[misc]
    _make_turn(
        sessions_dir,
        "sess-inspect",
        "turn_20260709T100000Z_ad00000c",
        result={"status": "completed"},
    )
    (turn,) = list_turns(sessions_dir, "sess-inspect")
    with pytest.raises(dataclasses.FrozenInstanceError):
        turn.status = "completed"  # type: ignore[misc]

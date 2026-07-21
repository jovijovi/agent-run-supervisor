"""C6: L1 store-isolation regression + structural call-site guard.

Native state binds only through the native_acp.storage seam into
``native-sessions/`` / ``native-runs/``; poisoned same-ID legacy roots are
never read and never change (byte-snapshot proven). The AST guard walks the
whole package, so modules added by later slices are covered automatically.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_run_supervisor.process_liveness import ProcessIdentity
from agent_run_supervisor.session import (
    STATE_OPEN,
    SessionStore,
)

T0 = dt.datetime(2026, 7, 21, 0, 0, 0, tzinfo=dt.timezone.utc)

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "agent_run_supervisor"
PACKAGE_DIR = SRC_ROOT / "native_acp"

NATIVE_KWARGS = dict(
    profile_id="opencode-1.18.4",
    profile_revision=1,
    profile_hash="e" * 64,
    owner="hermes",
    namespace="hermes/doc-check",
    workspace_hash="b" * 64,
    effective_cwd="/work/project",
    matched_root="/work",
)

POISON_BYTES = b"{ this is deliberately not parseable json"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _identity() -> ProcessIdentity:
    return ProcessIdentity(pid=4242, process_start="123", boot_id="boot", host="host")


def _snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    entries: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        entries[relative] = path.read_bytes() if path.is_file() else None
    return entries


def _seed_legacy(root: Path, session_id: str, payload: bytes) -> None:
    legacy_dir = root / "sessions" / session_id
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "session.json").write_bytes(payload)


def _native_matrix(storage, store: SessionStore, session_id: str) -> None:
    """The full Native session operation set against one record."""
    storage.create_native_session(store, session_id=session_id, now=T0, **NATIVE_KWARGS)
    store.open_session(session_id)
    assert [record.session_id for record in store.list_records()] == [session_id]
    lock = store.acquire_lock(
        session_id, "runtask", required_state=STATE_OPEN, reclaimable=False, now=T0
    )
    store.update_lock_holder(
        session_id,
        lock.token,
        identity=_identity(),
        holder_kind="native_agent",
        reclaimable=False,
        now=T0,
    )
    store.release_lock(session_id, lock.token)
    storage.bind_agent_session(store, session_id, agent_session_id="external-1", now=T0)
    store.commit_last_effective(session_id, model="a/b", effort="max", now=T0)
    from agent_run_supervisor.session import validate_native_binding

    validate_native_binding(
        store.open_session(session_id),
        profile=SimpleNamespace(
            profile_id="opencode-1.18.4", revision=1, profile_hash="e" * 64
        ),
        workspace_result=SimpleNamespace(workspace_hash="b" * 64),
        owner="hermes",
        namespace="hermes/doc-check",
        for_load=True,
    )
    store.mark_quarantined(session_id, reason="matrix end", run_id="run-x", now=T0)


# -- seam binding ------------------------------------------------------------


def test_seam_binds_stores_to_native_roots(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp import storage

    root = tmp_path / ".agent-run-supervisor"
    session_store = storage.native_session_store(root)
    event_store = storage.native_event_store(root)
    assert session_store.base_dir == root / "native-sessions"
    assert event_store.base_dir == root / "native-runs"

    storage.create_native_session(
        session_store, session_id="native-1", now=T0, **NATIVE_KWARGS
    )
    assert (root / "native-sessions" / "native-1" / "session.json").is_file()
    # Nothing materialized outside the native roots.
    assert sorted(entry.name for entry in root.iterdir()) == [
        "native-runs",
        "native-sessions",
    ]


def test_secure_modes_and_insecure_root_correction(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp import storage

    root = tmp_path / ".agent-run-supervisor"
    # Pre-seed an insecure native root: the seam must correct it to 0700.
    (root / "native-sessions").mkdir(parents=True)
    (root / "native-sessions").chmod(0o755)
    (root / "native-runs").mkdir()
    (root / "native-runs").chmod(0o755)

    session_store = storage.native_session_store(root)
    event_store = storage.native_event_store(root)
    assert _mode(root / "native-sessions") == 0o700
    assert _mode(root / "native-runs") == 0o700

    storage.create_native_session(
        session_store, session_id="native-1", now=T0, **NATIVE_KWARGS
    )
    session_dir = root / "native-sessions" / "native-1"
    assert _mode(session_dir) == 0o700
    assert _mode(session_dir / "session.json") == 0o600
    session_store.acquire_lock("native-1", "owner", required_state=STATE_OPEN, now=T0)
    assert _mode(session_dir / "lock.json") == 0o600

    handle = event_store.create_run("run-1")
    assert _mode(handle.run_dir) == 0o700
    artifact = storage.write_once_json(handle.run_dir / "spec.json", {"a": 1})
    assert _mode(artifact) == 0o600


def test_write_once_json_never_overwrites(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp import storage

    target = tmp_path / "artifact.json"
    storage.write_once_json(target, {"first": True})
    first_bytes = target.read_bytes()
    assert json.loads(first_bytes.decode("utf-8")) == {"first": True}
    with pytest.raises(FileExistsError):
        storage.write_once_json(target, {"second": True})
    assert target.read_bytes() == first_bytes


# -- same-ID coexistence and no-legacy-read/write ----------------------------


def test_same_id_sessions_coexist_without_collision(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp import storage

    root = tmp_path / ".agent-run-supervisor"
    legacy_payload = json.dumps({"session_id": "shared-id", "state": "open"}).encode()
    _seed_legacy(root, "shared-id", legacy_payload)

    store = storage.native_session_store(root)
    storage.create_native_session(store, session_id="shared-id", now=T0, **NATIVE_KWARGS)

    native_record = store.open_session("shared-id")
    assert native_record.session_kind == "native"
    assert native_record.owner == "hermes"
    assert [record.session_id for record in store.list_records()] == ["shared-id"]
    # The legacy record still exists, untouched, invisible to the native store.
    assert (root / "sessions" / "shared-id" / "session.json").read_bytes() == (
        legacy_payload
    )


def test_same_id_runs_coexist_without_collision(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp import storage

    root = tmp_path / ".agent-run-supervisor"
    legacy_run = root / "runs" / "run-77"
    legacy_run.mkdir(parents=True)
    (legacy_run / "result.json").write_bytes(b'{"status": "completed"}')

    handle = storage.native_event_store(root).create_run("run-77")
    assert handle.run_dir == root / "native-runs" / "run-77"
    assert handle.run_dir.is_dir()
    assert (legacy_run / "result.json").read_bytes() == b'{"status": "completed"}'


def test_poisoned_legacy_roots_are_never_read_or_written(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp import storage

    root = tmp_path / ".agent-run-supervisor"
    _seed_legacy(root, "shared-id", POISON_BYTES)
    legacy_runs = root / "runs" / "run-x"
    legacy_runs.mkdir(parents=True)
    (legacy_runs / "events.jsonl").write_bytes(POISON_BYTES)

    before_sessions = _snapshot(root / "sessions")
    before_runs = _snapshot(root / "runs")

    store = storage.native_session_store(root)
    event_store = storage.native_event_store(root)
    # Every native operation succeeds despite the poisoned same-ID legacy
    # record — any read of the legacy root would surface the poison.
    _native_matrix(storage, store, "shared-id")
    handle = event_store.create_run("run-x")
    storage.write_once_json(handle.run_dir / "spec.json", {"spec": True})
    handle.append_ndjson("events.jsonl", {"seq": 1, "type": "run_started"})

    assert _snapshot(root / "sessions") == before_sessions
    assert _snapshot(root / "runs") == before_runs


# -- structural call-site guard (extends automatically to later slices) ------


def _package_modules() -> list[Path]:
    assert PACKAGE_DIR.is_dir()
    return sorted(PACKAGE_DIR.glob("*.py"))


def _calls(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.append(node.func.attr)
    return names


def test_store_constructors_only_in_storage_seam() -> None:
    for module_path in _package_modules():
        if module_path.name == "storage.py":
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        offenders = [
            name
            for name in _calls(tree)
            if name in {"SessionStore", "EventStore"}
        ]
        assert not offenders, f"{module_path.name} constructs stores: {offenders}"


def test_no_legacy_root_literals_in_package() -> None:
    for module_path in _package_modules():
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {"sessions", "runs"}, (
                    f"{module_path.name} spells a legacy root literal"
                )
                if node.value in {"native-sessions", "native-runs"}:
                    assert module_path.name == "storage.py", (
                        f"{module_path.name} spells a native root literal "
                        "outside the seam"
                    )


def test_no_agent_role_spec_reference_in_package() -> None:
    for module_path in _package_modules():
        assert "AgentRoleSpec" not in module_path.read_text(encoding="utf-8"), (
            f"{module_path.name} references AgentRoleSpec"
        )


def test_native_record_creation_goes_through_the_seam_only() -> None:
    seam_names = {"create_native_session", "bind_agent_session"}
    for module_path in _package_modules():
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in seam_names:
                assert module_path.name == "storage.py", (
                    f"{module_path.name} calls {node.func.id} directly"
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in seam_names
            ):
                if module_path.name == "storage.py":
                    continue  # the seam wrappers delegate to the store methods
                base = node.func.value
                assert isinstance(base, ast.Name) and base.id == "storage", (
                    f"{module_path.name} calls {node.func.attr} outside the "
                    "storage seam wrappers"
                )


def test_no_module_outside_native_acp_calls_the_native_creation_api() -> None:
    seam_names = {"create_native_session", "bind_agent_session"}
    for module_path in sorted(SRC_ROOT.rglob("*.py")):
        relative = module_path.relative_to(SRC_ROOT)
        if relative.parts[0] == "native_acp":
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        offenders = [name for name in _calls(tree) if name in seam_names]
        assert not offenders, (
            f"{relative} calls the native creation API: {offenders}"
        )

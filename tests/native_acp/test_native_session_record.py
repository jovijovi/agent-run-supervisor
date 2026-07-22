"""C6: native session identity, quarantine, quarantine-atomic lease, and the
zero-migration byte-identity proof for legacy acpx records."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_run_supervisor import session_inspect
from agent_run_supervisor.process_liveness import ProcessIdentity
from agent_run_supervisor.session import (
    STATE_CLOSED,
    STATE_OPEN,
    STATE_QUARANTINED,
    SessionBindingError,
    SessionClosedError,
    SessionLockError,
    SessionQuarantinedError,
    SessionRecord,
    SessionStore,
    _json_bytes,
    _record_to_dict,
    validate_native_binding,
)

T0 = dt.datetime(2026, 7, 21, 0, 0, 0, tzinfo=dt.timezone.utc)

# Golden bytes generated from the pre-change serializer at commit ec4cf34 —
# the zero-migration proof that 0.1.7-shaped acpx records still serialize to
# the exact same bytes.
LEGACY_GOLDEN_MINIMAL = (
    b'{\n  "acpx_session_id": null,\n  "acpx_version": "0.12.0",\n  "adapter_agent": "codex",\n'
    b'  "created_at": "2026-07-21T00:00:00+00:00",\n  "effective_cwd": "/work/project",\n'
    b'  "matched_root": "/work",\n  "policy_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",\n'
    b'  "role_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n'
    b'  "role_id": "doc-check",\n  "schema_version": 1,\n  "session_id": "legacy-sess-1",\n'
    b'  "session_name": null,\n  "state": "open",\n  "updated_at": "2026-07-21T00:00:00+00:00",\n'
    b'  "workspace_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"\n}'
)
LEGACY_GOLDEN_WITH_MCP = (
    b'{\n  "acpx_session_id": "acpx-abc",\n  "acpx_version": "0.12.0",\n  "adapter_agent": "codex",\n'
    b'  "created_at": "2026-07-21T00:00:00+00:00",\n  "effective_cwd": "/work/project",\n'
    b'  "matched_root": null,\n  "mcp_config_path": "/work/mcp.json",\n'
    b'  "mcp_config_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",\n'
    b'  "policy_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",\n'
    b'  "role_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n'
    b'  "role_id": "doc-check",\n  "schema_version": 1,\n  "session_id": "legacy-sess-2",\n'
    b'  "session_name": "named",\n  "state": "closed",\n  "updated_at": "2026-07-21T01:00:00+00:00",\n'
    b'  "workspace_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"\n}'
)

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


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(base_dir=tmp_path / "native-root")


def _native(store: SessionStore, session_id: str = "native-1") -> SessionRecord:
    return store.create_native_session(session_id=session_id, now=T0, **NATIVE_KWARGS)


def _profile(**overrides) -> SimpleNamespace:
    values = dict(profile_id="opencode-1.18.4", revision=1, profile_hash="e" * 64)
    values.update(overrides)
    return SimpleNamespace(**values)


def _workspace(workspace_hash: str = "b" * 64) -> SimpleNamespace:
    return SimpleNamespace(workspace_hash=workspace_hash)


def _identity() -> ProcessIdentity:
    return ProcessIdentity(pid=4242, process_start="123", boot_id="boot", host="testhost")


# -- zero-migration byte identity ------------------------------------------


def test_legacy_records_serialize_byte_identical() -> None:
    minimal = SessionRecord(
        schema_version=1,
        session_id="legacy-sess-1",
        role_id="doc-check",
        role_hash="a" * 64,
        workspace_hash="b" * 64,
        policy_hash="c" * 64,
        acpx_version="0.12.0",
        adapter_agent="codex",
        effective_cwd="/work/project",
        matched_root="/work",
        state="open",
        created_at="2026-07-21T00:00:00+00:00",
        updated_at="2026-07-21T00:00:00+00:00",
        acpx_session_id=None,
        session_name=None,
    )
    assert _json_bytes(_record_to_dict(minimal)) == LEGACY_GOLDEN_MINIMAL

    with_mcp = SessionRecord(
        schema_version=1,
        session_id="legacy-sess-2",
        role_id="doc-check",
        role_hash="a" * 64,
        workspace_hash="b" * 64,
        policy_hash="c" * 64,
        acpx_version="0.12.0",
        adapter_agent="codex",
        effective_cwd="/work/project",
        matched_root=None,
        state="closed",
        created_at="2026-07-21T00:00:00+00:00",
        updated_at="2026-07-21T01:00:00+00:00",
        acpx_session_id="acpx-abc",
        session_name="named",
        mcp_config_path="/work/mcp.json",
        mcp_config_sha256="d" * 64,
    )
    assert _json_bytes(_record_to_dict(with_mcp)) == LEGACY_GOLDEN_WITH_MCP


# -- native creation contract ----------------------------------------------


def test_create_native_session_round_trip_and_exact_key_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _native(store)
    assert record.session_kind == "native"
    assert record.state == STATE_OPEN  # canonical active persists as open
    assert record.agent_session_id is None

    raw = json.loads(
        (store.base_dir / "native-1" / "session.json").read_text(encoding="utf-8")
    )
    # No legacy role/policy/acpx key, no sentinel, no null-valued native key.
    assert set(raw) == {
        "schema_version",
        "session_id",
        "workspace_hash",
        "effective_cwd",
        "matched_root",
        "state",
        "created_at",
        "updated_at",
        "session_kind",
        "native_profile_id",
        "native_profile_revision",
        "native_profile_hash",
        "owner",
        "namespace",
    }
    assert raw["state"] == "open"
    assert None not in raw.values()

    reopened = store.open_session("native-1")
    assert reopened == record


def test_bind_agent_session_commits_exactly_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    bound = store.bind_agent_session(
        "native-1", agent_session_id="external-xyz", now=T0
    )
    assert bound.agent_session_id == "external-xyz"
    with pytest.raises(SessionBindingError):
        store.bind_agent_session("native-1", agent_session_id="external-xyz", now=T0)
    with pytest.raises(SessionBindingError):
        store.bind_agent_session("native-1", agent_session_id="other", now=T0)
    assert store.open_session("native-1").agent_session_id == "external-xyz"


def test_active_open_state_bijection(tmp_path: Path) -> None:
    from agent_run_supervisor.native_acp.storage import (
        to_native_state,
        to_persisted_state,
    )

    store = _store(tmp_path)
    record = _native(store)
    assert record.state == "open"  # persisted compatibility value
    assert to_native_state(record.state) == "active"
    assert to_persisted_state("active") == "open"
    assert to_native_state("closed") == "closed"
    assert to_persisted_state("closed") == "closed"
    assert to_native_state("quarantined") == "quarantined"
    assert to_persisted_state("quarantined") == "quarantined"
    with pytest.raises(ValueError):
        to_native_state("active")  # 'active' never persists
    with pytest.raises(ValueError):
        to_persisted_state("open")  # 'open' is not a canonical native state


def test_native_omit_when_unset_grows_with_observations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    store.bind_agent_session("native-1", agent_session_id="external-xyz", now=T0)
    store.commit_last_effective(
        "native-1", model="kimi-for-coding/k3", effort="max", now=T0
    )
    raw = json.loads(
        (store.base_dir / "native-1" / "session.json").read_text(encoding="utf-8")
    )
    assert raw["agent_session_id"] == "external-xyz"
    assert raw["last_effective_model"] == "kimi-for-coding/k3"
    assert raw["last_effective_effort"] == "max"
    assert "quarantine_reason" not in raw
    assert "role_id" not in raw
    assert None not in raw.values()
    record = store.open_session("native-1")
    assert record.last_effective_model == "kimi-for-coding/k3"
    assert record.last_effective_effort == "max"


# -- native binding validation ----------------------------------------------


def test_validate_native_binding_matrix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    record = store.open_session("native-1")

    validate_native_binding(
        record,
        profile=_profile(),
        workspace_result=_workspace(),
        owner="hermes",
        namespace="hermes/doc-check",
    )

    for bad_profile in (
        _profile(profile_id="other-agent-1.0"),
        _profile(revision=2),
        _profile(profile_hash="f" * 64),
    ):
        with pytest.raises(SessionBindingError):
            validate_native_binding(
                record,
                profile=bad_profile,
                workspace_result=_workspace(),
                owner="hermes",
                namespace="hermes/doc-check",
            )

    with pytest.raises(SessionBindingError):
        validate_native_binding(
            record,
            profile=_profile(),
            workspace_result=_workspace("9" * 64),
            owner="hermes",
            namespace="hermes/doc-check",
        )
    with pytest.raises(SessionBindingError):
        validate_native_binding(
            record,
            profile=_profile(),
            workspace_result=_workspace(),
            owner="intruder",
            namespace="hermes/doc-check",
        )
    with pytest.raises(SessionBindingError):
        validate_native_binding(
            record,
            profile=_profile(),
            workspace_result=_workspace(),
            owner="hermes",
            namespace="hermes/other",
        )

    # session/load path requires the committed external id.
    with pytest.raises(SessionBindingError):
        validate_native_binding(
            record,
            profile=_profile(),
            workspace_result=_workspace(),
            owner="hermes",
            namespace="hermes/doc-check",
            for_load=True,
        )
    store.bind_agent_session("native-1", agent_session_id="external-xyz", now=T0)
    bound = store.open_session("native-1")
    validate_native_binding(
        bound,
        profile=_profile(),
        workspace_result=_workspace(),
        owner="hermes",
        namespace="hermes/doc-check",
        for_load=True,
    )

    # model/effort deltas are never a binding mismatch: a new Run's frozen
    # Spec is the legitimate switching input.
    store.commit_last_effective("native-1", model="a/b", effort="low", now=T0)
    validate_native_binding(
        store.open_session("native-1"),
        profile=_profile(),
        workspace_result=_workspace(),
        owner="hermes",
        namespace="hermes/doc-check",
        for_load=True,
    )


def test_validate_native_binding_refuses_quarantined(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    store.mark_quarantined("native-1", reason="observation lost", run_id="run-9", now=T0)
    with pytest.raises(SessionQuarantinedError):
        validate_native_binding(
            store.open_session("native-1"),
            profile=_profile(),
            workspace_result=_workspace(),
            owner="hermes",
            namespace="hermes/doc-check",
        )


# -- quarantine + lease atomicity -------------------------------------------


def test_quarantine_is_irreversible_and_refuses_reuse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    first = store.mark_quarantined(
        "native-1", reason="observation lost", run_id="run-9", now=T0
    )
    assert first.state == STATE_QUARANTINED
    assert first.quarantine_reason == "observation lost"
    assert first.quarantined_by_run_id == "run-9"

    # Idempotent-safe repeat: first fact wins, no error.
    again = store.mark_quarantined(
        "native-1", reason="second reason", run_id="run-10", now=T0
    )
    assert again.quarantine_reason == "observation lost"
    assert again.quarantined_by_run_id == "run-9"

    record = store.open_session("native-1")
    with pytest.raises(SessionQuarantinedError):
        SessionStore.ensure_open(record)
    assert isinstance(SessionQuarantinedError("x"), SessionClosedError)
    with pytest.raises(SessionQuarantinedError):
        store.mark_closed("native-1", now=T0)  # no un-quarantine via close
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock("native-1", "owner", required_state=STATE_OPEN, now=T0)
    assert not (store.base_dir / "native-1" / "lock.json").exists()


def test_lease_race_quarantine_first_never_mints_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    store.mark_quarantined("native-1", reason="r", run_id="run-1", now=T0)
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock("native-1", "owner", required_state=STATE_OPEN, now=T0)
    assert not (store.base_dir / "native-1" / "lock.json").exists()


def test_lease_race_lock_first_keeps_holder_lease_valid(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    lock = store.acquire_lock(
        "native-1",
        "runtask",
        required_state=STATE_OPEN,
        reclaimable=False,
        now=T0,
    )
    # Quarantine commits while the lease is held; it never unlinks lock.json.
    store.mark_quarantined("native-1", reason="kill after dispatch", run_id="run-2", now=T0)
    assert (store.base_dir / "native-1" / "lock.json").exists()
    # The quarantining finalizer's own lease stays valid for finalization
    # writes (holder update + release).
    store.update_lock_holder(
        "native-1",
        lock.token,
        identity=_identity(),
        holder_kind="native_agent",
        reclaimable=False,
        now=T0,
    )
    store.release_lock("native-1", lock.token)
    # Every later acquire refuses: no usable new lease on a quarantined record.
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock("native-1", "owner", required_state=STATE_OPEN, now=T0)


def test_expired_lock_reclamation_cannot_bypass_quarantine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    store.acquire_lock(
        "native-1", "runtask", required_state=STATE_OPEN, lease_seconds=1, now=T0
    )
    store.mark_quarantined("native-1", reason="r", run_id="run-3", now=T0)
    later = T0 + dt.timedelta(hours=2)
    lock_path = store.base_dir / "native-1" / "lock.json"
    before = lock_path.read_bytes()
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock("native-1", "other", required_state=STATE_OPEN, now=later)
    # The refusal neither created nor unlinked any lock: the expired lease
    # file is byte-identical.
    assert lock_path.read_bytes() == before


def test_required_state_none_preserves_legacy_acquire_behavior(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store, "native-closed")
    store.mark_closed("native-closed", now=T0)
    # Legacy default: acquire_lock never read session state before this
    # change, so required_state=None must keep acquiring on a closed record.
    lock = store.acquire_lock("native-closed", "legacy-owner", now=T0)
    store.release_lock("native-closed", lock.token)
    with pytest.raises(SessionClosedError):
        store.acquire_lock(
            "native-closed", "native-owner", required_state=STATE_OPEN, now=T0
        )


# -- legacy inspection surface + last-effective ------------------------------


def test_read_record_state_degrades_to_none_for_quarantined(tmp_path: Path) -> None:
    # Pinned exactly as the C1 G5 inventory classified this acpx-only
    # surface: a quarantined record is outside (STATE_OPEN, STATE_CLOSED),
    # so the legacy inspection state degrades to None.
    store = _store(tmp_path)
    _native(store)
    store.mark_quarantined("native-1", reason="r", run_id="run-4", now=T0)
    assert session_inspect._read_record_state(store, "native-1") is None


def test_commit_last_effective_updates_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _native(store)
    later = T0 + dt.timedelta(minutes=5)
    updated = store.commit_last_effective(
        "native-1", model="kimi-for-coding/k3", effort="max", now=later
    )
    assert updated.last_effective_model == "kimi-for-coding/k3"
    assert updated.last_effective_effort == "max"
    assert updated.updated_at == later.isoformat()
    reread = store.open_session("native-1")
    assert reread.last_effective_model == "kimi-for-coding/k3"
    assert reread.last_effective_effort == "max"

    store.mark_quarantined("native-1", reason="r", run_id="run-5", now=later)
    with pytest.raises(SessionQuarantinedError):
        store.commit_last_effective("native-1", model="a/b", effort="low", now=later)


def test_r5_b3_quarantine_pending_fence_blocks_acquire_even_after_ttl(
    tmp_path: Path,
) -> None:
    import datetime as dt

    from agent_run_supervisor.session import (
        QUARANTINE_PENDING_JSON,
        STATE_OPEN,
        SessionQuarantinedError,
    )

    store = _store(tmp_path)
    _native(store)
    lock = store.acquire_lock(
        "native-1", "owner", required_state=STATE_OPEN, now=T0, lease_seconds=1
    )
    assert lock.token
    store.write_quarantine_pending(
        "native-1", reason="interrupted", run_id="run-fence", now=T0
    )
    session_dir = tmp_path / "native-root" / "native-1"
    assert (session_dir / QUARANTINE_PENDING_JSON).is_file()
    assert store.open_session("native-1").state == "open"
    later = T0 + dt.timedelta(seconds=3600)
    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock(
            "native-1", "other", required_state=STATE_OPEN, now=later
        )
    # Successful quarantine clears fence.
    store.mark_quarantined(
        "native-1", reason="converged", run_id="run-fence", now=later
    )
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()
    assert store.open_session("native-1").state == "quarantined"


def test_r5_b3_mark_quarantined_clears_preexisting_fence_when_already_quarantined(
    tmp_path: Path,
) -> None:
    from agent_run_supervisor.session import QUARANTINE_PENDING_JSON

    store = _store(tmp_path)
    _native(store)
    store.mark_quarantined("native-1", reason="first", run_id="run-1", now=T0)
    session_dir = tmp_path / "native-root" / "native-1"
    # Simulate a stale fence left beside an already-quarantined record.
    (session_dir / QUARANTINE_PENDING_JSON).write_text(
        '{"schema":"ars.quarantine_pending","version":1,"run_id":"run-1",'
        '"reason":"stale","timestamp":"t"}',
        encoding="utf-8",
    )
    again = store.mark_quarantined(
        "native-1", reason="ignored", run_id="run-2", now=T0
    )
    assert again.quarantine_reason == "first"
    assert not (session_dir / QUARANTINE_PENDING_JSON).exists()

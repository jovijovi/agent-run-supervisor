"""PR-B WP4: the session compatibility epoch and its fail-closed reuse gate (C11).

Reuse requires equal profile ID/revision/``adapter_contract_hash``, equal
workspace/owner/namespace, **and** equal epoch. A missing or different epoch is
rejected before any lease mutation and before ``session/load``, and never
degrades into ``session/new``.

Pre-epoch records stay ``status``/``list``/``close``-readable: only ``load``
fails closed on them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.session import (
    SESSION_KIND_NATIVE,
    STATE_OPEN,
    SessionBindingError,
    SessionStore,
)
from agent_run_supervisor.session import validate_native_binding


class _Profile:
    profile_id = "opencode-native-acp"
    revision = 3

    def profile_hash(self) -> str:
        return "a" * 64

    def adapter_contract_hash(self) -> str:
        return "c" * 64


class _Workspace:
    workspace_hash = "w" * 64


def _store(tmp_path: Path) -> SessionStore:
    return storage.native_session_store(tmp_path)


def _create(store: SessionStore, session_id: str, **overrides):
    kwargs = dict(
        session_id=session_id,
        profile_id="opencode-native-acp",
        profile_revision=3,
        profile_hash="a" * 64,
        owner="hermes",
        namespace="hermes/ns",
        workspace_hash="w" * 64,
        effective_cwd="/tmp/ws",
        matched_root="/tmp/ws",
    )
    kwargs.update(overrides)
    return storage.create_native_session(store, **kwargs)


def _record_json(tmp_path: Path, session_id: str) -> dict:
    return json.loads(
        (tmp_path / storage.NATIVE_SESSIONS_DIRNAME / session_id / "session.json").read_text(
            encoding="utf-8"
        )
    )


# -- persistence --------------------------------------------------------------


def test_epoch_and_contract_hash_persist_when_supplied(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _create(
        store,
        "sess-epoch-1",
        session_compatibility_epoch=7,
        adapter_contract_hash="c" * 64,
    )
    assert record.session_compatibility_epoch == 7
    assert record.native_adapter_contract_hash == "c" * 64
    payload = _record_json(tmp_path, "sess-epoch-1")
    assert payload["session_compatibility_epoch"] == 7
    assert payload["native_adapter_contract_hash"] == "c" * 64
    assert store.open_session("sess-epoch-1").session_compatibility_epoch == 7


def test_pre_epoch_records_keep_their_exact_serialized_shape(tmp_path: Path) -> None:
    """Additive and omit-when-unset: a record without an epoch has no key."""
    store = _store(tmp_path)
    _create(store, "sess-legacy-1")
    payload = _record_json(tmp_path, "sess-legacy-1")
    assert "session_compatibility_epoch" not in payload
    assert "native_adapter_contract_hash" not in payload
    assert payload["session_kind"] == SESSION_KIND_NATIVE
    assert payload["state"] == STATE_OPEN


def test_pre_epoch_records_stay_status_list_and_close_readable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create(store, "sess-legacy-2")
    # status
    record = store.open_session("sess-legacy-2")
    assert record.session_compatibility_epoch is None
    # list
    assert "sess-legacy-2" in {row.session_id for row in store.list_records()}
    # close
    closed = store.mark_closed("sess-legacy-2")
    assert closed.state == "closed"


# -- the reuse gate (C11) -----------------------------------------------------


def _validate(record, **overrides):
    kwargs = dict(
        profile=_Profile(),
        workspace_result=_Workspace(),
        owner="hermes",
        namespace="hermes/ns",
        expected_epoch=7,
        expected_contract_hash="c" * 64,
    )
    kwargs.update(overrides)
    return validate_native_binding(record, **kwargs)


def test_equal_epoch_and_contract_hash_admit_reuse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _create(
        store,
        "sess-reuse-ok",
        session_compatibility_epoch=7,
        adapter_contract_hash="c" * 64,
    )
    _validate(record)  # no raise


@pytest.mark.parametrize("epoch", [6, 8])
def test_a_different_epoch_is_rejected(tmp_path: Path, epoch: int) -> None:
    """Lower *and* higher: an epoch is an identity, not an ordering."""
    store = _store(tmp_path)
    record = _create(
        store,
        f"sess-reuse-{epoch}",
        session_compatibility_epoch=epoch,
        adapter_contract_hash="c" * 64,
    )
    with pytest.raises(SessionBindingError) as err:
        _validate(record)
    assert "session_compatibility_epoch" in str(err.value)


def test_a_missing_epoch_is_rejected_when_the_run_carries_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _create(store, "sess-reuse-none")
    with pytest.raises(SessionBindingError) as err:
        _validate(record)
    assert "session_compatibility_epoch" in str(err.value)


def test_a_different_contract_hash_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _create(
        store,
        "sess-reuse-contract",
        session_compatibility_epoch=7,
        adapter_contract_hash="d" * 64,
    )
    with pytest.raises(SessionBindingError) as err:
        _validate(record)
    assert "adapter_contract_hash" in str(err.value)


def test_a_run_without_an_epoch_still_refuses_a_record_that_has_one(
    tmp_path: Path,
) -> None:
    """A reverted runtime must not silently load a Binding-era Session."""
    store = _store(tmp_path)
    record = _create(
        store,
        "sess-reuse-era",
        session_compatibility_epoch=7,
        adapter_contract_hash="c" * 64,
    )
    with pytest.raises(SessionBindingError) as err:
        _validate(record, expected_epoch=None, expected_contract_hash=None)
    assert "session_compatibility_epoch" in str(err.value)


def test_a_bindingless_run_and_a_bindingless_record_still_reuse(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _create(store, "sess-reuse-plain")
    _validate(record, expected_epoch=None, expected_contract_hash=None)


# -- G6: agent identity is part of Session identity ---------------------------
#
# A Session belongs to one agent under one registration. Everything below fails
# closed *before* the lease and long before ``session/load``, and never degrades
# into ``session/new``.


class _AgentProfile(_Profile):
    profile_id = "standard-native-acp-v1"
    revision = 1


def _create_agent(store: SessionStore, session_id: str, **overrides):
    kwargs = dict(
        agent_id="fake-alpha",
        agent_registration_hash="r" * 64,
        adapter_contract_hash="c" * 64,
        session_compatibility_epoch=1,
        profile_id="standard-native-acp-v1",
        profile_revision=1,
    )
    kwargs.update(overrides)
    return _create(store, session_id, **kwargs)


def _validate_agent(record, **overrides):
    kwargs = dict(
        profile=_AgentProfile(),
        workspace_result=_Workspace(),
        owner="hermes",
        namespace="hermes/ns",
        expected_contract_hash="c" * 64,
        expected_epoch=1,
        expected_agent_id="fake-alpha",
        expected_agent_registration_hash="r" * 64,
    )
    kwargs.update(overrides)
    return validate_native_binding(record, **kwargs)


def test_agent_identity_persists_and_reuses_when_it_matches(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _create_agent(store, "agent-sess-1")
    assert record.native_agent_id == "fake-alpha"
    assert record.native_agent_registration_hash == "r" * 64
    persisted = _record_json(tmp_path, "agent-sess-1")
    assert persisted["native_agent_id"] == "fake-alpha"
    _validate_agent(store.open_session("agent-sess-1"))


def test_a_session_created_under_one_agent_is_refused_for_another(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_agent(store, "agent-sess-2")
    with pytest.raises(SessionBindingError, match="agent_id"):
        _validate_agent(store.open_session("agent-sess-2"), expected_agent_id="fake-beta")


def test_agent_identity_rejection_is_symmetric_in_both_directions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_agent(store, "agent-sess-3")
    # An agent-bearing record refused by a runtime that carries none...
    with pytest.raises(SessionBindingError, match="agent_id"):
        _validate_agent(
            store.open_session("agent-sess-3"),
            expected_agent_id=None,
            expected_agent_registration_hash=None,
        )
    # ...and a record with none refused by a runtime that carries one.
    _create(store, "agent-sess-4", adapter_contract_hash="c" * 64,
            session_compatibility_epoch=1)
    with pytest.raises(SessionBindingError, match="agent_id"):
        _validate_agent(store.open_session("agent-sess-4"))


def test_a_compatibility_bearing_registration_edit_retires_the_session(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_agent(store, "agent-sess-5")
    with pytest.raises(SessionBindingError, match="agent_registration_hash"):
        _validate_agent(
            store.open_session("agent-sess-5"),
            expected_agent_registration_hash="d" * 64,
        )


def test_a_provenance_only_edit_does_not_retire_the_session(tmp_path: Path) -> None:
    """The hash excludes provenance, so re-recording a receipt is not an edit."""
    from agent_run_supervisor.native_acp import agent_registration as ar
    from agent_run_supervisor.native_acp.profile import STANDARD_NATIVE_ACP_V1
    from tests.native_acp import binding_fixtures as fx

    before = fx.fake_registration_payload(fx.FAKE_ALPHA_ID, STANDARD_NATIVE_ACP_V1)
    after = fx.fake_registration_payload(fx.FAKE_ALPHA_ID, STANDARD_NATIVE_ACP_V1)
    after["provenance"] = dict(after["provenance"])
    after["provenance"]["accepted_at"] = "2026-09-09T09:00:00+08:00"
    assert ar.registration_hash(before) == ar.registration_hash(after)

    store = _store(tmp_path)
    _create_agent(store, "agent-sess-6", agent_registration_hash=ar.registration_hash(before))
    _validate_agent(
        store.open_session("agent-sess-6"),
        expected_agent_registration_hash=ar.registration_hash(after),
    )


def test_legacy_session_json_bytes_stay_byte_identical(tmp_path: Path) -> None:
    """G6: a non-agent record must not gain a key, not even a null one."""
    store = _store(tmp_path)
    _create(store, "legacy-sess-1")
    persisted = _record_json(tmp_path, "legacy-sess-1")
    assert "native_agent_id" not in persisted
    assert "native_agent_registration_hash" not in persisted

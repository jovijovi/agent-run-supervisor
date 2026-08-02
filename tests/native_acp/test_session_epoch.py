"""V4 §7.1 Session identity, and the continuity rule, exactly.

Native Session identity is: ``agent_id``, profile identity, owner, namespace,
``workspace_hash``, and an optional **operator** ``session_epoch``. The
ARS-derived identity fields of the retired line — ``adapter_contract_hash``,
``session_compatibility_epoch``, and ``agent_registration_hash`` — are gone from
identity. A legacy record that still carries any of them stays owner-scoped
``status``/``list``/``close``-readable and is **refused for ``session/load``**
with a stable code.

``session_epoch`` is an operator escape hatch. No automatic bump exists
anywhere: an AGENT or adapter version change, an ARS upgrade, a profile revision
that does not change ACP semantics, a ``command``/``args``/``env``/``mediation``
edit, a registry replacement, and a daemon restart never change it. Comparison
is symmetric equality, so a record at epoch 1 is refused by a Run at epoch 2
*and* by a Run with no epoch — which is why adding the field for the first time
cuts continuity: absent is not 1.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agent_run_supervisor import session as session_mod
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.profile import STANDARD_NATIVE_ACP_V1
from agent_run_supervisor.native_acp.spec import resolve_workspace_binding
from agent_run_supervisor.session import (
    LEGACY_SESSION_IDENTITY_FIELDS,
    SessionBindingError,
    validate_native_binding,
)

SESSION_SOURCE = Path(session_mod.__file__)
PROFILE = STANDARD_NATIVE_ACP_V1


def make_record(tmp_path: Path, *, session_epoch=None, agent_id="a-1"):
    store = storage.native_session_store(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    binding = resolve_workspace_binding(root=workspace)
    storage.create_native_session(
        store,
        session_id="s-1",
        profile_id=PROFILE.profile_id,
        profile_revision=PROFILE.revision,
        profile_hash=PROFILE.profile_hash(),
        owner="hermes",
        namespace="hermes/ns",
        workspace_hash=binding.workspace_hash,
        effective_cwd=binding.effective_cwd,
        matched_root=binding.canonical_root,
        session_epoch=session_epoch,
        agent_id=agent_id,
    )
    return store, binding, store.open_session("s-1")


def validate(record, binding, *, epoch=None, agent_id="a-1", for_load=False):
    validate_native_binding(
        record,
        profile=PROFILE,
        workspace_result=binding,
        owner="hermes",
        namespace="hermes/ns",
        for_load=for_load,
        expected_epoch=epoch,
        expected_agent_id=agent_id,
    )


# -- the identity field set --------------------------------------------------


def test_identity_is_agent_profile_owner_namespace_workspace_and_epoch(tmp_path):
    _, binding, record = make_record(tmp_path, session_epoch=1)
    validate(record, binding, epoch=1)
    assert record.native_agent_id == "a-1"
    assert record.native_session_epoch == 1


@pytest.mark.parametrize(
    "override,expected",
    [({"agent_id": "a-2"}, "agent_id"), ({"epoch": 2}, "session_epoch")],
)
def test_each_identity_field_refuses_on_mismatch(tmp_path, override, expected):
    _, binding, record = make_record(tmp_path, session_epoch=1)
    kwargs = {"epoch": 1, "agent_id": "a-1"}
    kwargs.update(override)
    with pytest.raises(SessionBindingError) as excinfo:
        validate(record, binding, **kwargs)
    assert expected in str(excinfo.value)


def test_the_three_retired_identity_fields_are_gone_from_identity():
    tree = ast.parse(SESSION_SOURCE.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "validate_native_binding"
    )
    parameters = {arg.arg for arg in function.args.kwonlyargs}
    assert "expected_contract_hash" not in parameters
    assert "expected_agent_registration_hash" not in parameters
    assert "expected_epoch" in parameters
    assert "expected_agent_id" in parameters


def test_legacy_identity_fields_are_named_once():
    assert LEGACY_SESSION_IDENTITY_FIELDS == (
        "native_adapter_contract_hash",
        "session_compatibility_epoch",
        "native_agent_registration_hash",
    )


# -- absent is not 1 ---------------------------------------------------------


def test_absent_epoch_and_epoch_one_are_different_identities(tmp_path):
    _, binding, record = make_record(tmp_path, session_epoch=None)
    validate(record, binding, epoch=None)
    with pytest.raises(SessionBindingError):
        validate(record, binding, epoch=1)


def test_adding_the_field_for_the_first_time_cuts_continuity(tmp_path):
    """The same deliberate act as a bump. If you do not want the cut, do not add it."""
    _, binding, record = make_record(tmp_path, session_epoch=None)
    with pytest.raises(SessionBindingError) as excinfo:
        validate(record, binding, epoch=1)
    assert "session_epoch" in str(excinfo.value)


def test_epoch_equality_is_symmetric_not_ordered(tmp_path):
    _, binding, record = make_record(tmp_path, session_epoch=2)
    for candidate in (None, 1, 3):
        with pytest.raises(SessionBindingError):
            validate(record, binding, epoch=candidate)
    validate(record, binding, epoch=2)


# -- legacy records: status-readable, load-refused ---------------------------


def legacy_record(tmp_path, **legacy):
    store, binding, _ = make_record(tmp_path)
    raw = store._session_dir("s-1") / "session.json"
    payload = json.loads(raw.read_text(encoding="utf-8"))
    payload.update(legacy)
    raw.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    return store, binding, store.open_session("s-1")


LEGACY_CASES = [
    {"native_adapter_contract_hash": "a" * 64},
    {"session_compatibility_epoch": 3},
    {"native_agent_registration_hash": "b" * 64},
]


@pytest.mark.parametrize("legacy", LEGACY_CASES)
def test_a_legacy_identity_record_is_refused_for_load(tmp_path, legacy):
    _, binding, record = legacy_record(tmp_path, **legacy)
    with pytest.raises(SessionBindingError) as excinfo:
        validate(record, binding, for_load=True)
    assert "LEGACY_SESSION_IDENTITY" in str(excinfo.value)


@pytest.mark.parametrize("legacy", LEGACY_CASES)
def test_a_legacy_identity_record_is_refused_even_without_load(tmp_path, legacy):
    """Fail-closed in both directions: a Session created under one line, only there."""
    _, binding, record = legacy_record(tmp_path, **legacy)
    with pytest.raises(SessionBindingError):
        validate(record, binding)


@pytest.mark.parametrize("legacy", LEGACY_CASES)
def test_a_legacy_identity_record_stays_status_readable(tmp_path, legacy):
    store, _, record = legacy_record(tmp_path, **legacy)
    assert record.session_id == "s-1"
    assert record.owner == "hermes"
    assert store.open_session("s-1").namespace == "hermes/ns"
    assert any(item.session_id == "s-1" for item in store.list_records())


def test_a_legacy_identity_record_can_still_be_closed(tmp_path):
    store, _, _ = legacy_record(tmp_path, session_compatibility_epoch=3)
    store.mark_closed("s-1")
    assert store.open_session("s-1").state != "open"


def test_the_refusal_is_a_stable_code_carrying_no_legacy_value(tmp_path):
    _, binding, record = legacy_record(tmp_path, native_adapter_contract_hash="c" * 64)
    with pytest.raises(SessionBindingError) as excinfo:
        validate(record, binding, for_load=True)
    assert "c" * 64 not in str(excinfo.value)


# -- no automatic bump exists anywhere ---------------------------------------


def test_no_code_path_derives_increments_or_infers_an_epoch():
    package = Path(session_mod.__file__).resolve().parent
    for path in sorted(package.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for banned in (
            "session_epoch + 1",
            "session_epoch += 1",
            "session_epoch+1",
            "bump_epoch",
            "next_epoch",
            "derive_epoch",
            "infer_epoch",
        ):
            assert banned not in text, f"{path.name} derives an epoch: {banned}"


def test_the_epoch_only_ever_arrives_from_the_registry_entry():
    """Its one source is an operator's edit, carried by the entry."""
    from agent_run_supervisor.native_acp.agent_registration import AgentEntry
    from agent_run_supervisor.native_acp.profile import AgentInstance

    entry = AgentEntry(
        agent_id="a-1",
        profile_id=PROFILE.profile_id,
        command="some-agent",
        session_epoch=7,
    )
    assert AgentInstance(PROFILE, entry).session_epoch == 7
    assert not hasattr(PROFILE, "session_epoch")

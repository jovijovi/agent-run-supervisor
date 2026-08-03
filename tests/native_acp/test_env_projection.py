"""A16, structural half — the environment value never becomes durable evidence.

Every environment value is sensitive, regardless of key name, source class,
length, or apparent shape. A value may exist at its operator- or source-owned
origin, in ``arsd`` memory, and in the child environment. No projected value — and no digest,
fingerprint, or length-by-value computed to represent one — may flow into an
ARS **durable artifact or hash input**, and the resolved carrier itself may not
be rendered into a log line or an exception message. What an AGENT chooses to
echo back through free-form Run text is a separate matter, deliberately not
policed here: see ``test_projected_value_retention``.

Two types carry that split. :class:`ResolvedEnvironment` is the ephemeral,
non-serializable value carrier accepted only by the process-spawn seam.
:class:`EnvProjection` is the separate durable, value-blind shape:
per name, the name, its source class, its precedence layer, and its redaction
status, plus a resolved count, the mediation id, and the declared names that
were absent from the daemon's environment. Nothing else.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import pickle
import re

import pytest

from agent_run_supervisor.native_acp import profile as profile_mod
from agent_run_supervisor.native_acp import spec
from agent_run_supervisor.native_acp.agent_registration import AgentEntry

SENTINEL = "SeNtInEl-env-value-9x7"
MEDIATION_ID = "ask-privileged-tool-families-v1"


def entry(**overrides) -> AgentEntry:
    body = {
        "agent_id": "a-1",
        "profile_id": "standard-native-acp-v1",
        "command": "some-agent",
    }
    body.update(overrides)
    return AgentEntry(**body)


def resolve(arsd_env=None, **overrides) -> spec.ResolvedEnvironment:
    return spec.resolve_run_environment(
        arsd_env=arsd_env if arsd_env is not None else {"HOME": "/home/svc"},
        profile=profile_mod.STANDARD_NATIVE_ACP_V1,
        entry=entry(**overrides),
    )


# -- the value carrier is ephemeral and non-serializable --------------------


def test_exec_mapping_carries_every_layer():
    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "PATH": "/usr/bin", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
        env_overlay=(("SOME_AGENT_HOME", "/home/svc/.agent"),),
        mediation_id=MEDIATION_ID,
    )
    mapping = resolved.exec_mapping
    assert mapping["HOME"] == "/home/svc"
    assert mapping["TOKEN_NAME"] == SENTINEL
    assert mapping["SOME_AGENT_HOME"] == "/home/svc/.agent"
    reserved, source_value = next(iter(profile_mod.MEDIATION_BINDINGS[MEDIATION_ID]))
    assert mapping[reserved] == source_value


def test_exec_mapping_is_a_fresh_copy_each_time():
    resolved = resolve()
    first = resolved.exec_mapping
    first["HOME"] = "/tampered"
    assert resolved.exec_mapping["HOME"] == "/home/svc"


def test_resolution_happens_once_and_ignores_later_ambient_mutation(monkeypatch):
    """No window in which the daemon's own environment could change in between."""
    monkeypatch.setenv("HOME", "/home/svc")
    monkeypatch.setenv("PATH", "/usr/bin")
    resolved = spec.resolve_run_environment(
        arsd_env=dict(os.environ),
        profile=profile_mod.STANDARD_NATIVE_ACP_V1,
        entry=entry(),
    )
    before = resolved.exec_mapping
    monkeypatch.setenv("HOME", "/home/attacker")
    monkeypatch.setenv("PATH", "/attacker/bin")
    monkeypatch.setenv("LATE_ADDITION", "late")
    assert resolved.exec_mapping == before
    assert "LATE_ADDITION" not in resolved.exec_mapping


def test_carrier_repr_and_str_carry_no_value():
    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
    )
    assert SENTINEL not in repr(resolved)
    assert SENTINEL not in str(resolved)
    assert SENTINEL not in f"{resolved}"
    assert SENTINEL not in "%s %r" % (resolved, resolved)


def test_carrier_is_rejected_by_every_serializer():
    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
    )
    with pytest.raises(TypeError):
        json.dumps(resolved)
    with pytest.raises(TypeError):
        json.dumps({"env": resolved})
    with pytest.raises(TypeError):
        dataclasses.asdict(resolved)  # type: ignore[call-overload]
    with pytest.raises(Exception):
        pickle.dumps(resolved)
    for banned in ("to_dict", "asdict", "items", "keys", "values", "get"):
        assert not hasattr(resolved, banned), f"carrier exposes {banned}"


def test_carrier_equality_and_hashing_are_identity_only():
    """A value-derived ``__eq__``/``__hash__`` would hash a sensitive value."""
    first = resolve()
    second = resolve()
    assert first != second
    assert first == first
    assert hash(first) != hash(second) or first is second


def test_carrier_never_reaches_a_log_record(caplog):
    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
    )
    logger = logging.getLogger("test.env.projection")
    with caplog.at_level(logging.INFO, logger="test.env.projection"):
        logger.info("resolved %s / %r", resolved, resolved)
    assert SENTINEL not in caplog.text


def test_carrier_never_reaches_an_exception_message():
    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
    )
    try:
        raise ValueError(f"boom {resolved}")
    except ValueError as exc:
        assert SENTINEL not in str(exc)
        assert SENTINEL not in repr(exc)


def test_the_carrier_has_exactly_one_consumer():
    """Process spawn, and nothing else.

    The second consumer — a per-Run literal guard built from the same mapping —
    is removed, so the carrier exposes no accessor that enumerates its values
    for any other purpose.
    """
    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
    )
    assert resolved.exec_mapping["TOKEN_NAME"] == SENTINEL
    assert not hasattr(resolved, "sensitive_values")
    public_api = {
        name
        for name in dir(resolved)
        if not name.startswith("_")
    }
    assert public_api == {"exec_mapping", "value_blind_projection"}


# -- the durable projection is value-blind ----------------------------------


def test_projection_records_name_source_precedence_and_redaction_only():
    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
        env_overlay=(("SOME_AGENT_HOME", "/home/svc/.agent"),),
        mediation_id=MEDIATION_ID,
    )
    payload = resolved.value_blind_projection().to_dict()
    assert payload["values_persisted"] is False
    assert payload["redaction"] == "all-values-withheld"
    assert payload["resolved_count"] == len(resolved.exec_mapping)
    assert payload["mediation_id"] == MEDIATION_ID
    by_name = {item["name"]: item for item in payload["names"]}
    assert by_name["HOME"] == {
        "name": "HOME",
        "source": "base",
        "precedence": 1,
        "redacted": True,
    }
    assert by_name["TOKEN_NAME"]["source"] == "passthrough"
    assert by_name["TOKEN_NAME"]["precedence"] == 2
    assert by_name["SOME_AGENT_HOME"]["source"] == "overlay"
    assert by_name["SOME_AGENT_HOME"]["precedence"] == 3
    reserved = next(iter(profile_mod.MEDIATION_BINDINGS[MEDIATION_ID]))[0]
    assert by_name[reserved]["source"] == "mediation"
    assert by_name[reserved]["precedence"] == 4
    for item in payload["names"]:
        assert set(item) == {"name", "source", "precedence", "redacted"}


def test_projection_declares_absent_operator_names():
    resolved = resolve(
        arsd_env={"HOME": "/home/svc"}, env_passthrough=("SOME_AGENT_CONFIG",)
    )
    payload = resolved.value_blind_projection().to_dict()
    assert payload["declared_absent"] == ["SOME_AGENT_CONFIG"]
    assert "SOME_AGENT_CONFIG" not in {item["name"] for item in payload["names"]}


def test_projection_carries_no_value_and_no_value_derived_metadata():
    values = {
        "HOME": "/home/svc",
        "TOKEN_NAME": SENTINEL,
        "OTHER": "another-secret-literal",
    }
    resolved = resolve(arsd_env=values, env_passthrough=("TOKEN_NAME", "OTHER"))
    rendered = json.dumps(resolved.value_blind_projection().to_dict(), sort_keys=True)
    for literal in values.values():
        assert literal not in rendered
        # Not a length, not a prefix, not a suffix, not an equality token.
        assert str(len(literal)) not in re.sub(r"\"[a-z_]+\":", "", rendered)
        assert literal[:4] not in rendered
        assert literal[-4:] not in rendered


def test_projection_hash_material_is_independent_of_the_values():
    """Two Runs whose transmitted value changed may share a launch hash.

    The hash proves the declared projection, not the secret — which is exactly
    the property that keeps a value out of every hash input.
    """
    first = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": "value-one"},
        env_passthrough=("TOKEN_NAME",),
    )
    second = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": "a-completely-different-value"},
        env_passthrough=("TOKEN_NAME",),
    )
    assert (
        first.value_blind_projection().to_dict()
        == second.value_blind_projection().to_dict()
    )


def test_projection_changes_when_the_declaration_changes():
    baseline = resolve(arsd_env={"HOME": "/home/svc"})
    widened = resolve(
        arsd_env={"HOME": "/home/svc", "EXTRA": "x"}, env_passthrough=("EXTRA",)
    )
    assert (
        baseline.value_blind_projection().to_dict()
        != widened.value_blind_projection().to_dict()
    )


def test_no_hash_function_is_reachable_from_the_projection(monkeypatch):
    """Not even transiently: a keyed digest of a value is still a value record."""
    import hashlib

    def refuse(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("a hash was computed over environment material")

    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
    )
    monkeypatch.setattr(hashlib, "sha256", refuse)
    monkeypatch.setattr(hashlib, "blake2b", refuse)
    monkeypatch.setattr(hashlib, "new", refuse)
    resolved.value_blind_projection().to_dict()


# -- the launch snapshot -----------------------------------------------------


def test_launch_snapshot_holds_the_projection_and_no_value_bearing_env_field():
    resolved = resolve(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        env_passthrough=("TOKEN_NAME",),
        mediation_id=MEDIATION_ID,
    )
    snapshot = spec.LaunchSnapshot(
        command="some-agent",
        argv=("some-agent",),
        profile_id="standard-native-acp-v1",
        profile_revision=1,
        profile_hash="0" * 64,
        agent_id="a-1",
        env=resolved.value_blind_projection(),
        mediation_id=MEDIATION_ID,
        model_selector_id="model",
        effort_selector_id="effort",
    )
    payload = snapshot.to_dict()
    assert SENTINEL not in json.dumps(payload, sort_keys=True)
    for banned in ("fixed_env", "permission_env", "expected_runtime", "runtime_provenance"):
        assert banned not in payload
    assert payload["env"]["values_persisted"] is False
    assert len(snapshot.launch_hash()) == 64


def test_launch_schema_rejects_a_value_bearing_env_key():
    """A schema-level allowlist, so a value-bearing key cannot be reintroduced."""
    assert not spec.launch_payload_shape_is_exact(
        {
            "schema_version": spec.LAUNCH_SCHEMA_VERSION,
            "command": "x",
            "argv": ["x"],
            "profile_id": "p",
            "profile_revision": 1,
            "profile_hash": "0" * 64,
            "agent_id": "a",
            "env": {},
            "mediation_id": None,
            "model_selector_id": "model",
            "effort_selector_id": "effort",
            "session_epoch": None,
            "session_meta": None,
            "forbidden_capabilities": [],
            "credential_refs": [],
            "launch_spec_hash": "1" * 64,
            "fixed_env": [["TOKEN_NAME", SENTINEL]],
        }
    )


def test_launch_snapshot_carries_no_transport_or_endpoint_field():
    """A12: v1 is stdio by definition; a one-valued key is remote scaffolding."""
    fields = {f.name for f in dataclasses.fields(spec.LaunchSnapshot)}
    for banned in ("transport", "endpoint", "attach", "remote", "url", "address"):
        assert banned not in fields


# -- WP3.2 precedence, seen through the layering ----------------------------


def test_environment_layers_is_the_single_precedence_authority():
    """``resolve_environment`` composes exactly these layers, in this order."""
    arsd_env = {"HOME": "/home/svc", "PATH": "/usr/bin"}
    declared = entry(
        env_passthrough=("PATH",),
        env_overlay=(("PATH", "/operator/bin"),),
        mediation_id=MEDIATION_ID,
    )
    layers = spec.environment_layers(
        arsd_env=arsd_env,
        base_names=profile_mod.STANDARD_NATIVE_ACP_V1.base_allowlist,
        entry=declared,
    )
    resolved = spec.resolve_run_environment(
        arsd_env=arsd_env, profile=profile_mod.STANDARD_NATIVE_ACP_V1, entry=declared
    )
    assert resolved.exec_mapping == {name: value for name, value, _, _ in layers}

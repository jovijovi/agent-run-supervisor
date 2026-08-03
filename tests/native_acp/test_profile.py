"""The source-owned ACP compatibility profile registry, after the V4 reset.

A profile freezes **ACP protocol and compatibility semantics only**: the
protocol major, required capabilities, a forbidden-capability floor, session
semantics including required real ``session/load``, selector-id conventions, the
base environment allowlist, permission-mediation semantics, and — only where
cited ACP-level evidence requires it — frozen ACP session metadata and a
required permission-mode selector.

It contains no path, version, digest, model literal, agent name, value domain,
launch kind, artifact identity, or deployment fact. Every AGENT is instead one
operator-owned registry entry.

Exactly two profiles are registered. The three per-agent profiles
(``opencode-native-acp``, ``codex-acp-1.1.7``, ``claude-agent-acp-0.63.0``) are
retired by deletion — not by an alias, a redirect, a disable flag, or any other
mechanism capable of retiring one.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp import profile as profile_mod
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_COMPAT_V1,
    CURSOR_NATIVE_ACP_V1,
    DEFAULT_REGISTRY,
    RESERVED_MEDIATION_KEYS,
    STANDARD_NATIVE_ACP_V1,
    AcpCompatProfile,
    AgentInstance,
    ProfileValidationError,
    UnknownProfileError,
)

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agent_run_supervisor"
    / "native_acp"
    / "profile.py"
)

RETIRED_PROFILE_IDS = (
    "opencode-native-acp",
    "opencode-1.18.4",
    "codex-acp-1.1.7",
    "claude-agent-acp-0.63.0",
    "claude-agent-acp-0.61.0",
)


def entry(**overrides):
    body = {
        "agent_id": "a-1",
        "profile_id": STANDARD_NATIVE_ACP_V1.profile_id,
        "command": "some-agent",
    }
    body.update(overrides)
    return AgentEntry(**body)


# -- WP3.3: a closed, enumerated registry -----------------------------------
#
# The registry stays closed and small. It is three entries now: the conformance
# contract, the one profile with an evidenced ACP-semantic deviation, and the
# one with an evidenced configuration-fidelity deviation.


def test_registry_holds_exactly_the_three_registered_profiles():
    assert DEFAULT_REGISTRY.ids() == (
        "claude-agent-acp-compat-v1",
        "cursor-native-acp-v1",
        "standard-native-acp-v1",
    )


def test_registry_is_a_closed_set():
    assert DEFAULT_REGISTRY.get("standard-native-acp-v1") is STANDARD_NATIVE_ACP_V1
    assert DEFAULT_REGISTRY.get("claude-agent-acp-compat-v1") is CLAUDE_AGENT_ACP_COMPAT_V1
    assert DEFAULT_REGISTRY.get("cursor-native-acp-v1") is CURSOR_NATIVE_ACP_V1
    with pytest.raises(UnknownProfileError):
        DEFAULT_REGISTRY.get("mystery-agent-9.9")


@pytest.mark.parametrize("retired", RETIRED_PROFILE_IDS)
def test_retired_profile_ids_are_simply_unknown(retired):
    """Deleted, not aliased: the old id is an unknown profile and is refused."""
    with pytest.raises(UnknownProfileError):
        DEFAULT_REGISTRY.get(retired)
    assert retired not in DEFAULT_REGISTRY.ids()


@pytest.mark.parametrize("retired", RETIRED_PROFILE_IDS)
def test_retired_profile_ids_appear_nowhere_in_source(retired):
    assert retired not in SOURCE.read_text(encoding="utf-8")


def test_no_retirement_mechanism_was_introduced():
    """V4 retires profiles by deleting them, never by adding a way to disable one.

    A field defaulting to ``False``, an unused rule constant, or a marker would
    each be the mechanism ``non-approvals.md`` treats as its own decision.
    """
    text = SOURCE.read_text(encoding="utf-8").lower()
    for banned in ("deprecat", "retire", "disabl", "alias", "redirect", "superseded"):
        assert banned not in text, f"profile source names {banned!r}"
    fields = {field.name for field in dataclasses.fields(AcpCompatProfile)}
    assert not fields & {"deprecated", "enabled", "active", "alias_of"}


# -- A4: no deployment facts survive in source ------------------------------


def test_profile_source_carries_no_deployment_fact():
    text = SOURCE.read_text(encoding="utf-8")
    assert "/opt/" not in text
    assert "node_modules" not in text
    assert "ARTIFACT_MATERIALIZATION_PREFIX" not in text
    assert not re.search(r"\b[0-9a-f]{64}\b", text), "a digest survives in profile source"
    assert not re.search(r"\b\d+\.\d+\.\d+\b", text), "a version survives in source"


def test_profile_source_has_no_artifact_or_binding_vocabulary():
    text = SOURCE.read_text(encoding="utf-8")
    for banned in (
        "BindingSlot",
        "WrappedRuntimeArtifacts",
        "SLOT_DESCRIPTOR_FIELDS",
        "_REGISTERED_EXECUTABLES",
        "launch_kind",
        "interpreter",
        "tree_sha256",
        "adapter_entry_sha256",
        "slot_hash",
        "version_probe",
        "attestation",
        "promote",
        "binding_root",
    ):
        assert banned not in text, f"profile source still carries {banned!r}"


def test_profiles_declare_no_value_domain():
    for profile_id in DEFAULT_REGISTRY.ids():
        profile = DEFAULT_REGISTRY.get(profile_id)
        for banned in (
            "registered_models",
            "allowed_efforts",
            "default_model",
            "default_effort",
            "config_schema",
            "executable_key",
            "argv_template",
            "fixed_env",
            "credential_slots",
            "contract",
        ):
            assert not hasattr(profile, banned), f"{profile_id} still declares {banned}"


# -- what a profile does freeze ---------------------------------------------


def test_standard_profile_freezes_acp_conformance_only():
    profile = STANDARD_NATIVE_ACP_V1
    assert profile.profile_id == "standard-native-acp-v1"
    assert profile.acp_protocol_version == "1"
    assert profile.required_capabilities == ("loadSession",)
    assert profile.requires_session_load is True
    assert profile.permission_mode_selector_id is None
    assert profile.required_permission_mode is None
    assert profile.session_meta is None


def test_compat_profile_keeps_its_cited_acp_deviation():
    """The one evidenced ACP-semantic deviation survives as a compat profile."""
    profile = CLAUDE_AGENT_ACP_COMPAT_V1
    assert profile.profile_id == "claude-agent-acp-compat-v1"
    assert profile.acp_protocol_version == "1"
    assert profile.permission_mode_selector_id == "mode"
    assert profile.required_permission_mode == "default"
    assert profile.session_meta_payload() == {
        "claudeCode": {
            "options": {
                "settingSources": [],
                "tools": {"preset": "claude_code", "type": "preset"},
            }
        }
    }


def test_compat_session_meta_is_sent_on_both_session_calls():
    """One frozen ``_meta``, so a reused Session cannot restore ambient settings."""
    profile = CLAUDE_AGENT_ACP_COMPAT_V1
    assert profile.session_meta_for("new") == profile.session_meta_for("load")
    assert profile.session_meta_for("new") == profile.session_meta_payload()
    assert STANDARD_NATIVE_ACP_V1.session_meta_for("new") is None


def test_session_meta_payload_is_a_fresh_deep_copy():
    first = CLAUDE_AGENT_ACP_COMPAT_V1.session_meta_payload()
    first["claudeCode"]["options"]["settingSources"].append("user")
    again = CLAUDE_AGENT_ACP_COMPAT_V1.session_meta_payload()
    assert again["claudeCode"]["options"]["settingSources"] == []


def test_versioned_id_refuses_a_contract_whose_frozen_major_disagrees():
    with pytest.raises(ProfileValidationError):
        AcpCompatProfile(
            profile_id="standard-native-acp-v1", revision=1, acp_protocol_version="2"
        )
    # An unversioned id carries no generation claim and is left alone.
    AcpCompatProfile(profile_id="unversioned", revision=1, acp_protocol_version="2")


def test_permission_mode_pair_is_declared_together():
    with pytest.raises(ProfileValidationError):
        AcpCompatProfile(
            profile_id="p-v1",
            revision=1,
            acp_protocol_version="1",
            permission_mode_selector_id="mode",
        )
    with pytest.raises(ProfileValidationError):
        AcpCompatProfile(
            profile_id="p-v1",
            revision=1,
            acp_protocol_version="1",
            required_permission_mode="default",
        )


def test_session_meta_must_be_canonical_json_text():
    with pytest.raises(ProfileValidationError):
        AcpCompatProfile(
            profile_id="p-v1",
            revision=1,
            acp_protocol_version="1",
            session_meta='{"b": 1, "a": 2}',
        )
    AcpCompatProfile(
        profile_id="p-v1",
        revision=1,
        acp_protocol_version="1",
        session_meta=json.dumps({"a": 2, "b": 1}, sort_keys=True, separators=(",", ":")),
    )


def test_profile_hash_covers_the_frozen_semantics():
    baseline = STANDARD_NATIVE_ACP_V1.profile_hash()
    assert len(baseline) == 64
    assert baseline != CLAUDE_AGENT_ACP_COMPAT_V1.profile_hash()
    assert STANDARD_NATIVE_ACP_V1.snapshot_ref() == "registry:standard-native-acp-v1@r1"


def test_base_allowlist_covers_the_interactive_essentials():
    base = set(profile_mod.base_env_allowlist(STANDARD_NATIVE_ACP_V1))
    for name in ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "LANG", "TZ", "TERM"):
        assert name in base
    for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        assert name in base
    for name in ("http_proxy", "https_proxy", "no_proxy", "HTTPS_PROXY", "NO_PROXY"):
        assert name in base
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"):
        assert name in base


def test_ssh_auth_sock_is_deliberately_not_in_the_base_set():
    """Forwarding it is a real authority transfer and must be an explicit opt-in."""
    for profile_id in DEFAULT_REGISTRY.ids():
        base = profile_mod.base_env_allowlist(DEFAULT_REGISTRY.get(profile_id))
        assert "SSH_AUTH_SOCK" not in base


# -- AgentInstance: one profile plus one operator entry ---------------------


def test_instance_pairs_a_profile_with_a_registry_entry():
    instance = AgentInstance(STANDARD_NATIVE_ACP_V1, entry())
    assert instance.agent_id == "a-1"
    assert instance.command == "some-agent"
    assert instance.argv == ("some-agent",)
    assert instance.permission_env == ()
    assert instance.session_epoch is None


def test_instance_requires_an_entry():
    with pytest.raises(ProfileValidationError):
        AgentInstance(STANDARD_NATIVE_ACP_V1, None)


def test_instance_refuses_an_entry_for_a_different_profile():
    with pytest.raises(ProfileValidationError):
        AgentInstance(CLAUDE_AGENT_ACP_COMPAT_V1, entry())


def test_instance_argv_preserves_the_declared_command_byte_for_byte():
    instance = AgentInstance(
        STANDARD_NATIVE_ACP_V1, entry(command="some-agent", args=("acp", "--stdio"))
    )
    assert instance.argv == ("some-agent", "acp", "--stdio")
    assert instance.argv[0] == instance.command


def test_instance_selector_hints_fall_back_to_the_profile_convention():
    default = AgentInstance(STANDARD_NATIVE_ACP_V1, entry())
    assert default.model_selector_id == STANDARD_NATIVE_ACP_V1.model_selector_id
    assert default.effort_selector_id == STANDARD_NATIVE_ACP_V1.effort_selector_id
    hinted = AgentInstance(
        STANDARD_NATIVE_ACP_V1,
        entry(model_selector_id="model", effort_selector_id="reasoning_effort"),
    )
    assert hinted.effort_selector_id == "reasoning_effort"


def test_instance_forbidden_capabilities_are_a_superset_of_the_profile_floor():
    instance = AgentInstance(
        STANDARD_NATIVE_ACP_V1, entry(forbidden_capabilities=("terminal",))
    )
    assert set(STANDARD_NATIVE_ACP_V1.forbidden_capabilities) <= set(
        instance.forbidden_capabilities
    )
    assert "terminal" in instance.forbidden_capabilities


def test_instance_mediation_comes_from_the_source_table_only():
    selected = AgentInstance(
        STANDARD_NATIVE_ACP_V1, entry(mediation_id="ask-privileged-tool-families-v1")
    )
    assert (
        selected.permission_env
        == profile_mod.MEDIATION_BINDINGS["ask-privileged-tool-families-v1"]
    )
    assert AgentInstance(STANDARD_NATIVE_ACP_V1, entry()).permission_env == ()


def test_instance_exposes_no_agent_name_or_version_expectation():
    instance = AgentInstance(STANDARD_NATIVE_ACP_V1, entry())
    for banned in ("acp_agent_name", "acp_agent_version", "version_probe"):
        assert not hasattr(instance, banned)


# -- mediation authority ----------------------------------------------------


def test_reserved_mediation_keys_are_global_and_disjoint_from_every_base_set():
    assert RESERVED_MEDIATION_KEYS
    for profile_id in DEFAULT_REGISTRY.ids():
        base = set(profile_mod.base_env_allowlist(DEFAULT_REGISTRY.get(profile_id)))
        assert base.isdisjoint(RESERVED_MEDIATION_KEYS)


def test_mediation_pairs_refuses_an_unregistered_id():
    with pytest.raises(UnknownProfileError):
        profile_mod.mediation_pairs("invent-your-own-v1")
    assert profile_mod.mediation_pairs(None) == ()


# -- structural -------------------------------------------------------------


def test_profile_module_performs_no_filesystem_access():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported |= {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "os" not in imported
    assert "pathlib" not in imported
    assert "subprocess" not in imported

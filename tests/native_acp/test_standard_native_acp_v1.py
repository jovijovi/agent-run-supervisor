"""The versioned source profile, its four construction invariants, and the
agent-anchored Binding that only it descends into (closure §5.1/§5.3/§5.4).

G1.5, G4, G5 and the read-once half of G7 live here: one root serves the three
live profiles *and* two fake agents at once, the agent anchor is the only new
subtree, and caller text is judged before any filesystem query.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.native_acp import runtime_binding as rb
from agent_run_supervisor.native_acp.profile import (
    _REGISTERED_EXECUTABLES,
    _REGISTERED_PERMISSION_ENV,
    CLAUDE_AGENT_ACP_0_63_0,
    CODEX_ACP_1_1_7,
    DEFAULT_REGISTRY,
    LAUNCH_KIND_DIRECT,
    OPENCODE_NATIVE_ACP,
    SLOT_KIND_NATIVE_BINARY,
    STANDARD_NATIVE_ACP_V1,
    AdapterContract,
    AgentInstance,
    AgentProfile,
    ProfileValidationError,
    VersionProbeRule,
)

from . import binding_fixtures as fx

ALPHA = fx.FAKE_ALPHA_ID
BETA = fx.FAKE_BETA_ID


# -- §5.1 the source contract ------------------------------------------------


def test_the_profile_is_registered_alongside_the_three_live_rows() -> None:
    assert DEFAULT_REGISTRY.ids() == (
        "claude-agent-acp-0.63.0",
        "codex-acp-1.1.7",
        "opencode-native-acp",
        "standard-native-acp-v1",
    )


def test_the_contract_freezes_acp_v1_conformance_only() -> None:
    profile = STANDARD_NATIVE_ACP_V1
    contract = profile.contract
    assert profile.revision == 1
    assert contract.launch_kind == LAUNCH_KIND_DIRECT
    assert contract.acp_protocol_version == "1"
    assert contract.required_capabilities == ("loadSession",)
    assert profile.requires_session_load is True
    assert contract.requires_agent_registration is True
    assert contract.registration_schema_version == 1
    assert contract.wrapped_runtime is None
    assert contract.credential_root_slot is None
    assert contract.project_config_relpath is None
    assert profile.session_meta is None
    assert profile.fixed_env == ()
    slots = contract.binding_slots
    assert len(slots) == 1
    assert slots[0].name == "agent_cli"
    assert slots[0].kind == SLOT_KIND_NATIVE_BINARY
    assert slots[0].provides_executable is True
    assert contract.cli_slot == "agent_cli"
    assert contract.version_probe == VersionProbeRule(argv_suffix=("--version",))
    assert profile.env_allowlist == (
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    )


def test_the_profile_freezes_no_agent_owned_fact() -> None:
    """Invariant 2 in the positive: the registration owns every varying value."""
    profile = STANDARD_NATIVE_ACP_V1
    assert profile.model_selector_id == ""
    assert profile.effort_selector_id == ""
    assert profile.default_model == ""
    assert profile.default_effort == ""
    assert profile.registered_models == ()
    assert profile.allowed_efforts == ()
    assert profile.argv_template == ()
    assert profile.config_schema == {}


def test_the_executable_key_is_in_neither_source_registry() -> None:
    """Invariants 3 and 4: no source executable and no profile-keyed mediation."""
    key = STANDARD_NATIVE_ACP_V1.executable_key
    assert key not in _REGISTERED_EXECUTABLES
    assert key not in _REGISTERED_PERMISSION_ENV


# -- §5.1 the four construction invariants -----------------------------------


def _standard_contract(**overrides: Any) -> AdapterContract:
    body: dict[str, Any] = {
        "launch_kind": LAUNCH_KIND_DIRECT,
        "acp_agent_name": "standard-native-acp",
        "acp_protocol_version": "1",
        "version_probe": VersionProbeRule(argv_suffix=("--version",)),
        "binding_slots": STANDARD_NATIVE_ACP_V1.contract.binding_slots,
        "required_capabilities": ("loadSession",),
        "forbidden_capabilities": (),
        "cli_slot": "agent_cli",
        "requires_agent_registration": True,
        "registration_schema_version": 1,
    }
    body.update(overrides)
    return AdapterContract(**body)


def _standard_profile(**overrides: Any) -> AgentProfile:
    body: dict[str, Any] = {
        "profile_id": "standard-native-acp-v1",
        "revision": 1,
        "executable_key": "standard-native-acp",
        "argv_template": (),
        "env_allowlist": STANDARD_NATIVE_ACP_V1.env_allowlist,
        "credential_slots": (),
        "model_selector_id": "",
        "effort_selector_id": "",
        "default_model": "",
        "default_effort": "",
        "registered_models": (),
        "allowed_efforts": (),
        "requires_session_load": True,
        "config_schema": {},
        "contract": _standard_contract(),
    }
    body.update(overrides)
    return AgentProfile(**body)


def test_invariant_1_the_id_generation_must_equal_the_protocol_major() -> None:
    with pytest.raises(ProfileValidationError, match="protocol"):
        _standard_profile(contract=_standard_contract(acp_protocol_version="2"))


def test_invariant_1_a_v2_id_requires_protocol_2() -> None:
    with pytest.raises(ProfileValidationError, match="protocol"):
        _standard_profile(profile_id="standard-native-acp-v2")


def test_invariant_1_a_revision_bump_cannot_change_the_protocol_major() -> None:
    """The id carries the ACP generation; only a new id can carry a new one."""
    bumped = _standard_profile(revision=2)
    assert bumped.contract.acp_protocol_version == "1"
    with pytest.raises(ProfileValidationError, match="protocol"):
        _standard_profile(revision=2, contract=_standard_contract(acp_protocol_version="2"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_selector_id", "model"),
        ("effort_selector_id", "effort"),
        ("default_model", "x/y"),
        ("default_effort", "max"),
        ("registered_models", ("x/y",)),
        ("allowed_efforts", ("max",)),
        ("argv_template", ("acp",)),
        ("config_schema", {"schema_version": 1, "selectors": {"model": {}}}),
    ],
)
def test_invariant_2_the_profile_may_not_freeze_what_the_registration_owns(
    field: str, value: Any
) -> None:
    with pytest.raises(ProfileValidationError, match="registration"):
        _standard_profile(**{field: value})


def test_invariant_3_a_registration_scoped_profile_takes_no_source_mediation() -> None:
    with pytest.raises(ProfileValidationError, match="mediation"):
        _standard_profile(executable_key="opencode")


def test_invariant_4_a_registration_scoped_profile_takes_no_source_executable() -> None:
    with pytest.raises(ProfileValidationError, match="executable"):
        _standard_profile(executable_key="codex-acp")


def test_the_contract_pairs_the_registration_flag_with_its_schema_version() -> None:
    with pytest.raises(ProfileValidationError, match="registration_schema_version"):
        _standard_contract(registration_schema_version=None)
    with pytest.raises(ProfileValidationError, match="registration_schema_version"):
        _standard_contract(requires_agent_registration=False)


# -- §5.4 AgentInstance ------------------------------------------------------


def test_a_legacy_instance_returns_every_existing_profile_value() -> None:
    instance = AgentInstance(OPENCODE_NATIVE_ACP, None)
    assert instance.acp_agent_name == "OpenCode"
    assert instance.acp_protocol_version == "1"
    assert instance.argv_tokens == OPENCODE_NATIVE_ACP.argv_template
    assert instance.model_selector_id == OPENCODE_NATIVE_ACP.model_selector_id
    assert instance.effort_selector_id == OPENCODE_NATIVE_ACP.effort_selector_id
    assert instance.registered_models == OPENCODE_NATIVE_ACP.registered_models
    assert instance.allowed_efforts == OPENCODE_NATIVE_ACP.allowed_efforts
    assert instance.forbidden_capabilities == ("terminal",)
    assert instance.credential_slots == OPENCODE_NATIVE_ACP.credential_slots
    assert instance.required_credential_refs is None
    assert instance.version_probe == OPENCODE_NATIVE_ACP.contract.version_probe
    assert instance.agent_id is None
    assert instance.agent_registration_hash is None
    assert instance.permission_env == (
        ("OPENCODE_PERMISSION", '{"bash":"ask","edit":"ask","webfetch":"ask"}'),
    )


def test_an_agent_instance_answers_from_the_registration(tmp_path: Path) -> None:
    admitted = fx.admitted_agent(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    instance = AgentInstance(STANDARD_NATIVE_ACP_V1, admitted.registration)
    assert instance.acp_agent_name == "FakeAlpha"
    assert instance.argv_tokens == ("acp",)
    assert instance.model_selector_id == "model"
    assert instance.registered_models == ("alpha/one",)
    assert instance.agent_id == ALPHA
    assert instance.permission_env == (
        ("OPENCODE_PERMISSION", '{"bash":"ask","edit":"ask","webfetch":"ask"}'),
    )


def test_an_agent_selecting_no_mediation_binding_launches_with_none(
    tmp_path: Path,
) -> None:
    admitted = fx.admitted_agent(tmp_path, STANDARD_NATIVE_ACP_V1, BETA)
    instance = AgentInstance(STANDARD_NATIVE_ACP_V1, admitted.registration)
    assert instance.permission_env == ()
    assert instance.argv_tokens == ("serve", "--acp")
    assert instance.effort_selector_id == "reasoning"


# -- G1.5 inert in a root that has no agents subtree -------------------------


def test_the_new_profile_refuses_a_root_that_carries_no_agent_subtree(
    tmp_path: Path,
) -> None:
    root = fx.build_binding_root(tmp_path, OPENCODE_NATIVE_ACP)
    fx.build_binding_root(tmp_path, CODEX_ACP_1_1_7)
    fx.build_binding_root(tmp_path, CLAUDE_AGENT_ACP_0_63_0)
    reader = rb.BindingReader(root, ownership=fx.ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.read_registration(STANDARD_NATIVE_ACP_V1, ALPHA)
    assert excinfo.value.rule == "PROFILE_BINDING_ABSENT"
    # ...and the three live profiles still resolve from that same root.
    for profile in (OPENCODE_NATIVE_ACP, CODEX_ACP_1_1_7, CLAUDE_AGENT_ACP_0_63_0):
        assert reader.resolve_active(profile).generation_id == "gen-0001"


def test_an_agent_named_under_a_profile_with_agents_but_no_such_agent(
    tmp_path: Path,
) -> None:
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    reader = rb.BindingReader(root, ownership=fx.ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.read_registration(STANDARD_NATIVE_ACP_V1, BETA)
    assert excinfo.value.rule == "AGENT_BINDING_ABSENT"


def test_a_missing_registration_file_is_its_own_rule(tmp_path: Path) -> None:
    root = fx.build_agent_binding_root(
        tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA, write_registration=False
    )
    reader = rb.BindingReader(root, ownership=fx.ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.read_registration(STANDARD_NATIVE_ACP_V1, ALPHA)
    assert excinfo.value.rule == "REGISTRATION_ABSENT"


# -- G4 path safety ----------------------------------------------------------


class _FsSpy:
    """Records every filesystem entry point the descent could reach."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        for name in ("open", "lstat", "stat", "listdir", "readlink", "fstat"):
            original = getattr(os, name)

            def wrapper(*args: Any, _name: str = name, _orig: Any = original, **kwargs: Any):
                self.calls.append(_name)
                return _orig(*args, **kwargs)

            monkeypatch.setattr(os, name, wrapper)


@pytest.mark.parametrize(
    "agent_id",
    [
        "..",
        ".",
        "../codex-acp-1.1.7",
        "a/b",
        "/abs",
        "",
        "a" * 65,
        "café",
        "a\x00b",
        "-leading",
    ],
)
def test_an_unsafe_agent_id_is_refused_before_any_filesystem_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_id: str
) -> None:
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    reader = rb.BindingReader(root, ownership=fx.ownership())
    spy = _FsSpy(monkeypatch)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.read_registration(STANDARD_NATIVE_ACP_V1, agent_id)
    assert excinfo.value.rule == "AGENT_ID_UNSAFE"
    assert spy.calls == [], f"{spy.calls} ran before the value was judged"


def test_a_str_subclass_with_a_hostile_dunder_is_refused_on_type_identity() -> None:
    class Liar(str):
        def __str__(self) -> str:  # pragma: no cover - never reached
            return ".."

        def __eq__(self, other: object) -> bool:  # pragma: no cover
            return True

        def __hash__(self) -> int:  # pragma: no cover
            return hash("fake-alpha")

    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.agent_component(Liar("fake-alpha"))
    assert excinfo.value.rule == "AGENT_ID_UNSAFE"


@pytest.mark.parametrize("value", [None, 7, b"fake-alpha", ["fake-alpha"]])
def test_a_non_string_agent_id_is_refused_by_the_component_grammar(value: Any) -> None:
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.agent_component(value)
    assert excinfo.value.rule == "AGENT_ID_UNSAFE"


def test_the_component_grammar_returns_an_exact_str(tmp_path: Path) -> None:
    component = rb.agent_component("fake-alpha")
    assert type(component) is str
    assert component == "fake-alpha"


def test_agent_scope_is_required_and_forbidden_symmetrically(tmp_path: Path) -> None:
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    reader = rb.BindingReader(root, ownership=fx.ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.resolve_active(STANDARD_NATIVE_ACP_V1)
    assert excinfo.value.rule == "AGENT_SCOPE_REQUIRED"

    legacy_root = fx.build_binding_root(tmp_path, OPENCODE_NATIVE_ACP, dirname="legacy")
    legacy_reader = rb.BindingReader(legacy_root, ownership=fx.ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        legacy_reader.resolve_active(OPENCODE_NATIVE_ACP, agent_id=ALPHA)
    assert excinfo.value.rule == "AGENT_SCOPE_FORBIDDEN"


# -- G5 two agents, one generic path -----------------------------------------


def test_one_root_resolves_two_agents_and_all_three_live_profiles(
    tmp_path: Path,
) -> None:
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, BETA)
    for profile in (OPENCODE_NATIVE_ACP, CODEX_ACP_1_1_7, CLAUDE_AGENT_ACP_0_63_0):
        fx.build_binding_root(tmp_path, profile)

    reader = rb.BindingReader(root, ownership=fx.ownership())
    alpha = reader.read_registration(STANDARD_NATIVE_ACP_V1, ALPHA)
    beta = reader.read_registration(STANDARD_NATIVE_ACP_V1, BETA)
    assert (alpha.acp_agent_name, beta.acp_agent_name) == ("FakeAlpha", "FakeBeta")
    assert alpha.registration_hash != beta.registration_hash
    assert alpha.argv_tokens != beta.argv_tokens
    assert alpha.model_selector_id != beta.model_selector_id
    assert alpha.permission_binding_id != beta.permission_binding_id

    alpha_binding = reader.resolve_active(STANDARD_NATIVE_ACP_V1, agent_id=ALPHA)
    beta_binding = reader.resolve_active(STANDARD_NATIVE_ACP_V1, agent_id=BETA)
    assert alpha_binding.slot_set_hash != beta_binding.slot_set_hash
    assert alpha_binding.generation_id == beta_binding.generation_id == "gen-0001"
    for profile in (OPENCODE_NATIVE_ACP, CODEX_ACP_1_1_7, CLAUDE_AGENT_ACP_0_63_0):
        assert reader.resolve_active(profile).generation_id == "gen-0001"


def test_a_pointer_moved_between_agent_subtrees_is_refused_on_a_machine_field(
    tmp_path: Path,
) -> None:
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, BETA)
    alpha_pointer = rb.active_pointer_path(
        root, STANDARD_NATIVE_ACP_V1.profile_id, agent_id=ALPHA
    )
    beta_pointer = rb.active_pointer_path(
        root, STANDARD_NATIVE_ACP_V1.profile_id, agent_id=BETA
    )
    alpha_pointer.write_text(beta_pointer.read_text(encoding="utf-8"), encoding="utf-8")
    reader = rb.BindingReader(root, ownership=fx.ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.resolve_active(STANDARD_NATIVE_ACP_V1, agent_id=ALPHA)
    assert excinfo.value.rule == "POINTER_AGENT_MISMATCH"


def test_a_generation_accepted_for_another_agent_is_refused(tmp_path: Path) -> None:
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, BETA)
    alpha_manifest = rb.generation_manifest_path(
        root, STANDARD_NATIVE_ACP_V1.profile_id, "gen-0001", agent_id=ALPHA
    )
    beta_manifest = rb.generation_manifest_path(
        root, STANDARD_NATIVE_ACP_V1.profile_id, "gen-0001", agent_id=BETA
    )
    alpha_manifest.write_text(beta_manifest.read_text(encoding="utf-8"), encoding="utf-8")
    reader = rb.BindingReader(root, ownership=fx.ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.read_generation(
            "gen-0001", profile=STANDARD_NATIVE_ACP_V1, agent_id=ALPHA
        )
    assert excinfo.value.rule == "REGISTRATION_CONTRACT_MISMATCH"


def test_promoting_one_agent_leaves_every_other_pointer_byte_identical(
    tmp_path: Path,
) -> None:
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, BETA)
    for profile in (OPENCODE_NATIVE_ACP, CODEX_ACP_1_1_7, CLAUDE_AGENT_ACP_0_63_0):
        fx.build_binding_root(tmp_path, profile)
    fx.build_agent_binding_root(
        tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA, generation_id="gen-0002"
    )

    others = {
        path: path.read_bytes()
        for path in [
            rb.active_pointer_path(root, STANDARD_NATIVE_ACP_V1.profile_id, agent_id=BETA),
            *(
                rb.active_pointer_path(root, profile.profile_id)
                for profile in (
                    OPENCODE_NATIVE_ACP,
                    CODEX_ACP_1_1_7,
                    CLAUDE_AGENT_ACP_0_63_0,
                )
            ),
        ]
    }
    policy = fx.ownership()
    resolved = rb.validate_generation(
        root,
        "gen-0002",
        profile=STANDARD_NATIVE_ACP_V1,
        ownership=policy,
        agent_id=ALPHA,
        probe=False,
    )
    rb.write_active_pointer(
        root,
        resolved,
        profile=STANDARD_NATIVE_ACP_V1,
        ownership=policy,
        agent_id=ALPHA,
    )
    for path, before in others.items():
        assert path.read_bytes() == before
    assert (
        rb.read_active_pointer(
            root, profile=STANDARD_NATIVE_ACP_V1, ownership=policy, agent_id=ALPHA
        )[0]
        == "gen-0002"
    )


# -- G7 read-once ------------------------------------------------------------


def test_an_agent_scoped_resolution_reads_exactly_one_of_each(tmp_path: Path) -> None:
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    reader = rb.BindingReader(root, ownership=fx.ownership())
    rb.reset_read_counters()
    reader.read_registration(STANDARD_NATIVE_ACP_V1, ALPHA)
    reader.resolve_active(STANDARD_NATIVE_ACP_V1, agent_id=ALPHA)
    assert rb.read_counters() == {"registration": 1, "active": 1, "generation": 1}


def test_a_legacy_resolution_reads_no_registration(tmp_path: Path) -> None:
    root = fx.build_binding_root(tmp_path, OPENCODE_NATIVE_ACP)
    reader = rb.BindingReader(root, ownership=fx.ownership())
    rb.reset_read_counters()
    reader.resolve_active(OPENCODE_NATIVE_ACP)
    assert rb.read_counters() == {"registration": 0, "active": 1, "generation": 1}


# -- the generation freezes the Registration it was accepted against ---------
#
# A generation's ``agent_registration_hash`` is only worth writing down if
# something compares it with the registration that is actually live. Without
# that comparison an operator can validate and promote one registration and
# then swap in another under the same agent, and every other check — pointer,
# manifest bytes, manifest digest, epoch, contract identity — still passes,
# because none of them is about the registration's contents.


def _live_registration_hash(root: Path, agent_id: str = ALPHA) -> str:
    from agent_run_supervisor.native_acp import agent_registration as ar

    return ar.registration_hash(
        __import__("json").loads(
            rb.registration_path(
                root, STANDARD_NATIVE_ACP_V1.profile_id, agent_id
            ).read_text(encoding="utf-8")
        )
    )


def _drifted_registration(agent_id: str = ALPHA) -> dict[str, Any]:
    """The same agent, still valid, with a compatibility-bearing change.

    The model domain and default are exactly the kind of fact a Run is admitted
    against, so a swap here changes what the agent may be asked to do.
    """
    body = fx.fake_registration_payload(agent_id, STANDARD_NATIVE_ACP_V1)
    config = dict(body["config"])
    config["registered_models"] = ["alpha/two"]
    config["default_model"] = "alpha/two"
    body["config"] = config
    return body


def test_an_in_place_registration_swap_is_invisible_to_every_other_check(
    tmp_path: Path,
) -> None:
    """The premise: nothing else in the Binding notices this edit."""
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    pointer = rb.active_pointer_path(
        root, STANDARD_NATIVE_ACP_V1.profile_id, agent_id=ALPHA
    )
    manifest = rb.generation_manifest_path(
        root, STANDARD_NATIVE_ACP_V1.profile_id, "gen-0001", agent_id=ALPHA
    )
    before = (pointer.read_bytes(), manifest.read_bytes())
    frozen = _live_registration_hash(root)

    fx.write_canonical(
        rb.registration_path(root, STANDARD_NATIVE_ACP_V1.profile_id, ALPHA),
        _drifted_registration(),
    )

    assert (pointer.read_bytes(), manifest.read_bytes()) == before
    assert _live_registration_hash(root) != frozen
    # The generation still declares the *old* hash, and the manifest digest,
    # epoch, and contract identity are all still exactly right.
    resolved = rb.BindingReader(root, ownership=fx.ownership()).read_generation(
        "gen-0001", profile=STANDARD_NATIVE_ACP_V1, agent_id=ALPHA
    )
    assert resolved.contract_identity["agent_registration_hash"] == frozen


def test_a_drifted_registration_under_an_unchanged_generation_fails_closed(
    tmp_path: Path,
) -> None:
    """The runtime pair used for launch must refuse before anything spawns."""
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    fx.write_canonical(
        rb.registration_path(root, STANDARD_NATIVE_ACP_V1.profile_id, ALPHA),
        _drifted_registration(),
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        fx.admit_from_root(root, STANDARD_NATIVE_ACP_V1, ALPHA)
    assert excinfo.value.rule == "REGISTRATION_HASH_MISMATCH"


def test_admission_refuses_a_drifted_registration_before_any_launch(
    tmp_path: Path,
) -> None:
    from agent_run_supervisor.arsd import admission

    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    fx.write_canonical(
        rb.registration_path(root, STANDARD_NATIVE_ACP_V1.profile_id, ALPHA),
        _drifted_registration(),
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        admission.resolve_runtime_binding(
            STANDARD_NATIVE_ACP_V1,
            binding_root=root,
            ownership=fx.ownership(),
            agent_id=ALPHA,
        )
    assert excinfo.value.rule == "REGISTRATION_HASH_MISMATCH"


def test_operator_validation_refuses_a_drifted_registration(tmp_path: Path) -> None:
    """So a drifted registration can never be promoted or otherwise blessed."""
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    fx.write_canonical(
        rb.registration_path(root, STANDARD_NATIVE_ACP_V1.profile_id, ALPHA),
        _drifted_registration(),
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.validate_generation(
            root,
            "gen-0001",
            profile=STANDARD_NATIVE_ACP_V1,
            ownership=fx.ownership(),
            agent_id=ALPHA,
            probe=False,
        )
    assert excinfo.value.rule == "REGISTRATION_HASH_MISMATCH"


def test_a_provenance_only_registration_edit_stays_compatible(tmp_path: Path) -> None:
    """Provenance is outside the hash, so re-recording a receipt is not drift."""
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    frozen = _live_registration_hash(root)

    body = fx.fake_registration_payload(ALPHA, STANDARD_NATIVE_ACP_V1)
    provenance = dict(body["provenance"])
    provenance["accepted_at"] = "2026-09-09T09:00:00+08:00"
    provenance["acceptance_receipt"] = {"ref": "receipt:re-accepted", "sha256": "d" * 64}
    body["provenance"] = provenance
    fx.write_canonical(
        rb.registration_path(root, STANDARD_NATIVE_ACP_V1.profile_id, ALPHA), body
    )

    assert _live_registration_hash(root) == frozen
    admitted = fx.admit_from_root(root, STANDARD_NATIVE_ACP_V1, ALPHA)
    assert admitted.registration.registration_hash == frozen
    rb.validate_generation(
        root,
        "gen-0001",
        profile=STANDARD_NATIVE_ACP_V1,
        ownership=fx.ownership(),
        agent_id=ALPHA,
        probe=False,
    )


def test_the_admitted_pair_refuses_a_missing_or_foreign_registration(
    tmp_path: Path,
) -> None:
    """The invariant is symmetric: neither half may be absent or borrowed."""
    root = fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, ALPHA)
    fx.build_agent_binding_root(tmp_path, STANDARD_NATIVE_ACP_V1, BETA)
    reader = rb.BindingReader(root, ownership=fx.ownership())
    policy = fx.ownership()
    alpha_generation = reader.resolve_active(STANDARD_NATIVE_ACP_V1, agent_id=ALPHA)
    beta_registration = reader.read_registration(STANDARD_NATIVE_ACP_V1, BETA)

    # An agent-scoped generation with no registration at all.
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.AdmittedRuntimeBinding(resolved=alpha_generation, ownership=policy)
    assert excinfo.value.rule == "REGISTRATION_HASH_MISMATCH"

    # Another agent's registration pinned to this agent's generation.
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.AdmittedRuntimeBinding(
            resolved=alpha_generation, ownership=policy, registration=beta_registration
        )
    assert excinfo.value.rule == "REGISTRATION_HASH_MISMATCH"

    # A registration carried alongside a generation that freezes none.
    legacy_root = fx.build_binding_root(
        tmp_path, OPENCODE_NATIVE_ACP, dirname="legacy-pair"
    )
    legacy = rb.BindingReader(legacy_root, ownership=policy).resolve_active(
        OPENCODE_NATIVE_ACP
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.AdmittedRuntimeBinding(
            resolved=legacy, ownership=policy, registration=beta_registration
        )
    assert excinfo.value.rule == "REGISTRATION_HASH_MISMATCH"


def test_the_freeze_invariant_lives_in_exactly_one_place() -> None:
    """One central invariant, not duplicated ad hoc checks."""
    import ast
    from pathlib import Path as _Path

    source = (
        _Path(rb.__file__).read_text(encoding="utf-8")
        if hasattr(rb, "__file__")
        else ""
    )
    tree = ast.parse(source)
    raising = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and node.value == "REGISTRATION_HASH_MISMATCH"
    ]
    assert len(raising) == 1, f"the rule is raised from {len(raising)} places"

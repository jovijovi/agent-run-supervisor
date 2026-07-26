"""C4: closed, code-registered profile registry and the first OpenCode profile."""

from __future__ import annotations

import pytest

from agent_run_supervisor.native_acp.profile import (
    DEFAULT_REGISTRY,
    OPENCODE_NATIVE_ACP,
    AgentProfile,
    ProfileRegistry,
    UnknownProfileError,
    resolve_registered_executable,
)


def test_registry_is_a_closed_set() -> None:
    assert DEFAULT_REGISTRY.get("opencode-native-acp") is OPENCODE_NATIVE_ACP
    with pytest.raises(UnknownProfileError):
        DEFAULT_REGISTRY.get("mystery-agent-9.9")


def test_the_retired_opencode_id_is_an_unknown_profile_with_no_alias() -> None:
    # C15: the stable ID replaces ``opencode-1.18.4`` outright. There is no
    # compatibility alias, so the old ID is refused like any unknown profile.
    with pytest.raises(UnknownProfileError):
        DEFAULT_REGISTRY.get("opencode-1.18.4")
    assert "opencode-1.18.4" not in DEFAULT_REGISTRY.ids()


def test_opencode_profile_literals_are_pinned() -> None:
    profile = OPENCODE_NATIVE_ACP
    assert profile.profile_id == "opencode-native-acp"
    assert profile.revision >= 1
    assert profile.argv_template == ("acp",)  # fixed subcommand, no passthrough
    assert profile.model_selector_id == "model"
    assert profile.effort_selector_id == "effort"
    assert profile.default_model == "kimi-for-coding/k3"
    assert profile.default_effort == "max"
    assert "max" in profile.allowed_efforts
    assert profile.requires_session_load is True
    assert profile.credential_slots == ("kimi-for-coding",)


def test_opencode_registration_matches_the_discovery_evidence() -> None:
    """C15 + the discovery prerequisite: constants are byte-copies of evidence.

    The operator-run zero-prompt ACP discovery observed agentInfo OpenCode
    1.18.5, protocol 1, ``loadSession`` advertised, selectors model/effort, and
    — only after the exact model was set to kimi-for-coding/k3 — the
    model-dependent effort domain low|high|max. ``deepseek/deepseek-v4-pro``
    was registered under the retired 1.18.4 evidence and is deliberately not
    carried over: this discovery does not prove its effort domain.
    """
    profile = OPENCODE_NATIVE_ACP
    assert profile.revision == 3
    assert profile.registered_models == ("kimi-for-coding/k3",)
    assert profile.allowed_efforts == ("low", "high", "max")
    assert profile.snapshot_ref() == "registry:opencode-native-acp@r3"
    assert profile.contract.acp_agent_name == "OpenCode"
    assert profile.contract.acp_protocol_version == "1"
    assert profile.contract.required_capabilities == ("loadSession",)
    # agentInfo.version is never asserted equal to the CLI --version: no
    # version constant is frozen in this profile at all.
    assert "1.18.5" not in str(profile.snapshot())


def test_profile_hash_and_snapshot_are_deterministic() -> None:
    first = OPENCODE_NATIVE_ACP.profile_hash()
    second = OPENCODE_NATIVE_ACP.profile_hash()
    assert first == second
    assert len(first) == 64
    snapshot = OPENCODE_NATIVE_ACP.snapshot()
    assert snapshot["profile_id"] == "opencode-native-acp"
    assert snapshot["registered_models"] == ["kimi-for-coding/k3"]
    assert OPENCODE_NATIVE_ACP.snapshot_ref() == "registry:opencode-native-acp@r3"
    assert len(OPENCODE_NATIVE_ACP.config_schema_hash()) == 64


def test_direct_acp_executable_is_a_binding_fact_not_a_source_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The one OpenCode executable is both the AGENT CLI and the ACP
    # implementation, so its path is a deployment fact the Binding supplies —
    # the installation mapping no longer carries it, and no env/PATH override
    # can reintroduce one.
    monkeypatch.setenv("PATH", "/tmp/adversarial-bin")
    monkeypatch.setenv("OPENCODE_BIN", "/tmp/adversarial-bin/opencode")
    with pytest.raises(UnknownProfileError):
        resolve_registered_executable(OPENCODE_NATIVE_ACP.executable_key)
    with pytest.raises(UnknownProfileError):
        resolve_registered_executable("unregistered-agent")
    assert OPENCODE_NATIVE_ACP.contract.executable_slot().name == "agent_cli"


def test_profile_rejects_unknown_construction_surface() -> None:
    # No command/argv/env/JSON passthrough fields exist on the profile.
    with pytest.raises(TypeError):
        AgentProfile(  # type: ignore[call-arg]
            profile_id="x",
            revision=1,
            executable_key="opencode",
            argv_template=("acp",),
            env_allowlist=(),
            credential_slots=(),
            model_selector_id="model",
            effort_selector_id="effort",
            default_model="a/b",
            default_effort="max",
            registered_models=("a/b",),
            allowed_efforts=("max",),
            requires_session_load=True,
            config_schema={},
            contract=OPENCODE_NATIVE_ACP.contract,
            extra_argv=("--danger",),
        )


def test_profile_requires_an_adapter_contract() -> None:
    # A profile without its source-frozen contract has no accepted Binding
    # shape, no launch kind, and no probe rule: it cannot be registered.
    with pytest.raises(TypeError):
        AgentProfile(  # type: ignore[call-arg]
            profile_id="x",
            revision=1,
            executable_key="opencode",
            argv_template=("acp",),
            env_allowlist=(),
            credential_slots=(),
            model_selector_id="model",
            effort_selector_id="effort",
            default_model="a/b",
            default_effort="max",
            registered_models=("a/b",),
            allowed_efforts=("max",),
            requires_session_load=True,
            config_schema={},
        )


def test_registry_refuses_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        ProfileRegistry((OPENCODE_NATIVE_ACP, OPENCODE_NATIVE_ACP))


def test_opencode_permission_mediation_env_is_registered() -> None:
    # A4-S2 repair: the registered OpenCode launch binding must force the
    # privileged tool families (edit/bash/webfetch) through client-mediated
    # session/request_permission — OpenCode's default build agent otherwise
    # auto-allows in-process writes with zero mediation.
    from agent_run_supervisor.native_acp.profile import (
        resolve_registered_permission_env,
    )

    pairs = resolve_registered_permission_env(OPENCODE_NATIVE_ACP.executable_key)
    assert pairs == (
        ("OPENCODE_PERMISSION", '{"bash":"ask","edit":"ask","webfetch":"ask"}'),
    )
    # Unregistered executables carry no binding (nothing invented).
    assert resolve_registered_permission_env("unregistered-agent") == ()


# -- Codex closed-profile admission (D2/D3/D9/D11) ---------------------------

FROZEN_NODE_PATH = (
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/node/v24.14.0/bin/node"
)
FROZEN_NODE_SHA256 = (
    "e237a2839d0cbdc9a9a2adda1a184afc0f5b20306ffbe923af5686550472d8a8"
)
FROZEN_ADAPTER_ENTRY = (
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/codex-acp/1.1.7"
    "/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
)
FROZEN_ADAPTER_ENTRY_SHA256 = (
    "0deb6b820dfed8804cd76b16a50210fe12202e5e339b5edaa23f6987f1742e0a"
)
FROZEN_CODEX_CONFIG = '{"features":{"use_legacy_landlock":true}}'

# Goldens re-captured for the PR-B contract/Binding split. Every registered
# profile's revision bumped because its frozen contract changed shape, so the
# pre-PR-B pins are legitimately dead; these hold the new shape stable.
OPENCODE_PROFILE_HASH_GOLDEN = (
    "ac42ed6b8e67c919cd5fc56304e3b08d9bf745dcd8967441c97fb5947d95d844"
)
OPENCODE_CONFIG_SCHEMA_HASH_GOLDEN = (
    "5cbb40748af2b4ebf2ab900e9cdad5a750b6ab1b53d65758d4732314d9facc22"
)
OPENCODE_CONTRACT_HASH_GOLDEN = (
    "55b4c5255959fc209a64be155841a16bf836e7fea9b6864753c22a6ed3080807"
)


def test_codex_profile_snapshot_golden() -> None:
    """Source keeps compatibility semantics; deployment facts left for R13.

    ``CODEX_CONFIG`` pins Landlock because the adapter's default bwrap sandbox
    was disqualified at discovery (FAIL: the command reached the Codex sandbox
    without ACP permission mediation and failed with a bwrap loopback
    RTM_NEWADDR EPERM on this host). It is load-bearing, version-bound debt:
    any adapter/CLI/Node/CODEX_CONFIG change requires a full new
    install → discovery → permission-canary cycle and a profile revision bump.

    The downstream Codex CLI path/version/digest and the ``CODEX_HOME`` value
    are no longer here at all: they are contract-declared Binding slots.
    """
    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7

    profile = CODEX_ACP_1_1_7
    assert profile.profile_id == "codex-acp-1.1.7"
    assert profile.revision == 2
    assert profile.executable_key == "codex-acp"
    assert profile.argv_template == (FROZEN_ADAPTER_ENTRY,)
    assert profile.model_selector_id == "model"
    assert profile.effort_selector_id == "reasoning_effort"
    assert profile.default_model == "gpt-5.6-sol"
    assert profile.default_effort == "max"
    assert profile.registered_models == ("gpt-5.6-sol",)
    assert profile.allowed_efforts == ("max",)
    assert profile.requires_session_load is True
    assert profile.credential_slots == ("codex-home-auth",)
    assert profile.required_credential_refs == ("codex-home-auth",)

    # fixed_env in exact manifest order: tuple order is hash-significant.
    assert profile.fixed_env == (
        ("CODEX_CONFIG", FROZEN_CODEX_CONFIG),
        ("INITIAL_AGENT_MODE", "read-only"),
        ("NO_BROWSER", "1"),
    )
    contract = profile.contract
    assert contract.wrapped_runtime.interpreter_path == FROZEN_NODE_PATH
    assert contract.wrapped_runtime.interpreter_sha256 == FROZEN_NODE_SHA256
    assert contract.wrapped_runtime.adapter_entry_path == FROZEN_ADAPTER_ENTRY
    assert contract.wrapped_runtime.adapter_entry_sha256 == FROZEN_ADAPTER_ENTRY_SHA256
    assert contract.acp_agent_name == "@agentclientprotocol/codex-acp"
    assert contract.acp_protocol_version == "1"
    assert [slot.to_dict() for slot in contract.binding_slots] == [
        {
            "name": "downstream_cli",
            "kind": "package_tree",
            "env_key": "CODEX_PATH",
            "provides_executable": False,
            "descriptor_fields": [
                "package_root",
                "tree_sha256",
                "launcher_path",
                "launcher_sha256",
                "interpreter_path",
                "interpreter_sha256",
                "version",
            ],
        },
        {
            "name": "codex_home",
            "kind": "config_root",
            "env_key": "CODEX_HOME",
            "provides_executable": False,
            "descriptor_fields": ["path"],
        },
    ]
    assert contract.credential_root_slot == "codex_home"
    assert contract.project_config_relpath == ".codex/config.toml"

    snapshot = profile.snapshot()
    assert snapshot["fixed_env"] == [
        ["CODEX_CONFIG", FROZEN_CODEX_CONFIG],
        ["INITIAL_AGENT_MODE", "read-only"],
        ["NO_BROWSER", "1"],
    ]
    assert snapshot["required_credential_refs"] == ["codex-home-auth"]
    assert "expected_runtime" not in snapshot
    assert snapshot["config_schema"] == {
        "schema_version": 1,
        "selectors": {
            "model": {
                "config_id": "model",
                "type": "string",
                "domain": ["gpt-5.6-sol"],
            },
            "effort": {
                "config_id": "reasoning_effort",
                "type": "string",
                "domain": ["max"],
            },
        },
    }
    assert DEFAULT_REGISTRY.get("codex-acp-1.1.7") is CODEX_ACP_1_1_7
    assert DEFAULT_REGISTRY.ids() == (
        "claude-agent-acp-0.61.0",
        "codex-acp-1.1.7",
        "opencode-native-acp",
    )


def _codex_like(**overrides) -> AgentProfile:
    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7
    import dataclasses

    return dataclasses.replace(CODEX_ACP_1_1_7, **overrides)


def test_codex_fixed_env_validation_rules() -> None:
    base = (("CODEX_CONFIG", FROZEN_CODEX_CONFIG),)
    cases = {
        "duplicate key": base + (("CODEX_CONFIG", FROZEN_CODEX_CONFIG),),
        "non-allowlisted name": base + (("LD_PRELOAD", "/tmp/evil.so"),),
        "oversized value": (("CODEX_CONFIG", "{" + "x" * 4096 + "}"),),
        "non-printable value": base + (("NO_BROWSER", "1\n"),),
        "non-canonical CODEX_CONFIG": (
            ("CODEX_CONFIG", '{"features": {"use_legacy_landlock": true}}'),
        ),
        "non-object CODEX_CONFIG": (("CODEX_CONFIG", '["not-an-object"]'),),
        "unparsable CODEX_CONFIG": (("CODEX_CONFIG", "not json"),),
        "invalid INITIAL_AGENT_MODE": base + (("INITIAL_AGENT_MODE", "yolo"),),
    }
    for label, fixed_env in cases.items():
        with pytest.raises(ValueError):
            _codex_like(fixed_env=fixed_env)
        assert label  # keeps the failing case identifiable

    # The exact manifest tuple stays valid.
    _codex_like()


def test_a_binding_filled_env_key_may_never_also_be_a_source_constant() -> None:
    """C2: one launch variable, one authority.

    Freezing ``CODEX_PATH`` or ``CODEX_HOME`` in ``fixed_env`` would shadow the
    contract-declared Binding slot with a source constant that silently wins.
    """
    for key, value in (
        ("CODEX_PATH", "/opt/frozen/codex"),
        ("CODEX_HOME", "/opt/frozen/codex-home"),
    ):
        with pytest.raises(ValueError):
            _codex_like(fixed_env=(("CODEX_CONFIG", FROZEN_CODEX_CONFIG), (key, value)))


def test_codex_fixed_env_allowed_modes_accepted() -> None:
    for mode in ("read-only", "agent", "agent-full-access"):
        profile = _codex_like(
            fixed_env=(
                ("CODEX_CONFIG", FROZEN_CODEX_CONFIG),
                ("INITIAL_AGENT_MODE", mode),
            ),
        )
        assert dict(profile.fixed_env)["INITIAL_AGENT_MODE"] == mode


def test_opencode_snapshot_carries_no_deployment_fact() -> None:
    snapshot = OPENCODE_NATIVE_ACP.snapshot()
    assert "fixed_env" not in snapshot
    assert "expected_runtime" not in snapshot
    assert "required_credential_refs" not in snapshot
    assert sorted(snapshot) == [
        "allowed_efforts",
        "argv_template",
        "config_schema",
        "credential_slots",
        "default_effort",
        "default_model",
        "effort_selector_id",
        "env_allowlist",
        "executable_key",
        "model_selector_id",
        "profile_id",
        "registered_models",
        "requires_session_load",
        "revision",
    ]
    assert OPENCODE_NATIVE_ACP.profile_hash() == OPENCODE_PROFILE_HASH_GOLDEN
    assert OPENCODE_NATIVE_ACP.config_schema_hash() == OPENCODE_CONFIG_SCHEMA_HASH_GOLDEN
    assert OPENCODE_NATIVE_ACP.adapter_contract_hash() == OPENCODE_CONTRACT_HASH_GOLDEN
    assert OPENCODE_NATIVE_ACP.fixed_env == ()
    assert OPENCODE_NATIVE_ACP.required_credential_refs is None


def test_codex_executable_resolves_to_frozen_node_never_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7

    resolved = resolve_registered_executable(CODEX_ACP_1_1_7.executable_key)
    assert str(resolved) == FROZEN_NODE_PATH
    # The process image is the frozen Node; resolution never consults PATH.
    monkeypatch.setenv("PATH", "/tmp/adversarial-bin")
    monkeypatch.setenv("NODE", "/tmp/adversarial-bin/node")
    monkeypatch.setenv("CODEX_ACP_BIN", "/tmp/adversarial-bin/codex-acp")
    assert resolve_registered_executable(CODEX_ACP_1_1_7.executable_key) == resolved
    assert resolved.is_absolute()
    # The .bin/codex-acp shim is install evidence only; it is never executed.
    assert "node_modules/.bin" not in str(resolved)


def test_codex_carries_no_opencode_permission_binding() -> None:
    from agent_run_supervisor.native_acp.profile import (
        CODEX_ACP_1_1_7,
        resolve_registered_permission_env,
    )

    # _REGISTERED_PERMISSION_ENV stays OpenCode-only.
    assert resolve_registered_permission_env(CODEX_ACP_1_1_7.executable_key) == ()


# -- Claude closed-profile admission (B3) ------------------------------------

FROZEN_CLAUDE_ENTRY = (
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/claude-agent-acp/0.61.0"
    "/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js"
)
FROZEN_CLAUDE_ENTRY_SHA256 = (
    "260aac90bf75f197b93640087c1de66441761d43c2784efa035fdcee60b5dacd"
)
# Goldens re-captured for the PR-B contract/Binding split (revision 2). The
# config-schema hash is unchanged: selectors are compatibility semantics and
# never carried a deployment fact.
CODEX_PROFILE_HASH_GOLDEN = (
    "e574f54a37edf6c4adc5a91fff926c5c9da5a4724451c9f0f70ce7dbffa1e789"
)
CODEX_CONFIG_SCHEMA_HASH_GOLDEN = (
    "a86d084c818a7ca3be0a5298dda46800fc3977826e8007e3db74bea7f2b8829a"
)
CODEX_CONTRACT_HASH_GOLDEN = (
    "36b85cd59f12ffdb431bdd7989beaaa11f5c7272a895b7ad4060cb00d1c8fa89"
)
CLAUDE_CONTRACT_HASH_GOLDEN = (
    "0e62e4cbba144fc5954502e5b66222fc11891d1d566816e372b596ec88a1a38b"
)


def test_claude_profile_snapshot_golden() -> None:
    """Every registered value is a byte-copy of the frozen discovery manifest.

    The ACP model domain is exactly ``claude-fable-5[1m]`` and ``opus[1m]``.
    ``claude-opus-5[1m]`` is the *direct Claude CLI* author selector and is
    deliberately NOT registered: a live ACP set/readback on this adapter
    returns ``opus[1m]``, so the CLI-side string could never pass exact
    readback.
    """
    from agent_run_supervisor.native_acp.profile import CLAUDE_AGENT_ACP_0_61_0

    profile = CLAUDE_AGENT_ACP_0_61_0
    assert profile.profile_id == "claude-agent-acp-0.61.0"
    assert profile.revision == 2
    assert profile.executable_key == "claude-agent-acp"
    assert profile.argv_template == (FROZEN_CLAUDE_ENTRY,)
    assert profile.model_selector_id == "model"
    assert profile.effort_selector_id == "effort"
    assert profile.registered_models == ("claude-fable-5[1m]", "opus[1m]")
    assert profile.default_model == "opus[1m]"
    assert profile.allowed_efforts == ("max",)
    assert profile.default_effort == "max"
    assert profile.requires_session_load is True
    # Closed admission: the Claude CLI owns its own credential storage, which
    # ARS neither manages nor stages, so exactly zero caller references admit.
    assert profile.credential_slots == ()
    assert profile.required_credential_refs == ()

    # The downstream CLI is still bound only through ``CLAUDE_CODE_EXECUTABLE``
    # — a missing binding would silently switch the adapter to a PATH-resolved
    # or bundled fallback CLI — but the *value* is now a Binding slot, so the
    # source constant is gone and only the key remains code-known.
    assert profile.fixed_env == (("NO_BROWSER", "1"),)
    contract = profile.contract
    assert contract.launch_kind == "wrapped_acp"
    assert contract.wrapped_runtime.interpreter_path == FROZEN_NODE_PATH
    assert contract.wrapped_runtime.interpreter_sha256 == FROZEN_NODE_SHA256
    assert contract.wrapped_runtime.adapter_entry_path == FROZEN_CLAUDE_ENTRY
    assert contract.wrapped_runtime.adapter_entry_sha256 == FROZEN_CLAUDE_ENTRY_SHA256
    assert contract.acp_agent_name == "@agentclientprotocol/claude-agent-acp"
    assert [slot.to_dict() for slot in contract.binding_slots] == [
        {
            "name": "downstream_cli",
            "kind": "package_tree",
            "env_key": "CLAUDE_CODE_EXECUTABLE",
            "provides_executable": False,
            "descriptor_fields": [
                "package_root",
                "tree_sha256",
                "launcher_path",
                "launcher_sha256",
                "interpreter_path",
                "interpreter_sha256",
                "version",
            ],
        }
    ]
    # The Claude CLI owns its own credential storage: no ARS-managed root.
    assert contract.credential_root_slot is None
    assert contract.project_config_relpath is None

    snapshot = profile.snapshot()
    assert snapshot["fixed_env"] == [["NO_BROWSER", "1"]]
    assert snapshot["required_credential_refs"] == []
    assert "expected_runtime" not in snapshot
    assert len(profile.profile_hash()) == 64
    assert profile.adapter_contract_hash() == CLAUDE_CONTRACT_HASH_GOLDEN
    assert profile.snapshot_ref() == "registry:claude-agent-acp-0.61.0@r2"


def test_claude_profile_is_registered_alongside_the_existing_rows() -> None:
    from agent_run_supervisor.native_acp.profile import CLAUDE_AGENT_ACP_0_61_0

    assert DEFAULT_REGISTRY.get("claude-agent-acp-0.61.0") is CLAUDE_AGENT_ACP_0_61_0
    assert DEFAULT_REGISTRY.ids() == (
        "claude-agent-acp-0.61.0",
        "codex-acp-1.1.7",
        "opencode-native-acp",
    )


def test_claude_executable_resolves_to_the_frozen_node_never_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_run_supervisor.native_acp.profile import CLAUDE_AGENT_ACP_0_61_0

    resolved = resolve_registered_executable(CLAUDE_AGENT_ACP_0_61_0.executable_key)
    assert str(resolved) == FROZEN_NODE_PATH
    # The adapter entry is an ESM script with a `#!/usr/bin/env node` shebang:
    # the process image must be the frozen Node, never an env-resolved one.
    monkeypatch.setenv("PATH", "/tmp/adversarial-bin")
    monkeypatch.setenv("NODE", "/tmp/adversarial-bin/node")
    monkeypatch.setenv("CLAUDE_CODE_EXECUTABLE", "/tmp/adversarial-bin/claude")
    assert (
        resolve_registered_executable(CLAUDE_AGENT_ACP_0_61_0.executable_key)
        == resolved
    )
    assert "node_modules/.bin" not in str(resolved)


def test_claude_carries_no_opencode_permission_binding() -> None:
    from agent_run_supervisor.native_acp.profile import (
        CLAUDE_AGENT_ACP_0_61_0,
        resolve_registered_permission_env,
    )

    assert resolve_registered_permission_env(
        CLAUDE_AGENT_ACP_0_61_0.executable_key
    ) == ()


def _claude_like(**overrides) -> AgentProfile:
    from agent_run_supervisor.native_acp.profile import CLAUDE_AGENT_ACP_0_61_0
    import dataclasses

    return dataclasses.replace(CLAUDE_AGENT_ACP_0_61_0, **overrides)


def test_claude_binding_key_may_not_be_frozen_as_a_source_constant() -> None:
    # The contract declares ``CLAUDE_CODE_EXECUTABLE`` as the key its Binding
    # slot fills; freezing the same key here would shadow the operator fact.
    with pytest.raises(ValueError):
        _claude_like(
            fixed_env=(
                ("CLAUDE_CODE_EXECUTABLE", "/opt/frozen/claude"),
                ("NO_BROWSER", "1"),
            )
        )
    _claude_like()


def test_codex_profile_golden_after_the_binding_split() -> None:
    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7

    snapshot = CODEX_ACP_1_1_7.snapshot()
    assert "expected_runtime" not in snapshot
    assert CODEX_ACP_1_1_7.profile_hash() == CODEX_PROFILE_HASH_GOLDEN
    assert CODEX_ACP_1_1_7.config_schema_hash() == CODEX_CONFIG_SCHEMA_HASH_GOLDEN
    assert CODEX_ACP_1_1_7.adapter_contract_hash() == CODEX_CONTRACT_HASH_GOLDEN


# -- B4: profile-frozen permission mode --------------------------------------


def test_claude_profile_freezes_the_default_permission_mode() -> None:
    from agent_run_supervisor.native_acp.profile import CLAUDE_AGENT_ACP_0_61_0

    profile = CLAUDE_AGENT_ACP_0_61_0
    assert profile.permission_mode_selector_id == "mode"
    assert profile.required_permission_mode == "default"
    assert profile.config_schema["selectors"]["permission_mode"] == {
        "config_id": "mode",
        "type": "string",
        "domain": ["default"],
    }
    snapshot = profile.snapshot()
    assert snapshot["permission_mode_selector_id"] == "mode"
    assert snapshot["required_permission_mode"] == "default"


def test_permission_mode_binding_must_be_declared_as_a_pair() -> None:
    for overrides in (
        {"permission_mode_selector_id": "mode", "required_permission_mode": None},
        {"permission_mode_selector_id": None, "required_permission_mode": "default"},
    ):
        with pytest.raises(ValueError):
            _claude_like(**overrides)


def test_legacy_profiles_declare_no_permission_mode_and_omit_it() -> None:
    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7

    for profile in (OPENCODE_NATIVE_ACP, CODEX_ACP_1_1_7):
        assert profile.permission_mode_selector_id is None
        assert profile.required_permission_mode is None
        snapshot = profile.snapshot()
        assert "permission_mode_selector_id" not in snapshot
        assert "required_permission_mode" not in snapshot
    assert OPENCODE_NATIVE_ACP.profile_hash() == OPENCODE_PROFILE_HASH_GOLDEN
    assert CODEX_ACP_1_1_7.profile_hash() == CODEX_PROFILE_HASH_GOLDEN


# -- B5: profile-owned frozen session metadata -------------------------------

FROZEN_CLAUDE_SESSION_META = (
    '{"claudeCode":{"options":{"settingSources":[],'
    '"tools":{"preset":"claude_code","type":"preset"}}}}'
)


def test_claude_profile_freezes_the_exact_session_metadata() -> None:
    from agent_run_supervisor.native_acp.profile import CLAUDE_AGENT_ACP_0_61_0

    profile = CLAUDE_AGENT_ACP_0_61_0
    assert profile.session_meta == FROZEN_CLAUDE_SESSION_META
    payload = profile.session_meta_payload()
    assert payload == {
        "claudeCode": {
            "options": {
                "settingSources": [],
                "tools": {"type": "preset", "preset": "claude_code"},
            }
        }
    }
    # Hash-bound: the metadata is part of the profile snapshot.
    snapshot = profile.snapshot()
    assert snapshot["session_meta"] == payload


def test_session_metadata_payload_is_a_fresh_deep_copy_each_call() -> None:
    from agent_run_supervisor.native_acp.profile import CLAUDE_AGENT_ACP_0_61_0

    first = CLAUDE_AGENT_ACP_0_61_0.session_meta_payload()
    first["claudeCode"]["options"]["settingSources"].append("user")
    second = CLAUDE_AGENT_ACP_0_61_0.session_meta_payload()
    assert second["claudeCode"]["options"]["settingSources"] == []
    assert first is not second


def test_session_metadata_must_be_canonical_json_object_text() -> None:
    cases = {
        "unparsable": "not json",
        "non-object": '["settingSources"]',
        "non-canonical spacing": '{"claudeCode": {"options": {}}}',
        "non-canonical key order": '{"b":1,"a":2}',
        "empty": "",
    }
    for label, value in cases.items():
        with pytest.raises(ValueError):
            _claude_like(session_meta=value)
        assert label


def test_session_metadata_hash_binding_is_immutable_and_canonical() -> None:
    import dataclasses

    from agent_run_supervisor.native_acp.profile import CLAUDE_AGENT_ACP_0_61_0

    baseline = CLAUDE_AGENT_ACP_0_61_0.profile_hash()
    assert CLAUDE_AGENT_ACP_0_61_0.profile_hash() == baseline
    # A different frozen metadata is a different profile identity.
    other = dataclasses.replace(
        CLAUDE_AGENT_ACP_0_61_0,
        session_meta='{"claudeCode":{"options":{"settingSources":["user"]}}}',
    )
    assert other.profile_hash() != baseline
    # The frozen dataclass cannot be mutated in place.
    with pytest.raises(dataclasses.FrozenInstanceError):
        CLAUDE_AGENT_ACP_0_61_0.session_meta = "{}"  # type: ignore[misc]


def test_legacy_profiles_carry_no_session_metadata() -> None:
    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7

    for profile in (OPENCODE_NATIVE_ACP, CODEX_ACP_1_1_7):
        assert profile.session_meta is None
        assert profile.session_meta_payload() is None
        assert "session_meta" not in profile.snapshot()
    assert OPENCODE_NATIVE_ACP.profile_hash() == OPENCODE_PROFILE_HASH_GOLDEN
    assert CODEX_ACP_1_1_7.profile_hash() == CODEX_PROFILE_HASH_GOLDEN

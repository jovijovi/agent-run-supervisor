"""C4: closed, code-registered profile registry and the first OpenCode profile."""

from __future__ import annotations

import pytest

from agent_run_supervisor.native_acp.profile import (
    DEFAULT_REGISTRY,
    OPENCODE_1_18_4,
    AgentProfile,
    ProfileRegistry,
    UnknownProfileError,
    resolve_registered_executable,
)


def test_registry_is_a_closed_set() -> None:
    assert DEFAULT_REGISTRY.get("opencode-1.18.4") is OPENCODE_1_18_4
    with pytest.raises(UnknownProfileError):
        DEFAULT_REGISTRY.get("mystery-agent-9.9")


def test_opencode_profile_literals_are_pinned() -> None:
    profile = OPENCODE_1_18_4
    assert profile.profile_id == "opencode-1.18.4"
    assert profile.revision >= 1
    assert profile.argv_template == ("acp",)  # fixed subcommand, no passthrough
    assert profile.model_selector_id == "model"
    assert profile.effort_selector_id == "effort"
    assert profile.default_model == "kimi-for-coding/k3"
    assert profile.default_effort == "max"
    assert "max" in profile.allowed_efforts
    assert profile.requires_session_load is True
    assert profile.credential_slots == ("kimi-for-coding", "deepseek")


def test_opencode_profile_registers_the_approved_second_model() -> None:
    # Chair-approved C10 decision: the exact model+effort contract is kept
    # and the registered closed model pair is k3 plus deepseek-v4-pro (the
    # only configured-provider text/code model advertising a literal effort
    # both of whose offered values sit inside the registered effort domain).
    profile = OPENCODE_1_18_4
    assert profile.revision == 2
    assert profile.registered_models == (
        "kimi-for-coding/k3",
        "deepseek/deepseek-v4-pro",
    )
    assert profile.credential_slots == ("kimi-for-coding", "deepseek")
    assert profile.snapshot_ref() == "registry:opencode-1.18.4@r2"
    assert set(("high", "max")) <= set(profile.allowed_efforts)


def test_profile_hash_and_snapshot_are_deterministic() -> None:
    first = OPENCODE_1_18_4.profile_hash()
    second = OPENCODE_1_18_4.profile_hash()
    assert first == second
    assert len(first) == 64
    snapshot = OPENCODE_1_18_4.snapshot()
    assert snapshot["profile_id"] == "opencode-1.18.4"
    assert snapshot["registered_models"] == [
        "kimi-for-coding/k3",
        "deepseek/deepseek-v4-pro",
    ]
    assert OPENCODE_1_18_4.snapshot_ref() == "registry:opencode-1.18.4@r2"
    assert len(OPENCODE_1_18_4.config_schema_hash()) == 64


def test_executable_resolution_is_registry_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = resolve_registered_executable(OPENCODE_1_18_4.executable_key)
    # No caller/env path override: poisoning PATH and a lookalike env var
    # changes nothing about the resolution.
    monkeypatch.setenv("PATH", "/tmp/adversarial-bin")
    monkeypatch.setenv("OPENCODE_BIN", "/tmp/adversarial-bin/opencode")
    assert resolve_registered_executable(OPENCODE_1_18_4.executable_key) == registered
    assert registered.is_absolute()
    with pytest.raises(UnknownProfileError):
        resolve_registered_executable("unregistered-agent")


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
            extra_argv=("--danger",),
        )


def test_registry_refuses_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        ProfileRegistry((OPENCODE_1_18_4, OPENCODE_1_18_4))


def test_opencode_permission_mediation_env_is_registered() -> None:
    # A4-S2 repair: the registered OpenCode launch binding must force the
    # privileged tool families (edit/bash/webfetch) through client-mediated
    # session/request_permission — OpenCode's default build agent otherwise
    # auto-allows in-process writes with zero mediation.
    from agent_run_supervisor.native_acp.profile import (
        resolve_registered_permission_env,
    )

    pairs = resolve_registered_permission_env(OPENCODE_1_18_4.executable_key)
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
FROZEN_CLI_PATH = "/home/ecs-user/.local/bin/codex"
FROZEN_CLI_SHA256 = (
    "a2a05dafaa1acb002a45eaec0a462de5b13694fcfcd7bc43305f14781ce7be14"
)
FROZEN_CODEX_HOME = "/home/ecs-user/.config/agent-run-supervisor/codex-acp-1.1.7"
FROZEN_CODEX_CONFIG = '{"features":{"use_legacy_landlock":true}}'

# Byte-stability goldens captured at HEAD 773200b6 before the additive fields
# existed: omit-when-empty/None serialization must keep them identical.
OPENCODE_PROFILE_HASH_GOLDEN = (
    "0fb53b2c3eac618ad323d36ead44b8c74e2119d144a4e1f449a06d5144502842"
)
OPENCODE_CONFIG_SCHEMA_HASH_GOLDEN = (
    "1f278ec26c5effeb5d7265d0af20343add1f4fa90951b6f9030662c8efd65bfe"
)


def test_codex_profile_snapshot_golden() -> None:
    """Every registered value is a byte-copy of the frozen manifests.

    ``CODEX_CONFIG`` pins Landlock because the adapter's default bwrap sandbox
    was disqualified at discovery (FAIL: the command reached the Codex sandbox
    without ACP permission mediation and failed with a bwrap loopback
    RTM_NEWADDR EPERM on this host). It is load-bearing, version-bound debt:
    any adapter/CLI/Node/CODEX_CONFIG change requires a full new
    install → discovery → permission-canary cycle and a profile revision bump.
    """
    from agent_run_supervisor.native_acp.attestation import ExpectedRuntimeIdentity
    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7

    profile = CODEX_ACP_1_1_7
    assert profile.profile_id == "codex-acp-1.1.7"
    assert profile.revision == 1
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
        ("CODEX_HOME", FROZEN_CODEX_HOME),
        ("CODEX_PATH", FROZEN_CLI_PATH),
        ("CODEX_CONFIG", FROZEN_CODEX_CONFIG),
        ("INITIAL_AGENT_MODE", "read-only"),
        ("NO_BROWSER", "1"),
    )
    assert profile.expected_runtime == ExpectedRuntimeIdentity(
        node_path=FROZEN_NODE_PATH,
        node_sha256=FROZEN_NODE_SHA256,
        adapter_entry_path=FROZEN_ADAPTER_ENTRY,
        adapter_entry_sha256=FROZEN_ADAPTER_ENTRY_SHA256,
        cli_path=FROZEN_CLI_PATH,
        cli_sha256=FROZEN_CLI_SHA256,
        agent_info_name="@agentclientprotocol/codex-acp",
        agent_info_version="1.1.7",
        protocol_version="1",
    )

    snapshot = profile.snapshot()
    assert snapshot["fixed_env"] == [
        ["CODEX_HOME", FROZEN_CODEX_HOME],
        ["CODEX_PATH", FROZEN_CLI_PATH],
        ["CODEX_CONFIG", FROZEN_CODEX_CONFIG],
        ["INITIAL_AGENT_MODE", "read-only"],
        ["NO_BROWSER", "1"],
    ]
    assert snapshot["required_credential_refs"] == ["codex-home-auth"]
    assert snapshot["expected_runtime"]["protocol_version"] == "1"
    assert snapshot["expected_runtime"]["node_sha256"] == FROZEN_NODE_SHA256
    assert snapshot["expected_runtime"]["adapter_entry_sha256"] == (
        FROZEN_ADAPTER_ENTRY_SHA256
    )
    assert snapshot["expected_runtime"]["cli_sha256"] == FROZEN_CLI_SHA256
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
    assert DEFAULT_REGISTRY.ids() == ("codex-acp-1.1.7", "opencode-1.18.4")


def _codex_like(**overrides) -> AgentProfile:
    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7
    import dataclasses

    return dataclasses.replace(CODEX_ACP_1_1_7, **overrides)


def test_codex_fixed_env_validation_rules() -> None:
    identity = _codex_like().expected_runtime
    base = (
        ("CODEX_HOME", FROZEN_CODEX_HOME),
        ("CODEX_PATH", FROZEN_CLI_PATH),
    )
    cases = {
        "duplicate key": base + (("CODEX_HOME", FROZEN_CODEX_HOME),),
        "non-allowlisted name": base + (("LD_PRELOAD", "/tmp/evil.so"),),
        "oversized value": base + (("CODEX_CONFIG", "{" + "x" * 4096 + "}"),),
        "non-printable value": base + (("NO_BROWSER", "1\n"),),
        "non-canonical CODEX_CONFIG": base
        + (("CODEX_CONFIG", '{"features": {"use_legacy_landlock": true}}'),),
        "non-object CODEX_CONFIG": base + (("CODEX_CONFIG", '["not-an-object"]'),),
        "unparsable CODEX_CONFIG": base + (("CODEX_CONFIG", "not json"),),
        "invalid INITIAL_AGENT_MODE": base + (("INITIAL_AGENT_MODE", "yolo"),),
        "identity without CODEX_HOME": (("CODEX_PATH", FROZEN_CLI_PATH),),
        "identity without CODEX_PATH": (("CODEX_HOME", FROZEN_CODEX_HOME),),
    }
    for label, fixed_env in cases.items():
        with pytest.raises(ValueError):
            _codex_like(fixed_env=fixed_env, expected_runtime=identity)
        assert label  # keeps the failing case identifiable

    # The exact manifest tuple stays valid.
    _codex_like()


def test_codex_fixed_env_allowed_modes_accepted() -> None:
    identity = _codex_like().expected_runtime
    for mode in ("read-only", "agent", "agent-full-access"):
        profile = _codex_like(
            fixed_env=(
                ("CODEX_HOME", FROZEN_CODEX_HOME),
                ("CODEX_PATH", FROZEN_CLI_PATH),
                ("INITIAL_AGENT_MODE", mode),
            ),
            expected_runtime=identity,
        )
        assert dict(profile.fixed_env)["INITIAL_AGENT_MODE"] == mode


def test_opencode_snapshot_and_profile_hash_byte_stable() -> None:
    # Additive fields serialize omit-when-empty/None, so the legacy row's
    # snapshot and hashes stay byte-identical to HEAD 773200b6.
    snapshot = OPENCODE_1_18_4.snapshot()
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
    assert OPENCODE_1_18_4.profile_hash() == OPENCODE_PROFILE_HASH_GOLDEN
    assert OPENCODE_1_18_4.config_schema_hash() == OPENCODE_CONFIG_SCHEMA_HASH_GOLDEN
    assert OPENCODE_1_18_4.fixed_env == ()
    assert OPENCODE_1_18_4.expected_runtime is None
    assert OPENCODE_1_18_4.required_credential_refs is None


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

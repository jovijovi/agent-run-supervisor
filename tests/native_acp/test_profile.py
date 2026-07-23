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

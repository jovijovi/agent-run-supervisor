"""C4: admission freeze order — profile/launch resolution before an immutable
sealed AgentRunSpec; EffectiveRunState stays observation-only."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp.profile import (
    DEFAULT_REGISTRY,
    OPENCODE_1_18_4,
    AgentProfile,
    ProfileRegistry,
)
from agent_run_supervisor.native_acp.spec import (
    AgentRunRequest,
    AgentRunSpec,
    EffectiveRunState,
    InputRef,
    ResolvedLaunchSpec,
    RunLimits,
    RunSpecAssembler,
    SpecFreezeOrderError,
    SpecSealedError,
    SpecValidationError,
    resolve_workspace_binding,
    spec_hash,
)

# Stability pin for the canonical-JSON spec-hash (filled from the first GREEN
# run; any canonicalization drift afterwards is a regression).
GOLDEN_SPEC_HASH = "895dbbd3dee0979f23b2dc96ad59e6106e9d821839051118744d4975eb97c3cd"


def _request(**overrides) -> AgentRunRequest:
    kwargs = dict(
        owner="hermes",
        namespace="hermes/doc-check",
        profile_id="opencode-1.18.4",
        session_reuse="none",
        ars_session_id=None,
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),),
        requested_model="kimi-for-coding/k3",
        requested_effort="max",
        grant_ref="grant:doc-check-1",
        grant_hash="sha256:" + "b" * 64,
        grant_role_hash="sha256:" + "c" * 64,
        grant_capabilities=("read",),
        mcp_snapshot_hashes=(),
        credential_refs=("kimi-for-coding",),
        limits=RunLimits(),
        evidence_policy_hash="sha256:" + "d" * 64,
        recovery_policy_hash="sha256:" + "e" * 64,
    )
    kwargs.update(overrides)
    return AgentRunRequest(**kwargs)


def _sealed(tmp_path: Path, request: AgentRunRequest | None = None, **seal_overrides):
    assembler = RunSpecAssembler(request or _request())
    assembler.resolve_profile(DEFAULT_REGISTRY)
    assembler.bind_workspace(root=tmp_path)
    assembler.resolve_launch()
    seal_kwargs = dict(run_id="run-0001", submitted_at="2026-07-21T00:00:00+00:00")
    seal_kwargs.update(seal_overrides)
    return assembler.seal(**seal_kwargs)


# -- request validation -----------------------------------------------------


def test_request_requires_owner_and_namespace() -> None:
    with pytest.raises(SpecValidationError):
        _request(owner="")
    with pytest.raises(SpecValidationError):
        _request(namespace="")


def test_request_reuse_requires_session_id() -> None:
    with pytest.raises(SpecValidationError):
        _request(session_reuse="reuse", ars_session_id=None)
    _request(session_reuse="reuse", ars_session_id="sess-1")  # valid


def test_request_rejects_unknown_reuse_mode() -> None:
    with pytest.raises(SpecValidationError):
        _request(session_reuse="clone")


@pytest.mark.parametrize("version", [2, 0, -1, True, False, "1", 1.0, None])
def test_r4_b4_request_rejects_non_exact_schema_version(version) -> None:
    from agent_run_supervisor.native_acp.spec import SPEC_SCHEMA_VERSION

    assert SPEC_SCHEMA_VERSION == 1
    with pytest.raises(SpecValidationError):
        _request(schema_version=version)  # type: ignore[arg-type]


def test_r4_b4_request_defaults_missing_schema_version_to_current() -> None:
    from agent_run_supervisor.native_acp.spec import SPEC_SCHEMA_VERSION

    req = _request()
    assert req.schema_version == SPEC_SCHEMA_VERSION
    assert SPEC_SCHEMA_VERSION == 1


def test_limits_must_be_positive() -> None:
    with pytest.raises(SpecValidationError):
        RunLimits(turn_timeout_seconds=0)
    with pytest.raises(SpecValidationError):
        RunLimits(max_stderr_bytes=-1)


def test_run_limits_defaults_unchanged() -> None:
    limits = RunLimits()
    assert limits.startup_timeout_seconds == 60.0
    assert limits.turn_timeout_seconds == 600.0
    assert limits.cancel_grace_seconds == 10.0
    assert limits.max_stderr_bytes == 262_144
    assert limits.max_event_bytes == 65_536
    assert limits.max_events == 10_000


def test_run_limits_reject_bool_wrong_types_nan_inf() -> None:
    from agent_run_supervisor.native_acp import spec as spec_mod

    with pytest.raises(SpecValidationError):
        RunLimits(startup_timeout_seconds=True)  # type: ignore[arg-type]
    with pytest.raises(SpecValidationError):
        RunLimits(max_events=True)  # type: ignore[arg-type]
    with pytest.raises(SpecValidationError):
        RunLimits(max_stderr_bytes=1.5)  # type: ignore[arg-type]
    with pytest.raises(SpecValidationError):
        RunLimits(startup_timeout_seconds=float("nan"))
    with pytest.raises(SpecValidationError):
        RunLimits(turn_timeout_seconds=float("inf"))
    with pytest.raises(SpecValidationError):
        RunLimits(cancel_grace_seconds=float("-inf"))
    # Named operational ceilings must be exported and enforced.
    assert spec_mod.LIMIT_STARTUP_TIMEOUT_SECONDS_MAX == 3600
    assert spec_mod.LIMIT_TURN_TIMEOUT_SECONDS_MAX == 86400
    assert spec_mod.LIMIT_CANCEL_GRACE_SECONDS_MAX == 300
    assert spec_mod.LIMIT_MAX_STDERR_BYTES_MAX == 64 * 1024 * 1024
    assert spec_mod.LIMIT_MAX_EVENT_BYTES_MAX == 1024 * 1024
    assert spec_mod.LIMIT_MAX_EVENTS_MAX == 1_000_000
    assert spec_mod.LIMIT_MAX_EVENT_BYTES_MIN == 256
    assert spec_mod.LIMIT_EVENT_BUDGET_BYTES == 1024 * 1024 * 1024


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"startup_timeout_seconds": 3600.1}, "startup_timeout_seconds"),
        ({"turn_timeout_seconds": 86400.1}, "turn_timeout_seconds"),
        ({"cancel_grace_seconds": 300.1}, "cancel_grace_seconds"),
        ({"max_stderr_bytes": 64 * 1024 * 1024 + 1}, "max_stderr_bytes"),
        ({"max_event_bytes": 1024 * 1024 + 1}, "max_event_bytes"),
        ({"max_events": 1_000_001}, "max_events"),
        ({"max_event_bytes": 255}, "max_event_bytes"),
    ],
)
def test_run_limits_reject_above_caps_and_below_min_event_bytes(
    kwargs: dict, fragment: str
) -> None:
    with pytest.raises(SpecValidationError) as err:
        RunLimits(**kwargs)
    message = str(err.value)
    assert fragment in message
    assert "sk-live-" not in message
    # Deterministic: no repr of the offending numeric value required.
    assert message == message.strip()


def test_run_limits_reject_event_budget_exceeding_one_gib() -> None:
    # 65536 * 20000 = 1_310_720_000 > 1GiB
    with pytest.raises(SpecValidationError) as err:
        RunLimits(max_event_bytes=65_536, max_events=20_000)
    assert "budget" in str(err.value).lower() or "1" in str(err.value)


def test_run_limits_accept_boundary_caps() -> None:
    from agent_run_supervisor.native_acp import spec as spec_mod

    RunLimits(startup_timeout_seconds=spec_mod.LIMIT_STARTUP_TIMEOUT_SECONDS_MAX)
    RunLimits(turn_timeout_seconds=spec_mod.LIMIT_TURN_TIMEOUT_SECONDS_MAX)
    RunLimits(cancel_grace_seconds=spec_mod.LIMIT_CANCEL_GRACE_SECONDS_MAX)
    RunLimits(max_stderr_bytes=spec_mod.LIMIT_MAX_STDERR_BYTES_MAX)
    # Boundary pair that saturates but does not exceed the 1GiB budget.
    RunLimits(
        max_event_bytes=spec_mod.LIMIT_MAX_EVENT_BYTES_MAX,
        max_events=spec_mod.LIMIT_EVENT_BUDGET_BYTES // spec_mod.LIMIT_MAX_EVENT_BYTES_MAX,
    )
    RunLimits(max_event_bytes=spec_mod.LIMIT_MAX_EVENT_BYTES_MIN, max_events=10_000)


# -- freeze order -----------------------------------------------------------


def test_seal_requires_resolved_profile_and_launch(tmp_path: Path) -> None:
    assembler = RunSpecAssembler(_request())
    with pytest.raises(SpecFreezeOrderError):
        assembler.seal(run_id="run-1", submitted_at="2026-07-21T00:00:00+00:00")
    assembler.resolve_profile(DEFAULT_REGISTRY)
    with pytest.raises(SpecFreezeOrderError):
        assembler.seal(run_id="run-1", submitted_at="2026-07-21T00:00:00+00:00")
    assembler.bind_workspace(root=tmp_path)
    with pytest.raises(SpecFreezeOrderError):
        assembler.seal(run_id="run-1", submitted_at="2026-07-21T00:00:00+00:00")
    assembler.resolve_launch()
    spec = assembler.seal(run_id="run-1", submitted_at="2026-07-21T00:00:00+00:00")
    assert isinstance(spec, AgentRunSpec)


def test_launch_requires_profile_and_workspace(tmp_path: Path) -> None:
    assembler = RunSpecAssembler(_request())
    with pytest.raises(SpecFreezeOrderError):
        assembler.resolve_launch()
    assembler.resolve_profile(DEFAULT_REGISTRY)
    with pytest.raises(SpecFreezeOrderError):
        assembler.resolve_launch()


def test_sealing_twice_fails(tmp_path: Path) -> None:
    assembler = RunSpecAssembler(_request())
    assembler.resolve_profile(DEFAULT_REGISTRY)
    assembler.bind_workspace(root=tmp_path)
    assembler.resolve_launch()
    assembler.seal(run_id="run-1", submitted_at="2026-07-21T00:00:00+00:00")
    with pytest.raises(SpecSealedError):
        assembler.seal(run_id="run-2", submitted_at="2026-07-21T00:00:01+00:00")


def test_sealed_spec_is_immutable(tmp_path: Path) -> None:
    spec = _sealed(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.run_id = "run-9999"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.runtime.model_id = "other/model"  # type: ignore[misc]


def test_effort_outside_profile_domain_is_refused() -> None:
    assembler = RunSpecAssembler(_request(requested_effort="turbo"))
    with pytest.raises(SpecValidationError):
        assembler.resolve_profile(DEFAULT_REGISTRY)


def test_model_outside_registered_closed_set_is_refused() -> None:
    # The model selector's value domain is a closed registration: a request
    # for any unregistered model is refused at profile resolution.
    assembler = RunSpecAssembler(_request(requested_model="mystery/model-z"))
    with pytest.raises(SpecValidationError):
        assembler.resolve_profile(DEFAULT_REGISTRY)


def test_registered_second_model_is_admissible() -> None:
    assembler = RunSpecAssembler(
        _request(requested_model="deepseek/deepseek-v4-pro", requested_effort="high")
    )
    profile = assembler.resolve_profile(DEFAULT_REGISTRY)
    assert "deepseek/deepseek-v4-pro" in profile.registered_models


# -- spec hash --------------------------------------------------------------


def test_spec_hash_excludes_generated_fields(tmp_path: Path) -> None:
    base = _sealed(tmp_path)
    differently_generated = _sealed(
        tmp_path,
        run_id="run-7777",
        submitted_at="2026-07-22T09:30:00+00:00",
        retry_of_run_id="run-0001",
    )
    assert spec_hash(base) == spec_hash(differently_generated)


def test_spec_hash_binds_identity_and_inputs(tmp_path: Path) -> None:
    base = _sealed(tmp_path)
    assert spec_hash(_sealed(tmp_path, _request(owner="other"))) != spec_hash(base)
    assert (
        spec_hash(_sealed(tmp_path, _request(namespace="hermes/else")))
        != spec_hash(base)
    )
    changed_input = _request(
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "f" * 64),)
    )
    assert spec_hash(_sealed(tmp_path, changed_input)) != spec_hash(base)


def test_spec_hash_golden_stability() -> None:
    # Fully deterministic spec constructed directly (no filesystem inputs) so
    # the canonical-JSON hash is a portable golden.
    spec = AgentRunSpec.for_golden_fixture()
    assert spec_hash(spec) == GOLDEN_SPEC_HASH


# -- workspace binding ------------------------------------------------------


def test_workspace_binding_validates_root_and_cwd(tmp_path: Path) -> None:
    inside = tmp_path / "project"
    inside.mkdir()
    binding = resolve_workspace_binding(root=tmp_path, cwd=str(inside))
    assert binding.canonical_root == str(tmp_path.resolve())
    assert binding.effective_cwd == str(inside.resolve())
    assert binding.workspace_hash
    with pytest.raises(SpecValidationError):
        resolve_workspace_binding(root=tmp_path, cwd="/outside-root")
    with pytest.raises(SpecValidationError):
        resolve_workspace_binding(root=tmp_path / "missing", cwd=None)


# -- resolved launch spec ---------------------------------------------------


def _synthetic_profile(**overrides) -> AgentProfile:
    kwargs = dict(
        profile_id="synthetic-agent-1.0",
        revision=1,
        executable_key="opencode",
        argv_template=("serve", "--workspace", "<effective_cwd>"),
        env_allowlist=("HOME", "PATH", "ARS_TEST_SECRET_SLOT"),
        credential_slots=("test-slot",),
        model_selector_id="model",
        effort_selector_id="effort",
        default_model="provider/model-x",
        default_effort="max",
        registered_models=("provider/model-x",),
        allowed_efforts=("high", "max"),
        requires_session_load=False,
        config_schema={"selectors": {"model": "string", "effort": "string"}},
    )
    kwargs.update(overrides)
    return AgentProfile(**kwargs)


def test_launch_argv_substitutes_only_effective_cwd(tmp_path: Path) -> None:
    registry = ProfileRegistry((_synthetic_profile(),))
    request = _request(
        profile_id="synthetic-agent-1.0",
        requested_model="provider/model-x",
        requested_effort="max",
    )
    assembler = RunSpecAssembler(request)
    assembler.resolve_profile(registry)
    binding = assembler.bind_workspace(root=tmp_path)
    launch = assembler.resolve_launch()
    assert launch.argv[1:] == ("serve", "--workspace", binding.effective_cwd)
    assert launch.transport == "stdio"


def test_launch_refuses_unregistered_template_token(tmp_path: Path) -> None:
    registry = ProfileRegistry(
        (_synthetic_profile(argv_template=("serve", "<agent_home>")),)
    )
    request = _request(
        profile_id="synthetic-agent-1.0",
        requested_model="provider/model-x",
        requested_effort="max",
    )
    assembler = RunSpecAssembler(request)
    assembler.resolve_profile(registry)
    assembler.bind_workspace(root=tmp_path)
    with pytest.raises(SpecValidationError):
        assembler.resolve_launch()


def test_launch_serialization_carries_slot_names_never_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "hunter2-sentinel-value"
    monkeypatch.setenv("ARS_TEST_SECRET_SLOT", sentinel)
    registry = ProfileRegistry((_synthetic_profile(),))
    request = _request(
        profile_id="synthetic-agent-1.0",
        requested_model="provider/model-x",
        requested_effort="max",
    )
    assembler = RunSpecAssembler(request)
    assembler.resolve_profile(registry)
    assembler.bind_workspace(root=tmp_path)
    launch = assembler.resolve_launch()
    rendered = repr(launch) + json.dumps(launch.to_dict())
    assert "ARS_TEST_SECRET_SLOT" in rendered  # the slot NAME is carried
    assert sentinel not in rendered  # the value never is
    assert launch.env_allowlist == ("HOME", "PATH", "ARS_TEST_SECRET_SLOT")
    assert isinstance(launch, ResolvedLaunchSpec)


def test_launch_carries_registered_opencode_permission_mediation_env(
    tmp_path: Path,
) -> None:
    # A4-S2 repair: admission must bind the OpenCode launch to client-mediated
    # permission asks; the binding is registered supervisor policy, never
    # caller input, and it is durable launch evidence (launch.json).
    assembler = RunSpecAssembler(_request())
    assembler.resolve_profile(DEFAULT_REGISTRY)
    assembler.bind_workspace(root=tmp_path)
    launch = assembler.resolve_launch()
    expected = (
        ("OPENCODE_PERMISSION", '{"bash":"ask","edit":"ask","webfetch":"ask"}'),
    )
    assert launch.permission_env == expected
    payload = launch.to_dict()
    assert payload["permission_env"] == [list(pair) for pair in expected]


def test_launch_hash_binds_permission_env(tmp_path: Path) -> None:
    def _launch(permission_env) -> ResolvedLaunchSpec:
        return ResolvedLaunchSpec(
            executable="/registered/agent",
            argv=("/registered/agent", "acp"),
            env_allowlist=("HOME", "PATH"),
            credential_refs=(),
            profile_id="synthetic-agent-1.0",
            profile_revision=1,
            profile_hash="0" * 64,
            config_schema_hash="1" * 64,
            permission_env=permission_env,
        )

    bound = _launch((("OPENCODE_PERMISSION", '{"edit":"ask"}'),))
    unbound = _launch(())
    assert bound.launch_hash() != unbound.launch_hash()


# -- effective state --------------------------------------------------------


def test_effective_state_holds_observations_only(tmp_path: Path) -> None:
    spec = _sealed(tmp_path)
    state = EffectiveRunState()
    state.agent_session_id = "external-1"
    state.effective_model = "kimi-for-coding/k3"
    state.effective_effort = "max"
    payload = state.to_dict()
    assert payload["agent_session_id"] == "external-1"
    # Observations never flow back into the sealed spec.
    assert spec.runtime.model_id == "kimi-for-coding/k3"
    assert spec.session.ars_session_id is None


@pytest.mark.parametrize(
    "field",
    [
        "startup_timeout_seconds",
        "turn_timeout_seconds",
        "cancel_grace_seconds",
    ],
)
def test_r6_b5_run_limits_huge_int_no_overflow(field: str) -> None:
    with pytest.raises(SpecValidationError) as err:
        RunLimits(**{field: 10**10000})
    message = str(err.value)
    assert field in message
    assert "OverflowError" not in message


@pytest.mark.parametrize(
    "field",
    [
        "startup_timeout_seconds",
        "turn_timeout_seconds",
        "cancel_grace_seconds",
    ],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_r6_b5_run_limits_nan_inf_refused(field: str, bad: float) -> None:
    with pytest.raises(SpecValidationError):
        RunLimits(**{field: bad})


# -- Codex closed-profile admission (D3/D11) ---------------------------------

FROZEN_NODE_PATH = (
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/node/v24.14.0/bin/node"
)
FROZEN_ADAPTER_ENTRY = (
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/codex-acp/1.1.7"
    "/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
)
FROZEN_CLI_PATH = "/home/ecs-user/.local/bin/codex"
FROZEN_CODEX_HOME = "/home/ecs-user/.config/agent-run-supervisor/codex-acp-1.1.7"
FROZEN_CODEX_CONFIG = '{"features":{"use_legacy_landlock":true}}'

# Captured at HEAD 773200b6 before the additive launch fields existed.
OPENCODE_LAUNCH_HASH_GOLDEN = (
    "d15e8baae17b841ff569aa1710bf6dca680084c06e1bdc904c08f388428e2bcf"
)


def _codex_request(**overrides) -> AgentRunRequest:
    kwargs = dict(
        profile_id="codex-acp-1.1.7",
        requested_model="gpt-5.6-sol",
        requested_effort="max",
        credential_refs=("codex-home-auth",),
    )
    kwargs.update(overrides)
    return _request(**kwargs)


def _codex_launch(tmp_path: Path, request: AgentRunRequest | None = None):
    assembler = RunSpecAssembler(request or _codex_request())
    assembler.resolve_profile(DEFAULT_REGISTRY)
    assembler.bind_workspace(root=tmp_path)
    return assembler.resolve_launch()


def test_codex_launch_argv_frozen_node_plus_entry_golden(tmp_path: Path) -> None:
    launch = _codex_launch(tmp_path)
    # argv is exactly (frozen node, adapter entry js) — D9: the process image
    # is Node and resolution never consults PATH.
    assert launch.executable == FROZEN_NODE_PATH
    assert launch.argv == (FROZEN_NODE_PATH, FROZEN_ADAPTER_ENTRY)
    assert launch.credential_refs == ("codex-home-auth",)
    assert launch.permission_env == ()

    payload = launch.to_dict()
    assert payload["argv"] == [FROZEN_NODE_PATH, FROZEN_ADAPTER_ENTRY]
    assert payload["fixed_env"] == [
        ["CODEX_HOME", FROZEN_CODEX_HOME],
        ["CODEX_PATH", FROZEN_CLI_PATH],
        ["CODEX_CONFIG", FROZEN_CODEX_CONFIG],
        ["INITIAL_AGENT_MODE", "read-only"],
        ["NO_BROWSER", "1"],
    ]
    # Expected identity persists in launch.json before any check can fail.
    assert payload["expected_runtime"]["node_path"] == FROZEN_NODE_PATH
    assert payload["expected_runtime"]["adapter_entry_path"] == FROZEN_ADAPTER_ENTRY
    assert payload["expected_runtime"]["cli_path"] == FROZEN_CLI_PATH
    assert payload["expected_runtime"]["protocol_version"] == "1"
    assert len(launch.launch_hash()) == 64

    # The legacy OpenCode launch hash is byte-stable across the additive fields.
    opencode = RunSpecAssembler(_request())
    opencode.resolve_profile(DEFAULT_REGISTRY)
    opencode.bind_workspace(root=tmp_path)
    assert opencode.resolve_launch().launch_hash() == OPENCODE_LAUNCH_HASH_GOLDEN


def test_resolve_launch_performs_no_artifact_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D3: resolve_launch only mirrors the frozen expectation into the launch
    # spec — it opens no attested artifact. Artifact hashing belongs to the
    # spawn-boundary attestation, so expected identity persists in launch.json
    # before any check can fail.
    import builtins
    import os as os_module

    opened: list[str] = []
    real_open = builtins.open
    real_os_open = os_module.open

    def spy_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def spy_os_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_os_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os_module, "open", spy_os_open)
    launch = _codex_launch(tmp_path)
    monkeypatch.undo()

    assert launch.expected_runtime is not None
    for artifact in (FROZEN_NODE_PATH, FROZEN_ADAPTER_ENTRY, FROZEN_CLI_PATH):
        assert artifact not in opened


def test_launch_serialization_omits_empty_new_fields(tmp_path: Path) -> None:
    assembler = RunSpecAssembler(_request())
    assembler.resolve_profile(DEFAULT_REGISTRY)
    assembler.bind_workspace(root=tmp_path)
    launch = assembler.resolve_launch()
    payload = launch.to_dict()
    assert "fixed_env" not in payload
    assert "expected_runtime" not in payload
    assert launch.fixed_env == ()
    assert launch.expected_runtime is None
    assert sorted(payload) == [
        "argv",
        "config_schema_hash",
        "credential_refs",
        "env_allowlist",
        "executable",
        "permission_env",
        "profile_hash",
        "profile_id",
        "profile_revision",
        "transport",
    ]


def test_launch_hash_binds_fixed_env_and_expected_runtime(tmp_path: Path) -> None:
    import dataclasses

    from agent_run_supervisor.native_acp.profile import CODEX_ACP_1_1_7

    baseline = _codex_launch(tmp_path)
    moved_home = tuple(
        (name, str(tmp_path / "other-home") if name == "CODEX_HOME" else value)
        for name, value in CODEX_ACP_1_1_7.fixed_env
    )
    derived = dataclasses.replace(
        CODEX_ACP_1_1_7, profile_id="codex-acp-1.1.7-derived", fixed_env=moved_home
    )
    assembler = RunSpecAssembler(
        _codex_request(profile_id="codex-acp-1.1.7-derived")
    )
    assembler.resolve_profile(ProfileRegistry((derived,)))
    assembler.bind_workspace(root=tmp_path)
    assert assembler.resolve_launch().launch_hash() != baseline.launch_hash()


@pytest.mark.parametrize(
    "credential_refs",
    [
        (),                                            # missing
        ("kimi-for-coding",),                          # wrong
        ("codex-home-auth", "kimi-for-coding"),        # extra
        ("codex-home-auth", "codex-home-auth"),        # duplicated
    ],
)
def test_codex_credential_refs_exact_match_required(
    tmp_path: Path, credential_refs
) -> None:
    assembler = RunSpecAssembler(_codex_request(credential_refs=credential_refs))
    # Refused at admission — before workspace bind, before any credential-root
    # access, before spawn.
    with pytest.raises(SpecValidationError) as err:
        assembler.resolve_profile(DEFAULT_REGISTRY)
    assert "credential_refs" in str(err.value)


def test_codex_credential_refs_exact_match_accepted(tmp_path: Path) -> None:
    assembler = RunSpecAssembler(_codex_request())
    profile = assembler.resolve_profile(DEFAULT_REGISTRY)
    assert profile.required_credential_refs == ("codex-home-auth",)


def test_opencode_credential_refs_unconstrained_unchanged(tmp_path: Path) -> None:
    # required_credential_refs is None for the legacy row: no constraint.
    for refs in ((), ("kimi-for-coding",), ("kimi-for-coding", "deepseek")):
        assembler = RunSpecAssembler(_request(credential_refs=refs))
        profile = assembler.resolve_profile(DEFAULT_REGISTRY)
        assert profile.required_credential_refs is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"requested_model": "gpt-5.6-terra"},
        {"requested_model": "kimi-for-coding/k3"},
        {"requested_effort": "high"},
        {"requested_effort": "xhigh"},
    ],
)
def test_codex_out_of_domain_model_or_effort_refused(overrides) -> None:
    assembler = RunSpecAssembler(_codex_request(**overrides))
    with pytest.raises(SpecValidationError):
        assembler.resolve_profile(DEFAULT_REGISTRY)

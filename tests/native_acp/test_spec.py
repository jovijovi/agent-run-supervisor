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

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


def test_limits_must_be_positive() -> None:
    with pytest.raises(SpecValidationError):
        RunLimits(turn_timeout_seconds=0)
    with pytest.raises(SpecValidationError):
        RunLimits(max_stderr_bytes=-1)


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

"""Admission freeze order and the sealed, value-blind Run identity.

Resolve the agent (entry × profile) → bind the workspace → resolve the
environment exactly once → materialize the launch snapshot → seal the immutable
``AgentRunSpec``. Each step needs the previous one, and the order is enforced
rather than documented.

``EffectiveRunState``/``ObservedRuntime`` stay observation-only and never write
back. The launch snapshot is value-blind by schema, so a value-bearing key
cannot be reintroduced by an additive edit.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_COMPAT_V1,
    DEFAULT_REGISTRY,
    STANDARD_NATIVE_ACP_V1,
    AcpCompatProfile,
    ProfileRegistry,
    UnknownProfileError,
)
from agent_run_supervisor.native_acp import spec as spec_module
from agent_run_supervisor.native_acp.spec import (
    LAUNCH_SCHEMA_VERSION,
    NativeSpecError,
    SPEC_SCHEMA_VERSION,
    AgentRunRequest,
    AgentRunSpec,
    EffectiveRunState,
    EnvProjection,
    InputRef,
    LaunchSnapshot,
    ObservedRuntime,
    RunLimits,
    RunSpecAssembler,
    SpecFreezeOrderError,
    SpecSealedError,
    SpecValidationError,
    launch_hash_of_payload,
    launch_payload_shape_is_exact,
    resolve_run_environment,
    resolve_workspace_binding,
    spec_hash,
    spec_hash_of_payload,
    spec_payload_shape_is_exact,
)

AGENT_ID = "some-agent"

# Stability pin for the canonical-JSON spec hash of the golden fixture. Any
# canonicalization drift afterwards is a regression, not a re-baseline.
GOLDEN_SPEC_HASH = spec_hash(AgentRunSpec.for_golden_fixture())


def _request(**overrides) -> AgentRunRequest:
    kwargs = dict(
        owner="hermes",
        namespace="hermes/doc-check",
        agent_id=AGENT_ID,
        session_id=None,
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),),
        requested_model="provider/model",
        requested_effort="max",
        grant_ref="grant:doc-check-1",
        grant_hash="sha256:" + "b" * 64,
        grant_role_hash="sha256:" + "c" * 64,
        grant_capabilities=("read",),
        mcp_snapshot_hashes=(),
        credential_refs=(),
        limits=RunLimits(),
        evidence_policy_hash="sha256:" + "d" * 64,
        recovery_policy_hash="sha256:" + "e" * 64,
    )
    kwargs.update(overrides)
    return AgentRunRequest(**kwargs)


def _entry(**overrides) -> AgentEntry:
    kwargs = dict(
        agent_id=AGENT_ID,
        profile_id=STANDARD_NATIVE_ACP_V1.profile_id,
        command="some-agent",
    )
    kwargs.update(overrides)
    return AgentEntry(**kwargs)


def _assembled(tmp_path: Path, *, request=None, entry=None):
    request = request or _request()
    entry = entry or _entry()
    assembler = RunSpecAssembler(request)
    instance = assembler.resolve_agent(entry, registry=DEFAULT_REGISTRY)
    assembler.bind_workspace(root=tmp_path)
    environment = resolve_run_environment(
        arsd_env={"HOME": str(tmp_path), "PATH": "/usr/bin"},
        profile=instance.profile,
        entry=entry,
    )
    launch = assembler.resolve_launch(environment=environment)
    return assembler, instance, launch


# -- the wire request ---------------------------------------------------------


def test_the_request_names_an_agent_and_never_a_profile():
    fields = {field.name for field in dataclasses.fields(AgentRunRequest)}
    assert "agent_id" in fields
    assert "profile_id" not in fields
    assert _request().schema_version == SPEC_SCHEMA_VERSION


def test_schema_version_must_be_exactly_the_current_one():
    with pytest.raises(SpecValidationError):
        _request(schema_version=1)
    with pytest.raises(SpecValidationError):
        _request(schema_version=True)


def test_reuse_requires_a_session_id():
    with pytest.raises(SpecValidationError):
        _request(session_id="../escape")
    _request(session_id="sess-1")


def test_expected_binding_hash_is_carried_unchanged_and_undisposed():
    """F1: left explicitly undisposed rather than silently changed."""
    assert "expected_binding_hash" in {
        field.name for field in dataclasses.fields(AgentRunRequest)
    }
    request = _request(expected_binding_hash="sha256:" + "9" * 64)
    assert request.expected_binding_hash == "sha256:" + "9" * 64


def test_the_request_carries_no_model_or_effort_domain_check():
    """Live discovery is the domain authority; admission judges shape only."""
    _request(requested_model="anything-the-agent-might-advertise")
    _request(requested_effort="whatever")


def test_run_limits_default_to_a_six_hour_turn_timeout():
    assert RunLimits().turn_timeout_seconds == 21_600.0


# -- freeze order -------------------------------------------------------------


def test_launch_requires_a_resolved_agent_and_a_bound_workspace(tmp_path: Path):
    assembler = RunSpecAssembler(_request())
    environment = resolve_run_environment(
        arsd_env={}, profile=STANDARD_NATIVE_ACP_V1, entry=_entry()
    )
    with pytest.raises(SpecFreezeOrderError):
        assembler.resolve_launch(environment=environment)
    assembler.resolve_agent(_entry(), registry=DEFAULT_REGISTRY)
    with pytest.raises(SpecFreezeOrderError):
        assembler.resolve_launch(environment=environment)


def test_seal_requires_the_whole_chain(tmp_path: Path):
    assembler = RunSpecAssembler(_request())
    with pytest.raises(SpecFreezeOrderError):
        assembler.seal(run_id="run-1", submitted_at="2026-07-30T00:00:00+00:00")


def test_a_second_seal_is_refused(tmp_path: Path):
    assembler, _, _ = _assembled(tmp_path)
    assembler.seal(run_id="run-1", submitted_at="2026-07-30T00:00:00+00:00")
    with pytest.raises(SpecSealedError):
        assembler.seal(run_id="run-1", submitted_at="2026-07-30T00:00:00+00:00")


def test_resolve_launch_accepts_only_the_resolved_environment(tmp_path: Path):
    """A plain mapping is not a *resolved once* environment and is refused."""
    assembler = RunSpecAssembler(_request())
    assembler.resolve_agent(_entry(), registry=DEFAULT_REGISTRY)
    assembler.bind_workspace(root=tmp_path)
    with pytest.raises(SpecValidationError):
        assembler.resolve_launch(environment={"HOME": "/home/svc"})


def test_the_entry_must_name_the_requesting_agent(tmp_path: Path):
    assembler = RunSpecAssembler(_request(agent_id="other-agent"))
    with pytest.raises(SpecValidationError):
        assembler.resolve_agent(_entry(), registry=DEFAULT_REGISTRY)


def test_an_entry_naming_an_unregistered_profile_is_refused(tmp_path: Path):
    assembler = RunSpecAssembler(_request())
    with pytest.raises(UnknownProfileError):
        assembler.resolve_agent(
            _entry(profile_id="nope"), registry=DEFAULT_REGISTRY
        )


# -- the launch snapshot ------------------------------------------------------


def test_argv_zero_is_the_declared_command(tmp_path: Path):
    _, _, launch = _assembled(tmp_path, entry=_entry(command="some-agent", args=("acp",)))
    assert launch.command == "some-agent"
    assert launch.argv == ("some-agent", "acp")
    assert launch.argv[0] == launch.command


def test_a_launch_snapshot_refuses_an_argv_that_rewrites_argv_zero():
    with pytest.raises(SpecValidationError):
        LaunchSnapshot(
            command="some-agent",
            argv=("/resolved/path/some-agent",),
            profile_id="standard-native-acp-v1",
            profile_revision=1,
            profile_hash="0" * 64,
            agent_id=AGENT_ID,
            env=EnvProjection(resolved_count=0, names=()),
        )


def test_the_launch_snapshot_has_no_executable_field():
    fields = {field.name for field in dataclasses.fields(LaunchSnapshot)}
    assert "executable" not in fields
    assert "env_allowlist" not in fields
    assert "fixed_env" not in fields
    assert "permission_env" not in fields


def test_the_launch_projection_is_exactly_the_production_shape(tmp_path: Path):
    _, _, launch = _assembled(tmp_path)
    payload = launch.to_dict()
    payload["launch_spec_hash"] = launch.launch_hash()
    assert launch_payload_shape_is_exact(payload)
    assert payload["schema_version"] == LAUNCH_SCHEMA_VERSION


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.__setitem__("fixed_env", [["K", "V"]]),
        lambda p: p.__setitem__("permission_env", [["K", "V"]]),
        lambda p: p.__setitem__("transport", "stdio"),
        lambda p: p.pop("env"),
        lambda p: p.__setitem__("env", {"values": {"K": "V"}}),
        lambda p: p.__setitem__("schema_version", 1),
    ],
)
def test_a_launch_document_outside_the_allowlist_is_not_a_production_record(
    tmp_path: Path, mutate
):
    _, _, launch = _assembled(tmp_path)
    payload = launch.to_dict()
    payload["launch_spec_hash"] = launch.launch_hash()
    mutate(payload)
    assert not launch_payload_shape_is_exact(payload)


def test_the_launch_hash_rule_is_shared_by_writer_and_reader(tmp_path: Path):
    _, _, launch = _assembled(tmp_path)
    payload = launch.to_dict()
    payload["launch_spec_hash"] = launch.launch_hash()
    assert launch_hash_of_payload(payload) == launch.launch_hash()


def test_the_compat_profile_mirrors_its_frozen_session_meta(tmp_path: Path):
    entry = _entry(profile_id=CLAUDE_AGENT_ACP_COMPAT_V1.profile_id)
    _, _, launch = _assembled(tmp_path, entry=entry)
    assert launch.session_meta == CLAUDE_AGENT_ACP_COMPAT_V1.session_meta
    assert launch.to_dict()["session_meta"] == (
        CLAUDE_AGENT_ACP_COMPAT_V1.session_meta_payload()
    )


def test_a_profile_without_metadata_omits_the_key(tmp_path: Path):
    _, _, launch = _assembled(tmp_path)
    assert "session_meta" not in launch.to_dict()


# -- the sealed Spec ----------------------------------------------------------


def test_the_sealed_spec_carries_agent_identity_and_the_operator_epoch(tmp_path: Path):
    assembler, _, launch = _assembled(tmp_path, entry=_entry(session_epoch=3))
    spec = assembler.seal(run_id="run-1", submitted_at="2026-07-30T00:00:00+00:00")
    assert spec.agent.agent_id == AGENT_ID
    assert spec.agent.profile_id == STANDARD_NATIVE_ACP_V1.profile_id
    assert spec.agent.session_epoch == 3
    assert spec.launch_spec_hash == launch.launch_hash()


def test_the_absent_epoch_is_omitted_from_the_projection(tmp_path: Path):
    assembler, _, _ = _assembled(tmp_path)
    spec = assembler.seal(run_id="run-1", submitted_at="2026-07-30T00:00:00+00:00")
    payload = spec.to_dict()
    assert "session_epoch" not in payload["agent"]
    assert spec_payload_shape_is_exact({**payload, "spec_hash": spec_hash(spec)})


def test_a_present_epoch_is_projected(tmp_path: Path):
    assembler, _, _ = _assembled(tmp_path, entry=_entry(session_epoch=2))
    spec = assembler.seal(run_id="run-1", submitted_at="2026-07-30T00:00:00+00:00")
    payload = spec.to_dict()
    assert payload["agent"]["session_epoch"] == 2
    assert spec_payload_shape_is_exact({**payload, "spec_hash": spec_hash(spec)})


def test_the_spec_hash_excludes_exactly_the_generated_fields(tmp_path: Path):
    assembler, _, _ = _assembled(tmp_path)
    spec = assembler.seal(run_id="run-1", submitted_at="2026-07-30T00:00:00+00:00")
    other = dataclasses.replace(spec, run_id="run-2", submitted_at="2026-08-01T00:00:00+00:00")
    assert spec_hash(spec) == spec_hash(other)
    moved = dataclasses.replace(
        spec, identity=dataclasses.replace(spec.identity, owner="someone-else")
    )
    assert spec_hash(spec) != spec_hash(moved)


def test_the_spec_hash_rule_is_shared_by_writer_and_reader(tmp_path: Path):
    assembler, _, _ = _assembled(tmp_path)
    spec = assembler.seal(run_id="run-1", submitted_at="2026-07-30T00:00:00+00:00")
    payload = spec.to_dict()
    payload["spec_hash"] = spec_hash(spec)
    assert spec_hash_of_payload(payload) == spec_hash(spec)


def test_the_golden_spec_hash_is_stable():
    assert spec_hash(AgentRunSpec.for_golden_fixture()) == GOLDEN_SPEC_HASH
    assert len(GOLDEN_SPEC_HASH) == 64


def test_every_spec_field_appears_in_the_projection():
    """``to_dict`` is hand-written, so the guarantee is a structural test."""
    spec = AgentRunSpec.for_golden_fixture()
    payload = spec.to_dict()
    omitted = {qualified.split(".")[1] for qualified in ("agent.session_epoch",)}
    for field in dataclasses.fields(AgentRunSpec):
        assert field.name in payload
    for name, block in payload.items():
        if not isinstance(block, dict):
            continue
        model = getattr(spec, name)
        if not dataclasses.is_dataclass(model):
            continue
        expected = {f.name for f in dataclasses.fields(model)}
        assert set(block) in (expected, expected - omitted)


def test_workspace_fields_stay_complete_literals_and_hash_covered(tmp_path: Path):
    """Reviewer note 5: independently derived authority facts, not value flow."""
    workspace = tmp_path / "home" / "project"
    workspace.mkdir(parents=True)
    binding = resolve_workspace_binding(root=workspace)
    assert binding.canonical_root == str(workspace.resolve())
    assert binding.effective_cwd == str(workspace.resolve())
    assert len(binding.workspace_hash) == 64


def test_symlink_workspace_and_cwd_are_canonical_before_the_runspec_is_sealed(
    tmp_path: Path,
) -> None:
    real_workspace = tmp_path / "real-workspace"
    real_cwd = real_workspace / "nested"
    real_cwd.mkdir(parents=True)
    linked_workspace = tmp_path / "linked-workspace"
    linked_workspace.symlink_to(real_workspace, target_is_directory=True)

    request = _request()
    entry = _entry()
    assembler = RunSpecAssembler(request)
    instance = assembler.resolve_agent(entry, registry=DEFAULT_REGISTRY)
    binding = assembler.bind_workspace(
        root=linked_workspace,
        cwd=str(linked_workspace / "nested"),
    )
    assembler.resolve_launch(
        environment=resolve_run_environment(
            arsd_env={"HOME": str(tmp_path), "PATH": "/usr/bin"},
            profile=instance.profile,
            entry=entry,
        )
    )
    sealed = assembler.seal(
        run_id="run-symlink-canonical",
        submitted_at="2026-08-11T00:00:00+00:00",
    )

    assert binding.canonical_root == str(real_workspace.resolve())
    assert binding.effective_cwd == str(real_cwd.resolve())
    assert sealed.workspace.canonical_root == str(real_workspace.resolve())
    assert sealed.workspace.cwd == str(real_cwd.resolve())
    assert str(linked_workspace) not in json.dumps(sealed.to_dict())


# -- observations -------------------------------------------------------------


def test_observed_runtime_extends_effective_run_state():
    assert issubclass(ObservedRuntime, EffectiveRunState)
    observed = ObservedRuntime(declared_command="some-agent", resolved_argv=("some-agent",))
    payload = observed.to_dict()
    assert payload["observed_runtime"]["authoritative"] is False
    assert payload["observed_runtime"]["declared_command"] == "some-agent"


def test_observations_are_never_authoritative():
    payload = ObservedRuntime().to_dict()
    assert payload["observed_runtime"]["authoritative"] is False
    assert "authoritative" not in {
        field.name for field in dataclasses.fields(ObservedRuntime)
    }


def test_the_profile_registry_is_closed():
    registry = ProfileRegistry((STANDARD_NATIVE_ACP_V1,))
    assert registry.ids() == ("standard-native-acp-v1",)
    with pytest.raises(UnknownProfileError):
        registry.get("anything-else")
    with pytest.raises(UnknownProfileError):
        registry.get(None)


def test_a_duplicate_profile_id_is_refused():
    with pytest.raises(ValueError):
        ProfileRegistry((STANDARD_NATIVE_ACP_V1, STANDARD_NATIVE_ACP_V1))


def test_an_acp_compat_profile_freezes_no_deployment_fact():
    fields = {field.name for field in dataclasses.fields(AcpCompatProfile)}
    for banned in ("command", "argv", "path", "digest", "version", "executable_key"):
        assert banned not in fields


# -- D1: the no-close Session contract on the sealed Spec --------------------


def test_request_carries_one_optional_session_id_and_no_reuse_mode() -> None:
    fields = {f.name for f in dataclasses.fields(AgentRunRequest)}
    assert "session_id" in fields
    assert "session_reuse" not in fields
    assert "ars_session_id" not in fields
    assert not hasattr(spec_module, "_REUSE_MODES")


def test_spec_session_block_is_exactly_session_id_and_binding_hash() -> None:
    from agent_run_supervisor.native_acp.spec import SpecSession

    assert [f.name for f in dataclasses.fields(SpecSession)] == [
        "session_id",
        "expected_binding_hash",
    ]


def test_request_validates_session_id_grammar() -> None:
    for bad in ("../escape", "a/b", "a b", ".hidden", "-lead", ""):
        with pytest.raises(NativeSpecError):
            _request(session_id=bad)
    accepted = _request(session_id="sess-ok_1")
    assert accepted.session_id == "sess-ok_1"


# --- configurable per-Run event-ledger admission budget ----------------------
#
# Two different rules, deliberately separated: ``RunLimits`` judges field shape
# and each individual hard limit, while one injected policy object judges the
# cross-product ``max_event_bytes * max_events`` against the ceiling the
# admitting daemon was started with.

FOUR_GIB = 4 * 1024 * 1024 * 1024
# Exactly the default ceiling: 1 MiB per event × 4096 events.
_AT_DEFAULT_CEILING = {"max_event_bytes": 1024 * 1024, "max_events": 4096}


def test_the_default_run_event_budget_ceiling_is_four_gibibytes() -> None:
    assert spec_module.DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES == FOUR_GIB
    assert spec_module.EventBudgetPolicy().max_run_event_budget_bytes == FOUR_GIB
    assert (
        spec_module.DEFAULT_EVENT_BUDGET_POLICY.max_run_event_budget_bytes == FOUR_GIB
    )


def test_direct_run_limits_are_under_the_same_default_budget_policy() -> None:
    """The dev/direct construction path is judged, not exempted."""
    limits = RunLimits(**_AT_DEFAULT_CEILING)
    assert limits.max_event_bytes * limits.max_events == FOUR_GIB
    with pytest.raises(SpecValidationError):
        RunLimits(max_event_bytes=1024 * 1024, max_events=4097)


def test_an_injected_ceiling_decides_the_cross_product_to_the_byte() -> None:
    exact = 4096 * 100
    at_ceiling = spec_module.EventBudgetPolicy(max_run_event_budget_bytes=exact)
    one_byte_lower = spec_module.EventBudgetPolicy(max_run_event_budget_bytes=exact - 1)
    admitted = RunLimits(
        max_event_bytes=4096, max_events=100, event_budget_policy=at_ceiling
    )
    assert admitted.max_event_bytes * admitted.max_events == exact
    with pytest.raises(SpecValidationError):
        RunLimits(
            max_event_bytes=4096, max_events=100, event_budget_policy=one_byte_lower
        )


def test_a_raised_ceiling_admits_more_and_a_lowered_ceiling_less() -> None:
    generous = spec_module.EventBudgetPolicy(max_run_event_budget_bytes=2 * FOUR_GIB)
    beyond_default = RunLimits(
        max_event_bytes=1024 * 1024, max_events=8192, event_budget_policy=generous
    )
    assert beyond_default.max_event_bytes * beyond_default.max_events == 2 * FOUR_GIB
    with pytest.raises(SpecValidationError):
        # The same 8 GiB Run under the unchanged default ceiling.
        RunLimits(max_event_bytes=1024 * 1024, max_events=8192)
    strict = spec_module.EventBudgetPolicy(max_run_event_budget_bytes=1024 * 1024)
    with pytest.raises(SpecValidationError):
        RunLimits(max_event_bytes=1024 * 1024, max_events=2, event_budget_policy=strict)


def test_the_structural_maximum_is_derived_from_the_hard_limits() -> None:
    "One named bound, computed from the very limits it is a bound over."
    assert spec_module.STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES == (
        spec_module.LIMIT_MAX_EVENT_BYTES_MAX * spec_module.LIMIT_MAX_EVENTS_MAX
    )
    assert spec_module.DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES == FOUR_GIB
    assert FOUR_GIB < spec_module.STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES


def test_the_exact_structural_maximum_is_a_valid_ceiling() -> None:
    "Positive control: the bound itself configures, and admits the largest Run."
    bound = spec_module.STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES
    policy = spec_module.EventBudgetPolicy(max_run_event_budget_bytes=bound)
    assert policy.max_run_event_budget_bytes == bound
    assert spec_module.STRUCTURAL_EVENT_BUDGET_POLICY.max_run_event_budget_bytes == bound
    largest = RunLimits(
        max_event_bytes=spec_module.LIMIT_MAX_EVENT_BYTES_MAX,
        max_events=spec_module.LIMIT_MAX_EVENTS_MAX,
        event_budget_policy=policy,
    )
    assert largest.max_event_bytes * largest.max_events == bound


def test_a_ceiling_past_the_structural_maximum_is_refused() -> None:
    """A ceiling no Run could ever reach admits nothing and serializes badly.

    It would still be written into durable Run evidence and into every
    ``server_info`` frame, so an unbounded integer is a live serialization
    hazard bought for exactly zero admission value.
    """
    bound = spec_module.STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES
    with pytest.raises(SpecValidationError):
        spec_module.EventBudgetPolicy(max_run_event_budget_bytes=bound + 1)
    with pytest.raises(SpecValidationError):
        spec_module.EventBudgetPolicy(max_run_event_budget_bytes=10**4000)


@pytest.mark.parametrize("bad", [0, -1, True, False, 4096.0, "4096", None])
def test_a_configured_ceiling_fails_closed(bad) -> None:
    with pytest.raises(SpecValidationError):
        spec_module.EventBudgetPolicy(max_run_event_budget_bytes=bad)


def test_run_limits_refuse_an_object_that_is_not_an_event_budget_policy() -> None:
    class LooksLikeOne:
        max_run_event_budget_bytes = FOUR_GIB

        def check_run_limits(self, limits):
            return None

    with pytest.raises(SpecValidationError):
        RunLimits(event_budget_policy=LooksLikeOne())


def test_individual_hard_limits_survive_a_generous_ceiling() -> None:
    """Structural bounds are a separate rule and are not relaxed by a ceiling."""
    huge = spec_module.EventBudgetPolicy(
        max_run_event_budget_bytes=spec_module.STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES
    )
    for bad in (
        {"max_event_bytes": 1024 * 1024 + 1, "max_events": 1},
        {"max_event_bytes": 255, "max_events": 1},
        {"max_event_bytes": 256, "max_events": 1_000_001},
    ):
        with pytest.raises(SpecValidationError):
            RunLimits(**bad, event_budget_policy=huge)
    RunLimits(max_event_bytes=1024 * 1024, max_events=1, event_budget_policy=huge)
    RunLimits(max_event_bytes=256, max_events=1_000_000, event_budget_policy=huge)


def test_the_injected_budget_policy_is_not_sealed_run_limits_material() -> None:
    """The daemon's ceiling is never a per-Run field, wire key, or hash input."""
    assert [f.name for f in dataclasses.fields(RunLimits)] == [
        "startup_timeout_seconds",
        "turn_timeout_seconds",
        "cancel_grace_seconds",
        "max_stderr_bytes",
        "max_event_bytes",
        "max_events",
    ]
    strict = spec_module.EventBudgetPolicy(max_run_event_budget_bytes=FOUR_GIB // 2)
    under_policy = RunLimits(event_budget_policy=strict)
    assert dataclasses.asdict(under_policy) == dataclasses.asdict(RunLimits())
    assert under_policy == RunLimits()

"""Admission data model: freeze order and immutable Run identity (PRD R1).

``AgentRunRequest`` (validated wire input) → resolve the closed profile →
bind the workspace → materialize ``ResolvedLaunchSpec`` → seal the immutable
``AgentRunSpec``/``spec_hash``. ``EffectiveRunState`` holds observations only
and never writes back into Profile or Spec. Credential *values* never enter
this module — only slot names and references.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.process_liveness import ProcessIdentity
from agent_run_supervisor.role import PERMISSION_KINDS

from .profile import (
    AgentProfile,
    ProfileRegistry,
    resolve_registered_executable,
    resolve_registered_permission_env,
)

SPEC_SCHEMA_VERSION = 1

_CWD_TOKEN = "<effective_cwd>"
_REUSE_MODES = ("none", "reuse")
_MAX_FIELD_LENGTH = 512

# Finite operational ceilings for sealed RunLimits (Codex-review R2 / B4).
LIMIT_STARTUP_TIMEOUT_SECONDS_MAX = 3600.0
LIMIT_TURN_TIMEOUT_SECONDS_MAX = 86400.0
LIMIT_CANCEL_GRACE_SECONDS_MAX = 300.0
LIMIT_MAX_STDERR_BYTES_MAX = 64 * 1024 * 1024
LIMIT_MAX_EVENT_BYTES_MAX = 1024 * 1024
LIMIT_MAX_EVENTS_MAX = 1_000_000
LIMIT_MAX_EVENT_BYTES_MIN = 256
LIMIT_EVENT_BUDGET_BYTES = 1024 * 1024 * 1024


class NativeSpecError(ValueError):
    """Base class for admission/spec failures."""


class SpecValidationError(NativeSpecError):
    """A request/limit/workspace/launch value failed validation."""


class SpecFreezeOrderError(NativeSpecError):
    """The R1 freeze order was violated (seal before resolve, etc.)."""


class SpecSealedError(NativeSpecError):
    """A second seal was attempted on the same assembler."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SpecValidationError(message)


def _require_text(value: str, name: str, *, max_length: int = _MAX_FIELD_LENGTH) -> None:
    _require(isinstance(value, str) and bool(value), f"{name} must be a non-empty string")
    _require(len(value) <= max_length, f"{name} exceeds {max_length} characters")
    _require(
        all(ch.isprintable() for ch in value),
        f"{name} contains non-printable characters",
    )


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value == value
        and value not in (float("inf"), float("-inf"))
    )


def _validate_limit_float(name: str, value: Any, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecValidationError(f"limit {name} must be a finite number")
    # Integers: exact compare against positivity/maximum BEFORE float() so huge
    # decoded JSON ints never OverflowError inside validation.
    if type(value) is int:
        _require(value > 0, f"limit {name} must be positive")
        _require(value <= maximum, f"limit {name} exceeds maximum")
        return
    _require(
        value == value and value not in (float("inf"), float("-inf")),
        f"limit {name} must be a finite number",
    )
    _require(value > 0, f"limit {name} must be positive")
    _require(value <= maximum, f"limit {name} exceeds maximum")


def _validate_limit_int(name: str, value: Any, maximum: int, *, minimum: int = 1) -> None:
    _require(
        not isinstance(value, bool) and isinstance(value, int),
        f"limit {name} must be an integer",
    )
    _require(value >= minimum, f"limit {name} must be at least {minimum}")
    _require(value <= maximum, f"limit {name} exceeds maximum")


@dataclass(frozen=True)
class InputRef:
    ref: str
    content_hash: str

    def __post_init__(self) -> None:
        _require_text(self.ref, "input ref")
        _require_text(self.content_hash, "input content_hash")


@dataclass(frozen=True)
class RunLimits:
    startup_timeout_seconds: float = 60.0
    turn_timeout_seconds: float = 600.0
    cancel_grace_seconds: float = 10.0
    max_stderr_bytes: int = 262_144
    max_event_bytes: int = 65_536
    max_events: int = 10_000

    def __post_init__(self) -> None:
        _validate_limit_float(
            "startup_timeout_seconds",
            self.startup_timeout_seconds,
            LIMIT_STARTUP_TIMEOUT_SECONDS_MAX,
        )
        _validate_limit_float(
            "turn_timeout_seconds",
            self.turn_timeout_seconds,
            LIMIT_TURN_TIMEOUT_SECONDS_MAX,
        )
        _validate_limit_float(
            "cancel_grace_seconds",
            self.cancel_grace_seconds,
            LIMIT_CANCEL_GRACE_SECONDS_MAX,
        )
        _validate_limit_int(
            "max_stderr_bytes", self.max_stderr_bytes, LIMIT_MAX_STDERR_BYTES_MAX
        )
        _validate_limit_int(
            "max_event_bytes",
            self.max_event_bytes,
            LIMIT_MAX_EVENT_BYTES_MAX,
            minimum=LIMIT_MAX_EVENT_BYTES_MIN,
        )
        _validate_limit_int("max_events", self.max_events, LIMIT_MAX_EVENTS_MAX)
        budget = self.max_event_bytes * self.max_events
        _require(
            budget <= LIMIT_EVENT_BUDGET_BYTES,
            "limit event budget exceeds maximum (max_event_bytes * max_events)",
        )


@dataclass(frozen=True)
class AgentRunRequest:
    """Versioned wire input. Never carries shell text, argv, env, executable
    paths, or credential values — those surfaces do not exist here."""

    owner: str
    namespace: str
    profile_id: str
    session_reuse: str
    ars_session_id: str | None
    expected_binding_hash: str | None
    input_refs: tuple[InputRef, ...]
    requested_model: str
    requested_effort: str
    grant_ref: str
    grant_hash: str
    grant_role_hash: str
    grant_capabilities: tuple[str, ...]
    mcp_snapshot_hashes: tuple[str, ...]
    credential_refs: tuple[str, ...]
    limits: RunLimits
    evidence_policy_hash: str
    recovery_policy_hash: str
    schema_version: int = SPEC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(
            not isinstance(self.schema_version, bool)
            and isinstance(self.schema_version, int)
            and self.schema_version == SPEC_SCHEMA_VERSION,
            f"schema_version must be exactly {SPEC_SCHEMA_VERSION}",
        )
        _require_text(self.owner, "owner")
        _require_text(self.namespace, "namespace")
        _require_text(self.profile_id, "profile_id")
        _require(
            self.session_reuse in _REUSE_MODES,
            f"session_reuse must be one of {_REUSE_MODES}",
        )
        if self.session_reuse == "reuse":
            _require(
                bool(self.ars_session_id),
                "session_reuse='reuse' requires ars_session_id",
            )
        if self.ars_session_id is not None:
            _require_text(self.ars_session_id, "ars_session_id")
        _require_text(self.requested_model, "requested_model")
        _require_text(self.requested_effort, "requested_effort", max_length=64)
        _require_text(self.grant_ref, "grant_ref")
        _require_text(self.grant_hash, "grant_hash")
        _require_text(self.grant_role_hash, "grant_role_hash")
        for capability in self.grant_capabilities:
            _require(
                capability in PERMISSION_KINDS,
                f"unknown grant capability {capability!r}",
            )
        _require_text(self.evidence_policy_hash, "evidence_policy_hash")
        _require_text(self.recovery_policy_hash, "recovery_policy_hash")
        _require(isinstance(self.limits, RunLimits), "limits must be RunLimits")


@dataclass(frozen=True)
class WorkspaceBinding:
    canonical_root: str
    effective_cwd: str
    workspace_hash: str


def resolve_workspace_binding(*, root: Path, cwd: str | None = None) -> WorkspaceBinding:
    """Validate and bind the Run workspace (binding-config hash, not content)."""
    canonical_root = Path(root).expanduser().resolve()
    _require(canonical_root.is_dir(), f"workspace root {canonical_root} is not a directory")
    effective = Path(cwd).expanduser().resolve() if cwd else canonical_root
    _require(effective.is_dir(), f"effective cwd {effective} is not a directory")
    _require(
        effective == canonical_root or canonical_root in effective.parents,
        f"effective cwd {effective} is outside workspace root {canonical_root}",
    )
    payload = {
        "canonical_root": str(canonical_root),
        "effective_cwd": str(effective),
    }
    return WorkspaceBinding(
        canonical_root=str(canonical_root),
        effective_cwd=str(effective),
        workspace_hash=_sha256_hex(_canonical_json(payload)),
    )


@dataclass(frozen=True)
class ResolvedLaunchSpec:
    """Controlled launch material: fixed argv, slot names only, stdio.

    ``permission_env`` carries the registered agent-side permission mediation
    binding (name/value pairs injected at spawn): supervisor policy resolved
    from the closed profile registry, never caller input and never a
    credential value — serialized here as durable launch evidence.
    """

    executable: str
    argv: tuple[str, ...]
    env_allowlist: tuple[str, ...]
    credential_refs: tuple[str, ...]
    profile_id: str
    profile_revision: int
    profile_hash: str
    config_schema_hash: str
    permission_env: tuple[tuple[str, str], ...] = ()
    transport: str = "stdio"

    def to_dict(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "argv": list(self.argv),
            "env_allowlist": list(self.env_allowlist),
            "credential_refs": list(self.credential_refs),
            "profile_id": self.profile_id,
            "profile_revision": self.profile_revision,
            "profile_hash": self.profile_hash,
            "config_schema_hash": self.config_schema_hash,
            "permission_env": [list(pair) for pair in self.permission_env],
            "transport": self.transport,
        }

    def launch_hash(self) -> str:
        return _sha256_hex(_canonical_json(self.to_dict()))


@dataclass(frozen=True)
class RunIdentity:
    owner: str
    namespace: str


@dataclass(frozen=True)
class SpecSession:
    reuse: str
    ars_session_id: str | None
    expected_binding_hash: str | None


@dataclass(frozen=True)
class SpecAgent:
    profile_id: str
    profile_revision: int
    profile_snapshot_ref: str
    profile_hash: str
    config_schema_hash: str


@dataclass(frozen=True)
class SpecGrant:
    grant_ref: str
    grant_hash: str
    role_hash: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class SpecWorkspace:
    canonical_root: str
    cwd: str
    workspace_hash: str


@dataclass(frozen=True)
class SpecRuntime:
    model_id: str
    effort: str
    config_fidelity: str = "exact"


@dataclass(frozen=True)
class SpecBindings:
    mcp_snapshot_hashes: tuple[str, ...]
    credential_refs: tuple[str, ...]


@dataclass(frozen=True)
class AgentRunSpec:
    """Immutable requested facts, sealed before spawn."""

    schema_version: int
    identity: RunIdentity
    session: SpecSession
    agent: SpecAgent
    execution_grant: SpecGrant
    workspace: SpecWorkspace
    runtime: SpecRuntime
    bindings: SpecBindings
    input_refs: tuple[InputRef, ...]
    limits: RunLimits
    evidence_policy_hash: str
    recovery_policy_hash: str
    launch_spec_hash: str
    run_id: str
    submitted_at: str
    retry_of_run_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def for_golden_fixture() -> "AgentRunSpec":
        """A fully deterministic spec for the canonical-hash golden pin."""
        return AgentRunSpec(
            schema_version=SPEC_SCHEMA_VERSION,
            identity=RunIdentity(owner="golden-owner", namespace="golden/ns"),
            session=SpecSession(
                reuse="none", ars_session_id=None, expected_binding_hash=None
            ),
            agent=SpecAgent(
                profile_id="golden-profile-1.0",
                profile_revision=1,
                profile_snapshot_ref="registry:golden-profile-1.0@r1",
                profile_hash="0" * 64,
                config_schema_hash="1" * 64,
            ),
            execution_grant=SpecGrant(
                grant_ref="grant:golden",
                grant_hash="2" * 64,
                role_hash="3" * 64,
                capabilities=("read",),
            ),
            workspace=SpecWorkspace(
                canonical_root="/golden/root",
                cwd="/golden/root",
                workspace_hash="4" * 64,
            ),
            runtime=SpecRuntime(model_id="golden/model", effort="max"),
            bindings=SpecBindings(mcp_snapshot_hashes=(), credential_refs=("slot",)),
            input_refs=(InputRef(ref="prompt:golden", content_hash="sha256:" + "5" * 64),),
            limits=RunLimits(),
            evidence_policy_hash="6" * 64,
            recovery_policy_hash="7" * 64,
            launch_spec_hash="8" * 64,
            run_id="run-golden",
            submitted_at="2026-07-21T00:00:00+00:00",
            retry_of_run_id=None,
        )


# Generated Run identity/lineage fields excluded from the requested-fact hash:
# authenticated owner/namespace stay inside it (changing either changes it).
_GENERATED_FIELDS = ("run_id", "submitted_at", "retry_of_run_id")


def spec_hash(spec: AgentRunSpec) -> str:
    payload = spec.to_dict()
    for name in _GENERATED_FIELDS:
        payload.pop(name, None)
    return _sha256_hex(_canonical_json(payload))


@dataclass
class EffectiveRunState:
    """Observed effective state only; never rewrites Profile or Spec."""

    process_identity: ProcessIdentity | None = None
    agent_info: dict[str, Any] | None = None
    protocol_version: int | None = None
    capabilities: dict[str, Any] | None = None
    load_session_advertised: bool | None = None
    agent_session_id: str | None = None
    discovery_snapshots: list[dict[str, Any]] = field(default_factory=list)
    effective_model: str | None = None
    effective_effort: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_identity": (
                asdict(self.process_identity) if self.process_identity else None
            ),
            "agent_info": self.agent_info,
            "protocol_version": self.protocol_version,
            "capabilities": self.capabilities,
            "load_session_advertised": self.load_session_advertised,
            "agent_session_id": self.agent_session_id,
            "discovery_snapshots": list(self.discovery_snapshots),
            "effective_model": self.effective_model,
            "effective_effort": self.effective_effort,
        }


class RunSpecAssembler:
    """Enforces the R1 freeze order for one Run admission."""

    def __init__(self, request: AgentRunRequest) -> None:
        self._request = request
        self._profile: AgentProfile | None = None
        self._binding: WorkspaceBinding | None = None
        self._launch: ResolvedLaunchSpec | None = None
        self._sealed = False

    @property
    def request(self) -> AgentRunRequest:
        return self._request

    def resolve_profile(self, registry: ProfileRegistry) -> AgentProfile:
        profile = registry.get(self._request.profile_id)
        _require(
            self._request.requested_model in profile.registered_models,
            f"model {self._request.requested_model!r} is outside the registered "
            f"closed set {profile.registered_models} for {profile.profile_id}",
        )
        _require(
            self._request.requested_effort in profile.allowed_efforts,
            f"effort {self._request.requested_effort!r} is outside the registered "
            f"domain {profile.allowed_efforts} for {profile.profile_id}",
        )
        self._profile = profile
        return profile

    def bind_workspace(self, *, root: Path, cwd: str | None = None) -> WorkspaceBinding:
        self._binding = resolve_workspace_binding(root=root, cwd=cwd)
        return self._binding

    def resolve_launch(self) -> ResolvedLaunchSpec:
        if self._profile is None or self._binding is None:
            raise SpecFreezeOrderError(
                "resolve_launch requires a resolved profile and a bound workspace"
            )
        executable = resolve_registered_executable(self._profile.executable_key)
        argv: list[str] = [str(executable)]
        for token in self._profile.argv_template:
            if token == _CWD_TOKEN:
                argv.append(self._binding.effective_cwd)
            elif "<" in token or ">" in token:
                raise SpecValidationError(
                    f"unregistered argv template token {token!r}"
                )
            else:
                argv.append(token)
        self._launch = ResolvedLaunchSpec(
            executable=str(executable),
            argv=tuple(argv),
            env_allowlist=self._profile.env_allowlist,
            credential_refs=self._profile.credential_slots,
            profile_id=self._profile.profile_id,
            profile_revision=self._profile.revision,
            profile_hash=self._profile.profile_hash(),
            config_schema_hash=self._profile.config_schema_hash(),
            permission_env=resolve_registered_permission_env(
                self._profile.executable_key
            ),
        )
        return self._launch

    def seal(
        self,
        *,
        run_id: str,
        submitted_at: str,
        retry_of_run_id: str | None = None,
    ) -> AgentRunSpec:
        if self._sealed:
            raise SpecSealedError("this admission was already sealed")
        if self._profile is None or self._binding is None or self._launch is None:
            raise SpecFreezeOrderError(
                "seal requires resolved profile, bound workspace, and resolved launch"
            )
        _require_text(run_id, "run_id")
        _require_text(submitted_at, "submitted_at")
        request = self._request
        spec = AgentRunSpec(
            schema_version=request.schema_version,
            identity=RunIdentity(owner=request.owner, namespace=request.namespace),
            session=SpecSession(
                reuse=request.session_reuse,
                ars_session_id=request.ars_session_id,
                expected_binding_hash=request.expected_binding_hash,
            ),
            agent=SpecAgent(
                profile_id=self._profile.profile_id,
                profile_revision=self._profile.revision,
                profile_snapshot_ref=self._profile.snapshot_ref(),
                profile_hash=self._profile.profile_hash(),
                config_schema_hash=self._profile.config_schema_hash(),
            ),
            execution_grant=SpecGrant(
                grant_ref=request.grant_ref,
                grant_hash=request.grant_hash,
                role_hash=request.grant_role_hash,
                capabilities=request.grant_capabilities,
            ),
            workspace=SpecWorkspace(
                canonical_root=self._binding.canonical_root,
                cwd=self._binding.effective_cwd,
                workspace_hash=self._binding.workspace_hash,
            ),
            runtime=SpecRuntime(
                model_id=request.requested_model,
                effort=request.requested_effort,
            ),
            bindings=SpecBindings(
                mcp_snapshot_hashes=request.mcp_snapshot_hashes,
                credential_refs=request.credential_refs,
            ),
            input_refs=request.input_refs,
            limits=request.limits,
            evidence_policy_hash=request.evidence_policy_hash,
            recovery_policy_hash=request.recovery_policy_hash,
            launch_spec_hash=self._launch.launch_hash(),
            run_id=run_id,
            submitted_at=submitted_at,
            retry_of_run_id=retry_of_run_id,
        )
        self._sealed = True
        return spec

"""Admission data model: freeze order, immutable Run identity, sealed launch.

``AgentRunRequest`` (validated wire input) → resolve the registry entry and its
source profile → bind the workspace → resolve the environment **exactly once** →
materialize the value-blind ``LaunchSnapshot`` → seal the immutable
``AgentRunSpec``/``spec_hash``.

Two environment types carry the value boundary and never merge.
:class:`ResolvedEnvironment` is the ephemeral, non-serializable carrier of the
final projected values; it is accepted only by the process-spawn seam and is
consumed by nothing else. :class:`EnvProjection` is the separate
durable, value-blind shape that reaches ``launch.json``: per name, the name, its
source class, its precedence layer, and its redaction status, plus a resolved
count, the mediation id, and the operator-declared names that were absent from
the daemon's environment. No value, value digest, keyed digest, length, prefix,
suffix, equality token, or matcher table is ever hash input.

``EffectiveRunState``/``ObservedRuntime`` hold observations only and never write
back into a profile, a registry entry, or a Spec. Credential *values* never
enter this module — only caller-supplied references.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import InitVar, asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent_run_supervisor.process_liveness import ProcessIdentity
from agent_run_supervisor.session import is_valid_session_id

# The closed capability vocabulary a per-Run grant may name. Owned here because
# the Spec is the only thing that still reads it: a grant capability outside
# this set is refused rather than carried, so the set is part of the admission
# contract and not a leftover of the module it used to live in.
PERMISSION_KINDS: tuple[str, ...] = (
    "read",
    "search",
    "write",
    "execute",
    "terminal",
    "delete",
    "move",
    "fetch",
    "switch_mode",
    "other",
)

from .agent_registration import AgentEntry, validate_agent_id
from .launch_permissions import (
    ENV_SOURCE_LAUNCH_PERMISSION,
    MaterializedLaunchPermissions,
    policy_pair_is_exact,
    projection_matches_policy,
)
from .profile import (
    AcpCompatProfile,
    AgentInstance,
    ProfileRegistry,
    mediation_pairs,
)

# The sealed material genuinely changed again at the Session no-close model:
# the request's Session block became one optional ``session_id``, so a Spec
# sealed under the old reuse-mode shape describes an intent this runtime no
# longer models. It is therefore refused rather than silently re-interpreted
# under a shape it was never sealed against — the same rule that moved this
# constant at the reset, applied to the same kind of change.
#
# The launch snapshot did **not** change, so its version deliberately does not
# move: a version that tracks nothing tells a reader nothing.
SPEC_SCHEMA_VERSION = 3
LAUNCH_SCHEMA_VERSION = 2
_MAX_FIELD_LENGTH = 512

# Finite operational ceilings for sealed RunLimits.
LIMIT_STARTUP_TIMEOUT_SECONDS_MAX = 3600.0
LIMIT_TURN_TIMEOUT_SECONDS_MAX = 604800.0
LIMIT_CANCEL_GRACE_SECONDS_MAX = 300.0
LIMIT_MAX_STDERR_BYTES_MAX = 64 * 1024 * 1024
LIMIT_MAX_EVENT_BYTES_MAX = 1024 * 1024
LIMIT_MAX_EVENTS_MAX = 1_000_000
LIMIT_MAX_EVENT_BYTES_MIN = 256

# The *default* admission ceiling on one Run's normalized event ledger, in bytes
# (4 GiB). Deliberately not named ``LIMIT_*``: the three constants above are
# structural per-field maxima that no configuration moves, while this one is a
# deployment sizing choice an operator overrides at daemon startup. It bounds
# the theoretical worst case of a single Run's persistent ``events.jsonl`` —
# not preallocated memory, not the Run directory's total disk quota, and not a
# daemon-wide aggregate across concurrent Runs.
DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES = 4 * 1024 * 1024 * 1024

# The largest cross-product the individual structural limits above can produce,
# and therefore the largest ceiling that can ever admit anything: no Run may
# request more than ``LIMIT_MAX_EVENT_BYTES_MAX`` × ``LIMIT_MAX_EVENTS_MAX``, so
# a ceiling past this point admits exactly nothing extra. It is also the bound
# on the configured value, for a reason that is not tidiness: the ceiling is
# serialized into every accepted Run's durable evidence and into every
# ``server_info`` frame, so an unbounded integer is a live serialization hazard
# — bought for zero admission value. Derived here, once, from the limits it
# bounds; never respelled as a literal or as a digit-count rule.
STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES = LIMIT_MAX_EVENT_BYTES_MAX * LIMIT_MAX_EVENTS_MAX

# The environment layers, in application order. The two source-owned layers are
# applied last, always, as defense in depth: a defect in a parse-time collision
# check cannot silently let configuration shadow a source-owned pair. Layer 5 is
# present only for a profile that selects a launch-permission policy, and its
# key is source-owned in key and value exactly like layer 4's.
ENV_SOURCE_BASE = "base"
ENV_SOURCE_PASSTHROUGH = "passthrough"
ENV_SOURCE_OVERLAY = "overlay"
ENV_SOURCE_MEDIATION = "mediation"
ENV_PRECEDENCE = {
    ENV_SOURCE_BASE: 1,
    ENV_SOURCE_PASSTHROUGH: 2,
    ENV_SOURCE_OVERLAY: 3,
    ENV_SOURCE_MEDIATION: 4,
    ENV_SOURCE_LAUNCH_PERMISSION: 5,
}


class NativeSpecError(ValueError):
    """Base class for admission/spec failures."""


class SpecValidationError(NativeSpecError):
    """A request/limit/workspace/launch value failed validation."""


class SpecFreezeOrderError(NativeSpecError):
    """The freeze order was violated (seal before resolve, etc.)."""


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
class EventBudgetPolicy:
    """The admission ceiling one daemon applies to every Run it accepts.

    Two different questions are deliberately answered by two different objects.
    :class:`RunLimits` judges *field shape* and each *individual* structural
    hard limit — those are properties of the data model and no configuration
    moves them. This object judges exactly one *policy* question:
    ``max_event_bytes * max_events``, the theoretical worst case of one Run's
    persistent event ledger, against the ceiling this daemon was started with.

    It is a frozen value, injected where admission happens, so a differently
    configured daemon constructs its own instead of writing to shared state:
    there is no mutable module global to reconfigure, and no second copy of the
    rule for a direct/dev construction path to drift from.
    """

    max_run_event_budget_bytes: int = DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES

    def __post_init__(self) -> None:
        # Fail closed on configuration, exactly like a limit value: ``bool`` is
        # refused before ``int`` can accept it, since ``True`` is a positive
        # integer and a direct Python caller can supply one.
        _require(
            not isinstance(self.max_run_event_budget_bytes, bool)
            and isinstance(self.max_run_event_budget_bytes, int),
            "max_run_event_budget_bytes must be an integer",
        )
        _require(
            self.max_run_event_budget_bytes > 0,
            "max_run_event_budget_bytes must be positive",
        )
        _require(
            self.max_run_event_budget_bytes <= STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES,
            "max_run_event_budget_bytes exceeds the structural maximum",
        )

    def check_run_limits(self, limits: "RunLimits") -> None:
        """Refuse limits whose event-ledger worst case exceeds this ceiling."""
        budget = limits.max_event_bytes * limits.max_events
        _require(
            budget <= self.max_run_event_budget_bytes,
            "limit event budget exceeds the configured maximum "
            "(max_event_bytes * max_events)",
        )


# One immutable default instance, shared by every construction path that is not
# handed a daemon's own policy. Nothing rebinds or mutates it.
DEFAULT_EVENT_BUDGET_POLICY = EventBudgetPolicy()

# The policy at that structural bound: it adds nothing to the per-field limits.
# It is **not** a deployment setting and no daemon runs under it. It exists for
# the one step that has to understand a request *as a request* — its identity
# and canonical digest — before the admitting daemon's own policy is consulted,
# so that whether a retransmission is the same request cannot depend on a
# ceiling that changed since it was accepted. Parsing under it widens nothing:
# every per-field bound in ``RunLimits`` still applies, and new work is still
# judged by the daemon's real ceiling before anything is created.
STRUCTURAL_EVENT_BUDGET_POLICY = EventBudgetPolicy(
    max_run_event_budget_bytes=STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES
)


@dataclass(frozen=True)
class RunLimits:
    startup_timeout_seconds: float = 60.0
    turn_timeout_seconds: float = 21600.0
    cancel_grace_seconds: float = 10.0
    max_stderr_bytes: int = 262_144
    max_event_bytes: int = 65_536
    max_events: int = 10_000
    # The admitting daemon's ceiling, injected rather than looked up. An
    # ``InitVar`` is not a dataclass field, so it stays out of the wire key set,
    # ``dataclasses.fields``, ``asdict``, equality, and the sealed Spec
    # projection: policy decides admission and never becomes per-Run material,
    # which is why admission policy moves no schema version. ``None`` means the
    # default policy, so a direct/dev construction is judged rather than exempt.
    event_budget_policy: InitVar[EventBudgetPolicy | None] = None

    def __post_init__(self, event_budget_policy: EventBudgetPolicy | None) -> None:
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
        # Field shape is settled; the cross-product is the policy's question.
        policy = (
            DEFAULT_EVENT_BUDGET_POLICY
            if event_budget_policy is None
            else event_budget_policy
        )
        _require(
            isinstance(policy, EventBudgetPolicy),
            "event_budget_policy must be an EventBudgetPolicy",
        )
        policy.check_run_limits(self)


@dataclass(frozen=True)
class AgentRunRequest:
    """Versioned wire input.

    It never carries shell text, argv, environment keys or values, executable
    paths, or credential values — those surfaces do not exist here, so the
    refusal is structural rather than filtered. ``agent_id`` names an
    operator-registered agent and is the only registry-facing value that crosses
    the wire; its grammar belongs to exactly one place
    (:func:`~.agent_registration.validate_agent_id`), so a second copy of the
    rules cannot drift.
    """

    owner: str
    namespace: str
    agent_id: str
    # Carried unchanged and deliberately undisposed: whether this field keeps a
    # role after the reset is an explicit decision, not something to settle
    # inside a diff that was moving digest material for another reason.
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
    # The whole Session portion of the wire, and the only optional one: absent
    # (``None``) creates one new durable Session and runs its first Run;
    # present is existing-only reuse of exactly that Session. There is no reuse
    # mode, because there is nothing left for one to say — and no value of this
    # field can turn a reuse into a create.
    session_id: str | None = None
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
        _require(type(self.agent_id) is str, "agent_id must be a string")
        if self.session_id is not None:
            # Grammar is validated here, before the Spec is sealed and long
            # before any storage access: an unsafe id is a wire fact, and
            # nothing may touch a path to find out what it is.
            _require_text(self.session_id, "session_id")
            _require(
                is_valid_session_id(self.session_id),
                "session_id must be a safe session-store path component",
            )
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
    """Validate and bind the Run workspace (binding-config hash, not content).

    The canonical root and the effective cwd stay complete literals here and
    stay hash-covered, even when the workspace lives under ``$HOME``. They are
    independently derived authority facts, not environment-value flow, and
    guarding them would break workspace binding, reconciliation attribution, and
    audit.
    """
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


# ---------------------------------------------------------------------------
# Environment: one resolution, two types
# ---------------------------------------------------------------------------


def environment_layers(
    *,
    arsd_env: Mapping[str, str],
    base_names: Iterable[str],
    entry: AgentEntry,
    launch_permission: Iterable[tuple[str, str]] = (),
) -> tuple[tuple[str, str, str, int], ...]:
    """Compose the four layers and report the **winning** one per name.

    Order is base → pass-through → overlay → mediation, and a later layer wins.
    ``precedence`` is the winning layer, not a history: what a Run records is
    which authority actually supplied the value the child received.

    Layers 1 and 2 take names from the daemon's own environment *only when
    present*, so an absent name contributes nothing rather than an invented
    empty string. A name that is present but empty is a real declaration and is
    projected as it stands.
    """
    resolved: dict[str, tuple[str, str, int]] = {}

    def place(name: str, value: str, source: str) -> None:
        resolved[name] = (value, source, ENV_PRECEDENCE[source])

    for name in base_names:
        if name in arsd_env:
            place(name, arsd_env[name], ENV_SOURCE_BASE)
    for name in entry.env_passthrough:
        if name in arsd_env:
            place(name, arsd_env[name], ENV_SOURCE_PASSTHROUGH)
    for name, literal in entry.env_overlay:
        place(name, literal, ENV_SOURCE_OVERLAY)
    # Applied last, always.
    for name, literal in mediation_pairs(entry.mediation_id):
        place(name, literal, ENV_SOURCE_MEDIATION)
    for name, literal in launch_permission:
        place(name, literal, ENV_SOURCE_LAUNCH_PERMISSION)
    return tuple(
        (name, resolved[name][0], resolved[name][1], resolved[name][2])
        for name in sorted(resolved)
    )


def declared_absent_names(
    *, arsd_env: Mapping[str, str], entry: AgentEntry
) -> tuple[str, ...]:
    """Operator-declared pass-through names the daemon's environment did not hold.

    Recorded because "you declared it and it was not there" is the diagnosis an
    operator needs, and it is a **name**, so recording it discloses nothing.
    """
    return tuple(name for name in entry.env_passthrough if name not in arsd_env)


# The one categorical redaction marker the writer emits, named once so the
# emitter and the schema check cannot drift into two spellings of it.
ENV_REDACTION_MARKER = "all-values-withheld"


@dataclass(frozen=True)
class EnvName:
    """One durable, value-blind environment record."""

    name: str
    source: str
    precedence: int
    redacted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "precedence": self.precedence,
            "redacted": self.redacted,
        }


@dataclass(frozen=True)
class EnvProjection:
    """The durable environment evidence: names, classes, precedence — nothing else.

    Mediation values are withheld exactly like every other value: the mediation
    id is durable, and no Run record repeats its source-owned pairs.
    """

    resolved_count: int
    names: tuple[EnvName, ...]
    declared_absent: tuple[str, ...] = ()
    mediation_id: str | None = None
    values_persisted: bool = False
    redaction: str = ENV_REDACTION_MARKER

    def to_dict(self) -> dict[str, Any]:
        return {
            "values_persisted": self.values_persisted,
            "redaction": self.redaction,
            "resolved_count": self.resolved_count,
            "mediation_id": self.mediation_id,
            "names": [item.to_dict() for item in self.names],
            "declared_absent": list(self.declared_absent),
        }


class ResolvedEnvironment:
    """The ephemeral per-Run value carrier. Never durable, never serializable.

    It exists from the moment the environment is resolved until the child has
    been spawned. Exactly **one** consumer is allowed: the process-spawn seam,
    through :attr:`exec_mapping`. There is no accessor that enumerates the
    values for any other purpose, because there is no other purpose.

    Everything that could turn a value into a record is refused rather than
    merely avoided: there is no ``to_dict``, no mapping protocol, no
    serialization hook, and no value-derived equality or hash. ``repr`` and
    ``str`` name the count and nothing else, so an interpolated carrier in a log
    line or an exception message discloses nothing.
    """

    __slots__ = ("_values", "_projection")

    def __init__(
        self,
        layers: Iterable[tuple[str, str, str, int]],
        *,
        mediation_id: str | None = None,
        declared_absent: Iterable[str] = (),
    ) -> None:
        values: dict[str, str] = {}
        names: list[EnvName] = []
        for name, value, source, precedence in layers:
            values[name] = value
            names.append(EnvName(name=name, source=source, precedence=precedence))
        self._values = values
        self._projection = EnvProjection(
            resolved_count=len(values),
            names=tuple(names),
            declared_absent=tuple(declared_absent),
            mediation_id=mediation_id,
        )

    @property
    def exec_mapping(self) -> dict[str, str]:
        """A fresh copy for exec. The carrier never hands out its own mapping."""
        return dict(self._values)

    def value_blind_projection(self) -> EnvProjection:
        return self._projection

    def __repr__(self) -> str:
        return f"<ResolvedEnvironment names={len(self._values)} values=withheld>"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("ResolvedEnvironment is not serializable")


def resolve_environment(
    *,
    arsd_env: Mapping[str, str],
    base_names: Iterable[str],
    passthrough_names: Iterable[str],
    overlay: Iterable[tuple[str, str]],
    mediation: Iterable[tuple[str, str]],
    mediation_id: str | None = None,
    launch_permission: Iterable[tuple[str, str]] = (),
) -> ResolvedEnvironment:
    """Resolve the four layers exactly once, in memory, before sealing and spawn.

    The layer inputs are passed explicitly so this function has no opinion about
    where they came from and cannot re-read anything.
    """
    resolved: dict[str, tuple[str, str, int]] = {}

    def place(name: str, value: str, source: str) -> None:
        resolved[name] = (value, source, ENV_PRECEDENCE[source])

    passthrough = tuple(passthrough_names)
    for name in base_names:
        if name in arsd_env:
            place(name, arsd_env[name], ENV_SOURCE_BASE)
    for name in passthrough:
        if name in arsd_env:
            place(name, arsd_env[name], ENV_SOURCE_PASSTHROUGH)
    for name, literal in overlay:
        place(name, literal, ENV_SOURCE_OVERLAY)
    for name, literal in mediation:
        place(name, literal, ENV_SOURCE_MEDIATION)
    for name, literal in launch_permission:
        place(name, literal, ENV_SOURCE_LAUNCH_PERMISSION)
    layers = tuple(
        (name, resolved[name][0], resolved[name][1], resolved[name][2])
        for name in sorted(resolved)
    )
    return ResolvedEnvironment(
        layers,
        mediation_id=mediation_id,
        declared_absent=tuple(name for name in passthrough if name not in arsd_env),
    )


def resolve_run_environment(
    *,
    arsd_env: Mapping[str, str],
    profile: AcpCompatProfile,
    entry: AgentEntry,
    launch_permission: Iterable[tuple[str, str]] = (),
) -> ResolvedEnvironment:
    """The one-call composition the Run path uses. One resolution, one snapshot.

    ``launch_permission`` carries the source-owned pair of a profile that
    selected a launch-permission policy, and nothing else ever supplies it. It
    is empty for every other profile, so their projection is unchanged.
    """
    return resolve_environment(
        arsd_env=arsd_env,
        base_names=profile.base_allowlist,
        passthrough_names=entry.env_passthrough,
        overlay=entry.env_overlay,
        mediation=mediation_pairs(entry.mediation_id),
        mediation_id=entry.mediation_id,
        launch_permission=launch_permission,
    )


# ---------------------------------------------------------------------------
# The sealed launch snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaunchSnapshot:
    """Exactly what was handed to exec, recorded value-blind.

    ``command`` is the operator's declared string byte-for-byte and ``argv[0]``
    equals it. There is no ``executable`` field: the image is located by
    ``execvp``-style lookup over the child's projected ``PATH``, and where the
    kernel found it is an observation, never a sealed fact.
    """

    command: str
    argv: tuple[str, ...]
    profile_id: str
    profile_revision: int
    profile_hash: str
    agent_id: str
    env: EnvProjection
    mediation_id: str | None = None
    model_selector_id: str = "model"
    # ``None`` under model-only fidelity: the Run sets no effort selector, so
    # naming one here would seal a call that never happened.
    effort_selector_id: str | None = "effort"
    forbidden_capabilities: tuple[str, ...] = ()
    credential_refs: tuple[str, ...] = ()
    session_epoch: int | None = None
    session_meta: str | None = None
    # Present only for a profile that selected a launch-permission policy. The
    # digest binds the exact document the child was launched under, so the
    # policy is auditable without persisting where it lived or what it said.
    launch_permission_policy_id: str | None = None
    launch_permission_digest: str | None = None
    schema_version: int = LAUNCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(bool(self.argv), "launch argv must be non-empty")
        _require(
            self.argv[0] == self.command,
            "argv[0] must be the declared command string, byte for byte",
        )
        # The launch-permission evidence is one fact in three places, so it is
        # bound here rather than merely carried: the id and the digest are
        # all-or-none with a registered id and a canonical digest, and the
        # environment projection has to describe the same launch.
        _require(
            policy_pair_is_exact(
                self.launch_permission_policy_id, self.launch_permission_digest
            ),
            "launch permission policy id and digest must be absent together or "
            "present together, with a registered id and a canonical digest",
        )
        _require(
            projection_matches_policy(
                self.launch_permission_policy_id,
                tuple((item.name, item.source) for item in self.env.names),
            ),
            "the launch permission pair and the environment projection must "
            "describe the same launch",
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "command": self.command,
            "argv": list(self.argv),
            "profile_id": self.profile_id,
            "profile_revision": self.profile_revision,
            "profile_hash": self.profile_hash,
            "agent_id": self.agent_id,
            "env": self.env.to_dict(),
            "mediation_id": self.mediation_id,
            "model_selector_id": self.model_selector_id,
            "effort_selector_id": self.effort_selector_id,
            "forbidden_capabilities": list(self.forbidden_capabilities),
            "credential_refs": list(self.credential_refs),
            "session_epoch": self.session_epoch,
        }
        if self.session_meta is not None:
            payload["session_meta"] = json.loads(self.session_meta)
        if self.launch_permission_policy_id is not None:
            payload["launch_permission_policy_id"] = self.launch_permission_policy_id
            payload["launch_permission_digest"] = self.launch_permission_digest
        return payload

    def launch_hash(self) -> str:
        return _sha256_hex(_canonical_json(self.to_dict()))


@dataclass(frozen=True)
class RunIdentity:
    owner: str
    namespace: str


@dataclass(frozen=True)
class SpecSession:
    """The sealed Session intent: one optional id, and nothing else.

    ``session_id is None`` seals a create; a value seals existing-only reuse of
    exactly that Session. Reconciliation reads this block as its attribution
    authority, and a create derives its prospective id from the same
    authenticated identity that derived the Run id.
    """

    session_id: str | None
    expected_binding_hash: str | None


@dataclass(frozen=True)
class SpecAgent:
    """Which agent, under which source profile, at which operator epoch."""

    agent_id: str
    profile_id: str
    profile_revision: int
    profile_snapshot_ref: str
    profile_hash: str
    session_epoch: int | None


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
        """Every field, minus exactly the declared omit-when-None set."""
        payload = asdict(self)
        for qualified in SPEC_OMIT_WHEN_NONE:
            section, field_name = qualified.split(".")
            block = payload.get(section)
            if isinstance(block, dict) and block.get(field_name) is None:
                del block[field_name]
        return payload

    @staticmethod
    def for_golden_fixture() -> "AgentRunSpec":
        """A fully deterministic spec for the canonical-hash golden pin."""
        return AgentRunSpec(
            schema_version=SPEC_SCHEMA_VERSION,
            identity=RunIdentity(owner="golden-owner", namespace="golden/ns"),
            session=SpecSession(session_id=None, expected_binding_hash=None),
            agent=SpecAgent(
                agent_id="golden-agent",
                profile_id="golden-profile-v1",
                profile_revision=1,
                profile_snapshot_ref="registry:golden-profile-v1@r1",
                profile_hash="0" * 64,
                session_epoch=None,
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


# The complete, closed set of spec fields that leave the projection when they
# are ``None`` — qualified by section so the rule names one field of one block.
SPEC_OMIT_WHEN_NONE = ("agent.session_epoch",)

# Generated Run identity/lineage fields excluded from the requested-fact hash:
# authenticated owner/namespace stay inside it (changing either changes it).
_GENERATED_FIELDS = ("run_id", "submitted_at", "retry_of_run_id")


def spec_hash_of_payload(payload: Mapping[str, Any]) -> str:
    """The spec hash of an already-projected Spec document.

    The single canonical rule: exclude exactly the generated Run identity and
    lineage fields, then hash the canonical JSON of what remains. Both the
    production writer and any reader verifying a durable ``spec.json`` call
    *this* function, so a verifier can never drift into a subtly different
    digest.
    """
    material = {
        key: value
        for key, value in payload.items()
        if key not in _GENERATED_FIELDS and key != "spec_hash"
    }
    return _sha256_hex(_canonical_json(material))


def spec_hash(spec: AgentRunSpec) -> str:
    return spec_hash_of_payload(spec.to_dict())


def launch_hash_of_payload(payload: Mapping[str, Any]) -> str:
    """The launch seal of an already-projected launch document.

    Excludes exactly one field — the seal itself, which the writer appends after
    computing it — and hashes the canonical JSON of the rest.
    """
    material = {key: value for key, value in payload.items() if key != "launch_spec_hash"}
    return _sha256_hex(_canonical_json(material))


def _dataclass_projection_keys(model: type) -> set[str]:
    return {f.name for f in dataclasses.fields(model)}


def _spec_block_models() -> dict[str, type]:
    return {
        "identity": RunIdentity,
        "session": SpecSession,
        "agent": SpecAgent,
        "execution_grant": SpecGrant,
        "workspace": SpecWorkspace,
        "runtime": SpecRuntime,
        "bindings": SpecBindings,
        "limits": RunLimits,
    }


def spec_payload_shape_is_exact(payload: Any) -> bool:
    """Does this document carry exactly the production Spec projection?

    Key sets are derived from the production dataclasses rather than restated,
    so a field added to the Spec cannot silently become an unknown key here.
    """
    if not isinstance(payload, dict):
        return False
    expected_top = _dataclass_projection_keys(AgentRunSpec) | {"spec_hash"}
    omitted = {qualified.split(".")[1] for qualified in SPEC_OMIT_WHEN_NONE}
    if set(payload) != expected_top:
        return False
    for name, model in _spec_block_models().items():
        block = payload.get(name)
        if not isinstance(block, dict):
            return False
        expected = _dataclass_projection_keys(model)
        if name == "agent":
            present = set(block)
            if present not in (expected, expected - omitted):
                return False
            continue
        if set(block) != expected:
            return False
    input_refs = payload.get("input_refs")
    if not isinstance(input_refs, (list, tuple)):
        return False
    ref_keys = _dataclass_projection_keys(InputRef)
    for ref in input_refs:
        if not isinstance(ref, dict) or set(ref) != ref_keys:
            return False
    return True


# The launch document's closed key set, derived from the production dataclass
# plus the seal the writer appends. A value-bearing key such as ``fixed_env`` or
# ``permission_env`` is therefore not merely absent by habit: it is rejected by
# a schema-level allowlist, so it cannot be reintroduced by an additive edit.
LAUNCH_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "command",
        "argv",
        "profile_id",
        "profile_revision",
        "profile_hash",
        "agent_id",
        "env",
        "mediation_id",
        "model_selector_id",
        "effort_selector_id",
        "forbidden_capabilities",
        "credential_refs",
        "session_epoch",
        "launch_spec_hash",
    }
)
LAUNCH_OPTIONAL_FIELDS = frozenset(
    {"session_meta", "launch_permission_policy_id", "launch_permission_digest"}
)
ENV_PROJECTION_FIELDS = frozenset(
    {
        "values_persisted",
        "redaction",
        "resolved_count",
        "mediation_id",
        "names",
        "declared_absent",
    }
)
ENV_NAME_FIELDS = frozenset({"name", "source", "precedence", "redacted"})


def launch_payload_shape_is_exact(payload: Any) -> bool:
    """Does this document carry exactly a production launch projection?"""
    if not isinstance(payload, dict):
        return False
    present = set(payload)
    if not LAUNCH_REQUIRED_FIELDS <= present:
        return False
    if not present <= (LAUNCH_REQUIRED_FIELDS | LAUNCH_OPTIONAL_FIELDS):
        return False
    if payload.get("schema_version") != LAUNCH_SCHEMA_VERSION:
        return False
    command = payload.get("command")
    if not isinstance(command, str) or not command:
        return False
    argv = payload.get("argv")
    if not isinstance(argv, (list, tuple)) or not argv:
        return False
    if not all(isinstance(token, str) for token in argv):
        return False
    if argv[0] != command:
        return False
    if not isinstance(payload.get("agent_id"), str) or not payload["agent_id"]:
        return False
    if not isinstance(payload.get("launch_spec_hash"), str):
        return False
    if not env_projection_shape_is_exact(payload.get("env")):
        return False
    # The same binding the constructor enforces, asked of a document ARS is
    # reading back rather than one it just built.
    policy_id = payload.get("launch_permission_policy_id")
    digest = payload.get("launch_permission_digest")
    if not policy_pair_is_exact(policy_id, digest):
        return False
    names = payload["env"].get("names") or []
    return projection_matches_policy(
        policy_id,
        tuple(
            (item.get("name"), item.get("source"))
            for item in names
            if isinstance(item, dict)
        ),
    )


def _is_exact_int(value: Any) -> bool:
    """An ``int`` and not a ``bool``. ``isinstance(True, int)`` is ``True``."""
    return type(value) is int


def _is_env_name(value: Any) -> bool:
    """The registry's own name grammar, asked of the registry."""
    from agent_run_supervisor.native_acp.agent_registration import is_env_name

    return is_env_name(value)


def _env_name_entry_is_exact(item: Any) -> bool:
    if not isinstance(item, dict) or set(item) != ENV_NAME_FIELDS:
        return False
    if not _is_env_name(item.get("name")):
        return False
    source = item.get("source")
    if source not in ENV_PRECEDENCE:
        return False
    # ``precedence`` is not a free integer: the writer derives it from ``source``
    # through one table, so a record whose pair disagrees was not written here.
    if not _is_exact_int(item.get("precedence")):
        return False
    if item["precedence"] != ENV_PRECEDENCE[source]:
        return False
    # The writer has no path that emits ``False``; every projected value is
    # withheld, so an entry claiming otherwise is describing a different schema.
    return item.get("redacted") is True


def env_projection_shape_is_exact(payload: Any) -> bool:
    """The environment block is a production ``EnvProjection`` or it is not.

    Key sets are the cheap half of this question and the half that does not
    matter. A hybrid keeps every reset key and puts a value-bearing literal in a
    field nothing looked at — ``redaction``, a name's ``source``, the mediation
    id, a ``declared_absent`` entry — and the reset path is precisely the one
    that recomputes and publishes a digest over the whole record.

    So every field is checked against the **closed domain the writer emits**:
    two categorical constants, a bounded count that agrees with ``names``, the
    registered mediation ids, the environment-name grammar, and the exact
    ``source``/``precedence`` relation. Anything else is a value-bearing record
    and goes to the withholding path. This is not compatibility acceptance:
    there is one producer, and it is :meth:`EnvProjection.to_dict`.
    """
    from agent_run_supervisor.native_acp.agent_registration import (
        is_env_passthrough_domain,
    )
    from agent_run_supervisor.native_acp.profile import MEDIATION_BINDING_IDS

    if not isinstance(payload, dict) or set(payload) != ENV_PROJECTION_FIELDS:
        return False
    if payload.get("values_persisted") is not False:
        return False
    if payload.get("redaction") != ENV_REDACTION_MARKER:
        return False

    mediation_id = payload.get("mediation_id")
    if mediation_id is not None and mediation_id not in MEDIATION_BINDING_IDS:
        return False

    names = payload.get("names")
    if not isinstance(names, (list, tuple)):
        return False
    if not all(_env_name_entry_is_exact(item) for item in names):
        return False
    # ``environment_layers`` resolves into a dict and emits ``sorted(resolved)``,
    # so the writer's names are unique and ascending by construction.
    projected = [item["name"] for item in names]
    if projected != sorted(set(projected)):
        return False

    count = payload.get("resolved_count")
    if not _is_exact_int(count) or count < 0 or count != len(projected):
        return False

    # ``declared_absent`` is derived from exactly one thing: the entry's own
    # ``env_passthrough``, filtered to the names the daemon's environment did not
    # hold. A subset of a list the parser admitted is bounded, unique, and
    # grammar-valid by that same rule — so the reader asks the registry's own
    # domain predicate rather than re-deriving a weaker version of it. Grammar
    # alone let a repeated name, or thirty-three of them, reach the digest.
    return is_env_passthrough_domain(payload.get("declared_absent"))


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass
class EffectiveRunState:
    """Observed effective state only; never rewrites a profile, entry, or Spec."""

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


@dataclass
class ObservedRuntime(EffectiveRunState):
    """What was resolved and observed — recorded, never a gate.

    ``authoritative`` is a fixed ``False`` and there is no way to set it: no
    code path compares any field here against a source constant, a prior Run, a
    Session record, or a registry value to decide admission or reuse.
    """

    declared_command: str | None = None
    resolved_argv: tuple[str, ...] = ()
    path_lookup_hit: str | None = None
    mapped_image: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["observed_runtime"] = {
            "authoritative": False,
            "declared_command": self.declared_command,
            "resolved_argv": list(self.resolved_argv),
            "path_lookup_hit": self.path_lookup_hit,
            "mapped_image": self.mapped_image,
        }
        return payload


# ---------------------------------------------------------------------------
# The assembler
# ---------------------------------------------------------------------------


class RunSpecAssembler:
    """Enforces the freeze order for one Run admission."""

    def __init__(self, request: AgentRunRequest) -> None:
        self._request = request
        self._instance: AgentInstance | None = None
        self._binding: WorkspaceBinding | None = None
        self._launch: LaunchSnapshot | None = None
        self._sealed = False

    @property
    def request(self) -> AgentRunRequest:
        return self._request

    @property
    def instance(self) -> AgentInstance | None:
        return self._instance

    @property
    def profile(self) -> AcpCompatProfile | None:
        return None if self._instance is None else self._instance.profile

    def resolve_agent(
        self, entry: AgentEntry, *, registry: ProfileRegistry
    ) -> AgentInstance:
        """Pair the operator entry with its source profile. Zero filesystem access.

        The entry has already been read once, at daemon startup, into an
        immutable snapshot; nothing here re-opens anything.
        """
        validate_agent_id(self._request.agent_id)
        if entry.agent_id != self._request.agent_id:
            raise SpecValidationError(
                "resolved registry entry names a different agent than the request"
            )
        instance = AgentInstance(registry.get(entry.profile_id), entry)
        self._instance = instance
        return instance

    def bind_workspace(self, *, root: Path, cwd: str | None = None) -> WorkspaceBinding:
        self._binding = resolve_workspace_binding(root=root, cwd=cwd)
        return self._binding

    def resolve_launch(
        self,
        *,
        environment: ResolvedEnvironment,
        launch_permission: MaterializedLaunchPermissions | None = None,
    ) -> LaunchSnapshot:
        """Materialize the value-blind launch snapshot from the one resolution."""
        if self._instance is None or self._binding is None:
            raise SpecFreezeOrderError(
                "resolve_launch requires a resolved agent and a bound workspace"
            )
        if not isinstance(environment, ResolvedEnvironment):
            raise SpecValidationError(
                "resolve_launch accepts only a ResolvedEnvironment, resolved once"
            )
        if launch_permission is not None and (
            type(launch_permission) is not MaterializedLaunchPermissions
        ):
            # Typed before it is dereferenced: reaching for ``.policy_id`` on an
            # arbitrary object raises ``AttributeError`` out of a public seam,
            # and naming the object in the refusal would render whatever
            # ``__repr__`` it chose.
            raise SpecValidationError(
                "resolve_launch accepts only the launch permission material "
                "carrier, or None"
            )
        instance = self._instance
        # The resolved **profile** decides whether material exists at all, so a
        # mismatch here is an inconsistency between the layer that selects and
        # the layer that materializes — never something to reconcile silently.
        selected = instance.launch_permission_policy_id
        if launch_permission is None:
            if selected is not None:
                raise SpecValidationError(
                    "the resolved profile selects a launch permission policy, "
                    "so its material is required before the launch is sealed"
                )
        else:
            if selected is None:
                raise SpecValidationError(
                    "launch permission material was supplied for a profile "
                    "that selects no policy"
                )
            if launch_permission.policy_id != selected:
                raise SpecValidationError(
                    "launch permission material does not match the policy the "
                    "resolved profile selected"
                )
        self._launch = LaunchSnapshot(
            command=instance.command,
            argv=instance.argv,
            profile_id=instance.profile.profile_id,
            profile_revision=instance.profile.revision,
            profile_hash=instance.profile.profile_hash(),
            agent_id=instance.agent_id,
            env=environment.value_blind_projection(),
            mediation_id=instance.mediation_id,
            model_selector_id=instance.model_selector_id,
            effort_selector_id=instance.effort_selector_id,
            forbidden_capabilities=instance.forbidden_capabilities,
            credential_refs=self._request.credential_refs,
            session_epoch=instance.session_epoch,
            session_meta=instance.profile.session_meta,
            launch_permission_policy_id=(
                None if launch_permission is None else launch_permission.policy_id
            ),
            launch_permission_digest=(
                None if launch_permission is None else launch_permission.digest
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
        if self._instance is None or self._binding is None or self._launch is None:
            raise SpecFreezeOrderError(
                "seal requires a resolved agent, bound workspace, and resolved launch"
            )
        _require_text(run_id, "run_id")
        _require_text(submitted_at, "submitted_at")
        request = self._request
        instance = self._instance
        spec = AgentRunSpec(
            schema_version=request.schema_version,
            identity=RunIdentity(owner=request.owner, namespace=request.namespace),
            session=SpecSession(
                session_id=request.session_id,
                expected_binding_hash=request.expected_binding_hash,
            ),
            agent=SpecAgent(
                agent_id=instance.agent_id,
                profile_id=instance.profile.profile_id,
                profile_revision=instance.profile.revision,
                profile_snapshot_ref=instance.profile.snapshot_ref(),
                profile_hash=instance.profile.profile_hash(),
                session_epoch=instance.session_epoch,
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

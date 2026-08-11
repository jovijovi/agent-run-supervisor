"""Source-owned ACP compatibility profiles — layer 1 of the four-way boundary.

A profile answers exactly one question: **how do you speak ACP to a class of
agent?** It freezes the protocol major, the required capabilities, a
forbidden-capability floor, session semantics including required real
``session/load``, the selector-id conventions, the base environment allowlist,
permission-mediation semantics, and — only where cited ACP-level evidence
requires it — frozen ACP session metadata plus a required permission-mode
selector proven by readback.

It freezes nothing else. There is no path, version, digest, model literal, agent
name, value domain, launch kind, artifact identity, or deployment fact here,
because none of those is an ACP semantic. **Which command is which agent, here**
is the operator's answer, carried by one registry entry
(:mod:`agent_run_supervisor.native_acp.agent_registration`) that ARS reads once
at daemon startup.

That split is what makes an AGENT or adapter upgrade behind an unchanged
registered command cost no ARS action at all: no identity field anywhere derives
from what the agent turned out to be.

Five profiles are registered. ``standard-native-acp-v1`` is the ACP-v1
conformance contract every standards-conforming agent runs under.
``claude-agent-acp-compat-v1`` exists because one adapter carries a cited
ACP-semantic deviation: it resolves its initial permission mode from ambient
settings through its own settings manager and auto-allows tool calls in process
while that mode is permissive, so the mode is frozen as a config selector and
proven by exact readback before any prompt, and the frozen session metadata
removes the ambient setting sources that would otherwise define the underlying
SDK's permission rules and tool surface. Neither half is sufficient alone.

``codex-agent-acp-compat-v1`` exists because the adapter exposes a
permission-mode selector whose read-only literal differs from the other
profiles. Its ``mode`` is driven by a closed grant policy: ``read-only`` when
the frozen grant is exactly a subset of ``{read, search}``, and ``agent``
otherwise. The mode is proven before the separate model and effort selectors
and re-proven once, after both are configured, at the post-effort readback.
The advertised ``agent-full-access`` literal is evidence only and is never
selected. The adapter's ambient initial mode is not authority because every
Run performs the set and exact readback.

``cursor-native-acp-v1`` exists for another cited ACP-semantic deviation:
an agent whose model selector *is* the whole configuration, with no independent
effort selector to discover or set. That is expressed as a declared
configuration-fidelity mode. Revision 3 adds this profile's second frozen term:
its ``mode`` selector is driven by one closed, source-owned, grant-driven
permission-mode policy — ``ask`` when the Run's frozen grant is exactly a
subset of ``{read, search}``, ``agent`` for every other valid grant — proven by
exact readback before the model and re-proven after it. That selection is a
cooperative mitigation of an agent that can complete an edit in ``agent`` mode
without ever asking; it is not an OS sandbox and not a strong hostile-agent
boundary, and the ACP permission bridge and the post-completion violation
detector remain the enforcement line. Every other frozen term equals the
standard contract.

``reasonix-agent-acp-compat-v1`` carries one evidenced ACP-semantic deviation:
the agent's ambient ``tool_approval`` value can be ``auto`` or ``yolo`` as well
as ``ask``. ARS therefore sets ``ask`` before the model and effort and proves it
twice by exact readback on every Run, including after a real ``session/load``.
No other Reasonix selector is frozen and no agent-name branch exists in the
runtime path.

The ``-v1`` suffix is load-bearing: the id carries the ACP protocol generation,
construction refuses a profile whose frozen protocol disagrees with it, and a
future ``standard-native-acp-v2`` is therefore a separate profile with its own
registry entries and its own Sessions.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from .config_fidelity import (
    EFFORT_NOT_APPLICABLE,
    FIDELITY_MODEL_ONLY,
    FIDELITY_SEPARATE_SELECTORS,
    ConfigFidelityError,
    validate_fidelity_pairing,
)
from .launch_permissions import (
    LAUNCH_PERMISSION_POLICY_IDS,
    RESERVED_LAUNCH_PERMISSION_KEYS,
)


class UnknownProfileError(ValueError):
    """Lookup of a profile or mediation id outside the closed source set."""


class ProfileValidationError(ValueError):
    """A registered profile constant violates its construction-time contract."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_MAX_SESSION_META_LENGTH = 4096

# A profile id may carry the ACP protocol generation it speaks. When it does,
# the generation and the frozen protocol version must agree.
_PROTOCOL_GENERATION_RE = re.compile(r".*-v(\d+)")


# ---------------------------------------------------------------------------
# Mediation authority — source-owned in key and value
# ---------------------------------------------------------------------------

# Mediation environment values route an agent's privileged in-process tool
# families through ACP permission requests, so the permission bridge decides
# *before* a side effect. If configuration could shadow that, the default-deny
# claim would be decorative. Therefore the binding is source-owned in key and
# value, keyed by the capability family it mediates; a registry entry may select
# one id or none, and can never author a pair, a key, or a value.
_MEDIATION_BINDINGS: dict[str, tuple[tuple[str, str], ...]] = {
    "ask-privileged-tool-families-v1": (
        ("OPENCODE_PERMISSION", '{"bash":"ask","edit":"ask","webfetch":"ask"}'),
    ),
}

MEDIATION_BINDINGS: Mapping[str, tuple[tuple[str, str], ...]] = MappingProxyType(
    dict(_MEDIATION_BINDINGS)
)
MEDIATION_BINDING_IDS: frozenset[str] = frozenset(MEDIATION_BINDINGS)


def reserved_mediation_keys(
    bindings: Mapping[str, tuple[tuple[str, str], ...]],
) -> frozenset[str]:
    """The union of every key in **any** binding of ``bindings``.

    Global by construction rather than per-selection: the reserved set must not
    depend on which binding an entry chose, or whether it chose one at all.
    """
    return frozenset(key for pairs in bindings.values() for key, _ in pairs)


RESERVED_MEDIATION_KEYS: frozenset[str] = reserved_mediation_keys(MEDIATION_BINDINGS)


def mediation_pairs(mediation_id: str | None) -> tuple[tuple[str, str], ...]:
    """The source-owned pairs an entry selected, or none when it selected none."""
    if mediation_id is None:
        return ()
    try:
        return MEDIATION_BINDINGS[mediation_id]
    except KeyError:
        raise UnknownProfileError(
            f"unregistered mediation binding: {mediation_id!r}"
        ) from None


# ---------------------------------------------------------------------------
# Grant-driven permission-mode policies — source-owned and closed
# ---------------------------------------------------------------------------

# A profile may freeze a required permission mode as one literal (the Claude
# compat profile does), but for an agent whose safe mode depends on what the
# Run is allowed to do, one literal is wrong in one direction or the other:
# always-``ask`` breaks writable development Runs, always-``agent`` leaves
# read-only Runs one unasked edit from a post-hoc violation. So the profile may
# instead select one **policy** from this closed table, and the Run's frozen
# ``grant_capabilities`` — already sealed, immutable, and caller-authorized —
# become the policy's only input. The policy is source-owned in id and body;
# neither a registry entry nor a caller can author, select, or replace one, and
# generic runtime code asks the profile rather than branching on an agent name.
#
# The two registered policies implement the exact-subset rule and nothing else:
# a grant that is exactly a subset of ``{read, search}`` (including the empty
# grant) requires the selected profile's cooperative read-only mode
# (``ask`` or ``read-only``); every other valid grant requires ``agent``.
# There are no further grant classes. The mode values are the agent's own
# advertised ACP ``mode`` literals, set and read back opaquely by the fidelity
# machine like every other selector value.
_READ_ONLY_GRANT_CAPABILITIES: frozenset[str] = frozenset({"read", "search"})


def _read_only_grant_ask_else_agent(capabilities: Iterable[str]) -> str:
    return (
        "ask"
        if frozenset(capabilities) <= _READ_ONLY_GRANT_CAPABILITIES
        else "agent"
    )


def _read_only_grant_read_only_else_agent(
    capabilities: Iterable[str],
) -> str:
    return (
        "read-only"
        if frozenset(capabilities) <= _READ_ONLY_GRANT_CAPABILITIES
        else "agent"
    )


PERMISSION_MODE_POLICY_READ_ONLY_ASK = "read-only-grant-ask-else-agent-v1"
PERMISSION_MODE_POLICY_READ_ONLY_MODE = (
    "read-only-grant-read-only-else-agent-v1"
)

_PERMISSION_MODE_POLICIES: dict[str, Callable[[Iterable[str]], str]] = {
    PERMISSION_MODE_POLICY_READ_ONLY_ASK: _read_only_grant_ask_else_agent,
    PERMISSION_MODE_POLICY_READ_ONLY_MODE: _read_only_grant_read_only_else_agent,
}

PERMISSION_MODE_POLICY_IDS: frozenset[str] = frozenset(_PERMISSION_MODE_POLICIES)


# ---------------------------------------------------------------------------
# The base environment allowlist
# ---------------------------------------------------------------------------

# Layer 1 of the environment projection: names taken from the daemon's own
# environment, only when present, values unchanged. A filtered environment is
# not the interactive environment, so this covers the ordinary interactive
# essentials and the operator declares the rest per agent.
#
# ``SSH_AUTH_SOCK`` is deliberately absent. Forwarding it hands the AGENT live
# use of the operator's SSH keys — a real authority transfer, and therefore an
# explicit per-agent ``env_passthrough`` opt-in rather than a default.
BASE_ENV_ALLOWLIST: tuple[str, ...] = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "TZ",
    "TERM",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
    "http_proxy",
    "https_proxy",
    "ftp_proxy",
    "all_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "FTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)


def base_env_allowlist(profile: "AcpCompatProfile") -> tuple[str, ...]:
    """The layer-1 names this profile projects. One accessor, one authority."""
    return profile.base_allowlist


# ---------------------------------------------------------------------------
# AcpCompatProfile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcpCompatProfile:
    """How to speak ACP to a class of agent, and nothing else."""

    profile_id: str
    revision: int
    acp_protocol_version: str
    required_capabilities: tuple[str, ...] = ("loadSession",)
    # A floor, not a ceiling: a registry entry must forbid at least this and may
    # forbid more. Empty means no capability has been observed to warrant it.
    forbidden_capabilities: tuple[str, ...] = ()
    requires_session_load: bool = True
    base_allowlist: tuple[str, ...] = BASE_ENV_ALLOWLIST
    # How this class of agent is configured: two independent selectors, or a
    # model selector that is the whole configuration. See ``config_fidelity``.
    config_fidelity_mode: str = FIDELITY_SEPARATE_SELECTORS
    # Selector-id *conventions*, not value domains. An entry may hint a
    # different id; the live-advertised option set is the domain authority and
    # exact readback is the proof. ``effort_selector_id`` is ``None`` exactly
    # when the mode is model-only: naming a selector no Run ever sets would put
    # a fiction into every launch snapshot.
    model_selector_id: str = "model"
    effort_selector_id: str | None = "effort"
    # A selector is declared with exactly one mode authority, or not at all: a
    # selector with no required mode proves nothing, a required mode with no
    # selector cannot be set, and two authorities for one selector could
    # disagree. ``required_permission_mode`` freezes one literal;
    # ``permission_mode_policy_id`` selects one closed, source-owned policy
    # that computes the per-Run required mode from the Run's frozen grant
    # capabilities. The grant is only ever an input — the profile never learns
    # from it.
    permission_mode_selector_id: str | None = None
    required_permission_mode: str | None = None
    permission_mode_policy_id: str | None = None
    # An optional, closed, source-owned launch-permission policy id. Absent by
    # default. When present, ARS compiles that policy from the Run's frozen
    # grant and hands the child a private per-Run configuration *before* the
    # process starts, so an agent that would otherwise complete a side effect
    # without asking refuses it itself. It is a permission-mediation semantic,
    # so it belongs here — and it is an **id**, never a path, a document, or a
    # deployment fact.
    launch_permission_policy_id: str | None = None
    # Frozen ACP session metadata, stored as canonical JSON **text** so the
    # value is deeply immutable by construction and hashes canonically. It is
    # sent identically on ``session/new`` and ``session/load``: the agent
    # rebuilds its underlying query from the load request, so omitting it there
    # would silently restore ambient setting sources on every reused Session.
    # There is no caller metadata surface anywhere; this is the only source.
    session_meta: str | None = None

    def __post_init__(self) -> None:
        if not self.profile_id or not isinstance(self.profile_id, str):
            raise ProfileValidationError("profile_id must be a non-empty string")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise ProfileValidationError("revision must be an integer")
        if not self.acp_protocol_version.isdigit():
            raise ProfileValidationError("acp_protocol_version must be decimal text")
        overlap = set(self.required_capabilities) & set(self.forbidden_capabilities)
        if overlap:
            raise ProfileValidationError(
                f"capability declared both required and forbidden: {sorted(overlap)}"
            )
        self._validate_config_fidelity()
        self._validate_launch_permissions()
        self._validate_protocol_generation()
        self._validate_permission_mode()
        self._validate_session_meta()
        self._validate_mediation_disjointness()

    def _validate_config_fidelity(self) -> None:
        """The mode and the selector set are one declaration, not two.

        Asked of ``config_fidelity`` so the profile layer and the state machine
        cannot drift into two readings. The requested effort is not a profile
        fact, so the sentinel stands in for it here; the per-Run check happens
        where the request exists.
        """
        try:
            validate_fidelity_pairing(
                fidelity_mode=self.config_fidelity_mode,
                effort_selector_id=self.effort_selector_id,
                requested_effort=EFFORT_NOT_APPLICABLE,
            )
        except ConfigFidelityError as exc:
            raise ProfileValidationError(str(exc)) from exc
        if self.model_selector_id == self.effort_selector_id:
            raise ProfileValidationError("the two selector ids must be distinct")

    def _validate_launch_permissions(self) -> None:
        """The selected policy is one of the closed set, and owns its own key.

        A base-allowlist name that is also a policy's environment key would let
        an ambient value and a source-owned pair claim the same variable — the
        same rule the mediation binding already obeys.
        """
        policy_id = self.launch_permission_policy_id
        if policy_id is None:
            return
        # Judged before the lookup and before any formatting: an unhashable id
        # would raise out of a public constructor, a ``str`` subclass can lie
        # about hash and equality, and ``!r`` on a hostile object would invite
        # its own ``__repr__`` into the refusal. Categorical text only.
        if type(policy_id) is not str:
            raise ProfileValidationError(
                "launch_permission_policy_id must be a str when it is present"
            )
        if policy_id not in LAUNCH_PERMISSION_POLICY_IDS:
            raise ProfileValidationError(
                f"unregistered launch permission policy: {policy_id!r}"
            )
        collisions = sorted(
            (set(self.base_allowlist) | set(RESERVED_MEDIATION_KEYS))
            & RESERVED_LAUNCH_PERMISSION_KEYS
        )
        if collisions:
            raise ProfileValidationError(
                "base allowlist or mediation binding collides with reserved "
                f"launch permission keys: {collisions}"
            )

    def _validate_protocol_generation(self) -> None:
        """A versioned profile id carries its ACP major, exactly.

        The id, not the revision, carries the protocol generation, so a revision
        bump cannot turn a v1 contract into a v2 one and a future v2 is a
        different id — hence different registry entries and a different Session
        domain. Isolation is structural rather than a rule to remember.
        """
        match = _PROTOCOL_GENERATION_RE.fullmatch(self.profile_id)
        if match is None:
            return
        expected = match.group(1)
        if self.acp_protocol_version != expected:
            raise ProfileValidationError(
                f"profile id {self.profile_id!r} declares ACP protocol generation "
                f"{expected!r} but freezes {self.acp_protocol_version!r}"
            )

    def _validate_permission_mode(self) -> None:
        selector = self.permission_mode_selector_id
        required = self.required_permission_mode
        policy_id = self.permission_mode_policy_id
        if policy_id is not None:
            # Judged before the lookup and before any formatting, exactly like
            # the launch-permission policy id: categorical refusals only.
            if type(policy_id) is not str:
                raise ProfileValidationError(
                    "permission_mode_policy_id must be a str when it is present"
                )
            if policy_id not in PERMISSION_MODE_POLICY_IDS:
                raise ProfileValidationError(
                    f"unregistered permission mode policy: {policy_id!r}"
                )
        declared = (required is not None) + (policy_id is not None)
        if selector is None:
            if declared:
                raise ProfileValidationError(
                    "a required permission mode or permission-mode policy must "
                    "be declared together with permission_mode_selector_id"
                )
            return
        if declared != 1:
            raise ProfileValidationError(
                "permission_mode_selector_id must be declared with exactly one "
                "of required_permission_mode or permission_mode_policy_id"
            )
        for name, value in (
            ("permission_mode_selector_id", selector),
            ("required_permission_mode", required),
        ):
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                raise ProfileValidationError(f"{name} must be a non-empty string")
            if not all(ch.isprintable() for ch in value):
                raise ProfileValidationError(f"{name} contains non-printable characters")

    def _validate_session_meta(self) -> None:
        value = self.session_meta
        if value is None:
            return
        if not isinstance(value, str) or not value:
            raise ProfileValidationError("session_meta must be a non-empty string")
        if len(value) > _MAX_SESSION_META_LENGTH:
            raise ProfileValidationError(
                f"session_meta exceeds {_MAX_SESSION_META_LENGTH} characters"
            )
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise ProfileValidationError("session_meta must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ProfileValidationError("session_meta must be a JSON object")
        if _canonical_json(parsed) != value:
            raise ProfileValidationError(
                "session_meta must equal its canonical re-serialization byte for byte"
            )
        for key in parsed:
            if not isinstance(key, str) or not key:
                raise ProfileValidationError(
                    "session_meta top-level keys must be non-empty strings"
                )

    def _validate_mediation_disjointness(self) -> None:
        """One environment key never has two owners.

        A base-allowlist name that is also a reserved mediation key would let an
        ambient value and a source-owned pair claim the same variable. Asserted
        at construction so the two tables cannot drift apart silently.
        """
        collisions = sorted(set(self.base_allowlist) & RESERVED_MEDIATION_KEYS)
        if collisions:
            raise ProfileValidationError(
                f"base allowlist collides with reserved mediation keys: {collisions}"
            )

    # -- required permission mode ------------------------------------------

    def required_permission_mode_for(
        self, grant_capabilities: Iterable[str]
    ) -> str | None:
        """The mode this Run must prove, or ``None`` for a modeless profile.

        One answer for both declaration forms, so no caller learns which kind
        of profile it holds: a static profile answers its frozen literal for
        every grant, a policy profile computes from the Run's frozen grant, and
        a profile with no selector answers ``None``. Asked once per machine
        construction, which is what makes the required mode a recomputed
        per-Run fact rather than remembered state.
        """
        if self.permission_mode_selector_id is None:
            return None
        if self.required_permission_mode is not None:
            return self.required_permission_mode
        # Construction admitted exactly one authority, so the policy id is
        # present and registered here.
        assert self.permission_mode_policy_id is not None
        if isinstance(grant_capabilities, (str, bytes)):
            # ``str`` is an iterable of characters: text here would quietly
            # compute from letters and land on the permissive answer. Refuse
            # toward zero prompt instead.
            raise ConfigFidelityError(
                "grant_capabilities must be an iterable of capability tokens, "
                "not text"
            )
        policy = _PERMISSION_MODE_POLICIES[self.permission_mode_policy_id]
        return policy(grant_capabilities)

    # -- frozen session metadata ------------------------------------------

    def session_meta_payload(self) -> dict[str, Any] | None:
        """A fresh deep copy of the frozen metadata, or ``None``.

        Parsed per call from the canonical text, so no caller, Run, or test can
        mutate shared nested state between sessions.
        """
        if self.session_meta is None:
            return None
        return json.loads(self.session_meta)

    def session_meta_for(self, call: str) -> dict[str, Any] | None:
        """The ``_meta`` argument for ``session/new`` or ``session/load``.

        One accessor for both calls, because they must carry the identical
        frozen metadata and a second code path is how they would stop.
        """
        if call not in ("new", "load"):
            raise UnknownProfileError(f"unknown session call: {call!r}")
        return self.session_meta_payload()

    # -- identity ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "profile_id": self.profile_id,
            "revision": self.revision,
            "acp_protocol_version": self.acp_protocol_version,
            "required_capabilities": list(self.required_capabilities),
            "forbidden_capabilities": list(self.forbidden_capabilities),
            "requires_session_load": self.requires_session_load,
            "base_allowlist": list(self.base_allowlist),
            "model_selector_id": self.model_selector_id,
            "effort_selector_id": self.effort_selector_id,
        }
        # Emitted only when it deviates, exactly like the two optional terms
        # below: a profile whose ACP semantics did not move must keep the
        # ``profile_hash`` it already has, because that hash is Session
        # identity.
        if self.config_fidelity_mode != FIDELITY_SEPARATE_SELECTORS:
            payload["config_fidelity_mode"] = self.config_fidelity_mode
        if self.launch_permission_policy_id is not None:
            payload["launch_permission_policy_id"] = self.launch_permission_policy_id
        if self.permission_mode_selector_id is not None:
            payload["permission_mode_selector_id"] = self.permission_mode_selector_id
            if self.required_permission_mode is not None:
                payload["required_permission_mode"] = self.required_permission_mode
            if self.permission_mode_policy_id is not None:
                payload["permission_mode_policy_id"] = self.permission_mode_policy_id
        if self.session_meta is not None:
            payload["session_meta"] = json.loads(self.session_meta)
        return payload

    def snapshot_ref(self) -> str:
        return f"registry:{self.profile_id}@r{self.revision}"

    def profile_hash(self) -> str:
        return _sha256_hex(_canonical_json(self.snapshot()))


# ---------------------------------------------------------------------------
# AgentInstance — one profile paired with one operator registry entry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentInstance:
    """The seam every generic consumer asks: this profile, as this agent.

    Downstream code asks the *pair* for a fact and never asks which agent it is
    holding. The accessors are a closed, named set rather than a generic
    ``getattr`` bridge, so a fact that varies per agent has to be added here on
    purpose — which is what keeps an entry from quietly acquiring authority over
    something source still owns.
    """

    profile: AcpCompatProfile
    entry: Any

    def __post_init__(self) -> None:
        if self.entry is None:
            raise ProfileValidationError(
                f"profile {self.profile.profile_id} runs only as a registered agent"
            )
        if getattr(self.entry, "profile_id", None) != self.profile.profile_id:
            raise ProfileValidationError(
                "registry entry names a different profile than the one resolving it"
            )
        if (
            self.profile.config_fidelity_mode == FIDELITY_MODEL_ONLY
            and getattr(self.entry, "effort_selector_id", None) is not None
        ):
            raise ProfileValidationError(
                "a model-only profile sets no effort selector, so an entry "
                "hint for one would name a call that never happens"
            )

    # -- identity ----------------------------------------------------------

    @property
    def agent_id(self) -> str:
        return self.entry.agent_id

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    @property
    def session_epoch(self) -> int | None:
        """Operator-controlled continuity, or ``None``. Never derived.

        No code path anywhere increments or infers this: only an operator's edit
        changes it, and ``None`` is not ``1``.
        """
        return self.entry.session_epoch

    # -- launch ------------------------------------------------------------

    @property
    def command(self) -> str:
        return self.entry.command

    @property
    def argv(self) -> tuple[str, ...]:
        """``argv[0]`` is the declared command string, byte for byte."""
        return self.entry.argv()

    @property
    def permission_env(self) -> tuple[tuple[str, str], ...]:
        return mediation_pairs(self.entry.mediation_id)

    @property
    def mediation_id(self) -> str | None:
        return self.entry.mediation_id

    @property
    def env_passthrough(self) -> tuple[str, ...]:
        return self.entry.env_passthrough

    @property
    def env_overlay(self) -> tuple[tuple[str, str], ...]:
        return self.entry.env_overlay

    @property
    def base_allowlist(self) -> tuple[str, ...]:
        return self.profile.base_allowlist

    # -- ACP contract ------------------------------------------------------

    @property
    def acp_protocol_version(self) -> str:
        return self.profile.acp_protocol_version

    @property
    def required_capabilities(self) -> tuple[str, ...]:
        return self.profile.required_capabilities

    @property
    def forbidden_capabilities(self) -> tuple[str, ...]:
        """The profile floor raised by the entry's own narrowing."""
        return tuple(
            sorted(
                set(self.profile.forbidden_capabilities)
                | set(self.entry.forbidden_capabilities)
            )
        )

    # -- configuration -----------------------------------------------------

    @property
    def config_fidelity_mode(self) -> str:
        return self.profile.config_fidelity_mode

    @property
    def launch_permission_policy_id(self) -> str | None:
        """The profile's selection, asked of the pair. Never an agent name."""
        return self.profile.launch_permission_policy_id

    @property
    def model_selector_id(self) -> str:
        return self.entry.model_selector_id or self.profile.model_selector_id

    @property
    def effort_selector_id(self) -> str | None:
        """``None`` under model-only fidelity, and only there."""
        if self.profile.config_fidelity_mode == FIDELITY_MODEL_ONLY:
            return None
        return self.entry.effort_selector_id or self.profile.effort_selector_id


# ---------------------------------------------------------------------------
# The closed source registry
# ---------------------------------------------------------------------------


class ProfileRegistry:
    """Closed set of code-registered profiles; unknown IDs are errors."""

    def __init__(self, profiles: Iterable[AcpCompatProfile]) -> None:
        registered: dict[str, AcpCompatProfile] = {}
        for profile in profiles:
            if profile.profile_id in registered:
                raise ValueError(f"duplicate profile id: {profile.profile_id!r}")
            registered[profile.profile_id] = profile
        self._profiles = registered

    def get(self, profile_id: Any) -> AcpCompatProfile:
        try:
            return self._profiles[profile_id]
        except (KeyError, TypeError):
            raise UnknownProfileError(f"unknown profile id: {profile_id!r}") from None

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


# The ACP-v1 conformance contract. It freezes conformance and nothing else: no
# real agent's identity, capability, or selector constant appears here, because
# every one of those is an operator fact carried by a registry entry.
STANDARD_NATIVE_ACP_V1 = AcpCompatProfile(
    profile_id="standard-native-acp-v1",
    revision=1,
    acp_protocol_version="1",
    required_capabilities=("loadSession",),
    forbidden_capabilities=(),
    requires_session_load=True,
)

# The one profile whose *ACP behavior itself* deviates, with cited evidence.
#
# ``settingSources: []`` removes the adapter default so no ambient user, project
# or local settings file can define the underlying SDK's permission rules or
# tool surface; the tools preset pins the built-in tool set explicitly. That is
# the rule-source half of the defense. The frozen ``mode`` selector, proven by
# exact readback before any prompt, is the other half — the adapter resolves its
# initial mode through its own settings manager, a surface the frozen metadata
# does not govern, and auto-allows tool calls in process while that mode is
# permissive. Neither half alone is sufficient, so both are frozen here.
CLAUDE_AGENT_ACP_COMPAT_V1 = AcpCompatProfile(
    profile_id="claude-agent-acp-compat-v1",
    revision=1,
    acp_protocol_version="1",
    required_capabilities=("loadSession",),
    forbidden_capabilities=(),
    requires_session_load=True,
    permission_mode_selector_id="mode",
    required_permission_mode="default",
    session_meta=(
        '{"claudeCode":{"options":{"settingSources":[],'
        '"tools":{"preset":"claude_code","type":"preset"}}}}'
    ),
)

# The Codex compatibility profile keeps every standard ACP-v1 term, including
# separate model and effort selectors, and freezes one evidenced deviation.
#
# Cited ACP-level evidence: the adapter advertises a select option named
# ``mode`` with exact literals ``read-only``, ``agent``, and
# ``agent-full-access``; a zero-prompt exchange set and exactly read back both
# ``read-only`` and ``agent``. The adapter's initial mode is ambient and may
# be ``agent``, so initial selection alone cannot make a Run correct.
#
# Every Run therefore derives and proves its mode from its frozen grant before
# model and effort: exactly the subsets of ``{read, search}`` require
# ``read-only``, every other valid grant requires ``agent``, and
# ``agent-full-access`` is never a policy output. This is a cooperative
# adapter-side mode, not a sandbox or a replacement for mediation.
CODEX_AGENT_ACP_COMPAT_V1 = AcpCompatProfile(
    profile_id="codex-agent-acp-compat-v1",
    revision=1,
    acp_protocol_version="1",
    required_capabilities=("loadSession",),
    forbidden_capabilities=(),
    requires_session_load=True,
    permission_mode_selector_id="mode",
    permission_mode_policy_id=PERMISSION_MODE_POLICY_READ_ONLY_MODE,
)

# The one profile whose *configuration fidelity* deviates, with cited evidence.
#
# The agent advertises a single model selector whose value carries the whole
# configuration — the observed literal is ``grok-4.5[effort=high,fast=true]`` —
# and advertises no independent effort selector at all. ARS therefore sets and
# exact-reads-back that one opaque literal and reports ``N/A`` as the effective
# effort. It does not parse the literal, infer an effort from it, map a model
# name, or read the agent's unrelated ACP ``mode`` selector as an effort. Every
# other frozen term equals ``standard-native-acp-v1``, and configuration
# fidelity is this profile's **only** deviation.
#
# Revision 2 removed the launch-permission policy revision 1 selected. That
# backend's environment key is the agent's *whole* configuration root, not a
# permission-file override, so pointing it at per-Run material relocated the
# agent's own Session state into the Run directory and deleted it with the Run.
# Cited ACP-level evidence: Run 1 opened the external Session and prompted, and
# Run 2 — a new process, a new empty root — reached a real ``session/load`` that
# had no configured Session to answer with and failed pre-dispatch with
# ``CONFIG_FIDELITY``. That breaks GOAL contract 3 and PRD R4 continuity and
# crosses the PRD R8 boundary against managing AGENT state, and no permission-only
# injection surface exists that ARS is permitted to write. Enforcement stays with
# the ACP ``PermissionBridge``, the frozen-grant default-deny mediation, the
# post-completion violation detector, and the mandatory per-agent denied-action
# canary. See ``launch_permissions`` for the constraint on any future selection.
#
# Revision 3 freezes the grant-driven ``mode`` selection. Cited ACP-level
# evidence: in its ``agent`` mode this agent can complete an edit without ever
# emitting ``session/request_permission``, so on a read-only grant the
# permission bridge decides nothing and the violation is detected only after
# the file exists; in its advertised ``ask`` mode the agent itself asks before
# acting. The required mode is therefore computed per Run by the one closed
# grant-driven policy above — ``ask`` when the frozen grant is exactly a subset
# of ``{read, search}``, ``agent`` for every other valid grant — set before the
# model, proven by exact readback, and re-proven after the model set, exactly
# like the compat profile's frozen literal. This is a **cooperative temporary
# mitigation**: it is not an OS sandbox, not a strong hostile-agent boundary,
# and not a launch-permission replacement, and the enforcement line named above
# is unchanged. Moving the mode into profile semantics moved this profile's
# hash — a deliberate identity change for existing revision-2 Sessions.
CURSOR_NATIVE_ACP_V1 = AcpCompatProfile(
    profile_id="cursor-native-acp-v1",
    revision=3,
    acp_protocol_version="1",
    required_capabilities=("loadSession",),
    forbidden_capabilities=(),
    requires_session_load=True,
    config_fidelity_mode=FIDELITY_MODEL_ONLY,
    effort_selector_id=None,
    permission_mode_selector_id="mode",
    permission_mode_policy_id=PERMISSION_MODE_POLICY_READ_ONLY_ASK,
)

# Reasonix's sole compatibility deviation is configuration semantic rather
# than launch or deployment policy: its ``tool_approval`` selector admits
# values that do not ask the ACP Host before applicable tools run. Freeze the
# evidenced static ``ask`` value through the existing exact-readback sequence.
# Model and effort retain the standard selector conventions; ``work_mode`` is
# deliberately outside this narrow profile.
REASONIX_AGENT_ACP_COMPAT_V1 = AcpCompatProfile(
    profile_id="reasonix-agent-acp-compat-v1",
    revision=1,
    acp_protocol_version="1",
    required_capabilities=("loadSession",),
    forbidden_capabilities=(),
    requires_session_load=True,
    permission_mode_selector_id="tool_approval",
    required_permission_mode="ask",
)

DEFAULT_REGISTRY = ProfileRegistry(
    (
        STANDARD_NATIVE_ACP_V1,
        CLAUDE_AGENT_ACP_COMPAT_V1,
        CODEX_AGENT_ACP_COMPAT_V1,
        CURSOR_NATIVE_ACP_V1,
        REASONIX_AGENT_ACP_COMPAT_V1,
    )
)

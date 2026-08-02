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

Two profiles are registered. ``standard-native-acp-v1`` is the ACP-v1
conformance contract every standards-conforming agent runs under.
``claude-agent-acp-compat-v1`` exists because one adapter carries a cited
ACP-semantic deviation: it resolves its initial permission mode from ambient
settings through its own settings manager and auto-allows tool calls in process
while that mode is permissive, so the mode is frozen as a config selector and
proven by exact readback before any prompt, and the frozen session metadata
removes the ambient setting sources that would otherwise define the underlying
SDK's permission rules and tool surface. Neither half is sufficient alone.

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
from typing import Any, Iterable, Mapping


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
    # Selector-id *conventions*, not value domains. An entry may hint a
    # different id; the live-advertised option set is the domain authority and
    # exact readback is the proof.
    model_selector_id: str = "model"
    effort_selector_id: str = "effort"
    # Declared together or not at all: a selector with no required literal
    # proves nothing, and a required literal with no selector cannot be set.
    permission_mode_selector_id: str | None = None
    required_permission_mode: str | None = None
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
        if self.model_selector_id == self.effort_selector_id:
            raise ProfileValidationError("the two selector ids must be distinct")
        self._validate_protocol_generation()
        self._validate_permission_mode()
        self._validate_session_meta()
        self._validate_mediation_disjointness()

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
        if (selector is None) != (required is None):
            raise ProfileValidationError(
                "permission_mode_selector_id and required_permission_mode must "
                "be declared together"
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
        if self.permission_mode_selector_id is not None:
            payload["permission_mode_selector_id"] = self.permission_mode_selector_id
            payload["required_permission_mode"] = self.required_permission_mode
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
    def model_selector_id(self) -> str:
        return self.entry.model_selector_id or self.profile.model_selector_id

    @property
    def effort_selector_id(self) -> str:
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

DEFAULT_REGISTRY = ProfileRegistry((STANDARD_NATIVE_ACP_V1, CLAUDE_AGENT_ACP_COMPAT_V1))

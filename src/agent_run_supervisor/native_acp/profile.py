"""Typed, versioned, closed AgentProfile registry (PRD R12).

Profiles are code-registered constants: fixed executable reference (resolved
only through the operator-managed registered installation mapping — no
caller or environment path override), fixed argv template with registered
substitutions only, credential/env slot *names* (never values), typed config
selectors, and capability flags. No command/argv/env/JSON passthrough
surface exists.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .attestation import ExpectedRuntimeIdentity


class UnknownProfileError(ValueError):
    """Lookup of a profile or executable key outside the closed registry."""


class ProfileValidationError(ValueError):
    """A registered profile constant violates its construction-time contract."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Operator-managed registered installation mapping. Resolution never consults
# caller input, PATH, or any environment variable.
#
# ``codex-acp`` maps to the controller-frozen Node interpreter, not to the
# adapter's ``.bin`` shim: the adapter entrypoint is an ESM script whose
# ``#!/usr/bin/env node`` shebang would otherwise let the kernel resolve the
# interpreter from the child's ambient PATH. The process image is Node and the
# entry JS is argv[1], so interpreter selection never involves PATH at all.
_REGISTERED_EXECUTABLES: dict[str, Path] = {
    "opencode": Path("/home/linuxbrew/.linuxbrew/bin/opencode"),
    "codex-acp": Path(
        "/home/ecs-user/.local/share/agent-run-supervisor/adapters/node/v24.14.0/bin/node"
    ),
}

# Profile-owned frozen non-model launch environment (D2). The key set is closed
# per slice; values are bounded printable non-secret strings and never
# credential material.
_FIXED_ENV_ALLOWED_KEYS = frozenset(
    {"CODEX_HOME", "CODEX_PATH", "CODEX_CONFIG", "INITIAL_AGENT_MODE", "NO_BROWSER"}
)
_INITIAL_AGENT_MODES = frozenset({"read-only", "agent", "agent-full-access"})
_MAX_FIXED_ENV_VALUE_LENGTH = 512
# The attestation checks depend on both: a missing CODEX_PATH would silently
# switch the adapter to a PATH-resolved or bundled fallback CLI, changing
# downstream identity without any hash change.
_IDENTITY_REQUIRED_FIXED_ENV = ("CODEX_HOME", "CODEX_PATH")

# Registered agent-side permission mediation binding, keyed like the
# installation mapping and injected only at spawn by the supervisor — never
# caller input and never a credential value. OpenCode's default build agent
# permission base is "*": allow, so without this binding its in-process
# write/edit tools complete with zero client mediation (the A4-S2 blocker).
# Forcing edit/bash/webfetch to "ask" routes every privileged tool family
# through session/request_permission, making the frozen-grant
# PermissionBridge the deciding authority before any side effect.
_REGISTERED_PERMISSION_ENV: dict[str, tuple[tuple[str, str], ...]] = {
    "opencode": (
        ("OPENCODE_PERMISSION", '{"bash":"ask","edit":"ask","webfetch":"ask"}'),
    ),
}


def resolve_registered_executable(key: str) -> Path:
    try:
        return _REGISTERED_EXECUTABLES[key]
    except KeyError:
        raise UnknownProfileError(f"unregistered executable key: {key!r}") from None


def resolve_registered_permission_env(key: str) -> tuple[tuple[str, str], ...]:
    """Registered permission-mediation env pairs for an executable key;
    executables without a registered binding launch with none."""
    return _REGISTERED_PERMISSION_ENV.get(key, ())


@dataclass(frozen=True)
class AgentProfile:
    profile_id: str
    revision: int
    executable_key: str
    argv_template: tuple[str, ...]
    env_allowlist: tuple[str, ...]
    credential_slots: tuple[str, ...]
    model_selector_id: str
    effort_selector_id: str
    default_model: str
    default_effort: str
    # Closed, registered value domains for the typed config selectors: a
    # request outside them is refused at admission (live advertisement checks
    # then gate the run itself).
    registered_models: tuple[str, ...]
    allowed_efforts: tuple[str, ...]
    requires_session_load: bool
    config_schema: Mapping[str, Any]
    # Profile-owned frozen launch environment, deeply immutable and injected
    # only at spawn (D2). Empty for profiles that own no fixed environment.
    fixed_env: tuple[tuple[str, str], ...] = ()
    # Frozen runtime identity verified at the spawn boundary (D3/D10). ``None``
    # means no identity check, no attestation artifact, and no migration.
    expected_runtime: ExpectedRuntimeIdentity | None = None
    # Exact caller credential references this profile admits (D11). ``None``
    # means unconstrained, preserving legacy behavior.
    required_credential_refs: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        self._validate_fixed_env()
        if self.required_credential_refs is not None:
            for ref in self.required_credential_refs:
                if not isinstance(ref, str) or not ref:
                    raise ProfileValidationError(
                        "required_credential_refs entries must be non-empty strings"
                    )

    def _validate_fixed_env(self) -> None:
        seen: set[str] = set()
        for pair in self.fixed_env:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ProfileValidationError("fixed_env entries must be (name, value)")
            name, value = pair
            if name in seen:
                raise ProfileValidationError(f"duplicate fixed_env key: {name!r}")
            seen.add(name)
            if name not in _FIXED_ENV_ALLOWED_KEYS:
                raise ProfileValidationError(f"fixed_env key {name!r} is not registered")
            if not isinstance(value, str) or not value:
                raise ProfileValidationError(
                    f"fixed_env value for {name!r} must be a non-empty string"
                )
            if len(value) > _MAX_FIXED_ENV_VALUE_LENGTH:
                raise ProfileValidationError(
                    f"fixed_env value for {name!r} exceeds "
                    f"{_MAX_FIXED_ENV_VALUE_LENGTH} characters"
                )
            if not all(ch.isprintable() for ch in value):
                raise ProfileValidationError(
                    f"fixed_env value for {name!r} contains non-printable characters"
                )
            if name == "CODEX_CONFIG":
                try:
                    parsed = json.loads(value)
                except ValueError as exc:
                    raise ProfileValidationError(
                        "fixed_env CODEX_CONFIG must be valid JSON"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ProfileValidationError(
                        "fixed_env CODEX_CONFIG must be a JSON object"
                    )
                if _canonical_json(parsed) != value:
                    raise ProfileValidationError(
                        "fixed_env CODEX_CONFIG must equal its canonical "
                        "re-serialization byte for byte"
                    )
            if name == "INITIAL_AGENT_MODE" and value not in _INITIAL_AGENT_MODES:
                raise ProfileValidationError(
                    f"fixed_env INITIAL_AGENT_MODE {value!r} is not registered"
                )
        if self.expected_runtime is not None:
            for required in _IDENTITY_REQUIRED_FIXED_ENV:
                if required not in seen:
                    raise ProfileValidationError(
                        f"expected_runtime requires fixed_env {required}"
                    )

    def snapshot(self) -> dict[str, Any]:
        # Additive fields are omit-when-empty/None so legacy rows keep
        # byte-identical snapshots, profile hashes, and launch hashes.
        payload: dict[str, Any] = {
            "profile_id": self.profile_id,
            "revision": self.revision,
            "executable_key": self.executable_key,
            "argv_template": list(self.argv_template),
            "env_allowlist": list(self.env_allowlist),
            "credential_slots": list(self.credential_slots),
            "model_selector_id": self.model_selector_id,
            "effort_selector_id": self.effort_selector_id,
            "default_model": self.default_model,
            "default_effort": self.default_effort,
            "registered_models": list(self.registered_models),
            "allowed_efforts": list(self.allowed_efforts),
            "requires_session_load": self.requires_session_load,
            "config_schema": dict(self.config_schema),
        }
        if self.fixed_env:
            payload["fixed_env"] = [list(pair) for pair in self.fixed_env]
        if self.expected_runtime is not None:
            payload["expected_runtime"] = self.expected_runtime.to_dict()
        if self.required_credential_refs is not None:
            payload["required_credential_refs"] = list(self.required_credential_refs)
        return payload

    def snapshot_ref(self) -> str:
        return f"registry:{self.profile_id}@r{self.revision}"

    def profile_hash(self) -> str:
        return _sha256_hex(_canonical_json(self.snapshot()))

    def config_schema_hash(self) -> str:
        return _sha256_hex(_canonical_json(dict(self.config_schema)))


class ProfileRegistry:
    """Closed set of code-registered profiles; unknown IDs are errors."""

    def __init__(self, profiles: Iterable[AgentProfile]) -> None:
        registered: dict[str, AgentProfile] = {}
        for profile in profiles:
            if profile.profile_id in registered:
                raise ValueError(f"duplicate profile id: {profile.profile_id!r}")
            registered[profile.profile_id] = profile
        self._profiles = registered

    def get(self, profile_id: str) -> AgentProfile:
        try:
            return self._profiles[profile_id]
        except KeyError:
            raise UnknownProfileError(f"unknown profile id: {profile_id!r}") from None

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


# Revision 2 (chair-approved C10 decision): the effort selector on real
# OpenCode 1.18.4 is model-dependent, so the registered closed model pair is
# k3 plus deepseek/deepseek-v4-pro — the configured-provider text/code model
# whose live post-set-model option set advertises literal efforts high|max.
OPENCODE_1_18_4 = AgentProfile(
    profile_id="opencode-1.18.4",
    revision=2,
    executable_key="opencode",
    argv_template=("acp",),
    env_allowlist=(
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    ),
    credential_slots=("kimi-for-coding", "deepseek"),
    model_selector_id="model",
    effort_selector_id="effort",
    default_model="kimi-for-coding/k3",
    default_effort="max",
    registered_models=("kimi-for-coding/k3", "deepseek/deepseek-v4-pro"),
    allowed_efforts=("low", "medium", "high", "max"),
    requires_session_load=True,
    config_schema={
        "schema_version": 2,
        "selectors": {
            "model": {
                "config_id": "model",
                "type": "string",
                "domain": ["kimi-for-coding/k3", "deepseek/deepseek-v4-pro"],
            },
            "effort": {
                "config_id": "effort",
                "type": "string",
                "domain": ["low", "medium", "high", "max"],
            },
        },
    },
)

# Revision 1: the official Codex ACP adapter 1.1.7 over downstream Codex CLI
# 0.145.0, admitted as a closed profile whose every value is a byte-copy of the
# operator-frozen install/discovery/runtime manifests.
#
# Landlock is load-bearing, version-bound debt: the canonical
# ``CODEX_CONFIG`` below pins ``use_legacy_landlock`` because the adapter's
# default bwrap sandbox was disqualified at discovery — the command reached the
# Codex sandbox without ACP permission mediation and failed with a bwrap
# loopback ``RTM_NEWADDR`` EPERM on this host. Any adapter, downstream CLI,
# Node, or ``CODEX_CONFIG`` change requires a full new install → discovery →
# permission-canary cycle, a profile revision bump, and review; there is no
# silent fallback.
#
# Operational consequence of the D11a project-config closure: Codex workspaces
# must live under an ancestor chain free of ``.codex/config.toml``, because any
# such layer is a configuration surface this profile did not freeze and is
# refused at every spawn boundary.
CODEX_ADAPTER_ENTRY = (
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/codex-acp/1.1.7"
    "/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
)

CODEX_ACP_1_1_7 = AgentProfile(
    profile_id="codex-acp-1.1.7",
    revision=1,
    executable_key="codex-acp",
    argv_template=(CODEX_ADAPTER_ENTRY,),
    env_allowlist=(
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    ),
    fixed_env=(
        ("CODEX_HOME", "/home/ecs-user/.config/agent-run-supervisor/codex-acp-1.1.7"),
        ("CODEX_PATH", "/home/ecs-user/.local/bin/codex"),
        ("CODEX_CONFIG", '{"features":{"use_legacy_landlock":true}}'),
        ("INITIAL_AGENT_MODE", "read-only"),
        ("NO_BROWSER", "1"),
    ),
    credential_slots=("codex-home-auth",),
    required_credential_refs=("codex-home-auth",),
    model_selector_id="model",
    effort_selector_id="reasoning_effort",
    default_model="gpt-5.6-sol",
    default_effort="max",
    registered_models=("gpt-5.6-sol",),
    allowed_efforts=("max",),
    requires_session_load=True,
    config_schema={
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
    },
    expected_runtime=ExpectedRuntimeIdentity(
        node_path=(
            "/home/ecs-user/.local/share/agent-run-supervisor/adapters/node"
            "/v24.14.0/bin/node"
        ),
        node_sha256="e237a2839d0cbdc9a9a2adda1a184afc0f5b20306ffbe923af5686550472d8a8",
        adapter_entry_path=CODEX_ADAPTER_ENTRY,
        adapter_entry_sha256=(
            "0deb6b820dfed8804cd76b16a50210fe12202e5e339b5edaa23f6987f1742e0a"
        ),
        cli_path="/home/ecs-user/.local/bin/codex",
        cli_sha256="a2a05dafaa1acb002a45eaec0a462de5b13694fcfcd7bc43305f14781ce7be14",
        agent_info_name="@agentclientprotocol/codex-acp",
        agent_info_version="1.1.7",
        protocol_version="1",
    ),
)

DEFAULT_REGISTRY = ProfileRegistry((OPENCODE_1_18_4, CODEX_ACP_1_1_7))

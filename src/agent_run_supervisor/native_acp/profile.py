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


class UnknownProfileError(ValueError):
    """Lookup of a profile or executable key outside the closed registry."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Operator-managed registered installation mapping. Resolution never consults
# caller input, PATH, or any environment variable.
_REGISTERED_EXECUTABLES: dict[str, Path] = {
    "opencode": Path("/home/linuxbrew/.linuxbrew/bin/opencode"),
}

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

    def snapshot(self) -> dict[str, Any]:
        return {
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

DEFAULT_REGISTRY = ProfileRegistry((OPENCODE_1_18_4,))

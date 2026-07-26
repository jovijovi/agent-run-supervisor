"""Typed, versioned, closed AgentProfile registry (PRD R12/R13).

Profiles are code-registered constants: fixed executable reference (resolved
only through the operator-managed registered installation mapping — no
caller or environment path override), fixed argv template with registered
substitutions only, credential/env slot *names* (never values), typed config
selectors, and capability flags. No command/argv/env/JSON passthrough
surface exists.

Each profile also carries its source-frozen :class:`AdapterContract` — layer 1
of the three runtime-authority layers (PRD R13). The contract freezes
compatibility semantics and the *shape* of the Binding it accepts; it never
freezes a deployment-specific downstream CLI path, version, or digest. Those
are layer-2 operator facts read once per Run through
:mod:`agent_run_supervisor.native_acp.runtime_binding`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


class UnknownProfileError(ValueError):
    """Lookup of a profile or executable key outside the closed registry."""


class ProfileValidationError(ValueError):
    """A registered profile constant violates its construction-time contract."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


# Operator-managed registered installation mapping. Resolution never consults
# caller input, PATH, or any environment variable.
#
# ``codex-acp`` and ``claude-agent-acp`` map to the controller-frozen Node
# interpreter, not to the adapters' ``.bin`` shims: each adapter entrypoint is
# an ESM script whose ``#!/usr/bin/env node`` shebang would otherwise let the
# kernel resolve the interpreter from the child's ambient PATH. The process
# image is Node and the entry JS is argv[1], so interpreter selection never
# involves PATH at all.
#
# A ``direct_acp`` profile has no entry here at all: its one executable is both
# the AGENT CLI and the ACP implementation, so it is a deployment fact the
# operator-owned Binding supplies through the contract's executable slot.
_FROZEN_NODE = Path(
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/node/v24.14.0/bin/node"
)
_REGISTERED_EXECUTABLES: dict[str, Path] = {
    "codex-acp": _FROZEN_NODE,
    "claude-agent-acp": _FROZEN_NODE,
}

# Profile-owned frozen non-model launch environment (D2). The key set is closed
# per slice; values are bounded printable non-secret strings and never
# credential material.
_FIXED_ENV_ALLOWED_KEYS = frozenset(
    {
        "CODEX_HOME",
        "CODEX_PATH",
        "CODEX_CONFIG",
        "CLAUDE_CODE_EXECUTABLE",
        "INITIAL_AGENT_MODE",
        "NO_BROWSER",
    }
)
_INITIAL_AGENT_MODES = frozenset({"read-only", "agent", "agent-full-access"})
_MAX_FIXED_ENV_VALUE_LENGTH = 512
_MAX_SESSION_META_LENGTH = 4096

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


# ---------------------------------------------------------------------------
# AdapterContract — layer 1 of PRD R13
# ---------------------------------------------------------------------------

LAUNCH_KIND_WRAPPED = "wrapped_acp"
LAUNCH_KIND_DIRECT = "direct_acp"
LAUNCH_KINDS = (LAUNCH_KIND_WRAPPED, LAUNCH_KIND_DIRECT)

SLOT_KIND_NATIVE_BINARY = "native_binary"
SLOT_KIND_PACKAGE_TREE = "package_tree"
SLOT_KIND_CONFIG_ROOT = "config_root"
SLOT_KINDS = (SLOT_KIND_NATIVE_BINARY, SLOT_KIND_PACKAGE_TREE, SLOT_KIND_CONFIG_ROOT)

ARTIFACT_SLOT_KINDS = (SLOT_KIND_NATIVE_BINARY, SLOT_KIND_PACKAGE_TREE)

# The exact descriptor field set a Binding slot of each kind must carry — no
# more, no less. Declared here because it is contract shape, not operator data.
# ``package_tree`` deliberately requires the tree digest *and* the interpreter
# identity beside the launcher: a launcher hash alone never freezes the sibling
# code the launcher loads (C5).
# ``native_binary`` carries the same pair for the same reason: a script or a
# dynamically linked image is executed by an interpreter or loader that lives
# outside the hashed file, so its identity is frozen beside the file's own
# digest. Only an image that needs no external interpreter may leave both null.
SLOT_DESCRIPTOR_FIELDS: dict[str, tuple[str, ...]] = {
    SLOT_KIND_NATIVE_BINARY: (
        "path",
        "version",
        "sha256",
        "interpreter",
        "interpreter_sha256",
    ),
    SLOT_KIND_PACKAGE_TREE: (
        "package_root",
        "tree_sha256",
        "launcher_path",
        "launcher_sha256",
        "interpreter_path",
        "interpreter_sha256",
        "version",
    ),
    SLOT_KIND_CONFIG_ROOT: ("path",),
}

_SEMVER_RE = re.compile(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)\b")


def _parse_first_semver(text: str) -> str | None:
    match = _SEMVER_RE.search(text)
    return match.group(1) if match is not None else None


# Closed set of code-owned probe output parsers. A Binding names nothing here:
# the contract picks the parser, and the operator can never supply one.
VERSION_PROBE_PARSERS: dict[str, Callable[[str], str | None]] = {
    "first_semver": _parse_first_semver,
}


@dataclass(frozen=True)
class VersionProbeRule:
    """The only sanctioned way to learn an external CLI's real version (C6).

    A fixed non-prompt argv suffix, a hermetic environment, bounded output and
    timeout, and a code-owned parser. The rule is contract data; running it is
    an operator-command action, never an admission-path action.
    """

    argv_suffix: tuple[str, ...]
    parser_id: str = "first_semver"
    timeout_seconds: float = 15.0
    max_output_bytes: int = 8192

    def __post_init__(self) -> None:
        if not self.argv_suffix:
            raise ProfileValidationError("version probe requires a fixed argv suffix")
        for token in self.argv_suffix:
            if not isinstance(token, str) or not token or not token.startswith("-"):
                raise ProfileValidationError(
                    "version probe argv suffix accepts option tokens only"
                )
        if self.parser_id not in VERSION_PROBE_PARSERS:
            raise ProfileValidationError(
                f"version probe parser {self.parser_id!r} is not code-owned"
            )
        if not (0 < self.timeout_seconds <= 60):
            raise ProfileValidationError("version probe timeout must be in (0, 60]")
        if not (256 <= self.max_output_bytes <= 1 << 20):
            raise ProfileValidationError("version probe output bound is out of range")

    def parse(self, text: str) -> str | None:
        return VERSION_PROBE_PARSERS[self.parser_id](text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv_suffix": list(self.argv_suffix),
            "parser_id": self.parser_id,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
        }


@dataclass(frozen=True)
class BindingSlot:
    """One slot the contract accepts a Binding value into (C2).

    A slot declares a name, a kind, and — at most — the code-known env key the
    projected value fills. It never declares the value itself, and a Binding
    can never introduce a slot or an env key the contract has not declared.
    """

    name: str
    kind: str
    env_key: str | None = None
    provides_executable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.isidentifier():
            raise ProfileValidationError("binding slot name must be an identifier")
        if self.kind not in SLOT_KINDS:
            raise ProfileValidationError(f"unknown binding slot kind: {self.kind!r}")
        if self.env_key is not None and self.env_key not in _FIXED_ENV_ALLOWED_KEYS:
            raise ProfileValidationError(
                f"binding slot env key {self.env_key!r} is not a code-known key"
            )
        if self.provides_executable and self.kind != SLOT_KIND_NATIVE_BINARY:
            raise ProfileValidationError(
                "only a native_binary slot may provide the executable"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "env_key": self.env_key,
            "provides_executable": self.provides_executable,
            # C1 freezes the *accepted Binding schema*, not only the slot's
            # name and kind, so the descriptor field set rides in the contract
            # hash: widening or narrowing what a generation may declare is a
            # contract change and fails prior generations closed (C3).
            "descriptor_fields": list(SLOT_DESCRIPTOR_FIELDS[self.kind]),
        }


@dataclass(frozen=True)
class WrappedRuntimeArtifacts:
    """Source-frozen interpreter and ACP adapter identity for ``wrapped_acp``.

    These stay in code (C9): the interpreter and the adapter entry are ARS-
    controlled artifacts, not deployment facts an operator re-points.
    """

    interpreter_path: str
    interpreter_sha256: str
    adapter_entry_path: str
    adapter_entry_sha256: str

    def __post_init__(self) -> None:
        for name in ("interpreter_path", "adapter_entry_path"):
            value = getattr(self, name)
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ProfileValidationError(f"{name} must be an absolute path")
        for name in ("interpreter_sha256", "adapter_entry_sha256"):
            value = getattr(self, name)
            if not _is_sha256_hex(value):
                raise ProfileValidationError(f"{name} must be a sha256 hex digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "interpreter_path": self.interpreter_path,
            "interpreter_sha256": self.interpreter_sha256,
            "adapter_entry_path": self.adapter_entry_path,
            "adapter_entry_sha256": self.adapter_entry_sha256,
        }


@dataclass(frozen=True)
class AdapterContract:
    """The source-frozen adapter compatibility contract (PRD R13, C1).

    It freezes ``launch_kind``, the accepted Binding schema and slot
    projection, the ACP protocol/name plus required *and forbidden*
    capabilities, the wrapped interpreter/adapter artifact identity, and the
    code-owned version-probe rule. It freezes no downstream CLI path, version,
    digest, or config-root value: those are Binding facts.
    """

    launch_kind: str
    acp_agent_name: str
    acp_protocol_version: str
    version_probe: VersionProbeRule
    # The ACP-reported agent version, asserted only where it is itself a
    # *source* artifact fact — a wrapped adapter whose entry digest this
    # contract already freezes. ``None`` for ``direct_acp``, where
    # ``agentInfo.version`` reports the deployed executable: freezing it would
    # re-freeze a deployment fact, and it may never be asserted equal to a CLI
    # ``--version``.
    acp_agent_version: str | None = None
    binding_slots: tuple[BindingSlot, ...] = ()
    required_capabilities: tuple[str, ...] = ("loadSession",)
    forbidden_capabilities: tuple[str, ...] = ()
    wrapped_runtime: WrappedRuntimeArtifacts | None = None
    # Slot names that carry, respectively, the downstream CLI artifact and an
    # ARS-managed credential root. Both are slot *names*, never values.
    cli_slot: str | None = None
    credential_root_slot: str | None = None
    # Workspace project-config surface whose ancestor chain must stay clean.
    project_config_relpath: str | None = None

    def __post_init__(self) -> None:
        if self.launch_kind not in LAUNCH_KINDS:
            raise ProfileValidationError(f"unknown launch kind: {self.launch_kind!r}")
        if not self.acp_agent_name:
            raise ProfileValidationError("contract requires an ACP agent name")
        if not self.acp_protocol_version.isdigit():
            raise ProfileValidationError("acp_protocol_version must be decimal text")
        names = [slot.name for slot in self.binding_slots]
        if len(names) != len(set(names)):
            raise ProfileValidationError("duplicate binding slot name")
        overlap = set(self.required_capabilities) & set(self.forbidden_capabilities)
        if overlap:
            raise ProfileValidationError(
                f"capability declared both required and forbidden: {sorted(overlap)}"
            )
        artifact_slots = [
            slot for slot in self.binding_slots if slot.kind in ARTIFACT_SLOT_KINDS
        ]
        if len(artifact_slots) > 1:
            raise ProfileValidationError(
                "a contract declares at most one external CLI artifact slot"
            )
        if self.cli_slot is not None and self.cli_slot not in names:
            raise ProfileValidationError(f"cli_slot {self.cli_slot!r} is not declared")
        if self.credential_root_slot is not None:
            if self.credential_root_slot not in names:
                raise ProfileValidationError(
                    f"credential_root_slot {self.credential_root_slot!r} is not declared"
                )
            if self.slot(self.credential_root_slot).kind != SLOT_KIND_CONFIG_ROOT:
                raise ProfileValidationError(
                    "credential_root_slot must be a config_root slot"
                )
        if self.launch_kind == LAUNCH_KIND_DIRECT and self.acp_agent_version is not None:
            raise ProfileValidationError(
                "a direct_acp contract must not freeze the deployed agent version"
            )
        if self.launch_kind == LAUNCH_KIND_WRAPPED:
            if self.wrapped_runtime is None:
                raise ProfileValidationError(
                    "a wrapped_acp contract must freeze its interpreter and adapter"
                )
            if artifact_slots and artifact_slots[0].kind != SLOT_KIND_PACKAGE_TREE:
                raise ProfileValidationError(
                    "a wrapped downstream CLI binds as a package_tree closure"
                )
            if artifact_slots and artifact_slots[0].env_key is None:
                raise ProfileValidationError(
                    "a wrapped downstream CLI slot must fill a code-known env key"
                )
        else:
            if self.wrapped_runtime is not None:
                raise ProfileValidationError(
                    "a direct_acp contract freezes no wrapped interpreter/adapter"
                )
            if artifact_slots and not artifact_slots[0].provides_executable:
                raise ProfileValidationError(
                    "a direct_acp artifact slot must provide the executable"
                )

    @property
    def requires_binding(self) -> bool:
        return bool(self.binding_slots)

    def slot(self, name: str) -> BindingSlot:
        for slot in self.binding_slots:
            if slot.name == name:
                return slot
        raise UnknownProfileError(f"unknown binding slot: {name!r}")

    def executable_slot(self) -> BindingSlot | None:
        for slot in self.binding_slots:
            if slot.provides_executable:
                return slot
        return None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "launch_kind": self.launch_kind,
            "acp_agent_name": self.acp_agent_name,
            "acp_protocol_version": self.acp_protocol_version,
            "acp_agent_version": self.acp_agent_version,
            "version_probe": self.version_probe.to_dict(),
            "binding_slots": [slot.to_dict() for slot in self.binding_slots],
            "required_capabilities": list(self.required_capabilities),
            "forbidden_capabilities": list(self.forbidden_capabilities),
            "cli_slot": self.cli_slot,
            "credential_root_slot": self.credential_root_slot,
            "project_config_relpath": self.project_config_relpath,
        }
        if self.wrapped_runtime is not None:
            payload["wrapped_runtime"] = self.wrapped_runtime.to_dict()
        return payload


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
    # The source-frozen adapter compatibility contract (PRD R13 layer 1). It
    # owns launch kind, accepted Binding shape, ACP identity/capabilities, the
    # wrapped interpreter/adapter artifacts, and the version-probe rule.
    contract: AdapterContract
    # Profile-owned frozen launch environment, deeply immutable and injected
    # only at spawn (D2). Empty for profiles that own no fixed environment.
    # Deployment-specific values never live here: a Binding slot fills the
    # code-known env key its contract declared.
    fixed_env: tuple[tuple[str, str], ...] = ()
    # Exact caller credential references this profile admits (D11). ``None``
    # means unconstrained, preserving legacy behavior.
    required_credential_refs: tuple[str, ...] | None = None
    # Profile-frozen ACP permission mode (B4): the config selector id and the
    # exact literal every Run must prove by readback before it may prompt.
    # ``None``/``None`` means the profile freezes no permission mode and the
    # legacy two-selector fidelity sequence applies unchanged.
    permission_mode_selector_id: str | None = None
    required_permission_mode: str | None = None
    # Profile-owned frozen ACP session metadata (B5): the exact ``_meta``
    # argument sent on ``session/new`` *and* ``session/load``, stored as
    # canonical JSON **text** so the value is deeply immutable by construction
    # and hashes canonically. ``None`` means the profile owns no metadata and
    # the argument is omitted entirely (legacy wire frames stay byte-identical).
    # There is no caller metadata surface anywhere: this is the only source.
    session_meta: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.contract, AdapterContract):
            raise ProfileValidationError("profile requires an AdapterContract")
        self._validate_permission_mode()
        self._validate_session_meta()
        self._validate_fixed_env()
        self._validate_contract_binding()
        if self.required_credential_refs is not None:
            for ref in self.required_credential_refs:
                if not isinstance(ref, str) or not ref:
                    raise ProfileValidationError(
                        "required_credential_refs entries must be non-empty strings"
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
                raise ProfileValidationError(
                    f"{name} contains non-printable characters"
                )

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
                "session_meta must equal its canonical re-serialization byte "
                "for byte"
            )
        for key in parsed:
            if not isinstance(key, str) or not key:
                raise ProfileValidationError(
                    "session_meta top-level keys must be non-empty strings"
                )

    def session_meta_payload(self) -> dict[str, Any] | None:
        """A fresh deep copy of the frozen metadata, or ``None``.

        Parsed per call from the canonical text, so no caller, Run, or test can
        mutate shared nested state between sessions.
        """
        if self.session_meta is None:
            return None
        return json.loads(self.session_meta)

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
    def _validate_contract_binding(self) -> None:
        """A Binding-filled env key is never also a source-frozen constant.

        The contract declares which code-known env keys a Binding slot fills;
        freezing the same key in ``fixed_env`` would give one launch variable
        two authorities, and the source constant would silently win.
        """
        seen = {name for name, _ in self.fixed_env}
        for slot in self.contract.binding_slots:
            if slot.env_key is not None and slot.env_key in seen:
                raise ProfileValidationError(
                    f"fixed_env {slot.env_key!r} collides with binding slot "
                    f"{slot.name!r}"
                )
        if (
            self.contract.launch_kind == LAUNCH_KIND_DIRECT
            and self.contract.requires_binding
            and self.contract.executable_slot() is None
        ):
            raise ProfileValidationError(
                "a direct_acp profile must bind its executable through a slot"
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
        if self.required_credential_refs is not None:
            payload["required_credential_refs"] = list(self.required_credential_refs)
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

    def config_schema_hash(self) -> str:
        return _sha256_hex(_canonical_json(dict(self.config_schema)))

    def contract_snapshot(self) -> dict[str, Any]:
        """Everything the AdapterContract freezes, in one canonical projection.

        The profile snapshot is included wholesale because argv construction,
        code-known env keys, selectors, domains, permission mode, and frozen
        session metadata are all contract semantics (C1). Profile ID and
        revision ride along, so a revision bump necessarily changes the
        contract hash and fails every prior Binding generation closed (C3).
        """
        return {"profile": self.snapshot(), "contract": self.contract.to_dict()}

    def adapter_contract_hash(self) -> str:
        return _sha256_hex(_canonical_json(self.contract_snapshot()))


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


# Revision 3 (C15): the stable ID ``opencode-native-acp`` replaces the retired
# ``opencode-1.18.4`` with **no** compatibility alias — the old ID is simply an
# unknown profile and is refused at admission. The version left the ID because
# the executable's version is a Binding fact, not a source constant.
#
# Every identity/capability/selector constant below is a byte-copy of the
# operator-run zero-prompt ACP discovery against the installed executable:
# ``agentInfo`` OpenCode/1.18.5, protocol 1, ``loadSession`` advertised,
# selectors ``model``/``effort``, and — after the exact model was set to
# kimi-for-coding/k3 — the model-dependent effort domain low|high|max. The ACP
# ``agentInfo.version`` and the CLI ``--version`` stay independent facts; the
# CLI version is proven only by the contract's own probe at validate/promote,
# and this profile asserts no equality between the two.
#
# Only the model whose model-dependent effort domain the discovery actually
# observed is registered. ``deepseek/deepseek-v4-pro`` was registered at r2
# under the retired 1.18.4 evidence; re-registering it here would freeze a
# selector domain this discovery does not prove, so it stays out until its own
# discovery exists.
OPENCODE_NATIVE_ACP = AgentProfile(
    profile_id="opencode-native-acp",
    revision=3,
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
    credential_slots=("kimi-for-coding",),
    model_selector_id="model",
    effort_selector_id="effort",
    default_model="kimi-for-coding/k3",
    default_effort="max",
    registered_models=("kimi-for-coding/k3",),
    allowed_efforts=("low", "high", "max"),
    requires_session_load=True,
    config_schema={
        "schema_version": 3,
        "selectors": {
            "model": {
                "config_id": "model",
                "type": "string",
                "domain": ["kimi-for-coding/k3"],
            },
            "effort": {
                "config_id": "effort",
                "type": "string",
                "domain": ["low", "high", "max"],
            },
        },
    },
    contract=AdapterContract(
        launch_kind=LAUNCH_KIND_DIRECT,
        acp_agent_name="OpenCode",
        acp_protocol_version="1",
        version_probe=VersionProbeRule(argv_suffix=("--version",)),
        binding_slots=(
            BindingSlot(
                name="agent_cli",
                kind=SLOT_KIND_NATIVE_BINARY,
                provides_executable=True,
            ),
        ),
        required_capabilities=("loadSession",),
        # One executable is both the AGENT CLI and the ACP implementation, so a
        # generation that also claimed an adapter-side capability would be
        # describing a runtime this contract does not implement.
        forbidden_capabilities=("terminal",),
        cli_slot="agent_cli",
    ),
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
#
# Revision 2 (PR-B): the downstream Codex CLI path/version/digest and the
# ``CODEX_HOME`` credential-root value left these constants for the
# operator-owned Binding. Source keeps exactly what C9 assigns to it — the
# frozen Node interpreter and the ACP adapter entry — plus the frozen
# ``CODEX_CONFIG``/mode/browser environment, which are compatibility semantics
# rather than deployment facts.
CODEX_ADAPTER_ENTRY = (
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/codex-acp/1.1.7"
    "/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
)

CODEX_ACP_1_1_7 = AgentProfile(
    profile_id="codex-acp-1.1.7",
    revision=2,
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
    contract=AdapterContract(
        launch_kind=LAUNCH_KIND_WRAPPED,
        acp_agent_name="@agentclientprotocol/codex-acp",
        acp_protocol_version="1",
        acp_agent_version="1.1.7",
        version_probe=VersionProbeRule(argv_suffix=("--version",)),
        binding_slots=(
            BindingSlot(
                name="downstream_cli",
                kind=SLOT_KIND_PACKAGE_TREE,
                env_key="CODEX_PATH",
            ),
            BindingSlot(
                name="codex_home",
                kind=SLOT_KIND_CONFIG_ROOT,
                env_key="CODEX_HOME",
            ),
        ),
        required_capabilities=("loadSession",),
        # Population awaits this adapter's own capability discovery: nothing is
        # forbidden that an operator-run initialize exchange has not observed.
        forbidden_capabilities=(),
        wrapped_runtime=WrappedRuntimeArtifacts(
            interpreter_path=str(_FROZEN_NODE),
            interpreter_sha256=(
                "e237a2839d0cbdc9a9a2adda1a184afc0f5b20306ffbe923af5686550472d8a8"
            ),
            adapter_entry_path=CODEX_ADAPTER_ENTRY,
            adapter_entry_sha256=(
                "0deb6b820dfed8804cd76b16a50210fe12202e5e339b5edaa23f6987f1742e0a"
            ),
        ),
        cli_slot="downstream_cli",
        credential_root_slot="codex_home",
        project_config_relpath=".codex/config.toml",
    ),
)

# Revision 1: the official Claude ACP adapter 0.61.0 over the operator-installed
# downstream Claude CLI, admitted as a closed profile whose every value is a
# byte-copy of the operator-frozen discovery manifest.
#
# ACP Opus alias distinction: ``claude-opus-5[1m]`` is the *direct Claude CLI*
# author selector and is deliberately absent from the registered domain. A live
# ACP ``session/set_config_option(model)`` on this adapter reads back
# ``opus[1m]``, so the CLI-side string could never satisfy exact readback.
#
# The downstream CLI is bound only through profile-owned ``CLAUDE_CODE_EXECUTABLE``
# (the adapter's own resolution order checks that variable first and otherwise
# falls back to a bundled/PATH-resolved CLI). This profile freezes no ARS-managed
# credential root: the Claude CLI owns its own credential storage, which ARS
# neither manages, stages, nor inspects — so admission requires exactly zero
# caller credential references.
CLAUDE_ADAPTER_ENTRY = (
    "/home/ecs-user/.local/share/agent-run-supervisor/adapters/claude-agent-acp/0.61.0"
    "/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js"
)

# Revision 2 (PR-B): the downstream Claude CLI path/version/digest left these
# constants for the operator-owned Binding. ``CLAUDE_CODE_EXECUTABLE`` is still
# the only binding key the adapter honours, but the *value* is now projected
# from the contract-declared ``downstream_cli`` slot instead of being frozen
# here — so the profile still forbids a PATH-resolved or bundled fallback CLI
# while the deployment fact belongs to the operator.
CLAUDE_AGENT_ACP_0_61_0 = AgentProfile(
    profile_id="claude-agent-acp-0.61.0",
    revision=2,
    executable_key="claude-agent-acp",
    argv_template=(CLAUDE_ADAPTER_ENTRY,),
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
    fixed_env=(("NO_BROWSER", "1"),),
    credential_slots=(),
    required_credential_refs=(),
    model_selector_id="model",
    effort_selector_id="effort",
    default_model="opus[1m]",
    default_effort="max",
    registered_models=("claude-fable-5[1m]", "opus[1m]"),
    allowed_efforts=("max",),
    requires_session_load=True,
    # The adapter resolves its INITIAL permission mode from ambient Claude
    # settings through its own settings manager — a surface the frozen session
    # metadata's ``settingSources: []`` does not govern — and auto-allows tool
    # calls in-process while that mode is ``bypassPermissions``. The mode is
    # therefore frozen as a config selector and proven by readback before any
    # prompt, so the frozen grant is always the deciding authority.
    permission_mode_selector_id="mode",
    required_permission_mode="default",
    # Frozen ACP session metadata sent on session/new AND session/load.
    # ``settingSources: []`` removes the adapter default
    # ``["user","project","local"]`` so no ambient user/project/local settings
    # file can define the underlying SDK's permission rules or tool surface;
    # the tools preset pins the built-in tool set explicitly. This is the
    # rule-source half of the B4/B5 defense — the frozen ``mode`` selector is
    # the other half, and neither alone is sufficient.
    session_meta=(
        '{"claudeCode":{"options":{"settingSources":[],'
        '"tools":{"preset":"claude_code","type":"preset"}}}}'
    ),
    config_schema={
        "schema_version": 1,
        "selectors": {
            "model": {
                "config_id": "model",
                "type": "string",
                "domain": ["claude-fable-5[1m]", "opus[1m]"],
            },
            "effort": {
                "config_id": "effort",
                "type": "string",
                "domain": ["max"],
            },
            "permission_mode": {
                "config_id": "mode",
                "type": "string",
                "domain": ["default"],
            },
        },
    },
    contract=AdapterContract(
        launch_kind=LAUNCH_KIND_WRAPPED,
        acp_agent_name="@agentclientprotocol/claude-agent-acp",
        acp_protocol_version="1",
        acp_agent_version="0.61.0",
        version_probe=VersionProbeRule(argv_suffix=("--version",)),
        binding_slots=(
            BindingSlot(
                name="downstream_cli",
                kind=SLOT_KIND_PACKAGE_TREE,
                env_key="CLAUDE_CODE_EXECUTABLE",
            ),
        ),
        required_capabilities=("loadSession",),
        # Population awaits this adapter's own capability discovery.
        forbidden_capabilities=(),
        wrapped_runtime=WrappedRuntimeArtifacts(
            interpreter_path=str(_FROZEN_NODE),
            interpreter_sha256=(
                "e237a2839d0cbdc9a9a2adda1a184afc0f5b20306ffbe923af5686550472d8a8"
            ),
            adapter_entry_path=CLAUDE_ADAPTER_ENTRY,
            adapter_entry_sha256=(
                "260aac90bf75f197b93640087c1de66441761d43c2784efa035fdcee60b5dacd"
            ),
        ),
        cli_slot="downstream_cli",
        # The Claude CLI owns its own credential storage, which ARS neither
        # manages, stages, nor inspects — so no credential-root slot exists.
        credential_root_slot=None,
        project_config_relpath=None,
    ),
)

DEFAULT_REGISTRY = ProfileRegistry(
    (OPENCODE_NATIVE_ACP, CODEX_ACP_1_1_7, CLAUDE_AGENT_ACP_0_61_0)
)

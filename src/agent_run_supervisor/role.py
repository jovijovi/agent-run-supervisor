from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

SCHEMA_VERSION = 1

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


class RoleValidationError(ValueError):
    """Raised when an AgentRoleSpec mapping fails current validation."""


@dataclass(frozen=True)
class AgentRunnerSpec:
    type: str
    acpx_version: str
    acpx_binary: str | None
    adapter_agent: str
    model: str | None


@dataclass(frozen=True)
class AgentWorkspaceSpec:
    default_cwd: str
    allowed_roots: tuple[str, ...]
    allowed_roots_security_boundary: bool


@dataclass(frozen=True)
class AgentPermissionSpec:
    read: bool
    search: bool
    write: bool
    execute: bool
    terminal: bool
    delete: bool
    move: bool
    fetch: bool
    switch_mode: bool
    other: bool


@dataclass(frozen=True)
class AgentSessionSpec:
    strategy: str


@dataclass(frozen=True)
class AgentRunLimits:
    timeout_seconds: int
    max_turns: int
    max_output_bytes: int


@dataclass(frozen=True)
class AgentPromptSpec:
    role_instruction: str
    output_contract: str


@dataclass(frozen=True)
class AgentRedactionSpec:
    suppress_reads: bool = True
    redact_prompt: bool = True
    redact_stderr: bool = True
    redact_metadata: bool = True
    redact_env: bool = True


@dataclass(frozen=True)
class AgentRoleSpec:
    schema_version: int
    role_id: str
    display_name: str
    description: str
    runner: AgentRunnerSpec
    workspace: AgentWorkspaceSpec
    permissions: AgentPermissionSpec
    session: AgentSessionSpec
    limits: AgentRunLimits
    prompt: AgentPromptSpec
    redaction: AgentRedactionSpec = field(default_factory=AgentRedactionSpec)


def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise RoleValidationError(f"{where}: missing required key {key!r}")
    return mapping[key]


def _require_str(mapping: Mapping[str, Any], key: str, where: str) -> str:
    value = _require(mapping, key, where)
    if not isinstance(value, str) or not value:
        raise RoleValidationError(f"{where}: {key!r} must be a non-empty string")
    return value


def _require_bool(mapping: Mapping[str, Any], key: str, where: str) -> bool:
    value = _require(mapping, key, where)
    if not isinstance(value, bool):
        raise RoleValidationError(f"{where}: {key!r} must be a boolean")
    return value


def _require_positive_int(mapping: Mapping[str, Any], key: str, where: str) -> int:
    value = _require(mapping, key, where)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RoleValidationError(f"{where}: {key!r} must be a positive integer")
    return value


def _runner(raw: Mapping[str, Any]) -> AgentRunnerSpec:
    where = "runner"
    if not isinstance(raw, Mapping):
        raise RoleValidationError(f"{where}: must be a mapping")
    runner_type = _require_str(raw, "type", where)
    if runner_type != "acpx":
        raise RoleValidationError(f"{where}: type must be 'acpx' (got {runner_type!r})")
    acpx_version = _require_str(raw, "acpx_version", where)
    if acpx_version != "0.10.0":
        raise RoleValidationError(
            f"{where}: acpx_version must be '0.10.0' for the current acpx contract (got {acpx_version!r})",
        )
    adapter_agent = _require_str(raw, "adapter_agent", where)
    acpx_binary = raw.get("acpx_binary")
    if acpx_binary is not None and (not isinstance(acpx_binary, str) or not acpx_binary):
        raise RoleValidationError(f"{where}: acpx_binary must be null or non-empty string")
    model = raw.get("model")
    if model is not None and (not isinstance(model, str) or not model):
        raise RoleValidationError(f"{where}: model must be null or non-empty string")
    return AgentRunnerSpec(
        type=runner_type,
        acpx_version=acpx_version,
        acpx_binary=acpx_binary,
        adapter_agent=adapter_agent,
        model=model,
    )


def _workspace(raw: Mapping[str, Any]) -> AgentWorkspaceSpec:
    where = "workspace"
    if not isinstance(raw, Mapping):
        raise RoleValidationError(f"{where}: must be a mapping")
    default_cwd = _require_str(raw, "default_cwd", where)
    allowed_roots_raw = _require(raw, "allowed_roots", where)
    if not isinstance(allowed_roots_raw, list) or not allowed_roots_raw:
        raise RoleValidationError(f"{where}: allowed_roots must be a non-empty list")
    if any(not isinstance(item, str) or not item for item in allowed_roots_raw):
        raise RoleValidationError(f"{where}: allowed_roots entries must be non-empty strings")
    boundary = raw.get("allowed_roots_security_boundary", False)
    if not isinstance(boundary, bool):
        raise RoleValidationError(
            f"{where}: allowed_roots_security_boundary must be a boolean",
        )
    if boundary:
        raise RoleValidationError(
            f"{where}: allowed_roots_security_boundary=true is rejected — "
            "allowed_roots is not a security boundary.",
        )
    return AgentWorkspaceSpec(
        default_cwd=default_cwd,
        allowed_roots=tuple(allowed_roots_raw),
        allowed_roots_security_boundary=False,
    )


def _permissions(raw: Mapping[str, Any]) -> AgentPermissionSpec:
    where = "permissions"
    if not isinstance(raw, Mapping):
        raise RoleValidationError(f"{where}: must be a mapping")
    unknown = set(raw.keys()) - set(PERMISSION_KINDS)
    if unknown:
        raise RoleValidationError(
            f"{where}: unknown permission kinds: {sorted(unknown)}",
        )
    values = {kind: _require_bool(raw, kind, where) for kind in PERMISSION_KINDS}
    return AgentPermissionSpec(**values)


def _session(raw: Mapping[str, Any] | None) -> AgentSessionSpec:
    where = "session"
    if raw is None:
        return AgentSessionSpec(strategy="exec")
    if not isinstance(raw, Mapping):
        raise RoleValidationError(f"{where}: must be a mapping")
    strategy = _require_str(raw, "strategy", where)
    if strategy != "exec":
        raise RoleValidationError(
            f"{where}: current role schema only accepts strategy='exec' until persistent sessions are implemented (got {strategy!r})",
        )
    return AgentSessionSpec(strategy=strategy)


def _limits(raw: Mapping[str, Any]) -> AgentRunLimits:
    where = "limits"
    if not isinstance(raw, Mapping):
        raise RoleValidationError(f"{where}: must be a mapping")
    timeout_seconds = _require_positive_int(raw, "timeout_seconds", where)
    max_turns = _require_positive_int(raw, "max_turns", where)
    max_output_bytes = _require_positive_int(raw, "max_output_bytes", where)
    return AgentRunLimits(
        timeout_seconds=timeout_seconds,
        max_turns=max_turns,
        max_output_bytes=max_output_bytes,
    )


def _prompt(raw: Mapping[str, Any] | None) -> AgentPromptSpec:
    where = "prompt"
    if raw is None:
        return AgentPromptSpec(role_instruction="", output_contract="")
    if not isinstance(raw, Mapping):
        raise RoleValidationError(f"{where}: must be a mapping")
    role_instruction = raw.get("role_instruction", "")
    output_contract = raw.get("output_contract", "")
    if not isinstance(role_instruction, str) or not isinstance(output_contract, str):
        raise RoleValidationError(f"{where}: role_instruction/output_contract must be strings")
    return AgentPromptSpec(
        role_instruction=role_instruction,
        output_contract=output_contract,
    )


def _redaction(raw: Mapping[str, Any] | None) -> AgentRedactionSpec:
    if raw is None:
        return AgentRedactionSpec()
    where = "redaction"
    if not isinstance(raw, Mapping):
        raise RoleValidationError(f"{where}: must be a mapping")
    flags = {}
    for key in ("suppress_reads", "redact_prompt", "redact_stderr", "redact_metadata", "redact_env"):
        value = raw.get(key, True)
        if not isinstance(value, bool):
            raise RoleValidationError(f"{where}: {key!r} must be a boolean")
        flags[key] = value
    return AgentRedactionSpec(**flags)


def load_role(mapping: Mapping[str, Any]) -> AgentRoleSpec:
    if not isinstance(mapping, Mapping):
        raise RoleValidationError("AgentRoleSpec must be a mapping")
    schema_version = _require(mapping, "schema_version", "AgentRoleSpec")
    if schema_version != SCHEMA_VERSION:
        raise RoleValidationError(
            f"AgentRoleSpec: schema_version must be {SCHEMA_VERSION} (got {schema_version!r})",
        )
    role_id = _require_str(mapping, "role_id", "AgentRoleSpec")
    display_name = mapping.get("display_name", role_id)
    description = mapping.get("description", "")
    if not isinstance(display_name, str) or not isinstance(description, str):
        raise RoleValidationError(
            "AgentRoleSpec: display_name and description must be strings",
        )
    return AgentRoleSpec(
        schema_version=schema_version,
        role_id=role_id,
        display_name=display_name,
        description=description,
        runner=_runner(_require(mapping, "runner", "AgentRoleSpec")),
        workspace=_workspace(_require(mapping, "workspace", "AgentRoleSpec")),
        permissions=_permissions(_require(mapping, "permissions", "AgentRoleSpec")),
        session=_session(mapping.get("session")),
        limits=_limits(_require(mapping, "limits", "AgentRoleSpec")),
        prompt=_prompt(mapping.get("prompt")),
        redaction=_redaction(mapping.get("redaction")),
    )


def role_to_dict(role: AgentRoleSpec) -> dict:
    data = asdict(role)
    data["workspace"]["allowed_roots"] = list(role.workspace.allowed_roots)
    return data


def role_hash(role: AgentRoleSpec) -> str:
    payload = json.dumps(role_to_dict(role), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

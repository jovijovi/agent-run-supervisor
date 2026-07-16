"""Native role-bound acpx ``--mcp-config`` binding.

Resolves a role's optional ``runner.mcp_config`` declaration into a canonical
path + content-SHA binding, failing closed on anything that is not an
absolute, readable JSON file with a top-level ``mcpServers`` array.
Diagnostics never embed config file content: MCP server configs routinely
carry secrets (tokens, env values), so errors reference the declared path
only.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from agent_run_supervisor.role import AgentRoleSpec


class McpConfigError(ValueError):
    """Raised when a role's declared mcp_config cannot be verified."""


@dataclass(frozen=True)
class McpConfigBinding:
    """Canonical identity of a verified MCP config file."""

    path: str
    sha256: str


def resolve_mcp_config(role: AgentRoleSpec) -> McpConfigBinding | None:
    """Verify the role's declared MCP config and return its binding.

    Returns ``None`` when the role declares no ``runner.mcp_config``. Otherwise
    reads the declared file fresh — callers re-invoke this immediately before
    every spawn so same-path content drift is caught — and returns the
    canonical (symlink-resolved) absolute path plus the SHA-256 of the raw
    file bytes.
    """
    declared = role.runner.mcp_config
    if declared is None:
        return None
    if not isinstance(declared, str) or not Path(declared).is_absolute():
        raise McpConfigError(f"mcp_config {declared!r} must be an absolute path")
    canonical = os.path.realpath(declared)
    try:
        raw = Path(canonical).read_bytes()
    except OSError as exc:
        raise McpConfigError(f"mcp_config {declared!r} is unreadable: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        # Never echo file content into diagnostics — configs may carry secrets.
        raise McpConfigError(
            f"mcp_config {declared!r} is not valid UTF-8 JSON",
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), list):
        raise McpConfigError(
            f"mcp_config {declared!r} must be a JSON object with a top-level "
            "'mcpServers' array",
        )
    return McpConfigBinding(
        path=canonical,
        sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
    )

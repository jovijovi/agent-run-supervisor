"""Native role-bound acpx ``--mcp-config`` binding.

Resolves a role's optional ``runner.mcp_config`` declaration into a canonical
path + content-SHA binding, failing closed on anything that is not an
absolute, regular, readable JSON file with a top-level ``mcpServers`` array.
Non-regular file types (FIFO/socket/device/directory) are rejected before any
blocking open: a reader-less FIFO would otherwise hang validation forever.
Diagnostics never embed config file content: MCP server configs routinely
carry secrets (tokens, env values), so errors reference the declared path
only.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
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
    raw = _read_regular_file_bytes(canonical, declared)
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


_READ_CHUNK_BYTES = 65536


def _read_regular_file_bytes(canonical: str, declared: str) -> bytes:
    """Read ``canonical`` fail-closed, refusing non-regular file types.

    A plain ``open()`` on a reader-less FIFO blocks forever, so this never
    issues a blocking open: it stats first (rejecting FIFO/socket/device/
    directory without opening), then opens ``O_NONBLOCK`` and re-checks the
    opened descriptor with ``fstat`` so a stat-to-open swap to a non-regular
    file still fails closed instead of blocking or reading a stream.
    ``O_NOFOLLOW`` refuses a symlink swapped over the already-canonical final
    component after ``realpath``.
    """
    try:
        pre_mode = os.stat(canonical, follow_symlinks=False).st_mode
    except OSError as exc:
        raise McpConfigError(f"mcp_config {declared!r} is unreadable: {exc}") from exc
    if not stat.S_ISREG(pre_mode):
        raise McpConfigError(f"mcp_config {declared!r} must be a regular file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(canonical, flags)
    except OSError as exc:
        raise McpConfigError(f"mcp_config {declared!r} is unreadable: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise McpConfigError(f"mcp_config {declared!r} must be a regular file")
        chunks = []
        while True:
            try:
                chunk = os.read(fd, _READ_CHUNK_BYTES)
            except OSError as exc:
                raise McpConfigError(
                    f"mcp_config {declared!r} is unreadable: {exc}",
                ) from exc
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)

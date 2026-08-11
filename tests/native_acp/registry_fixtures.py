"""The single factory for operator agent-registry documents used by tests.

One place builds the TOML text and the on-disk file, so a bounds change has one
place to move and no suite can drift into asserting a shape the parser never
accepts. Every fixture writes below the caller's pytest ``tmp_path``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent_run_supervisor.native_acp import agent_registry

STANDARD_PROFILE = "standard-native-acp-v1"
COMPAT_PROFILE = "claude-agent-acp-compat-v1"
REASONIX_PROFILE = "reasonix-agent-acp-compat-v1"
MEDIATION_ID = "ask-privileged-tool-families-v1"


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _toml_array(values: Iterable[str]) -> str:
    return "[" + ", ".join(_toml_string(item) for item in values) + "]"


def _toml_table(pairs: Mapping[str, str]) -> str:
    body = ", ".join(f"{key} = {_toml_string(value)}" for key, value in pairs.items())
    return "{" + body + "}"


def entry_lines(agent_id: str, **fields: Any) -> list[str]:
    """Render one ``[agents."<id>"]`` table from typed Python values.

    The key is always quoted: an unquoted dotted key is TOML *nesting*, so
    ``a0.b`` would silently become two tables rather than one agent id.
    """
    lines = [f"[agents.{_toml_string(agent_id)}]"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        elif isinstance(value, str):
            lines.append(f"{key} = {_toml_string(value)}")
        elif isinstance(value, Mapping):
            lines.append(f"{key} = {_toml_table(value)}")
        else:
            lines.append(f"{key} = {_toml_array(value)}")
    return lines


def registry_text(
    *,
    schema_version: int | None = agent_registry.REGISTRY_SCHEMA_VERSION,
    entries: Mapping[str, Mapping[str, Any]] | None = None,
    extra_lines: Iterable[str] = (),
) -> str:
    """A complete registry document. ``entries`` maps agent_id → field mapping."""
    if entries is None:
        entries = {"native-agent": {"profile": STANDARD_PROFILE, "command": "some-agent"}}
    lines: list[str] = []
    if schema_version is not None:
        lines.append(f"schema_version = {schema_version}")
    lines.extend(extra_lines)
    for agent_id, fields in entries.items():
        lines.append("")
        lines.extend(entry_lines(agent_id, **fields))
    return "\n".join(lines) + "\n"


def write_registry(
    tmp_path: Path,
    *,
    name: str = "agents.toml",
    text: str | None = None,
    mode: int = 0o600,
    **kwargs: Any,
) -> Path:
    """Write a registry file below ``tmp_path`` and return its path."""
    path = Path(tmp_path) / name
    path.write_text(registry_text(**kwargs) if text is None else text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def minimal_entry(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"profile": STANDARD_PROFILE, "command": "some-agent"}
    body.update(overrides)
    return body


def full_entry(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "profile": STANDARD_PROFILE,
        "command": "/opt/example/bin/some-agent",
        "args": ["acp"],
        "mediation": MEDIATION_ID,
        "env_passthrough": ["SSH_AUTH_SOCK", "SOME_AGENT_CONFIG"],
        "env_overlay": {"SOME_AGENT_HOME": "/home/svc/.some-agent", "NO_BROWSER": "1"},
        "model_selector": "model",
        "effort_selector": "reasoning_effort",
        "forbidden_capabilities": ["terminal"],
        "session_epoch": 1,
    }
    body.update(overrides)
    return body


def omp_entry(**overrides: Any) -> dict[str, Any]:
    """The documented oh-my-pi operator entry, as parser-ready values."""
    body: dict[str, Any] = {
        "profile": STANDARD_PROFILE,
        "command": "/home/ecs-user/.local/bin/omp",
        "args": ["--approval-mode=always-ask", "acp"],
        "effort_selector": "thinking",
    }
    body.update(overrides)
    return body


def reasonix_entry(**overrides: Any) -> dict[str, Any]:
    """The documented Reasonix operator entry, as parser-ready values."""
    body: dict[str, Any] = {
        "profile": REASONIX_PROFILE,
        "command": "/home/linuxbrew/.linuxbrew/bin/reasonix",
        "args": ["acp"],
        "env_overlay": {
            "PATH": "/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin"
        },
    }
    body.update(overrides)
    return body

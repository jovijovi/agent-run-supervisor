"""The single factory for operator agent-registry documents used by tests.

One place builds the TOML text and the on-disk file, so a bounds change has one
place to move and no suite can drift into asserting a shape the parser never
accepts. Every fixture writes below the caller's pytest ``tmp_path``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple

from agent_run_supervisor.native_acp import agent_registry
from agent_run_supervisor.native_acp.profile import DEFAULT_REGISTRY

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


def snapshot(**kwargs: Any) -> agent_registry.AgentRegistrySnapshot:
    """The immutable snapshot a started daemon would hold, with no file at all.

    The daemon's snapshot *is* the parse result, so a test that needs one asks
    the real parser here rather than assembling entries by hand — a hand-built
    snapshot could hold a shape the parser never admits.
    """
    return agent_registry.parse_registry_text(registry_text(**kwargs))


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


# -- bounds documents ---------------------------------------------------------
#
# Deliberately separate from every fixture above. Those exist to be *read*: they
# spell an entry the way an operator would write one, and the renderer pads it
# for legibility. These exist to answer "how many entries fit under
# MAX_REGISTRY_BYTES", where legibility is the enemy — every byte of padding
# understates the answer, so a test built on the readable rendering proves its
# bound against a registry smaller than the one an operator could really load.

#: TOML reads a dotted key as *nesting*, so a compact document may only use ids
#: that are bare keys. Quoting one back would cost the two bytes this is for.
_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")


def widest_agent_id(index: int) -> str:
    """A distinct canonical id at the exact grammar maximum: 64 chars.

    Widest, because a bounds document wants the largest response its byte
    budget can buy, and alphanumeric so it is also a legal bare key.
    """
    wide = f"a{index:063d}"
    assert agent_registry.validate_agent_id(wide) == wide
    return wide


def _cheapest_entry() -> dict[str, str]:
    """The cheapest field *values* the parser accepts.

    Derived rather than spelled: the shortest **registered** profile id — the
    parser admits exactly that set, so a literal would stop being the cheapest
    the moment the set changes — and the shortest legal command, which is one
    basename character.
    """
    return {"profile": min(sorted(DEFAULT_REGISTRY.ids()), key=len), "command": "a"}


def compact_registry_text(agent_ids: Iterable[str]) -> str:
    """The most byte-efficient legal encoding of a registry document.

    Cheapest values (:func:`_cheapest_entry`) in the cheapest *encoding*: one
    ``[agents]`` table instead of a header per entry, bare keys instead of
    quoted ones, inline tables, no padding around ``=``, no blank lines, and no
    trailing newline — TOML does not require one, so carrying it would spend a
    byte this function exists not to spend. Each of those costs bytes in the
    readable rendering, and bytes are the whole question here. Bounds work only
    — never a model of operator authoring.
    """
    entry = _cheapest_entry()
    inline = "{" + ",".join(f'{key}="{value}"' for key, value in entry.items()) + "}"
    lines = [
        f"schema_version={agent_registry.REGISTRY_SCHEMA_VERSION}",
        "[agents]",
    ]
    for agent_id in agent_ids:
        if _BARE_KEY_RE.fullmatch(agent_id) is None:
            raise ValueError(f"a compact bounds id must be a TOML bare key: {agent_id!r}")
        lines.append(f"{agent_id}={inline}")
    return "\n".join(lines)


class MaximumLegalRegistry(NamedTuple):
    """The largest legal document, the next one up, and how it was derived."""

    text: str
    one_more: str
    count: int
    marginal_bytes: int


def maximum_legal_registry() -> MaximumLegalRegistry:
    """The largest document the real registry parser accepts, and the next one up.

    Derived from ``MAX_REGISTRY_BYTES`` and the *measured* cost of the compact
    encoding above — never a hand-estimated threshold and never a pinned count.
    Both documents come from the same encoding and the same entry, so "one more"
    is one more of the same thing rather than a second, differently shaped
    estimate.
    """
    header = len(compact_registry_text(()).encode("utf-8"))
    marginal = len(compact_registry_text((widest_agent_id(0),)).encode("utf-8")) - header
    count = (agent_registry.MAX_REGISTRY_BYTES - header) // marginal

    def document(entries: int) -> str:
        return compact_registry_text(widest_agent_id(index) for index in range(entries))

    return MaximumLegalRegistry(
        text=document(count),
        one_more=document(count + 1),
        count=count,
        marginal_bytes=marginal,
    )


def readable_entry_marginal_bytes() -> int:
    """What one widest-id entry costs in the *readable* rendering.

    Exists for one assertion: the compact document has to stay strictly cheaper
    than this. Nothing else in a bounds test fails when the document quietly
    becomes roomier — a padded entry still parses, still fits, still encodes.
    """
    header = len(registry_text(entries={}).encode("utf-8"))
    one = len(
        registry_text(entries={widest_agent_id(0): minimal_entry()}).encode("utf-8")
    )
    return one - header

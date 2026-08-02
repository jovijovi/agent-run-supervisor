"""The operator-owned agent registry **entry** and its bounded grammars.

Layer 2 of the four-way boundary: which command is that agent, *here*. An entry
carries the command and its argv, the environment names and literals the
operator declares, a mediation *selection*, selector-id hints, a capability
narrowing, and an optional continuity epoch. It declares no capability
requirement, protocol version, mediation pair, digest, path expectation,
version, or transport — those are not fields, so the refusal is structural
rather than filtered.

This module is **pure**. It performs no filesystem query, opens nothing, and
resolves no path: a grammar that decides on text alone cannot be raced,
redirected, or made to disagree with what a later reader sees. The single
reader of the registry file is
:mod:`agent_run_supervisor.native_acp.agent_registry`, which calls in here with
values it has already decoded.

Nothing here computes, stores, or reports a hash of anything. The entry is not
an identity to be frozen: an agent upgrade behind an unchanged registered
command must cost no ARS action at all, and a fingerprint-as-gate is exactly
the failure mode the reset removes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

REGISTRY_SCHEMA_VERSION = 1

# The complete, closed field set of one entry. Required first, then optional.
REQUIRED_ENTRY_FIELDS = ("profile", "command")
OPTIONAL_ENTRY_FIELDS = (
    "args",
    "mediation",
    "env_passthrough",
    "env_overlay",
    "model_selector",
    "effort_selector",
    "forbidden_capabilities",
    "session_epoch",
)
ENTRY_FIELDS = REQUIRED_ENTRY_FIELDS + OPTIONAL_ENTRY_FIELDS

AGENT_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SELECTOR_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
CAPABILITY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")

MAX_COMMAND_BYTES = 4096
MAX_ARGS = 32
MAX_ARG_BYTES = 1024
MAX_ENV_PASSTHROUGH = 32
MAX_ENV_NAME_CHARS = 128
MAX_ENV_OVERLAY = 32
MAX_ENV_OVERLAY_VALUE_BYTES = 4096
MAX_FORBIDDEN_CAPABILITIES = 16


class RegistryRefusal(Exception):
    """Fail-closed registry refusal; ``rule`` names the failing rule.

    The message carries the rule, a field path, and at most an environment
    *name*. It never carries an overlay value, a raw file fragment, or any text
    the operator's own data chose.
    """

    def __init__(self, *, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


def refuse(rule: str, message: str) -> RegistryRefusal:
    return RegistryRefusal(rule=rule, message=f"agent registry refused [{rule}]: {message}")


@dataclass(frozen=True)
class AgentEntry:
    """One operator-authored agent, projected into immutable typed values."""

    agent_id: str
    profile_id: str
    command: str
    args: tuple[str, ...] = ()
    mediation_id: str | None = None
    env_passthrough: tuple[str, ...] = ()
    env_overlay: tuple[tuple[str, str], ...] = field(default=())
    model_selector_id: str | None = None
    effort_selector_id: str | None = None
    forbidden_capabilities: tuple[str, ...] = ()
    session_epoch: int | None = None

    def argv(self) -> tuple[str, ...]:
        """The exact argv handed to exec: ``argv[0]`` is the declared command.

        Byte-for-byte, exactly as a shell would pass it. A bare name stays a
        bare name; the image is located by ``execvp``-style lookup over the
        child's projected ``PATH``.
        """
        return (self.command, *self.args)


# -- primitive validators -----------------------------------------------------


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _byte_length(value: str) -> int:
    return len(value.encode("utf-8", "surrogatepass"))


def validate_agent_id(value: Any, *, rule: str = "AGENT_ID_INVALID") -> str:
    """The one ``agent_id`` grammar, shared by the parse and by admission."""
    if not isinstance(value, str) or AGENT_ID_RE.fullmatch(value) is None:
        raise refuse(rule, "agent id must match [a-z0-9][a-z0-9._-]{0,63}")
    return value


def _validate_command(agent_id: str, value: Any) -> str:
    where = f"agents.{agent_id}.command"
    if not isinstance(value, str) or not value:
        raise refuse("ENTRY_COMMAND_INVALID", f"{where} must be a non-empty string")
    if _byte_length(value) > MAX_COMMAND_BYTES:
        raise refuse(
            "ENTRY_COMMAND_INVALID", f"{where} exceeds {MAX_COMMAND_BYTES} bytes"
        )
    if "\x00" in value:
        raise refuse("ENTRY_COMMAND_INVALID", f"{where} carries a NUL byte")
    if value.startswith("/"):
        return value
    if "/" in value:
        raise refuse(
            "ENTRY_COMMAND_INVALID",
            f"{where} must be absolute or a single basename with no path separator",
        )
    return value


def _validate_args(agent_id: str, value: Any) -> tuple[str, ...]:
    """The declared argv tail: bounded by count, bytes, and NUL. Nothing else.

    An **empty token is valid**. ``argv`` is handed to ``exec`` directly and
    never to a shell, so ``""`` is an ordinary token — it is how an operator
    passes an empty positional to a CLI that distinguishes "absent" from
    "present and empty". Refusing it invented a rule the registry contract does
    not state, and the alternative failure mode is worse than a refusal: silently
    dropping the token would hand the child a different argv than the one
    declared, which is the one thing command semantics promise never happens.

    ``argv[0]`` is a separate question and keeps its own non-empty rule: it is
    the image to locate, not a token to pass.
    """
    where = f"agents.{agent_id}.args"
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise refuse("ENTRY_ARG_TOKEN_INVALID", f"{where} must be an array of strings")
    if len(value) > MAX_ARGS:
        raise refuse("ENTRY_ARG_TOKEN_INVALID", f"{where} exceeds {MAX_ARGS} tokens")
    for index, token in enumerate(value):
        position = f"{where}[{index}]"
        if _byte_length(token) > MAX_ARG_BYTES:
            raise refuse(
                "ENTRY_ARG_TOKEN_INVALID", f"{position} exceeds {MAX_ARG_BYTES} bytes"
            )
        if "\x00" in token:
            raise refuse("ENTRY_ARG_TOKEN_INVALID", f"{position} carries a NUL byte")
    return tuple(value)


def is_env_name(value: Any) -> bool:
    """One environment name, by this registry's own grammar and length bound."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_ENV_NAME_CHARS
        and ENV_NAME_RE.fullmatch(value) is not None
    )


def is_env_passthrough_domain(value: Any) -> bool:
    """A whole ``env_passthrough`` name list, exactly as the parser admits one.

    Three facts, stated once: every name grammar-valid, no name repeated, and at
    most :data:`MAX_ENV_PASSTHROUGH` of them. Two directions need this same
    answer — the parser, to admit an operator's file, and the launch-record
    reader, to recognise a projection its own writer could have produced — and a
    reader that answered it differently would accept a document no writer can
    emit. So the decision has one implementation and both sides call it.

    The bound is an ARS schema bound, not an OS or ACP limit; it lives at
    :data:`MAX_ENV_PASSTHROUGH` and is not restated anywhere else.
    """
    if not isinstance(value, (list, tuple)):
        return False
    if len(value) > MAX_ENV_PASSTHROUGH:
        return False
    if not all(is_env_name(name) for name in value):
        return False
    return len(set(value)) == len(value)


def _validate_env_name(where: str, name: Any) -> str:
    if not isinstance(name, str) or not name:
        raise refuse("ENTRY_ENV_KEY_INVALID", f"{where} name must be a non-empty string")
    if not is_env_name(name):
        raise refuse(
            "ENTRY_ENV_KEY_INVALID",
            f"{where} name {name!r} must match [A-Za-z_][A-Za-z0-9_]*",
        )
    return name


def _validate_env_passthrough(agent_id: str, value: Any) -> tuple[str, ...]:
    """Admit the list through the shared domain; walk it only to explain a refusal.

    The accept decision is :func:`is_env_passthrough_domain` and nothing else,
    so the parser cannot admit a list the launch reader would reject. The walk
    below runs only when that decision was already *no*, and exists purely to
    name which of the three rules failed and where — an operator needs that, and
    a boolean cannot say it.
    """
    where = f"agents.{agent_id}.env_passthrough"
    if not isinstance(value, list):
        raise refuse("ENTRY_ENV_KEY_INVALID", f"{where} must be an array of names")
    if is_env_passthrough_domain(value):
        return tuple(value)
    if len(value) > MAX_ENV_PASSTHROUGH:
        raise refuse(
            "ENTRY_ENV_KEY_INVALID", f"{where} exceeds {MAX_ENV_PASSTHROUGH} names"
        )
    names: list[str] = []
    for name in value:
        validated = _validate_env_name(where, name)
        if validated in names:
            raise refuse("ENTRY_ENV_KEY_INVALID", f"{where} repeats {validated}")
        names.append(validated)
    # Unreachable while the walk covers every rule the predicate applies. Kept
    # because falling through would silently return a list the reader refuses.
    raise refuse("ENTRY_ENV_KEY_INVALID", f"{where} is not an accepted name list")


def _validate_env_overlay(agent_id: str, value: Any) -> tuple[tuple[str, str], ...]:
    where = f"agents.{agent_id}.env_overlay"
    if not isinstance(value, dict):
        raise refuse("ENTRY_ENV_KEY_INVALID", f"{where} must be a table of name = value")
    if len(value) > MAX_ENV_OVERLAY:
        raise refuse("ENTRY_ENV_KEY_INVALID", f"{where} exceeds {MAX_ENV_OVERLAY} pairs")
    pairs: list[tuple[str, str]] = []
    for name, literal in value.items():
        validated = _validate_env_name(where, name)
        # The value is judged, never quoted: reporting it would hand the
        # operator's own literal back through a refusal message.
        if not isinstance(literal, str):
            raise refuse(
                "ENTRY_ENV_VALUE_INVALID", f"{where}.{validated} must be a string"
            )
        if _byte_length(literal) > MAX_ENV_OVERLAY_VALUE_BYTES:
            raise refuse(
                "ENTRY_ENV_VALUE_INVALID",
                f"{where}.{validated} exceeds {MAX_ENV_OVERLAY_VALUE_BYTES} bytes",
            )
        if not all(ch.isprintable() for ch in literal):
            raise refuse(
                "ENTRY_ENV_VALUE_INVALID",
                f"{where}.{validated} contains non-printable characters",
            )
        pairs.append((validated, literal))
    return tuple(pairs)


def _validate_selector(agent_id: str, key: str, value: Any) -> str:
    where = f"agents.{agent_id}.{key}"
    if not isinstance(value, str) or SELECTOR_ID_RE.fullmatch(value) is None:
        raise refuse(
            "ENTRY_SELECTOR_INVALID",
            f"{where} must be a selector id, never a value domain",
        )
    return value


def _validate_forbidden_capabilities(agent_id: str, value: Any) -> tuple[str, ...]:
    where = f"agents.{agent_id}.forbidden_capabilities"
    if not isinstance(value, list):
        raise refuse("ENTRY_CAPABILITY_INVALID", f"{where} must be an array of names")
    if len(value) > MAX_FORBIDDEN_CAPABILITIES:
        raise refuse(
            "ENTRY_CAPABILITY_INVALID",
            f"{where} exceeds {MAX_FORBIDDEN_CAPABILITIES} names",
        )
    names: list[str] = []
    for name in value:
        if not isinstance(name, str) or CAPABILITY_RE.fullmatch(name) is None:
            raise refuse("ENTRY_CAPABILITY_INVALID", f"{where} carries an unbounded name")
        if name not in names:
            names.append(name)
    return tuple(sorted(names))


def _validate_session_epoch(agent_id: str, value: Any) -> int:
    where = f"agents.{agent_id}.session_epoch"
    if not _is_plain_int(value) or value <= 0:
        raise refuse("ENTRY_SESSION_EPOCH_INVALID", f"{where} must be a positive integer")
    return value


# -- the entry parser ---------------------------------------------------------


def parse_entry(
    payload: Any,
    *,
    agent_id: str,
    known_profile_ids: frozenset[str],
    known_mediation_ids: frozenset[str],
) -> AgentEntry:
    """Project one decoded entry table, or refuse by a stable rule.

    ``known_profile_ids`` and ``known_mediation_ids`` are handed in rather than
    imported so this module stays a pure grammar with no opinion about which
    profiles source happens to register today.
    """
    validate_agent_id(agent_id)
    if not isinstance(payload, dict):
        raise refuse("REGISTRY_PARSE", f"agents.{agent_id} must be a table")
    unknown = sorted(key for key in payload if key not in ENTRY_FIELDS)
    if unknown:
        raise refuse(
            "REGISTRY_UNKNOWN_KEY",
            f"agents.{agent_id} carries unknown key(s): {unknown}",
        )
    missing = [name for name in REQUIRED_ENTRY_FIELDS if name not in payload]
    if missing:
        raise refuse("ENTRY_FIELD_MISSING", f"agents.{agent_id} omits {missing}")

    profile_id = payload["profile"]
    if not isinstance(profile_id, str) or profile_id not in known_profile_ids:
        raise refuse(
            "ENTRY_UNKNOWN_PROFILE",
            f"agents.{agent_id}.profile does not name a source-registered profile",
        )

    mediation_id = payload.get("mediation")
    if mediation_id is not None:
        if not isinstance(mediation_id, str) or mediation_id not in known_mediation_ids:
            raise refuse(
                "ENTRY_UNKNOWN_MEDIATION_ID",
                f"agents.{agent_id}.mediation does not name a source-owned binding",
            )

    return AgentEntry(
        agent_id=agent_id,
        profile_id=profile_id,
        command=_validate_command(agent_id, payload["command"]),
        args=_validate_args(agent_id, payload.get("args", [])),
        mediation_id=mediation_id,
        env_passthrough=_validate_env_passthrough(
            agent_id, payload.get("env_passthrough", [])
        ),
        env_overlay=_validate_env_overlay(agent_id, payload.get("env_overlay", {})),
        model_selector_id=(
            None
            if "model_selector" not in payload
            else _validate_selector(agent_id, "model_selector", payload["model_selector"])
        ),
        effort_selector_id=(
            None
            if "effort_selector" not in payload
            else _validate_selector(
                agent_id, "effort_selector", payload["effort_selector"]
            )
        ),
        forbidden_capabilities=_validate_forbidden_capabilities(
            agent_id, payload.get("forbidden_capabilities", [])
        ),
        session_epoch=(
            None
            if "session_epoch" not in payload
            else _validate_session_epoch(agent_id, payload["session_epoch"])
        ),
    )


def declared_environment_names(entry: AgentEntry) -> tuple[str, ...]:
    """Every environment name this entry names, in either declaration."""
    return (*entry.env_passthrough, *(name for name, _ in entry.env_overlay))


def entry_projection(entry: AgentEntry) -> Mapping[str, Any]:
    """Value-blind projection for operator reporting.

    Overlay **names** appear; overlay values never do. There is no digest of
    anything here, and no field a Session identity could be derived from beyond
    the operator's own ``session_epoch``.
    """
    return {
        "agent_id": entry.agent_id,
        "profile": entry.profile_id,
        "command": entry.command,
        "args": list(entry.args),
        "mediation": entry.mediation_id,
        "env_passthrough": list(entry.env_passthrough),
        "env_overlay_names": [name for name, _ in entry.env_overlay],
        "model_selector": entry.model_selector_id,
        "effort_selector": entry.effort_selector_id,
        "forbidden_capabilities": list(entry.forbidden_capabilities),
        "session_epoch": entry.session_epoch,
    }

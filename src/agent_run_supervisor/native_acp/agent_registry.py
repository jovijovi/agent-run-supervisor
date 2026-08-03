"""The one operator agent-registry file, read exactly once at daemon startup.

**The contract, stated once.** One operator-owned TOML file, supplied by a
required ``--agents-file`` daemon flag, read exactly once at daemon startup into
an immutable in-memory snapshot. Any defect refuses the whole file — never a
partial honor, never a cached previous start, never a repair — and the daemon
refuses to listen before any state write. After that the registry is never
opened again for the daemon's whole lifetime, so the Run, spawn, finalization,
and reconciliation paths perform zero registry filesystem access, two concurrent
Runs can never resolve different registry contents, and a serving daemon cannot
be re-pointed.

**Config hygiene, explicitly not attestation.** The path is resolved, symlinks
are followed, and the resolved target must be a regular file that is not group-
or world-writable. A dotfiles symlink works, including below ``$HOME``; a file
anyone can edit does not. This is ARS declining to take orders from a
world-writable file — the same standard as an SSH config — and it is bounded to
*its own configuration file*. It says nothing whatsoever about ``command``: ARS
performs no ownership, mode, ancestor, symlink, or digest check on the declared
command, on its ancestors, or on anything the AGENT subsequently loads.

The bounded per-entry grammar lives in
:mod:`agent_run_supervisor.native_acp.agent_registration`, which is pure. This
module owns exactly the two things that grammar must not: the single filesystem
read, and the document-level rules that need the whole file at once — the schema
version, the unknown-key floor, and the global mediation-key collision check.
"""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from .agent_registration import (
    ENTRY_FIELDS,
    REGISTRY_SCHEMA_VERSION,
    AgentEntry,
    RegistryRefusal,
    declared_environment_names,
    parse_entry,
    refuse,
    validate_agent_id,
)
from .launch_permissions import reserved_keys_for_policy
from .profile import (
    DEFAULT_REGISTRY,
    MEDIATION_BINDING_IDS,
    RESERVED_MEDIATION_KEYS,
)

__all__ = [
    "MAX_REGISTRY_BYTES",
    "REGISTRY_SCHEMA_VERSION",
    "AgentEntry",
    "AgentRegistrySnapshot",
    "RegistryRefusal",
    "load_agents_file",
    "parse_registry_text",
    "resolve_agent_entry",
]

MAX_REGISTRY_BYTES = 1 << 20
TOP_LEVEL_FIELDS = ("schema_version", "agents")

# Mode bits that make the file writable by someone other than its owner. A
# registry anyone can edit is a registry anyone can use to choose a command.
_UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


class AgentRegistrySnapshot:
    """The immutable in-memory result of the one registry read.

    There is deliberately no ``reload``, no retained path, and no lazily
    evaluated property: a second read would be a contract violation, not a
    performance question, so the seam for one simply does not exist.
    """

    __slots__ = ("_entries",)

    def __init__(self, entries: Mapping[str, AgentEntry]) -> None:
        self._entries: Mapping[str, AgentEntry] = MappingProxyType(dict(entries))

    @property
    def entries(self) -> Mapping[str, AgentEntry]:
        return self._entries

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[str]:
        return iter(self.ids())

    def __contains__(self, agent_id: object) -> bool:
        return agent_id in self._entries

    def get(self, agent_id: Any) -> AgentEntry:
        """Resolve one entry in memory. Never touches the filesystem.

        The grammar runs before the lookup, so ``agent_id`` — the one
        registry-facing value that crosses the wire — is judged as text before
        it can select anything at all.
        """
        validate_agent_id(agent_id)
        try:
            return self._entries[agent_id]
        except KeyError:
            raise refuse(
                "AGENT_NOT_REGISTERED", f"no registry entry names agent {agent_id!r}"
            ) from None

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"<AgentRegistrySnapshot entries={len(self._entries)}>"


def resolve_agent_entry(
    snapshot: AgentRegistrySnapshot | None, agent_id: Any
) -> AgentEntry:
    """The single admission-path resolution: pure, in memory, zero filesystem.

    A daemon with no snapshot is a daemon that never parsed a registry, which
    the startup order makes impossible; refusing here keeps the direct
    ``ars-core`` test/dev path from inventing a default.
    """
    if snapshot is None:
        raise refuse(
            "REGISTRY_ABSENT",
            "no agent registry snapshot is configured for this supervisor",
        )
    return snapshot.get(agent_id)


# -- the single read ----------------------------------------------------------


def _read_registry_bytes(path: Path) -> bytes:
    """Open once, judge the descriptor, read bounded. No second lookup.

    ``fstat`` on the open descriptor rather than ``stat`` on the path is what
    makes the hygiene check and the read describe the same object: a file
    swapped between a path check and a path read is a different file, and this
    reader never gives it the chance. Symlinks are deliberately followed — a
    dotfiles symlink is the ordinary operator layout — so the target's own mode
    is what is judged.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        raise refuse("REGISTRY_ABSENT", "the agents file does not exist") from None
    except IsADirectoryError:
        raise refuse(
            "REGISTRY_NOT_REGULAR_FILE", "the agents file is not a regular file"
        ) from None
    except OSError:
        raise refuse("REGISTRY_UNREADABLE", "the agents file could not be opened") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise refuse(
                "REGISTRY_NOT_REGULAR_FILE", "the agents file is not a regular file"
            )
        if info.st_mode & _UNSAFE_WRITE_BITS:
            raise refuse(
                "REGISTRY_UNSAFE_MODE",
                "the agents file is group- or world-writable",
            )
        if info.st_size > MAX_REGISTRY_BYTES:
            raise refuse(
                "REGISTRY_TOO_LARGE", f"the agents file exceeds {MAX_REGISTRY_BYTES} bytes"
            )
        chunks: list[bytes] = []
        remaining = MAX_REGISTRY_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 16))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError:
        raise refuse("REGISTRY_UNREADABLE", "the agents file could not be read") from None
    finally:
        try:
            os.close(fd)
        except OSError:  # pragma: no cover - close failure is not evidence
            pass
    if len(raw) > MAX_REGISTRY_BYTES:
        raise refuse(
            "REGISTRY_TOO_LARGE", f"the agents file exceeds {MAX_REGISTRY_BYTES} bytes"
        )
    return raw


def load_agents_file(path: Path | str) -> AgentRegistrySnapshot:
    """Resolve, read, and parse the agents file exactly once.

    Every refusal raised here happens before any state write and before the
    socket is bound, so a defective registry costs a refusal to listen rather
    than a half-configured daemon.
    """
    target = Path(path)
    raw = _read_registry_bytes(target)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise refuse("REGISTRY_PARSE", "the agents file is not valid UTF-8") from None
    return parse_registry_text(text)


def parse_registry_text(text: str) -> AgentRegistrySnapshot:
    """Strict parse of already-read registry text into an immutable snapshot.

    ``agents validate`` and the daemon call exactly this function, so what an
    operator sees offline is what the daemon will decide at its next start.
    """
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        # The decoder's message quotes the offending line; a raw file fragment
        # is never operator-facing evidence here.
        raise refuse("REGISTRY_PARSE", "the agents file is not valid TOML") from None
    return parse_registry_document(document)


def parse_registry_document(document: Any) -> AgentRegistrySnapshot:
    if not isinstance(document, dict):
        raise refuse("REGISTRY_PARSE", "the agents file must be a TOML table")
    unknown = sorted(key for key in document if key not in TOP_LEVEL_FIELDS)
    if unknown:
        raise refuse(
            "REGISTRY_UNKNOWN_KEY", f"the agents file carries unknown key(s): {unknown}"
        )
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or (
        version != REGISTRY_SCHEMA_VERSION
    ):
        raise refuse(
            "REGISTRY_SCHEMA_VERSION",
            f"schema_version must be exactly {REGISTRY_SCHEMA_VERSION}",
        )
    agents = document.get("agents", {})
    if not isinstance(agents, dict):
        raise refuse("REGISTRY_PARSE", "agents must be a table of agent tables")

    known_profile_ids = frozenset(DEFAULT_REGISTRY.ids())
    entries: dict[str, AgentEntry] = {}
    for agent_id in sorted(agents):
        entries[agent_id] = parse_entry(
            agents[agent_id],
            agent_id=agent_id,
            known_profile_ids=known_profile_ids,
            known_mediation_ids=MEDIATION_BINDING_IDS,
        )
    _refuse_mediation_key_collisions(entries)
    _refuse_launch_permission_key_collisions(entries)
    return AgentRegistrySnapshot(entries)


def _refuse_mediation_key_collisions(entries: Mapping[str, AgentEntry]) -> None:
    """Reserved mediation keys are global, and a collision refuses startup.

    Global, not per-selection: the reserved set is the union of every key in
    *any* registered binding, so the rule does not depend on which binding an
    entry chose or whether it chose one at all. If configuration could shadow a
    mediation key, the default-deny claim would be decorative — so a collision
    refuses the whole file, and ``agents validate`` applies this identical check
    offline, at authoring time.

    Mediation is applied last anyway, as defense in depth: a defect here cannot
    silently let an overlay disable mediation, and the two properties are tested
    independently so neither can be mistaken for the other's proof.
    """
    for agent_id in sorted(entries):
        for name in declared_environment_names(entries[agent_id]):
            if name in RESERVED_MEDIATION_KEYS:
                raise refuse(
                    "MEDIATION_KEY_COLLISION",
                    f"agents.{agent_id} declares reserved mediation key {name}; "
                    "the mediation binding is source-owned in key and value",
                )


def _refuse_launch_permission_key_collisions(
    entries: Mapping[str, AgentEntry]
) -> None:
    """A launch-permission key is refused **for the profile that selects it**.

    Layer 5 is applied last, so a declaration of that key would be silently
    overwritten and the resulting projection would look perfectly consistent
    while hiding the conflict. An operator who wrote the key decided something,
    so the refusal happens where they can still see it — at parse time, which
    ``agents validate`` and the daemon reach through this same function.

    Per-selection, unlike the mediation rule above: a profile that selects no
    launch policy projects no layer 5 and materializes nothing, so there is
    nothing here for the registry to protect. Which profile selects which
    policy is registry data; nothing in this check knows an agent's name.
    """
    for agent_id in sorted(entries):
        entry = entries[agent_id]
        # The profile id already passed the closed-set check in ``parse_entry``.
        profile = DEFAULT_REGISTRY.get(entry.profile_id)
        reserved = reserved_keys_for_policy(profile.launch_permission_policy_id)
        if not reserved:
            continue
        for name in declared_environment_names(entry):
            if name in reserved:
                raise refuse(
                    "LAUNCH_PERMISSION_KEY_COLLISION",
                    f"agents.{agent_id} declares {name}, which the launch "
                    "permission policy its profile selects owns in key and "
                    "value",
                )


# Named here so a reader of this module can see the closed entry field set
# without opening the grammar. Re-exported, never redefined.
ENTRY_FIELD_SET = ENTRY_FIELDS

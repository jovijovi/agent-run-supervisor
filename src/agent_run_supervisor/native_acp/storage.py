"""Store-isolation binding seam: the only Native root constructors.

Every Native session/run store binds here to ``native-sessions/`` and
``native-runs/`` under an already-resolved supervisor control root. The
helpers are deliberately root-binding constructors plus thin native-only
wrappers — not a storage abstraction. Direct ``SessionStore``/``EventStore``
construction anywhere else in ``native_acp/`` is a structural test failure;
legacy stores, roots, and defaults are never referenced or modified.
"""

from __future__ import annotations

import datetime as _dt
import enum
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.event_store import (
    EventStore,
    durable_secure_mkdir,
    exclusive_create_bytes,
)
from agent_run_supervisor.process_liveness import LivenessProbe
from agent_run_supervisor.result import (
    MAX_NATIVE_RESULT_SERIALIZED_BYTES,
    validate_native_terminal_result,
)
from agent_run_supervisor.session import SessionRecord, SessionStore

# The only place in src/ where these directory names are spelled.
NATIVE_SESSIONS_DIRNAME = "native-sessions"
NATIVE_RUNS_DIRNAME = "native-runs"

# Native state vocabulary bijection: the authority/API term 'active' maps to
# the existing on-disk compatibility value 'open' at this boundary; 'closed'
# and 'quarantined' persist 1:1. The persisted file never carries 'active'.
_TO_NATIVE_STATE = {"open": "active", "closed": "closed", "quarantined": "quarantined"}
_TO_PERSISTED_STATE = {
    "active": "open",
    "closed": "closed",
    "quarantined": "quarantined",
}

# Read cap for terminal evidence: schema ceiling plus decode bound.
_MAX_TERMINAL_READ_BYTES = MAX_NATIVE_RESULT_SERIALIZED_BYTES


class NativeTerminalKind(enum.Enum):
    """Typed outcomes for the single trusted Native terminal reader."""

    ABSENT = "absent"
    TRUSTED = "trusted"
    INVALID = "invalid"


@dataclass(frozen=True)
class NativeTerminalState:
    """ABSENT, TRUSTED(payload), or INVALID/UNCERTAIN — never raw errors/data."""

    kind: NativeTerminalKind
    payload: dict[str, Any] | None = None


def to_native_state(persisted: str) -> str:
    try:
        return _TO_NATIVE_STATE[persisted]
    except KeyError:
        raise ValueError(f"not a persisted native session state: {persisted!r}") from None


def to_persisted_state(native: str) -> str:
    try:
        return _TO_PERSISTED_STATE[native]
    except KeyError:
        raise ValueError(f"not a canonical native session state: {native!r}") from None


def native_session_store(
    supervisor_root: Path, *, liveness_probe: LivenessProbe | None = None
) -> SessionStore:
    """A SessionStore bound explicitly to ``<supervisor_root>/native-sessions``.

    Performs no discovery of its own: the caller passes the already-resolved
    supervisor control root. The root is created-or-verified at 0700 via the
    durable secure-mkdir primitive — a pre-existing insecure mode is
    deliberately corrected.
    """
    root = Path(supervisor_root) / NATIVE_SESSIONS_DIRNAME
    durable_secure_mkdir(root)
    return SessionStore(base_dir=root, liveness_probe=liveness_probe)


def native_event_store(supervisor_root: Path) -> EventStore:
    """An EventStore bound explicitly to ``<supervisor_root>/native-runs``.

    Secured at 0700 here, so ``EventStore.create_run``'s own plain mkdir never
    runs against an unverified root.
    """
    root = Path(supervisor_root) / NATIVE_RUNS_DIRNAME
    durable_secure_mkdir(root)
    return EventStore(base_dir=root)


def write_once_json(path: Path, payload: Mapping[str, Any]) -> Path:
    """The single sanctioned writer for immutable Native artifacts.

    Canonical JSON created exclusively at 0600 via ``exclusive_create_bytes``
    (temp + atomic no-clobber publish); a second create of the same path raises
    ``FileExistsError`` and can never overwrite the first bytes. Ordinary
    atomic replacement is *not* write-once and is never used for these.
    """
    data = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    return exclusive_create_bytes(Path(path), data)


def read_native_terminal_result(path: Path, *, run_id: str) -> NativeTerminalState:
    """Single trusted Native terminal reader for live paths and reconciliation.

    Opens with ``O_RDONLY|O_NOFOLLOW``, requires a regular file, reads through
    that fd with a finite cap, and delegates schema trust to
    :func:`validate_native_terminal_result`. Returns ABSENT, TRUSTED(payload),
    or INVALID (corrupt/oversize/symlink/wrong-schema/compat-only/uncertain IO)
    without raw errors or data.
    """
    path = Path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return NativeTerminalState(NativeTerminalKind.ABSENT)
    except OSError:
        return NativeTerminalState(NativeTerminalKind.INVALID)

    raw: bytes | None = None
    try:
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raw = None
            elif st.st_size > _MAX_TERMINAL_READ_BYTES:
                raw = None
            else:
                raw = _read_fd_capped(fd, _MAX_TERMINAL_READ_BYTES + 1)
                if len(raw) > _MAX_TERMINAL_READ_BYTES:
                    raw = None
        except OSError:
            raw = None
    finally:
        try:
            os.close(fd)
        except OSError:
            raw = None

    if raw is None:
        return NativeTerminalState(NativeTerminalKind.INVALID)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return NativeTerminalState(NativeTerminalKind.INVALID)

    validated = validate_native_terminal_result(payload, run_id=run_id)
    if validated is None:
        return NativeTerminalState(NativeTerminalKind.INVALID)
    return NativeTerminalState(NativeTerminalKind.TRUSTED, payload=validated)


def _read_fd_capped(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = os.read(fd, min(65_536, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def create_native_session(
    store: SessionStore,
    *,
    session_id: str,
    profile_id: str,
    profile_revision: int,
    profile_hash: str,
    owner: str,
    namespace: str,
    workspace_hash: str,
    effective_cwd: str,
    matched_root: str | None,
    now: _dt.datetime | None = None,
) -> SessionRecord:
    """The only sanctioned Native call site for record creation."""
    return store.create_native_session(
        session_id=session_id,
        profile_id=profile_id,
        profile_revision=profile_revision,
        profile_hash=profile_hash,
        owner=owner,
        namespace=namespace,
        workspace_hash=workspace_hash,
        effective_cwd=effective_cwd,
        matched_root=matched_root,
        now=now,
    )


def bind_agent_session(
    store: SessionStore,
    session_id: str,
    *,
    agent_session_id: str,
    now: _dt.datetime | None = None,
) -> SessionRecord:
    """The only sanctioned Native call site for external-ID binding."""
    return store.bind_agent_session(
        session_id, agent_session_id=agent_session_id, now=now
    )

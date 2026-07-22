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
import json
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.event_store import (
    EventStore,
    exclusive_create_bytes,
    secure_mkdir,
)
from agent_run_supervisor.process_liveness import LivenessProbe
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
    approved ``secure_mkdir`` primitive — a pre-existing insecure mode is
    deliberately corrected.
    """
    root = Path(supervisor_root) / NATIVE_SESSIONS_DIRNAME
    secure_mkdir(root)
    return SessionStore(base_dir=root, liveness_probe=liveness_probe)


def native_event_store(supervisor_root: Path) -> EventStore:
    """An EventStore bound explicitly to ``<supervisor_root>/native-runs``.

    Secured at 0700 here, so ``EventStore.create_run``'s own plain mkdir never
    runs against an unverified root.
    """
    root = Path(supervisor_root) / NATIVE_RUNS_DIRNAME
    secure_mkdir(root)
    return EventStore(base_dir=root)


def write_once_json(path: Path, payload: Mapping[str, Any]) -> Path:
    """The single sanctioned writer for immutable Native artifacts.

    Canonical JSON created exclusively (``O_EXCL``) at 0600 via
    ``exclusive_create_bytes``; a second create of the same path raises
    ``FileExistsError`` and can never overwrite the first bytes. Ordinary
    atomic replacement is *not* write-once and is never used for these.
    """
    data = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    return exclusive_create_bytes(Path(path), data)


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

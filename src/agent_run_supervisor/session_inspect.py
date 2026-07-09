"""Read-only local session inspection for callers.

This module is the generic caller-side inspection surface over one persistent
session's on-disk state: the ``session.json`` record, the ``lock.json`` lease,
the ``turns/<turn_id>/`` artifact directories, and the latest turn's
``result.json`` status / ``progress.json`` snapshot.

Contract:

- **Pure local, read-only.** Every call is filesystem reads only — no acpx, no
  AGENT, no subprocess, no lock taken, no artifact mutated. (The existing
  ``SessionRuntime.status`` spawns ``acpx sessions show`` and therefore must
  not sit on a caller's liveness hot path; this module is the non-spawning
  alternative.)
- **Fail closed, never fabricate.** A missing session reports ``exists=False``;
  a corrupt record / lock / result / progress artifact degrades the affected
  field to its conservative unknown value (``state=None``,
  ``holder_liveness="unknown"``, ``status=None``, ``progress=None``) — a value
  the API cannot prove is never reported as healthy. Only an unsafe
  ``session_id`` raises (:class:`~agent_run_supervisor.session.InvalidSessionIdError`),
  because that is caller error, not artifact state.
- **No raw content.** Results carry ids, closed-vocabulary states, counts, and
  the structural :class:`~agent_run_supervisor.hermes_caller.events.ProgressSnapshot`
  only. ``result.json``'s ``final_message`` / prompt text / event bodies are
  never read or surfaced; a ``status`` outside the supervisor's closed
  :class:`~agent_run_supervisor.exit_classifier.AgentRunStatus` vocabulary is
  reported as ``None``, never echoed. :class:`TurnInfo.turn_dir` is the one
  deliberate exception: a **caller-private** filesystem path (same trust level
  as ``SessionTurnOutcome.turn_dir``) for the host's own binding layer — it is
  not part of any public projection and must not be serialized outward.

Lease semantics mirror :meth:`SessionStore.detect_stale_locks` (same expiry
boundary, same K1 liveness classification, same conservative treatment of an
unreadable lock), evaluated for a single session so one corrupt *other* session
can never break this inspection.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_run_supervisor import process_liveness as _liveness
from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.hermes_caller.events import ProgressSnapshot, load_progress
from agent_run_supervisor.session import (
    LOCK_JSON,
    STATE_CLOSED,
    STATE_OPEN,
    InvalidSessionIdError,
    SessionNotFoundError,
    SessionStore,
)
from agent_run_supervisor.session_runtime import TURNS_DIR

RESULT_JSON = "result.json"

#: The closed turn-status vocabulary a ``result.json`` may legitimately carry.
#: Anything else is treated as unreadable (``None``), never echoed.
_KNOWN_TURN_STATUSES = frozenset(status.value for status in AgentRunStatus)

#: Supervisor-generated turn ids are safe path components; anything else under
#: ``turns/`` is debris, not a turn, and is ignored rather than surfaced.
_SAFE_TURN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Exceptions that mean "this artifact cannot be read/parsed" — the degrade-to-
#: unknown set shared by the record / lock / result / progress readers.
_ARTIFACT_READ_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError)


@dataclass(frozen=True)
class TurnInfo:
    """One turn's identity, caller-private artifact directory, and status.

    ``status`` is the ``result.json`` ``status`` when present and inside the
    closed :class:`AgentRunStatus` vocabulary, else ``None`` (in-flight turn,
    missing artifact, or corrupt/off-vocabulary payload). ``turn_dir`` is a
    caller-private path for the host's binding layer — never for serialization
    into public projections.
    """

    turn_id: str
    turn_dir: Path
    status: str | None


@dataclass(frozen=True)
class SessionInspection:
    """Read-only structural view of one local session; no paths, no raw text.

    ``state`` is ``"open"`` / ``"closed"`` from a readable record, else ``None``.
    ``lease_held`` is True only for a present, not-provably-expired lease.
    ``holder_liveness`` is the K1 classification (``alive`` / ``crashed`` /
    ``unknown``) of a present lock's recorded holder set, ``None`` with no lock.
    ``lease_recoverable`` mirrors :meth:`SessionStore.detect_stale_locks`:
    TTL-expired, or provably crashed while reclamation is allowed.
    """

    session_id: str
    exists: bool
    state: str | None
    lease_held: bool
    holder_liveness: str | None
    lease_recoverable: bool
    turn_count: int
    latest_turn_id: str | None
    latest_turn_status: str | None
    progress: ProgressSnapshot | None


def _missing(session_id: str) -> SessionInspection:
    return SessionInspection(
        session_id=session_id,
        exists=False,
        state=None,
        lease_held=False,
        holder_liveness=None,
        lease_recoverable=False,
        turn_count=0,
        latest_turn_id=None,
        latest_turn_status=None,
        progress=None,
    )


def _read_record_state(store: SessionStore, session_id: str) -> str | None:
    """The record's state when readable and in the closed vocabulary, else None.

    Raises :class:`SessionNotFoundError` / :class:`InvalidSessionIdError`
    exactly as :meth:`SessionStore.open_session` does; a parse failure after the
    existence check degrades to ``None`` (the session exists, its record is
    unreadable).
    """

    record = store.open_session(session_id)
    if record.state in (STATE_OPEN, STATE_CLOSED):
        return record.state
    return None


def _turn_dirs(session_dir: Path) -> list[Path]:
    turns_root = session_dir / TURNS_DIR
    if not turns_root.is_dir():
        return []
    try:
        entries = list(turns_root.iterdir())
    except OSError:
        return []
    turn_dirs = [
        entry
        for entry in entries
        if entry.is_dir() and _SAFE_TURN_ID_RE.fullmatch(entry.name) is not None
    ]
    return sorted(turn_dirs, key=lambda path: path.name)


def _read_turn_status(turn_dir: Path) -> str | None:
    result_path = turn_dir / RESULT_JSON
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except _ARTIFACT_READ_ERRORS:
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if not isinstance(status, str) or status not in _KNOWN_TURN_STATUSES:
        return None
    return status


def _read_latest_progress(turn_dir: Path) -> ProgressSnapshot | None:
    try:
        return load_progress(turn_dir)
    except _ARTIFACT_READ_ERRORS:
        return None


def _inspect_lock(
    session_dir: Path,
    probe: _liveness.LivenessProbe,
    moment: _dt.datetime,
) -> tuple[bool, str | None, bool]:
    """``(lease_held, holder_liveness, lease_recoverable)`` for one session.

    Mirrors :meth:`SessionStore.detect_stale_locks` exactly: the ``expires_at
    <= now`` replacement boundary, K1 :func:`process_liveness.classify_lock`
    holder classification (a ``reclaimable=False`` lock is never classified —
    always ``unknown`` — and never liveness-recovered), and the conservative
    unreadable-lock posture (never a live lease, holder unverifiable). Only the
    ``os.kill(pid, 0)`` existence probe is ever issued; nothing is signalled,
    removed, or rewritten.
    """

    lock_path = session_dir / LOCK_JSON
    if not lock_path.exists():
        return False, None, False

    try:
        payload: Any = json.loads(lock_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("lock payload must be a JSON object")
        expires_at = _dt.datetime.fromisoformat(payload["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
        lease_expired = expires_at <= moment
        recovery_allowed = payload.get("reclaimable", True) is not False
        holder_liveness = (
            _liveness.UNKNOWN
            if payload.get("reclaimable") is False
            else _liveness.classify_lock(payload, probe=probe)
        )
    except _ARTIFACT_READ_ERRORS:
        # An unreadable/garbage lock is never a live lease and its holder
        # cannot be verified.
        lease_expired = True
        holder_liveness = _liveness.UNKNOWN
        recovery_allowed = True

    recoverable = lease_expired or (
        recovery_allowed and holder_liveness == _liveness.CRASHED
    )
    return (not lease_expired), holder_liveness, recoverable


def inspect_session(
    sessions_dir: str | Path,
    session_id: str,
    *,
    liveness_probe: _liveness.LivenessProbe | None = None,
    now: _dt.datetime | None = None,
) -> SessionInspection:
    """Inspect one local session's record / lease / turns without spawning.

    Pure local read: no acpx, no AGENT, no subprocess, no lock taken, nothing
    mutated. A missing session yields ``exists=False``; corrupt artifacts
    degrade per the module contract; only an unsafe ``session_id`` raises.
    ``liveness_probe`` / ``now`` are deterministic-test injection seams with the
    same defaults as :class:`SessionStore`.
    """

    base_dir = Path(sessions_dir)
    store = SessionStore(base_dir=base_dir, liveness_probe=liveness_probe)
    try:
        state = _read_record_state(store, session_id)
    except InvalidSessionIdError:
        raise
    except SessionNotFoundError:
        return _missing(session_id)
    except _ARTIFACT_READ_ERRORS:
        state = None

    session_dir = base_dir / session_id
    probe = liveness_probe or _liveness.REAL_PROBE
    moment = now or _dt.datetime.now(tz=_dt.timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    lease_held, holder_liveness, lease_recoverable = _inspect_lock(
        session_dir, probe, moment
    )

    turn_dirs = _turn_dirs(session_dir)
    latest = turn_dirs[-1] if turn_dirs else None
    return SessionInspection(
        session_id=session_id,
        exists=True,
        state=state,
        lease_held=lease_held,
        holder_liveness=holder_liveness,
        lease_recoverable=lease_recoverable,
        turn_count=len(turn_dirs),
        latest_turn_id=latest.name if latest is not None else None,
        latest_turn_status=_read_turn_status(latest) if latest is not None else None,
        progress=_read_latest_progress(latest) if latest is not None else None,
    )


def list_turns(sessions_dir: str | Path, session_id: str) -> tuple[TurnInfo, ...]:
    """Enumerate one session's turns in stable (turn-identity) order.

    Pure local read-only. Supervisor-generated turn ids are UTC-timestamp
    prefixed, so lexicographic turn-id order is creation order; entries under
    ``turns/`` that are not directories or not safe path components are debris
    and are ignored. A missing session fails closed with
    :class:`SessionNotFoundError`; an unsafe id with
    :class:`InvalidSessionIdError`; a session with no turns yields ``()``.
    Event bodies are never read.
    """

    base_dir = Path(sessions_dir)
    store = SessionStore(base_dir=base_dir)
    try:
        store.open_session(session_id)
    except (InvalidSessionIdError, SessionNotFoundError):
        raise
    except _ARTIFACT_READ_ERRORS:
        # A parse failure past the existence check: the session exists with an
        # unreadable record; turn enumeration stays available.
        pass

    return tuple(
        TurnInfo(
            turn_id=turn_dir.name,
            turn_dir=turn_dir,
            status=_read_turn_status(turn_dir),
        )
        for turn_dir in _turn_dirs(base_dir / session_id)
    )


__all__ = [
    "RESULT_JSON",
    "SessionInspection",
    "TurnInfo",
    "inspect_session",
    "list_turns",
]

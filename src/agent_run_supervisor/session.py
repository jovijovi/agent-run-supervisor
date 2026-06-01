"""S1b persistent-session store + lease lock foundation.

This module owns the on-disk *foundation* for persistent sessions: a per-session
directory, an atomic ``session.json`` binding record, a binding-validation gate,
and a lease-based ``lock.json``. It deliberately does **not** launch or talk to a
real acpx persistent session — that runtime is out of scope for S1b. Everything
here is local filesystem state with the same security posture as the event store
(0700 dirs, 0600 files, atomic final writes, ``O_EXCL`` lock creation).
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from agent_run_supervisor.event_store import (
    FILE_MODE,
    atomic_write_json,
    exclusive_create_bytes,
    secure_mkdir,
)
from agent_run_supervisor.policy import policy_hash
from agent_run_supervisor.role import (
    DEFAULT_SESSION_LEASE_SECONDS,
    AgentRoleSpec,
    role_hash,
)
from agent_run_supervisor.workspace import WorkspaceValidationResult, workspace_hash

SCHEMA_VERSION = 1
SESSION_JSON = "session.json"
LOCK_JSON = "lock.json"
LIFECYCLE_GUARD = ".lifecycle.guard"

STATE_OPEN = "open"
STATE_CLOSED = "closed"

# Session ids name a directory under the sessions root, so they must be safe
# path components: start alphanumeric, then alphanumerics/underscore/hyphen. No
# dots (rules out ``.``/``..``), no separators, no whitespace.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


class SessionError(RuntimeError):
    """Base error for the session store."""


class InvalidSessionIdError(SessionError, ValueError):
    """Raised when a session id is unsafe (traversal / illegal characters)."""


class SessionExistsError(SessionError):
    """Raised when creating a session that already exists on disk."""


class SessionNotFoundError(SessionError):
    """Raised when opening / locking a session that does not exist."""


class SessionBindingError(SessionError):
    """Raised when a persisted session no longer matches its role/workspace/policy.

    Fails closed *before* any mutation so a drifted role can never reuse a
    session bound to different permissions, workspace, adapter, or acpx version.
    """


class SessionLockError(SessionError):
    """Raised when a lease lock is held, missing, or released with a wrong token."""


class SessionClosedError(SessionError):
    """Raised when a mutating operation targets an already-closed session.

    Fails closed: once a session record is ``closed``, send/close/abort must
    refuse *before* spawning acpx or mutating turn/management artifacts. Safe
    read-only surfaces (``open``/``status``/``list``) are unaffected.
    """


@dataclass(frozen=True)
class SessionRecord:
    schema_version: int
    session_id: str
    role_id: str
    role_hash: str
    workspace_hash: str
    policy_hash: str
    acpx_version: str
    adapter_agent: str
    effective_cwd: str
    matched_root: str | None
    state: str
    created_at: str
    updated_at: str
    acpx_session_id: str | None = None
    session_name: str | None = None


@dataclass(frozen=True)
class SessionLock:
    token: str
    owner: str
    acquired_at: str
    expires_at: str


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _ensure_aware(moment: _dt.datetime) -> _dt.datetime:
    """Treat naive datetimes as UTC so expiry comparisons never raise."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=_dt.timezone.utc)
    return moment


def _validate_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise InvalidSessionIdError(
            f"session_id {session_id!r} must match {_SESSION_ID_RE.pattern} "
            "(safe path component: no traversal, separators, or whitespace)",
        )
    return session_id


class SessionStore:
    """Filesystem-backed store for persistent-session bindings and lease locks."""

    def __init__(self, base_dir: Path) -> None:
        # ``base_dir`` is the sessions root (e.g. .agent-run-supervisor/sessions),
        # either the default location or a caller-provided directory.
        self.base_dir = Path(base_dir)

    # -- paths ------------------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        return self.base_dir / _validate_session_id(session_id)

    def _require_session_dir(self, session_id: str) -> Path:
        session_dir = self._session_dir(session_id)
        if not (session_dir / SESSION_JSON).exists():
            raise SessionNotFoundError(f"no session {session_id!r} under {self.base_dir}")
        return session_dir

    # -- create / open ----------------------------------------------------

    def create_session(
        self,
        *,
        session_id: str,
        role: AgentRoleSpec,
        workspace_result: WorkspaceValidationResult,
        acpx_session_id: str | None = None,
        session_name: str | None = None,
        now: _dt.datetime | None = None,
    ) -> SessionRecord:
        session_dir = self._session_dir(session_id)
        if (session_dir / SESSION_JSON).exists():
            raise SessionExistsError(f"session {session_id!r} already exists")

        moment = _ensure_aware(now or _utc_now()).isoformat()
        record = SessionRecord(
            schema_version=SCHEMA_VERSION,
            session_id=session_id,
            role_id=role.role_id,
            role_hash=role_hash(role),
            workspace_hash=workspace_hash(role, workspace_result),
            policy_hash=policy_hash(role),
            acpx_version=role.runner.acpx_version,
            adapter_agent=role.runner.adapter_agent,
            effective_cwd=str(workspace_result.effective_cwd),
            matched_root=(
                str(workspace_result.matched_root)
                if workspace_result.matched_root is not None
                else None
            ),
            state="open",
            created_at=moment,
            updated_at=moment,
            acpx_session_id=acpx_session_id,
            session_name=session_name,
        )

        secure_mkdir(session_dir)
        try:
            exclusive_create_bytes(session_dir / SESSION_JSON, _json_bytes(asdict(record)))
        except FileExistsError as exc:
            raise SessionExistsError(f"session {session_id!r} already exists") from exc
        return record

    def open_session(self, session_id: str) -> SessionRecord:
        session_dir = self._require_session_dir(session_id)
        data = _read_json(session_dir / SESSION_JSON)
        return _record_from_dict(data)

    def list_records(self) -> list[SessionRecord]:
        """List every local session record under the sessions root.

        Read-only and local: no acpx launch, no lock taken. Directories without
        a ``session.json`` (and stray files) are skipped. Results are sorted by
        ``session_id`` for deterministic output. A missing root yields ``[]``.
        """
        if not self.base_dir.is_dir():
            return []
        records: list[SessionRecord] = []
        for entry in sorted(self.base_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            session_json = entry / SESSION_JSON
            if not session_json.exists():
                continue
            records.append(_record_from_dict(_read_json(session_json)))
        return records

    # -- lifecycle state --------------------------------------------------

    @contextmanager
    def lifecycle_guard(self, session_id: str):
        """Serialize local lifecycle operations for one session.

        This guard is intentionally separate from the short ``lock.json`` guard:
        ``send`` uses the lease to serialize prompt turns, while ``close`` and
        ``abort`` use this lifecycle guard so a close cannot race an abort or a
        second close into stale open-state management work. The guard is local
        filesystem coordination only; it does not launch or contact acpx.
        """
        session_dir = self._require_session_dir(session_id)
        with _session_lifecycle_guard(session_dir):
            yield

    @staticmethod
    def ensure_open(record: SessionRecord) -> None:
        """Fail closed unless ``record`` is still open.

        Callers invoke this *before* any mutation (send/close/abort) so a closed
        session can never be re-driven into acpx or have artifacts mutated.
        """
        if record.state != STATE_OPEN:
            raise SessionClosedError(
                f"session {record.session_id!r} is {record.state!r}; "
                "mutating operations are refused on non-open sessions",
            )

    def mark_closed(self, session_id: str, *, now: _dt.datetime | None = None) -> SessionRecord:
        """Atomically transition an open session record to ``closed``.

        Serialized under the per-session guard and re-checked there, so two
        concurrent closes cannot both succeed: the second raises
        ``SessionClosedError``. The record is rewritten atomically at 0600.
        """
        session_dir = self._require_session_dir(session_id)
        with _session_lock_guard(session_dir):
            record = _record_from_dict(_read_json(session_dir / SESSION_JSON))
            if record.state == STATE_CLOSED:
                raise SessionClosedError(
                    f"session {session_id!r} is already closed",
                )
            moment = _ensure_aware(now or _utc_now()).isoformat()
            closed = replace(record, state=STATE_CLOSED, updated_at=moment)
            atomic_write_json(session_dir / SESSION_JSON, asdict(closed))
            return closed

    # -- binding gate -----------------------------------------------------

    def validate_binding(
        self,
        record: SessionRecord,
        *,
        role: AgentRoleSpec,
        workspace_result: WorkspaceValidationResult,
    ) -> None:
        """Refuse to proceed unless ``record`` still matches the live role.

        Checks role hash, policy hash, workspace hash, acpx version, and adapter
        before any caller mutates the session. Raises ``SessionBindingError`` on
        the first mismatch.
        """
        mismatches: list[str] = []
        if record.role_hash != role_hash(role):
            mismatches.append("role_hash")
        if record.policy_hash != policy_hash(role):
            mismatches.append("policy_hash")
        if record.workspace_hash != workspace_hash(role, workspace_result):
            mismatches.append("workspace_hash")
        if record.acpx_version != role.runner.acpx_version:
            mismatches.append("acpx_version")
        if record.adapter_agent != role.runner.adapter_agent:
            mismatches.append("adapter_agent")
        if mismatches:
            raise SessionBindingError(
                f"session {record.session_id!r} binding mismatch: "
                f"{', '.join(mismatches)} differ from the current role",
            )

    # -- stale-lock detection (W4, read-only) -----------------------------

    def detect_stale_locks(self, *, now: _dt.datetime | None = None) -> list[dict]:
        """Report lock/lease/temp-debris state per record. Read-only.

        For every local session record, report whether a ``lock.json`` is
        present, whether its lease is **provably expired** (``expires_at <= now``,
        matching :meth:`acquire_lock`'s replacement boundary), and any leftover
        ``.tmp-*`` atomic-write debris in the session directory.

        This launches nothing, takes no lock, signals no process, and never
        removes or rewrites a lock or record. An unreadable/garbage ``lock.json``
        is treated conservatively as expired (it cannot be a live lease). A
        non-expired lock is always reported as live and is never force-broken.
        """
        moment = _ensure_aware(now or _utc_now())
        report: list[dict] = []
        for record in self.list_records():
            session_dir = self.base_dir / record.session_id
            lock_path = session_dir / LOCK_JSON
            lock_present = lock_path.exists()
            lease_expired = False
            if lock_present:
                try:
                    existing = _read_json(lock_path)
                    expires_at = _ensure_aware(
                        _dt.datetime.fromisoformat(existing["expires_at"])
                    )
                    lease_expired = expires_at <= moment
                except (OSError, ValueError, KeyError, TypeError):
                    # An unreadable/garbage lock is never a live lease.
                    lease_expired = True
            tmp_debris = sorted(
                entry.name
                for entry in session_dir.iterdir()
                if entry.name.startswith(".tmp") or entry.name.endswith(".tmp")
            )
            report.append(
                {
                    "session_id": record.session_id,
                    "state": record.state,
                    "lock_present": lock_present,
                    "lease_expired": lease_expired,
                    "tmp_debris": tmp_debris,
                }
            )
        return report

    # -- lease lock -------------------------------------------------------

    def acquire_lock(
        self,
        session_id: str,
        owner: str,
        *,
        now: _dt.datetime | None = None,
        lease_seconds: int = DEFAULT_SESSION_LEASE_SECONDS,
    ) -> SessionLock:
        """Acquire the session's lease lock, creating ``lock.json`` exclusively.

        A non-expired lock blocks (``SessionLockError``). An expired lock is
        replaced deterministically: any expired lease is cleared and a fresh
        lock minted for the new owner.
        """
        session_dir = self._require_session_dir(session_id)
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or lease_seconds <= 0
        ):
            raise SessionLockError("lease_seconds must be a positive integer")
        with _session_lock_guard(session_dir):
            moment = _ensure_aware(now or _utc_now())
            lock_path = session_dir / LOCK_JSON

            if lock_path.exists():
                existing = _read_json(lock_path)
                expires_at = _ensure_aware(_dt.datetime.fromisoformat(existing["expires_at"]))
                if moment < expires_at:
                    raise SessionLockError(
                        f"session {session_id!r} is locked by "
                        f"{existing.get('owner')!r} until {existing['expires_at']}",
                    )
                # Lease expired: clear it while the per-session guard is held, so
                # another acquirer cannot replace the file between our read and unlink.
                lock_path.unlink()

            token = secrets.token_hex(16)
            payload = {
                "token": token,
                "owner": owner,
                "acquired_at": moment.isoformat(),
                "expires_at": (moment + _dt.timedelta(seconds=lease_seconds)).isoformat(),
            }
            try:
                exclusive_create_bytes(lock_path, _json_bytes(payload))
            except FileExistsError as exc:  # lost a race to another acquirer
                raise SessionLockError(f"session {session_id!r} lock already held") from exc
            return SessionLock(
                token=token,
                owner=owner,
                acquired_at=payload["acquired_at"],
                expires_at=payload["expires_at"],
            )

    def release_lock(self, session_id: str, token: str) -> None:
        """Release the lease lock — requires the matching token; else refuse."""
        session_dir = self._require_session_dir(session_id)
        with _session_lock_guard(session_dir):
            lock_path = session_dir / LOCK_JSON
            if not lock_path.exists():
                raise SessionLockError(f"session {session_id!r} holds no lock to release")
            existing = _read_json(lock_path)
            if existing.get("token") != token:
                raise SessionLockError(
                    f"session {session_id!r} release refused: token does not match holder",
                )
            lock_path.unlink()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")


@contextmanager
def _session_lock_guard(session_dir: Path):
    """Serialize lock.json replacement/release within one local session directory.

    POSIX `flock` is advisory, but all SessionStore lock mutations use this guard.
    It closes the expired-lock replacement race where a second acquirer could delete
    a fresh lock created after it read the stale one.
    """
    guard_path = session_dir / ".lock.guard"
    with open(guard_path, "a+b") as guard:
        os.chmod(guard_path, FILE_MODE)
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(guard.fileno(), fcntl.LOCK_UN)


@contextmanager
def _session_lifecycle_guard(session_dir: Path):
    """Serialize close/abort/list-adjacent lifecycle mutations for one session."""
    guard_path = session_dir / LIFECYCLE_GUARD
    with open(guard_path, "a+b") as guard:
        os.chmod(guard_path, FILE_MODE)
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(guard.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _record_from_dict(data: dict[str, Any]) -> SessionRecord:
    fields = {key: data.get(key) for key in SessionRecord.__dataclass_fields__}
    return SessionRecord(**fields)

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
from agent_run_supervisor import process_liveness as _liveness
from agent_run_supervisor.mcp_config import (
    McpConfigBinding,
    McpConfigError,
    resolve_mcp_config,
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
# Durable fence written before required quarantine; cleared only after the
# session state is durably quarantined. Not a new state vocabulary — open
# sessions carrying this fence still refuse STATE_OPEN lease acquisition.
QUARANTINE_PENDING_JSON = "quarantine-pending.json"
QUARANTINE_PENDING_SCHEMA = "ars.quarantine_pending"
QUARANTINE_PENDING_VERSION = 1

STATE_OPEN = "open"
STATE_CLOSED = "closed"
# Native-only persistent state (PRD R5). acpx records never carry it; the
# canonical Native vocabulary maps active <-> open at the storage seam.
STATE_QUARANTINED = "quarantined"

SESSION_KIND_NATIVE = "native"

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


class SessionQuarantinedError(SessionClosedError):
    """Raised when an operation targets a quarantined native session.

    Subclasses :class:`SessionClosedError` so existing acpx catch-sites keep
    catching (acpx records never carry the state). Quarantine is irreversible
    in v1: no API un-quarantines a record.
    """


@dataclass(frozen=True)
class SessionRecord:
    # Field order is unchanged; the persisted key set per record kind is the
    # contract (see ``_record_to_dict``), not dataclass default mechanics.
    # Legacy acpx records always carry the role/policy/acpx fields; native
    # records omit them entirely and never serialize null or sentinel values.
    schema_version: int
    session_id: str
    role_id: str | None = None
    role_hash: str | None = None
    workspace_hash: str | None = None
    policy_hash: str | None = None
    acpx_version: str | None = None
    adapter_agent: str | None = None
    effective_cwd: str | None = None
    matched_root: str | None = None
    state: str = STATE_OPEN
    created_at: str | None = None
    updated_at: str | None = None
    acpx_session_id: str | None = None
    session_name: str | None = None
    # Optional native --mcp-config binding: canonical config path + content
    # SHA captured at create time. Omitted from session.json when unset so
    # pre-feature records keep their exact serialized shape.
    mcp_config_path: str | None = None
    mcp_config_sha256: str | None = None
    # Native session identity/observations (absent => legacy acpx record).
    # All optional, omitted when unset — the exact mcp_config_* pattern.
    session_kind: str | None = None
    native_profile_id: str | None = None
    native_profile_revision: int | None = None
    native_profile_hash: str | None = None
    agent_session_id: str | None = None
    owner: str | None = None
    namespace: str | None = None
    last_effective_model: str | None = None
    last_effective_effort: str | None = None
    quarantine_reason: str | None = None
    quarantined_by_run_id: str | None = None


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

    def __init__(
        self,
        base_dir: Path,
        *,
        liveness_probe: _liveness.LivenessProbe | None = None,
    ) -> None:
        # ``base_dir`` is the sessions root (e.g. .agent-run-supervisor/sessions),
        # either the default location or a caller-provided directory.
        self.base_dir = Path(base_dir)
        # The liveness probe records the holder identity into ``lock.json`` and
        # classifies an encountered holder for safe crash recovery. The default
        # is the real stdlib probe; tests inject a deterministic fake.
        self._liveness_probe = liveness_probe or _liveness.REAL_PROBE

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
        mcp_binding: McpConfigBinding | None = None,
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
            mcp_config_path=mcp_binding.path if mcp_binding is not None else None,
            mcp_config_sha256=mcp_binding.sha256 if mcp_binding is not None else None,
        )

        secure_mkdir(session_dir)
        try:
            exclusive_create_bytes(
                session_dir / SESSION_JSON, _json_bytes(_record_to_dict(record))
            )
        except FileExistsError as exc:
            raise SessionExistsError(f"session {session_id!r} already exists") from exc
        return record

    def create_native_session(
        self,
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
        """Create a Native session record — the only Native creation API.

        Takes **no** ``AgentRoleSpec`` and never accepts, synthesizes, or
        defaults any legacy role hash, policy hash, acpx version, adapter,
        acpx session identifier, or sentinel value. Field provenance is fixed:
        profile identity from the resolved frozen ``AgentProfile``, owner and
        namespace from the authenticated identity frozen in the Run spec, and
        the workspace binding from the spec's validated workspace (the spec's
        binding hash — never the legacy role-based ``workspace_hash``).
        Reachable from Native code solely via the ``native_acp.storage`` seam.
        """
        session_dir = self._session_dir(session_id)
        if (session_dir / SESSION_JSON).exists():
            raise SessionExistsError(f"session {session_id!r} already exists")
        moment = _ensure_aware(now or _utc_now()).isoformat()
        record = SessionRecord(
            schema_version=SCHEMA_VERSION,
            session_id=session_id,
            workspace_hash=workspace_hash,
            effective_cwd=effective_cwd,
            matched_root=matched_root,
            state=STATE_OPEN,  # canonical native 'active', persisted as 'open'
            created_at=moment,
            updated_at=moment,
            session_kind=SESSION_KIND_NATIVE,
            native_profile_id=profile_id,
            native_profile_revision=profile_revision,
            native_profile_hash=profile_hash,
            agent_session_id=None,
            owner=owner,
            namespace=namespace,
        )
        secure_mkdir(session_dir)
        try:
            exclusive_create_bytes(
                session_dir / SESSION_JSON, _json_bytes(_record_to_dict(record))
            )
        except FileExistsError as exc:
            raise SessionExistsError(f"session {session_id!r} already exists") from exc
        return record

    def bind_agent_session(
        self,
        session_id: str,
        *,
        agent_session_id: str,
        now: _dt.datetime | None = None,
    ) -> SessionRecord:
        """Commit the external Agent Session ID exactly once.

        Called after the owning Run's first successful ``session/new``. Any
        second bind — same or different value — is refused; generalized rebind
        stays a non-goal.
        """
        session_dir = self._require_session_dir(session_id)
        with _session_lock_guard(session_dir):
            record = _record_from_dict(_read_json(session_dir / SESSION_JSON))
            if record.session_kind != SESSION_KIND_NATIVE:
                raise SessionBindingError(
                    f"session {session_id!r} is not a native record",
                )
            if record.agent_session_id is not None:
                raise SessionBindingError(
                    f"session {session_id!r} already binds external session "
                    f"{record.agent_session_id!r}; rebind is refused",
                )
            moment = _ensure_aware(now or _utc_now()).isoformat()
            bound = replace(
                record, agent_session_id=agent_session_id, updated_at=moment
            )
            atomic_write_json(session_dir / SESSION_JSON, _record_to_dict(bound))
            return bound

    def write_quarantine_pending(
        self,
        session_id: str,
        *,
        reason: str,
        run_id: str,
        now: _dt.datetime | None = None,
    ) -> None:
        """Persist the durable quarantine-pending fence under the session guard.

        Must precede a required :meth:`mark_quarantined` attempt. The fence
        alone (even while ``session.json`` still says open) blocks
        ``acquire_lock(required_state=STATE_OPEN)``.
        """
        session_dir = self._require_session_dir(session_id)
        moment = _ensure_aware(now or _utc_now()).isoformat()
        payload = {
            "schema": QUARANTINE_PENDING_SCHEMA,
            "version": QUARANTINE_PENDING_VERSION,
            "run_id": run_id,
            "reason": reason,
            "timestamp": moment,
        }
        with _session_lock_guard(session_dir):
            atomic_write_json(session_dir / QUARANTINE_PENDING_JSON, payload)

    def mark_quarantined(
        self,
        session_id: str,
        *,
        reason: str,
        run_id: str,
        now: _dt.datetime | None = None,
    ) -> SessionRecord:
        """Irreversibly quarantine a session (idempotent-safe, first fact wins).

        Serialized under the same per-session guard as :meth:`mark_closed` and
        the lease surface, so state transition vs lease minting is a single
        serialized decision. Never unlinks an existing ``lock.json``: the
        quarantining finalizer's already-held lease stays valid for its own
        finalization writes. There is no un-quarantine API (v1 contract).

        After the quarantined state is durably written, any
        ``quarantine-pending.json`` fence is cleared under the same guard so
        startup reconciliation can converge an interrupted fence by calling
        this method.
        """
        session_dir = self._require_session_dir(session_id)
        with _session_lock_guard(session_dir):
            record = _record_from_dict(_read_json(session_dir / SESSION_JSON))
            if record.state == STATE_QUARANTINED:
                self._clear_quarantine_pending_unlocked(session_dir)
                return record
            moment = _ensure_aware(now or _utc_now()).isoformat()
            quarantined = replace(
                record,
                state=STATE_QUARANTINED,
                quarantine_reason=reason,
                quarantined_by_run_id=run_id,
                updated_at=moment,
            )
            atomic_write_json(
                session_dir / SESSION_JSON, _record_to_dict(quarantined)
            )
            self._clear_quarantine_pending_unlocked(session_dir)
            return quarantined

    def _clear_quarantine_pending_unlocked(self, session_dir: Path) -> None:
        """Clear the fence; caller must already hold the per-session guard."""
        fence = session_dir / QUARANTINE_PENDING_JSON
        try:
            if fence.exists():
                fence.unlink()
        except FileNotFoundError:
            return

    def commit_last_effective(
        self,
        session_id: str,
        *,
        model: str,
        effort: str,
        now: _dt.datetime | None = None,
    ) -> SessionRecord:
        """Atomically record the last exact-readback-proven model/effort pair.

        Commit timing contract: called only after an exact-readback success
        inside the owning Run (post-readback, pre-prompt) and after a proven
        rollback. Agent-side drift is evidence, never a record write.
        """
        session_dir = self._require_session_dir(session_id)
        with _session_lock_guard(session_dir):
            record = _record_from_dict(_read_json(session_dir / SESSION_JSON))
            if record.state == STATE_QUARANTINED:
                raise SessionQuarantinedError(
                    f"session {session_id!r} is quarantined; observations are "
                    "not committed",
                )
            if record.state != STATE_OPEN:
                raise SessionClosedError(
                    f"session {session_id!r} is {record.state!r}; last-effective "
                    "commits require an open session",
                )
            moment = _ensure_aware(now or _utc_now()).isoformat()
            updated = replace(
                record,
                last_effective_model=model,
                last_effective_effort=effort,
                updated_at=moment,
            )
            atomic_write_json(session_dir / SESSION_JSON, _record_to_dict(updated))
            return updated

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
        This is a pre-lock static check; the in-guard ``required_state`` check
        of :meth:`acquire_lock` is the correctness mechanism for native leases.
        """
        if record.state == STATE_QUARANTINED:
            raise SessionQuarantinedError(
                f"session {record.session_id!r} is quarantined; "
                "it refuses all new work",
            )
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
            if record.state == STATE_QUARANTINED:
                # Quarantine is irreversible; close must not replace it.
                raise SessionQuarantinedError(
                    f"session {session_id!r} is quarantined and cannot be closed",
                )
            if record.state == STATE_CLOSED:
                raise SessionClosedError(
                    f"session {session_id!r} is already closed",
                )
            moment = _ensure_aware(now or _utc_now()).isoformat()
            closed = replace(record, state=STATE_CLOSED, updated_at=moment)
            atomic_write_json(session_dir / SESSION_JSON, _record_to_dict(closed))
            return closed

    # -- binding gate -----------------------------------------------------

    def validate_binding(
        self,
        record: SessionRecord,
        *,
        role: AgentRoleSpec,
        workspace_result: WorkspaceValidationResult,
    ) -> McpConfigBinding | None:
        """Refuse to proceed unless ``record`` still matches the live role.

        Checks role hash, policy hash, workspace hash, acpx version, adapter,
        and the optional mcp_config binding before any caller mutates the
        session. Raises ``SessionBindingError`` on the first mismatch.

        The mcp_config check re-reads the declared config file, so callers
        that re-validate under their lease/lifecycle guard also recheck for
        same-path content drift immediately before spawning acpx. On success
        it returns the freshly verified :class:`McpConfigBinding` (``None``
        for unbound roles); callers must compile the spawned argv from this
        binding's canonical path so a declared symlink swapped after
        validation can never redirect what acpx reads.
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
        mcp_mismatches, mcp_binding = self._mcp_binding_state(record, role)
        mismatches.extend(mcp_mismatches)
        if mismatches:
            raise SessionBindingError(
                f"session {record.session_id!r} binding mismatch: "
                f"{', '.join(mismatches)} differ from the current role",
            )
        return mcp_binding

    @staticmethod
    def _mcp_binding_state(
        record: SessionRecord, role: AgentRoleSpec
    ) -> tuple[list[str], McpConfigBinding | None]:
        """Compare the record's persisted mcp_config binding against the live role.

        Fails closed on binding gain/loss, canonical-path change, and content
        SHA drift (same path, different bytes — invisible to ``role_hash``).
        A declared config that can no longer be verified is itself a binding
        failure. Diagnostics carry mismatch names only, never config content.
        Returns the mismatch names plus the freshly verified binding.
        """
        try:
            current = resolve_mcp_config(role)
        except McpConfigError as exc:
            raise SessionBindingError(
                f"session {record.session_id!r} mcp_config binding cannot be "
                f"verified: {exc}",
            ) from exc
        if record.mcp_config_path is None and record.mcp_config_sha256 is None:
            return (["mcp_config_gained"] if current is not None else [], current)
        if current is None:
            return (["mcp_config_lost"], None)
        mismatches: list[str] = []
        if record.mcp_config_path != current.path:
            mismatches.append("mcp_config_path")
        if record.mcp_config_sha256 != current.sha256:
            mismatches.append("mcp_config_sha256")
        return (mismatches, current)

    # -- stale-lock detection (W4, read-only) -----------------------------

    def detect_stale_locks(self, *, now: _dt.datetime | None = None) -> list[dict]:
        """Report lock/lease/liveness/temp-debris state per record. Read-only.

        For every local session record, report whether a ``lock.json`` is
        present, whether its lease is **provably expired** (``expires_at <= now``,
        matching :meth:`acquire_lock`'s replacement boundary), the recorded
        holder's liveness classification (``holder_liveness`` —
        ``alive``/``crashed``/``unknown``, or ``None`` when there is no lock; see
        :func:`process_liveness.classify_lock`), any leftover ``.tmp-*``
        atomic-write debris, and whether the lease is ``recoverable``
        (TTL-expired *or* the reclaimable holder set provably crashed).

        This launches nothing, takes no lock, removes/rewrites nothing, and
        sends **no terminating signal to recorded holders and kills no prior holder** — the only syscall
        it issues against the recorded PID is a no-op ``os.kill(pid, 0)``
        existence probe. An unreadable/garbage ``lock.json`` is treated
        conservatively as expired with an ``unknown`` holder (it cannot be a live
        lease). A non-expired lock with an ``alive``/``unknown`` holder is never
        ``recoverable`` and is never force-broken.
        """
        moment = _ensure_aware(now or _utc_now())
        report: list[dict] = []
        for record in self.list_records():
            session_dir = self.base_dir / record.session_id
            lock_path = session_dir / LOCK_JSON
            lock_present = lock_path.exists()
            lease_expired = False
            holder_liveness: str | None = None
            if lock_present:
                try:
                    existing = _read_json(lock_path)
                    expires_at = _ensure_aware(
                        _dt.datetime.fromisoformat(existing["expires_at"])
                    )
                    lease_expired = expires_at <= moment
                    holder_liveness = (
                        _liveness.UNKNOWN
                        if existing.get("reclaimable") is False
                        else _liveness.classify_lock(existing, probe=self._liveness_probe)
                    )
                except (OSError, ValueError, KeyError, TypeError):
                    # An unreadable/garbage lock is never a live lease, and its
                    # holder cannot be verified.
                    lease_expired = True
                    holder_liveness = _liveness.UNKNOWN
            recovery_allowed = True
            if lock_present:
                try:
                    recovery_allowed = _read_json(lock_path).get("reclaimable", True) is not False
                except (OSError, ValueError, TypeError):
                    recovery_allowed = True
            recoverable = lease_expired or (
                recovery_allowed and holder_liveness == _liveness.CRASHED
            )
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
                    "holder_liveness": holder_liveness,
                    "recoverable": recoverable,
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
        reclaim_crashed: bool = False,
        reclaimable: bool = True,
        holder_kind: str = "supervisor",
        required_state: str | None = None,
    ) -> SessionLock:
        """Acquire the session's lease lock, creating ``lock.json`` exclusively.

        A non-expired lock blocks (``SessionLockError``). An expired lock is
        replaced deterministically: any expired lease is cleared and a fresh
        lock minted for the new owner.

        K1 crash recovery: when ``reclaim_crashed`` is ``True``, a *within-TTL*
        lock whose recorded holder set is **provably crashed** (see
        :func:`process_liveness.classify_lock`) is also reclaimed. Any live
        holder — or any holder whose liveness cannot be proven (foreign host,
        missing identity, unreadable start time, indeterminate probe) — still
        blocks. A composite supervisor+child lock is reclaimed only when both
        identities are provably crashed.
        The default (``False``) preserves the strict TTL-only lease contract.
        Callers may also create an initially unreclaimable lock
        (``reclaimable=False``), used by the runtime while a subprocess holder is
        not yet recorded; such a lock can only be recovered by TTL expiry until
        the caller updates the holder identity.
        Reclamation happens entirely under the per-session guard, so two
        acquirers cannot both reclaim: the loser then sees the fresh live lock.

        ``required_state`` (native callers pass ``STATE_OPEN``) re-reads the
        persisted session state **inside the same guarded critical section**
        that inspects, reclaims, and creates ``lock.json`` — covering the
        fresh-create, TTL-expired, and reclaim paths alike. A mismatch raises
        :class:`SessionQuarantinedError`/:class:`SessionClosedError` and
        neither creates nor unlinks any lock. The default ``None`` preserves
        exact legacy behavior; no acpx call site is edited.
        """
        session_dir = self._require_session_dir(session_id)
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or lease_seconds <= 0
        ):
            raise SessionLockError("lease_seconds must be a positive integer")
        with _session_lock_guard(session_dir):
            if required_state is not None:
                current = _record_from_dict(_read_json(session_dir / SESSION_JSON))
                if current.state != required_state:
                    if current.state == STATE_QUARANTINED:
                        raise SessionQuarantinedError(
                            f"session {session_id!r} is quarantined; no new "
                            "lease is ever minted for it",
                        )
                    raise SessionClosedError(
                        f"session {session_id!r} is {current.state!r}; lease "
                        f"acquisition requires state {required_state!r}",
                    )
                # Fence check is independent of session.json state and of lock
                # TTL/expiry: an interrupted quarantine still fails closed.
                if (
                    required_state == STATE_OPEN
                    and (session_dir / QUARANTINE_PENDING_JSON).exists()
                ):
                    raise SessionQuarantinedError(
                        f"session {session_id!r} has a quarantine-pending fence; "
                        "no new lease is ever minted until quarantine converges",
                    )
            moment = _ensure_aware(now or _utc_now())
            lock_path = session_dir / LOCK_JSON

            if lock_path.exists():
                existing = _read_json(lock_path)
                expires_at = _ensure_aware(_dt.datetime.fromisoformat(existing["expires_at"]))
                if moment < expires_at and not (
                    reclaim_crashed and self._holder_crashed(existing)
                ):
                    raise SessionLockError(
                        f"session {session_id!r} is locked by "
                        f"{existing.get('owner')!r} until {existing['expires_at']}",
                    )
                # Either the lease expired, or its holder is provably crashed and
                # the caller opted into reclamation. Clear it while the
                # per-session guard is held so another acquirer cannot replace the
                # file between our read and unlink.
                lock_path.unlink()

            token = secrets.token_hex(16)
            identity = self._liveness_probe.current()
            payload = {
                "token": token,
                "owner": owner,
                "acquired_at": moment.isoformat(),
                "expires_at": (moment + _dt.timedelta(seconds=lease_seconds)).isoformat(),
                "host": identity.host,
                "pid": identity.pid,
                "process_start": identity.process_start,
                "boot_id": identity.boot_id,
                "holder_kind": holder_kind,
                "reclaimable": bool(reclaimable),
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

    def update_lock_holder(
        self,
        session_id: str,
        token: str,
        *,
        identity: _liveness.ProcessIdentity,
        holder_kind: str,
        reclaimable: bool,
        now: _dt.datetime | None = None,
    ) -> None:
        """Atomically add a child process identity after subprocess spawn.

        The caller must prove ownership with the lease token. Runtime send/close
        acquire an initially unreclaimable supervisor lock, then call this after
        the acpx subprocess has spawned. The supervisor identity remains the
        top-level holder because it still owns artifact mutation after the child
        exits; the child identity is recorded additively. Liveness reclamation is
        safe only when the composite supervisor+child holder set is provably
        crashed.
        """
        session_dir = self._require_session_dir(session_id)
        with _session_lock_guard(session_dir):
            lock_path = session_dir / LOCK_JSON
            if not lock_path.exists():
                raise SessionLockError(f"session {session_id!r} holds no lock to update")
            existing = _read_json(lock_path)
            if existing.get("token") != token:
                raise SessionLockError(
                    f"session {session_id!r} holder update refused: token does not match holder",
                )
            existing.update(
                {
                    "holder_kind": "supervisor_with_subprocess",
                    "reclaimable": bool(reclaimable),
                    "child_host": identity.host,
                    "child_pid": identity.pid,
                    "child_process_start": identity.process_start,
                    "child_boot_id": identity.boot_id,
                    "child_holder_kind": holder_kind,
                    "child_holder_updated_at": _ensure_aware(now or _utc_now()).isoformat(),
                }
            )
            atomic_write_json(lock_path, existing)

    def _holder_crashed(self, lock_data: dict[str, Any]) -> bool:
        """True only when the recorded lock holder set is provably crashed."""
        if lock_data.get("reclaimable") is False:
            return False
        return (
            _liveness.classify_lock(lock_data, probe=self._liveness_probe)
            == _liveness.CRASHED
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


_LEGACY_ONLY_FIELDS = (
    "role_id",
    "role_hash",
    "policy_hash",
    "acpx_version",
    "adapter_agent",
    "acpx_session_id",
)

_NATIVE_FIELDS = (
    "session_kind",
    "native_profile_id",
    "native_profile_revision",
    "native_profile_hash",
    "agent_session_id",
    "owner",
    "namespace",
    "last_effective_model",
    "last_effective_effort",
    "quarantine_reason",
    "quarantined_by_run_id",
)


def _record_to_dict(record: SessionRecord) -> dict[str, Any]:
    """Serialize a record, omitting kind-foreign and unset-optional fields.

    Zero-migration shape: legacy acpx records keep their exact pre-feature
    ``session.json`` key set byte-for-byte (native keys are all unset and
    omitted). Native records omit the legacy role/policy/acpx keys entirely —
    never serialized as null, never given sentinel values — plus any unset
    native optionals (the mcp_config_* omit-when-unset pattern).
    """
    data = asdict(record)
    if record.mcp_config_path is None and record.mcp_config_sha256 is None:
        del data["mcp_config_path"]
        del data["mcp_config_sha256"]
    if record.session_kind == SESSION_KIND_NATIVE:
        for key in _LEGACY_ONLY_FIELDS:
            del data[key]
        if record.session_name is None:
            del data["session_name"]
        for key in _NATIVE_FIELDS:
            if data[key] is None:
                del data[key]
    else:
        for key in _NATIVE_FIELDS:
            del data[key]
    return data


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


def _profile_hash_of(profile: Any) -> Any:
    value = getattr(profile, "profile_hash", None)
    return value() if callable(value) else value


def validate_native_binding(
    record: SessionRecord,
    *,
    profile: Any,
    workspace_result: Any,
    owner: str,
    namespace: str,
    for_load: bool = False,
) -> None:
    """Fail closed unless ``record`` still matches the native Run's identity.

    Hard-fails on profile (id/revision/hash), workspace-binding-hash, owner,
    or namespace mismatch, and on a quarantined record. Model/effort
    differences are **not** a mismatch — a new Run's frozen Spec is the
    legitimate switching input. On the ``session/load`` path
    (``for_load=True``) the committed external ``agent_session_id`` must be
    present. ``profile`` needs ``profile_id``/``revision``/``profile_hash``
    (attribute or zero-arg callable); ``workspace_result`` needs
    ``workspace_hash``. The legacy role-based :meth:`SessionStore.validate_binding`
    is untouched.
    """
    if record.state == STATE_QUARANTINED:
        raise SessionQuarantinedError(
            f"session {record.session_id!r} is quarantined; binding validation "
            "refuses it",
        )
    if record.session_kind != SESSION_KIND_NATIVE:
        raise SessionBindingError(
            f"session {record.session_id!r} is not a native record",
        )
    mismatches: list[str] = []
    if record.native_profile_id != getattr(profile, "profile_id", None):
        mismatches.append("profile_id")
    if record.native_profile_revision != getattr(profile, "revision", None):
        mismatches.append("profile_revision")
    if record.native_profile_hash != _profile_hash_of(profile):
        mismatches.append("profile_hash")
    if record.workspace_hash != getattr(workspace_result, "workspace_hash", None):
        mismatches.append("workspace_hash")
    if record.owner != owner:
        mismatches.append("owner")
    if record.namespace != namespace:
        mismatches.append("namespace")
    if for_load and record.agent_session_id is None:
        mismatches.append("agent_session_id_missing")
    if mismatches:
        raise SessionBindingError(
            f"native session {record.session_id!r} binding mismatch: "
            f"{', '.join(mismatches)}",
        )

"""Durable Session store + lease lock foundation.

This module owns the on-disk *foundation* for Sessions: a per-session directory,
an atomic ``session.json`` identity record, a binding-validation gate, and a
lease-based ``lock.json``. Everything here is local filesystem state with the
same security posture as the event store (0700 dirs, 0600 files, atomic final
writes, ``O_EXCL`` lock creation).

**Runs terminate; Sessions do not close.** There is one Session kind, and a
record carries identity plus continuity evidence and *no lifecycle state*: no
``state``, no ``closed_at``, no close reason or source, and no
ephemeral/persistent flag. What can stop reuse is narrower and independent —
a live lease (one Run at a time) and :data:`quarantine` evidence (continuity was
machine-proven unsafe). Neither is a lifecycle value, and no Run terminal
changes either.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
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
    durable_atomic_write_json,
    durable_unlink,
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
MUTATION_GUARD = ".mutation.guard"
# Durable fence written before required quarantine; cleared only after the
# quarantine evidence is durably committed. A session carrying this fence
# refuses a new lease even while its record still shows no quarantine.
QUARANTINE_PENDING_JSON = "quarantine-pending.json"
QUARANTINE_PENDING_SCHEMA = "ars.quarantine_pending"
QUARANTINE_PENDING_VERSION = 1

SESSION_KIND_NATIVE = "native"

# The complete, closed quarantine vocabulary. Quarantine evidence names a
# *category* and nothing else: no exception text, no agent-authored or remote
# text, no path, no id but the source Run's own. Adding a category is an
# ordinary additive change here; writing free text is not possible at all.
QUARANTINE_DISPATCH_OBSERVATION_LOST = "DISPATCH_OBSERVATION_LOST"
QUARANTINE_DISPATCH_WITHOUT_TERMINAL = "DISPATCH_WITHOUT_TRUSTWORTHY_TERMINAL"
QUARANTINE_UNTRUSTED_TERMINAL_EVIDENCE = "UNTRUSTED_TERMINAL_EVIDENCE"
QUARANTINE_SWITCH_ROLLBACK_UNPROVEN = "SWITCH_ROLLBACK_UNPROVEN"
QUARANTINE_RECONCILED_DISPATCH_WITHOUT_TERMINAL = (
    "RECONCILED_DISPATCH_WITHOUT_TERMINAL"
)

QUARANTINE_REASON_CODES = (
    QUARANTINE_DISPATCH_OBSERVATION_LOST,
    QUARANTINE_DISPATCH_WITHOUT_TERMINAL,
    QUARANTINE_UNTRUSTED_TERMINAL_EVIDENCE,
    QUARANTINE_SWITCH_ROLLBACK_UNPROVEN,
    QUARANTINE_RECONCILED_DISPATCH_WITHOUT_TERMINAL,
)

# The exact key set of the quarantine evidence structure, named once so the
# writer, the reader, and any projection cannot drift.
QUARANTINE_EVIDENCE_FIELDS = ("reason_code", "source_run_id", "recorded_at")

# Session ids name a directory under the sessions root, so they must be safe
# path components: start alphanumeric, then alphanumerics/underscore/hyphen. No
# dots (rules out ``.``/``..``), no separators, no whitespace. This is the one
# definition: the wire validates against it before any storage access, and the
# store validates against it before touching a path.
SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-]*$"
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)


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


class SessionRecordInvalidError(SessionError):
    """Raised when a Native session record is not strictly structurally valid.

    Distinct from :class:`SessionBindingError`: that one means "a readable
    record does not match this Run", while this one means "the record itself is
    not usable evidence at all". Reuse and reconciliation both need the two
    kept apart — a mismatch is a Run-scoped refusal, an invalid record is never
    actionable for anyone.
    """


class SessionLockError(SessionError):
    """Raised when a lease lock is held, missing, or released with a wrong token."""


class SessionQuarantinedError(SessionError):
    """Raised when an operation targets a Session carrying quarantine evidence.

    Quarantine is not a lifecycle state and this is not a "closed" error: the
    Session still exists and stays queryable, it simply refuses new work
    because continuity was machine-proven unsafe. Quarantine is irreversible:
    no API un-quarantines a record.
    """


def is_valid_session_id(session_id: Any) -> bool:
    """True when ``session_id`` is a safe Session-store path component.

    Pure predicate over :data:`SESSION_ID_PATTERN`. Callers that must refuse
    *before* touching storage — the wire parser and the Spec validator — ask
    this; the store itself raises :class:`InvalidSessionIdError` instead.
    """
    return type(session_id) is str and _SESSION_ID_RE.match(session_id) is not None


# The prospective-Session derivation. A create names its Session by a pure,
# tagged function of the Run identity that already derives from the
# authenticated ``(principal_id, request_id)`` pair — so repeating a request
# converges on the same Run *and* the same Session, and a lost response can
# never split a caller's context into two Sessions. The tag and prefix keep the
# Run and Session namespaces disjoint; the result is a safe path component by
# construction, because it is a fixed prefix plus hex and carries no caller text.
SESSION_ID_PREFIX = "sess-"
_SESSION_ID_DERIVATION_TAG = b"ars-session-id-v1\x00"


def derive_session_id_for_run(run_id: str) -> str:
    """The prospective Session id of the create Run named ``run_id``.

    The single definition. Admission derives it from the admission key through
    the Run id, the durable submission validator re-derives it from the Run id
    it attests, and ``RunTask`` derives it from the Run id it was given — three
    readers, one rule, so they cannot disagree about which Session a create
    would produce.
    """
    if type(run_id) is not str or not run_id:
        raise InvalidSessionIdError("run_id must be a non-empty str")
    material = _SESSION_ID_DERIVATION_TAG + run_id.encode("utf-8")
    return SESSION_ID_PREFIX + hashlib.sha256(material).hexdigest()[:32]


def build_quarantine_evidence(
    *, reason_code: str, run_id: str, recorded_at: str
) -> dict[str, Any]:
    """The one constructor for quarantine evidence, validated at the seam.

    ``reason_code`` must be a member of :data:`QUARANTINE_REASON_CODES`, so an
    exception message, an agent-authored string, or a path can never become
    durable evidence — the refusal is structural rather than filtered.
    """
    if reason_code not in QUARANTINE_REASON_CODES:
        raise ValueError(
            "quarantine reason_code must be one of "
            f"{QUARANTINE_REASON_CODES}",
        )
    if type(run_id) is not str or not run_id:
        raise ValueError("quarantine source_run_id must be a non-empty str")
    return {
        "reason_code": reason_code,
        "source_run_id": run_id,
        "recorded_at": recorded_at,
    }


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
    # Optional safety evidence, never a lifecycle value: ``None`` or exactly
    # ``{reason_code, source_run_id, recorded_at}`` built by
    # :func:`build_quarantine_evidence`. A record carrying it still exists and
    # stays queryable; it refuses new Runs.
    quarantine: dict[str, Any] | None = None
    # The registered agent this Session belongs to. Reuse under a different
    # agent fails closed before the lease and before ``session/load``.
    native_agent_id: str | None = None
    # The **operator's** continuity epoch, copied from the registry entry that
    # admitted the creating Run. Nothing derives, increments, or infers it, and
    # absent is not 1 — so adding the field for the first time cuts continuity,
    # which is the same deliberate act as a bump.
    native_session_epoch: int | None = None
    # The last ``initialize`` observation of this Session, recorded so the next
    # Run can *report* drift. Deliberately not identity: nothing compares these
    # to admit, refuse, or reuse, and a Run whose agent reports something new
    # binds exactly as before. They exist because "the agent changed under an
    # unchanged registered command" is worth telling an operator and worth
    # nothing as a gate.
    native_last_agent_info_name: str | None = None
    native_last_agent_info_version: str | None = None
    native_last_advertised_capabilities: list[str] | None = None
    # Retired ARS-derived identity fields of the Binding line. They are still
    # *read* so a pre-reset record stays owner-scoped status/list/close-readable
    # — but a record carrying any of them is refused for ``session/load``,
    # because the identity it was created under no longer exists here.
    native_adapter_contract_hash: str | None = None
    session_compatibility_epoch: int | None = None
    native_agent_registration_hash: str | None = None


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
        agent_session_id: str,
        agent_id: str | None = None,
        session_epoch: int | None = None,
        now: _dt.datetime | None = None,
    ) -> SessionRecord:
        """Atomically create ONE fully bound Native Session record.

        The external AGENT Session ID is **required**, so no code path can
        produce a record with a missing external id and bind it later: there is
        no provisional record, no unbound record, and no second write to
        complete one. The caller reaches here only after ``session/new`` has
        returned, and a crash before this single exclusive write leaves no
        Session at all — which is exactly what makes a failed creation
        distinguishable from an existing resumable Session.

        Takes **no** ``AgentRoleSpec`` and never accepts, synthesizes, or
        defaults any legacy role hash, policy hash, acpx version, adapter,
        acpx session identifier, or sentinel value. Field provenance is fixed:
        profile identity from the resolved frozen ``AgentProfile``, owner and
        namespace from the authenticated identity frozen in the Run spec, and
        the workspace binding from the spec's validated workspace (the spec's
        binding hash — never the legacy role-based ``workspace_hash``).
        Reachable from Native code solely via the ``native_acp.storage`` seam.
        """
        if type(agent_session_id) is not str or not agent_session_id:
            raise SessionBindingError(
                f"session {session_id!r} requires a non-empty external session id",
            )
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
            created_at=moment,
            updated_at=moment,
            session_kind=SESSION_KIND_NATIVE,
            native_profile_id=profile_id,
            native_profile_revision=profile_revision,
            native_profile_hash=profile_hash,
            native_agent_id=agent_id,
            native_session_epoch=session_epoch,
            agent_session_id=agent_session_id,
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

    def write_quarantine_pending(
        self,
        session_id: str,
        *,
        reason_code: str,
        run_id: str,
        now: _dt.datetime | None = None,
    ) -> None:
        """Persist the durable quarantine-pending fence under the session guard.

        Must precede a required :meth:`mark_quarantined` attempt. The fence
        alone — even while the record still carries no quarantine evidence —
        blocks ``acquire_lock(refuse_quarantined=True)``.
        """
        session_dir = self._require_session_dir(session_id)
        moment = _ensure_aware(now or _utc_now()).isoformat()
        evidence = build_quarantine_evidence(
            reason_code=reason_code, run_id=run_id, recorded_at=moment
        )
        payload = {
            "schema": QUARANTINE_PENDING_SCHEMA,
            "version": QUARANTINE_PENDING_VERSION,
            **evidence,
        }
        with _session_lock_guard(session_dir):
            durable_atomic_write_json(session_dir / QUARANTINE_PENDING_JSON, payload)

    def mark_quarantined(
        self,
        session_id: str,
        *,
        reason_code: str,
        run_id: str,
        now: _dt.datetime | None = None,
    ) -> SessionRecord:
        """Irreversibly record quarantine evidence (idempotent, first fact wins).

        Serialized under the same per-session guard as the lease surface, so
        writing evidence versus minting a lease is a single serialized
        decision. Never unlinks an existing ``lock.json``: the quarantining
        finalizer's already-held lease stays valid for its own finalization
        writes. There is no un-quarantine API.

        Quarantine is evidence, not a state transition: the Session still
        exists and stays queryable afterwards. After the evidence is durably
        written, any ``quarantine-pending.json`` fence is cleared under the
        same guard, so startup reconciliation converges an interrupted fence by
        calling this method.
        """
        session_dir = self._require_session_dir(session_id)
        # Validate the category *before* taking the guard: a rejected code must
        # never fence, never write, and never block another acquirer.
        moment = _ensure_aware(now or _utc_now()).isoformat()
        evidence = build_quarantine_evidence(
            reason_code=reason_code, run_id=run_id, recorded_at=moment
        )
        with _session_lock_guard(session_dir):
            record = _record_from_dict(_read_json(session_dir / SESSION_JSON))
            if record.quarantine is not None:
                self._clear_quarantine_pending_unlocked(session_dir)
                return record
            quarantined = replace(
                record,
                quarantine=evidence,
                updated_at=moment,
            )
            durable_atomic_write_json(
                session_dir / SESSION_JSON, _record_to_dict(quarantined)
            )
            self._clear_quarantine_pending_unlocked(session_dir)
            return quarantined

    def has_quarantine_pending(self, session_id: str) -> bool:
        """Read-only: is the durable quarantine-pending fence present?

        Pure inspection — it takes no guard, mutates nothing, and answers
        ``False`` for an absent session, an invalid id, or any unreadable path,
        so a caller can decide whether a fence still needs converging without
        writing one to find out.
        """
        try:
            path = self._session_dir(session_id) / QUARANTINE_PENDING_JSON
        except InvalidSessionIdError:
            return False
        try:
            os.lstat(path)
        except OSError:
            return False
        return True

    def _clear_quarantine_pending_unlocked(self, session_dir: Path) -> None:
        """Clear the fence; caller must already hold the per-session guard.

        Uses crash-durable unlink (unlink then parent directory fsync). Missing
        fence is idempotent. Durability failures propagate — callers must not
        claim terminal success or release the lease when clear fails.
        """
        durable_unlink(session_dir / QUARANTINE_PENDING_JSON)

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
            if record.quarantine is not None:
                raise SessionQuarantinedError(
                    f"session {session_id!r} is quarantined; observations are "
                    "not committed",
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

    def commit_last_observation(
        self,
        session_id: str,
        *,
        agent_info_name: str,
        agent_info_version: str,
        advertised_capabilities: tuple[str, ...],
        now: _dt.datetime | None = None,
    ) -> SessionRecord:
        """Record the ``initialize`` observation this Session last accepted.

        Evidence, never identity. It is written only after the observation has
        already passed the contract checks, and it is read only to decide
        whether the *next* Run should emit a drift warning — nothing compares it
        to admit, refuse, or reuse anything, and no epoch is derived from it.

        Every field here is **child-controlled free text**: ``agentInfo`` is
        whatever the agent chose to say about itself, and the advertised
        capability *keys* are whatever it chose to advertise. ``judge_initialize``
        deliberately gates neither — a self-report is evidence, so there is
        nothing to check it against. The seam still refuses a non-``str``,
        because ``session.json`` is durable JSON and an object with a hostile
        ``__str__`` is exactly what must not be serialized into it.
        """
        for label, candidate in (
            ("agent_info_name", agent_info_name),
            ("agent_info_version", agent_info_version),
            *(("advertised_capability", item) for item in advertised_capabilities),
        ):
            if type(candidate) is not str:
                raise TypeError(
                    f"native session observation {label} must be a str"
                )
        session_dir = self._require_session_dir(session_id)
        with _session_lock_guard(session_dir):
            record = _record_from_dict(_read_json(session_dir / SESSION_JSON))
            if record.quarantine is not None:
                raise SessionQuarantinedError(
                    f"session {session_id!r} is quarantined; observations are "
                    "not committed",
                )
            moment = _ensure_aware(now or _utc_now()).isoformat()
            updated = replace(
                record,
                native_last_agent_info_name=agent_info_name,
                native_last_agent_info_version=agent_info_version,
                native_last_advertised_capabilities=list(advertised_capabilities),
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

    # -- local mutation serialization -------------------------------------

    @contextmanager
    def mutation_guard(self, session_id: str):
        """Serialize local non-lease mutations for one session.

        Deliberately separate from the short ``lock.json`` guard: prompt turns
        serialize on the lease, while local artifact mutations that are not a
        Run serialize here, so two of them cannot interleave. The guard is
        local filesystem coordination only; it launches and contacts nothing.
        """
        session_dir = self._require_session_dir(session_id)
        with _session_mutation_guard(session_dir):
            yield

    @staticmethod
    def ensure_usable(record: SessionRecord) -> None:
        """Fail closed unless ``record`` accepts new work.

        A Session always exists and is always resumable, so there is exactly
        one thing to refuse: durable quarantine evidence. This is a pre-lock
        static check; the in-guard ``refuse_quarantined`` check of
        :meth:`acquire_lock` is the correctness mechanism.
        """
        if record.quarantine is not None:
            raise SessionQuarantinedError(
                f"session {record.session_id!r} is quarantined; "
                "it refuses all new work",
            )

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
        that re-validate under their lease/mutation guard also recheck for
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
                    "quarantined": record.quarantine is not None,
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
        refuse_quarantined: bool = False,
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

        ``refuse_quarantined`` (native callers pass ``True``) re-reads the
        persisted quarantine evidence **inside the same guarded critical
        section** that inspects, reclaims, and creates ``lock.json`` — covering
        the fresh-create, TTL-expired, and reclaim paths alike. Evidence, or an
        unconverged quarantine fence, raises :class:`SessionQuarantinedError`
        and neither creates nor unlinks any lock. The default ``False``
        preserves exact legacy behavior; no acpx call site is edited.
        """
        session_dir = self._require_session_dir(session_id)
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or lease_seconds <= 0
        ):
            raise SessionLockError("lease_seconds must be a positive integer")
        with _session_lock_guard(session_dir):
            if refuse_quarantined:
                current = _record_from_dict(_read_json(session_dir / SESSION_JSON))
                if current.quarantine is not None:
                    raise SessionQuarantinedError(
                        f"session {session_id!r} is quarantined; no new "
                        "lease is ever minted for it",
                    )
                # Fence check is independent of the record's own evidence and
                # of lock TTL/expiry: an interrupted quarantine fails closed.
                if (session_dir / QUARANTINE_PENDING_JSON).exists():
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
    "native_adapter_contract_hash",
    "agent_session_id",
    "owner",
    "namespace",
    "last_effective_model",
    "last_effective_effort",
    "session_compatibility_epoch",
    "native_agent_id",
    "native_agent_registration_hash",
    "native_session_epoch",
    "native_last_agent_info_name",
    "native_last_agent_info_version",
    "native_last_advertised_capabilities",
)

# The retired ARS-derived identity fields, named once so the reader, the
# refusal, and any audit all mean the same three things. A record carrying any
# of them was created under an identity model this runtime deliberately deleted.
LEGACY_SESSION_IDENTITY_FIELDS = (
    "native_adapter_contract_hash",
    "session_compatibility_epoch",
    "native_agent_registration_hash",
)

# The stable code a legacy-identity record is refused with. It names the class
# of record and nothing about its contents.
LEGACY_SESSION_IDENTITY = "LEGACY_SESSION_IDENTITY"


def _record_to_dict(record: SessionRecord) -> dict[str, Any]:
    """Serialize a record, omitting kind-foreign and unset-optional fields.

    Legacy acpx records keep their historical ``session.json`` key set minus the
    deleted lifecycle ``state`` (native keys are all unset and omitted). Native
    records omit the legacy role/policy/acpx keys entirely — never serialized as
    null, never given sentinel values — plus any unset native optionals (the
    mcp_config_* omit-when-unset pattern).

    ``quarantine`` is deliberately **kind-neutral**: it is the only remaining
    refusal a Session can carry, so a record of either kind must be able to hold
    it. Making it native-only would silently drop the evidence on write and
    leave the acpx runtime with nothing to fail closed on.
    """
    data = asdict(record)
    if record.mcp_config_path is None and record.mcp_config_sha256 is None:
        del data["mcp_config_path"]
        del data["mcp_config_sha256"]
    if record.quarantine is None:
        del data["quarantine"]
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
def _session_mutation_guard(session_dir: Path):
    """Serialize local non-lease artifact mutations for one session."""
    guard_path = session_dir / MUTATION_GUARD
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


def _quarantine_is_exact(value: Any) -> bool:
    """True for ``None`` or exactly the bounded categorical evidence shape."""
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != set(QUARANTINE_EVIDENCE_FIELDS):
        return False
    if value.get("reason_code") not in QUARANTINE_REASON_CODES:
        return False
    return _nonempty_str(value.get("source_run_id")) and _nonempty_str(
        value.get("recorded_at")
    )


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def validate_native_session_record(
    record: SessionRecord, *, expected_session_id: str
) -> None:
    """Strict structural validation of an already-read Native session record.

    Structure and *self-identity* — whether the record matches a particular
    Run's profile, workspace, and era stays :func:`validate_native_binding`'s
    job. One definition of "strictly readable record" serves both the load-only
    reuse gate and reconciliation's actionability predicate, so the two can
    never disagree about which records exist as usable evidence.

    ``expected_session_id`` is required and must equal the record's own
    ``session_id``: a record is located by directory name, so a document whose
    internal identity names a *different* Session is a conflict, not a
    relabelled record. Neither reuse nor reconciliation may take the directory
    name as sufficient. The failure names field categories, never values.
    """
    problems: list[str] = []
    if record.schema_version != SCHEMA_VERSION:
        problems.append("schema_version")
    if not _nonempty_str(record.session_id):
        problems.append("session_id")
    elif record.session_id != expected_session_id:
        problems.append("session_id_conflict")
    if record.session_kind != SESSION_KIND_NATIVE:
        problems.append("session_kind")
    if not _quarantine_is_exact(record.quarantine):
        problems.append("quarantine")
    for name in ("owner", "namespace", "workspace_hash"):
        if not _nonempty_str(getattr(record, name)):
            problems.append(name)
    if not _nonempty_str(record.native_profile_id):
        problems.append("native_profile_id")
    if not _nonempty_str(record.native_profile_hash):
        problems.append("native_profile_hash")
    if not isinstance(record.native_profile_revision, int) or isinstance(
        record.native_profile_revision, bool
    ):
        problems.append("native_profile_revision")
    if record.agent_session_id is not None and not _nonempty_str(
        record.agent_session_id
    ):
        problems.append("agent_session_id")
    if problems:
        raise SessionRecordInvalidError(
            f"native session record is invalid: {', '.join(problems)}",
        )


def read_native_session_record(
    store: SessionStore, session_id: str
) -> SessionRecord | None:
    """An already-existing, strictly readable Native record, else ``None``.

    Never raises and never creates, repairs, or reopens anything: an absent,
    unreadable, malformed, non-native, or internally conflicting record is
    simply not a record for the caller's purposes. Reconciliation's
    actionability predicate is built on exactly this, and it is the same gate
    the load-only reuse path applies.
    """
    try:
        record = store.open_session(session_id)
    except Exception:
        # Absent, unreadable, or not a safe id — all "no usable record".
        return None
    try:
        validate_native_session_record(record, expected_session_id=session_id)
    except SessionRecordInvalidError:
        return None
    return record


def validate_native_binding(
    record: SessionRecord,
    *,
    profile: Any,
    workspace_result: Any,
    owner: str,
    namespace: str,
    for_load: bool = False,
    expected_epoch: int | None = None,
    expected_agent_id: str | None = None,
) -> None:
    """Fail closed unless ``record`` still matches the native Run's identity.

    Identity is exactly: ``agent_id``, profile identity (id/revision/hash),
    owner, namespace, ``workspace_hash``, and the operator's optional
    ``session_epoch``. Hard-fails on any mismatch and on a quarantined record.
    Model/effort differences are **not** a mismatch — a new Run's frozen Spec is
    the legitimate switching input. On the ``session/load`` path
    (``for_load=True``) the committed external ``agent_session_id`` must also be
    present. ``profile`` needs ``profile_id``/``revision``/``profile_hash``
    (attribute or zero-arg callable); ``workspace_result`` needs
    ``workspace_hash``. The legacy role-based
    :meth:`SessionStore.validate_binding` is untouched.

    Epoch equality is **symmetric**, on purpose: a record that has an epoch is
    refused by a Run that has none, and a Run that has one is refused by a
    record that has none. An epoch is an identity, not an ordering — lower and
    higher are both refused — and that symmetry is what makes rollback
    fail-closed in both directions, with no shim and no alias.

    Nothing here derives an epoch. It arrives from the operator's registry entry
    or it is absent, and absent is not 1.

    A record carrying any retired ARS-derived identity field is refused outright
    with a stable code. Those Sessions remain owner-scoped
    ``status``/``list``-readable; only binding to a Run is refused,
    because the identity they were created under no longer exists here.

    Called before the lease is acquired and long before ``session/load``, and
    there is no ``session/new`` fallback on any reuse path.
    """
    if record.quarantine is not None:
        raise SessionQuarantinedError(
            f"session {record.session_id!r} is quarantined; binding validation "
            "refuses it",
        )
    if record.session_kind != SESSION_KIND_NATIVE:
        raise SessionBindingError(
            f"session {record.session_id!r} is not a native record",
        )
    legacy = [
        name
        for name in LEGACY_SESSION_IDENTITY_FIELDS
        if getattr(record, name, None) is not None
    ]
    if legacy:
        # The field *names* are the evidence; their values never are.
        raise SessionBindingError(
            f"native session {record.session_id!r} refused "
            f"[{LEGACY_SESSION_IDENTITY}]: it carries retired identity "
            f"field(s) {', '.join(legacy)}",
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
    if record.native_agent_id != expected_agent_id:
        mismatches.append("agent_id")
    if record.native_session_epoch != expected_epoch:
        mismatches.append("session_epoch")
    if for_load and record.agent_session_id is None:
        mismatches.append("agent_session_id_missing")
    if mismatches:
        raise SessionBindingError(
            f"native session {record.session_id!r} binding mismatch: "
            f"{', '.join(mismatches)}",
        )

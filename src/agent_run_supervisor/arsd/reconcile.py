"""Startup-only exhaustive reconciliation over Native run/session roots.

Runs to completion strictly before the socket is bound. Scope is exclusively
the Native roots obtained through ``native_acp.storage``; legacy ``runs/`` /
``sessions/`` stores are never read or written. Never sends a prompt, never
calls ACP, never opens a registry, never mints/unlinks/rewrites a lock, never
creates, reopens or repairs a Session, and never rewrites an existing terminal
``result.json``.

The algorithm is: **classify every input first**, attribute identity from the
ordered authority (a valid Spec, else a valid submission, else nothing), decide
one row of the exhaustive first-match table, and only then write — fence,
quarantine, progress, terminal — in that order. Absent and corrupt are never
collapsed: they select different rows, and only one of them is safe.
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_run_supervisor.event_store import EventStore, atomic_write_json
from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.run_task import (
    DISPATCH_STARTED_MARKER,
    PROMPT_ACCEPTED_MARKER,
)
from agent_run_supervisor.native_acp.spec import (
    SPEC_SCHEMA_VERSION,
    launch_hash_of_payload,
    launch_payload_shape_is_exact,
    spec_hash_of_payload,
    spec_payload_shape_is_exact,
)
from agent_run_supervisor.result import build_result_payload
from agent_run_supervisor.session import (
    STATE_OPEN,
    STATE_QUARANTINED,
    SessionStore,
    read_native_session_record,
)

from . import admission

_LOGGER = logging.getLogger(__name__)

_RESULT_NAME = "result.json"
_PROGRESS_NAME = "progress.json"
_SPEC_NAME = "spec.json"
_LAUNCH_NAME = "launch.json"

_DETAIL_RECONCILED_UNKNOWN = "RECONCILED_UNKNOWN"
_DETAIL_RECONCILED_PRE_DISPATCH = "RECONCILED_PRE_DISPATCH"
_QUARANTINE_REASON = (
    "reconciled: dispatched run without a trustworthy ACP terminal"
)

# Stable categorical refusal rules. Each names a rule and nothing else: no run
# id, no session id, no path, no artifact bytes.
REFUSE_UNATTRIBUTABLE_UNKNOWN_TERMINAL = "RECONCILE_UNATTRIBUTABLE_UNKNOWN_TERMINAL"
REFUSE_CORRUPT_TERMINAL = "RECONCILE_CORRUPT_TERMINAL"
REFUSE_UNATTRIBUTABLE_DISPATCH = "RECONCILE_UNATTRIBUTABLE_DISPATCH"
REFUSE_CORRUPT_LAUNCH = "RECONCILE_CORRUPT_LAUNCH"
REFUSE_CORRUPT_SPEC = "RECONCILE_CORRUPT_SPEC"
REFUSE_LAUNCH_WITHOUT_SPEC = "RECONCILE_LAUNCH_WITHOUT_SPEC"
REFUSE_CORRUPT_SUBMISSION = "RECONCILE_CORRUPT_SUBMISSION"

_REFUSAL_BY_ROW = {
    3: REFUSE_UNATTRIBUTABLE_UNKNOWN_TERMINAL,
    4: REFUSE_CORRUPT_TERMINAL,
    6: REFUSE_UNATTRIBUTABLE_DISPATCH,
    8: REFUSE_CORRUPT_LAUNCH,
    9: REFUSE_CORRUPT_SPEC,
    10: REFUSE_LAUNCH_WITHOUT_SPEC,
    11: REFUSE_CORRUPT_SUBMISSION,
}

_REUSE_MODES = ("reuse", "none")

DOC = storage.JsonDocumentKind


class ReconciliationError(RuntimeError):
    """Sanitized fail-closed reconciliation refusal; daemon must not listen."""


class TerminalClass(enum.Enum):
    """The four terminal states row selection distinguishes."""

    TRUSTED_TERMINAL = "trusted_terminal"
    TRUSTED_UNKNOWN = "trusted_unknown"
    CORRUPT = "corrupt"
    ABSENT = "absent"


TERMINAL_CLASS_BY_NAME = {member.value: member for member in TerminalClass}


class Outcome(enum.Enum):
    """The complete permitted outcome vocabulary — there is no fifth value."""

    AUTHORITATIVE_TERMINAL = "authoritative_terminal"
    UNKNOWN_QUARANTINE = "unknown_quarantine"
    PRE_DISPATCH_FAILED = "pre_dispatch_failed"
    REFUSE_TO_LISTEN = "refuse_to_listen"


@dataclass(frozen=True)
class Attribution:
    """Identity taken from the ordered attribution authority."""

    session_id: str
    owner: str
    namespace: str
    source: str  # "spec" | "submission"


@dataclass(frozen=True)
class RowDecision:
    row: int
    outcome: Outcome


@dataclass(frozen=True)
class RunFacts:
    """Everything classification produced, before any write.

    The first six fields are the **only** inputs to row selection, and
    ``actionable`` is the only Session-derived one of them.
    """

    terminal: TerminalClass
    dispatch: bool
    spec: storage.JsonDocumentKind
    launch: storage.JsonDocumentKind
    submission: storage.JsonDocumentKind
    actionable: bool
    attribution: Attribution | None = None
    terminal_payload: dict[str, Any] | None = None
    run_id: str = ""


# -- classification ----------------------------------------------------------


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


DISPATCH_MARKER_NAMES = (DISPATCH_STARTED_MARKER, PROMPT_ACCEPTED_MARKER)


def _dispatch_present(run_dir: Path) -> bool:
    """Dispatch is PRESENT when ``lstat`` finds *either* marker name.

    Type and contents are irrelevant: a symlink, a directory, a malformed
    marker, and any I/O result that cannot prove clean absence all count as
    present. Only a clean ``ENOENT`` on **both** names proves no dispatch could
    have happened — a prompt may have reached the agent, and nothing about a
    marker's shape can make that less true.
    """
    for name in DISPATCH_MARKER_NAMES:
        try:
            os.lstat(run_dir / name)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        return True
    return False


def _classify_terminal(
    run_dir: Path, *, run_id: str
) -> tuple[TerminalClass, dict[str, Any] | None]:
    state = storage.read_native_terminal_result(
        run_dir / _RESULT_NAME, run_id=run_id
    )
    if state.kind is storage.NativeTerminalKind.ABSENT:
        return TerminalClass.ABSENT, None
    if state.kind is storage.NativeTerminalKind.INVALID:
        return TerminalClass.CORRUPT, None
    payload = state.payload or {}
    if payload.get("status") == AgentRunStatus.UNKNOWN.value:
        return TerminalClass.TRUSTED_UNKNOWN, payload
    return TerminalClass.TRUSTED_TERMINAL, payload


def _spec_attribution(payload: Any, *, run_id: str) -> Attribution | None:
    """Strict Spec validation, or ``None``.

    Three independent gates, all required. The document must carry **exactly**
    the production projection for the frozen schema version; its embedded
    ``spec_hash`` must equal the seal recomputed by the production rule, so a
    hash-covered field mutated behind an unchanged hash is corrupt; and the
    identity fields it attributes must themselves be well formed. A Spec that
    cannot answer all of those is not authority for anything.
    """
    if not spec_payload_shape_is_exact(payload):
        return None
    if payload.get("schema_version") != SPEC_SCHEMA_VERSION:
        return None
    if payload.get("run_id") != run_id or not _nonempty_str(run_id):
        return None
    embedded = payload.get("spec_hash")
    if not _nonempty_str(embedded):
        return None
    if embedded != spec_hash_of_payload(payload):
        return None
    if not _nonempty_str(payload.get("launch_spec_hash")):
        return None
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        return None
    owner = identity.get("owner")
    namespace = identity.get("namespace")
    if not _nonempty_str(owner) or not _nonempty_str(namespace):
        return None
    session = payload.get("session")
    if not isinstance(session, dict):
        return None
    reuse = session.get("reuse")
    if reuse not in _REUSE_MODES:
        return None
    ars_session_id = session.get("ars_session_id")
    if reuse == "reuse":
        if not _nonempty_str(ars_session_id):
            return None
        session_id = ars_session_id
    else:
        # A non-reuse request may still carry an id: the request model requires
        # one only in the reuse direction, and the writer copies whatever it
        # was given. Runtime Session selection ignores it, so the durable
        # validator accepts the document and derives only the deterministic
        # ephemeral id — the stray value never becomes attribution authority.
        # Its *type* domain is unchanged: null, or a non-empty string.
        if ars_session_id is not None and not _nonempty_str(ars_session_id):
            return None
        session_id = f"{run_id}-ephemeral"
    agent = payload.get("agent")
    if not isinstance(agent, dict):
        return None
    if not _nonempty_str(agent.get("profile_id")):
        return None
    if not _nonempty_str(agent.get("profile_hash")):
        return None
    if not _plain_int(agent.get("profile_revision")):
        return None
    grant = payload.get("execution_grant")
    if not isinstance(grant, dict):
        return None
    for field in ("grant_ref", "grant_hash", "role_hash"):
        if not _nonempty_str(grant.get(field)):
            return None
    if not isinstance(grant.get("capabilities"), list):
        return None
    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        return None
    for field in ("canonical_root", "cwd", "workspace_hash"):
        if not _nonempty_str(workspace.get(field)):
            return None
    if not isinstance(payload.get("input_refs"), list):
        return None
    return Attribution(
        session_id=session_id, owner=owner, namespace=namespace, source="spec"
    )


def _classify_launch(
    run_dir: Path, *, spec_state: storage.JsonDocumentState
) -> storage.JsonDocumentKind:
    """Structural and self-sealed always; Spec-bound when a valid Spec exists.

    The seal is **recomputed** from the launch body by the production rule and
    must equal the embedded one, so body tampering behind an unchanged seal is
    corrupt. When a valid Spec exists, that same recomputed seal must also
    equal the Spec's reference — the Spec seals the launch it actually sealed,
    never whatever the launch document claims about itself.
    """
    state = storage.classify_json_document(run_dir / _LAUNCH_NAME)
    if state.kind is not DOC.VALID:
        return state.kind
    payload = state.payload or {}
    if not launch_payload_shape_is_exact(payload):
        return DOC.CORRUPT
    recomputed = launch_hash_of_payload(payload)
    if payload.get("launch_spec_hash") != recomputed:
        return DOC.CORRUPT
    if spec_state.kind is DOC.VALID and isinstance(spec_state.payload, dict):
        if recomputed != spec_state.payload.get("launch_spec_hash"):
            # A present referenced launch that fails its Spec's hash is not the
            # allowed absent crash point.
            return DOC.CORRUPT
    return DOC.VALID


def _is_actionable(
    attribution: Attribution | None, *, session_store: SessionStore
) -> bool:
    """An already-existing, strictly readable, matching, usable record.

    Never creates, reopens, or repairs anything: a missing, unreadable,
    foreign, or closed record simply is not actionable.
    """
    if attribution is None:
        return False
    record = read_native_session_record(session_store, attribution.session_id)
    if record is None:
        return False
    if record.owner != attribution.owner or record.namespace != attribution.namespace:
        return False
    return record.state in (STATE_OPEN, STATE_QUARANTINED)


def classify_run(run_dir: Path, *, session_store: SessionStore) -> RunFacts:
    """Classify every input of one Run before any write happens."""
    run_dir = Path(run_dir)
    run_id = run_dir.name
    terminal, terminal_payload = _classify_terminal(run_dir, run_id=run_id)
    dispatch = _dispatch_present(run_dir)

    spec_state = storage.classify_json_document(run_dir / _SPEC_NAME)
    spec_attribution = (
        _spec_attribution(spec_state.payload, run_id=run_id)
        if spec_state.kind is DOC.VALID
        else None
    )
    spec_kind = (
        DOC.VALID
        if spec_attribution is not None
        else (DOC.ABSENT if spec_state.kind is DOC.ABSENT else DOC.CORRUPT)
    )
    launch_kind = _classify_launch(
        run_dir,
        spec_state=(
            spec_state
            if spec_kind is DOC.VALID
            else storage.JsonDocumentState(spec_kind)
        ),
    )
    submission_state = admission.classify_submission(run_dir, run_id=run_id)

    # Ordered attribution authority: a valid Spec is authoritative and the
    # submission is ignored even when absent, corrupt, or conflicting; a valid
    # submission is a fallback only when the Spec is not valid. Launch records,
    # result fields, directory names, progress, events, locks, and marker
    # contents are never attribution authority.
    attribution = spec_attribution
    if attribution is None and submission_state.kind is DOC.VALID:
        found = submission_state.attribution
        assert found is not None
        attribution = Attribution(
            session_id=found.session_id,
            owner=found.owner,
            namespace=found.namespace,
            source="submission",
        )

    return RunFacts(
        terminal=terminal,
        dispatch=dispatch,
        spec=spec_kind,
        launch=launch_kind,
        submission=submission_state.kind,
        actionable=_is_actionable(attribution, session_store=session_store),
        attribution=attribution,
        terminal_payload=terminal_payload,
        run_id=run_id,
    )


# -- the exhaustive first-match table ---------------------------------------


def select_row(facts: RunFacts) -> RowDecision:
    """Assign exactly one row and one outcome to every artifact combination.

    First match wins, top to bottom, over `T × D × S × L × U` plus the
    actionability predicate. The table is total: every one of the 216
    combinations reaches exactly one ``return``.
    """
    if facts.terminal is TerminalClass.TRUSTED_TERMINAL:
        # 1 — authoritative terminal, preserved byte-for-byte, no mutation.
        return RowDecision(1, Outcome.AUTHORITATIVE_TERMINAL)
    if facts.terminal is TerminalClass.TRUSTED_UNKNOWN:
        if facts.actionable:
            # 2 — authoritative unknown; converge fence, quarantine, progress.
            return RowDecision(2, Outcome.UNKNOWN_QUARANTINE)
        # 3 — no substitute owner or Session may be invented.
        return RowDecision(3, Outcome.REFUSE_TO_LISTEN)
    if facts.terminal is TerminalClass.CORRUPT:
        # 4 — never rewrite, never delete, never write progress or a result.
        return RowDecision(4, Outcome.REFUSE_TO_LISTEN)
    if facts.dispatch:
        if facts.actionable:
            # 5 — a prompt may have been dispatched: unknown + quarantine.
            return RowDecision(5, Outcome.UNKNOWN_QUARANTINE)
        # 6 — no terminal is fabricated without durable Session attribution.
        return RowDecision(6, Outcome.REFUSE_TO_LISTEN)
    if facts.spec is DOC.VALID:
        if facts.launch in (DOC.VALID, DOC.ABSENT):
            # 7 — a missing launch is the allowed crash point between the
            # ordered spec.json and launch.json writes.
            return RowDecision(7, Outcome.PRE_DISPATCH_FAILED)
        # 8 — a present referenced launch that fails validation is not.
        return RowDecision(8, Outcome.REFUSE_TO_LISTEN)
    if facts.spec is DOC.CORRUPT:
        # 9 — a valid submission cannot rehabilitate corrupt Spec evidence.
        return RowDecision(9, Outcome.REFUSE_TO_LISTEN)
    if facts.launch in (DOC.VALID, DOC.CORRUPT):
        # 10 — any launch without the Spec it must follow violates the seal.
        return RowDecision(10, Outcome.REFUSE_TO_LISTEN)
    # 11 — nothing but the submission is left to scope the reservation.
    if facts.submission is DOC.VALID:
        return RowDecision(11, Outcome.PRE_DISPATCH_FAILED)
    if facts.submission is DOC.ABSENT:
        return RowDecision(11, Outcome.PRE_DISPATCH_FAILED)
    return RowDecision(11, Outcome.REFUSE_TO_LISTEN)


# -- effects -----------------------------------------------------------------


def reconcile(supervisor_root: Path) -> None:
    """Converge every native run dir under ``supervisor_root`` (idempotent)."""
    session_store = storage.native_session_store(supervisor_root)
    event_store = storage.native_event_store(supervisor_root)
    # Read-only inspection may inform the structured log only — never mutation.
    try:
        stale = session_store.detect_stale_locks()
    except Exception:
        _LOGGER.exception("arsd.reconcile: detect_stale_locks failed")
    else:
        if stale:
            _LOGGER.info(
                "arsd.reconcile: stale-lock inspection entries=%s",
                len(stale),
            )
    _reconcile_stores(session_store=session_store, event_store=event_store)


def _reconcile_stores(
    *, session_store: SessionStore, event_store: EventStore
) -> None:
    runs_root = Path(event_store.base_dir)
    if not runs_root.is_dir():
        return
    for entry in sorted(runs_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.is_symlink():
            continue
        _reconcile_run(run_dir=entry, session_store=session_store)


def _reconcile_run(*, run_dir: Path, session_store: SessionStore) -> None:
    # Classification and attribution complete before any mutation.
    facts = classify_run(run_dir, session_store=session_store)
    decision = select_row(facts)
    _apply(facts, decision, run_dir=run_dir, session_store=session_store)


def _apply(
    facts: RunFacts,
    decision: RowDecision,
    *,
    run_dir: Path,
    session_store: SessionStore,
) -> None:
    """Write ordering: fence → quarantine → progress → terminal, last."""
    run_id = facts.run_id or run_dir.name
    if decision.outcome is Outcome.AUTHORITATIVE_TERMINAL:
        return

    if decision.outcome is Outcome.REFUSE_TO_LISTEN:
        if decision.row == 4 and facts.dispatch and facts.actionable:
            # A corrupt terminal still fences a possibly dispatched Run before
            # refusing; the corrupt bytes are never rewritten or deleted, and
            # neither progress nor a result is ever written for this row.
            _fence_and_quarantine(session_store, facts=facts, run_id=run_id)
        _refuse(decision.row, run_id=run_id)

    if decision.outcome is Outcome.UNKNOWN_QUARANTINE:
        _fence_and_quarantine(session_store, facts=facts, run_id=run_id)
        _ensure_terminal_progress(run_dir, AgentRunStatus.UNKNOWN.value)
        if decision.row == 5:
            _write_terminal_result(
                run_dir,
                run_id=run_id,
                status=AgentRunStatus.UNKNOWN,
                detail_code=_DETAIL_RECONCILED_UNKNOWN,
                session_id=facts.attribution.session_id if facts.attribution else None,
            )
        return

    # Pre-dispatch: one failed terminal, no Session mutation, no progress. The
    # Session (if any) stays reusable — reconciliation certifies nothing about
    # it and a later reuse must still pass the load proof.
    _write_terminal_result(
        run_dir,
        run_id=run_id,
        status=AgentRunStatus.FAILED,
        detail_code=_DETAIL_RECONCILED_PRE_DISPATCH,
        session_id=facts.attribution.session_id if facts.attribution else None,
    )


def _refuse(row: int, *, run_id: str) -> None:
    rule = _REFUSAL_BY_ROW[row]
    _LOGGER.warning("arsd.reconcile: refusing to listen rule=%s run=%s", rule, run_id)
    raise ReconciliationError(f"reconciliation refused [{rule}]")


def _fence_and_quarantine(
    session_store: SessionStore, *, facts: RunFacts, run_id: str
) -> None:
    """Fence first, then quarantine; both idempotent, both fail closed.

    The durable quarantine-pending fence lands before the state transition, so
    a crash between them still leaves a non-leasable Session and the next
    startup converges the same row. A failure here propagates: no progress and
    no terminal may follow an unconverged quarantine.
    """
    attribution = facts.attribution
    assert attribution is not None  # actionable implies attributed
    session_id = attribution.session_id
    record = read_native_session_record(session_store, session_id)
    if record is not None and record.state == STATE_QUARANTINED:
        # Already converged: an already-quarantined Session is a no-op on every
        # rerun. The one exception is a fence left behind by a crash between
        # the two writes — clearing it is what ``mark_quarantined`` already
        # does idempotently, and it needs no second fence to do it.
        if session_store.has_quarantine_pending(session_id):
            session_store.mark_quarantined(
                session_id, reason=_QUARANTINE_REASON, run_id=run_id
            )
        return
    session_store.write_quarantine_pending(
        session_id, reason=_QUARANTINE_REASON, run_id=run_id
    )
    session_store.mark_quarantined(
        session_id, reason=_QUARANTINE_REASON, run_id=run_id
    )


def _ensure_terminal_progress(run_dir: Path, state: str) -> None:
    """Write progress only when the recorded disposition differs."""
    path = run_dir / _PROGRESS_NAME
    existing = storage.classify_json_document(path).payload
    if isinstance(existing, dict) and existing.get("state") == state:
        return
    last_seq = 0
    event_count = 0
    if isinstance(existing, dict):
        if isinstance(existing.get("last_seq"), int) and not isinstance(
            existing.get("last_seq"), bool
        ):
            last_seq = existing["last_seq"]
        if isinstance(existing.get("event_count"), int) and not isinstance(
            existing.get("event_count"), bool
        ):
            event_count = existing["event_count"]
        else:
            event_count = last_seq
    payload = {
        "schema_version": 1,
        "state": state,
        "last_seq": last_seq,
        "event_count": event_count,
        "updated_at": _utc_now_iso(),
    }
    atomic_write_json(path, payload)


def _write_terminal_result(
    run_dir: Path,
    *,
    run_id: str,
    status: AgentRunStatus,
    detail_code: str,
    session_id: str | None,
) -> None:
    result_path = run_dir / _RESULT_NAME
    try:
        os.lstat(result_path)
    except OSError:
        pass
    else:
        return
    payload = build_result_payload(
        run_id=run_id,
        status=status,
        origin="supervisor",
        detail_code=detail_code,
        retryable=_RETRYABLE_DEFAULT[status],
        exit_code=None,
        signal=None,
        stop_reason=None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=run_dir,
        raw_event_path="events.jsonl",
    )
    if session_id:
        payload["session_id"] = session_id
    try:
        storage.write_once_json(result_path, payload)
    except FileExistsError:
        # Concurrent/first-fact-wins: never rewrite an existing terminal.
        return


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


__all__ = [
    "Attribution",
    "Outcome",
    "ReconciliationError",
    "RowDecision",
    "RunFacts",
    "TERMINAL_CLASS_BY_NAME",
    "TerminalClass",
    "classify_run",
    "reconcile",
    "select_row",
]

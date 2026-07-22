"""Startup-only idempotent reconciliation over Native run/session roots.

Runs to completion strictly before the socket is bound. Scope is exclusively
the Native roots obtained through ``native_acp.storage``; legacy ``runs/`` /
``sessions/`` stores are never read or written. Never sends a prompt, never
calls ACP, never mints/unlinks/rewrites a lock, and never rewrites an existing
terminal ``result.json``.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.event_store import EventStore, atomic_write_json
from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.run_task import DISPATCH_STARTED_MARKER
from agent_run_supervisor.result import build_result_payload, validate_native_terminal_result
from agent_run_supervisor.session import SessionNotFoundError, SessionStore

_LOGGER = logging.getLogger(__name__)

_RESULT_NAME = "result.json"
_PROGRESS_NAME = "progress.json"
_SUBMISSION_NAME = "submission.json"
_SPEC_NAME = "spec.json"

_DETAIL_RECONCILED_UNKNOWN = "RECONCILED_UNKNOWN"
_DETAIL_RECONCILED_PRE_DISPATCH = "RECONCILED_PRE_DISPATCH"
_QUARANTINE_REASON = (
    "reconciled: dispatched run without a trustworthy ACP terminal"
)
_UNTRUSTED_TERMINAL_MESSAGE = (
    "untrusted or corrupt terminal evidence; refusing reconciliation"
)
# Conservative bound for startup terminal evidence (1 MiB); reads request +1.
_MAX_TERMINAL_RESULT_BYTES = 1 * 1024 * 1024


class ReconciliationError(RuntimeError):
    """Sanitized fail-closed reconciliation refusal; daemon must not listen."""


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
    run_id = run_dir.name
    result_path = run_dir / _RESULT_NAME
    marker_present = (run_dir / DISPATCH_STARTED_MARKER).is_file()
    submission = _read_json_object(run_dir / _SUBMISSION_NAME)
    spec = _read_json_object(run_dir / _SPEC_NAME)
    session_id = _resolve_session_id(
        run_id=run_id, submission=submission, spec=spec
    )

    if _path_exists_nofollow(result_path):
        existing = _validate_terminal_result(result_path, run_id=run_id)
        if existing is None:
            _refuse_untrusted_terminal(
                session_store=session_store,
                session_id=session_id,
                run_id=run_id,
                marker_present=marker_present,
            )
        if existing.get("status") == AgentRunStatus.UNKNOWN.value and marker_present:
            _ensure_session_quarantined(
                session_store, session_id=session_id, run_id=run_id
            )
            _ensure_terminal_progress(run_dir, AgentRunStatus.UNKNOWN.value)
        return

    if marker_present:
        # Side effects first; terminal write-once last (plan §8 commit order).
        _ensure_session_quarantined(
            session_store, session_id=session_id, run_id=run_id
        )
        _ensure_terminal_progress(run_dir, AgentRunStatus.UNKNOWN.value)
        _write_terminal_result(
            run_dir,
            run_id=run_id,
            status=AgentRunStatus.UNKNOWN,
            detail_code=_DETAIL_RECONCILED_UNKNOWN,
            session_id=session_id,
        )
        return

    # Pre-dispatch (submission and/or spec) or bare reservation — no
    # Session/progress side effects; session stays reusable/untouched and no
    # ownership is invented for a bare dir. Terminal write-once only.
    expose_session = session_id if (submission is not None or spec is not None) else None
    _write_terminal_result(
        run_dir,
        run_id=run_id,
        status=AgentRunStatus.FAILED,
        detail_code=_DETAIL_RECONCILED_PRE_DISPATCH,
        session_id=expose_session,
    )


def _refuse_untrusted_terminal(
    *,
    session_store: SessionStore,
    session_id: str | None,
    run_id: str,
    marker_present: bool,
) -> None:
    """Quarantine when dispatched; always fail closed before progress/result."""
    if marker_present:
        _ensure_session_quarantined(
            session_store, session_id=session_id, run_id=run_id
        )
    raise ReconciliationError(_UNTRUSTED_TERMINAL_MESSAGE)


def _validate_terminal_result(
    path: Path, *, run_id: str
) -> dict[str, Any] | None:
    """Return a trusted Native terminal object, or ``None`` if unusable.

    Opens with ``O_RDONLY|O_NOFOLLOW``, validates the opened fd is a regular
    file, and reads through that fd with a finite cap. Delegates schema trust
    to :func:`validate_native_terminal_result` (complete Native base shape,
    supported statuses only, exact retryability, strict types). Any
    fstat/read/close ``OSError`` is untrusted evidence (``None``); the fd is
    always closed.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        return None

    raw: bytes | None = None
    try:
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raw = None
            elif st.st_size > _MAX_TERMINAL_RESULT_BYTES:
                raw = None
            else:
                raw = _read_fd_capped(fd, _MAX_TERMINAL_RESULT_BYTES + 1)
                if len(raw) > _MAX_TERMINAL_RESULT_BYTES:
                    raw = None
        except OSError:
            raw = None
    finally:
        try:
            os.close(fd)
        except OSError:
            raw = None

    if raw is None:
        return None

    try:
        text = raw.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None
    return validate_native_terminal_result(payload, run_id=run_id)


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


def _path_exists_nofollow(path: Path) -> bool:
    try:
        os.lstat(path)
    except OSError:
        return False
    return True


def _resolve_session_id(
    *,
    run_id: str,
    submission: Mapping[str, Any] | None,
    spec: Mapping[str, Any] | None,
) -> str | None:
    if submission is not None:
        reuse = submission.get("session_reuse")
        if reuse == "reuse":
            ars_session_id = submission.get("ars_session_id")
            return ars_session_id if isinstance(ars_session_id, str) else None
        return f"{run_id}-ephemeral"
    if spec is not None:
        session = spec.get("session")
        if isinstance(session, dict):
            reuse = session.get("reuse")
            if reuse == "reuse":
                ars_session_id = session.get("ars_session_id")
                return ars_session_id if isinstance(ars_session_id, str) else None
            return f"{run_id}-ephemeral"
    return None


def _ensure_session_quarantined(
    session_store: SessionStore,
    *,
    session_id: str | None,
    run_id: str,
) -> None:
    if not session_id:
        return
    try:
        session_store.mark_quarantined(
            session_id, reason=_QUARANTINE_REASON, run_id=run_id
        )
    except SessionNotFoundError:
        # Missing session dir is non-fatal (plan §8); later durable steps may
        # still converge the run without inventing a session record.
        _LOGGER.warning(
            "arsd.reconcile: session %s missing while reconciling run %s",
            session_id,
            run_id,
        )
    except Exception:
        # Fail closed: never write progress/result after a quarantine miss —
        # an irreversible unknown must not coexist with an unquarantined session.
        _LOGGER.exception(
            "arsd.reconcile: quarantine failed for session %s run %s",
            session_id,
            run_id,
        )
        raise


def _ensure_terminal_progress(run_dir: Path, state: str) -> None:
    """Write progress only when the recorded disposition differs."""
    path = run_dir / _PROGRESS_NAME
    existing = _read_json_object(path)
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
    if _path_exists_nofollow(result_path):
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


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()

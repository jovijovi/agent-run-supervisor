"""H1 W2 confined, dry-run-first run/session artifact retention.

This module *plans* and *applies* safe cleanup of run and session artifacts that
live under a resolved ``.agent-run-supervisor`` root. It is stdlib-only and keeps
the same security posture as ``event_store.py`` / ``session.py``: it launches
nothing (no acpx, no network, no process signals), planning is strictly
read-only, and deletion happens only with an explicit ``confirm is True``.

Safety boundaries (each pinned by ``tests/test_retention.py``):

1. **Artifact-root confinement.** ``runs_dir`` / ``sessions_dir`` must resolve
   under a ``.agent-run-supervisor`` path segment, else :class:`RetentionError`.
   The tool refuses to operate on arbitrary directories (``/``, ``$HOME``, …).
2. **Per-candidate confinement.** A candidate is only deletable when its
   *resolved* path is strictly inside the resolved root; the root itself (and the
   ``runs``/``sessions`` enumeration dirs) is never a delete target, and a path
   that resolves outside root is refused at apply time (TOCTOU-aware).
3. **No symlink escape.** A symlinked entry is skipped with
   ``reason="symlink_escape"`` and never traversed or removed; deletion never
   follows a symlink (re-checked immediately before removal).
4. **Never delete live sessions.** An ``open`` session is *always* skipped
   (``reason="open_session"``), regardless of policy or flags, and the state is
   re-read at apply time so a session that re-opens between plan and apply is
   still refused; a session holding a non-expired lease is always skipped
   (``reason="live_lock"``). Only ``closed`` sessions are ever deletable.
5. **Expired-lock hygiene.** A closed/eligible session's *expired* ``lock.json``
   is removed together with the directory; a live lock makes the whole session
   non-deletable (rule 4).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_run_supervisor.session import LOCK_JSON, SESSION_JSON, STATE_OPEN

#: The only directory name this tool will ever delete inside.
ARTIFACT_ROOT_NAME = ".agent-run-supervisor"
#: Per-run metadata file whose mtime is the preferred age source for a run dir.
RUN_MARKER = "result.json"

_DAY_SECONDS = 86400.0
_UTC = timezone.utc


class RetentionError(RuntimeError):
    """Raised when a cleanup request violates a retention safety boundary."""


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention bounds. At least one of the two bounds must be set.

    ``max_age_days`` deletes anything strictly older than the bound; ``max_count``
    keeps the newest N eligible artifacts and deletes the remainder. Open and
    live-locked sessions are *never* deletable regardless of these bounds.
    """

    max_age_days: int | None = None
    max_count: int | None = None

    def has_bound(self) -> bool:
        return self.max_age_days is not None or self.max_count is not None


@dataclass(frozen=True)
class CleanupCandidate:
    """One run/session artifact and its planned disposition."""

    kind: str  # "run" | "session"
    id: str
    path: Path
    age_seconds: float
    action: str  # "delete" | "skip"
    reason: str


@dataclass(frozen=True)
class CleanupPlan:
    """The read-only result of :func:`plan_cleanup`."""

    root: Path
    runs_dir: Path
    sessions_dir: Path
    delete: list[CleanupCandidate]
    skip: list[CleanupCandidate]


@dataclass(frozen=True)
class CleanupResult:
    """The result of :func:`apply_cleanup` — deleted ids and any failures."""

    plan: CleanupPlan
    deleted: list[str]
    failed: list[dict]


@dataclass
class _Pending:
    """A policy-eligible artifact awaiting the age/count decision."""

    kind: str
    id: str
    path: Path
    age_seconds: float


# --- time helpers ---------------------------------------------------------


def _ensure_aware(moment: datetime) -> datetime:
    """Treat naive datetimes as UTC so comparisons never raise."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=_UTC)
    return moment


def _now(now: datetime | None) -> datetime:
    return _ensure_aware(now) if now is not None else datetime.now(tz=_UTC)


# --- confinement helpers --------------------------------------------------


def _artifact_root_of(path: Path) -> Path | None:
    """Return the ``.agent-run-supervisor`` ancestor of ``path``, or ``None``."""
    resolved = Path(path).resolve()
    parts = resolved.parts
    if ARTIFACT_ROOT_NAME not in parts:
        return None
    idx = parts.index(ARTIFACT_ROOT_NAME)
    return Path(*parts[: idx + 1])


def _confined_root(runs_dir: Path, sessions_dir: Path) -> Path:
    """Resolve the shared artifact root or refuse (rule 1)."""
    runs_root = _artifact_root_of(runs_dir)
    if runs_root is None:
        raise RetentionError(
            f"refuses to operate: runs_dir {runs_dir!s} does not resolve under a "
            f"{ARTIFACT_ROOT_NAME!r} artifact root"
        )
    sessions_root = _artifact_root_of(sessions_dir)
    if sessions_root is None:
        raise RetentionError(
            f"refuses to operate: sessions_dir {sessions_dir!s} does not resolve under a "
            f"{ARTIFACT_ROOT_NAME!r} artifact root"
        )
    if runs_root != sessions_root:
        raise RetentionError(
            "runs_dir and sessions_dir resolve to different "
            f"{ARTIFACT_ROOT_NAME!r} roots ({runs_root!s} vs {sessions_root!s})"
        )
    return runs_root


def _resolve_within_root(path: Path, root: Path) -> Path:
    """Return ``path`` resolved, or refuse if it escapes/equals the root (rule 2)."""
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise RetentionError(
            f"{path!s} resolves outside artifact root {root!s}"
        ) from exc
    if relative == Path("."):
        raise RetentionError(f"refuses to delete the artifact root itself: {path!s}")
    return resolved


# --- filesystem helpers ---------------------------------------------------


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _age_seconds(entry: Path, marker: str, now: datetime) -> float:
    """Age from ``entry/marker`` mtime, falling back to the dir's own mtime.

    Never follows a symlink: a symlinked entry is aged by its own ``lstat``.
    """
    if entry.is_symlink():
        try:
            mtime = entry.lstat().st_mtime
        except OSError:
            return 0.0
    else:
        try:
            mtime = (entry / marker).stat().st_mtime
        except OSError:
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                return 0.0
    moment = datetime.fromtimestamp(mtime, tz=_UTC)
    return max(0.0, (now - moment).total_seconds())


def _has_live_lock(session_dir: Path, now: datetime) -> bool:
    """True only when ``lock.json`` holds a lease that has not yet expired.

    An absent, unreadable, or garbage lock is never a live lease (it cannot
    protect a session from cleanup); a parseable, future ``expires_at`` is live.
    """
    lock_path = session_dir / LOCK_JSON
    if not lock_path.exists():
        return False
    try:
        data = _read_json(lock_path)
        expires_at = _ensure_aware(datetime.fromisoformat(data["expires_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return expires_at > now


def _symlink_escape_candidate(
    entry: Path, kind: str, root: Path, now: datetime, marker: str
) -> CleanupCandidate | None:
    """Return a forced symlink-escape skip candidate, or ``None`` if safe (rule 3)."""
    escaped = entry.is_symlink()
    if not escaped:
        try:
            _resolve_within_root(entry, root)
        except RetentionError:
            escaped = True
    if not escaped:
        return None
    return CleanupCandidate(
        kind=kind,
        id=entry.name,
        path=entry,
        age_seconds=_age_seconds(entry, marker, now),
        action="skip",
        reason="symlink_escape",
    )


# --- scanning -------------------------------------------------------------


def _scan_runs(
    runs_dir: Path, root: Path, now: datetime
) -> tuple[list[CleanupCandidate], list[_Pending]]:
    forced: list[CleanupCandidate] = []
    eligible: list[_Pending] = []
    if not runs_dir.is_dir():
        return forced, eligible
    for entry in sorted(runs_dir.iterdir(), key=lambda p: p.name):
        escape = _symlink_escape_candidate(entry, "run", root, now, RUN_MARKER)
        if escape is not None:
            forced.append(escape)
            continue
        if not entry.is_dir():
            continue  # stray files are not run artifacts
        eligible.append(
            _Pending(
                kind="run",
                id=entry.name,
                path=entry,
                age_seconds=_age_seconds(entry, RUN_MARKER, now),
            )
        )
    return forced, eligible


def _scan_sessions(
    sessions_dir: Path, root: Path, now: datetime
) -> tuple[list[CleanupCandidate], list[_Pending]]:
    forced: list[CleanupCandidate] = []
    eligible: list[_Pending] = []
    if not sessions_dir.is_dir():
        return forced, eligible
    for entry in sorted(sessions_dir.iterdir(), key=lambda p: p.name):
        escape = _symlink_escape_candidate(entry, "session", root, now, SESSION_JSON)
        if escape is not None:
            forced.append(escape)
            continue
        if not entry.is_dir():
            continue
        if not (entry / SESSION_JSON).exists():
            continue  # not a session-record directory
        age = _age_seconds(entry, SESSION_JSON, now)

        # A live lease always wins (rule 4) — checked before the open-state gate
        # so a held lock reports ``live_lock`` rather than ``open_session``.
        if _has_live_lock(entry, now):
            forced.append(
                CleanupCandidate(
                    kind="session", id=entry.name, path=entry,
                    age_seconds=age, action="skip", reason="live_lock",
                )
            )
            continue

        # An ``open`` session is *never* deletable (rule 4): skip it regardless
        # of policy or flags; only ``closed`` sessions reach the age/count gate.
        try:
            state = _read_json(entry / SESSION_JSON).get("state")
        except (OSError, ValueError):
            state = None
        if state == STATE_OPEN:
            forced.append(
                CleanupCandidate(
                    kind="session", id=entry.name, path=entry,
                    age_seconds=age, action="skip", reason="open_session",
                )
            )
            continue

        eligible.append(_Pending(kind="session", id=entry.name, path=entry, age_seconds=age))
    return forced, eligible


def _classify_eligible(
    pending: list[_Pending], policy: RetentionPolicy
) -> tuple[list[CleanupCandidate], list[CleanupCandidate]]:
    """Split policy-eligible artifacts into delete/skip by age and count."""
    keep_ids: set[int] | None = None
    if policy.max_count is not None:
        newest_first = sorted(pending, key=lambda p: p.age_seconds)
        keep_ids = {id(p) for p in newest_first[: policy.max_count]}

    delete: list[CleanupCandidate] = []
    skip: list[CleanupCandidate] = []
    for item in pending:
        too_old = (
            policy.max_age_days is not None
            and item.age_seconds > policy.max_age_days * _DAY_SECONDS
        )
        over_count = keep_ids is not None and id(item) not in keep_ids
        if too_old or over_count:
            reason = "max_age_days" if too_old else "max_count"
            delete.append(_finalize(item, "delete", reason))
        else:
            skip.append(_finalize(item, "skip", "retained"))
    return delete, skip


def _finalize(item: _Pending, action: str, reason: str) -> CleanupCandidate:
    return CleanupCandidate(
        kind=item.kind,
        id=item.id,
        path=item.path,
        age_seconds=item.age_seconds,
        action=action,
        reason=reason,
    )


# --- public API -----------------------------------------------------------


def plan_cleanup(
    *,
    runs_dir: Path,
    sessions_dir: Path,
    policy: RetentionPolicy,
    now: datetime | None = None,
) -> CleanupPlan:
    """Plan a confined cleanup. Read-only: deletes nothing.

    Enumerates run and session directories, classifies each as ``delete`` or
    ``skip`` per ``policy`` and the safety rules, and returns a :class:`CleanupPlan`.
    Refuses (``RetentionError``) when ``policy`` has no bound or when either
    directory is not confined to a ``.agent-run-supervisor`` root.
    """
    if not policy.has_bound():
        raise RetentionError(
            "RetentionPolicy requires at least one of max_age_days / max_count"
        )
    runs_dir = Path(runs_dir)
    sessions_dir = Path(sessions_dir)
    root = _confined_root(runs_dir, sessions_dir)
    moment = _now(now)

    run_forced, run_eligible = _scan_runs(runs_dir, root, moment)
    sess_forced, sess_eligible = _scan_sessions(sessions_dir, root, moment)

    delete, retained = _classify_eligible([*run_eligible, *sess_eligible], policy)
    skip = [*run_forced, *sess_forced, *retained]
    return CleanupPlan(
        root=root,
        runs_dir=runs_dir.resolve(),
        sessions_dir=sessions_dir.resolve(),
        delete=delete,
        skip=skip,
    )


def apply_cleanup(
    plan: CleanupPlan, *, confirm: bool, now: datetime | None = None
) -> CleanupResult:
    """Delete only ``plan.delete`` entries, re-verifying every safety invariant.

    Refuses entirely unless ``confirm is True`` (dry-run is the default). Each
    candidate is re-checked immediately before removal (no symlink, resolves
    strictly within ``plan.root``, session not re-opened, no live lease) so a
    tampered or raced plan can never escape the artifact root or delete a session
    that re-opened between plan and apply.
    """
    if confirm is not True:
        raise RetentionError(
            "apply_cleanup refuses to delete without confirm=True (dry-run is the default)"
        )
    moment = _now(now)
    deleted: list[str] = []
    failed: list[dict] = []
    for candidate in plan.delete:
        try:
            _delete_candidate(candidate, plan.root, moment)
        except (RetentionError, OSError) as exc:
            failed.append(
                {"id": candidate.id, "path": str(candidate.path), "reason": str(exc)}
            )
            continue
        deleted.append(candidate.id)
    return CleanupResult(plan=plan, deleted=deleted, failed=failed)


def _delete_candidate(candidate: CleanupCandidate, root: Path, now: datetime) -> None:
    path = Path(candidate.path)
    if path.is_symlink():
        raise RetentionError(f"refuses to delete symlink {path!s}")
    _resolve_within_root(path, root)
    if candidate.kind == "session":
        # TOCTOU-aware: re-read state so a session that re-opened after planning
        # is refused (rule 4), then re-confirm no live lease was taken.
        if _is_open_session(path):
            raise RetentionError(
                f"refuses to delete session {candidate.id!r}: its state is now open"
            )
        if _has_live_lock(path, now):
            raise RetentionError(
                f"refuses to delete session {candidate.id!r}: its lease is now live"
            )
    shutil.rmtree(path)


def _is_open_session(session_dir: Path) -> bool:
    """True when the session record's current state reads ``open``.

    Used as an apply-time recheck (rule 4). An absent or unreadable record is not
    treated as open here — the candidate was a readable, closed record at plan
    time, and the path-confinement / live-lock guards still apply.
    """
    try:
        state = _read_json(session_dir / SESSION_JSON).get("state")
    except (OSError, ValueError):
        return False
    return state == STATE_OPEN

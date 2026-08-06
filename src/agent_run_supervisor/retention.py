"""Confined, dry-run-first Run evidence retention.

This module *plans* and *applies* safe pruning of bulky Run evidence under a
resolved ``.agent-run-supervisor`` root. It is stdlib-only and keeps the same
security posture as ``event_store.py`` / ``session.py``: it launches nothing (no
acpx, no network, no process signals), planning is strictly read-only, and
removal happens only with an explicit ``confirm is True``.

**Sessions are never deletion candidates, and Run directories are never
deleted.** Runs terminate; Sessions do not close, and a Session identity record
is small and durable by default — silence, age, Run completion, daemon restart,
and caller disconnection never imply expiry. A Run directory keeps a minimal
immutable idempotency and attribution spine forever, because duplicate-submit
handling and reconciliation read it; only the bulky evidence around that spine
is prunable, and only after a trustworthy terminal exists. Pruning is therefore
never an idempotency reset: a repeated authenticated ``request_id`` stays
recognized and stays non-dispatching.

Safety boundaries (each pinned by ``tests/test_retention.py``):

1. **Artifact-root confinement.** ``runs_dir`` / ``sessions_dir`` must resolve
   under a ``.agent-run-supervisor`` path segment, else :class:`RetentionError`.
   The tool refuses to operate on arbitrary directories (``/``, ``$HOME``, …).
2. **Per-candidate confinement.** A candidate is only prunable when its
   *resolved* path is strictly inside the resolved root; the root itself (and the
   ``runs``/``sessions`` enumeration dirs) is never a target, and a path
   that resolves outside root is refused at apply time (TOCTOU-aware).
3. **No symlink escape.** A symlinked entry is skipped with
   ``reason="symlink_escape"`` and never traversed or removed; removal never
   follows a symlink (re-checked immediately before removal).
4. **Sessions are durable.** Every Session directory is skipped with
   ``reason="session_durable"``, unconditionally, regardless of policy, age,
   flags, lease, or quarantine. There is no bound that makes one deletable.
5. **The Run spine survives.** :data:`RUN_IDEMPOTENCY_SPINE` is the single
   allowlist, and a Run without a trustworthy terminal is not prunable at all
   (``reason="no_trustworthy_terminal"``).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_run_supervisor.native_acp.storage import (
    NativeTerminalKind,
    read_native_terminal_result,
)
from agent_run_supervisor.session import SESSION_JSON

#: The only directory name this tool will ever remove anything inside.
ARTIFACT_ROOT_NAME = ".agent-run-supervisor"
#: Per-run metadata file whose mtime is the preferred age source for a run dir.
RUN_MARKER = "result.json"

#: The immutable idempotency and attribution spine of a Run directory, defined
#: **once** here so no policy, bound, or future caller can shorten it. Pruning
#: removes everything else and never one of these: the durable submission that
#: recognizes a repeated authenticated ``request_id``, the sealed Spec/launch
#: attribution and terminal result that reconciliation decides on, and the two
#: dispatch markers that keep its uncertainty verdict identical after a prune.
RUN_IDEMPOTENCY_SPINE = (
    "submission.json",
    "spec.json",
    "launch.json",
    "result.json",
    "prompt-dispatch-started",
    "prompt-accepted",
)

_DAY_SECONDS = 86400.0
_UTC = timezone.utc


class RetentionError(RuntimeError):
    """Raised when a cleanup request violates a retention safety boundary."""


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention bounds. At least one of the two bounds must be set.

    ``max_age_days`` prunes anything strictly older than the bound;
    ``max_count`` keeps the newest N eligible Runs and prunes the remainder.
    Sessions are never prunable regardless of these bounds, and no bound ever
    reaches a Run's idempotency spine.
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
    action: str  # "prune" | "skip"
    reason: str


@dataclass(frozen=True)
class CleanupPlan:
    """The read-only result of :func:`plan_cleanup`."""

    root: Path
    runs_dir: Path
    sessions_dir: Path
    prune: list[CleanupCandidate]
    skip: list[CleanupCandidate]


@dataclass(frozen=True)
class CleanupResult:
    """The result of :func:`apply_cleanup` — pruned run ids and any failures."""

    plan: CleanupPlan
    pruned: list[str]
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


def _has_trustworthy_terminal(run_dir: Path) -> bool:
    """True only when the **production** reader trusts this Run's terminal.

    Pruning is irreversible data loss gated on one fact — this Run is over —
    and that fact already has exactly one definition in the product. Retention
    must not carry a second, looser one: a document the supervisor would refuse
    as evidence must never license deleting evidence.

    :func:`read_native_terminal_result` is that definition. It is bounded
    (a size ceiling and a no-follow open), schema-complete, status-closed, and
    identity-exact against this Run — so a truncated, forged, oversized,
    symlinked, wrong-Run, or unknown-status result all resolve the same way
    here: not trusted, therefore not prunable.
    """
    state = read_native_terminal_result(run_dir / RUN_MARKER, run_id=run_dir.name)
    return state.kind is NativeTerminalKind.TRUSTED


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
        age = _age_seconds(entry, RUN_MARKER, now)
        if not _has_trustworthy_terminal(entry):
            # An in-flight or unterminated Run keeps everything: its evidence
            # is what a supervisor or a reconciliation still needs.
            forced.append(
                CleanupCandidate(
                    kind="run", id=entry.name, path=entry,
                    age_seconds=age, action="skip",
                    reason="no_trustworthy_terminal",
                )
            )
            continue
        eligible.append(
            _Pending(kind="run", id=entry.name, path=entry, age_seconds=age)
        )
    return forced, eligible


def _scan_sessions(
    sessions_dir: Path, root: Path, now: datetime
) -> list[CleanupCandidate]:
    """Every Session directory, always skipped.

    There is no eligibility path and no bound to apply: a Session is durable,
    so it is reported for visibility and never selected. A live lease and
    quarantine evidence are query/admission facts, not deletion eligibility, so
    neither is read here — reading them could only suggest that some *other*
    value would have made the directory deletable, and none does.
    """
    forced: list[CleanupCandidate] = []
    if not sessions_dir.is_dir():
        return forced
    for entry in sorted(sessions_dir.iterdir(), key=lambda p: p.name):
        escape = _symlink_escape_candidate(entry, "session", root, now, SESSION_JSON)
        if escape is not None:
            forced.append(escape)
            continue
        if not entry.is_dir():
            continue
        if not (entry / SESSION_JSON).exists():
            continue  # not a session-record directory
        forced.append(
            CleanupCandidate(
                kind="session", id=entry.name, path=entry,
                age_seconds=_age_seconds(entry, SESSION_JSON, now),
                action="skip", reason="session_durable",
            )
        )
    return forced


def _classify_eligible(
    pending: list[_Pending], policy: RetentionPolicy
) -> tuple[list[CleanupCandidate], list[CleanupCandidate]]:
    """Split policy-eligible Runs into prune/skip by age and count."""
    keep_ids: set[int] | None = None
    if policy.max_count is not None:
        newest_first = sorted(pending, key=lambda p: p.age_seconds)
        keep_ids = {id(p) for p in newest_first[: policy.max_count]}

    prune: list[CleanupCandidate] = []
    skip: list[CleanupCandidate] = []
    for item in pending:
        too_old = (
            policy.max_age_days is not None
            and item.age_seconds > policy.max_age_days * _DAY_SECONDS
        )
        over_count = keep_ids is not None and id(item) not in keep_ids
        if too_old or over_count:
            reason = "max_age_days" if too_old else "max_count"
            prune.append(_finalize(item, "prune", reason))
        else:
            skip.append(_finalize(item, "skip", "retained"))
    return prune, skip


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
    """Plan a confined prune. Read-only: removes nothing.

    Enumerates Run and Session directories, classifies each as ``prune`` or
    ``skip`` per ``policy`` and the safety rules, and returns a
    :class:`CleanupPlan`. Refuses (``RetentionError``) when ``policy`` has no
    bound or when either directory is not confined to a
    ``.agent-run-supervisor`` root. Session directories are always ``skip``.
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
    sess_forced = _scan_sessions(sessions_dir, root, moment)

    prune, retained = _classify_eligible(run_eligible, policy)
    skip = [*run_forced, *sess_forced, *retained]
    return CleanupPlan(
        root=root,
        runs_dir=runs_dir.resolve(),
        sessions_dir=sessions_dir.resolve(),
        prune=prune,
        skip=skip,
    )


def apply_cleanup(
    plan: CleanupPlan, *, confirm: bool, now: datetime | None = None
) -> CleanupResult:
    """Prune only ``plan.prune`` entries, re-verifying every safety invariant.

    Refuses entirely unless ``confirm is True`` (dry-run is the default). Each
    candidate is re-checked immediately before removal (no symlink, resolves
    strictly within ``plan.root``, terminal still present) so a tampered or
    raced plan can never escape the artifact root — and the spine is re-read
    from :data:`RUN_IDEMPOTENCY_SPINE` at removal time, never from the plan, so
    a tampered plan cannot widen what is removable either.
    """
    if confirm is not True:
        raise RetentionError(
            "apply_cleanup refuses to remove anything without confirm=True "
            "(dry-run is the default)"
        )
    moment = _now(now)
    pruned: list[str] = []
    failed: list[dict] = []
    for candidate in plan.prune:
        try:
            _prune_candidate(candidate, plan.root, moment)
        except (RetentionError, OSError) as exc:
            failed.append(
                {"id": candidate.id, "path": str(candidate.path), "reason": str(exc)}
            )
            continue
        pruned.append(candidate.id)
    return CleanupResult(plan=plan, pruned=pruned, failed=failed)


def _prune_candidate(candidate: CleanupCandidate, root: Path, now: datetime) -> None:
    """Remove one Run's non-spine evidence. The directory itself always stays."""
    path = Path(candidate.path)
    if candidate.kind != "run":
        # Structural, not defensive: nothing but a Run ever reaches ``prune``.
        raise RetentionError(f"refuses to prune a {candidate.kind} artifact")
    if path.is_symlink():
        raise RetentionError(f"refuses to prune through symlink {path!s}")
    resolved = _resolve_within_root(path, root)
    # TOCTOU-aware: a Run that lost its terminal between plan and apply is no
    # longer over, so its evidence is no longer prunable.
    if not _has_trustworthy_terminal(path):
        raise RetentionError(
            f"refuses to prune run {candidate.id!r}: it has no readable terminal"
        )
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.name in RUN_IDEMPOTENCY_SPINE:
            continue
        # Re-confine every child on its own: a symlinked child is unlinked as
        # the link it is, and never followed.
        if entry.is_symlink():
            entry.unlink()
            continue
        _resolve_within_root(entry, resolved)
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

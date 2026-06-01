"""H1 W2 retention/cleanup boundary + selection tests.

These pin the *safety* of artifact retention: a confined, dry-run-first cleanup
that can never delete outside a resolved ``.agent-run-supervisor`` artifact root,
never follows symlinks out of root, never removes open/live-locked sessions, and
only deletes with an explicit ``confirm=True``. They launch nothing (no acpx, no
network, no process signals) — everything is local filesystem state.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.retention import (
    CleanupCandidate,
    CleanupPlan,
    RetentionError,
    RetentionPolicy,
    apply_cleanup,
    plan_cleanup,
)
from agent_run_supervisor.role import AgentRoleSpec, load_role
from agent_run_supervisor.session import SessionStore
from agent_run_supervisor.workspace import validate_effective_cwd

UTC = timezone.utc
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
PAST = NOW - timedelta(days=1)
ARTIFACT = ".agent-run-supervisor"


# --- helpers --------------------------------------------------------------


def _artifact_dirs(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / ARTIFACT
    runs = base / "runs"
    sessions = base / "sessions"
    runs.mkdir(parents=True)
    sessions.mkdir(parents=True)
    return runs, sessions


def _age_path(path: Path, *, age_days: float, now: datetime = NOW) -> None:
    ts = (now - timedelta(days=age_days)).timestamp()
    os.utime(path, (ts, ts))


def _make_run(runs_dir: Path, run_id: str, *, age_days: float, now: datetime = NOW) -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir()
    marker = run_dir / "result.json"
    marker.write_text("{}", encoding="utf-8")
    _age_path(marker, age_days=age_days, now=now)
    _age_path(run_dir, age_days=age_days, now=now)
    return run_dir


def _role(valid_role_dict: dict[str, Any], work: Path) -> AgentRoleSpec:
    payload = dict(valid_role_dict)
    payload["workspace"] = dict(valid_role_dict["workspace"])
    payload["workspace"]["default_cwd"] = str(work)
    payload["workspace"]["allowed_roots"] = [str(work)]
    payload["workspace"]["allowed_roots_security_boundary"] = False
    payload["session"] = {"strategy": "persistent"}
    return load_role(payload)


def _session_env(tmp_path: Path, valid_role_dict: dict[str, Any], sessions_dir: Path):
    work = tmp_path / "work"
    work.mkdir()
    role = _role(valid_role_dict, work)
    workspace = validate_effective_cwd(role, override=None)
    store = SessionStore(base_dir=sessions_dir)
    return store, role, workspace


def _by_id(plan: CleanupPlan) -> dict[str, CleanupCandidate]:
    return {c.id: c for c in (*plan.delete, *plan.skip)}


# --- selection: max_age / max_count ---------------------------------------


def test_plan_cleanup_selects_by_max_age_and_deletes_nothing(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    old = _make_run(runs, "run-old", age_days=10)
    fresh = _make_run(runs, "run-fresh", age_days=1)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=5), now=NOW
    )

    by_id = _by_id(plan)
    assert by_id["run-old"].action == "delete"
    assert by_id["run-fresh"].action == "skip"
    # Planning is read-only: every path still exists afterwards.
    assert old.exists()
    assert fresh.exists()


def test_plan_cleanup_selects_by_max_count_keeps_newest(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    r1 = _make_run(runs, "run-1", age_days=3)  # oldest
    r2 = _make_run(runs, "run-2", age_days=2)
    r3 = _make_run(runs, "run-3", age_days=1)  # newest

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_count=2), now=NOW
    )

    delete_ids = {c.id for c in plan.delete}
    skip_ids = {c.id for c in plan.skip}
    assert delete_ids == {"run-1"}
    assert {"run-2", "run-3"} <= skip_ids
    assert r1.exists() and r2.exists() and r3.exists()


def test_plan_cleanup_requires_at_least_one_bound(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    with pytest.raises(RetentionError):
        plan_cleanup(
            runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(), now=NOW
        )


# --- confinement refusal (rule 1) -----------------------------------------


def test_plan_cleanup_refuses_dirs_outside_artifact_root(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    sessions = tmp_path / "sessions"
    runs.mkdir()
    sessions.mkdir()

    with pytest.raises(RetentionError):
        plan_cleanup(
            runs_dir=runs,
            sessions_dir=sessions,
            policy=RetentionPolicy(max_age_days=1),
            now=NOW,
        )


# --- per-candidate confinement (rule 2) -----------------------------------


def test_plan_cleanup_never_targets_root_or_enumeration_dirs(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    _make_run(runs, "run-old", age_days=10)
    root = (tmp_path / ARTIFACT).resolve()

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )

    targeted = {c.path.resolve() for c in plan.delete}
    assert root not in targeted
    assert runs.resolve() not in targeted
    assert sessions.resolve() not in targeted


def test_apply_cleanup_refuses_candidate_resolving_outside_root(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    root = (tmp_path / ARTIFACT).resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious", encoding="utf-8")

    # A hand-crafted plan that tries to delete a path outside the artifact root
    # (simulating TOCTOU / a tampered plan) must be refused at apply time.
    bad = CleanupCandidate(
        kind="run", id="evil", path=outside, age_seconds=10_000_000.0,
        action="delete", reason="forced",
    )
    plan = CleanupPlan(
        root=root, runs_dir=runs.resolve(), sessions_dir=sessions.resolve(),
        delete=[bad], skip=[],
    )

    result = apply_cleanup(plan, confirm=True, now=NOW)

    assert "evil" not in result.deleted
    assert any(f["id"] == "evil" for f in result.failed)
    assert outside.exists()
    assert (outside / "keep.txt").exists()


# --- symlink escape (rule 3) ----------------------------------------------


def test_plan_skips_symlink_escape_and_apply_preserves_target(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    outside = tmp_path / "outside-target"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious", encoding="utf-8")
    link = runs / "escaped-run"
    link.symlink_to(outside, target_is_directory=True)

    # max_count=0 would otherwise mark every entry for deletion; the symlink must
    # still be skipped (never traversed/removed) on safety grounds.
    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_count=0), now=NOW
    )

    by_id = _by_id(plan)
    assert by_id["escaped-run"].action == "skip"
    assert by_id["escaped-run"].reason == "symlink_escape"
    assert all(c.id != "escaped-run" for c in plan.delete)

    result = apply_cleanup(plan, confirm=True, now=NOW)

    # The symlink target outside the root is never touched.
    assert outside.exists()
    assert (outside / "keep.txt").exists()


# --- open / live session protection (rules 4 & 5) -------------------------


def test_plan_protects_open_and_live_sessions_and_frees_closed(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)

    store.create_session(session_id="sess-open", role=role, workspace_result=workspace, now=NOW)
    store.create_session(session_id="sess-closed", role=role, workspace_result=workspace, now=NOW)
    store.mark_closed("sess-closed", now=NOW)
    store.create_session(session_id="sess-live", role=role, workspace_result=workspace, now=NOW)
    store.acquire_lock("sess-live", owner="worker", now=NOW, lease_seconds=3600)

    for sid in ("sess-open", "sess-closed", "sess-live"):
        _age_path(sessions / sid / "session.json", age_days=30)
        _age_path(sessions / sid, age_days=30)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )

    by_id = _by_id(plan)
    assert by_id["sess-open"].action == "skip"
    assert by_id["sess-open"].reason == "open_session"
    assert by_id["sess-live"].action == "skip"
    assert by_id["sess-live"].reason == "live_lock"
    assert by_id["sess-closed"].action == "delete"


def test_apply_deletes_closed_session_with_expired_lock(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)

    store.create_session(session_id="sess-exp", role=role, workspace_result=workspace, now=NOW)
    store.mark_closed("sess-exp", now=NOW)
    # An expired lease left behind on a closed session (acquired in the past).
    store.acquire_lock("sess-exp", owner="worker", now=PAST, lease_seconds=10)
    lock_path = sessions / "sess-exp" / "lock.json"
    assert lock_path.exists()
    _age_path(sessions / "sess-exp" / "session.json", age_days=30)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )
    assert any(c.id == "sess-exp" and c.action == "delete" for c in plan.delete)

    result = apply_cleanup(plan, confirm=True, now=NOW)

    assert "sess-exp" in result.deleted
    # The whole dir — including the expired lock — is gone.
    assert not (sessions / "sess-exp").exists()


def test_plan_skips_live_locked_session(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)
    store.create_session(session_id="sess-held", role=role, workspace_result=workspace, now=NOW)
    store.acquire_lock("sess-held", owner="worker", now=NOW, lease_seconds=3600)
    _age_path(sessions / "sess-held" / "session.json", age_days=30)

    plan = plan_cleanup(
        runs_dir=runs,
        sessions_dir=sessions,
        policy=RetentionPolicy(max_age_days=1),
        now=NOW,
    )

    by_id = _by_id(plan)
    assert by_id["sess-held"].action == "skip"
    assert by_id["sess-held"].reason == "live_lock"


def test_plan_never_deletes_open_session_even_without_a_live_lock(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)
    # An open session with NO lock.json at all — the H1 requirement is that an
    # open session is unconditionally protected, not only when live-locked.
    store.create_session(session_id="sess-open2", role=role, workspace_result=workspace, now=NOW)
    assert not (sessions / "sess-open2" / "lock.json").exists()
    _age_path(sessions / "sess-open2" / "session.json", age_days=30)
    _age_path(sessions / "sess-open2", age_days=30)

    # Even the most aggressive policy (delete-everything) must keep the open
    # session: it is forced into `skip` and never appears in `delete`.
    plan = plan_cleanup(
        runs_dir=runs,
        sessions_dir=sessions,
        policy=RetentionPolicy(max_age_days=1, max_count=0),
        now=NOW,
    )

    by_id = _by_id(plan)
    assert by_id["sess-open2"].action == "skip"
    assert by_id["sess-open2"].reason == "open_session"
    assert all(c.id != "sess-open2" for c in plan.delete)
    assert (sessions / "sess-open2").exists()


def test_apply_refuses_session_reopened_between_plan_and_apply(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)
    store.create_session(session_id="sess-flip", role=role, workspace_result=workspace, now=NOW)
    store.mark_closed("sess-flip", now=NOW)
    _age_path(sessions / "sess-flip" / "session.json", age_days=30)

    # Closed at plan time -> the session is planned for deletion.
    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )
    assert any(c.id == "sess-flip" and c.action == "delete" for c in plan.delete)

    # TOCTOU: the record flips back to `open` after planning but before apply.
    session_json = sessions / "sess-flip" / "session.json"
    data = json.loads(session_json.read_text(encoding="utf-8"))
    data["state"] = "open"
    session_json.write_text(json.dumps(data), encoding="utf-8")

    result = apply_cleanup(plan, confirm=True, now=NOW)

    # Apply re-reads state and refuses: nothing deleted, the dir survives intact.
    assert "sess-flip" not in result.deleted
    assert any(f["id"] == "sess-flip" for f in result.failed)
    assert (sessions / "sess-flip").exists()
    assert session_json.exists()


# --- dry-run vs apply (rule: explicit confirm) ----------------------------


def test_dry_run_then_apply_deletes_only_planned(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    old = _make_run(runs, "run-old", age_days=10)
    fresh = _make_run(runs, "run-fresh", age_days=1)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=5), now=NOW
    )
    # Planning deletes nothing.
    assert old.exists() and fresh.exists()

    # confirm must be exactly True; the default-safe path refuses.
    with pytest.raises(RetentionError):
        apply_cleanup(plan, confirm=False, now=NOW)
    assert old.exists() and fresh.exists()

    result = apply_cleanup(plan, confirm=True, now=NOW)

    assert result.deleted == ["run-old"]
    assert not old.exists()
    # Only planned deletions happen — the skip set is untouched.
    assert fresh.exists()
    assert result.failed == []

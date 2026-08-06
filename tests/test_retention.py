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
from agent_run_supervisor.session import (
    QUARANTINE_DISPATCH_OBSERVATION_LOST,
    SessionStore,
)
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


def _production_terminal(run_id: str) -> dict[str, Any]:
    """Exactly what the production writer emits for a completed Run.

    Retention trusts the production reader, so a fixture that wants to be
    trusted has to be a document production would actually have written.
    """
    from agent_run_supervisor.exit_classifier import AgentRunStatus
    from agent_run_supervisor.result import build_result_payload

    return build_result_payload(
        run_id=run_id,
        status=AgentRunStatus.COMPLETED,
        origin="acp",
        detail_code=None,
        retryable=False,
        exit_code=0,
        signal=None,
        stop_reason="end_turn",
        usage=None,
        final_message="done",
        truncated=False,
        truncate_reason=None,
        run_dir=None,
    )


def _make_run(runs_dir: Path, run_id: str, *, age_days: float, now: datetime = NOW) -> Path:
    """A terminated Run: only a Run that is over is ever prunable."""
    run_dir = runs_dir / run_id
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"seq":1}\n', encoding="utf-8")
    marker = run_dir / "result.json"
    marker.write_text(json.dumps(_production_terminal(run_id)), encoding="utf-8")
    _age_path(marker, age_days=age_days, now=now)
    _age_path(run_dir, age_days=age_days, now=now)
    return run_dir


def _role(valid_role_dict: dict[str, Any], work: Path) -> AgentRoleSpec:
    payload = dict(valid_role_dict)
    payload["workspace"] = dict(valid_role_dict["workspace"])
    payload["workspace"]["default_cwd"] = str(work)
    payload["workspace"]["allowed_roots"] = [str(work)]
    payload["workspace"]["allowed_roots_security_boundary"] = False
    payload["session"] = {"lease_seconds": 900}
    return load_role(payload)


def _session_env(tmp_path: Path, valid_role_dict: dict[str, Any], sessions_dir: Path):
    work = tmp_path / "work"
    work.mkdir()
    role = _role(valid_role_dict, work)
    workspace = validate_effective_cwd(role, override=None)
    store = SessionStore(base_dir=sessions_dir)
    return store, role, workspace


def _by_id(plan: CleanupPlan) -> dict[str, CleanupCandidate]:
    return {c.id: c for c in (*plan.prune, *plan.skip)}


# --- selection: max_age / max_count ---------------------------------------


def test_plan_cleanup_selects_by_max_age_and_deletes_nothing(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    old = _make_run(runs, "run-old", age_days=10)
    fresh = _make_run(runs, "run-fresh", age_days=1)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=5), now=NOW
    )

    by_id = _by_id(plan)
    assert by_id["run-old"].action == "prune"
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

    prune_ids = {c.id for c in plan.prune}
    skip_ids = {c.id for c in plan.skip}
    assert prune_ids == {"run-1"}
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

    targeted = {c.path.resolve() for c in plan.prune}
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
        action="prune", reason="forced",
    )
    plan = CleanupPlan(
        root=root, runs_dir=runs.resolve(), sessions_dir=sessions.resolve(),
        prune=[bad], skip=[],
    )

    result = apply_cleanup(plan, confirm=True, now=NOW)

    assert "evil" not in result.pruned
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
    assert all(c.id != "escaped-run" for c in plan.prune)

    result = apply_cleanup(plan, confirm=True, now=NOW)

    # The symlink target outside the root is never touched.
    assert outside.exists()
    assert (outside / "keep.txt").exists()


# --- Session directories are durable (rule 4) -----------------------------
#
# There is no state, lease, age, or policy that makes a Session directory
# deletable. The old suite had five tests here because five different values
# could each free one; one test replaces them because no value can.


def test_no_session_is_ever_prunable_whatever_its_lease_or_evidence(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)

    store.create_session(
        session_id="sess-plain", role=role, workspace_result=workspace, now=NOW
    )
    store.create_session(
        session_id="sess-quarantined", role=role, workspace_result=workspace, now=NOW
    )
    store.mark_quarantined(
        "sess-quarantined",
        reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST,
        run_id="run-prior",
        now=NOW,
    )
    store.create_session(
        session_id="sess-live", role=role, workspace_result=workspace, now=NOW
    )
    store.acquire_lock("sess-live", owner="worker", now=NOW, lease_seconds=3600)
    store.create_session(
        session_id="sess-expired", role=role, workspace_result=workspace, now=NOW
    )
    store.acquire_lock("sess-expired", owner="worker", now=PAST, lease_seconds=10)

    every = ("sess-plain", "sess-quarantined", "sess-live", "sess-expired")
    for sid in every:
        _age_path(sessions / sid / "session.json", age_days=4000)
        _age_path(sessions / sid, age_days=4000)

    # The most aggressive policy expressible: everything is over-age and the
    # keep-count is zero.
    plan = plan_cleanup(
        runs_dir=runs,
        sessions_dir=sessions,
        policy=RetentionPolicy(max_age_days=1, max_count=0),
        now=NOW,
    )

    by_id = _by_id(plan)
    for sid in every:
        assert by_id[sid].action == "skip", sid
        assert by_id[sid].reason == "session_durable", sid
    assert [c.id for c in plan.prune if c.kind == "session"] == []

    result = apply_cleanup(plan, confirm=True, now=NOW)

    assert not [pruned for pruned in result.pruned if pruned in every]
    for sid in every:
        assert (sessions / sid / "session.json").exists(), sid


def test_a_session_is_reported_for_visibility_not_selected(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    """Durable does not mean invisible: an operator still sees what is there."""
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)
    store.create_session(
        session_id="sess-seen", role=role, workspace_result=workspace, now=NOW
    )

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )

    seen = _by_id(plan)["sess-seen"]
    assert seen.kind == "session"
    assert seen.action == "skip"


def test_pruning_a_run_never_reaches_a_session_directory(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    """Structural: the only thing apply can remove is non-spine Run evidence."""
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)
    store.create_session(
        session_id="sess-bystander", role=role, workspace_result=workspace, now=NOW
    )
    _make_run(runs, "run-old", age_days=10)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=5), now=NOW
    )
    result = apply_cleanup(plan, confirm=True, now=NOW)

    assert result.pruned == ["run-old"]
    assert (sessions / "sess-bystander" / "session.json").exists()


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

    assert result.pruned == ["run-old"]
    # The Run directory and its spine survive; only bulk evidence is gone.
    assert old.exists()
    assert (old / "result.json").exists()
    assert not (old / "events.jsonl").exists()
    # Only planned prunes happen — the skip set is untouched byte for byte.
    assert fresh.exists()
    assert (fresh / "events.jsonl").exists()
    assert result.failed == []


# --- D1: the no-close retention contract ----------------------------------
#
# Session identity records are durable by default: silence, age, Run completion,
# daemon restart, and caller disconnection never imply expiry, so a Session
# directory is **never** a deletion candidate at all. Run retention prunes bulky
# evidence only after a trustworthy terminal, and always preserves one centrally
# defined immutable idempotency/attribution spine, so a repeated authenticated
# ``request_id`` stays non-dispatching after pruning.


def _terminal_run(runs_dir: Path, run_id: str, *, age_days: float) -> Path:
    """A Run directory with a trustworthy terminal plus bulky evidence."""
    run_dir = runs_dir / run_id
    run_dir.mkdir()
    (run_dir / "submission.json").write_text(
        json.dumps({"schema_version": 1, "run_id": run_id}), encoding="utf-8"
    )
    (run_dir / "spec.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    (run_dir / "launch.json").write_text("{}", encoding="utf-8")
    (run_dir / "result.json").write_text(
        json.dumps(_production_terminal(run_id)), encoding="utf-8"
    )
    (run_dir / "prompt-dispatch-started").write_text("", encoding="utf-8")
    (run_dir / "prompt-accepted").write_text("", encoding="utf-8")
    # Bulk evidence: prunable.
    (run_dir / "events.jsonl").write_text('{"seq":1}\n' * 100, encoding="utf-8")
    (run_dir / "stderr.txt").write_text("noise\n" * 100, encoding="utf-8")
    (run_dir / "effective.json").write_text("{}", encoding="utf-8")
    for path in run_dir.iterdir():
        _age_path(path, age_days=age_days)
    _age_path(run_dir, age_days=age_days)
    return run_dir


def test_the_run_spine_allowlist_is_defined_exactly_once() -> None:
    from agent_run_supervisor import retention

    assert retention.RUN_IDEMPOTENCY_SPINE == (
        "submission.json",
        "spec.json",
        "launch.json",
        "result.json",
        "prompt-dispatch-started",
        "prompt-accepted",
    )


def test_a_session_directory_is_never_a_deletion_candidate(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    store, role, workspace = _session_env(tmp_path, valid_role_dict, sessions)
    store.create_native_session(
        session_id="sess-durable",
        profile_id="prof-1",
        profile_revision=1,
        profile_hash="e" * 64,
        owner="hermes",
        namespace="hermes/ns",
        workspace_hash="b" * 64,
        effective_cwd=str(tmp_path / "work"),
        matched_root=str(tmp_path / "work"),
        agent_id="native-agent",
        agent_session_id="external-abc",
    )
    _age_path(sessions / "sess-durable", age_days=4000)

    plan = plan_cleanup(
        runs_dir=runs,
        sessions_dir=sessions,
        policy=RetentionPolicy(max_age_days=1, max_count=0),
        now=NOW,
    )

    assert [c.id for c in plan.prune if c.kind == "session"] == []
    candidate = _by_id(plan)["sess-durable"]
    assert candidate.action == "skip"
    assert candidate.reason == "session_durable"

    apply_cleanup(plan, confirm=True)
    assert (sessions / "sess-durable" / "session.json").exists()


def test_terminal_run_pruning_preserves_the_idempotency_spine(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    run_dir = _terminal_run(runs, "run-old", age_days=10)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=5), now=NOW
    )
    assert _by_id(plan)["run-old"].action == "prune"

    result = apply_cleanup(plan, confirm=True)
    assert result.pruned == ["run-old"]
    assert not result.failed

    assert run_dir.is_dir()
    survivors = {p.name for p in run_dir.iterdir()}
    from agent_run_supervisor import retention

    assert set(retention.RUN_IDEMPOTENCY_SPINE) == survivors
    assert not (run_dir / "events.jsonl").exists()
    assert not (run_dir / "stderr.txt").exists()


def test_a_run_without_a_trustworthy_terminal_is_never_pruned(tmp_path: Path) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    run_dir = runs / "run-inflight"
    run_dir.mkdir()
    (run_dir / "submission.json").write_text("{}", encoding="utf-8")
    (run_dir / "events.jsonl").write_text('{"seq":1}\n', encoding="utf-8")
    _age_path(run_dir, age_days=999)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )
    candidate = _by_id(plan)["run-inflight"]
    assert candidate.action == "skip"
    assert candidate.reason == "no_trustworthy_terminal"

    apply_cleanup(plan, confirm=True)
    assert (run_dir / "events.jsonl").exists()


def test_a_pruned_request_id_is_still_recognized_and_never_redispatched(
    tmp_path: Path,
) -> None:
    """The whole point of the spine: pruning is not an idempotency reset."""
    from agent_run_supervisor.arsd import admission

    runs, sessions = _artifact_dirs(tmp_path)
    run_dir = runs / "run-keyed"
    run_dir.mkdir()
    key = admission.AdmissionKey(principal_id="p-1", request_id="req-1")
    run_id = admission.derive_run_id(key)
    (run_dir / "events.jsonl").write_text('{"seq":1}\n' * 50, encoding="utf-8")
    (run_dir / "result.json").write_text(
        json.dumps(_production_terminal("run-keyed")), encoding="utf-8"
    )
    (run_dir / "submission.json").write_text(
        json.dumps(
            {
                "schema_version": admission.SUBMISSION_SCHEMA_VERSION,
                "principal_id": key.principal_id,
                "request_id": key.request_id,
                "run_id": run_id,
            }
        ),
        encoding="utf-8",
    )
    _age_path(run_dir, age_days=999)
    for path in run_dir.iterdir():
        _age_path(path, age_days=999)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )
    apply_cleanup(plan, confirm=True)

    submission = admission.read_submission(run_dir)
    assert submission is not None
    assert admission.submission_binds_key(submission, key)


# --- B4: pruning requires a terminal production itself would trust ---------
#
# Pruning is irreversible data loss gated on one fact: this Run is over. That
# fact has exactly one definition in the product — the bounded Native terminal
# reader — and retention must not carry a looser second one. A document the
# supervisor would refuse as evidence must not license deleting evidence.


def _run_with_terminal(runs_dir: Path, run_id: str, terminal: Any) -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"seq":1}\n' * 20, encoding="utf-8")
    if terminal is not None:
        (run_dir / "result.json").write_text(
            terminal if isinstance(terminal, str) else json.dumps(terminal),
            encoding="utf-8",
        )
    for path in run_dir.iterdir():
        _age_path(path, age_days=999)
    _age_path(run_dir, age_days=999)
    return run_dir


def _trusted_terminal(run_id: str) -> dict[str, Any]:
    return _production_terminal(run_id)


def test_a_production_trusted_terminal_is_prunable(tmp_path: Path) -> None:
    """The positive control: a real terminal really does license a prune."""
    runs, sessions = _artifact_dirs(tmp_path)
    run_dir = _run_with_terminal(runs, "run-real", _trusted_terminal("run-real"))

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )
    assert _by_id(plan)["run-real"].action == "prune"

    result = apply_cleanup(plan, confirm=True, now=NOW)
    assert result.pruned == ["run-real"]
    assert not (run_dir / "events.jsonl").exists()
    assert (run_dir / "result.json").exists()


@pytest.mark.parametrize(
    "label,terminal",
    [
        ("minimal_shape", {"run_id": "run-x", "status": "completed"}),
        ("wrong_run", None),  # filled in below with a foreign run_id
        ("unknown_status", {"run_id": "run-x", "status": "vibing"}),
        ("not_an_object", ["completed"]),
        ("unparseable", "{ this was never finished"),
        ("empty", {}),
    ],
)
def test_a_terminal_production_would_refuse_never_licenses_pruning(
    tmp_path: Path, label: str, terminal: Any
) -> None:
    runs, sessions = _artifact_dirs(tmp_path)
    if label == "wrong_run":
        terminal = _trusted_terminal("run-somebody-else")
    run_dir = _run_with_terminal(runs, "run-x", terminal)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )

    candidate = _by_id(plan)["run-x"]
    assert candidate.action == "skip", label
    assert candidate.reason == "no_trustworthy_terminal", label

    apply_cleanup(plan, confirm=True, now=NOW)
    assert (run_dir / "events.jsonl").exists(), label


def test_a_symlinked_terminal_never_licenses_pruning(tmp_path: Path) -> None:
    """A terminal that is not a regular file is not a terminal."""
    runs, sessions = _artifact_dirs(tmp_path)
    real = tmp_path / "elsewhere-result.json"
    real.write_text(json.dumps(_trusted_terminal("run-link")), encoding="utf-8")
    run_dir = runs / "run-link"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"seq":1}\n', encoding="utf-8")
    (run_dir / "result.json").symlink_to(real)
    _age_path(run_dir, age_days=999)

    plan = plan_cleanup(
        runs_dir=runs, sessions_dir=sessions, policy=RetentionPolicy(max_age_days=1), now=NOW
    )

    assert _by_id(plan)["run-link"].action == "skip"
    apply_cleanup(plan, confirm=True, now=NOW)
    assert (run_dir / "events.jsonl").exists()


def test_retention_uses_the_production_terminal_reader(tmp_path: Path) -> None:
    """Structural: one definition of a trustworthy terminal, not two."""
    from agent_run_supervisor import retention as retention_module
    from agent_run_supervisor.native_acp import storage as storage_module

    seen: list[str] = []
    real = storage_module.read_native_terminal_result

    def spy(path, *, run_id):
        seen.append(run_id)
        return real(path, run_id=run_id)

    runs, sessions = _artifact_dirs(tmp_path)
    _run_with_terminal(runs, "run-spy", _trusted_terminal("run-spy"))
    original = retention_module.read_native_terminal_result
    retention_module.read_native_terminal_result = spy
    try:
        plan_cleanup(
            runs_dir=runs,
            sessions_dir=sessions,
            policy=RetentionPolicy(max_age_days=1),
            now=NOW,
        )
    finally:
        retention_module.read_native_terminal_result = original

    assert seen == ["run-spy"]

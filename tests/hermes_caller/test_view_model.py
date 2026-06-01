"""T4 — progress/result view-model construction (plan §6.5, §9, §10-T4).

The view-model is the caller-owned presentation projection. It maps STRUCTURAL
normalized-event evidence to a progress card (running/completed/error/permission
lifecycle) and a completed ``CallerResult`` + caller-derived verdict to a result
card. It carries the supervisor status as *evidence* (never as a verdict), the
already-redacted ``final_message`` as *untrusted* findings text, a local
evidence reference (no upload), and a session-lifecycle chip — and it never
surfaces bulk agent content.

GREEN coverage: the ``hermes_caller.view_model`` module exists and keeps output compact.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Callable

from agent_run_supervisor.caller import CallerResult
from agent_run_supervisor.hermes_caller.events import NormalizedEventView, load_events
from agent_run_supervisor.hermes_caller.verdict import (
    BusinessVerdict,
    VerdictDecision,
    derive_verdict,
)
from agent_run_supervisor.hermes_caller.view_model import (
    CardPhase,
    CardViewModel,
    ProgressItem,
    build_progress_view_model,
    build_result_view_model,
)

BULK_MARKER = "BULK_AGENT_CONTENT_THAT_MUST_NOT_LEAK"
PROGRESS_STATES = {"in_progress", "done", "failed"}


def _drop_families(views: list[NormalizedEventView], families: set[str]) -> list[NormalizedEventView]:
    return [v for v in views if v.family not in families]


# --------------------------------------------------------------------------- #
# Progress view-model — lifecycle phases
# --------------------------------------------------------------------------- #
def test_progress_running_phase_before_completion(events_run_dir: Path) -> None:
    # Drop the terminal run_completed to simulate an in-flight run.
    running = _drop_families(load_events(events_run_dir), {"run_completed"})
    vm = build_progress_view_model(running)

    assert isinstance(vm, CardViewModel)
    assert vm.phase is CardPhase.RUNNING


def test_progress_completed_phase(events_run_dir: Path) -> None:
    vm = build_progress_view_model(load_events(events_run_dir))
    assert vm.phase is CardPhase.COMPLETED


def test_progress_error_phase(failed_events_run_dir: Path) -> None:
    vm = build_progress_view_model(load_events(failed_events_run_dir))
    assert vm.phase is CardPhase.ERROR


def test_progress_permission_phase(failed_events_run_dir: Path) -> None:
    # Before the terminal failure, an open permission request drives the
    # needs-permission lifecycle phase.
    pre_terminal = _drop_families(
        load_events(failed_events_run_dir), {"run_failed", "permission_denied"}
    )
    vm = build_progress_view_model(pre_terminal)
    assert vm.phase is CardPhase.NEEDS_PERMISSION


# --------------------------------------------------------------------------- #
# Progress view-model — structural labels only, no bulk content
# --------------------------------------------------------------------------- #
def test_progress_items_are_structural_with_no_bulk_content(events_run_dir: Path) -> None:
    vm = build_progress_view_model(load_events(events_run_dir))

    assert vm.progress and all(isinstance(p, ProgressItem) for p in vm.progress)
    for item in vm.progress:
        assert item.state in PROGRESS_STATES
        # Structural label only — never a verbatim agent body.
        assert BULK_MARKER not in item.label

    # No string field anywhere on the view-model may carry bulk content.
    for value in dataclasses.asdict(vm).values():
        if isinstance(value, str):
            assert BULK_MARKER not in value


def test_progress_carries_session_lifecycle(events_run_dir: Path) -> None:
    vm = build_progress_view_model(load_events(events_run_dir), session_lifecycle="alive")
    assert vm.session_lifecycle == "alive"


# --------------------------------------------------------------------------- #
# Result view-model — evidence chip, verdict banner, untrusted findings
# --------------------------------------------------------------------------- #
def test_result_view_model_carries_evidence_verdict_and_findings(
    make_caller_result: Callable[..., CallerResult],
    run_result_issues: dict[str, Any],
    events_run_dir: Path,
) -> None:
    result = make_caller_result(run_result_issues, run_dir=str(events_run_dir))
    events = load_events(events_run_dir)
    decision = derive_verdict(result)

    vm = build_result_view_model(result, events, decision, session_lifecycle="closed")

    assert isinstance(vm, CardViewModel)
    assert vm.phase in {CardPhase.COMPLETED, CardPhase.VERDICT}

    # Supervisor status is carried as EVIDENCE, distinct from the verdict.
    assert vm.supervisor_status == "completed"

    # The caller-owned verdict object rides along as a banner.
    assert isinstance(vm.verdict, VerdictDecision)
    assert vm.verdict.verdict is BusinessVerdict.NEEDS_REVISION

    # findings_text is the already-redacted final_message, treated as untrusted text.
    assert vm.findings_text == run_result_issues["final_message"]

    # Evidence reference is a local artifact dir — never an upload/delivery handle.
    assert vm.evidence_ref == str(events_run_dir)

    # Session lifecycle chip is carried through.
    assert vm.session_lifecycle == "closed"


def test_result_view_model_error_phase_on_non_success(
    make_caller_result: Callable[..., CallerResult],
    run_result_failed: dict[str, Any],
    failed_events_run_dir: Path,
) -> None:
    result = make_caller_result(run_result_failed, run_dir=str(failed_events_run_dir))
    events = load_events(failed_events_run_dir)
    decision = derive_verdict(result)

    vm = build_result_view_model(result, events, decision)

    assert vm.phase is CardPhase.ERROR
    assert vm.supervisor_status == "timed_out"
    assert vm.verdict.verdict is BusinessVerdict.BLOCK

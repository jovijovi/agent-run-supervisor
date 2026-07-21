"""C8 L1: the architecture §5 terminal table as a pure function.

Every row is pinned. Exit-code classification is subordinate by
construction: the function never consults exit codes, so a dispatched Turn
without a reliable ACP terminal can never finalize completed/cancelled-class.
"""

from __future__ import annotations

import pytest

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp.run_task import (
    FinalizationObservations,
    finalize_run_state,
)


def _finalize(**kwargs):
    return finalize_run_state(FinalizationObservations(**kwargs))


def test_existing_result_is_kept_irreversibly() -> None:
    status, disposition = _finalize(result_exists=True, dispatch_started=True)
    assert status is None
    assert disposition == "keep"


def test_terminal_event_without_result_rebuilds() -> None:
    status, disposition = _finalize(
        persisted_terminal_event="end_turn", dispatch_started=True
    )
    assert status is AgentRunStatus.COMPLETED
    assert disposition == "active"


def test_pre_dispatch_failure_is_failed_and_session_stays_active() -> None:
    status, disposition = _finalize(dispatch_started=False)
    assert status is AgentRunStatus.FAILED
    assert disposition == "active"


def test_reliable_acp_terminal_completes() -> None:
    status, disposition = _finalize(dispatch_started=True, acp_stop_reason="end_turn")
    assert status is AgentRunStatus.COMPLETED
    assert disposition == "active"


@pytest.mark.parametrize("stop_reason", ["max_tokens", "max_turn_requests", "refusal"])
def test_other_agent_terminals_are_completed_class(stop_reason: str) -> None:
    status, disposition = _finalize(
        dispatch_started=True, acp_stop_reason=stop_reason
    )
    assert status is AgentRunStatus.COMPLETED
    assert disposition == "active"


def test_agent_side_cancel_terminal_is_cancelled_active() -> None:
    status, disposition = _finalize(dispatch_started=True, acp_stop_reason="cancelled")
    assert status is AgentRunStatus.CANCELLED
    assert disposition == "active"


def test_supervisor_cancel_with_acp_terminal_is_cancelled_active() -> None:
    status, disposition = _finalize(
        dispatch_started=True, acp_stop_reason="cancelled", supervisor_cancelled=True
    )
    assert status is AgentRunStatus.CANCELLED
    assert disposition == "active"


def test_supervisor_timeout_with_acp_terminal_is_timed_out_active() -> None:
    status, disposition = _finalize(
        dispatch_started=True, acp_stop_reason="cancelled", supervisor_timed_out=True
    )
    assert status is AgentRunStatus.TIMED_OUT
    assert disposition == "active"


def test_permission_violation_fails_the_turn_despite_terminal() -> None:
    status, disposition = _finalize(
        dispatch_started=True, acp_stop_reason="end_turn", permission_violation=True
    )
    assert status is AgentRunStatus.FAILED
    assert disposition == "active"


@pytest.mark.parametrize(
    ("flags", "expected_status"),
    [
        ({"supervisor_timed_out": True}, AgentRunStatus.TIMED_OUT),
        ({"supervisor_cancelled": True}, AgentRunStatus.CANCELLED),
        ({}, AgentRunStatus.FAILED),
    ],
)
def test_escalated_kill_after_dispatch_quarantines(flags, expected_status) -> None:
    status, disposition = _finalize(
        dispatch_started=True, escalated_kill_after_dispatch=True, **flags
    )
    assert status is expected_status
    assert disposition == "quarantined"


def test_child_exit_without_terminal_is_failed_quarantined() -> None:
    # A clean-looking exit code can never upgrade this row: exit-code detail
    # is subordinate and the function never sees it.
    status, disposition = _finalize(
        dispatch_started=True, child_exit_without_terminal=True
    )
    assert status is AgentRunStatus.FAILED
    assert disposition == "quarantined"


def test_observation_lost_is_unknown_quarantined() -> None:
    status, disposition = _finalize(
        dispatch_started=True, observation_interrupted=True
    )
    assert status is AgentRunStatus.UNKNOWN
    assert disposition == "quarantined"


def test_dispatched_with_nothing_provable_is_unknown_quarantined() -> None:
    status, disposition = _finalize(dispatch_started=True)
    assert status is AgentRunStatus.UNKNOWN
    assert disposition == "quarantined"


def test_reliable_terminal_wins_over_interrupted_observation() -> None:
    status, disposition = _finalize(
        dispatch_started=True,
        acp_stop_reason="end_turn",
        observation_interrupted=True,
    )
    assert status is AgentRunStatus.COMPLETED
    assert disposition == "active"


def test_result_exists_wins_over_everything() -> None:
    status, disposition = _finalize(
        result_exists=True,
        dispatch_started=True,
        acp_stop_reason="end_turn",
        observation_interrupted=True,
        child_exit_without_terminal=True,
    )
    assert status is None
    assert disposition == "keep"

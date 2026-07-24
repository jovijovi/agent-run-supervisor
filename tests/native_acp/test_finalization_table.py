"""C8 L1: the architecture §5 terminal table as a pure function.

Every row is pinned. Exit-code classification is subordinate by
construction: the function never consults exit codes, so a dispatched Turn
without a reliable ACP terminal can never finalize completed/cancelled-class.
"""

from __future__ import annotations

import pytest

from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
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


def test_pre_dispatch_failure_with_unproven_rollback_quarantines() -> None:
    # Architecture §5: session reusable on pre-dispatch failure only "unless
    # rollback cannot be proven".
    status, disposition = _finalize(dispatch_started=False, rollback_unproven=True)
    assert status is AgentRunStatus.FAILED
    assert disposition == "quarantined"


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
        # B2 (2026-07-24): escalated kill without an ACP terminal is
        # uncertain even when the trigger was the supervisor turn timeout.
        ({"supervisor_timed_out": True}, AgentRunStatus.UNKNOWN),
        ({"supervisor_cancelled": True}, AgentRunStatus.UNKNOWN),
        ({}, AgentRunStatus.FAILED),
    ],
)
def test_escalated_kill_after_dispatch_quarantines(flags, expected_status) -> None:
    status, disposition = _finalize(
        dispatch_started=True, escalated_kill_after_dispatch=True, **flags
    )
    assert status is expected_status
    assert disposition == "quarantined"


def test_supervisor_cancelled_escalated_kill_without_terminal_is_unknown() -> None:
    # PRD R5: the prompt may have executed; without a trustworthy ACP
    # terminal, cancelled-class would overclaim. UNKNOWN is hard-nonretryable.
    status, disposition = _finalize(
        dispatch_started=True,
        escalated_kill_after_dispatch=True,
        supervisor_cancelled=True,
    )
    assert status is AgentRunStatus.UNKNOWN
    assert disposition == "quarantined"
    assert _RETRYABLE_DEFAULT[AgentRunStatus.UNKNOWN] is False


def test_supervisor_timeout_escalated_kill_without_terminal_is_unknown() -> None:
    # B2 (2026-07-24): a dispatched prompt that forced supervisor
    # termination/escalation with no trustworthy ACP terminal may already
    # have executed. timed_out/retryable=true would invite a retry the
    # quarantined Session then refuses; GOAL contract 5 / PRD R5 require
    # unknown + quarantined + hard-nonretryable.
    status, disposition = _finalize(
        dispatch_started=True,
        escalated_kill_after_dispatch=True,
        supervisor_timed_out=True,
    )
    assert status is AgentRunStatus.UNKNOWN
    assert disposition == "quarantined"
    assert _RETRYABLE_DEFAULT[AgentRunStatus.UNKNOWN] is False


def test_trustworthy_cancel_terminal_wins_over_escalated_kill() -> None:
    status, disposition = _finalize(
        dispatch_started=True,
        acp_stop_reason="cancelled",
        escalated_kill_after_dispatch=True,
        supervisor_cancelled=True,
    )
    assert status is AgentRunStatus.CANCELLED
    assert disposition == "active"


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


def test_r4_b2_evidence_pipeline_overrides_completed_to_failed_active() -> None:
    status, disposition = _finalize(
        dispatch_started=True,
        acp_stop_reason="end_turn",
        evidence_pipeline_failure=True,
    )
    assert status is AgentRunStatus.FAILED
    assert disposition == "active"
    assert _RETRYABLE_DEFAULT[status] is False


def test_r4_b2_evidence_pipeline_overrides_cancelled_to_failed() -> None:
    status, disposition = _finalize(
        dispatch_started=True,
        acp_stop_reason="cancelled",
        evidence_pipeline_failure=True,
    )
    assert status is AgentRunStatus.FAILED
    assert disposition == "active"


def test_r4_b2_evidence_pipeline_overrides_terminal_backed_timed_out() -> None:
    # The only remaining timed_out row is ACP-terminal-backed (active);
    # a broken evidence pipeline still downgrades it to failed.
    status, disposition = _finalize(
        dispatch_started=True,
        acp_stop_reason="cancelled",
        supervisor_timed_out=True,
        evidence_pipeline_failure=True,
    )
    assert status is AgentRunStatus.FAILED
    assert disposition == "active"
    assert _RETRYABLE_DEFAULT[status] is False


def test_r4_b2_escalated_timeout_with_evidence_pipeline_stays_unknown() -> None:
    # B2 (2026-07-24): the escalated-timeout row is unknown, and unknown
    # uncertainty remains the strongest status under pipeline failure.
    status, disposition = _finalize(
        dispatch_started=True,
        escalated_kill_after_dispatch=True,
        supervisor_timed_out=True,
        evidence_pipeline_failure=True,
    )
    assert status is AgentRunStatus.UNKNOWN
    assert disposition == "quarantined"
    assert _RETRYABLE_DEFAULT[status] is False


def test_r4_b2_unknown_remains_stronger_than_evidence_pipeline() -> None:
    status, disposition = _finalize(
        dispatch_started=True,
        observation_interrupted=True,
        evidence_pipeline_failure=True,
    )
    assert status is AgentRunStatus.UNKNOWN
    assert disposition == "quarantined"


def test_r4_b2_existing_result_still_irreversible_with_evidence_pipeline() -> None:
    status, disposition = _finalize(
        result_exists=True,
        dispatch_started=True,
        evidence_pipeline_failure=True,
        acp_stop_reason="end_turn",
    )
    assert status is None
    assert disposition == "keep"


def test_r4_b2_pre_dispatch_evidence_pipeline_stays_failed_active() -> None:
    status, disposition = _finalize(
        dispatch_started=False,
        evidence_pipeline_failure=True,
    )
    assert status is AgentRunStatus.FAILED
    assert disposition == "active"

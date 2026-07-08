from __future__ import annotations

import pytest

from agent_run_supervisor.exit_classifier import (
    AgentRunStatus,
    ClassifierInput,
    ClassifierOutput,
    classify_exit,
)


@pytest.mark.parametrize(
    "exit_code,expected_status",
    [
        (0, AgentRunStatus.COMPLETED),
        (1, AgentRunStatus.RUNNER_ERROR),
        (2, AgentRunStatus.INVALID_INVOCATION),
        (3, AgentRunStatus.TIMED_OUT),
        (4, AgentRunStatus.NO_SESSION),
        (5, AgentRunStatus.PERMISSION_DENIED),
        (130, AgentRunStatus.INTERRUPTED),
        (99, AgentRunStatus.INFRASTRUCTURE_ERROR),
    ],
)
def test_classify_exit_table(exit_code: int, expected_status: AgentRunStatus) -> None:
    result = classify_exit(ClassifierInput(exit_code=exit_code))
    assert result.status is expected_status


@pytest.mark.parametrize("exit_code", [1, 2, 3, 4, 5, 99, 130])
def test_nonzero_exit_never_completes(exit_code: int) -> None:
    result = classify_exit(ClassifierInput(exit_code=exit_code))
    assert result.status is not AgentRunStatus.COMPLETED


def test_classify_exit_zero_with_protocol_error_overrides_to_protocol_error() -> None:
    result = classify_exit(
        ClassifierInput(exit_code=0, protocol_error=True),
    )
    assert result.status is AgentRunStatus.PROTOCOL_ERROR


def test_classify_exit_one_with_acpx_runtime_origin_keeps_runner_error() -> None:
    result = classify_exit(
        ClassifierInput(
            exit_code=1,
            acpx_code="RUNTIME",
            origin="acp",
        ),
    )
    assert result.status is AgentRunStatus.RUNNER_ERROR


def test_classify_supervisor_kill_returns_infrastructure_error_with_origin_supervisor() -> None:
    result = classify_exit(
        ClassifierInput(
            exit_code=137,
            supervisor_killed=True,
        ),
    )
    assert result.status in {
        AgentRunStatus.INFRASTRUCTURE_ERROR,
        AgentRunStatus.TIMED_OUT,
    }
    assert result.origin == "supervisor"


def test_classify_usage_with_explicit_usage_acpx_code_classifies_invalid_invocation() -> None:
    result = classify_exit(
        ClassifierInput(
            exit_code=2,
            acpx_code="USAGE",
            origin="cli",
        ),
    )
    assert result.status is AgentRunStatus.INVALID_INVOCATION
    assert result.origin == "cli"


def test_classifier_output_marks_retryable_per_design_table() -> None:
    table = {
        AgentRunStatus.COMPLETED: False,
        AgentRunStatus.INVALID_INVOCATION: False,
        AgentRunStatus.PERMISSION_DENIED: False,
        AgentRunStatus.NO_SESSION: False,
        AgentRunStatus.INTERRUPTED: False,
        AgentRunStatus.TIMED_OUT: True,
        AgentRunStatus.PROTOCOL_ERROR: False,
    }
    for status, expected_retryable in table.items():
        out = ClassifierOutput(status=status, retryable=expected_retryable, origin="cli")
        assert out.retryable is expected_retryable


# --- S2 no-op fail-closed classification -----------------------------------
#
# Regression for the `/goal …` incident: acpx exited 0 with a protocol-clean
# stream but produced no agent output and no tool events, and the run was
# reported `completed`. Exit 0 without an observed effect must now classify
# as the fail-closed `no_op`, never as success.


def test_exit_zero_without_observed_effect_classifies_no_op() -> None:
    result = classify_exit(ClassifierInput(exit_code=0, no_observed_effect=True))

    assert result.status is AgentRunStatus.NO_OP
    assert result.retryable is False
    assert result.detail_code == "NO_OP"
    assert result.status is not AgentRunStatus.COMPLETED


def test_exit_zero_with_observed_effect_stays_completed() -> None:
    result = classify_exit(ClassifierInput(exit_code=0, no_observed_effect=False))

    assert result.status is AgentRunStatus.COMPLETED


def test_protocol_error_takes_precedence_over_no_op() -> None:
    result = classify_exit(
        ClassifierInput(exit_code=0, protocol_error=True, no_observed_effect=True),
    )

    assert result.status is AgentRunStatus.PROTOCOL_ERROR


def test_nonzero_exit_keeps_base_status_despite_no_observed_effect() -> None:
    result = classify_exit(ClassifierInput(exit_code=5, no_observed_effect=True))

    assert result.status is AgentRunStatus.PERMISSION_DENIED


def test_supervisor_timeout_takes_precedence_over_no_op() -> None:
    result = classify_exit(
        ClassifierInput(
            exit_code=3,
            supervisor_timed_out=True,
            supervisor_killed=True,
            no_observed_effect=True,
        ),
    )

    assert result.status is AgentRunStatus.TIMED_OUT
    assert result.origin == "supervisor"


def test_no_op_result_payload_carries_no_op_error_code() -> None:
    from pathlib import Path

    from agent_run_supervisor.result import build_result_payload

    payload = build_result_payload(
        run_id="turn_probe",
        status=AgentRunStatus.NO_OP,
        origin="cli",
        detail_code="NO_OP",
        retryable=False,
        exit_code=0,
        signal=None,
        stop_reason="end_turn",
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=Path("/tmp/turn_probe"),
    )

    assert payload["status"] == "no_op"
    assert payload["error_code"] == "NO_OP"
    assert payload["retryable"] is False

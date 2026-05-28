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

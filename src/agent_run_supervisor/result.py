from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_run_supervisor.exit_classifier import AgentRunStatus


@dataclass
class RunOutcome:
    """Final outcome packaged for callers; result dict is the persisted result.json shape."""

    run_dir: Path
    status: AgentRunStatus
    result: dict[str, Any]


def build_result_payload(
    *,
    run_id: str,
    status: AgentRunStatus,
    origin: str,
    detail_code: str | None,
    retryable: bool,
    exit_code: int | None,
    signal: int | None,
    stop_reason: str | None,
    usage: dict[str, Any] | None,
    final_message: str,
    truncated: bool,
    truncate_reason: str | None,
    run_dir: Path,
    stderr_path: str = "stderr.log",
    raw_event_path: str = "acpx-stdout.ndjson",
    redaction_report_path: str = "redaction-report.json",
    error_code: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": status.value,
        "business_verdict": None,
        "error_code": error_code or _default_error_code(status),
        "detail_code": detail_code,
        "origin": origin,
        "retryable": retryable,
        "acpx_exit_code": exit_code,
        "signal": signal,
        "stop_reason": stop_reason,
        "usage": usage,
        "final_message": final_message,
        "truncated": truncated,
        "truncate_reason": truncate_reason,
        "run_dir": str(run_dir),
        "stderr_path": stderr_path,
        "raw_event_path": raw_event_path,
        "redaction_report_path": redaction_report_path,
    }
    return payload


_ERROR_CODE_FOR_STATUS: dict[AgentRunStatus, str | None] = {
    AgentRunStatus.COMPLETED: None,
    AgentRunStatus.RUNNER_ERROR: "RUNNER_ERROR",
    AgentRunStatus.INVALID_INVOCATION: "INVALID_INVOCATION",
    AgentRunStatus.TIMED_OUT: "TIMED_OUT",
    AgentRunStatus.NO_SESSION: "NO_SESSION",
    AgentRunStatus.PERMISSION_DENIED: "PERMISSION_DENIED",
    AgentRunStatus.INTERRUPTED: "INTERRUPTED",
    AgentRunStatus.PROTOCOL_ERROR: "PROTOCOL_ERROR",
    AgentRunStatus.INFRASTRUCTURE_ERROR: "INFRASTRUCTURE_ERROR",
    AgentRunStatus.POLICY_ERROR: "POLICY_ERROR",
}


def _default_error_code(status: AgentRunStatus) -> str | None:
    return _ERROR_CODE_FOR_STATUS.get(status)

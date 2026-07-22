from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus


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
    observed_effect: bool | None = None,
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
        # Whether the parsed stream showed real agent output/tool activity
        # (parser.has_observed_effect); null when nothing was parsed (dry run).
        "observed_effect": observed_effect,
        "run_dir": str(run_dir),
        "stderr_path": stderr_path,
        "raw_event_path": raw_event_path,
        "redaction_report_path": redaction_report_path,
    }
    return payload


_ERROR_CODE_FOR_STATUS: dict[AgentRunStatus, str | None] = {
    AgentRunStatus.COMPLETED: None,
    AgentRunStatus.NO_OP: "NO_OP",
    AgentRunStatus.RUNNER_ERROR: "RUNNER_ERROR",
    AgentRunStatus.INVALID_INVOCATION: "INVALID_INVOCATION",
    AgentRunStatus.TIMED_OUT: "TIMED_OUT",
    AgentRunStatus.NO_SESSION: "NO_SESSION",
    AgentRunStatus.PERMISSION_DENIED: "PERMISSION_DENIED",
    AgentRunStatus.INTERRUPTED: "INTERRUPTED",
    AgentRunStatus.PROTOCOL_ERROR: "PROTOCOL_ERROR",
    AgentRunStatus.INFRASTRUCTURE_ERROR: "INFRASTRUCTURE_ERROR",
    AgentRunStatus.POLICY_ERROR: "POLICY_ERROR",
    AgentRunStatus.FAILED: "FAILED",
    AgentRunStatus.CANCELLED: "CANCELLED",
    AgentRunStatus.UNKNOWN: "UNKNOWN",
}


def _default_error_code(status: AgentRunStatus) -> str | None:
    return _ERROR_CODE_FOR_STATUS.get(status)


# Native ACP terminal vocabulary (PRD R5). Compat/acpx statuses are never trusted here.
_NATIVE_TERMINAL_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.TIMED_OUT,
        AgentRunStatus.UNKNOWN,
    }
)
_NATIVE_ORIGINS = frozenset({"acp", "supervisor"})
_REQUIRED_NATIVE_RESULT_FIELDS = (
    "run_id",
    "status",
    "business_verdict",
    "error_code",
    "detail_code",
    "origin",
    "retryable",
    "acpx_exit_code",
    "signal",
    "stop_reason",
    "usage",
    "final_message",
    "truncated",
    "truncate_reason",
    "observed_effect",
    "run_dir",
    "stderr_path",
    "raw_event_path",
    "redaction_report_path",
)


def _is_strict_bool(value: Any) -> bool:
    return type(value) is bool


def _is_optional_int(value: Any) -> bool:
    return value is None or (type(value) is int)


def _is_optional_str(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_optional_dict(value: Any) -> bool:
    return value is None or isinstance(value, dict)


def validate_native_terminal_result(
    payload: Any, *, run_id: str
) -> dict[str, Any] | None:
    """Validate a persisted Native ACP terminal ``result.json`` object.

    Requires the complete base shape emitted by :func:`build_result_payload`,
    exact ``run_id``, supported Native statuses only, exact retryability, and
    strict types (``bool`` is not ``int``). Optional Native extension fields may
    remain. Returns the validated payload, or ``None`` when evidence is
    untrusted. Fail-closed: never raises with raw field values.
    """
    if not isinstance(payload, dict):
        return None
    for key in _REQUIRED_NATIVE_RESULT_FIELDS:
        if key not in payload:
            return None
    if payload.get("run_id") != run_id or not isinstance(payload.get("run_id"), str):
        return None
    status_raw = payload.get("status")
    if not isinstance(status_raw, str):
        return None
    try:
        status = AgentRunStatus(status_raw)
    except ValueError:
        return None
    if status not in _NATIVE_TERMINAL_STATUSES:
        return None
    retryable = payload.get("retryable")
    if not _is_strict_bool(retryable):
        return None
    if retryable is not _RETRYABLE_DEFAULT[status]:
        return None
    origin = payload.get("origin")
    if not isinstance(origin, str) or origin not in _NATIVE_ORIGINS:
        return None
    error_code = payload.get("error_code")
    if status is AgentRunStatus.COMPLETED:
        if error_code is not None:
            return None
    elif not isinstance(error_code, str):
        return None
    if not _is_optional_str(payload.get("detail_code")):
        return None
    if not _is_optional_int(payload.get("acpx_exit_code")):
        return None
    if not _is_optional_int(payload.get("signal")):
        return None
    if not _is_optional_str(payload.get("stop_reason")):
        return None
    if not _is_optional_dict(payload.get("usage")):
        return None
    if not isinstance(payload.get("final_message"), str):
        return None
    if not _is_strict_bool(payload.get("truncated")):
        return None
    if not _is_optional_str(payload.get("truncate_reason")):
        return None
    observed = payload.get("observed_effect")
    if observed is not None and not _is_strict_bool(observed):
        return None
    if not isinstance(payload.get("run_dir"), str):
        return None
    if not isinstance(payload.get("stderr_path"), str):
        return None
    if not isinstance(payload.get("raw_event_path"), str):
        return None
    if not isinstance(payload.get("redaction_report_path"), str):
        return None
    # business_verdict is reserved; Native terminals emit null only.
    if payload.get("business_verdict") is not None:
        return None
    return payload

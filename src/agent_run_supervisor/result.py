from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus

# Native final-message UTF-8 budget at ingestion. Sized so worst-case JSON
# escaping plus the result envelope stays below reconciliation's 1 MiB reader
# and UDS MAX_FRAME_BYTES (1 MiB); the exact serialized result ceiling below
# is the hard write gate.
MAX_FINAL_MESSAGE_BYTES = 64 * 1024
# Exact on-disk Native result.json ceiling (write-once indent=2 form).
MAX_NATIVE_RESULT_SERIALIZED_BYTES = 512 * 1024
# Optional terminal fields that can grow from external/error data.
MAX_USAGE_SERIALIZED_BYTES = 4 * 1024
MAX_FAILURE_REASON_BYTES = 4 * 1024


@dataclass
class RunOutcome:
    """Final outcome packaged for callers; result dict is the persisted result.json shape."""

    run_dir: Path
    status: AgentRunStatus
    result: dict[str, Any]


def truncate_utf8_bytes(data: bytes, max_bytes: int) -> bytes:
    """Return a ``data`` prefix of at most ``max_bytes`` on a valid UTF-8 boundary."""
    if max_bytes <= 0 or not data:
        return b""
    chunk = data[:max_bytes]
    while chunk:
        try:
            chunk.decode("utf-8")
            return chunk
        except UnicodeDecodeError:
            chunk = chunk[:-1]
    return b""


def sanitize_usage(usage: Any) -> dict[str, Any] | None:
    """Bound optional Native ``usage``; drop or replace when oversized/untrusted."""
    if usage is None:
        return None
    if not isinstance(usage, dict):
        return None
    try:
        rendered = json.dumps(usage, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if len(rendered.encode("utf-8")) > MAX_USAGE_SERIALIZED_BYTES:
        return {"truncated": True, "truncate_reason": "max_usage_bytes"}
    return usage


# Caller-visible/persisted failure_reason values are fixed categorical phrases.
# detail_code remains the diagnostic contract; never pass through raw exception
# text, paths, class names, or credential-shaped material.
ALLOWED_FAILURE_REASONS = frozenset(
    {
        "admission failed",
        "spawn failed",
        "startup timed out",
        "session load unavailable",
        "silent session recreation",
        "config fidelity failed",
        "evidence pipeline failed",
        "supervisor cancellation",
        "run exception",
        "run failed",
    }
)


def sanitize_failure_reason(reason: Any) -> str | None:
    """Emit only allow-listed categorical ``failure_reason`` phrases."""
    if reason is None:
        return None
    if isinstance(reason, str) and reason in ALLOWED_FAILURE_REASONS:
        return reason
    return "run failed"


def native_result_serialized_size(payload: Mapping[str, Any]) -> int:
    """Byte size of the write-once Native result form (indent=2, sort_keys)."""
    return len(
        json.dumps(dict(payload), sort_keys=True, indent=2).encode("utf-8")
    )


def build_minimal_evidence_pipeline_result(
    *,
    run_id: str,
    run_dir: Path,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Minimal failed/non-retryable EVIDENCE_PIPELINE terminal that fits the ceiling."""
    payload = build_result_payload(
        run_id=run_id,
        status=AgentRunStatus.FAILED,
        origin="supervisor",
        detail_code="EVIDENCE_PIPELINE",
        retryable=False,
        exit_code=None,
        signal=None,
        stop_reason=None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=run_dir,
        raw_event_path="events.jsonl",
    )
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def enforce_native_result_ceiling(
    payload: dict[str, Any],
    *,
    run_id: str,
    run_dir: Path,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return ``payload`` if it fits the exact ceiling; else a minimal terminal."""
    if native_result_serialized_size(payload) <= MAX_NATIVE_RESULT_SERIALIZED_BYTES:
        return payload
    return build_minimal_evidence_pipeline_result(
        run_id=run_id, run_dir=run_dir, session_id=session_id
    )


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


# Completed-class ACP stop reasons (Native finalization table authority).
# Consumed by RunTask and trusted Native terminal validation — single source.
COMPLETED_ACP_STOP_REASONS = frozenset(
    {"end_turn", "max_tokens", "max_turn_requests", "refusal"}
)
_EMERGENCY_FINALIZE_DETAIL = "EMERGENCY_FINALIZE"

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
    exact ``run_id``, supported Native statuses only, exact status-derived
    ``error_code``, exact retryability, trusted status/origin/stop/detail
    grammar from Native emitters, and strict types (``bool`` is not ``int``).
    Optional Native extension fields may remain. Returns the validated
    payload, or ``None`` when evidence is untrusted. Fail-closed: never
    raises with raw field values.
    """
    try:
        return _validate_native_terminal_result_inner(payload, run_id=run_id)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _validate_native_terminal_result_inner(
    payload: Any, *, run_id: str
) -> dict[str, Any] | None:
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
    # Trusted Native rows use the exact status-derived code (None only for
    # completed). Arbitrary non-null codes for non-completed statuses are
    # untrusted — never echo the hostile value.
    if error_code != _default_error_code(status):
        return None
    detail_code = payload.get("detail_code")
    if not _is_optional_str(detail_code):
        return None
    if not _is_optional_int(payload.get("acpx_exit_code")):
        return None
    if not _is_optional_int(payload.get("signal")):
        return None
    stop_reason = payload.get("stop_reason")
    if not _is_optional_str(stop_reason):
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
    # Optional failure_reason: absent/None stay allowed; any present non-null
    # value must be an exact allowlisted categorical string. Never sanitize
    # here — reject so storage classifies the terminal untrusted.
    if "failure_reason" in payload:
        reason = payload["failure_reason"]
        if reason is not None and (
            not isinstance(reason, str) or reason not in ALLOWED_FAILURE_REASONS
        ):
            return None
    if not _native_terminal_semantic_grammar_ok(
        status=status,
        origin=origin,
        stop_reason=stop_reason,
        detail_code=detail_code,
    ):
        return None
    # Exact serialized ceiling: a terminal that cannot be framed/queried is
    # untrusted evidence (fail closed).
    try:
        if native_result_serialized_size(payload) > MAX_NATIVE_RESULT_SERIALIZED_BYTES:
            return None
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    return payload


def _native_terminal_semantic_grammar_ok(
    *,
    status: AgentRunStatus,
    origin: str,
    stop_reason: str | None,
    detail_code: str | None,
) -> bool:
    """Exact trusted Native status/origin/stop/detail grammar from emitters.

    Derived from RunTask finalization, emergency finalize, admission
    registration-failure, and reconcile durable rows — not generic
    ``build_result_payload`` compatibility fixtures.
    """
    # origin=acp always requires ACP stop evidence.
    if origin == "acp" and stop_reason is None:
        return False

    if status is AgentRunStatus.COMPLETED:
        return (
            origin == "acp" and stop_reason in COMPLETED_ACP_STOP_REASONS
        )
    if status is AgentRunStatus.CANCELLED:
        return origin == "acp" and stop_reason == "cancelled"
    if status is AgentRunStatus.UNKNOWN:
        return origin == "supervisor" and stop_reason is None
    if status is AgentRunStatus.TIMED_OUT:
        if origin == "acp":
            return stop_reason is not None
        # Escalated-kill-after-timeout: supervisor, no ACP stop.
        return origin == "supervisor" and stop_reason is None
    if status is AgentRunStatus.FAILED:
        if origin == "acp":
            # Permission-violation / non-completed ACP stop / emergency path
            # may carry any non-null stop reason including completed-class.
            return stop_reason is not None
        if origin != "supervisor":
            return False
        if stop_reason is None:
            # Pre-dispatch, admission, reconcile, child-exit, evidence, etc.
            return True
        # Supervisor + non-null ACP stop: only the emergency-failed shape.
        return detail_code == _EMERGENCY_FINALIZE_DETAIL
    return False

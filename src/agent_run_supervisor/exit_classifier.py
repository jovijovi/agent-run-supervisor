from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AgentRunStatus(str, Enum):
    COMPLETED = "completed"
    NO_OP = "no_op"
    RUNNER_ERROR = "runner_error"
    INVALID_INVOCATION = "invalid_invocation"
    TIMED_OUT = "timed_out"
    NO_SESSION = "no_session"
    PERMISSION_DENIED = "permission_denied"
    INTERRUPTED = "interrupted"
    PROTOCOL_ERROR = "protocol_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    POLICY_ERROR = "policy_error"


_RETRYABLE_DEFAULT: dict[AgentRunStatus, bool] = {
    AgentRunStatus.COMPLETED: False,
    # Fail closed and stay put: a deterministic no-op turn would likely no-op
    # again; the caller must fix the prompt/permissions, not blind-retry.
    AgentRunStatus.NO_OP: False,
    AgentRunStatus.RUNNER_ERROR: True,
    AgentRunStatus.INVALID_INVOCATION: False,
    AgentRunStatus.TIMED_OUT: True,
    AgentRunStatus.NO_SESSION: False,
    AgentRunStatus.PERMISSION_DENIED: False,
    AgentRunStatus.INTERRUPTED: False,
    AgentRunStatus.PROTOCOL_ERROR: False,
    AgentRunStatus.INFRASTRUCTURE_ERROR: True,
    AgentRunStatus.POLICY_ERROR: False,
}

_BASE_STATUS_FOR_EXIT: dict[int, AgentRunStatus] = {
    0: AgentRunStatus.COMPLETED,
    1: AgentRunStatus.RUNNER_ERROR,
    2: AgentRunStatus.INVALID_INVOCATION,
    3: AgentRunStatus.TIMED_OUT,
    4: AgentRunStatus.NO_SESSION,
    5: AgentRunStatus.PERMISSION_DENIED,
    130: AgentRunStatus.INTERRUPTED,
}


@dataclass(frozen=True)
class ClassifierInput:
    exit_code: int
    signal: int | None = None
    acpx_code: str | None = None
    origin: str | None = None
    protocol_error: bool = False
    supervisor_killed: bool = False
    supervisor_timed_out: bool = False
    # True when the parsed stream shows no agent output and no tool activity
    # (see ``parser.has_observed_effect``). Only consulted for exit 0.
    no_observed_effect: bool = False


@dataclass(frozen=True)
class ClassifierOutput:
    status: AgentRunStatus
    retryable: bool
    origin: str
    detail_code: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def _resolve_origin(input_: ClassifierInput, default: str) -> str:
    if input_.supervisor_killed or input_.supervisor_timed_out:
        return "supervisor"
    if input_.origin:
        return input_.origin
    return default


def classify_exit(input_: ClassifierInput) -> ClassifierOutput:
    notes: list[str] = []

    if input_.supervisor_killed or input_.supervisor_timed_out:
        status = (
            AgentRunStatus.TIMED_OUT
            if input_.supervisor_timed_out
            else AgentRunStatus.INFRASTRUCTURE_ERROR
        )
        notes.append("supervisor watchdog/kill engaged")
        return ClassifierOutput(
            status=status,
            retryable=_RETRYABLE_DEFAULT[status],
            origin="supervisor",
            detail_code="SUPERVISOR_KILL",
            notes=tuple(notes),
        )

    base = _BASE_STATUS_FOR_EXIT.get(input_.exit_code, AgentRunStatus.INFRASTRUCTURE_ERROR)

    if base is AgentRunStatus.COMPLETED and input_.protocol_error:
        status = AgentRunStatus.PROTOCOL_ERROR
        notes.append("exit 0 but protocol error observed in stdout stream")
        return ClassifierOutput(
            status=status,
            retryable=_RETRYABLE_DEFAULT[status],
            origin=_resolve_origin(input_, "cli"),
            detail_code="PROTOCOL_ERROR",
            notes=tuple(notes),
        )

    if base is AgentRunStatus.COMPLETED and input_.no_observed_effect:
        status = AgentRunStatus.NO_OP
        notes.append("exit 0 but no agent output or tool activity observed")
        return ClassifierOutput(
            status=status,
            retryable=_RETRYABLE_DEFAULT[status],
            origin=_resolve_origin(input_, "cli"),
            detail_code="NO_OP",
            notes=tuple(notes),
        )

    if base is AgentRunStatus.RUNNER_ERROR:
        acpx_code = (input_.acpx_code or "").upper()
        origin = (input_.origin or "").lower()
        if acpx_code in {"USAGE"}:
            status = AgentRunStatus.INVALID_INVOCATION
        elif acpx_code in {"TIMEOUT"}:
            status = AgentRunStatus.TIMED_OUT
        elif acpx_code in {"NO_SESSION"}:
            status = AgentRunStatus.NO_SESSION
        elif acpx_code in {"PERMISSION_DENIED"}:
            status = AgentRunStatus.PERMISSION_DENIED
        elif acpx_code in {"INFRASTRUCTURE", "SUPERVISOR"}:
            status = AgentRunStatus.INFRASTRUCTURE_ERROR
        elif origin == "supervisor":
            status = AgentRunStatus.INFRASTRUCTURE_ERROR
        else:
            status = AgentRunStatus.RUNNER_ERROR
        if input_.acpx_code:
            notes.append(f"acpxCode={input_.acpx_code}")
        return ClassifierOutput(
            status=status,
            retryable=_RETRYABLE_DEFAULT[status],
            origin=_resolve_origin(input_, origin or "acp"),
            detail_code=input_.acpx_code,
            notes=tuple(notes),
        )

    status = base
    return ClassifierOutput(
        status=status,
        retryable=_RETRYABLE_DEFAULT[status],
        origin=_resolve_origin(input_, "cli"),
        detail_code=input_.acpx_code,
        notes=tuple(notes),
    )

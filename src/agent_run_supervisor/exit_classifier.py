from __future__ import annotations

from enum import Enum


class AgentRunStatus(str, Enum):
    """The complete Native ACP terminal vocabulary (PRD R5).

    Five members, and deliberately no sixth. The wider set this enum once
    carried existed to classify a supervised process exit into a runner-shaped
    status; that classifier and every emitter of those statuses are gone, so the
    members left the enum with them rather than lingering as values nothing can
    produce and nothing accepts.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


_RETRYABLE_DEFAULT: dict[AgentRunStatus, bool] = {
    AgentRunStatus.COMPLETED: False,
    AgentRunStatus.TIMED_OUT: True,
    AgentRunStatus.FAILED: False,
    AgentRunStatus.CANCELLED: False,
    # unknown is hard-False: a possibly-dispatched prompt is never retried.
    AgentRunStatus.UNKNOWN: False,
}

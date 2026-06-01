"""Progress/result view-model construction (caller-owned presentation).

The view-model is the caller-owned presentation projection. It maps STRUCTURAL
normalized-event evidence to a progress card (preparing/running/needs-permission/
completed/error lifecycle) and maps a completed ``CallerResult`` + caller-derived
verdict to a result card. It carries the supervisor status as *evidence* (never a
verdict), the already-redacted ``final_message`` as *untrusted* findings text, a
local evidence reference (no upload), and a session-lifecycle chip. It never
surfaces bulk agent content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent_run_supervisor.caller import CallerResult

from .events import NormalizedEventView
from .verdict import VerdictDecision

_DEFAULT_TITLE = "Document Check"

# Result-card phase reflects the supervisor status: completed -> COMPLETED,
# any non-success status -> ERROR.
_SUCCESS_STATUSES = frozenset({"completed"})

# Families that contribute a structural activity line to the progress list.
_ACTIVITY_FAMILIES = frozenset(
    {"tool_started", "tool_updated", "tool_completed", "agent_message_delta"}
)

_TOOL_STATE_BY_STATUS = {
    "in_progress": "in_progress",
    "completed": "done",
    "failed": "failed",
}


class CardPhase(str, Enum):
    PREPARING = "preparing"
    RUNNING = "running"
    NEEDS_PERMISSION = "needs_permission"
    COMPLETED = "completed"
    ERROR = "error"
    VERDICT = "verdict"


@dataclass(frozen=True)
class ProgressItem:
    label: str              # structural activity line (kind/status only)
    state: str              # in_progress | done | failed


@dataclass(frozen=True)
class CardViewModel:
    phase: CardPhase
    title: str
    progress: list[ProgressItem] = field(default_factory=list)
    supervisor_status: str | None = None      # evidence chip, NOT a verdict
    findings_text: str | None = None          # redacted final_message, untrusted
    verdict: VerdictDecision | None = None     # caller-owned banner
    session_lifecycle: str | None = None       # opened | alive | closed
    evidence_ref: str | None = None            # local run_dir/session_dir; no upload


def _phase_from_events(events: list[NormalizedEventView]) -> CardPhase:
    families = {event.family for event in events}
    if "run_failed" in families:
        return CardPhase.ERROR
    if "run_completed" in families:
        return CardPhase.COMPLETED
    if "permission_requested" in families:
        return CardPhase.NEEDS_PERMISSION
    if families:
        return CardPhase.RUNNING
    return CardPhase.PREPARING


def _tool_state(event: NormalizedEventView) -> str:
    if event.status in _TOOL_STATE_BY_STATUS:
        return _TOOL_STATE_BY_STATUS[event.status]
    return "done" if event.family == "tool_completed" else "in_progress"


def _progress_items(events: list[NormalizedEventView]) -> list[ProgressItem]:
    items: list[ProgressItem] = []
    for event in events:
        if event.family not in _ACTIVITY_FAMILIES:
            continue
        if event.family == "agent_message_delta":
            length = event.text_length
            label = (
                f"drafting findings ({length} chars)"
                if length is not None
                else "drafting findings"
            )
            items.append(ProgressItem(label=label, state="in_progress"))
            continue
        action = event.family.replace("tool_", "")
        kind = event.kind or "tool"
        items.append(ProgressItem(label=f"{kind} ({action})", state=_tool_state(event)))
    return items


def build_progress_view_model(
    events: list[NormalizedEventView],
    *,
    session_lifecycle: str | None = None,
) -> CardViewModel:
    """Map normalized-event evidence to a running/preparing progress view-model."""
    return CardViewModel(
        phase=_phase_from_events(events),
        title=_DEFAULT_TITLE,
        progress=_progress_items(events),
        session_lifecycle=session_lifecycle,
    )


def build_result_view_model(
    result: CallerResult,
    events: list[NormalizedEventView],
    decision: VerdictDecision,
    *,
    session_lifecycle: str | None = None,
) -> CardViewModel:
    """Map a completed ``CallerResult`` + verdict to a result view-model.

    ``findings_text`` is the already-redacted ``final_message``, carried as
    UNTRUSTED text (escaping happens at the presentation adapter, not here).
    """
    status = result.supervisor_status
    phase = CardPhase.COMPLETED if status in _SUCCESS_STATUSES else CardPhase.ERROR
    findings = result.result.get("final_message")
    evidence_ref = result.run_dir or result.artifact_dir
    return CardViewModel(
        phase=phase,
        title=_DEFAULT_TITLE,
        progress=_progress_items(events),
        supervisor_status=status,
        findings_text=findings,
        verdict=decision,
        session_lifecycle=session_lifecycle,
        evidence_ref=evidence_ref,
    )

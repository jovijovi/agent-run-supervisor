"""Caller-owned business verdict derivation from a generic ``CallerResult``.

The supervisor never decides business pass/fail: its ``business_verdict`` stays
``null`` and its ``status`` is *evidence* only. This module derives the
caller-owned document-check verdict from that status plus the already-redacted
``final_message`` — and deliberately ignores any (forged) ``business_verdict``
carried in the supervisor payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent_run_supervisor.caller import CallerResult

# A supervisor status is "success" only when the run completed; everything else
# (timed_out, *_error, permission_denied, interrupted, ...) is non-success.
_SUCCESS_STATUSES = frozenset({"completed"})

# Clean-signal phrases removed before scanning for issue markers, so a clean
# message like "no issues found" is not itself read as an issue.
_CLEAN_PHRASES = (
    "no issues found",
    "no issues",
    "no problems found",
    "no problems",
)

# Substrings that mark issue/fail/block-like findings in the redacted message.
_ISSUE_MARKERS = (
    "issue",
    "missing",
    "broken",
    "revis",
    "fail",
    "block",
    "incomplete",
    "inconsistent",
    "error",
    "unresolved",
    "must be",
)


class BusinessVerdict(str, Enum):
    PASS = "PASS"
    NEEDS_REVISION = "NEEDS_REVISION"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class VerdictDecision:
    verdict: BusinessVerdict
    rationale: str                  # caller-owned, derived from status + findings
    supervisor_status: str | None   # carried as EVIDENCE, never equated with verdict


def _findings_look_clean(message: str | None) -> bool:
    text = (message or "").lower()
    for phrase in _CLEAN_PHRASES:
        text = text.replace(phrase, " ")
    return not any(marker in text for marker in _ISSUE_MARKERS)


def derive_verdict(result: CallerResult) -> VerdictDecision:
    """Derive the caller-owned document-check verdict from a ``CallerResult``.

    Status is evidence, not a pass/fail. A non-success status blocks regardless
    of message text; a completed run is PASS only when its findings look clean,
    otherwise NEEDS_REVISION. A forged ``business_verdict`` is never trusted.
    """
    status = result.supervisor_status
    final_message = result.result.get("final_message")

    if status not in _SUCCESS_STATUSES:
        return VerdictDecision(
            verdict=BusinessVerdict.BLOCK,
            rationale=f"Run did not complete cleanly (supervisor status: {status!r}).",
            supervisor_status=status,
        )

    if not (final_message or "").strip():
        # A blank message is not a clean bill of health: a completed check
        # that produced no findings text gives us nothing to derive PASS from
        # (e.g. a tools-only silent turn). Fail closed instead of promoting
        # silence to success.
        return VerdictDecision(
            verdict=BusinessVerdict.BLOCK,
            rationale="Completed but produced no output; a blank report cannot pass.",
            supervisor_status=status,
        )

    if _findings_look_clean(final_message):
        return VerdictDecision(
            verdict=BusinessVerdict.PASS,
            rationale="Completed check reported no blocking findings.",
            supervisor_status=status,
        )

    return VerdictDecision(
        verdict=BusinessVerdict.NEEDS_REVISION,
        rationale="Completed check reported findings that require revision.",
        supervisor_status=status,
    )

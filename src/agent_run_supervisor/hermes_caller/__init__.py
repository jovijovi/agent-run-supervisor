"""Concrete Hermes caller + offline Feishu view-model adapter (caller-side).

A stdlib-only, caller-side subpackage that builds a concrete document-check
caller (``Hermes``) and an OFFLINE Feishu rich-card view-model adapter above the
generic I1 caller boundary. It depends only on the public supervisor surfaces
(``agent_run_supervisor.caller`` and ``agent_run_supervisor.role``) and never
imports supervisor internals.

Everything here is fake/local/offline: no real Feishu API, no IM delivery, no
public ingress, no Gateway/Sachima behavior, no automatic replies, and no
platform fields in the generic supervisor contract. The business verdict, the
view-model, and the card rendering are all caller-owned; the supervisor status
is carried as evidence only and never equated with the verdict.
"""
from __future__ import annotations

from .events import (
    EventPage,
    EventRecord,
    NormalizedEventView,
    ProgressSnapshot,
    load_events,
    load_progress,
    read_event_page,
)
from .feishu_adapter import escape_untrusted, to_feishu_card_payload
from .hermes import HermesDocCheckCaller
from .intake import (
    build_check_prompt,
    build_exec_spec,
    build_session_close_spec,
    build_session_create_spec,
    build_session_send_spec,
    build_session_status_spec,
    resolve_document,
)
from .task import DocCheckTask
from .verdict import BusinessVerdict, VerdictDecision, derive_verdict
from .view_model import (
    CardPhase,
    CardViewModel,
    ProgressItem,
    build_progress_view_model,
    build_result_view_model,
)

__all__ = [
    "DocCheckTask",
    "BusinessVerdict",
    "VerdictDecision",
    "derive_verdict",
    "NormalizedEventView",
    "load_events",
    "EventRecord",
    "EventPage",
    "read_event_page",
    "ProgressSnapshot",
    "load_progress",
    "CardPhase",
    "CardViewModel",
    "ProgressItem",
    "build_progress_view_model",
    "build_result_view_model",
    "escape_untrusted",
    "to_feishu_card_payload",
    "resolve_document",
    "build_check_prompt",
    "build_exec_spec",
    "build_session_create_spec",
    "build_session_send_spec",
    "build_session_status_spec",
    "build_session_close_spec",
    "HermesDocCheckCaller",
]

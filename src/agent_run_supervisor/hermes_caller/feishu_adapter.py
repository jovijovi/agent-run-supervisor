"""OFFLINE view-model -> Feishu rich-card payload dict (no delivery).

This adapter turns a caller-owned ``CardViewModel`` into a plain, JSON-
serializable Feishu card payload dict and STOPS there. The payload is NEVER
sent: there is no Feishu SDK/import, no HTTP, no token, no recipient — and the
returned dict carries no platform/delivery identifier at any depth. All
untrusted text is escaped (never rendered as trusted Markdown/HTML).
"""
from __future__ import annotations

import html
from typing import Any

from .view_model import CardViewModel


def escape_untrusted(text: str | None) -> str:
    """Escape untrusted agent text for safe plain rendering.

    Uses ``html.escape`` and NEVER renders trusted Markdown/HTML. ``None`` maps
    to the empty string so callers can render a missing field safely.
    """
    return html.escape(text or "")


def _verdict_block(view_model: CardViewModel) -> dict[str, Any] | None:
    decision = view_model.verdict
    if decision is None:
        return None
    return {
        "verdict": decision.verdict.value,
        "rationale": escape_untrusted(decision.rationale),
        "supervisor_status": decision.supervisor_status,
    }


def to_feishu_card_payload(view_model: CardViewModel) -> dict[str, Any]:
    """Build an OFFLINE Feishu rich-card payload dict from the view-model.

    Returns a plain dict only — escaped, delivery-free, and platform-free.
    """
    return {
        "schema": "2.0",
        "card": {
            "header": {
                "title": escape_untrusted(view_model.title),
                "phase": view_model.phase.value,
            },
            "body": {
                "supervisor_status": view_model.supervisor_status,
                "verdict": _verdict_block(view_model),
                "progress": [
                    {"label": escape_untrusted(item.label), "state": item.state}
                    for item in view_model.progress
                ],
                "findings": escape_untrusted(view_model.findings_text),
                "session_lifecycle": view_model.session_lifecycle,
                "evidence_ref": escape_untrusted(view_model.evidence_ref),
            },
        },
    }

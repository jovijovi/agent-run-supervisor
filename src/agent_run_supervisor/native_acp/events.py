"""ACP session-update → normalized-event-family projection.

Maps wire-shaped update dicts (the C5 client dumps SDK models by alias) into
the existing caller-stable normalized-event schema: structural fields only,
``text_length`` instead of any text body, and ``unknown_update`` with a
``key:type`` summary for forward compatibility. Bodies are never copied.
"""

from __future__ import annotations

from typing import Any, Mapping


def _key_summary(update: Mapping[str, Any]) -> str:
    return ",".join(
        f"{key}:{type(value).__name__}" for key, value in sorted(update.items())
    )


def _text_length(update: Mapping[str, Any]) -> int | None:
    content = update.get("content")
    if isinstance(content, Mapping):
        text = content.get("text")
        if isinstance(text, str):
            return len(text)
    return None


class NativeAcpEventNormalizer:
    """Stateless projection of one session update into one event dict."""

    def normalize_update(self, update: Mapping[str, Any]) -> dict[str, Any]:
        update_type = update.get("sessionUpdate")
        if update_type == "agent_message_chunk":
            event: dict[str, Any] = {"type": "agent_message_delta"}
            length = _text_length(update)
            if length is not None:
                event["text_length"] = length
            return event
        if update_type == "agent_thought_chunk":
            return {"type": "agent_thought_delta"}
        if update_type == "usage_update":
            return {"type": "usage_updated"}
        if update_type == "available_commands_update":
            return {"type": "available_commands_updated"}
        if update_type == "tool_call":
            return {
                "type": "tool_started",
                "tool_call_id": update.get("toolCallId"),
                "kind": update.get("kind"),
            }
        if update_type == "tool_call_update":
            status = update.get("status")
            family = (
                "tool_completed"
                if status in {"completed", "failed"}
                else "tool_updated"
            )
            return {
                "type": family,
                "tool_call_id": update.get("toolCallId"),
                "status": status,
            }
        return {
            "type": "unknown_update",
            "update_type": update_type if isinstance(update_type, str) else None,
            "key_summary": _key_summary(update),
        }

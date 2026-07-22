"""C7: ACP session-update → normalized-event-family projection.

Structural fields only — the normalizer never copies text bodies
(``text_length`` only) and unknown update types degrade to ``unknown_update``
with a ``key_summary`` of ``key:type`` hints.
"""

from __future__ import annotations

import json

from agent_run_supervisor.native_acp.events import NativeAcpEventNormalizer

SECRET_TEXT = "SECRET-BODY-a1b2c3-never-persist"


def _normalizer() -> NativeAcpEventNormalizer:
    return NativeAcpEventNormalizer()


def test_agent_message_chunk_maps_to_delta_with_text_length_only() -> None:
    event = _normalizer().normalize_update(
        {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": SECRET_TEXT},
        }
    )
    assert event == {
        "type": "agent_message_delta",
        "text_length": len(SECRET_TEXT),
    }
    assert SECRET_TEXT not in json.dumps(event)


def test_agent_thought_chunk_maps_without_any_body() -> None:
    event = _normalizer().normalize_update(
        {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": SECRET_TEXT},
        }
    )
    assert event == {"type": "agent_thought_delta"}


def test_usage_and_available_commands_map_to_bare_families() -> None:
    normalizer = _normalizer()
    assert normalizer.normalize_update({"sessionUpdate": "usage_update"}) == {
        "type": "usage_updated"
    }
    assert normalizer.normalize_update(
        {"sessionUpdate": "available_commands_update", "availableCommands": []}
    ) == {"type": "available_commands_updated"}


def test_tool_call_maps_to_tool_started() -> None:
    event = _normalizer().normalize_update(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "tool-9",
            "kind": "read",
            "title": SECRET_TEXT,
        }
    )
    assert event == {"type": "tool_started", "tool_call_id": "tool-9", "kind": "read"}


def test_tool_call_update_status_vocabulary() -> None:
    normalizer = _normalizer()
    completed = normalizer.normalize_update(
        {"sessionUpdate": "tool_call_update", "toolCallId": "t", "status": "completed"}
    )
    failed = normalizer.normalize_update(
        {"sessionUpdate": "tool_call_update", "toolCallId": "t", "status": "failed"}
    )
    running = normalizer.normalize_update(
        {"sessionUpdate": "tool_call_update", "toolCallId": "t", "status": "in_progress"}
    )
    assert completed["type"] == "tool_completed"
    assert failed["type"] == "tool_completed"
    assert running["type"] == "tool_updated"
    assert running == {"type": "tool_updated", "tool_call_id": "t", "status": "in_progress"}


def test_unknown_update_carries_structural_key_summary_only() -> None:
    event = _normalizer().normalize_update(
        {
            "sessionUpdate": "config_option_update",
            "configOptions": [{"id": "model", "currentValue": SECRET_TEXT}],
        }
    )
    assert event["type"] == "unknown_update"
    assert event["update_type"] == "config_option_update"
    assert "configOptions:list" in event["key_summary"]
    assert SECRET_TEXT not in json.dumps(event)


def test_adversarial_unknown_update_never_copies_values() -> None:
    event = _normalizer().normalize_update(
        {
            "sessionUpdate": "totally_new_thing",
            "payload": {"nested": SECRET_TEXT},
            "note": SECRET_TEXT,
        }
    )
    assert event["type"] == "unknown_update"
    rendered = json.dumps(event)
    assert SECRET_TEXT not in rendered
    assert "payload:dict" in event["key_summary"]
    assert "note:str" in event["key_summary"]


def test_missing_update_type_degrades_to_unknown() -> None:
    event = _normalizer().normalize_update({"weird": True})
    assert event["type"] == "unknown_update"
    assert event["update_type"] is None

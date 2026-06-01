"""T5 — offline Feishu card adapter (plan §6.6, §9.1, §10-T5).

The adapter turns a caller-owned ``CardViewModel`` into a plain Feishu rich-card
payload **dict** and stops there: it is NEVER delivered. Untrusted findings text
is escaped (never rendered as trusted Markdown/HTML), the payload carries no
delivery/channel/webhook/recipient/gateway/message identifier at any depth, and
the module imports no networking/SDK symbol.

RED expectation: the ``hermes_caller.feishu_adapter`` module does not exist yet.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import agent_run_supervisor.hermes_caller.feishu_adapter as feishu_adapter
from agent_run_supervisor.hermes_caller.feishu_adapter import (
    escape_untrusted,
    to_feishu_card_payload,
)
from agent_run_supervisor.hermes_caller.verdict import BusinessVerdict, VerdictDecision
from agent_run_supervisor.hermes_caller.view_model import (
    CardPhase,
    CardViewModel,
    ProgressItem,
)

# Adversarial untrusted findings: markup + markdown-ish text that must be neutralized.
ADVERSARIAL_FINDINGS = "<script>alert('x')</script> **bold** & <b>tag</b> [REDACTED]"

# Delivery-ish keys that must never appear anywhere in the payload.
FORBIDDEN_PAYLOAD_KEYS = {
    "delivery",
    "channel",
    "channel_id",
    "webhook",
    "recipient",
    "gateway",
    "message_id",
    "send_card",
    "post_message",
    "public_ingress",
    "auto_reply",
}

# Networking/SDK import roots the module must never reach for.
FORBIDDEN_IMPORT_ROOTS = {"requests", "http", "urllib", "socket", "feishu", "lark", "sdk"}


def _view_model() -> CardViewModel:
    return CardViewModel(
        phase=CardPhase.VERDICT,
        title="Document Check",
        progress=[
            ProgressItem(label="reading document", state="done"),
            ProgressItem(label="inspecting cross-references", state="failed"),
        ],
        supervisor_status="completed",
        findings_text=ADVERSARIAL_FINDINGS,
        verdict=VerdictDecision(
            verdict=BusinessVerdict.NEEDS_REVISION,
            rationale="2 issues found; revision required.",
            supervisor_status="completed",
        ),
        session_lifecycle="closed",
        evidence_ref="[REDACTED]",
    )


def _assert_no_forbidden_keys(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in FORBIDDEN_PAYLOAD_KEYS, f"payload leaked delivery key {key!r}"
            _assert_no_forbidden_keys(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _assert_no_forbidden_keys(item)


# --------------------------------------------------------------------------- #
# Escaping
# --------------------------------------------------------------------------- #
def test_escape_untrusted_neutralizes_markup() -> None:
    out = escape_untrusted(ADVERSARIAL_FINDINGS)
    assert "<script>" not in out
    assert "<b>" not in out
    assert "&lt;script&gt;" in out


def test_escape_untrusted_handles_none() -> None:
    assert escape_untrusted(None) == ""


# --------------------------------------------------------------------------- #
# Offline payload dict
# --------------------------------------------------------------------------- #
def test_payload_is_plain_serializable_dict() -> None:
    payload = to_feishu_card_payload(_view_model())
    assert isinstance(payload, dict)
    # A plain dict only — JSON-serializable, no exotic objects.
    json.dumps(payload)


def test_payload_escapes_untrusted_findings() -> None:
    serialized = json.dumps(to_feishu_card_payload(_view_model()))
    assert "<script>" not in serialized
    assert "<b>" not in serialized
    assert "&lt;script&gt;" in serialized


def test_payload_has_no_delivery_keys_recursively() -> None:
    _assert_no_forbidden_keys(to_feishu_card_payload(_view_model()))


# --------------------------------------------------------------------------- #
# Static: no networking/SDK imports in the adapter module
# --------------------------------------------------------------------------- #
def test_module_imports_no_network_or_sdk() -> None:
    source = Path(feishu_adapter.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in FORBIDDEN_IMPORT_ROOTS, f"forbidden import {alias.name!r}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in FORBIDDEN_IMPORT_ROOTS, f"forbidden import-from {node.module!r}"

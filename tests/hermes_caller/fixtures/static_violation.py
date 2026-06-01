"""SCRATCH FIXTURE — deliberately violates the forbidden-surface rules.

This file is NOT production code and is NOT a test module (pytest does not
collect ``fixtures/static_violation.py``). It exists only so
``test_no_forbidden_surface.py`` can prove its static guard actually *catches*
banned imports and platform/delivery surfaces, instead of vacuously passing.

It is syntactically valid Python (so ``compileall`` stays green) but seeds, on
purpose, every category the guard must reject:

  * a networking/SDK import (``socket``, ``urllib``);
  * a supervisor-internal import the caller layer must never reach into
    (``session_runtime``);
  * platform/delivery identifiers used as real fields/calls
    (``webhook``, ``send_card``, ``post_message``, ``delivery_state``).

None of these may ever appear in the real ``hermes_caller`` package as an
import, field, or call — only as non-approval / scenario prose in comments.
"""
from __future__ import annotations

import socket
from urllib import request

from agent_run_supervisor import session_runtime  # forbidden supervisor internal


def send_card(payload: dict) -> None:
    """Forbidden delivery surface: a real caller must never deliver."""
    webhook = "https://example.invalid/ingress"
    delivery_state = {"channel": "[REDACTED]", "recipient": "[REDACTED]"}
    conn = socket.socket()
    request.urlopen(webhook)
    _ = (session_runtime, conn, delivery_state)


def post_message(card: dict) -> None:
    """Second forbidden delivery surface."""
    send_card(card)

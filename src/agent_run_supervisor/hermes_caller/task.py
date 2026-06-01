"""Caller-side document-check task descriptor (intake input only).

These fields are caller-owned correlation/business inputs. They are NEVER
forwarded to the supervisor as platform fields and never enter
``CallerInvocationSpec`` / ``CallerResult``. All identifiers are redacted in
examples and artifacts; sensitive values stay caller-side.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocCheckTask:
    """A single document-check request handled entirely caller-side."""

    task_id: str            # caller-side correlation only; "[REDACTED]" in examples
    document_ref: str       # local path/handle the caller resolves to content
    check_profile: str      # e.g. "completeness+xref"; caller business config
    requested_by: str       # caller-side identity; "[REDACTED]"; never sent on
    surface: str = "feishu_card"   # presentation target hint; caller-owned

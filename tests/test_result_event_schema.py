"""W3 fidelity guard for ``docs/design/result-event-schema.md``.

This test pins the *documented* top-level ``result.json`` key set against the
keys ``result.build_result_payload`` actually emits, so the caller-stable schema
doc can never silently drift from the code. The doc embeds its canonical key
table between machine-readable markers; this test parses that table and compares
it to ``set(build_result_payload(...).keys())``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.result import (
    build_result_payload,
    validate_native_terminal_result,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DOC = _REPO_ROOT / "docs" / "design" / "result-event-schema.md"

_KEYS_BEGIN = "<!-- result-json-keys:begin -->"
_KEYS_END = "<!-- result-json-keys:end -->"
# A documented key is the backtick-wrapped token in the first column of a table
# row inside the marked block, e.g. ``| `run_id` | string | ... |``.
_FIRST_COLUMN_KEY = re.compile(r"^\|\s*`([^`]+)`\s*\|")

_NATIVE_STATUSES = (
    AgentRunStatus.COMPLETED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
    AgentRunStatus.TIMED_OUT,
    AgentRunStatus.UNKNOWN,
)


def _documented_result_keys() -> set[str]:
    text = _SCHEMA_DOC.read_text(encoding="utf-8")
    start = text.index(_KEYS_BEGIN) + len(_KEYS_BEGIN)
    end = text.index(_KEYS_END, start)
    keys: set[str] = set()
    for line in text[start:end].splitlines():
        match = _FIRST_COLUMN_KEY.match(line)
        if match:
            keys.add(match.group(1))
    return keys


def _actual_result_keys() -> set[str]:
    payload = build_result_payload(
        run_id="run_probe",
        status=AgentRunStatus.COMPLETED,
        origin="cli",
        detail_code=None,
        retryable=False,
        exit_code=0,
        signal=None,
        stop_reason="end_turn",
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=Path("/tmp/run_probe"),
    )
    return set(payload.keys())


def _native_payload(
    status: AgentRunStatus,
    *,
    run_id: str = "run_probe",
    origin: str = "supervisor",
    **overrides,
) -> dict:
    payload = build_result_payload(
        run_id=run_id,
        status=status,
        origin=origin,
        detail_code="PROBE" if status is not AgentRunStatus.COMPLETED else None,
        retryable=_RETRYABLE_DEFAULT[status],
        exit_code=None,
        signal=None,
        stop_reason=None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=Path("/tmp/run_probe"),
        raw_event_path="events.jsonl",
    )
    payload.update(overrides)
    return payload


def test_schema_doc_exists_with_frontmatter() -> None:
    assert _SCHEMA_DOC.is_file(), f"missing schema doc: {_SCHEMA_DOC}"
    text = _SCHEMA_DOC.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "schema doc must open with a YAML frontmatter block"


def test_documented_result_keys_match_build_result_payload() -> None:
    documented = _documented_result_keys()
    actual = _actual_result_keys()
    assert documented, "no result.json keys parsed from the schema doc markers"
    assert documented == actual, (
        "result.json schema doc drifted from build_result_payload(): "
        f"missing_from_doc={sorted(actual - documented)} "
        f"extra_in_doc={sorted(documented - actual)}"
    )


@pytest.mark.parametrize("status", _NATIVE_STATUSES)
def test_validate_native_terminal_accepts_all_supported_statuses(
    status: AgentRunStatus,
) -> None:
    payload = _native_payload(status)
    # Optional Native extension fields may remain.
    payload["session_id"] = "sess-ext"
    validated = validate_native_terminal_result(payload, run_id="run_probe")
    assert validated is not None
    assert validated["status"] == status.value
    assert validated["retryable"] is _RETRYABLE_DEFAULT[status]


def test_validate_native_terminal_rejects_runner_error_compat_status() -> None:
    payload = _native_payload(AgentRunStatus.FAILED)
    payload["status"] = AgentRunStatus.RUNNER_ERROR.value
    payload["error_code"] = "RUNNER_ERROR"
    payload["retryable"] = True
    assert validate_native_terminal_result(payload, run_id="run_probe") is None


def test_validate_native_terminal_rejects_missing_required_field() -> None:
    payload = _native_payload(AgentRunStatus.COMPLETED)
    del payload["run_dir"]
    assert validate_native_terminal_result(payload, run_id="run_probe") is None


def test_validate_native_terminal_rejects_bool_as_int_and_wrong_types() -> None:
    payload = _native_payload(AgentRunStatus.FAILED)
    payload["retryable"] = 0  # bool is not int; int is not bool
    assert validate_native_terminal_result(payload, run_id="run_probe") is None
    payload = _native_payload(AgentRunStatus.FAILED)
    payload["truncated"] = 1
    assert validate_native_terminal_result(payload, run_id="run_probe") is None
    payload = _native_payload(AgentRunStatus.FAILED)
    payload["acpx_exit_code"] = True
    assert validate_native_terminal_result(payload, run_id="run_probe") is None


def test_validate_native_terminal_rejects_invalid_origin_and_error_code_rules() -> None:
    payload = _native_payload(AgentRunStatus.COMPLETED, origin="cli")
    assert validate_native_terminal_result(payload, run_id="run_probe") is None
    payload = _native_payload(AgentRunStatus.COMPLETED)
    payload["error_code"] = "FAILED"
    assert validate_native_terminal_result(payload, run_id="run_probe") is None
    payload = _native_payload(AgentRunStatus.FAILED)
    payload["error_code"] = None
    assert validate_native_terminal_result(payload, run_id="run_probe") is None
    payload = _native_payload(AgentRunStatus.FAILED)
    payload["run_id"] = "other"
    assert validate_native_terminal_result(payload, run_id="run_probe") is None


@pytest.mark.parametrize(
    ("origin", "trusted"),
    [
        ([], False),
        ({}, False),
        (1, False),
        (True, False),
        ("", False),
        ("cli", False),
        ("acp", True),
        ("supervisor", True),
    ],
)
def test_r4_residual_origin_malformed_types_never_raise(
    origin, trusted: bool
) -> None:
    """Unhashable/malformed origin must return None — never TypeError."""
    payload = _native_payload(AgentRunStatus.FAILED, origin="supervisor")
    payload["origin"] = origin
    validated = validate_native_terminal_result(payload, run_id="run_probe")
    if trusted:
        assert validated is not None
        assert validated["origin"] == origin
    else:
        assert validated is None

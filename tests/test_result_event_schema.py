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
    COMPLETED_ACP_STOP_REASONS,
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


def _legal_native_defaults(status: AgentRunStatus) -> dict:
    """Per-status legal trusted Native shape from real emitters (not broad fixtures)."""
    if status is AgentRunStatus.COMPLETED:
        return {
            "origin": "acp",
            "stop_reason": "end_turn",
            "detail_code": None,
        }
    if status is AgentRunStatus.CANCELLED:
        return {
            "origin": "acp",
            "stop_reason": "cancelled",
            "detail_code": None,
        }
    if status is AgentRunStatus.TIMED_OUT:
        # Escalated-kill-after-timeout row (supervisor, no ACP stop).
        return {
            "origin": "supervisor",
            "stop_reason": None,
            "detail_code": None,
        }
    if status is AgentRunStatus.UNKNOWN:
        return {
            "origin": "supervisor",
            "stop_reason": None,
            "detail_code": "RECONCILED_UNKNOWN",
        }
    # FAILED: admission/reconcile/pre-dispatch supervisor row.
    return {
        "origin": "supervisor",
        "stop_reason": None,
        "detail_code": "REGISTRATION_FAILED",
    }


def _native_payload(
    status: AgentRunStatus,
    *,
    run_id: str = "run_probe",
    origin: str | None = None,
    **overrides,
) -> dict:
    defaults = _legal_native_defaults(status)
    if origin is not None:
        defaults["origin"] = origin
    payload = build_result_payload(
        run_id=run_id,
        status=status,
        origin=defaults["origin"],
        detail_code=defaults["detail_code"],
        retryable=_RETRYABLE_DEFAULT[status],
        exit_code=None,
        signal=None,
        stop_reason=defaults["stop_reason"],
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
def test_validate_native_terminal_accepts_legal_per_status_payloads(
    status: AgentRunStatus,
) -> None:
    payload = _native_payload(status)
    # Optional Native extension fields may remain.
    payload["session_id"] = "sess-ext"
    validated = validate_native_terminal_result(payload, run_id="run_probe")
    assert validated is not None
    assert validated["status"] == status.value
    assert validated["retryable"] is _RETRYABLE_DEFAULT[status]


@pytest.mark.parametrize("stop_reason", sorted(COMPLETED_ACP_STOP_REASONS))
def test_validate_native_terminal_accepts_each_completed_acp_stop_reason(
    stop_reason: str,
) -> None:
    payload = _native_payload(AgentRunStatus.COMPLETED, stop_reason=stop_reason)
    assert validate_native_terminal_result(payload, run_id="run_probe") is not None


def test_validate_native_terminal_accepts_legal_emitter_variants() -> None:
    # timed_out + ACP stop (supervisor timed out after ACP terminal).
    assert (
        validate_native_terminal_result(
            _native_payload(
                AgentRunStatus.TIMED_OUT,
                origin="acp",
                stop_reason="end_turn",
                detail_code=None,
            ),
            run_id="run_probe",
        )
        is not None
    )
    # failed + ACP stop (non-completed / permission path).
    assert (
        validate_native_terminal_result(
            _native_payload(
                AgentRunStatus.FAILED,
                origin="acp",
                stop_reason="end_turn",
                detail_code=None,
            ),
            run_id="run_probe",
        )
        is not None
    )
    # Emergency-failed with supervisor origin + ACP stop evidence.
    assert (
        validate_native_terminal_result(
            _native_payload(
                AgentRunStatus.FAILED,
                origin="supervisor",
                stop_reason="end_turn",
                detail_code="EMERGENCY_FINALIZE",
            ),
            run_id="run_probe",
        )
        is not None
    )


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
    payload["error_code"] = "TIMED_OUT"  # wrong status-derived code
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
    if origin == "acp":
        # ACP origin requires non-null stop evidence for a trusted failed row.
        payload["stop_reason"] = "end_turn"
    validated = validate_native_terminal_result(payload, run_id="run_probe")
    if trusted:
        assert validated is not None
        assert validated["origin"] == origin
    else:
        assert validated is None


@pytest.mark.parametrize(
    "hostile",
    [
        # completed/cancelled forged with supervisor + no stop
        {
            "status": AgentRunStatus.COMPLETED,
            "origin": "supervisor",
            "stop_reason": None,
            "detail_code": None,
            "error_code": None,
        },
        {
            "status": AgentRunStatus.CANCELLED,
            "origin": "supervisor",
            "stop_reason": None,
            "detail_code": None,
            "error_code": "CANCELLED",
        },
        # ACP origin without stop reason
        {
            "status": AgentRunStatus.FAILED,
            "origin": "acp",
            "stop_reason": None,
            "detail_code": None,
            "error_code": "FAILED",
        },
        {
            "status": AgentRunStatus.TIMED_OUT,
            "origin": "acp",
            "stop_reason": None,
            "detail_code": None,
            "error_code": "TIMED_OUT",
        },
        # completed without completed-class stop / cancelled mismatch
        {
            "status": AgentRunStatus.COMPLETED,
            "origin": "acp",
            "stop_reason": "cancelled",
            "detail_code": None,
            "error_code": None,
        },
        {
            "status": AgentRunStatus.COMPLETED,
            "origin": "acp",
            "stop_reason": "not_a_stop",
            "detail_code": None,
            "error_code": None,
        },
        {
            "status": AgentRunStatus.CANCELLED,
            "origin": "acp",
            "stop_reason": "end_turn",
            "detail_code": None,
            "error_code": "CANCELLED",
        },
        # unknown requires supervisor + no stop
        {
            "status": AgentRunStatus.UNKNOWN,
            "origin": "acp",
            "stop_reason": "end_turn",
            "detail_code": None,
            "error_code": "UNKNOWN",
        },
        {
            "status": AgentRunStatus.UNKNOWN,
            "origin": "supervisor",
            "stop_reason": "end_turn",
            "detail_code": "EMERGENCY_FINALIZE",
            "error_code": "UNKNOWN",
        },
        # supervisor + stop only legal for emergency-failed
        {
            "status": AgentRunStatus.FAILED,
            "origin": "supervisor",
            "stop_reason": "end_turn",
            "detail_code": "REGISTRATION_FAILED",
            "error_code": "FAILED",
        },
        {
            "status": AgentRunStatus.TIMED_OUT,
            "origin": "supervisor",
            "stop_reason": "end_turn",
            "detail_code": "EMERGENCY_FINALIZE",
            "error_code": "TIMED_OUT",
        },
        # arbitrary non-null error code for non-completed
        {
            "status": AgentRunStatus.FAILED,
            "origin": "supervisor",
            "stop_reason": None,
            "detail_code": "REGISTRATION_FAILED",
            "error_code": "RUNNER_ERROR",
        },
        {
            "status": AgentRunStatus.TIMED_OUT,
            "origin": "supervisor",
            "stop_reason": None,
            "detail_code": None,
            "error_code": "FAILED",
        },
    ],
)
def test_r13_b2_hostile_status_origin_stop_error_detail_never_trusted(
    hostile: dict,
) -> None:
    """Cross-product incoherence fails closed — None, never raise/echo."""
    status = hostile["status"]
    payload = _native_payload(status)
    payload["origin"] = hostile["origin"]
    payload["stop_reason"] = hostile["stop_reason"]
    payload["detail_code"] = hostile["detail_code"]
    payload["error_code"] = hostile["error_code"]
    # Canary in an unused extension must never surface via exceptions.
    payload["hostile_canary"] = "sk-live-" + "LEAKCANARY-grammar"
    assert validate_native_terminal_result(payload, run_id="run_probe") is None


def test_r5_b1_validate_rejects_over_exact_serialized_ceiling() -> None:
    from agent_run_supervisor.result import (
        MAX_NATIVE_RESULT_SERIALIZED_BYTES,
        native_result_serialized_size,
    )

    payload = _native_payload(AgentRunStatus.COMPLETED)
    payload["final_message"] = "x" * (MAX_NATIVE_RESULT_SERIALIZED_BYTES)
    assert native_result_serialized_size(payload) > MAX_NATIVE_RESULT_SERIALIZED_BYTES
    assert validate_native_terminal_result(payload, run_id="run_probe") is None


def test_r5_b1_minimal_evidence_pipeline_fits_and_validates() -> None:
    from agent_run_supervisor.result import (
        MAX_NATIVE_RESULT_SERIALIZED_BYTES,
        build_minimal_evidence_pipeline_result,
        native_result_serialized_size,
    )

    payload = build_minimal_evidence_pipeline_result(
        run_id="run_probe",
        run_dir=Path("/tmp/run_probe"),
        session_id="sess",
    )
    assert native_result_serialized_size(payload) <= MAX_NATIVE_RESULT_SERIALIZED_BYTES
    assert validate_native_terminal_result(payload, run_id="run_probe") is not None
    assert payload["status"] == "failed"
    assert payload["retryable"] is False
    assert payload["detail_code"] == "EVIDENCE_PIPELINE"


def test_r6_b3_validate_never_raises_on_cyclic_payload() -> None:
    payload: dict = {"run_id": "run_probe"}
    payload["self"] = payload
    assert validate_native_terminal_result(payload, run_id="run_probe") is None


def test_r6_b3_validate_never_raises_on_deep_payload() -> None:
    node: dict = {"leaf": 1}
    for _ in range(5000):
        node = {"n": node}
    payload = {"run_id": "run_probe", "deep": node}
    assert validate_native_terminal_result(payload, run_id="run_probe") is None


def test_r10_b3_sanitize_failure_reason_rejects_raw_hostile_text() -> None:
    import secrets

    from agent_run_supervisor.result import sanitize_failure_reason

    canary_path = "/tmp/" + "leakcanary-" + secrets.token_hex(4)
    canary_secret = "sk-" + secrets.token_hex(12)
    hostile = f"OSError at {canary_path} token={canary_secret}"
    cleaned = sanitize_failure_reason(hostile)
    assert cleaned == "run failed"
    assert canary_path not in cleaned
    assert canary_secret not in cleaned
    assert sanitize_failure_reason("spawn failed") == "spawn failed"
    assert sanitize_failure_reason("supervisor cancellation") == (
        "supervisor cancellation"
    )
    assert sanitize_failure_reason(None) is None


def test_r11_b1_validate_rejects_hostile_failure_reason() -> None:
    """Persisted Native terminals: non-allowlisted failure_reason is INVALID."""
    import secrets

    from agent_run_supervisor.result import ALLOWED_FAILURE_REASONS

    canary_path = "/tmp/" + "leakcanary-" + secrets.token_hex(4)
    canary_secret = "sk-" + secrets.token_hex(12)
    canary_provider = "provider-exception-Traceback"
    hostile = (
        f"OSError at {canary_path}; token={canary_secret}; "
        f"{canary_provider}: credential material"
    )
    assert validate_native_terminal_result(
        _native_payload(AgentRunStatus.FAILED, failure_reason=hostile),
        run_id="run_probe",
    ) is None
    assert validate_native_terminal_result(
        _native_payload(AgentRunStatus.FAILED, failure_reason=123),
        run_id="run_probe",
    ) is None
    assert validate_native_terminal_result(
        _native_payload(AgentRunStatus.FAILED, failure_reason="not allowlisted"),
        run_id="run_probe",
    ) is None

    # Absent key and explicit None remain allowed; categorical stays trusted.
    absent = _native_payload(AgentRunStatus.FAILED)
    assert "failure_reason" not in absent
    assert validate_native_terminal_result(absent, run_id="run_probe") is not None
    assert (
        validate_native_terminal_result(
            _native_payload(AgentRunStatus.FAILED, failure_reason=None),
            run_id="run_probe",
        )
        is not None
    )
    allowed = "spawn failed"
    assert allowed in ALLOWED_FAILURE_REASONS
    validated = validate_native_terminal_result(
        _native_payload(AgentRunStatus.FAILED, failure_reason=allowed),
        run_id="run_probe",
    )
    assert validated is not None
    assert validated["failure_reason"] == allowed

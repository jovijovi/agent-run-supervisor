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
        signal=None,
        stop_reason="end_turn",
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=Path("/tmp/run_probe"),
        raw_event_path="events.jsonl",
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
    # ``session_id`` is a *declared* optional of the closed set, not an
    # extension the validator tolerates.
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


def test_validate_native_terminal_rejects_a_retired_status() -> None:
    """``runner_error`` is a string a stale record can still carry, not a member."""
    payload = _native_payload(AgentRunStatus.FAILED)
    payload["status"] = "runner_error"
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
    payload["signal"] = True
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


# -- WP2.7: the free-form terminal field is typed, not merely documented -----


def _native_builder_fields(tmp_path):
    from agent_run_supervisor.exit_classifier import AgentRunStatus as Status

    return dict(
        run_id="run_probe",
        status=Status.COMPLETED,
        origin="acp",
        detail_code=None,
        retryable=False,
        signal=None,
        stop_reason="end_turn",
        usage=None,
        truncated=False,
        truncate_reason=None,
        run_dir=tmp_path,
        raw_event_path="events.jsonl",
    )


def test_native_result_builder_accepts_ordinary_bounded_text(tmp_path) -> None:
    """``final_message`` is the one field a child authors end to end.

    It arrives already statically redacted and already bounded by the
    final-message ceiling, and it is retained as written. There is no per-Run
    literal set to test it against any more.
    """
    from agent_run_supervisor.result import build_native_result_payload

    payload = build_native_result_payload(
        final_message="said terminal-sentinel-4c19 aloud",
        **_native_builder_fields(tmp_path),
    )

    assert payload["final_message"] == "said terminal-sentinel-4c19 aloud"


def test_native_result_builder_refuses_a_non_string_final_message(tmp_path) -> None:
    """``result.json`` is durable JSON, so the seam judges the type.

    A str *subclass* is refused as well: ``type(x) is str`` rather than
    ``isinstance``, so an object that overrides ``__str__``, ``__eq__``, or
    ``__class__`` cannot talk its way into a serializer.
    """
    from agent_run_supervisor.result import build_native_result_payload

    class Impostor(str):
        pass

    for candidate in (None, 17, Impostor("raw child text")):
        with pytest.raises(TypeError):
            build_native_result_payload(
                final_message=candidate, **_native_builder_fields(tmp_path)
            )


# -- API v3 carries no process-exit field ------------------------------------
#
# Both directions of one decision, and neither is a compatibility path. Forward:
# a v3 result does not carry the retired key, and nothing writes one. Backward:
# there is no backward — API v3 is the only contract, so a record carrying that
# key, or any key this version does not define, is untrusted evidence and is
# refused whole. No tolerant reader, projection, alias, or migration exists for
# it, and no stored record is rewritten.

_RETIRED_EXIT_FIELD = "acpx" + "_exit_code"


def test_v3_results_carry_no_process_exit_field() -> None:
    for status in _NATIVE_STATUSES:
        payload = _native_payload(status)
        assert _RETIRED_EXIT_FIELD not in payload
        assert "exit_code" not in payload
        assert validate_native_terminal_result(payload, run_id="run_probe") is not None


def test_the_builder_refuses_a_process_exit_argument() -> None:
    """Not renamed and not re-added under a neutral spelling — removed."""
    for keyword in (_RETIRED_EXIT_FIELD, "exit_code"):
        with pytest.raises(TypeError):
            build_result_payload(
                run_id="run_probe",
                status=AgentRunStatus.COMPLETED,
                origin="acp",
                detail_code=None,
                retryable=False,
                signal=None,
                stop_reason="end_turn",
                usage=None,
                final_message="",
                truncated=False,
                truncate_reason=None,
                run_dir=Path("/tmp/run_probe"),
                raw_event_path="events.jsonl",
                **{keyword: 0},
            )


def test_the_required_field_set_no_longer_demands_a_process_exit_field() -> None:
    from agent_run_supervisor.result import _REQUIRED_NATIVE_RESULT_FIELDS

    assert _RETIRED_EXIT_FIELD not in _REQUIRED_NATIVE_RESULT_FIELDS
    assert "exit_code" not in _REQUIRED_NATIVE_RESULT_FIELDS


@pytest.mark.parametrize(
    "legacy_value", [0, 3, 137, None, "137", True, {"unexpected": "type"}, []]
)
def test_a_record_carrying_the_retired_key_is_untrusted(legacy_value) -> None:
    """API v3 is the only contract, so the retired key makes a record invalid.

    There is no tolerant reader and no projection: a projection is itself a way
    of reading a record this version does not define, and it is exactly how the
    field reached a wire response before. The field set is closed, so the record
    is refused whole — and refused for *any* value, because consulting the value
    would be the reading the decision forbids.
    """
    payload = _native_payload(AgentRunStatus.COMPLETED)
    payload[_RETIRED_EXIT_FIELD] = legacy_value

    assert validate_native_terminal_result(payload, run_id="run_probe") is None


@pytest.mark.parametrize("unknown", ["exit_code", "acpx_code", "future_extension"])
def test_a_record_carrying_any_unknown_field_is_untrusted(unknown: str) -> None:
    """The rule is the closed field set, not a blocklist of one retired name."""
    payload = _native_payload(AgentRunStatus.COMPLETED)
    payload[unknown] = "whatever"

    assert validate_native_terminal_result(payload, run_id="run_probe") is None


def test_the_recognized_optional_fields_still_validate() -> None:
    """Closing the set must not refuse what current emitters actually write."""
    payload = _native_payload(AgentRunStatus.FAILED)
    payload["session_id"] = "sess-ext"
    payload["failure_reason"] = "spawn failed"

    validated = validate_native_terminal_result(payload, run_id="run_probe")

    assert validated is not None
    assert validated["session_id"] == "sess-ext"
    assert validated["failure_reason"] == "spawn failed"


def test_the_retired_key_is_not_reachable_from_any_current_source_path() -> None:
    """No current module reads, types, branches on, copies, or re-emits it."""
    src = _REPO_ROOT / "src"
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in sorted(src.rglob("*.py"))
        if _RETIRED_EXIT_FIELD in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_run_text_storage_refuses_a_non_string() -> None:
    from agent_run_supervisor.native_acp import storage

    class _Handle:
        def write_text(self, name: str, value: str):  # pragma: no cover
            raise AssertionError("the refusal must happen before the write")

    class Impostor(str):
        pass

    with pytest.raises(TypeError):
        storage.write_run_text(_Handle(), "stderr.log", Impostor("raw child text"))

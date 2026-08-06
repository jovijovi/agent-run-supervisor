"""The Native terminal-status vocabulary, pinned as a closed set.

``AgentRunStatus`` once carried a second, wider vocabulary: the statuses a
process-exit classifier produced for the retired runtime. That classifier and
every emitter of those statuses are gone, so the enum is now exactly the five
Native terminals of PRD R5 — and *exactly* is the pin. A member that nothing can
produce and nothing accepts is not a harmless leftover: it is a value a hostile
or stale ``result.json`` can still claim, and the validator's trust decision is
built on this set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.result import (
    _ERROR_CODE_FOR_STATUS,
    _NATIVE_TERMINAL_STATUSES,
    build_result_payload,
    validate_native_terminal_result,
)

#: The complete vocabulary. PRD R5, and nothing beside it.
NATIVE_TERMINALS = ("completed", "failed", "cancelled", "timed_out", "unknown")

#: Statuses the retired process-exit classifier produced. None may return.
RETIRED_STATUSES = (
    "no_op",
    "runner_error",
    "invalid_invocation",
    "no_session",
    "permission_denied",
    "interrupted",
    "protocol_error",
    "infrastructure_error",
    "policy_error",
)


def _payload(status: AgentRunStatus, **overrides) -> dict:
    payload = build_result_payload(
        run_id="run-native-test",
        status=status,
        origin="supervisor",
        detail_code=None,
        retryable=_RETRYABLE_DEFAULT[status],
        signal=None,
        stop_reason=None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=Path("/tmp/run-native-test"),
        raw_event_path="events.jsonl",
    )
    payload.update(overrides)
    return payload


# -- the enum is the vocabulary ----------------------------------------------


def test_the_enum_is_exactly_the_native_terminal_vocabulary() -> None:
    assert {status.value for status in AgentRunStatus} == set(NATIVE_TERMINALS)
    assert _NATIVE_TERMINAL_STATUSES == set(AgentRunStatus)


@pytest.mark.parametrize("value", RETIRED_STATUSES)
def test_a_retired_status_is_no_longer_a_member(value: str) -> None:
    with pytest.raises(ValueError):
        AgentRunStatus(value)


def test_the_derived_tables_narrowed_with_the_enum() -> None:
    """A table row for a member that no longer exists is an unreachable branch."""
    assert set(_RETRYABLE_DEFAULT) == set(AgentRunStatus)
    assert set(_ERROR_CODE_FOR_STATUS) == set(AgentRunStatus)


def test_the_exit_classifier_module_exports_no_classifier() -> None:
    """The runtime that needed a process-exit classification is gone with it."""
    import agent_run_supervisor.exit_classifier as exit_classifier

    for name in ("classify_exit", "ClassifierInput", "ClassifierOutput"):
        assert not hasattr(exit_classifier, name), name


# -- payload behaviour per member --------------------------------------------


@pytest.mark.parametrize("value", NATIVE_TERMINALS)
def test_member_constructs_and_round_trips_json(value: str) -> None:
    status = AgentRunStatus(value)
    assert status.value == value
    assert json.loads(json.dumps({"status": status.value}))["status"] == value


@pytest.mark.parametrize(
    ("value", "error_code"),
    [
        ("completed", None),
        ("failed", "FAILED"),
        ("cancelled", "CANCELLED"),
        ("timed_out", "TIMED_OUT"),
        ("unknown", "UNKNOWN"),
    ],
)
def test_result_payload_carries_the_status_derived_error_code(
    value: str, error_code: str | None
) -> None:
    payload = _payload(AgentRunStatus(value))
    assert payload["status"] == value
    assert payload["error_code"] == error_code
    assert payload["retryable"] is _RETRYABLE_DEFAULT[AgentRunStatus(value)]


@pytest.mark.parametrize("value", NATIVE_TERMINALS)
def test_result_payload_round_trips_through_disk(tmp_path: Path, value: str) -> None:
    payload = _payload(AgentRunStatus(value))
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    reread = json.loads(path.read_text(encoding="utf-8"))
    assert reread["status"] == value
    assert reread["retryable"] is _RETRYABLE_DEFAULT[AgentRunStatus(value)]


@pytest.mark.parametrize("value", RETIRED_STATUSES)
def test_a_persisted_record_claiming_a_retired_status_is_untrusted(
    tmp_path: Path, value: str
) -> None:
    """A record from the retired line is unreadable evidence, not a terminal.

    The enum lookup is what refuses it, so the refusal cannot be forgotten in a
    branch: there is no member to compare against any more.
    """
    payload = _payload(AgentRunStatus.FAILED)
    payload["status"] = value
    assert validate_native_terminal_result(payload, run_id="run-native-test") is None

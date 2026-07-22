"""C2: additive Native terminal-status vocabulary (failed/cancelled/unknown).

Consumer-behavior pins follow the C1 G5 audit record: enum construction alone
proves nothing about consumers, so every consumer classified intolerant or
semantically meaningful gets a focused behavior test here. acpx classification
must remain provably unchanged (zero-coercion sweep).
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_run_supervisor import commands, session_inspect
from agent_run_supervisor.caller import CallerResult
from agent_run_supervisor.exit_classifier import (
    _RETRYABLE_DEFAULT,
    AgentRunStatus,
    ClassifierInput,
    classify_exit,
)
from agent_run_supervisor.hermes_caller.verdict import BusinessVerdict, derive_verdict
from agent_run_supervisor.hermes_caller.view_model import (
    CardPhase,
    build_result_view_model,
)
from agent_run_supervisor.result import RunOutcome, build_result_payload

NEW_MEMBERS = ("failed", "cancelled", "unknown")


def _payload(status: AgentRunStatus) -> dict:
    return build_result_payload(
        run_id="run-native-test",
        status=status,
        origin="supervisor",
        detail_code=None,
        retryable=False,
        exit_code=None,
        signal=None,
        stop_reason=None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=Path("/tmp/run-native-test"),
    )


def _caller_result(status_value: str) -> CallerResult:
    return CallerResult(
        mode="exec",
        supervisor_status=status_value,
        result={"status": status_value, "final_message": "diagnostic text"},
        artifact_dir=None,
        run_dir="/tmp/run-native-test",
        session_dir=None,
    )


@pytest.mark.parametrize("value", NEW_MEMBERS)
def test_new_member_constructs_and_round_trips_json(value: str) -> None:
    status = AgentRunStatus(value)
    assert status.value == value
    assert json.loads(json.dumps({"status": status.value}))["status"] == value


@pytest.mark.parametrize("value", NEW_MEMBERS)
def test_retryable_default_is_false_for_new_members(value: str) -> None:
    assert _RETRYABLE_DEFAULT[AgentRunStatus(value)] is False


@pytest.mark.parametrize(
    ("value", "error_code"),
    [("failed", "FAILED"), ("cancelled", "CANCELLED"), ("unknown", "UNKNOWN")],
)
def test_result_payload_carries_new_members(value: str, error_code: str) -> None:
    payload = _payload(AgentRunStatus(value))
    assert payload["status"] == value
    assert payload["retryable"] is False
    assert payload["error_code"] == error_code


@pytest.mark.parametrize("value", NEW_MEMBERS)
def test_result_payload_round_trips_with_retryable_false(
    tmp_path: Path, value: str
) -> None:
    payload = _payload(AgentRunStatus(value))
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    reread = json.loads(path.read_text(encoding="utf-8"))
    assert reread["status"] == value
    assert reread["retryable"] is False
    assert reread["error_code"] == value.upper()


def test_classify_exit_never_returns_new_members() -> None:
    # Zero-coercion guard: acpx classification is provably unchanged — no
    # input combination in the bounded sweep produces a Native-only member.
    new_members = {AgentRunStatus(value) for value in NEW_MEMBERS}
    exit_codes = (0, 1, 2, 3, 4, 5, 99, 130)
    acpx_codes = (
        None,
        "USAGE",
        "TIMEOUT",
        "NO_SESSION",
        "PERMISSION_DENIED",
        "INFRASTRUCTURE",
        "SUPERVISOR",
        "MYSTERY",
    )
    origins = (None, "supervisor", "acp")
    flag_grid = tuple(itertools.product((False, True), repeat=4))
    for exit_code, acpx_code, origin, flags in itertools.product(
        exit_codes, acpx_codes, origins, flag_grid
    ):
        protocol_error, killed, timed_out, no_effect = flags
        output = classify_exit(
            ClassifierInput(
                exit_code=exit_code,
                acpx_code=acpx_code,
                origin=origin,
                protocol_error=protocol_error,
                supervisor_killed=killed,
                supervisor_timed_out=timed_out,
                no_observed_effect=no_effect,
            )
        )
        assert output.status not in new_members, (exit_code, acpx_code, flags)


def test_known_turn_statuses_include_new_members() -> None:
    # Documents the deliberate vocabulary growth of the derived frozenset.
    assert set(NEW_MEMBERS) <= session_inspect._KNOWN_TURN_STATUSES


@pytest.mark.parametrize("value", NEW_MEMBERS)
def test_read_turn_status_returns_new_members(tmp_path: Path, value: str) -> None:
    status = AgentRunStatus(value)
    turn_dir = tmp_path / "turn-0001"
    turn_dir.mkdir()
    (turn_dir / "result.json").write_text(
        json.dumps({"status": status.value}), encoding="utf-8"
    )
    assert session_inspect._read_turn_status(turn_dir) == value


class _StatusRunner:
    def __init__(self, status: AgentRunStatus) -> None:
        self._status = status

    def run(self, *, role, prompt, cwd, **_) -> RunOutcome:
        return RunOutcome(
            run_dir=Path("/tmp/run-native-test"),
            status=self._status,
            result={"status": self._status.value},
        )


@pytest.mark.parametrize("value", NEW_MEMBERS)
def test_cmd_run_maps_new_members_to_exit_1(
    monkeypatch: pytest.MonkeyPatch,
    role_file: Path,
    prompt_file: Path,
    value: str,
) -> None:
    status = AgentRunStatus(value)
    monkeypatch.setattr(commands, "SupervisorRunner", lambda **_: _StatusRunner(status))
    args = argparse.Namespace(
        role=str(role_file),
        prompt_file=str(prompt_file),
        no_real_run=False,
        cwd=None,
        runs_dir=None,
    )
    assert commands.cmd_run(args) == 1


class _StatusRuntime:
    def __init__(self, status_value: str) -> None:
        self._value = status_value

    def send(self, *, role, session_id, prompt, cwd=None, **_) -> SimpleNamespace:
        return SimpleNamespace(result={"status": self._value})


@pytest.mark.parametrize("value", NEW_MEMBERS)
def test_cmd_session_send_maps_new_members_to_exit_1(
    monkeypatch: pytest.MonkeyPatch,
    role_file: Path,
    prompt_file: Path,
    value: str,
) -> None:
    status = AgentRunStatus(value)
    monkeypatch.setattr(
        commands, "SessionRuntime", lambda **_: _StatusRuntime(status.value)
    )
    args = argparse.Namespace(
        session_command="send",
        role=str(role_file),
        prompt_file=str(prompt_file),
        goal_file=None,
        session_id="native-test-session",
        cwd=None,
        sessions_dir=None,
    )
    assert commands.cmd_session(args) == 1


@pytest.mark.parametrize("value", NEW_MEMBERS)
def test_verdict_treats_new_members_as_non_success(value: str) -> None:
    status = AgentRunStatus(value)
    decision = derive_verdict(_caller_result(status.value))
    assert decision.verdict is not BusinessVerdict.PASS
    assert decision.supervisor_status == value


@pytest.mark.parametrize("value", NEW_MEMBERS)
def test_view_model_treats_new_members_as_error_with_verbatim_chip(
    value: str,
) -> None:
    status = AgentRunStatus(value)
    result = _caller_result(status.value)
    decision = derive_verdict(result)
    view_model = build_result_view_model(result, [], decision)
    assert view_model.phase is CardPhase.ERROR
    assert view_model.supervisor_status == value

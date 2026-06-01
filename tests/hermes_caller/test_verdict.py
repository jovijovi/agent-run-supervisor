"""T2 — caller-owned business verdict derivation (plan §6.3, §10-T2).

Invariants under test:
  * the verdict is derived caller-side from supervisor *status* (evidence) plus
    the already-redacted ``final_message`` — it is NOT the supervisor status;
  * a ``completed`` status does not force PASS (issues -> NEEDS_REVISION) and a
    non-success status forces BLOCK even with clean-looking findings — proving
    status != verdict in both directions;
  * derivation never trusts a (forged) ``business_verdict`` in the supervisor
    payload, which the supervisor contractually always leaves ``null``.

GREEN coverage: the ``hermes_caller.verdict`` module exists and owns caller verdicts.
"""
from __future__ import annotations

from typing import Any, Callable

from agent_run_supervisor.caller import CallerResult
from agent_run_supervisor.hermes_caller.verdict import (
    BusinessVerdict,
    VerdictDecision,
    derive_verdict,
)


def test_completed_with_clean_findings_is_pass(
    make_caller_result: Callable[..., CallerResult], run_result_pass: dict[str, Any]
) -> None:
    decision = derive_verdict(make_caller_result(run_result_pass))

    assert isinstance(decision, VerdictDecision)
    assert decision.verdict is BusinessVerdict.PASS
    # Supervisor status is carried as EVIDENCE, never equated with the verdict.
    assert decision.supervisor_status == "completed"
    assert isinstance(decision.rationale, str) and decision.rationale.strip()


def test_completed_with_issues_is_needs_revision_not_pass(
    make_caller_result: Callable[..., CallerResult], run_result_issues: dict[str, Any]
) -> None:
    decision = derive_verdict(make_caller_result(run_result_issues))

    # A 'completed' supervisor status must NOT auto-promote to PASS.
    assert decision.verdict is BusinessVerdict.NEEDS_REVISION
    assert decision.supervisor_status == "completed"


def test_non_success_status_is_block(
    make_caller_result: Callable[..., CallerResult], run_result_failed: dict[str, Any]
) -> None:
    decision = derive_verdict(make_caller_result(run_result_failed))

    assert decision.verdict is BusinessVerdict.BLOCK
    assert decision.supervisor_status == "timed_out"


def test_status_failure_blocks_even_with_clean_findings(
    make_caller_result: Callable[..., CallerResult], run_result_failed: dict[str, Any]
) -> None:
    # A reassuring final_message cannot rescue a non-success supervisor status:
    # the status is evidence, and a failed run blocks regardless of message text.
    forged = dict(run_result_failed)
    forged["final_message"] = "No issues found; everything looks complete and consistent."

    decision = derive_verdict(make_caller_result(forged))

    assert decision.verdict is BusinessVerdict.BLOCK


def test_ignores_forged_business_verdict_on_success(
    make_caller_result: Callable[..., CallerResult], run_result_pass: dict[str, Any]
) -> None:
    # The supervisor always leaves business_verdict null; if a payload nonetheless
    # carries one, the caller must NOT trust it. A forged BLOCK must be ignored.
    tampered = dict(run_result_pass)
    tampered["business_verdict"] = "BLOCK"

    decision = derive_verdict(make_caller_result(tampered))

    assert decision.verdict is BusinessVerdict.PASS


def test_ignores_forged_business_verdict_on_failure(
    make_caller_result: Callable[..., CallerResult], run_result_failed: dict[str, Any]
) -> None:
    tampered = dict(run_result_failed)
    tampered["business_verdict"] = "PASS"

    decision = derive_verdict(make_caller_result(tampered))

    assert decision.verdict is BusinessVerdict.BLOCK


def test_supervisor_status_is_evidence_distinct_from_verdict(
    make_caller_result: Callable[..., CallerResult], run_result_issues: dict[str, Any]
) -> None:
    decision = derive_verdict(make_caller_result(run_result_issues))

    # status is a plain supervisor enum string; verdict is a caller-owned enum.
    assert isinstance(decision.supervisor_status, str)
    assert isinstance(decision.verdict, BusinessVerdict)
    assert decision.supervisor_status != decision.verdict.value

#!/usr/bin/env python3
"""Independently adjudicate raw ARS batch controller evidence."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Mapping


EVIDENCE_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
PASS = "PASS"
FAIL = "FAIL"
INDETERMINATE = "INDETERMINATE"
_TERMINAL_CLASSES = frozenset(
    {"completed", "failed", "cancelled", "timed_out", "unknown"}
)

_MANIFEST_KEYS = frozenset(
    {"schema_version", "matrix_schema_version", "preflight", "planned_cases"}
)
_PLAN_KEYS = frozenset(
    {"case_ref", "round_index", "case_index", "evidence_relpath"}
)
_COMPLETION_KEYS = frozenset(
    {"schema_version", "case_count", "completed_case_refs"}
)
_PREFLIGHT_KEYS = frozenset({"server_info", "checks"})
_PREFLIGHT_CHECK_KEYS = frozenset(
    {
        "api_constraint",
        "daemon_version_constraint",
        "registry_membership",
        "capacity",
        "event_budget",
        "matrix_limits",
    }
)
_CASE_KEYS = frozenset(
    {
        "schema_version",
        "case_id",
        "round_index",
        "case_index",
        "request",
        "prompt",
        "task_checker",
        "event_constraints",
        "workspace_relpath",
        "events_relpath",
        "controller",
    }
)
_CONTROLLER_KEYS = frozenset(
    {
        "submission_attempts",
        "request_id",
        "submit_ack",
        "submit_error",
        "terminal_observation",
        "observation_errors",
        "events_exhausted",
        "session_observation",
        "task_checker",
    }
)
_CHECKER_SPEC_KEYS = frozenset({"argv", "timeout_seconds"})
_EVENT_CONSTRAINT_KEYS = frozenset(
    {
        "required_event_types",
        "forbidden_event_types",
        "required_permission_decisions",
        "forbidden_permission_decisions",
    }
)


class AdjudicationError(Exception):
    """Raw evidence is absent, malformed, incomplete, or would be overwritten."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdjudicationError("evidence JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    del value
    raise AdjudicationError("evidence JSON contains a non-JSON numeric constant")


def _strict_json_loads(raw: str) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def _read_regular_json(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise AdjudicationError("evidence JSON must be a regular file")
        payload = _strict_json_loads(path.read_text(encoding="utf-8"))
    except AdjudicationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise AdjudicationError("evidence JSON could not be read") from None
    if type(payload) is not dict:
        raise AdjudicationError("evidence JSON must contain an object")
    return payload


def _closed(payload: Mapping[str, Any], keys: frozenset[str], where: str) -> None:
    if type(payload) is not dict or set(payload) != set(keys):
        raise AdjudicationError(f"{where} violates its closed evidence schema")


def _safe_evidence_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise AdjudicationError("evidence path is invalid")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise AdjudicationError("evidence path must be a safe relative path")
    current = root
    try:
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise AdjudicationError("evidence root must be a directory")
        for part in candidate.parts[:-1]:
            current /= part
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise AdjudicationError("evidence parent must be a directory")
    except AdjudicationError:
        raise
    except OSError:
        raise AdjudicationError("evidence path could not be inspected") from None
    return root / candidate


def _validate_plan(plan: Any) -> None:
    _closed(plan, _PLAN_KEYS, "planned case")
    round_index = plan["round_index"]
    case_index = plan["case_index"]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in (round_index, case_index)
    ):
        raise AdjudicationError("planned case indexes are invalid")
    expected_ref = f"r{round_index:03d}-c{case_index:03d}"
    if plan["case_ref"] != expected_ref:
        raise AdjudicationError("planned case reference is invalid")
    if plan["evidence_relpath"] != f"cases/{expected_ref}.json":
        raise AdjudicationError("planned case evidence path is invalid")


def _validate_case_shape(case: Any, plan: Mapping[str, Any]) -> None:
    _closed(case, _CASE_KEYS, "case evidence")
    if case.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise AdjudicationError("case evidence schema is unsupported")
    if (
        case.get("round_index") != plan.get("round_index")
        or case.get("case_index") != plan.get("case_index")
    ):
        raise AdjudicationError("case evidence does not match the manifest")
    case_ref = plan["case_ref"]
    if case.get("events_relpath") != f"events/{case_ref}.jsonl" or case.get(
        "workspace_relpath"
    ) != f"workspaces/{case_ref}":
        raise AdjudicationError("case evidence paths do not match the manifest")
    if not isinstance(case.get("case_id"), str) or not isinstance(
        case.get("prompt"), str
    ):
        raise AdjudicationError("case evidence text fields are invalid")

    request = case.get("request")
    if type(request) is not dict or any(
        not isinstance(request.get(key), str)
        for key in (
            "owner",
            "namespace",
            "agent_id",
            "requested_model",
            "requested_effort",
        )
    ):
        raise AdjudicationError("case request evidence is invalid")
    checker_spec = case.get("task_checker")
    _closed(checker_spec, _CHECKER_SPEC_KEYS, "task checker evidence")
    if (
        type(checker_spec["argv"]) is not list
        or not checker_spec["argv"]
        or any(not isinstance(item, str) or not item for item in checker_spec["argv"])
    ):
        raise AdjudicationError("task checker argv evidence is invalid")
    timeout = checker_spec["timeout_seconds"]
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise AdjudicationError("task checker timeout evidence is invalid")

    constraints = case.get("event_constraints")
    _closed(constraints, _EVENT_CONSTRAINT_KEYS, "event constraint evidence")
    for value in constraints.values():
        if type(value) is not list or any(not isinstance(item, str) for item in value):
            raise AdjudicationError("event constraint evidence is invalid")

    controller = case.get("controller")
    _closed(controller, _CONTROLLER_KEYS, "case controller evidence")
    if controller.get("submission_attempts") != 1:
        raise AdjudicationError("case evidence does not prove one submission attempt")
    if type(controller.get("observation_errors")) is not list or type(
        controller.get("task_checker")
    ) is not dict:
        raise AdjudicationError("case controller evidence is invalid")


def _event_summary(path: Path) -> dict[str, set[str]]:
    types: set[str] = set()
    permission_decisions: set[str] = set()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise AdjudicationError("event evidence must be a regular file")
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = _strict_json_loads(line)
                if type(event) is not dict:
                    raise AdjudicationError("event evidence contains a non-object")
                event_type = event.get("type")
                if isinstance(event_type, str):
                    types.add(event_type)
                if event_type == "permission_mediation":
                    decision = event.get("decision")
                    if isinstance(decision, str):
                        permission_decisions.add(decision)
    except AdjudicationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise AdjudicationError("event evidence could not be read") from None
    return {"types": types, "permission_decisions": permission_decisions}


def _axis(verdict: str, **facts: bool | int | str | None) -> dict[str, Any]:
    return {"verdict": verdict, **facts}


def _transport_axis(controller: Mapping[str, Any]) -> dict[str, Any]:
    if controller.get("submit_error") is not None:
        return _axis(FAIL, terminal_class=None)
    terminal_observation = controller.get("terminal_observation")
    if type(terminal_observation) is not dict:
        return _axis(INDETERMINATE, terminal_class=None)
    result = terminal_observation.get("result")
    if type(result) is not dict:
        return _axis(INDETERMINATE, terminal_class=None)
    status = result.get("status")
    if not isinstance(status, str) or status not in _TERMINAL_CLASSES:
        return _axis(INDETERMINATE, terminal_class=None)
    if status == "completed":
        verdict = PASS
    elif status == "unknown":
        verdict = INDETERMINATE
    else:
        verdict = FAIL
    return _axis(verdict, terminal_class=status)


def _configuration_axis(case: Mapping[str, Any]) -> dict[str, Any]:
    request = case["request"]
    controller = case["controller"]
    session = controller.get("session_observation")
    terminal_observation = controller.get("terminal_observation")
    result = (
        terminal_observation.get("result")
        if type(terminal_observation) is dict
        else None
    )
    detail = result.get("detail_code") if type(result) is dict else None
    if type(session) is dict:
        exact = (
            session.get("owner") == request.get("owner")
            and session.get("namespace") == request.get("namespace")
            and session.get("agent_id") == request.get("agent_id")
            and session.get("last_effective_model") == request.get("requested_model")
            and session.get("last_effective_effort")
            == request.get("requested_effort")
        )
        return _axis(PASS if exact else FAIL, exact_readback=exact)
    if isinstance(detail, str) and detail.startswith("CONFIG_"):
        return _axis(FAIL, exact_readback=False)
    return _axis(INDETERMINATE, exact_readback=False)


def _checker_axis(controller: Mapping[str, Any]) -> dict[str, Any]:
    checker = controller.get("task_checker")
    if type(checker) is not dict or checker.get("executed") is not True:
        return _axis(INDETERMINATE, exited=False, timed_out=False)
    timed_out = checker.get("timed_out") is True
    returncode = checker.get("returncode")
    if timed_out:
        verdict = FAIL
    elif isinstance(returncode, int) and not isinstance(returncode, bool):
        verdict = PASS if returncode == 0 else FAIL
    else:
        verdict = INDETERMINATE
    return _axis(
        verdict,
        exited=isinstance(returncode, int) and not isinstance(returncode, bool),
        timed_out=timed_out,
        exit_code=(
            returncode
            if isinstance(returncode, int) and not isinstance(returncode, bool)
            else None
        ),
    )


def _execution_axis(
    case: Mapping[str, Any], events: dict[str, set[str]] | None
) -> dict[str, Any]:
    if case["controller"].get("events_exhausted") is not True or events is None:
        return _axis(
            INDETERMINATE,
            required_observed=False,
            forbidden_absent=False,
        )
    constraints = case["event_constraints"]
    event_types = events["types"]
    decisions = events["permission_decisions"]
    required_observed = set(constraints["required_event_types"]) <= event_types and set(
        constraints["required_permission_decisions"]
    ) <= decisions
    forbidden_absent = not (
        set(constraints["forbidden_event_types"]) & event_types
        or set(constraints["forbidden_permission_decisions"]) & decisions
    )
    return _axis(
        PASS if required_observed and forbidden_absent else FAIL,
        required_observed=required_observed,
        forbidden_absent=forbidden_absent,
    )


def _settled_axis(case: Mapping[str, Any]) -> dict[str, Any]:
    controller = case["controller"]
    terminal = controller.get("terminal_observation")
    terminal_present = type(terminal) is dict and type(terminal.get("result")) is dict
    events_exhausted = controller.get("events_exhausted") is True
    session = controller.get("session_observation")
    session_observed = type(session) is dict
    not_quarantined = session_observed and session.get("quarantine") is None
    checker = controller.get("task_checker")
    checker_observed = type(checker) is dict and checker.get("executed") is True
    settled = all(
        (
            terminal_present,
            events_exhausted,
            session_observed,
            not_quarantined,
            checker_observed,
        )
    )
    return _axis(
        PASS if settled else INDETERMINATE,
        terminal_present=terminal_present,
        events_exhausted=events_exhausted,
        session_observed=session_observed,
        not_quarantined=not_quarantined,
        checker_observed=checker_observed,
    )


def _business_verdict(axes: list[Mapping[str, Any]]) -> str:
    verdicts = [axis["verdict"] for axis in axes]
    if all(verdict == PASS for verdict in verdicts):
        return PASS
    if any(verdict == FAIL for verdict in verdicts):
        return FAIL
    return INDETERMINATE


def _adjudicate_case(root: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    _validate_plan(plan)
    case_path = _safe_evidence_path(root, plan["evidence_relpath"])
    case = _read_regular_json(case_path)
    _validate_case_shape(case, plan)

    events: dict[str, set[str]] | None = None
    if case["controller"].get("events_exhausted") is True:
        events_path = _safe_evidence_path(root, case["events_relpath"])
        events = _event_summary(events_path)
    transport = _transport_axis(case["controller"])
    configuration = _configuration_axis(case)
    checker = _checker_axis(case["controller"])
    execution = _execution_axis(case, events)
    settled = _settled_axis(case)
    axes = [transport, configuration, checker, execution, settled]
    return {
        "case_ref": plan["case_ref"],
        "transport_ars_terminal": transport,
        "configuration_fidelity": configuration,
        "task_checker": checker,
        "execution_constraints": execution,
        "settled_state": settled,
        "business_verdict": _business_verdict(axes),
    }


def build_receipt(evidence_dir: Path | str) -> dict[str, Any]:
    """Recompute sanitized verdicts from write-once controller artifacts."""
    root = Path(evidence_dir)
    manifest = _read_regular_json(root / "controller-manifest.json")
    completion = _read_regular_json(root / "completion.json")
    _closed(manifest, _MANIFEST_KEYS, "controller manifest")
    _closed(completion, _COMPLETION_KEYS, "completion evidence")
    if manifest.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise AdjudicationError("controller evidence schema is unsupported")
    if completion.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise AdjudicationError("completion evidence schema is unsupported")
    preflight = manifest.get("preflight")
    _closed(preflight, _PREFLIGHT_KEYS, "preflight evidence")
    checks = preflight.get("checks")
    _closed(checks, _PREFLIGHT_CHECK_KEYS, "preflight checks")
    if any(value != PASS for value in checks.values()):
        raise AdjudicationError("controller preflight evidence did not pass")
    planned = manifest.get("planned_cases")
    if type(planned) is not list:
        raise AdjudicationError("controller manifest has no planned case list")
    for plan in planned:
        _validate_plan(plan)
    completed_refs = completion.get("completed_case_refs")
    planned_refs = [
        item.get("case_ref") for item in planned if type(item) is dict
    ]
    if (
        type(completed_refs) is not list
        or len(planned_refs) != len(planned)
        or len(set(planned_refs)) != len(planned_refs)
        or completed_refs != planned_refs
        or completion.get("case_count") != len(planned_refs)
    ):
        raise AdjudicationError("controller evidence is incomplete")
    cases = [_adjudicate_case(root, plan) for plan in planned]
    overall = _business_verdict(
        [{"verdict": case["business_verdict"]} for case in cases]
    )
    summary = {
        PASS: sum(case["business_verdict"] == PASS for case in cases),
        FAIL: sum(case["business_verdict"] == FAIL for case in cases),
        INDETERMINATE: sum(
            case["business_verdict"] == INDETERMINATE for case in cases
        ),
    }
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": "ars-batch-agent-acceptance-receipt",
        "preflight": {"verdict": PASS},
        "privacy": {
            "identifiers": "[REDACTED]",
            "paths": "[REDACTED]",
            "free_form_output": "[REDACTED]",
        },
        "cases": cases,
        "summary": summary,
        "business_verdict": overall,
        "ars_completion_is_business_success": False,
        "hostile_isolation_proven": False,
    }


def write_receipt(path: Path | str, receipt: Mapping[str, Any]) -> None:
    """Write one receipt exclusively; never replace an existing artifact."""
    target = Path(path)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(target, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    except FileExistsError:
        raise AdjudicationError("refusing to overwrite an existing receipt") from None
    except OSError:
        raise AdjudicationError("receipt could not be written") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Adjudicate captured ARS batch evidence without rerunning work."
    )
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = build_receipt(args.evidence)
        write_receipt(args.receipt, receipt)
    except AdjudicationError as error:
        print(f"adjudication refused: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(receipt["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

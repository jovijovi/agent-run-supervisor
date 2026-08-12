#!/usr/bin/env python3
"""Run a validated external-AGENT acceptance matrix through the arsd Socket API."""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping

from agent_run_supervisor.arsd import protocol
from agent_run_supervisor.arsd.client import ArsdClient
from agent_run_supervisor.native_acp.agent_registry import load_agents_file
from agent_run_supervisor.native_acp.spec import (
    STRUCTURAL_EVENT_BUDGET_POLICY,
    RunLimits,
)


MATRIX_SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 1
MAX_MATRIX_BYTES = 1 << 20

_TOP_KEYS = frozenset(
    {"schema_version", "server_constraints", "controller", "rounds"}
)
_SERVER_KEYS = frozenset({"api_version", "allowed_daemon_versions"})
_CONTROLLER_KEYS = frozenset(
    {
        "max_concurrency",
        "max_rounds",
        "max_cases",
        "poll_interval_seconds",
        "terminal_timeout_seconds",
        "events_page_limit",
        "checker_output_limit_bytes",
    }
)
_ROUND_KEYS = frozenset({"round_id", "cases"})
_CASE_KEYS = frozenset(
    {"case_id", "request", "prompt", "task_checker", "event_constraints"}
)
_CHECKER_KEYS = frozenset({"argv", "timeout_seconds"})
_EVENT_CONSTRAINT_KEYS = frozenset(
    {
        "required_event_types",
        "forbidden_event_types",
        "required_permission_decisions",
        "forbidden_permission_decisions",
    }
)
_LIMIT_KEYS = frozenset(field.name for field in dataclasses.fields(RunLimits))
_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_REQUIRED_OPERATIONS = frozenset(
    {"server_info", "submit", "run_status", "run_events", "session_status"}
)


class BatchAcceptanceError(Exception):
    """Base class for stable local controller failures."""


class MatrixValidationError(BatchAcceptanceError):
    """The trusted local matrix is not in the closed schema."""


class PreflightError(BatchAcceptanceError):
    """The live daemon or operator registry cannot admit the matrix safely."""


class EvidenceError(BatchAcceptanceError):
    """Fresh, exclusive local evidence storage could not be established."""


def _fail(message: str) -> None:
    raise MatrixValidationError(message)


def _closed(mapping: Any, keys: frozenset[str], where: str) -> dict[str, Any]:
    if type(mapping) is not dict:
        _fail(f"{where} must be a JSON object")
    if set(mapping) != set(keys):
        _fail(f"{where} must contain exactly its closed field set")
    return mapping


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(f"{where} must be a positive integer")
    return value


def _positive_number(value: Any, where: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        _fail(f"{where} must be a positive finite number")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or not value.isprintable():
        _fail(f"{where} must be non-empty printable text")
    return value


def _string_list(value: Any, where: str, *, nonempty: bool = False) -> list[str]:
    if type(value) is not list or (nonempty and not value):
        _fail(f"{where} must be a JSON array of strings")
    if any(not isinstance(item, str) or not item for item in value):
        _fail(f"{where} must be a JSON array of non-empty strings")
    if len(value) != len(set(value)):
        _fail(f"{where} must not contain duplicates")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MatrixValidationError("matrix JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    del value
    raise MatrixValidationError("matrix JSON contains a non-JSON numeric constant")


def load_matrix(path: Path | str) -> dict[str, Any]:
    """Read and fully validate the closed matrix before any Socket API call."""
    matrix_path = Path(path)
    try:
        raw = matrix_path.read_bytes()
    except OSError:
        raise MatrixValidationError("matrix file could not be read") from None
    if len(raw) > MAX_MATRIX_BYTES:
        raise MatrixValidationError("matrix file exceeds its byte limit")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except MatrixValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise MatrixValidationError("matrix file is not strict JSON") from None
    return validate_matrix(payload)


def validate_matrix(payload: Any) -> dict[str, Any]:
    matrix = _closed(payload, _TOP_KEYS, "matrix")
    if matrix["schema_version"] != MATRIX_SCHEMA_VERSION or isinstance(
        matrix["schema_version"], bool
    ):
        _fail("matrix schema_version is unsupported")

    server = _closed(
        matrix["server_constraints"], _SERVER_KEYS, "server_constraints"
    )
    _positive_int(server["api_version"], "server_constraints.api_version")
    _string_list(
        server["allowed_daemon_versions"],
        "server_constraints.allowed_daemon_versions",
        nonempty=True,
    )

    controller = _closed(matrix["controller"], _CONTROLLER_KEYS, "controller")
    for key in (
        "max_concurrency",
        "max_rounds",
        "max_cases",
        "events_page_limit",
        "checker_output_limit_bytes",
    ):
        _positive_int(controller[key], f"controller.{key}")
    for key in ("poll_interval_seconds", "terminal_timeout_seconds"):
        _positive_number(controller[key], f"controller.{key}")

    rounds = matrix["rounds"]
    if type(rounds) is not list or not rounds:
        _fail("rounds must be a non-empty JSON array")
    if len(rounds) > controller["max_rounds"]:
        _fail("matrix exceeds controller.max_rounds")

    round_ids: set[str] = set()
    case_ids: set[str] = set()
    case_count = 0
    for round_index, item in enumerate(rounds):
        round_row = _closed(item, _ROUND_KEYS, f"rounds[{round_index}]")
        round_id = _text(round_row["round_id"], f"rounds[{round_index}].round_id")
        if _SAFE_ID.fullmatch(round_id) is None or round_id in round_ids:
            _fail("round ids must be unique safe identifiers")
        round_ids.add(round_id)
        cases = round_row["cases"]
        if type(cases) is not list or not cases:
            _fail("each round must contain a non-empty cases array")
        for case_index, item_case in enumerate(cases):
            _validate_case(item_case, round_index, case_index, case_ids)
            case_count += 1
    if case_count > controller["max_cases"]:
        _fail("matrix exceeds controller.max_cases")
    return matrix


def _validate_case(
    item: Any, round_index: int, case_index: int, case_ids: set[str]
) -> None:
    where = f"rounds[{round_index}].cases[{case_index}]"
    case = _closed(item, _CASE_KEYS, where)
    case_id = _text(case["case_id"], f"{where}.case_id")
    if _SAFE_ID.fullmatch(case_id) is None or case_id in case_ids:
        _fail("case ids must be unique safe identifiers")
    case_ids.add(case_id)
    if not isinstance(case["prompt"], str):
        _fail(f"{where}.prompt must be a string")

    request = case["request"]
    if type(request) is not dict:
        _fail(f"{where}.request must be a JSON object")
    if "session_id" in request:
        _fail("batch cases must omit session_id so every case creates a new Session")
    limits = request.get("limits")
    if type(limits) is not dict or set(limits) != set(_LIMIT_KEYS):
        _fail("each case must explicitly provide every Run limit")

    checker = _closed(case["task_checker"], _CHECKER_KEYS, f"{where}.task_checker")
    _string_list(checker["argv"], f"{where}.task_checker.argv", nonempty=True)
    _positive_number(
        checker["timeout_seconds"], f"{where}.task_checker.timeout_seconds"
    )

    constraints = _closed(
        case["event_constraints"],
        _EVENT_CONSTRAINT_KEYS,
        f"{where}.event_constraints",
    )
    for key in sorted(_EVENT_CONSTRAINT_KEYS):
        _string_list(constraints[key], f"{where}.event_constraints.{key}")

    submit_payload = {
        "request": request,
        "prompt_text": case["prompt"],
        "workspace_root": "<case-workspace>",
        "cwd": None,
        "retry_of_run_id": None,
    }
    try:
        protocol.parse_submit(
            submit_payload, budget_policy=STRUCTURAL_EVENT_BUDGET_POLICY
        )
    except (protocol.ProtocolError, TypeError, ValueError):
        _fail(f"{where} is not a valid Socket API submit shape")


def _case_rows(matrix: Mapping[str, Any]):
    for round_index, round_row in enumerate(matrix["rounds"], start=1):
        for case_index, case in enumerate(round_row["cases"], start=1):
            yield round_index, case_index, case


def _case_ref(round_index: int, case_index: int) -> str:
    return f"r{round_index:03d}-c{case_index:03d}"


def _stable_error(error: BaseException) -> dict[str, Any]:
    code = getattr(error, "code", None)
    payload: dict[str, Any] = {"kind": type(error).__name__}
    if isinstance(code, str):
        payload["code"] = code
    return payload


def _preflight(
    matrix: Mapping[str, Any],
    *,
    socket_path: Path | str,
    agents_file: Path | str,
    client_factory: Callable[..., Any],
    registry_loader: Callable[[Path | str], Any],
) -> dict[str, Any]:
    requested_api = matrix["server_constraints"]["api_version"]
    try:
        snapshot = registry_loader(agents_file)
    except Exception:
        raise PreflightError("operator registry preflight failed") from None
    registered = frozenset(snapshot.ids())
    requested_agents = {
        case["request"]["agent_id"] for _, _, case in _case_rows(matrix)
    }
    if not requested_agents <= registered:
        raise PreflightError("matrix names an agent absent from the supplied registry")

    try:
        with client_factory(socket_path, api_version=requested_api) as client:
            info = client.server_info()
    except Exception:
        raise PreflightError("server_info preflight failed") from None
    if type(info) is not dict:
        raise PreflightError("server_info returned an invalid shape")
    supported = info.get("supported_api_versions")
    if (
        type(supported) is not list
        or requested_api not in supported
        or info.get("api_version") != requested_api
    ):
        raise PreflightError("requested API version is not served")
    if info.get("version") not in matrix["server_constraints"][
        "allowed_daemon_versions"
    ]:
        raise PreflightError("daemon version is outside the allowed set")
    operations = info.get("operations")
    if type(operations) is not list or not _REQUIRED_OPERATIONS <= set(operations):
        raise PreflightError("daemon lacks a required Socket API operation")
    limits = info.get("limits")
    if type(limits) is not dict:
        raise PreflightError("server_info limits are unavailable")
    live_capacity = limits.get("max_concurrent_runs")
    live_budget = limits.get("max_run_event_budget_bytes")
    live_page_limit = limits.get("events_page_limit")
    for value in (live_capacity, live_budget, live_page_limit):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PreflightError("server_info carries an invalid live limit")
    controller = matrix["controller"]
    if controller["max_concurrency"] > live_capacity:
        raise PreflightError("controller concurrency exceeds live daemon capacity")
    if controller["events_page_limit"] > live_page_limit:
        raise PreflightError("controller event page size exceeds the live limit")
    for _, _, case in _case_rows(matrix):
        case_limits = case["request"]["limits"]
        event_budget = case_limits["max_event_bytes"] * case_limits["max_events"]
        if event_budget > live_budget:
            raise PreflightError("a case event budget exceeds the live daemon ceiling")
    return {
        "server_info": info,
        "checks": {
            "api_constraint": "PASS",
            "daemon_version_constraint": "PASS",
            "registry_membership": "PASS",
            "capacity": "PASS",
            "event_budget": "PASS",
            "matrix_limits": "PASS",
        },
    }


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    except FileExistsError:
        raise EvidenceError("refusing to overwrite existing evidence") from None
    except OSError:
        raise EvidenceError("evidence could not be written") from None


def _bounded_text(raw: bytes | str | None, limit: int) -> tuple[str, bool]:
    if raw is None:
        data = b""
    elif isinstance(raw, bytes):
        data = raw
    else:
        data = raw.encode("utf-8", errors="replace")
    truncated = len(data) > limit
    return data[:limit].decode("utf-8", errors="replace"), truncated


def _run_checker(case: Mapping[str, Any], workspace: Path, output_limit: int) -> dict:
    spec = case["task_checker"]
    argv = list(spec["argv"])
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            shell=False,
            capture_output=True,
            timeout=spec["timeout_seconds"],
            check=False,
        )
        stdout, stdout_truncated = _bounded_text(completed.stdout, output_limit)
        stderr, stderr_truncated = _bounded_text(completed.stderr, output_limit)
        return {
            "executed": True,
            "returncode": completed.returncode,
            "timed_out": False,
            "stdout": stdout,
            "stdout_truncated": stdout_truncated,
            "stderr": stderr,
            "stderr_truncated": stderr_truncated,
        }
    except subprocess.TimeoutExpired as error:
        stdout, stdout_truncated = _bounded_text(error.stdout, output_limit)
        stderr, stderr_truncated = _bounded_text(error.stderr, output_limit)
        return {
            "executed": True,
            "returncode": None,
            "timed_out": True,
            "stdout": stdout,
            "stdout_truncated": stdout_truncated,
            "stderr": stderr,
            "stderr_truncated": stderr_truncated,
        }
    except OSError as error:
        return {
            "executed": False,
            "returncode": None,
            "timed_out": False,
            "error": _stable_error(error),
        }


def _capture_events(
    client: Any,
    run_id: str,
    *,
    page_limit: int,
    max_events: int,
    events_path: Path,
) -> tuple[bool, dict[str, Any] | None]:
    from_seq = 0
    event_count = 0
    try:
        with events_path.open("x", encoding="utf-8") as stream:
            while True:
                page = client.run_events(
                    run_id, from_seq=from_seq, limit=page_limit, follow=False
                )
                events = page.get("events")
                if type(events) is not list:
                    raise EvidenceError("run_events returned an invalid page")
                if len(events) > page_limit or event_count + len(events) > max_events:
                    raise EvidenceError("run_events exceeded the declared event limit")
                if not events and page.get("exhausted") is not True:
                    raise EvidenceError("run_events returned an empty open page")
                for event in events:
                    if type(event) is not dict:
                        raise EvidenceError("run_events returned an invalid event")
                    stream.write(
                        json.dumps(
                            event,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                event_count += len(events)
                if page.get("exhausted") is True:
                    return True, None
                next_from_seq = page.get("next_from_seq")
                if (
                    isinstance(next_from_seq, bool)
                    or not isinstance(next_from_seq, int)
                    or next_from_seq <= from_seq
                ):
                    raise EvidenceError("run_events pagination did not advance")
                from_seq = next_from_seq
    except Exception as error:
        return False, _stable_error(error)


def _execute_case(
    case: Mapping[str, Any],
    *,
    round_index: int,
    case_index: int,
    output_dir: Path,
    socket_path: Path | str,
    api_version: int,
    controller_config: Mapping[str, Any],
    client_factory: Callable[..., Any],
) -> str:
    case_ref = _case_ref(round_index, case_index)
    workspace_relpath = f"workspaces/{case_ref}"
    events_relpath = f"events/{case_ref}.jsonl"
    workspace = output_dir / workspace_relpath
    workspace.mkdir(mode=0o700)
    controller: dict[str, Any] = {
        "submission_attempts": 1,
        "request_id": "batch-" + secrets.token_hex(16),
        "submit_ack": None,
        "submit_error": None,
        "terminal_observation": None,
        "observation_errors": [],
        "events_exhausted": False,
        "session_observation": None,
        "task_checker": {"executed": False},
    }
    request = dict(case["request"])
    submit_payload = {
        "request": request,
        "prompt_text": case["prompt"],
        "workspace_root": str(workspace),
        "cwd": None,
        "retry_of_run_id": None,
    }
    terminal: dict[str, Any] | None = None
    try:
        with client_factory(socket_path, api_version=api_version) as client:
            try:
                ack = client.submit(
                    request_id=controller["request_id"], payload=submit_payload
                )
                controller["submit_ack"] = ack
            except Exception as error:
                controller["submit_error"] = _stable_error(error)
                ack = None
            if type(ack) is dict:
                run_id = ack.get("run_id")
                session_id = ack.get("session_id")
                if isinstance(run_id, str) and isinstance(session_id, str):
                    deadline = (
                        time.monotonic()
                        + controller_config["terminal_timeout_seconds"]
                    )
                    while True:
                        try:
                            status = client.run_status(run_id)
                        except Exception as error:
                            controller["observation_errors"].append(
                                {"phase": "run_status", **_stable_error(error)}
                            )
                            break
                        if type(status) is dict and type(status.get("result")) is dict:
                            terminal = status
                            controller["terminal_observation"] = status
                            break
                        if time.monotonic() >= deadline:
                            controller["observation_errors"].append(
                                {"phase": "run_status", "kind": "ControllerTimeout"}
                            )
                            break
                        time.sleep(controller_config["poll_interval_seconds"])
                    if terminal is not None:
                        exhausted, error = _capture_events(
                            client,
                            run_id,
                            page_limit=controller_config["events_page_limit"],
                            max_events=request["limits"]["max_events"],
                            events_path=output_dir / events_relpath,
                        )
                        controller["events_exhausted"] = exhausted
                        if error is not None:
                            controller["observation_errors"].append(
                                {"phase": "run_events", **error}
                            )
                        try:
                            controller["session_observation"] = client.session_status(
                                session_id
                            )
                        except Exception as error:
                            controller["observation_errors"].append(
                                {"phase": "session_status", **_stable_error(error)}
                            )
    except Exception as error:
        controller["observation_errors"].append(
            {"phase": "client", **_stable_error(error)}
        )
    if terminal is not None:
        controller["task_checker"] = _run_checker(
            case, workspace, controller_config["checker_output_limit_bytes"]
        )
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "case_id": case["case_id"],
        "round_index": round_index,
        "case_index": case_index,
        "request": request,
        "prompt": case["prompt"],
        "task_checker": case["task_checker"],
        "event_constraints": case["event_constraints"],
        "workspace_relpath": workspace_relpath,
        "events_relpath": events_relpath,
        "controller": controller,
    }
    _write_json_exclusive(output_dir / "cases" / f"{case_ref}.json", evidence)
    return case_ref


def run_batch(
    *,
    matrix_path: Path | str,
    socket_path: Path | str,
    agents_file: Path | str,
    output_dir: Path | str,
    client_factory: Callable[..., Any] = ArsdClient,
    registry_loader: Callable[[Path | str], Any] = load_agents_file,
) -> dict[str, Any]:
    """Run all rounds once and write only new controller evidence artifacts."""
    matrix = load_matrix(matrix_path)
    output = Path(output_dir)
    if os.path.lexists(output):
        raise EvidenceError("output directory must be fresh")
    preflight = _preflight(
        matrix,
        socket_path=socket_path,
        agents_file=agents_file,
        client_factory=client_factory,
        registry_loader=registry_loader,
    )
    try:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        (output / "cases").mkdir(mode=0o700)
        (output / "events").mkdir(mode=0o700)
        (output / "workspaces").mkdir(mode=0o700)
    except FileExistsError:
        raise EvidenceError("output directory must be fresh") from None
    except OSError:
        raise EvidenceError("output directory could not be created") from None

    planned_cases = [
        {
            "case_ref": _case_ref(round_index, case_index),
            "round_index": round_index,
            "case_index": case_index,
            "evidence_relpath": (
                f"cases/{_case_ref(round_index, case_index)}.json"
            ),
        }
        for round_index, case_index, _ in _case_rows(matrix)
    ]
    _write_json_exclusive(
        output / "controller-manifest.json",
        {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "matrix_schema_version": matrix["schema_version"],
            "preflight": preflight,
            "planned_cases": planned_cases,
        },
    )

    completed: list[str] = []
    controller = matrix["controller"]
    api_version = matrix["server_constraints"]["api_version"]
    for round_index, round_row in enumerate(matrix["rounds"], start=1):
        with ThreadPoolExecutor(
            max_workers=controller["max_concurrency"],
            thread_name_prefix="ars-batch-case",
        ) as executor:
            futures = [
                executor.submit(
                    _execute_case,
                    case,
                    round_index=round_index,
                    case_index=case_index,
                    output_dir=output,
                    socket_path=socket_path,
                    api_version=api_version,
                    controller_config=controller,
                    client_factory=client_factory,
                )
                for case_index, case in enumerate(round_row["cases"], start=1)
            ]
            completed.extend(future.result() for future in futures)
    completion = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "case_count": len(completed),
        "completed_case_refs": completed,
    }
    _write_json_exclusive(output / "completion.json", completion)
    return completion


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one validated ARS external-AGENT acceptance matrix."
    )
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--agents-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_batch(
            matrix_path=args.matrix,
            socket_path=args.socket,
            agents_file=args.agents_file,
            output_dir=args.output,
        )
    except BatchAcceptanceError as error:
        print(f"batch acceptance refused: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

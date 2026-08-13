#!/usr/bin/env python3
"""Run the fixed response-only R1/R2/R3 delivery quick health check."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from agent_run_supervisor.arsd.client import ArsdClient
from agent_run_supervisor.process_liveness import classify_holder

from _common import (
    AgentRoute,
    CaseFailure,
    ControllerConfig,
    ControllerError,
    add_common_arguments,
    await_terminal,
    build_payload,
    child_path,
    config_from_namespace,
    create_output_root,
    preflight,
    prove_run,
    read_all_events,
    request_id,
    summarize_overall,
    write_json_exclusive,
    write_summary,
)


ROUNDS = ("R1", "R2", "R3")
_SIGNATURES = {
    "R1": "bubble_sort(values)",
    "R2": "bubble_sort(values, reverse=False)",
    "R3": "bubble_sort(values, key=None, reverse=False)",
}


def prompt_for(round_id: str) -> str:
    """Return the fixed payload for one response-only Run."""

    try:
        signature = _SIGNATURES[round_id]
    except KeyError:
        raise CaseFailure("ROUND") from None
    return (
        f"Write Python source that defines {signature} using bubble sort. "
        "Return the source text only, without Markdown. Do not use tools, read "
        "files, execute commands, or modify the workspace."
    )


def _case_row(
    route: AgentRoute, round_id: str, verdict: str, failure: str | None
) -> dict[str, Any]:
    return {
        "agent_id": route.agent_id,
        "model": route.model,
        "effort": route.effort,
        "round": round_id,
        "verdict": verdict,
        "first_failure": failure,
    }


def _finish_case(
    root: Path,
    route: AgentRoute,
    round_id: str,
    raw: dict[str, Any],
    verdict: str,
    failure: str | None,
) -> dict[str, Any]:
    raw["verdict"] = verdict
    raw["first_failure"] = failure
    write_json_exclusive(root / "raw" / f"{round_id}-{route.agent_id}.json", raw)
    return _case_row(route, round_id, verdict, failure)


def _run_case(
    config: ControllerConfig,
    route: AgentRoute,
    round_id: str,
    *,
    root: Path,
    page_limit: int,
    client_factory: Callable[..., Any],
    sleeper: Callable[[float], None],
    liveness_checker: Callable[[Mapping[str, Any]], str],
) -> dict[str, Any]:
    workspace = child_path(root, "workspaces", round_id, route.agent_id)
    workspace.parent.mkdir(mode=0o700, exist_ok=True)
    workspace.mkdir(mode=0o700, exist_ok=False)
    prompt = prompt_for(round_id)
    local_request_id = request_id(f"response-{round_id.lower()}-{route.agent_id}")
    payload = build_payload(
        config,
        route,
        case_ref=f"prompt:ars-quick-health:response-only:{round_id}",
        prompt=prompt,
        workspace=workspace,
    )
    raw: dict[str, Any] = {
        "case": round_id,
        "agent_id": route.agent_id,
        "submission_attempts": 1,
    }

    try:
        with client_factory(config.socket_path) as client:
            try:
                ack = client.submit(request_id=local_request_id, payload=payload)
            except Exception:
                return _finish_case(
                    root, route, round_id, raw, "INDETERMINATE", "SUBMIT"
                )
            try:
                result = await_terminal(
                    client,
                    str(ack.get("run_id", "")),
                    config.controller_policy,
                    sleeper=sleeper,
                )
                events = read_all_events(
                    client,
                    str(ack.get("run_id", "")),
                    page_limit=page_limit,
                    max_events=config.request_limits.max_events,
                )
            except CaseFailure as error:
                return _finish_case(
                    root, route, round_id, raw, "INDETERMINATE", error.category
                )
    except Exception:
        return _finish_case(
            root, route, round_id, raw, "INDETERMINATE", "OBSERVATION"
        )

    try:
        proof = prove_run(
            config,
            route,
            ack=ack,
            request_id=local_request_id,
            result=result,
            events=events,
            session_operation="create",
            liveness_checker=liveness_checker,
        )
    except CaseFailure as error:
        return _finish_case(
            root, route, round_id, raw, "INDETERMINATE", error.category
        )
    raw.update(proof.raw_receipt)
    if proof.verdict != "PASS":
        return _finish_case(
            root, route, round_id, raw, proof.verdict, proof.first_failure
        )

    try:
        workspace_unchanged = not any(workspace.iterdir())
    except OSError:
        return _finish_case(
            root, route, round_id, raw, "INDETERMINATE", "WORKSPACE_UNPROVEN"
        )
    final_message = proof.result.get("final_message")
    deliverable_present = isinstance(final_message, str) and len(final_message) > 0
    checks = raw["checks"]
    checks["workspace_unchanged"] = workspace_unchanged
    checks["deliverable_present"] = deliverable_present
    if not workspace_unchanged:
        return _finish_case(
            root, route, round_id, raw, "FAIL", "WORKSPACE_MUTATED"
        )
    if not deliverable_present:
        return _finish_case(
            root, route, round_id, raw, "FAIL", "DELIVERABLE_MISSING"
        )
    return _finish_case(root, route, round_id, raw, "PASS", None)


def run_response_only(
    config: ControllerConfig,
    *,
    client_factory: Callable[..., Any] = ArsdClient,
    sleeper: Callable[[float], None] = time.sleep,
    liveness_checker: Callable[[Mapping[str, Any]], str] = classify_holder,
) -> dict[str, Any]:
    live, diagnostics = preflight(config, client_factory)
    if any(
        len(prompt_for(round_id).encode("utf-8")) > live.max_prompt_bytes
        for round_id in ROUNDS
    ):
        raise ControllerError("PROMPT_LIMIT")
    root = create_output_root(config, diagnostics)
    by_agent: dict[str, list[dict[str, Any]]] = {
        route.agent_id: [] for route in config.routes
    }
    workers = live.workers_for(config)
    for round_id in ROUNDS:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _run_case,
                    config,
                    route,
                    round_id,
                    root=root,
                    page_limit=live.event_page_limit,
                    client_factory=client_factory,
                    sleeper=sleeper,
                    liveness_checker=liveness_checker,
                ): route
                for route in config.routes
            }
            rows = [future.result() for future in futures]
            round_rows = {row["agent_id"]: row for row in rows}
        for route in config.routes:
            by_agent[route.agent_id].append(round_rows[route.agent_id])

    results: list[dict[str, Any]] = []
    for route in config.routes:
        rounds = by_agent[route.agent_id]
        results.append(
            {
                "agent_id": route.agent_id,
                "model": route.model,
                "effort": route.effort,
                "rounds": [
                    {
                        "round": row["round"],
                        "verdict": row["verdict"],
                        "first_failure": row["first_failure"],
                    }
                    for row in rounds
                ],
                "verdict": summarize_overall(rounds),
                "first_failure": next(
                    (row["first_failure"] for row in rounds if row["first_failure"]),
                    None,
                ),
            }
        )
    summary = {
        "schema_version": 1,
        "controller": "response-only-r1-r2-r3",
        "ars_package_version": live.package_version,
        "api_version": live.api_version,
        "results": results,
        "overall": summarize_overall(results),
    }
    write_summary(root, summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run fixed response-only delivery checks through arsd."
    )
    add_common_arguments(parser)
    args = parser.parse_args(argv)
    try:
        config = config_from_namespace(args)
    except ControllerError as error:
        parser.error(error.category)
    return argparse.Namespace(config=config)


def main(argv: list[str] | None = None) -> int:
    parsed = parse_args(argv)
    try:
        summary = run_response_only(parsed.config, client_factory=ArsdClient)
    except ControllerError as error:
        summary = {
            "schema_version": 1,
            "controller": "response-only-r1-r2-r3",
            "results": [],
            "overall": "INDETERMINATE",
            "error": error.category,
        }
    except Exception:
        summary = {
            "schema_version": 1,
            "controller": "response-only-r1-r2-r3",
            "results": [],
            "overall": "INDETERMINATE",
            "error": "CONTROLLER",
        }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["overall"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the fixed real Session create-then-load continuity quick health check."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import secrets
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


def _new_token() -> str:
    return "CONTINUITY-" + secrets.token_hex(16).upper()


def _prompts(token: str) -> tuple[str, str]:
    return (
        "Remember this exact continuity token for the next Run: "
        f"{token}\nReply exactly: STORED",
        "Return the exact continuity token from the preceding Run. Reply with "
        "the token only: no quotes, label, Markdown, whitespace, or explanation.",
    )


def _result_row(
    route: AgentRoute, verdict: str, failure: str | None
) -> dict[str, Any]:
    return {
        "agent_id": route.agent_id,
        "model": route.model,
        "effort": route.effort,
        "verdict": verdict,
        "first_failure": failure,
    }


def _prefix(leg: str, category: str) -> str:
    return f"{leg}_{category}"


def _run_agent(
    config: ControllerConfig,
    route: AgentRoute,
    *,
    root: Path,
    page_limit: int,
    client_factory: Callable[..., Any],
    sleeper: Callable[[float], None],
    liveness_checker: Callable[[Mapping[str, Any]], str],
    token_factory: Callable[[], str],
) -> dict[str, Any]:
    workspace = child_path(root, "workspaces", "session-reuse", route.agent_id)
    workspace.parent.mkdir(mode=0o700, exist_ok=True)
    workspace.mkdir(mode=0o700, exist_ok=False)
    token = token_factory()
    raw: dict[str, Any] = {
        "agent_id": route.agent_id,
        "case": "S1-create/S2-load",
        "S1_submission_attempts": 1,
        "S2_submission_attempts": 0,
    }

    def finish(verdict: str, failure: str | None) -> dict[str, Any]:
        raw["verdict"] = verdict
        raw["first_failure"] = failure
        write_json_exclusive(
            root / "raw" / f"session-reuse-{route.agent_id}.json", raw
        )
        return _result_row(route, verdict, failure)

    if (
        not isinstance(token, str)
        or re.fullmatch(r"CONTINUITY-[A-F0-9]{32}", token) is None
    ):
        return finish("INDETERMINATE", "TOKEN_GENERATION")
    prompt1, prompt2 = _prompts(token)

    try:
        with client_factory(config.socket_path) as client:
            request1 = request_id(f"session-s1-{route.agent_id}")
            payload1 = build_payload(
                config,
                route,
                case_ref="prompt:ars-quick-health:session-reuse:S1",
                prompt=prompt1,
                workspace=workspace,
            )
            try:
                ack1 = client.submit(request_id=request1, payload=payload1)
            except Exception:
                return finish("INDETERMINATE", "S1_SUBMIT")
            try:
                result1 = await_terminal(
                    client,
                    str(ack1.get("run_id", "")),
                    config.controller_policy,
                    sleeper=sleeper,
                )
                events1 = read_all_events(
                    client,
                    str(ack1.get("run_id", "")),
                    page_limit=page_limit,
                    max_events=config.request_limits.max_events,
                )
                proof1 = prove_run(
                    config,
                    route,
                    ack=ack1,
                    request_id=request1,
                    result=result1,
                    events=events1,
                    session_operation="create",
                    liveness_checker=liveness_checker,
                )
            except CaseFailure as error:
                return finish("INDETERMINATE", _prefix("S1", error.category))
            final_message1 = proof1.result.get("final_message")
            deliverable_present = (
                isinstance(final_message1, str) and len(final_message1) > 0
            )
            proof1.raw_receipt["checks"]["deliverable_present"] = deliverable_present
            raw["S1"] = proof1.raw_receipt
            if proof1.verdict != "PASS":
                return finish(
                    proof1.verdict, _prefix("S1", proof1.first_failure or "PROOF")
                )
            if not deliverable_present:
                return finish("FAIL", "S1_DELIVERABLE_MISSING")

            # No fallback exists: S2 reuses exactly S1's returned Session id.
            raw["S2_submission_attempts"] = 1
            request2 = request_id(f"session-s2-{route.agent_id}")
            payload2 = build_payload(
                config,
                route,
                case_ref="prompt:ars-quick-health:session-reuse:S2",
                prompt=prompt2,
                workspace=workspace,
                session_id=str(ack1.get("session_id", "")),
            )
            try:
                ack2 = client.submit(request_id=request2, payload=payload2)
            except Exception:
                return finish("INDETERMINATE", "S2_SUBMIT")
            try:
                result2 = await_terminal(
                    client,
                    str(ack2.get("run_id", "")),
                    config.controller_policy,
                    sleeper=sleeper,
                )
                events2 = read_all_events(
                    client,
                    str(ack2.get("run_id", "")),
                    page_limit=page_limit,
                    max_events=config.request_limits.max_events,
                )
                proof2 = prove_run(
                    config,
                    route,
                    ack=ack2,
                    request_id=request2,
                    result=result2,
                    events=events2,
                    session_operation="load",
                    liveness_checker=liveness_checker,
                )
            except CaseFailure as error:
                return finish("INDETERMINATE", _prefix("S2", error.category))
            raw["S2"] = proof2.raw_receipt
            if proof2.verdict != "PASS":
                return finish(
                    proof2.verdict, _prefix("S2", proof2.first_failure or "PROOF")
                )
            if ack1.get("run_id") == ack2.get("run_id"):
                return finish("FAIL", "RUNS_NOT_DISTINCT")
            if ack1.get("session_id") != ack2.get("session_id"):
                return finish("FAIL", "SESSION_CHANGED")
            if proof2.result.get("final_message") != token:
                return finish("FAIL", "TOKEN_MISMATCH")
            raw["continuity"] = {
                "distinct_runs": True,
                "same_session": True,
                "token_exact_match": True,
            }
    except Exception:
        return finish("INDETERMINATE", "OBSERVATION")
    return finish("PASS", None)


def run_session_reuse(
    config: ControllerConfig,
    *,
    client_factory: Callable[..., Any] = ArsdClient,
    sleeper: Callable[[float], None] = time.sleep,
    liveness_checker: Callable[[Mapping[str, Any]], str] = classify_holder,
    token_factory: Callable[[], str] = _new_token,
) -> dict[str, Any]:
    live, diagnostics = preflight(config, client_factory)
    sample_prompts = _prompts("CONTINUITY-" + "A" * 32)
    if any(
        len(prompt.encode("utf-8")) > live.max_prompt_bytes
        for prompt in sample_prompts
    ):
        raise ControllerError("PROMPT_LIMIT")
    root = create_output_root(config, diagnostics)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=live.workers_for(config)
    ) as executor:
        futures = {
            route.agent_id: executor.submit(
                _run_agent,
                config,
                route,
                root=root,
                page_limit=live.event_page_limit,
                client_factory=client_factory,
                sleeper=sleeper,
                liveness_checker=liveness_checker,
                token_factory=token_factory,
            )
            for route in config.routes
        }
        results = [futures[route.agent_id].result() for route in config.routes]
    summary = {
        "schema_version": 1,
        "controller": "session-reuse-create-load",
        "ars_package_version": live.package_version,
        "api_version": live.api_version,
        "results": results,
        "overall": summarize_overall(results),
    }
    write_summary(root, summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run fixed real Session create then load/reuse through arsd."
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
        summary = run_session_reuse(parsed.config, client_factory=ArsdClient)
    except ControllerError as error:
        summary = {
            "schema_version": 1,
            "controller": "session-reuse-create-load",
            "results": [],
            "overall": "INDETERMINATE",
            "error": error.category,
        }
    except Exception:
        summary = {
            "schema_version": 1,
            "controller": "session-reuse-create-load",
            "results": [],
            "overall": "INDETERMINATE",
            "error": "CONTROLLER",
        }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["overall"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

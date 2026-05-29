from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

from agent_run_supervisor.event_store import EventStore
from agent_run_supervisor.parser import ParseResult, parse_acpx_stdout
from agent_run_supervisor.preflight import probe_acpx, probe_node
from agent_run_supervisor.role import RoleValidationError, load_role, role_hash
from agent_run_supervisor.runner import SupervisorRunner
from agent_run_supervisor.workspace import WorkspaceValidationError

def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _parse_role_file(path: str | Path):
    raw = _load_json(path)
    return load_role(raw)


def _parse_result_payload(result: ParseResult) -> dict[str, Any]:
    return {
        "protocol_error": result.protocol_error,
        "protocol_error_reasons": result.protocol_error_reasons,
        "final_message": result.final_message,
        "usage": result.usage,
        "business_verdict": None,
        "truncated": result.truncated,
        "truncate_reason": result.truncate_reason,
        "unknown_update_types": result.unknown_update_types,
        "permission_request_count": result.permission_request_count,
        "permission_denied_count": result.permission_denied_count,
        "event_count": len(result.events),
    }


def cmd_validate_role(args: argparse.Namespace) -> int:
    try:
        role = _parse_role_file(args.role_file)
    except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
        print(f"role validation error: {exc}", file=sys.stderr)
        return 1
    _print_json(
        {
            "valid": True,
            "role_id": role.role_id,
            "role_hash": role_hash(role),
            "allowed_roots_security_boundary": role.workspace.allowed_roots_security_boundary,
        }
    )
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    try:
        result = parse_acpx_stdout(Path(args.events_file))
    except OSError as exc:
        print(f"protocol_error: cannot read events: {exc}", file=sys.stderr)
        return 1
    payload = _parse_result_payload(result)
    if result.protocol_error:
        print("protocol_error: " + "; ".join(result.protocol_error_reasons), file=sys.stderr)
        _print_json(payload)
        return 1
    _print_json(payload)
    return 0


def _default_fixture_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "acpx-0.10.0"


def cmd_doctor(args: argparse.Namespace) -> int:
    role_payload: dict[str, Any] | None = None
    role = None
    if args.role:
        try:
            role = _parse_role_file(args.role)
        except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
            print(f"role validation error: {exc}", file=sys.stderr)
            return 1
        role_payload = {"valid": True, "role_id": role.role_id, "role_hash": role_hash(role)}

    fixtures_dir = Path(args.fixtures) if args.fixtures else _default_fixture_dir()
    replay_path = fixtures_dir / "success-codex-sentinel" / "stdout.ndjson"
    fixture_replay: dict[str, Any]
    if replay_path.exists():
        replay = parse_acpx_stdout(replay_path)
        fixture_replay = _parse_result_payload(replay)
    else:
        fixture_replay = {
            "protocol_error": True,
            "protocol_error_reasons": [f"missing fixture {replay_path}"],
        }

    probe = EventStore(base_dir=Path.cwd() / ".tmp" / "doctor-event-store-probe").permission_probe()
    node_probe = probe_node()
    acpx_probe = probe_acpx(binary=role.runner.acpx_binary if role is not None else None)
    payload = {
        "ok": not fixture_replay.get("protocol_error", True),
        "python_version": platform.python_version(),
        "node_version_requirement": ">=22.12",
        "launched_real_agent": False,
        "event_store_probe": probe,
        "fixture_replay": fixture_replay,
        "role_validation": role_payload,
        "node_probe": node_probe,
        "acpx_probe": acpx_probe,
    }
    _print_json(payload)
    return 0 if payload["ok"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    try:
        role = _parse_role_file(args.role)
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
        print(f"run input error: {exc}", file=sys.stderr)
        return 1
    runner = SupervisorRunner(runs_dir=Path(args.runs_dir) if args.runs_dir else None)
    try:
        if args.no_real_run:
            outcome = runner.dry_run(role=role, prompt=prompt, cwd=args.cwd)
            _print_json(outcome.result)
            return 0
        outcome = runner.run(role=role, prompt=prompt, cwd=args.cwd)
    except WorkspaceValidationError as exc:
        print(f"workspace validation error: {exc}", file=sys.stderr)
        return 1
    _print_json(outcome.result)
    return 0 if outcome.result["status"] == "completed" else 1

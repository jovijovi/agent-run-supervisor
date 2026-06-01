from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

from agent_run_supervisor.event_store import EventStore
from agent_run_supervisor.parser import ParseResult, parse_acpx_stdout
from agent_run_supervisor.policy import ExecStrategyError
from agent_run_supervisor.preflight import (
    probe_acpx,
    probe_adapter,
    probe_node,
    probe_npx,
    probe_policy,
    probe_redaction,
    probe_session_readiness,
    probe_workspace,
)
from agent_run_supervisor.retention import (
    CleanupPlan,
    CleanupResult,
    RetentionError,
    RetentionPolicy,
    apply_cleanup,
    plan_cleanup,
)
from agent_run_supervisor.role import RoleValidationError, load_role, role_hash
from agent_run_supervisor.runner import SupervisorRunner
from agent_run_supervisor.session import SessionError
from agent_run_supervisor.session_runtime import SessionRuntime, SessionRuntimeError
from agent_run_supervisor.workspace import WorkspaceValidationError

DEFAULT_RUNS_DIR_NAME = Path(".agent-run-supervisor") / "runs"
DEFAULT_SESSIONS_DIR_NAME = Path(".agent-run-supervisor") / "sessions"

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

    # Always-on, pure-local read-only probes (deterministic in CI).
    redaction_probe = probe_redaction()
    session_probe = probe_session_readiness(role)

    # Role-dependent probes only run when a role is supplied.
    policy_probe = probe_policy(role) if role is not None else None
    workspace_probe = probe_workspace(role) if role is not None else None
    npx_probe = probe_npx(role) if role is not None else None
    adapter_probe = probe_adapter(role) if role is not None else None

    # ``ok`` gates only on pure-local deterministic probes so the no-role CI gate
    # keeps exiting 0 without node/acpx/npx. External-binary probes
    # (node/acpx/npx/adapter) are informational and never flip ``ok``.
    ok = (
        not fixture_replay.get("protocol_error", True)
        and bool(probe.get("dir_mode_ok"))
        and bool(probe.get("file_mode_ok"))
        and bool(redaction_probe.get("ok"))
        and bool(session_probe.get("ok"))
    )
    if role is not None:
        ok = ok and bool(policy_probe.get("ok")) and bool(workspace_probe.get("ok"))

    payload = {
        "ok": ok,
        "python_version": platform.python_version(),
        "node_version_requirement": ">=22.12",
        "launched_real_agent": False,
        "event_store_probe": probe,
        "fixture_replay": fixture_replay,
        "role_validation": role_payload,
        "node_probe": node_probe,
        "acpx_probe": acpx_probe,
        "redaction_probe": redaction_probe,
        "session_probe": session_probe,
        "policy_probe": policy_probe,
        "workspace_probe": workspace_probe,
        "npx_probe": npx_probe,
        "adapter_probe": adapter_probe,
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
    except ExecStrategyError as exc:
        print(f"session strategy error: {exc}", file=sys.stderr)
        return 1
    except WorkspaceValidationError as exc:
        print(f"workspace validation error: {exc}", file=sys.stderr)
        return 1
    _print_json(outcome.result)
    return 0 if outcome.result["status"] == "completed" else 1


def _resolve_sessions_dir(args: argparse.Namespace) -> Path:
    return (
        Path(args.sessions_dir)
        if args.sessions_dir
        else Path.cwd() / DEFAULT_SESSIONS_DIR_NAME
    )


def _cmd_session_list(args: argparse.Namespace, sessions_dir: Path) -> int:
    role = None
    if getattr(args, "role", None):
        try:
            role = _parse_role_file(args.role)
        except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
            print(f"session input error: {exc}", file=sys.stderr)
            return 1
    runtime = SessionRuntime(sessions_dir=sessions_dir)
    outcome = runtime.list_sessions(role=role)
    _print_json(outcome.result)
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    session_command = getattr(args, "session_command", None)
    if session_command is None:
        print(
            "error: a session subcommand is required "
            "(create | send | status | close | abort | list)",
            file=sys.stderr,
        )
        return 2

    sessions_dir = _resolve_sessions_dir(args)

    # ``list`` is local, read-only, and role-optional: handle it before the
    # role-required subcommands so it never requires a role file.
    if session_command == "list":
        return _cmd_session_list(args, sessions_dir)

    try:
        role = _parse_role_file(args.role)
    except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
        print(f"session input error: {exc}", file=sys.stderr)
        return 1

    runtime = SessionRuntime(sessions_dir=sessions_dir)

    if session_command == "send":
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"session input error: {exc}", file=sys.stderr)
            return 1

    try:
        if session_command == "create":
            outcome = runtime.create_session(
                role=role,
                session_id=args.session_id,
                session_name=args.session_name,
                cwd=args.cwd,
            )
            _print_json(outcome.result)
            return 0
        if session_command == "send":
            turn = runtime.send(
                role=role,
                session_id=args.session_id,
                prompt=prompt,
                cwd=args.cwd,
            )
            _print_json(turn.result)
            return 0 if turn.result["status"] == "completed" else 1
        if session_command == "status":
            status = runtime.status(
                role=role,
                session_id=args.session_id,
                cwd=args.cwd,
            )
            _print_json(status.result)
            return 0 if status.ok else 1
        if session_command == "close":
            closed = runtime.close(
                role=role,
                session_id=args.session_id,
                cwd=args.cwd,
            )
            _print_json(closed.result)
            return 0
        if session_command == "abort":
            aborted = runtime.abort(
                role=role,
                session_id=args.session_id,
                cwd=args.cwd,
            )
            _print_json(aborted.result)
            return 0
    except ExecStrategyError as exc:
        print(f"session strategy error: {exc}", file=sys.stderr)
        return 1
    except WorkspaceValidationError as exc:
        print(f"workspace validation error: {exc}", file=sys.stderr)
        return 1
    except SessionRuntimeError as exc:
        print(f"session runtime error: {exc}", file=sys.stderr)
        return 1
    except SessionError as exc:
        print(f"session error: {exc}", file=sys.stderr)
        return 1

    print(f"error: unknown session subcommand {session_command!r}", file=sys.stderr)
    return 2


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "kind": candidate.kind,
        "id": candidate.id,
        "path": str(candidate.path),
        "age_seconds": candidate.age_seconds,
        "action": candidate.action,
        "reason": candidate.reason,
    }


def _plan_payload(plan: CleanupPlan) -> dict[str, Any]:
    return {
        "root": str(plan.root),
        "runs_dir": str(plan.runs_dir),
        "sessions_dir": str(plan.sessions_dir),
        "delete": [_candidate_payload(c) for c in plan.delete],
        "skip": [_candidate_payload(c) for c in plan.skip],
    }


def cmd_cleanup(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runs_dir) if args.runs_dir else Path.cwd() / DEFAULT_RUNS_DIR_NAME
    sessions_dir = (
        Path(args.sessions_dir)
        if args.sessions_dir
        else Path.cwd() / DEFAULT_SESSIONS_DIR_NAME
    )
    policy = RetentionPolicy(max_age_days=args.max_age_days, max_count=args.max_count)
    try:
        plan = plan_cleanup(
            runs_dir=runs_dir,
            sessions_dir=sessions_dir,
            policy=policy,
        )
    except RetentionError as exc:
        print(f"retention error: {exc}", file=sys.stderr)
        return 1

    if not args.apply:
        # Dry-run is the default: list first, delete nothing.
        _print_json({"applied": False, "plan": _plan_payload(plan)})
        return 0

    result: CleanupResult = apply_cleanup(plan, confirm=True)
    _print_json(
        {
            "applied": True,
            "deleted": result.deleted,
            "failed": result.failed,
            "plan": _plan_payload(plan),
        }
    )
    return 0 if not result.failed else 1

from __future__ import annotations

import argparse
import sys
from typing import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-run-supervisor",
        description="Supervise ACP/acpx external AGENT runs and sessions with redacted local evidence.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    validate_role = subparsers.add_parser(
        "validate-role",
        help="Validate an AgentRoleSpec file.",
    )
    validate_role.add_argument("role_file", help="Path to a JSON AgentRoleSpec file.")

    replay = subparsers.add_parser(
        "replay",
        help="Replay an observed acpx stdout NDJSON stream through the parser.",
    )
    replay.add_argument("events_file", help="Path to acpx stdout NDJSON file.")

    doctor = subparsers.add_parser(
        "doctor",
        help="Run environment/fixture probes without launching a real AGENT.",
    )
    doctor.add_argument("--role", default=None, help="Optional role file to validate.")
    doctor.add_argument(
        "--fixtures",
        default=None,
        help="Optional fixture directory to replay.",
    )

    run = subparsers.add_parser(
        "run",
        help="Supervise local acpx exec or persist safe dry-run artifacts.",
        description="Supervise local acpx exec under AgentRoleSpec policy, or persist safe dry-run artifacts with --no-real-run.",
    )
    run.add_argument("--role", required=True, help="Path to AgentRoleSpec JSON file.")
    run.add_argument("--prompt-file", required=True, help="Path to prompt text file.")
    run.add_argument("--cwd", default=None, help="Override effective cwd for exec or dry-run compilation.")
    run.add_argument(
        "--no-real-run",
        action="store_true",
        help="Persist safe dry-run artifacts without launching local acpx exec.",
    )
    run.add_argument(
        "--runs-dir",
        default=None,
        help="Directory that holds run artifacts (defaults to .agent-run-supervisor/runs).",
    )

    _add_session_parser(subparsers)
    _add_cleanup_parser(subparsers)
    _add_runtime_binding_parser(subparsers)
    return parser


def _add_runtime_binding_parser(subparsers: argparse._SubParsersAction) -> None:
    """Operator surface for the Runtime Binding (PRD R13, C14).

    Exactly four subcommands. There is no ``--force``: a generation that cannot
    be validated is never promoted. No command escalates privilege, installs an
    artifact, edits a service unit, restarts ``arsd``, or contacts a provider.
    A pure promotion needs no restart because admission re-reads the active
    pointer per Run — it takes effect on the *next* Run and never re-points one
    that is already sealed.
    """
    binding = subparsers.add_parser(
        "runtime-binding",
        help="Validate, promote, roll back, or inspect an operator Runtime Binding.",
        description=(
            "Read-only inspection plus atomic replacement of active.json. "
            "Preparing an immutable, non-service-writable artifact root is an "
            "operator action outside this tool."
        ),
    )
    binding_sub = binding.add_subparsers(
        dest="runtime_binding_command", metavar="runtime_binding_command"
    )

    def _add_generation_args(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--binding-root",
            required=True,
            help="Operator-owned Binding root (read-only except active.json).",
        )
        sub.add_argument(
            "--profile",
            required=True,
            help="Registered profile id whose live contract must accept the generation.",
        )
        sub.add_argument("--generation", required=True, help="Generation id to act on.")
        sub.add_argument(
            "--trusted-uid",
            type=int,
            action="append",
            default=None,
            help=(
                "UID permitted to own the artifact root and its ancestors "
                "(repeatable). Root is always trusted; default is root only."
            ),
        )
        sub.add_argument(
            "--service-uid",
            type=int,
            default=None,
            help=(
                "The arsd/AGENT UID that must NOT be able to rewrite the "
                "artifacts (defaults to this process's effective UID)."
            ),
        )

    validate = binding_sub.add_parser(
        "validate",
        help="Probe-backed check of one generation against the live contract.",
    )
    _add_generation_args(validate)

    promote = binding_sub.add_parser(
        "promote",
        help="Validate, then atomically replace active.json. No daemon restart.",
    )
    _add_generation_args(promote)

    rollback = binding_sub.add_parser(
        "rollback",
        help=(
            "Re-promote a previously validated generation: the target is "
            "re-proven with the same validation and probe that promotion "
            "required, and must not already be active."
        ),
    )
    _add_generation_args(rollback)

    inspect = binding_sub.add_parser(
        "inspect-run",
        help="Recompute one Run's launch seal and report its runtime provenance.",
    )
    inspect.add_argument(
        "--run-dir", required=True, help="Native run directory holding launch.json."
    )


def _add_cleanup_parser(subparsers: argparse._SubParsersAction) -> None:
    """Confined, dry-run-first run/session artifact retention/cleanup."""
    cleanup = subparsers.add_parser(
        "cleanup",
        help="Plan (dry-run) or apply confined retention cleanup of local run/session artifacts.",
        description=(
            "List run/session artifacts under a .agent-run-supervisor root that a "
            "retention policy would remove. Dry-run by default; --apply deletes only "
            "the planned, confined, non-live entries."
        ),
    )
    cleanup.add_argument(
        "--runs-dir",
        default=None,
        help="Directory that holds run artifacts (defaults to .agent-run-supervisor/runs).",
    )
    cleanup.add_argument(
        "--sessions-dir",
        default=None,
        help="Directory that holds session records (defaults to .agent-run-supervisor/sessions).",
    )
    cleanup.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Delete artifacts strictly older than this many days.",
    )
    cleanup.add_argument(
        "--max-count",
        type=int,
        default=None,
        help="Keep only the newest N eligible artifacts; delete the rest.",
    )
    cleanup.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete the planned entries (default is a read-only dry-run).",
    )


def _add_session_parser(subparsers: argparse._SubParsersAction) -> None:
    """Persistent-session lifecycle: ``session create|send|status|close|abort|list``."""
    session = subparsers.add_parser(
        "session",
        help="Persistent session lifecycle (create / send / status / close / abort / list).",
        description="Create/open a role-bound persistent session, send a prompt turn, query status, close, abort/cancel, or list local session records.",
    )
    session_sub = session.add_subparsers(dest="session_command", metavar="session_command")

    def _add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--role", required=True, help="Path to AgentRoleSpec JSON file.")
        sub.add_argument("--session-id", required=True, help="Local session id (safe path component).")
        sub.add_argument("--cwd", default=None, help="Override effective cwd for session work.")
        sub.add_argument(
            "--sessions-dir",
            default=None,
            help="Directory that holds session records (defaults to .agent-run-supervisor/sessions).",
        )

    create = session_sub.add_parser("create", help="Create/open a local persistent session.")
    _add_common(create)
    create.add_argument(
        "--session-name",
        default=None,
        help="acpx session name (defaults to --session-id).",
    )

    send = session_sub.add_parser("send", help="Send one prompt turn to a session.")
    _add_common(send)
    prompt_source = send.add_mutually_exclusive_group(required=True)
    prompt_source.add_argument("--prompt-file", help="Path to prompt text file.")
    prompt_source.add_argument(
        "--goal-file",
        help=(
            "Path to goal text; validated and compiled into the adapter-"
            "appropriate goal turn (the goal-contract/v1 text template unless "
            "the adapter natively supports a /goal command)."
        ),
    )

    status = session_sub.add_parser("status", help="Query a session's status/show record.")
    _add_common(status)

    close = session_sub.add_parser("close", help="Close an open local persistent session.")
    _add_common(close)

    abort = session_sub.add_parser("abort", help="Cooperatively cancel/abort active session work.")
    _add_common(abort)

    list_sessions = session_sub.add_parser(
        "list",
        help="List local session records (read-only; does not launch acpx/AGENT work).",
    )
    list_sessions.add_argument(
        "--sessions-dir",
        default=None,
        help="Directory that holds session records (defaults to .agent-run-supervisor/sessions).",
    )
    list_sessions.add_argument(
        "--role",
        default=None,
        help="Optional role file; if given, list only records owned by that role.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        print("error: a subcommand is required", file=sys.stderr)
        return 2

    if args.command == "validate-role":
        from agent_run_supervisor.commands import cmd_validate_role

        return cmd_validate_role(args)
    if args.command == "replay":
        from agent_run_supervisor.commands import cmd_replay

        return cmd_replay(args)
    if args.command == "doctor":
        from agent_run_supervisor.commands import cmd_doctor

        return cmd_doctor(args)
    if args.command == "run":
        from agent_run_supervisor.commands import cmd_run

        return cmd_run(args)
    if args.command == "session":
        from agent_run_supervisor.commands import cmd_session

        return cmd_session(args)
    if args.command == "cleanup":
        from agent_run_supervisor.commands import cmd_cleanup

        return cmd_cleanup(args)
    if args.command == "runtime-binding":
        from agent_run_supervisor.commands import cmd_runtime_binding

        return cmd_runtime_binding(args)
    parser.print_usage(sys.stderr)
    print(f"error: unknown command {args.command}", file=sys.stderr)
    return 2

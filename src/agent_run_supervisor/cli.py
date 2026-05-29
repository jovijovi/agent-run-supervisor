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
        help="Compile a role+prompt; refuses real agent launch until E1 exec runner support lands.",
        description="Compile a role+prompt; refuses real agent launch until E1 exec runner support lands.",
    )
    run.add_argument("--role", required=True, help="Path to AgentRoleSpec JSON file.")
    run.add_argument("--prompt-file", required=True, help="Path to prompt text file.")
    run.add_argument("--cwd", default=None, help="Override effective cwd for dry-run compilation.")
    run.add_argument(
        "--no-real-run",
        action="store_true",
        help="Persist safe dry-run artifacts while real exec runner support is incomplete.",
    )
    run.add_argument(
        "--runs-dir",
        default=None,
        help="Directory that holds run artifacts (defaults to .agent-run-supervisor/runs).",
    )
    return parser


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
    parser.print_usage(sys.stderr)
    print(f"error: unknown command {args.command}", file=sys.stderr)
    return 2

from __future__ import annotations

import argparse
import sys
from typing import Sequence


def _build_parser() -> argparse.ArgumentParser:
    """The complete installed operator surface: three read-only commands.

    ``agents validate``, ``agents doctor``, and ``run inspect`` — the three the
    design authority declares, and deliberately no fourth. Nothing here starts a
    Run: production ingress is ``arsd`` over its local socket, and this console
    script is the operator's read-only window onto the registry and onto one
    Run's recorded launch evidence.
    """
    parser = argparse.ArgumentParser(
        prog="agent-run-supervisor",
        description=(
            "Read-only operator checks over the agent registry and per-Run "
            "launch evidence. Runs are submitted to arsd, not to this command."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    run = subparsers.add_parser(
        "run",
        help="Per-Run evidence (read-only).",
        description="Read one Run's recorded evidence. Starts nothing.",
    )
    _add_run_inspect_parser(run)
    _add_agents_parser(subparsers)
    return parser


def _add_agents_parser(subparsers: argparse._SubParsersAction) -> None:
    """Operator surface for the agent registry.

    Exactly two subcommands, and deliberately no third. There is no ``promote``,
    no ``rollback``, and no ``--force``: nothing here installs an artifact, edits
    a service unit, restarts ``arsd``, escalates privilege, or contacts a
    provider. A registry edit takes effect at the next daemon start, which is a
    service action an operator takes, not something this tool can do.
    """
    agents = subparsers.add_parser(
        "agents",
        help="Validate the operator agent registry, or run a per-agent doctor.",
        description=(
            "Read-only checks over the operator-owned agents file. Output names "
            "entry ids, counts, environment names, source classes, and rule "
            "outcomes — never an overlay or mediation value."
        ),
    )
    agents_sub = agents.add_subparsers(dest="agents_command", metavar="agents_command")

    validate = agents_sub.add_parser(
        "validate",
        help=(
            "Parse the agents file and apply the identical checks the daemon "
            "applies at startup, including the mediation-key collision rule."
        ),
    )
    validate.add_argument(
        "--agents-file", required=True, help="Path to the operator agents file."
    )

    doctor = agents_sub.add_parser(
        "doctor",
        help=(
            "Report the projected environment name set and declared launch for "
            "each registered agent, plus an optional zero-prompt ACP probe."
        ),
    )
    doctor.add_argument(
        "--agents-file", required=True, help="Path to the operator agents file."
    )
    doctor.add_argument(
        "--agent", default=None, help="Limit the report to one registered agent id."
    )
    doctor.add_argument(
        "--no-probe",
        action="store_true",
        help=(
            "Report the projection only and start no external child. Without "
            "this flag doctor starts the registered command, which writes that "
            "AGENT's own state."
        ),
    )


def _add_run_inspect_parser(run: argparse.ArgumentParser) -> None:
    """``run inspect`` — per-Run evidence, read-only, value-blind for legacy."""
    run_sub = run.add_subparsers(dest="run_command", metavar="run_command")
    inspect = run_sub.add_parser(
        "inspect",
        help=(
            "Report one Run's launch evidence. A reset-schema record has its "
            "value-blind launch hash recomputed; a pre-reset record is "
            "classified first and its value-bearing material withheld."
        ),
    )
    inspect.add_argument(
        "--run-dir", required=True, help="Native run directory holding launch.json."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        print("error: a subcommand is required", file=sys.stderr)
        return 2

    if args.command == "run":
        if getattr(args, "run_command", None) != "inspect":
            parser.print_usage(sys.stderr)
            print("error: run requires the inspect subcommand", file=sys.stderr)
            return 2
        from agent_run_supervisor.commands import cmd_run_inspect

        return cmd_run_inspect(args)
    if args.command == "agents":
        from agent_run_supervisor.commands import cmd_agents

        return cmd_agents(args)
    parser.print_usage(sys.stderr)
    print(f"error: unknown command {args.command}", file=sys.stderr)
    return 2

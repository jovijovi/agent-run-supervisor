from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC_DIR), existing) if p)
    return subprocess.run(
        [sys.executable, "-m", "agent_run_supervisor", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(REPO_ROOT),
    )


def test_module_imports() -> None:
    import agent_run_supervisor

    assert hasattr(agent_run_supervisor, "__version__")


def test_module_version_matches_pyproject() -> None:
    import tomllib

    import agent_run_supervisor

    pyproject = REPO_ROOT / "pyproject.toml"
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    assert agent_run_supervisor.__version__ == data["project"]["version"]


#: The complete installed operator surface (D2). Nothing else is a command.
SUPPORTED_LEAVES = (("agents", "validate"), ("agents", "doctor"), ("run", "inspect"))
#: Commands the acpx removal deleted. A regression here is a resurrection.
REMOVED_LEAVES = ("validate-role", "replay", "doctor", "session", "cleanup")


def test_cli_help_lists_only_the_supported_top_level_commands() -> None:
    completed = _run_cli("--help")

    assert completed.returncode == 0, completed.stderr
    assert "agents" in completed.stdout, completed.stdout
    assert "run" in completed.stdout, completed.stdout
    assert "acpx" not in completed.stdout.lower(), completed.stdout


def test_cli_help_names_no_removed_command() -> None:
    """``--help`` is the surface an operator reads; it may not advertise a ghost."""
    completed = _run_cli("--help")
    words = set(completed.stdout.split())

    for leaf in REMOVED_LEAVES:
        assert leaf not in words, f"--help still advertises {leaf!r}\n{completed.stdout}"


def test_removed_top_level_commands_are_rejected() -> None:
    for leaf in ("validate-role", "replay", "doctor", "session", "cleanup"):
        completed = _run_cli(leaf)
        assert completed.returncode != 0, (leaf, completed.stdout)


def test_run_without_inspect_is_rejected() -> None:
    """``run`` survives only as the parent of ``inspect``; the exec leaf is gone."""
    completed = _run_cli("run")
    assert completed.returncode == 2
    assert "inspect" in completed.stderr

    completed = _run_cli("run", "--role", "role.json", "--prompt-file", "p.txt")
    assert completed.returncode != 0


def test_cli_no_subcommand_exits_nonzero() -> None:
    completed = _run_cli()

    assert completed.returncode != 0
    assert "usage" in (completed.stderr + completed.stdout).lower()


def test_cli_unknown_subcommand_exits_nonzero() -> None:
    completed = _run_cli("not-a-real-command")

    assert completed.returncode != 0


def test_supported_subcommand_help_exits_zero_and_is_acpx_free() -> None:
    for leaf in SUPPORTED_LEAVES:
        completed = _run_cli(*leaf, "--help")
        assert completed.returncode == 0, (leaf, completed.stderr)
        assert "acpx" not in completed.stdout.lower(), (leaf, completed.stdout)


def test_run_help_offers_inspect_and_no_exec_boundary() -> None:
    completed = _run_cli("run", "--help")

    assert completed.returncode == 0, completed.stderr
    help_text = completed.stdout.lower()
    assert "inspect" in help_text
    assert "acpx" not in help_text
    assert "--no-real-run" not in help_text
    assert "--prompt-file" not in help_text
    assert "gateway" not in help_text

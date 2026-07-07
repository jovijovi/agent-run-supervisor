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


def test_cli_help_lists_subcommands() -> None:
    completed = _run_cli("--help")

    assert completed.returncode == 0, completed.stderr
    for subcommand in ("validate-role", "replay", "doctor", "run"):
        assert subcommand in completed.stdout, completed.stdout


def test_cli_no_subcommand_exits_nonzero() -> None:
    completed = _run_cli()

    assert completed.returncode != 0
    assert "usage" in (completed.stderr + completed.stdout).lower()


def test_cli_unknown_subcommand_exits_nonzero() -> None:
    completed = _run_cli("not-a-real-command")

    assert completed.returncode != 0


def test_cli_subcommand_help_exits_zero() -> None:
    for subcommand in ("validate-role", "replay", "doctor", "run"):
        completed = _run_cli(subcommand, "--help")
        assert completed.returncode == 0, (subcommand, completed.stderr)


def test_run_help_describes_local_exec_boundary() -> None:
    completed = _run_cli("run", "--help")

    assert completed.returncode == 0, completed.stderr
    help_text = completed.stdout.lower()
    assert "supervise local acpx exec" in help_text
    assert "refuses real agent launch" not in help_text
    assert "real_run_disabled" not in help_text
    assert "gateway" not in help_text

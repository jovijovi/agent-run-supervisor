from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"


def _run_cli_impl(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC_DIR), existing) if p)
    return subprocess.run(
        [sys.executable, "-m", "agent_run_supervisor", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(cwd or REPO_ROOT),
    )


@pytest.fixture()
def run_cli():
    return _run_cli_impl


@pytest.fixture()
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture()
def prompt_file(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.txt"
    path.write_text("Be brief.\nSay hello.", encoding="utf-8")
    return path

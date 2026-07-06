from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
FIXTURES_ROOT = REPO_ROOT / "fixtures" / "acpx-0.12.0"


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
def fixtures_root() -> Path:
    return FIXTURES_ROOT


@pytest.fixture()
def valid_role_dict() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "role_id": "codex-reviewer",
        "display_name": "Codex Review",
        "description": "Read-only reviewer.",
        "runner": {
            "type": "acpx",
            "acpx_version": "0.12.0",
            "acpx_binary": None,
            "adapter_agent": "codex",
            "model": "gpt-5.5[low]",
        },
        "workspace": {
            "default_cwd": "/tmp/work",
            "allowed_roots": ["/tmp/work"],
            "allowed_roots_security_boundary": False,
        },
        "permissions": {
            "read": True,
            "search": True,
            "write": False,
            "execute": False,
            "terminal": False,
            "delete": False,
            "move": False,
            "fetch": False,
            "switch_mode": False,
            "other": False,
        },
        "session": {"strategy": "exec"},
        "limits": {
            "timeout_seconds": 60,
            "max_turns": 1,
            "max_output_bytes": 10_485_760,
        },
        "prompt": {
            "role_instruction": "Be brief.",
            "output_contract": "Return text.",
        },
        "redaction": {
            "suppress_reads": True,
            "redact_prompt": True,
            "redact_stderr": True,
            "redact_metadata": True,
            "redact_env": True,
        },
    }


@pytest.fixture()
def role_file(tmp_path: Path, valid_role_dict: dict[str, Any]) -> Path:
    path = tmp_path / "role.json"
    path.write_text(json.dumps(valid_role_dict), encoding="utf-8")
    return path


@pytest.fixture()
def prompt_file(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.txt"
    path.write_text("Be brief.\nSay hello.", encoding="utf-8")
    return path

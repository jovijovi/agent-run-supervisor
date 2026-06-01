from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import tools.static_safety_scan as static_safety_scan

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_static_safety_scan_passes_on_current_repository() -> None:
    report = static_safety_scan.run_scan(REPO_ROOT)

    assert report["ok"] is True
    assert report["counts"] == {"secret": 0, "source_ast": 0, "stale": 0, "total": 0}


def test_static_safety_scan_detects_secret_danger_and_stale_phrase(tmp_path: Path) -> None:
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    stale_red_phrase = "RED " + "expectation: old TDD text\n"
    (tmp_path / "README.md").write_text(stale_red_phrase, encoding="utf-8")
    openai_project_key = "sk-" + "proj-" + "A" * 24
    (tmp_path / ".env").write_text("OPENAI_API_KEY=" + openai_project_key + "\n", encoding="utf-8")
    (src / "bad.py").write_text("import requests\nimport os\nos.system('echo unsafe')\n", encoding="utf-8")

    report = static_safety_scan.run_scan(tmp_path)

    assert report["ok"] is False
    kinds = {finding["kind"] for finding in report["findings"]}
    assert "secret:openai_key" in kinds
    assert "forbidden_import:requests" in kinds
    assert "dangerous_call:os.system" in kinds
    assert "stale:red_expectation_tail" in kinds


def test_static_safety_scan_cli_returns_nonzero_on_findings(tmp_path: Path) -> None:
    stale_pr_phrase = "ready-for" + "-PR\n"
    (tmp_path / "README.md").write_text(stale_pr_phrase, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "static_safety_scan.py"), str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["counts"]["stale"] == 1

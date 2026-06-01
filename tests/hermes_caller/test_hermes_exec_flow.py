"""T6 — exec document-check flow (plan §6.7, §7, §10-T6).

``HermesDocCheckCaller.run_exec`` orchestrates a one-shot check above the I1
boundary: it builds an exec spec, delegates through ``invoke_caller`` (here with
an injected fake runner, so nothing launches acpx), derives a caller-owned
verdict, and returns ``(CallerResult, CardViewModel)``. The ``dry_run=True`` path
uses ``exec_dry_run`` and launches no AGENT.

RED expectation: the ``hermes_caller.hermes`` module does not exist yet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.caller import CallerResult
from agent_run_supervisor.hermes_caller.hermes import HermesDocCheckCaller
from agent_run_supervisor.hermes_caller.task import DocCheckTask
from agent_run_supervisor.hermes_caller.view_model import CardViewModel


@pytest.fixture()
def task(doc_task_kwargs: dict[str, Any]) -> DocCheckTask:
    return DocCheckTask(**doc_task_kwargs)


def test_run_exec_returns_result_and_view_model(
    task: DocCheckTask, exec_doc_role, fake_runner, work_dir: Path, tmp_path: Path
) -> None:
    caller = HermesDocCheckCaller(runner=fake_runner)

    result, view_model = caller.run_exec(
        task, role=exec_doc_role, cwd=work_dir, runs_dir=tmp_path / "runs"
    )

    assert isinstance(result, CallerResult)
    assert isinstance(view_model, CardViewModel)
    assert result.mode == "exec"

    # The real run path was driven exactly once; dry_run was not touched.
    assert len(fake_runner.run_calls) == 1
    assert fake_runner.dry_run_calls == []


def test_run_exec_dry_run_launches_nothing(
    task: DocCheckTask, exec_doc_role, fake_runner, work_dir: Path
) -> None:
    caller = HermesDocCheckCaller(runner=fake_runner)

    result, view_model = caller.run_exec(task, role=exec_doc_role, cwd=work_dir, dry_run=True)

    assert isinstance(result, CallerResult)
    assert isinstance(view_model, CardViewModel)
    assert result.mode == "exec_dry_run"

    # A dry run previews configuration only — the real runner is never invoked.
    assert len(fake_runner.dry_run_calls) == 1
    assert fake_runner.run_calls == []

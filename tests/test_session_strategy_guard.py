"""Fail-closed guard: exec compilation/launch must refuse persistent roles.

Once `AgentRoleSpec` accepts `session.strategy='persistent'` (S1b), the exec
path must NOT silently launch a one-shot `acpx exec` for a persistent-session
role. `validate-role` may accept persistent roles, but `run`/`dry_run`/compile
must fail closed before any artifact is written or any process is launched.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import pytest

from agent_run_supervisor.policy import ExecStrategyError, compile_command
from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import SubprocessOutcome, SupervisorRunner

from tests.test_role import VALID_ROLE


def _persistent_role(work_dir: Path) -> Any:
    spec = copy.deepcopy(VALID_ROLE)
    spec["workspace"] = dict(spec["workspace"])
    spec["workspace"]["default_cwd"] = str(work_dir)
    spec["workspace"]["allowed_roots"] = [str(work_dir)]
    spec["session"] = {"strategy": "persistent"}
    return load_role(spec)


class _NeverExecutor:
    """Executor that records calls; must never be invoked for persistent roles."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        grace_ms: int,
    ) -> SubprocessOutcome:
        self.calls.append({"argv": argv})
        raise AssertionError("executor must not run for a persistent-session role")


def test_compile_command_refuses_persistent_strategy(tmp_path: Path) -> None:
    role = _persistent_role(tmp_path)

    with pytest.raises(ExecStrategyError) as excinfo:
        compile_command(role, cwd=str(tmp_path), prompt="hello")
    assert "persistent" in str(excinfo.value)


def test_runner_run_refuses_persistent_role_before_launch(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _persistent_role(work)
    executor = _NeverExecutor()
    runs_dir = tmp_path / "runs"
    runner = SupervisorRunner(runs_dir=runs_dir, executor=executor)

    with pytest.raises(ExecStrategyError):
        runner.run(role=role, prompt="hello", cwd=None, env={"PATH": "/usr/bin"})

    assert executor.calls == []
    # Fail closed: no run artifacts are created for a refused persistent role.
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []


def test_runner_dry_run_refuses_persistent_role(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _persistent_role(work)
    runs_dir = tmp_path / "runs"
    runner = SupervisorRunner(runs_dir=runs_dir)

    with pytest.raises(ExecStrategyError):
        runner.dry_run(role=role, prompt="hello", cwd=None, env={"PATH": "/usr/bin"})

    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []


def test_runner_finalize_outcome_refuses_persistent_role(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _persistent_role(work)
    runs_dir = tmp_path / "runs"
    runner = SupervisorRunner(runs_dir=runs_dir)
    outcome = SubprocessOutcome(exit_code=0, signal=None, stdout=b"", stderr=b"")

    with pytest.raises(ExecStrategyError):
        runner.finalize_outcome(
            role=role,
            prompt="hello",
            cwd=None,
            subprocess_outcome=outcome,
            env={"PATH": "/usr/bin"},
        )

    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []

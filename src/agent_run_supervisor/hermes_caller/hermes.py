"""Orchestration of exec + persistent-session document-check flows.

``HermesDocCheckCaller`` composes the intake adapter, the generic I1 boundary
(``invoke_caller``), the caller-owned verdict, and the presentation view-model.
Optional ``runner`` / ``session_runtime`` are injected for fake/local testing and
passed straight through to ``invoke_caller``. Nothing here parses raw streams,
delivers messages, or carries platform identifiers — binding, leasing, and
redaction stay supervisor-owned below the boundary.
"""
from __future__ import annotations

from pathlib import Path

from agent_run_supervisor.caller import CallerResult, invoke_caller
from agent_run_supervisor.role import AgentRoleSpec

from .events import load_events
from .intake import (
    build_exec_spec,
    build_session_close_spec,
    build_session_create_spec,
    build_session_send_spec,
    build_session_status_spec,
)
from .task import DocCheckTask
from .verdict import derive_verdict
from .view_model import CardViewModel, build_progress_view_model, build_result_view_model


class HermesDocCheckCaller:
    """Concrete local caller orchestrating a document-check above the I1 boundary."""

    def __init__(self, *, runner=None, session_runtime=None) -> None:
        self._runner = runner
        self._session_runtime = session_runtime

    def run_exec(
        self,
        task: DocCheckTask,
        *,
        role: AgentRoleSpec,
        cwd: str | Path,
        runs_dir: str | Path | None = None,
        dry_run: bool = False,
    ) -> tuple[CallerResult, CardViewModel]:
        """One-shot document-check: invoke exec, derive verdict, build view-model.

        A ``dry_run`` previews configuration only (mode ``exec_dry_run``) and
        launches no AGENT, so it returns a preparing/preview view-model.
        """
        spec = build_exec_spec(
            task, role=role, cwd=cwd, runs_dir=runs_dir, dry_run=dry_run
        )
        result = invoke_caller(spec, runner=self._runner)

        if dry_run:
            return result, build_progress_view_model([])

        return result, self._result_view_model(result)

    def run_session(
        self,
        task: DocCheckTask,
        *,
        role: AgentRoleSpec,
        session_id: str,
        turn_prompts: list[str],
        cwd: str | Path,
        sessions_dir: str | Path | None = None,
    ) -> tuple[list[CallerResult], CardViewModel]:
        """Interactive multi-turn check: create -> send* -> status -> close.

        Returns each turn/management ``CallerResult`` plus the final result
        view-model, whose session lifecycle has advanced to ``closed``.
        """
        results: list[CallerResult] = []

        results.append(
            invoke_caller(
                build_session_create_spec(
                    task, role=role, session_id=session_id, cwd=cwd,
                    sessions_dir=sessions_dir,
                ),
                session_runtime=self._session_runtime,
            )
        )

        turn_results: list[CallerResult] = []
        for prompt in turn_prompts:
            turn = invoke_caller(
                build_session_send_spec(
                    task, role=role, session_id=session_id, prompt=prompt,
                    cwd=cwd, sessions_dir=sessions_dir,
                ),
                session_runtime=self._session_runtime,
            )
            turn_results.append(turn)
            results.append(turn)

        results.append(
            invoke_caller(
                build_session_status_spec(
                    task, role=role, session_id=session_id, cwd=cwd,
                    sessions_dir=sessions_dir,
                ),
                session_runtime=self._session_runtime,
            )
        )

        results.append(
            invoke_caller(
                build_session_close_spec(
                    task, role=role, session_id=session_id, cwd=cwd,
                    sessions_dir=sessions_dir,
                ),
                session_runtime=self._session_runtime,
            )
        )

        final = turn_results[-1] if turn_results else results[-1]
        view_model = self._result_view_model(final, session_lifecycle="closed")
        return results, view_model

    @staticmethod
    def _result_view_model(
        result: CallerResult,
        *,
        session_lifecycle: str | None = None,
    ) -> CardViewModel:
        artifact_dir = result.run_dir or result.artifact_dir
        events = load_events(artifact_dir) if artifact_dir is not None else []
        decision = derive_verdict(result)
        return build_result_view_model(
            result, events, decision, session_lifecycle=session_lifecycle
        )

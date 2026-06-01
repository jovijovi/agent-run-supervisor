"""T7 — persistent-session document-check flow (plan §6.7, §8, §10-T7).

``HermesDocCheckCaller.run_session`` orchestrates an interactive multi-turn check
above the I1 boundary, driving create -> send (per turn prompt) -> status ->
close through an injected fake session-runtime (nothing launches acpx). It
returns one ``CallerResult`` per call plus a final result view-model whose
session lifecycle has advanced to ``closed``.

GREEN coverage: the ``hermes_caller.hermes`` module now drives the session flow.
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


def test_run_session_drives_full_lifecycle(
    task: DocCheckTask, persistent_doc_role, fake_session_runtime, work_dir: Path
) -> None:
    caller = HermesDocCheckCaller(session_runtime=fake_session_runtime)

    results, view_model = caller.run_session(
        task,
        role=persistent_doc_role,
        session_id="[REDACTED]",
        turn_prompts=["re-check section 1", "re-check section 2"],
        cwd=work_dir,
        sessions_dir=fake_session_runtime.sessions_dir,
    )

    # create -> send -> send -> status -> close, in order.
    assert fake_session_runtime.calls == [
        "create_session",
        "send",
        "send",
        "status",
        "close",
    ]

    # One CallerResult per call.
    assert isinstance(results, list)
    assert len(results) == 5
    assert all(isinstance(r, CallerResult) for r in results)

    # The final view-model reflects a closed session lifecycle.
    assert isinstance(view_model, CardViewModel)
    assert view_model.session_lifecycle == "closed"

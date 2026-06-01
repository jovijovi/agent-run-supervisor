"""Intake/role adapter: ``DocCheckTask`` -> generic ``CallerInvocationSpec``.

The adapter maps a caller-owned task to the generic I1 boundary spec. It
populates ONLY generic supervisor fields (mode/role/prompt/context/cwd/
runs_dir/sessions_dir/session_id/session_name) and never carries a task-side or
platform field. Document resolution is a local read only — no network.
"""
from __future__ import annotations

from pathlib import Path

from agent_run_supervisor.caller import (
    EXEC_DRY_RUN_MODE,
    EXEC_MODE,
    SESSION_CLOSE_MODE,
    SESSION_CREATE_MODE,
    SESSION_SEND_MODE,
    SESSION_STATUS_MODE,
    CallerInvocationSpec,
)
from agent_run_supervisor.role import AgentRoleSpec

from .task import DocCheckTask


def resolve_document(task: DocCheckTask) -> str:
    """Resolve a caller-side ``document_ref`` to local check-context text.

    Local-only read; no network. The returned text becomes the caller-owned
    ``context`` carried into the generic spec.
    """
    return Path(task.document_ref).read_text(encoding="utf-8")


def build_check_prompt(task: DocCheckTask) -> str:
    """Build caller-owned check instructions (the ``prompt``)."""
    return (
        f"Perform a document check using the '{task.check_profile}' profile. "
        "Inspect the provided document for completeness and cross-reference "
        "consistency, and report any findings."
    )


def build_exec_spec(
    task: DocCheckTask,
    *,
    role: AgentRoleSpec,
    cwd: str | Path,
    runs_dir: str | Path | None = None,
    dry_run: bool = False,
) -> CallerInvocationSpec:
    """Map a task to an ``exec`` (or ``exec_dry_run``) spec — no platform fields."""
    return CallerInvocationSpec(
        mode=EXEC_DRY_RUN_MODE if dry_run else EXEC_MODE,
        role=role,
        prompt=build_check_prompt(task),
        context=resolve_document(task),
        cwd=cwd,
        runs_dir=runs_dir,
    )


def build_session_create_spec(
    task: DocCheckTask,
    *,
    role: AgentRoleSpec,
    session_id: str,
    cwd: str | Path,
    sessions_dir: str | Path | None = None,
    session_name: str | None = None,
) -> CallerInvocationSpec:
    """Map a task to a ``session_create`` spec."""
    return CallerInvocationSpec(
        mode=SESSION_CREATE_MODE,
        role=role,
        cwd=cwd,
        sessions_dir=sessions_dir,
        session_id=session_id,
        session_name=session_name,
    )


def build_session_send_spec(
    task: DocCheckTask,
    *,
    role: AgentRoleSpec,
    session_id: str,
    prompt: str,
    cwd: str | Path,
    sessions_dir: str | Path | None = None,
) -> CallerInvocationSpec:
    """Map a per-turn prompt to a ``session_send`` spec."""
    return CallerInvocationSpec(
        mode=SESSION_SEND_MODE,
        role=role,
        prompt=prompt,
        cwd=cwd,
        sessions_dir=sessions_dir,
        session_id=session_id,
    )


def build_session_status_spec(
    task: DocCheckTask,
    *,
    role: AgentRoleSpec,
    session_id: str,
    cwd: str | Path,
    sessions_dir: str | Path | None = None,
) -> CallerInvocationSpec:
    """Map a task to a read-only ``session_status`` spec."""
    return CallerInvocationSpec(
        mode=SESSION_STATUS_MODE,
        role=role,
        cwd=cwd,
        sessions_dir=sessions_dir,
        session_id=session_id,
    )


def build_session_close_spec(
    task: DocCheckTask,
    *,
    role: AgentRoleSpec,
    session_id: str,
    cwd: str | Path,
    sessions_dir: str | Path | None = None,
) -> CallerInvocationSpec:
    """Map a task to a ``session_close`` spec."""
    return CallerInvocationSpec(
        mode=SESSION_CLOSE_MODE,
        role=role,
        cwd=cwd,
        sessions_dir=sessions_dir,
        session_id=session_id,
    )

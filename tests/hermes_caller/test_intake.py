"""T1 — intake/role adapter: DocCheckTask -> generic CallerInvocationSpec.

Behavioral contract (plan §6.1/§6.2, §7, §10-T1):
  * builds exec and exec_dry_run specs as well as the four session specs;
  * populates ONLY generic supervisor fields (mode/role/prompt/context/cwd/
    runs_dir/sessions_dir/session_id/session_name);
  * never carries a task or platform field (task_id/document_ref/requested_by/
    surface/channel/webhook/...).

GREEN coverage: ``agent_run_supervisor.hermes_caller`` intake exists and must
stay local/offline without platform identifiers.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.caller import CallerInvocationSpec
from agent_run_supervisor.hermes_caller.intake import (
    build_check_prompt,
    build_exec_spec,
    build_session_close_spec,
    build_session_create_spec,
    build_session_send_spec,
    build_session_status_spec,
    resolve_document,
)
from agent_run_supervisor.hermes_caller.task import DocCheckTask

# Generic supervisor spec fields — the ONLY attributes intake may populate.
GENERIC_SPEC_FIELDS = {
    "mode",
    "role",
    "role_file",
    "prompt",
    "context",
    "cwd",
    "runs_dir",
    "sessions_dir",
    "session_id",
    "session_name",
}

# Task-side / platform identifiers that must never appear on a spec.
FORBIDDEN_SPEC_ATTRS = {
    "task_id",
    "document_ref",
    "check_profile",
    "requested_by",
    "surface",
    "platform",
    "channel",
    "channel_id",
    "webhook",
    "recipient",
    "gateway",
    "delivery",
}


def _assert_generic_only(spec: Any) -> None:
    # It must be exactly the generic boundary type, not a platform-extended subclass.
    assert type(spec) is CallerInvocationSpec
    field_names = {f.name for f in dataclasses.fields(spec)}
    assert field_names == GENERIC_SPEC_FIELDS
    for attr in FORBIDDEN_SPEC_ATTRS:
        assert not hasattr(spec, attr), f"spec leaked task/platform field {attr!r}"


@pytest.fixture()
def task(doc_task_kwargs: dict[str, Any]) -> DocCheckTask:
    return DocCheckTask(**doc_task_kwargs)


def test_resolve_document_reads_local_check_material(task: DocCheckTask, doc_body_sentinel: str) -> None:
    context = resolve_document(task)
    assert isinstance(context, str)
    assert doc_body_sentinel in context


def test_build_check_prompt_is_caller_owned_and_nonempty(task: DocCheckTask) -> None:
    prompt = build_check_prompt(task)
    assert isinstance(prompt, str)
    assert prompt.strip()
    # The caller-owned business config drives the instructions.
    assert task.check_profile in prompt


def test_build_exec_spec_is_generic_exec_mode(
    task: DocCheckTask, exec_doc_role, work_dir: Path, tmp_path: Path, doc_body_sentinel: str
) -> None:
    spec = build_exec_spec(task, role=exec_doc_role, cwd=work_dir, runs_dir=tmp_path / "runs")

    assert spec.mode == "exec"
    assert spec.role is exec_doc_role
    assert isinstance(spec.prompt, str) and spec.prompt.strip()
    assert isinstance(spec.context, str) and doc_body_sentinel in spec.context
    assert str(spec.cwd) == str(work_dir)
    assert str(spec.runs_dir) == str(tmp_path / "runs")
    _assert_generic_only(spec)


def test_build_exec_spec_dry_run_uses_exec_dry_run_mode(task: DocCheckTask, exec_doc_role, work_dir: Path) -> None:
    spec = build_exec_spec(task, role=exec_doc_role, cwd=work_dir, dry_run=True)
    assert spec.mode == "exec_dry_run"
    _assert_generic_only(spec)


def test_build_session_create_spec(task: DocCheckTask, persistent_doc_role, work_dir: Path) -> None:
    spec = build_session_create_spec(
        task, role=persistent_doc_role, session_id="[REDACTED]", cwd=work_dir
    )
    assert spec.mode == "session_create"
    assert spec.role is persistent_doc_role
    assert spec.session_id == "[REDACTED]"
    _assert_generic_only(spec)


def test_build_session_send_spec_requires_prompt(task: DocCheckTask, persistent_doc_role, work_dir: Path) -> None:
    spec = build_session_send_spec(
        task, role=persistent_doc_role, session_id="[REDACTED]", prompt="re-check section 2", cwd=work_dir
    )
    assert spec.mode == "session_send"
    assert spec.session_id == "[REDACTED]"
    assert isinstance(spec.prompt, str) and spec.prompt.strip()
    _assert_generic_only(spec)


def test_build_session_status_spec(task: DocCheckTask, persistent_doc_role, work_dir: Path) -> None:
    spec = build_session_status_spec(
        task, role=persistent_doc_role, session_id="[REDACTED]", cwd=work_dir
    )
    assert spec.mode == "session_status"
    assert spec.session_id == "[REDACTED]"
    _assert_generic_only(spec)


def test_build_session_close_spec(task: DocCheckTask, persistent_doc_role, work_dir: Path) -> None:
    spec = build_session_close_spec(
        task, role=persistent_doc_role, session_id="[REDACTED]", cwd=work_dir
    )
    assert spec.mode == "session_close"
    assert spec.session_id == "[REDACTED]"
    _assert_generic_only(spec)


def test_doc_check_task_is_frozen(doc_task_kwargs: dict[str, Any]) -> None:
    task = DocCheckTask(**doc_task_kwargs)
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.task_id = "mutated"  # type: ignore[misc]

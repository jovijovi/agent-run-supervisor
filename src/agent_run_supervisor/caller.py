"""Generic local caller boundary for library integrations.

The caller boundary is intentionally thin: it validates a local invocation
specification, loads the role when supplied by file, combines caller-owned
context with the prompt, and delegates execution to the existing supervisor
surfaces. It does not parse acpx streams, interpret business success, deliver
messages, or carry platform identifiers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_run_supervisor.role import AgentRoleSpec, load_role
from agent_run_supervisor.runner import DryRunResult, RunOutcome, SupervisorRunner
from agent_run_supervisor.session_runtime import (
    SessionCloseOutcome,
    SessionCreateOutcome,
    SessionRuntime,
    SessionStatusOutcome,
    SessionTurnOutcome,
)

DEFAULT_SESSIONS_DIR_NAME = Path(".agent-run-supervisor") / "sessions"

EXEC_MODE = "exec"
EXEC_DRY_RUN_MODE = "exec_dry_run"
SESSION_CREATE_MODE = "session_create"
SESSION_SEND_MODE = "session_send"
SESSION_STATUS_MODE = "session_status"
SESSION_CLOSE_MODE = "session_close"
SESSION_ABORT_MODE = "session_abort"
SESSION_LIST_MODE = "session_list"

SUPPORTED_MODES = {
    EXEC_MODE,
    EXEC_DRY_RUN_MODE,
    SESSION_CREATE_MODE,
    SESSION_SEND_MODE,
    SESSION_STATUS_MODE,
    SESSION_CLOSE_MODE,
    SESSION_ABORT_MODE,
    SESSION_LIST_MODE,
}
PROMPT_REQUIRED_MODES = {EXEC_MODE, EXEC_DRY_RUN_MODE, SESSION_SEND_MODE}
SESSION_ID_REQUIRED_MODES = {
    SESSION_CREATE_MODE,
    SESSION_SEND_MODE,
    SESSION_STATUS_MODE,
    SESSION_CLOSE_MODE,
    SESSION_ABORT_MODE,
}
SESSION_MODES = SESSION_ID_REQUIRED_MODES | {SESSION_LIST_MODE}


class CallerInvocationError(ValueError):
    """Raised when a local caller invocation is invalid before delegation."""


@dataclass(frozen=True)
class CallerInvocationSpec:
    mode: str
    role: AgentRoleSpec | None = None
    role_file: str | Path | None = None
    prompt: str | None = None
    context: str | None = None
    cwd: str | Path | None = None
    runs_dir: str | Path | None = None
    sessions_dir: str | Path | None = None
    session_id: str | None = None
    session_name: str | None = None


@dataclass(frozen=True)
class CallerResult:
    mode: str
    supervisor_status: str | None
    result: dict[str, Any]
    artifact_dir: str | None
    run_dir: str | None
    session_dir: str | None
    business_verdict: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "supervisor_status": self.supervisor_status,
            "result": self.result,
            "artifact_dir": self.artifact_dir,
            "run_dir": self.run_dir,
            "session_dir": self.session_dir,
            "business_verdict": self.business_verdict,
        }


def invoke_caller(
    spec: CallerInvocationSpec,
    *,
    runner: SupervisorRunner | None = None,
    session_runtime: SessionRuntime | None = None,
) -> CallerResult:
    """Invoke the local supervisor through a generic caller-owned spec.

    ``business_verdict`` stays ``None`` both in the wrapped supervisor result
    and in the caller wrapper. Callers remain responsible for business verdicts,
    rendering, delivery, and any platform integration outside this library.
    """
    _validate_spec(spec)
    role = _resolve_role(spec)
    cwd = _optional_str(spec.cwd)

    if spec.mode in {EXEC_MODE, EXEC_DRY_RUN_MODE}:
        active_runner = runner or SupervisorRunner(
            runs_dir=Path(spec.runs_dir) if spec.runs_dir is not None else None
        )
        prompt = _combined_prompt(spec)
        if spec.mode == EXEC_DRY_RUN_MODE:
            dry = active_runner.dry_run(role=role, prompt=prompt, cwd=cwd)
            return _from_run_like(mode=spec.mode, outcome=dry)
        outcome = active_runner.run(role=role, prompt=prompt, cwd=cwd)
        return _from_run_like(mode=spec.mode, outcome=outcome)

    active_runtime = session_runtime or SessionRuntime(sessions_dir=_sessions_dir(spec))

    if spec.mode == SESSION_LIST_MODE:
        listed = active_runtime.list_sessions(role=role)
        sessions_dir = _sessions_dir(spec)
        return CallerResult(
            mode=spec.mode,
            supervisor_status=None,
            result=dict(listed.result),
            artifact_dir=str(sessions_dir),
            run_dir=None,
            session_dir=None,
            business_verdict=None,
        )

    session_id = _required_session_id(spec)

    if spec.mode == SESSION_CREATE_MODE:
        created = active_runtime.create_session(
            role=role,
            session_id=session_id,
            session_name=spec.session_name,
            cwd=cwd,
        )
        return _from_session_create(spec.mode, created)
    if spec.mode == SESSION_SEND_MODE:
        sent = active_runtime.send(
            role=role,
            session_id=session_id,
            prompt=_combined_prompt(spec),
            cwd=cwd,
        )
        return _from_session_turn(
            spec.mode,
            sent,
            session_dir=_session_dir(active_runtime, spec, session_id),
        )
    if spec.mode == SESSION_STATUS_MODE:
        status = active_runtime.status(role=role, session_id=session_id, cwd=cwd)
        return _from_session_management(
            spec.mode,
            status.result,
            session_dir=_session_dir(active_runtime, spec, session_id),
        )
    if spec.mode == SESSION_CLOSE_MODE:
        closed = active_runtime.close(role=role, session_id=session_id, cwd=cwd)
        return _from_session_close(spec.mode, closed, _session_dir(active_runtime, spec, session_id))
    if spec.mode == SESSION_ABORT_MODE:
        aborted = active_runtime.abort(role=role, session_id=session_id, cwd=cwd)
        return _from_session_management(
            spec.mode,
            aborted.result,
            session_dir=_session_dir(active_runtime, spec, session_id),
        )

    raise CallerInvocationError(f"unsupported mode {spec.mode!r}")


def _validate_spec(spec: CallerInvocationSpec) -> None:
    if spec.mode not in SUPPORTED_MODES:
        raise CallerInvocationError(f"unsupported mode {spec.mode!r}")
    role_source_count = int(spec.role is not None) + int(spec.role_file is not None)
    if role_source_count != 1:
        raise CallerInvocationError("exactly one role source is required: role or role_file")
    if spec.mode in PROMPT_REQUIRED_MODES and not _has_text(spec.prompt):
        raise CallerInvocationError(f"mode {spec.mode!r} requires a non-empty prompt")
    if spec.mode in SESSION_ID_REQUIRED_MODES and not _has_text(spec.session_id):
        raise CallerInvocationError(f"mode {spec.mode!r} requires a non-empty session_id")
    if spec.session_name is not None:
        if spec.mode != SESSION_CREATE_MODE:
            raise CallerInvocationError("session_name is only accepted for session_create")
        if not _has_text(spec.session_name):
            raise CallerInvocationError("session_name must be a non-empty string")


def _resolve_role(spec: CallerInvocationSpec) -> AgentRoleSpec:
    if spec.role is not None:
        return spec.role
    if spec.role_file is None:
        # Unreachable after validation; keeps type checkers honest.
        raise CallerInvocationError("missing role source")
    raw = json.loads(Path(spec.role_file).read_text(encoding="utf-8"))
    return load_role(raw)


def _combined_prompt(spec: CallerInvocationSpec) -> str:
    prompt = spec.prompt or ""
    context = spec.context or ""
    if context:
        return f"{context}\n\n{prompt}"
    return prompt


def _from_run_like(
    *,
    mode: str,
    outcome: DryRunResult | RunOutcome,
) -> CallerResult:
    result = dict(outcome.result)
    run_dir = str(outcome.run_dir)
    return CallerResult(
        mode=mode,
        supervisor_status=_status_from(result),
        result=result,
        artifact_dir=run_dir,
        run_dir=run_dir,
        session_dir=None,
        business_verdict=None,
    )


def _from_session_create(mode: str, outcome: SessionCreateOutcome) -> CallerResult:
    result = dict(outcome.result)
    session_dir = str(outcome.session_dir)
    return CallerResult(
        mode=mode,
        supervisor_status=_status_from(result),
        result=result,
        artifact_dir=session_dir,
        run_dir=None,
        session_dir=session_dir,
        business_verdict=None,
    )


def _from_session_turn(
    mode: str,
    outcome: SessionTurnOutcome,
    *,
    session_dir: Path,
) -> CallerResult:
    result = dict(outcome.result)
    run_dir = result.get("run_dir")
    run_dir_str = run_dir if isinstance(run_dir, str) else str(outcome.turn_dir)
    return CallerResult(
        mode=mode,
        supervisor_status=_status_from(result),
        result=result,
        artifact_dir=run_dir_str,
        run_dir=run_dir_str,
        session_dir=str(session_dir),
        business_verdict=None,
    )


def _from_session_management(
    mode: str,
    result: dict[str, Any],
    *,
    session_dir: Path,
) -> CallerResult:
    result_copy = dict(result)
    session_dir_str = str(session_dir)
    return CallerResult(
        mode=mode,
        supervisor_status=_status_from(result_copy),
        result=result_copy,
        artifact_dir=session_dir_str,
        run_dir=None,
        session_dir=session_dir_str,
        business_verdict=None,
    )


def _from_session_close(
    mode: str,
    outcome: SessionCloseOutcome,
    session_dir: Path,
) -> CallerResult:
    return _from_session_management(mode, outcome.result, session_dir=session_dir)


def _status_from(result: dict[str, Any]) -> str | None:
    status = result.get("status")
    return status if isinstance(status, str) else None


def _sessions_dir(spec: CallerInvocationSpec) -> Path:
    if spec.sessions_dir is not None:
        return Path(spec.sessions_dir)
    return Path.cwd() / DEFAULT_SESSIONS_DIR_NAME


def _session_dir(
    runtime: SessionRuntime,
    spec: CallerInvocationSpec,
    session_id: str,
) -> Path:
    base = Path(spec.sessions_dir) if spec.sessions_dir is not None else runtime.sessions_dir
    return base / session_id


def _required_session_id(spec: CallerInvocationSpec) -> str:
    if spec.session_id is None:
        raise CallerInvocationError("session_id is required")
    return spec.session_id


def _optional_str(value: str | Path | None) -> str | None:
    return str(value) if value is not None else None


def _has_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value)

"""S1c persistent-session runtime MVP.

This module connects S1a's fixture-proven ``acpx@0.10.0`` persistent-session
command contract to S1b's local :class:`SessionStore`/lease-lock foundation. It
implements the first behaviour-bearing slice of the persistent-session runtime:

- ``create_session`` — validate a persistent role + workspace, run the
  fixture-shaped ``sessions new`` management command through an executor, parse
  the management JSON into a safe summary, and create the local session record
  bound to the role/workspace/policy/acpx/adapter identity.
- ``send`` — re-open + re-validate the binding, acquire the lease lock, run a
  single ``prompt -s`` turn, persist redacted turn artifacts under
  ``sessions/<session_id>/turns/<turn_id>/``, classify the result, and release
  the lease on success *and* failure.
- ``status`` — re-validate the binding and run a read-only ``status -s``
  management query, returning a safe summary (no lease taken).

It deliberately does **not** implement close/abort semantics, crash/interruption
recovery, or retention/cleanup — those remain later S1 slices. Everything is
local filesystem state; there is no Sachima/Hermes/Gateway/IM integration, no
public ingress, no automatic replies, and no agent-to-agent routing.

The runtime owns *supervisor* status only; ``business_verdict`` stays ``null``.
"""
from __future__ import annotations

import datetime as _dt
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.event_store import (
    RunHandle,
    atomic_write_json,
    secure_mkdir,
)
from agent_run_supervisor.exit_classifier import (
    AgentRunStatus,
    ClassifierInput,
    classify_exit,
)
from agent_run_supervisor.parser import (
    ManagementParseError,
    ParseResult,
    parse_acpx_stdout_bytes,
    summarize_management_json,
)
from agent_run_supervisor.policy import (
    compile_session_create_command,
    compile_session_prompt_command,
    compile_session_status_command,
    ensure_persistent_strategy,
)
from agent_run_supervisor.redaction import (
    RedactionReport,
    redact_mapping,
    redact_text,
)
from agent_run_supervisor.result import build_result_payload
from agent_run_supervisor.role import (
    DEFAULT_SESSION_LEASE_SECONDS,
    AgentRoleSpec,
)
from agent_run_supervisor.runner import (
    SubprocessExecutor,
    SubprocessOutcome,
    execute_subprocess,
)
from agent_run_supervisor.session import (
    SessionExistsError,
    SessionLockError,
    SessionNotFoundError,
    SessionRecord,
    SessionStore,
)
from agent_run_supervisor.workspace import (
    WorkspaceValidationResult,
    validate_effective_cwd,
)

DEFAULT_OWNER = "agent-run-supervisor"
DEFAULT_WATCHDOG_GRACE_MS = 1_000
TURNS_DIR = "turns"
MANAGEMENT_DIR = "management"


class SessionRuntimeError(RuntimeError):
    """Raised when an acpx management command fails or returns unusable output.

    Fails closed: the local session record is never created/updated when the
    management command exits non-zero, returns unparseable output, or reports an
    error envelope.
    """


@dataclass
class SessionCreateOutcome:
    session_id: str
    session_dir: Path
    record: SessionRecord
    summary: dict[str, Any]
    result: dict[str, Any]


@dataclass
class SessionTurnOutcome:
    session_id: str
    turn_id: str
    turn_dir: Path
    status: AgentRunStatus
    result: dict[str, Any]


@dataclass
class SessionStatusOutcome:
    session_id: str
    ok: bool
    summary: dict[str, Any]
    result: dict[str, Any]


class SessionRuntime:
    """Local persistent-session runtime over :class:`SessionStore`."""

    def __init__(
        self,
        *,
        sessions_dir: Path,
        executor: SubprocessExecutor | None = None,
        owner: str = DEFAULT_OWNER,
        watchdog_grace_ms: int = DEFAULT_WATCHDOG_GRACE_MS,
    ) -> None:
        self.sessions_dir = Path(sessions_dir)
        self.store = SessionStore(base_dir=self.sessions_dir)
        self.executor = executor or execute_subprocess
        self.owner = owner
        self.watchdog_grace_ms = watchdog_grace_ms

    # -- create -----------------------------------------------------------

    def create_session(
        self,
        *,
        role: AgentRoleSpec,
        session_id: str,
        session_name: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        now: _dt.datetime | None = None,
    ) -> SessionCreateOutcome:
        ensure_persistent_strategy(role)
        workspace = validate_effective_cwd(role, cwd)
        # Fail closed before spawning acpx if the local record already exists
        # (also validates the session id is a safe path component).
        self._refuse_existing(session_id)

        effective_name = session_name or session_id
        argv = compile_session_create_command(
            role, cwd=str(workspace.effective_cwd), session_name=effective_name
        )
        outcome = self._run(argv=argv, role=role, workspace=workspace, env=env)
        summary = self._require_management_summary(
            outcome,
            op="create",
            expected_kinds={"session_ensured"},
            require_acpx_session_id=True,
        )

        acpx_session_id = summary.get("acpx_session_id")
        record = self.store.create_session(
            session_id=session_id,
            role=role,
            workspace_result=workspace,
            acpx_session_id=acpx_session_id if isinstance(acpx_session_id, str) else None,
            session_name=effective_name,
            now=now,
        )
        self._write_management_artifact(session_id, "create.json", summary)

        result = {
            "session_id": session_id,
            "acpx_session_id": record.acpx_session_id,
            "session_name": record.session_name,
            "state": record.state,
            "kind": summary.get("kind"),
            "created": summary.get("created"),
            "session_dir": str(self.sessions_dir / session_id),
            "business_verdict": None,
        }
        return SessionCreateOutcome(
            session_id=session_id,
            session_dir=self.sessions_dir / session_id,
            record=record,
            summary=summary,
            result=result,
        )

    # -- send -------------------------------------------------------------

    def send(
        self,
        *,
        role: AgentRoleSpec,
        session_id: str,
        prompt: str,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        now: _dt.datetime | None = None,
    ) -> SessionTurnOutcome:
        ensure_persistent_strategy(role)
        record = self.store.open_session(session_id)
        workspace = validate_effective_cwd(role, cwd)
        # Refuse cross-role/workspace/policy/acpx/adapter drift BEFORE taking a
        # lease, spawning acpx, or writing any turn artifact.
        self.store.validate_binding(record, role=role, workspace_result=workspace)

        prompt_session_name = record.session_name or session_id
        lease_seconds = role.session.lease_seconds or DEFAULT_SESSION_LEASE_SECONDS
        lock = self.store.acquire_lock(
            session_id, owner=self.owner, now=now, lease_seconds=lease_seconds
        )
        try:
            return self._run_turn(
                role=role,
                session_id=session_id,
                workspace=workspace,
                prompt=prompt,
                prompt_session_name=prompt_session_name,
                env=env,
            )
        finally:
            self._release_quietly(session_id, lock.token)

    # -- status -----------------------------------------------------------

    def status(
        self,
        *,
        role: AgentRoleSpec,
        session_id: str,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> SessionStatusOutcome:
        ensure_persistent_strategy(role)
        record = self.store.open_session(session_id)
        workspace = validate_effective_cwd(role, cwd)
        self.store.validate_binding(record, role=role, workspace_result=workspace)

        show_name = record.session_name or session_id
        argv = compile_session_status_command(
            role, cwd=str(workspace.effective_cwd), session_name=show_name
        )
        outcome = self._run(argv=argv, role=role, workspace=workspace, env=env)
        summary = self._require_management_summary(
            outcome,
            op="status",
            expected_kinds={"status_snapshot"},
            require_acpx_session_id=False,
        )
        self._write_management_artifact(session_id, "status.json", summary)

        ok = summary.get("status") == "alive"
        result = {
            "session_id": session_id,
            "ok": ok,
            "acpx_exit_code": outcome.exit_code,
            "summary": summary,
            "business_verdict": None,
        }
        return SessionStatusOutcome(
            session_id=session_id, ok=ok, summary=summary, result=result
        )

    # -- internals --------------------------------------------------------

    def _refuse_existing(self, session_id: str) -> None:
        try:
            self.store.open_session(session_id)
        except SessionNotFoundError:
            return
        raise SessionExistsError(f"session {session_id!r} already exists")

    def _run(
        self,
        *,
        argv: list[str],
        role: AgentRoleSpec,
        workspace: WorkspaceValidationResult,
        env: Mapping[str, str] | None,
    ) -> SubprocessOutcome:
        env_map = dict(env) if env is not None else dict(os.environ)
        return self.executor(
            argv=argv,
            cwd=workspace.effective_cwd,
            env=env_map,
            timeout_seconds=_watchdog_timeout(
                role.limits.timeout_seconds, self.watchdog_grace_ms
            ),
            grace_ms=self.watchdog_grace_ms,
        )

    def _require_management_summary(
        self,
        outcome: SubprocessOutcome,
        *,
        op: str,
        expected_kinds: set[str],
        require_acpx_session_id: bool,
    ) -> dict[str, Any]:
        if outcome.exit_code != 0:
            raise SessionRuntimeError(
                f"session {op} failed: acpx exited {outcome.exit_code}",
            )
        try:
            summary = summarize_management_json(outcome.stdout)
        except ManagementParseError as exc:
            raise SessionRuntimeError(
                f"session {op} failed: unparseable management output: {exc}",
            ) from exc
        if summary.get("kind") == "error":
            raise SessionRuntimeError(
                f"session {op} failed: acpx error {summary.get('acpx_code')!r}",
            )
        kind = summary.get("kind")
        if kind not in expected_kinds:
            raise SessionRuntimeError(
                f"session {op} failed: unexpected management kind {kind!r}",
            )
        acpx_session_id = summary.get("acpx_session_id")
        if require_acpx_session_id and not isinstance(acpx_session_id, str):
            raise SessionRuntimeError(
                f"session {op} failed: missing acpx_session_id in management output",
            )
        return summary

    def _run_turn(
        self,
        *,
        role: AgentRoleSpec,
        session_id: str,
        workspace: WorkspaceValidationResult,
        prompt: str,
        prompt_session_name: str,
        env: Mapping[str, str] | None,
    ) -> SessionTurnOutcome:
        turn_id = _generate_turn_id()
        secure_mkdir(self.sessions_dir / session_id / TURNS_DIR)
        turn_dir = self.sessions_dir / session_id / TURNS_DIR / turn_id
        secure_mkdir(turn_dir)
        handle = RunHandle(run_id=turn_id, run_dir=turn_dir)
        report = RedactionReport()

        redacted_prompt, prompt_report = redact_text(prompt, location="prompt")
        report.merge(prompt_report)
        handle.write_text("prompt.txt", redacted_prompt)

        argv = compile_session_prompt_command(
            role,
            cwd=str(workspace.effective_cwd),
            session_name=prompt_session_name,
            prompt=prompt,
        )
        outcome = self._run(argv=argv, role=role, workspace=workspace, env=env)

        handle.write_text("stderr.log", _decode_redacted(outcome.stderr, report, "stderr"))
        stdout_text = outcome.stdout.decode("utf-8", errors="replace")
        redacted_stdout, stdout_report = redact_text(stdout_text, location="acpx_stdout")
        report.merge(stdout_report)
        handle.write_text("acpx-stdout.ndjson", redacted_stdout)

        parse_result = parse_acpx_stdout_bytes(
            outcome.stdout, max_output_bytes=role.limits.max_output_bytes
        )
        for event in parse_result.events:
            handle.append_ndjson("normalized-events.jsonl", event)

        acpx_code, origin = _acpx_error_metadata(parse_result)
        classification = classify_exit(
            ClassifierInput(
                exit_code=outcome.exit_code,
                signal=outcome.signal,
                acpx_code=acpx_code,
                origin=origin,
                protocol_error=parse_result.protocol_error,
                supervisor_killed=outcome.supervisor_killed,
                supervisor_timed_out=outcome.supervisor_timed_out,
            )
        )
        redacted_final, final_report = redact_text(
            parse_result.final_message, location="final_message"
        )
        report.merge(final_report)

        result = build_result_payload(
            run_id=turn_id,
            status=classification.status,
            origin=classification.origin,
            detail_code=classification.detail_code,
            retryable=classification.retryable,
            exit_code=outcome.exit_code,
            signal=outcome.signal,
            stop_reason=_stop_reason(parse_result),
            usage=parse_result.usage,
            final_message=redacted_final,
            truncated=parse_result.truncated,
            truncate_reason=parse_result.truncate_reason,
            run_dir=turn_dir,
        )
        result["session_id"] = session_id
        result["turn_id"] = turn_id
        result.update(_kill_metadata(outcome))
        handle.write_json("result.json", result)
        handle.write_json("redaction-report.json", _redaction_payload(report))

        return SessionTurnOutcome(
            session_id=session_id,
            turn_id=turn_id,
            turn_dir=turn_dir,
            status=classification.status,
            result=result,
        )

    def _write_management_artifact(
        self, session_id: str, name: str, summary: dict[str, Any]
    ) -> None:
        mgmt_dir = self.sessions_dir / session_id / MANAGEMENT_DIR
        secure_mkdir(mgmt_dir)
        redacted, _ = redact_mapping(summary)
        atomic_write_json(mgmt_dir / name, redacted)

    def _release_quietly(self, session_id: str, token: str) -> None:
        # Best-effort release in a finally block: a release failure must never
        # mask the original turn error.
        try:
            self.store.release_lock(session_id, token)
        except SessionLockError:
            pass


def _watchdog_timeout(acpx_timeout_seconds: int, grace_ms: int) -> int:
    grace_seconds = max(1, (max(grace_ms, 0) + 999) // 1000)
    return acpx_timeout_seconds + grace_seconds


def _generate_turn_id() -> str:
    ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"turn_{ts}_{secrets.token_hex(4)}"


def _decode_redacted(data: bytes, report: RedactionReport, location: str) -> str:
    text = data.decode("utf-8", errors="replace")
    redacted, sub_report = redact_text(text, location=location)
    report.merge(sub_report)
    return redacted


def _acpx_error_metadata(parse_result: ParseResult) -> tuple[str | None, str | None]:
    if not parse_result.acpx_error:
        return None, None
    data = parse_result.acpx_error.get("data")
    if isinstance(data, dict):
        return data.get("acpxCode"), data.get("origin")
    return None, None


def _stop_reason(parse_result: ParseResult) -> str | None:
    for event in reversed(parse_result.events):
        if event.get("type") == "run_completed":
            stop_reason = event.get("stop_reason")
            if isinstance(stop_reason, str):
                return stop_reason
    return None


def _kill_metadata(outcome: SubprocessOutcome) -> dict[str, Any]:
    return {
        "kill_reason": outcome.kill_reason,
        "kill_signal": outcome.kill_signal,
        "grace_ms": outcome.grace_ms,
        "process_group_used": outcome.process_group_used,
        "stdout_closed": outcome.stdout_closed,
        "stderr_closed": outcome.stderr_closed,
    }


def _redaction_payload(report: RedactionReport) -> dict[str, Any]:
    return {
        "matches": [
            {"pattern": match.pattern_name, "note": match.note}
            for match in report.matches
        ]
    }

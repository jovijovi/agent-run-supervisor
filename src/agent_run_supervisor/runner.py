from __future__ import annotations

import datetime as _dt
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.event_store import EventStore, RunHandle
from agent_run_supervisor.exit_classifier import (
    AgentRunStatus,
    ClassifierInput,
    classify_exit,
)
from agent_run_supervisor.parser import ParseResult, parse_acpx_stdout_bytes
from agent_run_supervisor.policy import (
    compile_command,
    compile_permission_policy,
    policy_hash,
)
from agent_run_supervisor.redaction import (
    RedactionReport,
    redact_argv,
    redact_env,
    redact_mapping,
    redact_text,
)
from agent_run_supervisor.result import build_result_payload, RunOutcome
from agent_run_supervisor.role import AgentRoleSpec, role_hash
from agent_run_supervisor.workspace import (
    ALLOWED_ROOTS_DISCLAIMER,
    WorkspaceValidationResult,
    validate_effective_cwd,
)

DEFAULT_RUNS_DIR_NAME = Path(".agent-run-supervisor") / "runs"


@dataclass
class SubprocessOutcome:
    exit_code: int
    signal: int | None
    stdout: bytes
    stderr: bytes
    supervisor_killed: bool = False
    supervisor_timed_out: bool = False


@dataclass
class DryRunResult:
    run_id: str
    run_dir: Path
    result: dict[str, Any]


@dataclass
class _ArtifactBundle:
    handle: RunHandle
    argv: list[str]
    policy: dict[str, Any]
    redaction_report: RedactionReport = field(default_factory=RedactionReport)


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _generate_run_id() -> str:
    ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{ts}_{secrets.token_hex(4)}"


class SupervisorRunner:
    def __init__(self, runs_dir: Path | None = None) -> None:
        if runs_dir is None:
            runs_dir = Path.cwd() / DEFAULT_RUNS_DIR_NAME
        self.store = EventStore(base_dir=Path(runs_dir))

    def _prepare_artifacts(
        self,
        *,
        role: AgentRoleSpec,
        prompt: str,
        cwd: str | None,
        env: Mapping[str, str] | None,
        dry_run: bool,
        workspace: WorkspaceValidationResult,
    ) -> _ArtifactBundle:
        run_id = _generate_run_id()
        handle = self.store.create_run(run_id)

        resolved_cwd_str = str(workspace.effective_cwd)
        argv = compile_command(role, cwd=resolved_cwd_str, prompt=prompt)
        policy = compile_permission_policy(role)
        report = RedactionReport()

        redacted_prompt, prompt_report = redact_text(prompt, location="prompt")
        report.merge(prompt_report)
        handle.write_text("prompt.txt", redacted_prompt)

        env_map = dict(env) if env is not None else dict(os.environ)
        redacted_env, env_report = redact_env(env_map)
        report.merge(env_report)
        handle.write_json("env.redacted.json", redacted_env)

        redacted_argv, argv_report = redact_argv(argv)
        report.merge(argv_report)
        handle.write_json("command.argv.json", redacted_argv)

        handle.write_json("generated-policy.json", policy)

        metadata = {
            "schema_version": 1,
            "run_id": run_id,
            "role_id": role.role_id,
            "role_hash": role_hash(role),
            "policy_hash": policy_hash(role),
            "acpx_version": role.runner.acpx_version,
            "adapter_agent": role.runner.adapter_agent,
            "started_at": _utc_now_iso(),
            "cwd": cwd or role.workspace.default_cwd,
            "effective_cwd": resolved_cwd_str,
            "allowed_roots": list(role.workspace.allowed_roots),
            "allowed_roots_security_boundary": False,
            "allowed_roots_disclaimer": ALLOWED_ROOTS_DISCLAIMER,
            "dry_run": dry_run,
        }
        redacted_metadata, meta_report = redact_mapping(metadata)
        report.merge(meta_report)
        handle.write_json("metadata.json", redacted_metadata)

        return _ArtifactBundle(
            handle=handle,
            argv=argv,
            policy=policy,
            redaction_report=report,
        )

    def _persist_redaction_report(self, bundle: _ArtifactBundle) -> None:
        report_payload = {
            "matches": [
                {"pattern": match.pattern_name, "note": match.note}
                for match in bundle.redaction_report.matches
            ]
        }
        bundle.handle.write_json("redaction-report.json", report_payload)

    def dry_run(
        self,
        *,
        role: AgentRoleSpec,
        prompt: str,
        cwd: str | None,
        env: Mapping[str, str] | None = None,
    ) -> DryRunResult:
        workspace = validate_effective_cwd(role, cwd)
        bundle = self._prepare_artifacts(
            role=role,
            prompt=prompt,
            cwd=cwd,
            env=env,
            dry_run=True,
            workspace=workspace,
        )
        result = build_result_payload(
            run_id=bundle.handle.run_id,
            status=AgentRunStatus.COMPLETED,
            origin="supervisor",
            detail_code="DRY_RUN",
            retryable=False,
            exit_code=None,
            signal=None,
            stop_reason=None,
            usage=None,
            final_message="",
            truncated=False,
            truncate_reason=None,
            run_dir=bundle.handle.run_dir,
            error_code="DRY_RUN",
        )
        result["status"] = "dry_run"
        bundle.handle.write_json("result.json", result)
        self._persist_redaction_report(bundle)
        return DryRunResult(
            run_id=bundle.handle.run_id,
            run_dir=bundle.handle.run_dir,
            result=result,
        )

    def finalize_outcome(
        self,
        *,
        role: AgentRoleSpec,
        prompt: str,
        cwd: str | None,
        subprocess_outcome: SubprocessOutcome,
        env: Mapping[str, str] | None = None,
    ) -> RunOutcome:
        workspace = validate_effective_cwd(role, cwd)
        bundle = self._prepare_artifacts(
            role=role,
            prompt=prompt,
            cwd=cwd,
            env=env,
            dry_run=False,
            workspace=workspace,
        )

        bundle.handle.write_text("stderr.log", _decode_redacted(subprocess_outcome.stderr, bundle.redaction_report, "stderr"))
        _persist_stdout(bundle.handle, subprocess_outcome.stdout)

        parse_result = parse_acpx_stdout_bytes(
            subprocess_outcome.stdout,
            max_output_bytes=role.limits.max_output_bytes,
        )
        _persist_normalized_events(bundle.handle, parse_result)

        acpx_code, origin = _extract_error_metadata(parse_result)

        classifier_input = ClassifierInput(
            exit_code=subprocess_outcome.exit_code,
            signal=subprocess_outcome.signal,
            acpx_code=acpx_code,
            origin=origin,
            protocol_error=parse_result.protocol_error,
            supervisor_killed=subprocess_outcome.supervisor_killed,
            supervisor_timed_out=subprocess_outcome.supervisor_timed_out,
        )
        classification = classify_exit(classifier_input)

        stop_reason = _extract_stop_reason(parse_result)
        usage = parse_result.usage
        redacted_final, final_report = redact_text(parse_result.final_message, location="final_message")
        bundle.redaction_report.merge(final_report)

        result = build_result_payload(
            run_id=bundle.handle.run_id,
            status=classification.status,
            origin=classification.origin,
            detail_code=classification.detail_code,
            retryable=classification.retryable,
            exit_code=subprocess_outcome.exit_code,
            signal=subprocess_outcome.signal,
            stop_reason=stop_reason,
            usage=usage,
            final_message=redacted_final,
            truncated=parse_result.truncated,
            truncate_reason=parse_result.truncate_reason,
            run_dir=bundle.handle.run_dir,
        )
        bundle.handle.write_json("result.json", result)
        self._persist_redaction_report(bundle)
        return RunOutcome(
            run_dir=bundle.handle.run_dir,
            status=classification.status,
            result=result,
        )


def _decode_redacted(data: bytes, report: RedactionReport, location: str) -> str:
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = ""
    redacted, sub_report = redact_text(text, location=location)
    report.merge(sub_report)
    return redacted


def _persist_stdout(handle: RunHandle, stdout: bytes) -> None:
    text = stdout.decode("utf-8", errors="replace")
    redacted, _ = redact_text(text, location="acpx_stdout")
    handle.write_text("acpx-stdout.ndjson", redacted)


def _persist_normalized_events(handle: RunHandle, parse_result: ParseResult) -> None:
    for event in parse_result.events:
        handle.append_ndjson("normalized-events.jsonl", event)


def _extract_error_metadata(parse_result: ParseResult) -> tuple[str | None, str | None]:
    if not parse_result.acpx_error:
        return None, None
    data = parse_result.acpx_error.get("data")
    if isinstance(data, dict):
        return data.get("acpxCode"), data.get("origin")
    return None, None


def _extract_stop_reason(parse_result: ParseResult) -> str | None:
    for event in reversed(parse_result.events):
        if event.get("type") == "run_completed":
            stop_reason = event.get("stop_reason")
            if isinstance(stop_reason, str):
                return stop_reason
    return None

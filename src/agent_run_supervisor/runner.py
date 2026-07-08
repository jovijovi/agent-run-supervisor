from __future__ import annotations

import datetime as _dt
import inspect
import os
import queue
import secrets
import signal as _signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from agent_run_supervisor.event_store import EventStore, RunHandle
from agent_run_supervisor.live_stream import LiveEventSink
from agent_run_supervisor.exit_classifier import (
    AgentRunStatus,
    ClassifierInput,
    classify_exit,
)
from agent_run_supervisor.parser import (
    ParseResult,
    has_observed_effect,
    parse_acpx_stdout_bytes,
)
from agent_run_supervisor.policy import (
    compile_command,
    compile_permission_policy,
    ensure_exec_strategy,
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
DEFAULT_WATCHDOG_GRACE_MS = 1_000


@dataclass
class SubprocessOutcome:
    exit_code: int
    signal: int | None
    stdout: bytes
    stderr: bytes
    supervisor_killed: bool = False
    supervisor_timed_out: bool = False
    kill_reason: str | None = None
    kill_signal: str | None = None
    grace_ms: int | None = None
    process_group_used: bool | None = None
    stdout_closed: bool | None = None
    stderr_closed: bool | None = None
    stdout_sink_failed: bool = False


class SubprocessExecutor(Protocol):
    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
        grace_ms: int,
        on_spawn: Callable[[int], None] | None = None,
        stdout_sink: Callable[[bytes], None] | None = None,
    ) -> SubprocessOutcome: ...


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
    def __init__(
        self,
        runs_dir: Path | None = None,
        *,
        executor: SubprocessExecutor | None = None,
        watchdog_grace_ms: int = DEFAULT_WATCHDOG_GRACE_MS,
    ) -> None:
        if runs_dir is None:
            runs_dir = Path.cwd() / DEFAULT_RUNS_DIR_NAME
        self.store = EventStore(base_dir=Path(runs_dir))
        self.executor = executor or execute_subprocess
        self.watchdog_grace_ms = watchdog_grace_ms

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
        ensure_exec_strategy(role)
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

    def run(
        self,
        *,
        role: AgentRoleSpec,
        prompt: str,
        cwd: str | None,
        env: Mapping[str, str] | None = None,
    ) -> RunOutcome:
        ensure_exec_strategy(role)
        workspace = validate_effective_cwd(role, cwd)
        env_map = dict(env) if env is not None else dict(os.environ)
        bundle = self._prepare_artifacts(
            role=role,
            prompt=prompt,
            cwd=cwd,
            env=env_map,
            dry_run=False,
            workspace=workspace,
        )
        live_sink = LiveEventSink(bundle.handle, bundle.redaction_report, max_output_bytes=role.limits.max_output_bytes)
        subprocess_outcome = execute_with_optional_stdout_sink(
            self.executor,
            argv=bundle.argv,
            cwd=workspace.effective_cwd,
            env=env_map,
            timeout_seconds=_outer_watchdog_timeout_seconds(
                role.limits.timeout_seconds,
                self.watchdog_grace_ms,
            ),
            grace_ms=self.watchdog_grace_ms,
            stdout_sink=live_sink.feed,
        )
        return self._finalize_prepared_outcome(
            bundle=bundle,
            role=role,
            subprocess_outcome=subprocess_outcome,
            live_sink=live_sink,
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
        ensure_exec_strategy(role)
        workspace = validate_effective_cwd(role, cwd)
        bundle = self._prepare_artifacts(
            role=role,
            prompt=prompt,
            cwd=cwd,
            env=env,
            dry_run=False,
            workspace=workspace,
        )
        return self._finalize_prepared_outcome(
            bundle=bundle,
            role=role,
            subprocess_outcome=subprocess_outcome,
        )

    def _finalize_prepared_outcome(
        self,
        *,
        bundle: _ArtifactBundle,
        role: AgentRoleSpec,
        subprocess_outcome: SubprocessOutcome,
        live_sink: LiveEventSink | None = None,
    ) -> RunOutcome:
        bundle.handle.write_text("stderr.log", _decode_redacted(subprocess_outcome.stderr, bundle.redaction_report, "stderr"))

        use_live_sink = (
            live_sink is not None
            and live_sink.started
            and not subprocess_outcome.stdout_sink_failed
            and subprocess_outcome.stdout_closed is not False
        )
        if use_live_sink:
            parse_result = live_sink.finish("running")
        else:
            if live_sink is not None and live_sink.started:
                live_sink.disable()
            _persist_stdout(bundle.handle, subprocess_outcome.stdout, bundle.redaction_report)
            if live_sink is not None and live_sink.started:
                bundle.handle.write_text("normalized-events.jsonl", "")
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
            no_observed_effect=not has_observed_effect(parse_result),
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
            observed_effect=has_observed_effect(parse_result),
        )
        result.update(_kill_metadata_payload(subprocess_outcome))
        if use_live_sink:
            live_sink.write_progress(classification.status.value)
        elif live_sink is not None and live_sink.started:
            _write_progress(bundle.handle, classification.status.value, len(parse_result.events))
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


def _kill_metadata_payload(outcome: SubprocessOutcome) -> dict[str, Any]:
    return {
        "kill_reason": outcome.kill_reason,
        "kill_signal": outcome.kill_signal,
        "grace_ms": outcome.grace_ms,
        "process_group_used": outcome.process_group_used,
        "stdout_closed": outcome.stdout_closed,
        "stderr_closed": outcome.stderr_closed,
    }


def _outer_watchdog_timeout_seconds(acpx_timeout_seconds: int, grace_ms: int) -> int:
    grace_seconds = max(1, (max(grace_ms, 0) + 999) // 1000)
    return acpx_timeout_seconds + grace_seconds


def execute_with_optional_stdout_sink(
    executor: SubprocessExecutor,
    *,
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    grace_ms: int,
    on_spawn: Callable[[int], None] | None = None,
    stdout_sink: Callable[[bytes], None] | None = None,
) -> SubprocessOutcome:
    signature = _executor_signature(executor)
    kwargs: dict[str, Any] = {
        "argv": argv,
        "cwd": cwd,
        "env": env,
        "timeout_seconds": timeout_seconds,
        "grace_ms": grace_ms,
    }
    if on_spawn is not None or _executor_accepts_param(signature, "on_spawn", executor):
        kwargs["on_spawn"] = on_spawn
    if stdout_sink is not None and _executor_accepts_param(signature, "stdout_sink", executor):
        kwargs["stdout_sink"] = stdout_sink
    return executor(**kwargs)


def _executor_signature(executor: SubprocessExecutor) -> inspect.Signature | None:
    try:
        return inspect.signature(executor)
    except (TypeError, ValueError):
        return None


def _executor_accepts_param(
    signature: inspect.Signature | None,
    name: str,
    executor: SubprocessExecutor,
) -> bool:
    if executor is execute_subprocess:
        return True
    if signature is None:
        return False
    return name in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )


def execute_subprocess(
    *,
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    grace_ms: int,
    on_spawn: Callable[[int], None] | None = None,
    stdout_sink: Callable[[bytes], None] | None = None,
) -> SubprocessOutcome:
    start_new_session = os.name == "posix"
    deadline = time.monotonic() + timeout_seconds
    sink_failed = False
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=start_new_session,
        )
    except FileNotFoundError as exc:
        return SubprocessOutcome(
            exit_code=127,
            signal=None,
            stdout=b"",
            stderr=str(exc).encode("utf-8", errors="replace"),
            stdout_closed=True,
            stderr_closed=True,
        )
    except OSError as exc:
        return SubprocessOutcome(
            exit_code=1,
            signal=None,
            stdout=b"",
            stderr=str(exc).encode("utf-8", errors="replace"),
            stdout_closed=True,
            stderr_closed=True,
        )

    if on_spawn is not None:
        try:
            on_spawn(process.pid)
        except Exception as exc:  # fail closed: do not leave an untracked child running
            stdout, stderr, kill_signal, kill_reason, process_group_used = _stop_after_spawn_callback_error(
                process, start_new_session=start_new_session, grace_ms=grace_ms
            )
            return SubprocessOutcome(
                exit_code=1,
                signal=_signal_from_returncode(process.returncode),
                stdout=stdout,
                stderr=(stderr or b"")
                + f"\nspawn callback failed: {exc}".encode("utf-8", errors="replace"),
                supervisor_killed=True,
                supervisor_timed_out=False,
                kill_reason=kill_reason,
                kill_signal=kill_signal,
                grace_ms=grace_ms,
                process_group_used=process_group_used,
                stdout_closed=True,
                stderr_closed=True,
            )

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    sink_queue: queue.SimpleQueue[bytes | None] | None = (
        queue.SimpleQueue() if stdout_sink is not None else None
    )

    def pump_stdout() -> None:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.readline()
            if not chunk:
                break
            stdout_chunks.append(chunk)
            if sink_queue is not None:
                sink_queue.put(chunk)
        if sink_queue is not None:
            sink_queue.put(None)

    def pump_stdout_sink() -> None:
        nonlocal sink_failed
        assert stdout_sink is not None
        assert sink_queue is not None
        while True:
            chunk = sink_queue.get()
            if chunk is None:
                break
            if sink_failed:
                continue
            try:
                stdout_sink(chunk)
            except Exception:
                # Keep draining so the child cannot block on a full pipe. The
                # complete stdout buffer is still returned for final fallback.
                sink_failed = True

    def pump_stderr() -> None:
        assert process.stderr is not None
        while True:
            chunk = process.stderr.readline()
            if not chunk:
                break
            stderr_chunks.append(chunk)

    stdout_thread = threading.Thread(target=pump_stdout, daemon=True)
    stderr_thread = threading.Thread(target=pump_stderr, daemon=True)
    sink_thread = (
        threading.Thread(target=pump_stdout_sink, daemon=True)
        if stdout_sink is not None
        else None
    )
    if sink_thread is not None:
        sink_thread.start()
    stdout_thread.start()
    stderr_thread.start()

    try:
        process.wait(timeout=timeout_seconds)
        remaining = max(deadline - time.monotonic(), 0)
        if _join_output_threads(stdout_thread, stderr_thread, sink_thread, remaining):
            return SubprocessOutcome(
                exit_code=_normalize_returncode(process.returncode),
                signal=_signal_from_returncode(process.returncode),
                stdout=b"".join(stdout_chunks),
                stderr=b"".join(stderr_chunks),
                stdout_closed=True,
                stderr_closed=True,
                stdout_sink_failed=sink_failed,
            )

        if not stdout_thread.is_alive() and not stderr_thread.is_alive():
            sink_still_running = False
            if sink_thread is not None and sink_thread.is_alive():
                sink_thread.join(max(grace_ms, 0) / 1000)
                sink_still_running = sink_thread.is_alive()
                if sink_still_running:
                    _disable_stdout_sink(stdout_sink)
                    if sink_queue is not None:
                        sink_queue.put(None)
            return SubprocessOutcome(
                exit_code=_normalize_returncode(process.returncode),
                signal=_signal_from_returncode(process.returncode),
                stdout=b"".join(stdout_chunks),
                stderr=b"".join(stderr_chunks),
                stdout_closed=True,
                stderr_closed=True,
                stdout_sink_failed=sink_failed or sink_still_running,
            )

        kill_signal = "SIGTERM"
        kill_reason = "watchdog_timeout_pipe_drain"
        process_group_used = start_new_session and os.name == "posix"
        _terminate_process(process, use_group=process_group_used)
        if not _join_output_threads(stdout_thread, stderr_thread, sink_thread, max(grace_ms, 0) / 1000):
            kill_signal = "SIGKILL"
            kill_reason = "watchdog_timeout_pipe_drain_force_kill"
            _kill_process(process, use_group=process_group_used)
            _join_output_threads(stdout_thread, stderr_thread, sink_thread, max(grace_ms, 0) / 1000)
        _disable_stdout_sink(stdout_sink)
        if sink_queue is not None:
            sink_queue.put(None)
        return SubprocessOutcome(
            exit_code=3,
            signal=_signal_from_returncode(process.returncode),
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            supervisor_killed=True,
            supervisor_timed_out=True,
            kill_reason=kill_reason,
            kill_signal=kill_signal,
            grace_ms=grace_ms,
            process_group_used=process_group_used,
            stdout_closed=not stdout_thread.is_alive(),
            stderr_closed=not stderr_thread.is_alive(),
            stdout_sink_failed=True,
        )
    except subprocess.TimeoutExpired:
        kill_signal = "SIGTERM"
        kill_reason = "watchdog_timeout"
        process_group_used = False
        _terminate_process(process, use_group=start_new_session)
        process_group_used = start_new_session and os.name == "posix"
        try:
            process.wait(timeout=max(grace_ms, 0) / 1000)
        except subprocess.TimeoutExpired:
            kill_signal = "SIGKILL"
            kill_reason = "watchdog_timeout_force_kill"
            _kill_process(process, use_group=process_group_used)
            process.wait()
        if not _join_output_threads(stdout_thread, stderr_thread, sink_thread, max(grace_ms, 0) / 1000):
            kill_signal = "SIGKILL"
            kill_reason = "watchdog_timeout_force_kill"
            _kill_process(process, use_group=process_group_used)
            _join_output_threads(stdout_thread, stderr_thread, sink_thread, max(grace_ms, 0) / 1000)
        _disable_stdout_sink(stdout_sink)
        if sink_queue is not None:
            sink_queue.put(None)
        sink_thread_alive = sink_thread is not None and sink_thread.is_alive()
        return SubprocessOutcome(
            exit_code=3,
            signal=_signal_from_returncode(process.returncode),
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            supervisor_killed=True,
            supervisor_timed_out=True,
            kill_reason=kill_reason,
            kill_signal=kill_signal,
            grace_ms=grace_ms,
            process_group_used=process_group_used,
            stdout_closed=not stdout_thread.is_alive(),
            stderr_closed=not stderr_thread.is_alive(),
            stdout_sink_failed=sink_failed or stdout_thread.is_alive() or sink_thread_alive,
        )


def _disable_stdout_sink(stdout_sink: Callable[[bytes], None] | None) -> None:
    if stdout_sink is None:
        return
    owner = getattr(stdout_sink, "__self__", None)
    disable = getattr(owner, "disable", None)
    if callable(disable):
        try:
            disable()
        except Exception:
            pass


def _join_output_threads(
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
    sink_thread: threading.Thread | None,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(timeout_seconds, 0)
    for thread in (stdout_thread, stderr_thread, sink_thread):
        if thread is None:
            continue
        remaining = max(deadline - time.monotonic(), 0)
        thread.join(remaining)
    return (
        not stdout_thread.is_alive()
        and not stderr_thread.is_alive()
        and (sink_thread is None or not sink_thread.is_alive())
    )


def _stop_after_spawn_callback_error(
    process: subprocess.Popen[bytes], *, start_new_session: bool, grace_ms: int
) -> tuple[bytes, bytes, str, str, bool]:
    stdout = b""
    stderr = b""
    kill_signal = "SIGTERM"
    kill_reason = "spawn_callback_failed"
    process_group_used = start_new_session and os.name == "posix"
    _terminate_process(process, use_group=process_group_used)
    try:
        more_stdout, more_stderr = process.communicate(timeout=max(grace_ms, 0) / 1000)
        stdout += more_stdout or b""
        stderr += more_stderr or b""
    except subprocess.TimeoutExpired:
        kill_signal = "SIGKILL"
        kill_reason = "spawn_callback_failed_force_kill"
        _kill_process(process, use_group=process_group_used)
        more_stdout, more_stderr = process.communicate()
        stdout += more_stdout or b""
        stderr += more_stderr or b""
    return stdout, stderr, kill_signal, kill_reason, process_group_used


def _terminate_process(process: subprocess.Popen[bytes], *, use_group: bool) -> None:
    if use_group and os.name == "posix":
        try:
            os.killpg(process.pid, _signal.SIGTERM)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    process.terminate()


def _kill_process(process: subprocess.Popen[bytes], *, use_group: bool) -> None:
    if use_group and os.name == "posix":
        try:
            os.killpg(process.pid, _signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    process.kill()



def _normalize_returncode(returncode: int | None) -> int:
    if returncode is None:
        return 1
    if returncode < 0:
        return 128 + abs(returncode)
    return returncode


def _signal_from_returncode(returncode: int | None) -> int | None:
    if returncode is not None and returncode < 0:
        return abs(returncode)
    return None


def _write_progress(handle: RunHandle, state: str, event_count: int) -> None:
    handle.write_json(
        "progress.json",
        {
            "schema_version": 1,
            "state": state,
            "last_seq": event_count,
            "event_count": event_count,
            "updated_at": _utc_now_iso(),
        },
    )


def _persist_stdout(handle: RunHandle, stdout: bytes, report: RedactionReport) -> None:
    text = stdout.decode("utf-8", errors="replace")
    redacted, stdout_report = redact_text(text, location="acpx_stdout")
    report.merge(stdout_report)
    handle.write_text("acpx-stdout.ndjson", redacted)


def _persist_normalized_events(handle: RunHandle, parse_result: ParseResult) -> None:
    for seq, event in enumerate(parse_result.events, start=1):
        payload = dict(event)
        payload["seq"] = seq
        handle.append_ndjson("normalized-events.jsonl", payload)


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

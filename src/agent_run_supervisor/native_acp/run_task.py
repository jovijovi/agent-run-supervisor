"""RunTask: the coordinating per-Run vertical (architecture §2/§5).

Admission assembly → supervised spawn → driver/fidelity sequence → double
dispatch markers → bounded evidence → finalization per the terminal table →
session disposition, all bound exclusively to the C6 Native store seam.
Stage-2 arsd will wrap this object; direct embedding is the sanctioned
test/dev path. A top-level exception guard converts every per-Run failure
into a controlled terminal state — never propagation.

Write-once artifact rule: ``spec.json``, ``launch.json``, ``effective.json``,
both dispatch markers, and the terminal ``result.json`` are created only via
the seam's ``write_once_json``; mutable ``progress.json`` uses atomic
replacement and ``events.jsonl`` the bounded single-writer append.

``prompt-accepted`` means only that the complete local ACP prompt frame was
written to the supervised transport and drained — a local write-completion
fact, never a remote acceptance, and never an upgrade of certainty.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_run_supervisor.event_store import RunHandle
from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.managed_process import (
    ManagedExit,
    ManagedProcess,
    ManagedProcessLimits,
    spawn_managed_process,
)
from agent_run_supervisor.redaction import RedactionReport, redact_text
from agent_run_supervisor.result import build_result_payload
from agent_run_supervisor.session import (
    STATE_OPEN,
    SessionLock,
    SessionNotFoundError,
    SessionStore,
    validate_native_binding,
)

from . import storage
from .client import NativeAcpClient
from .config_fidelity import ConfigFidelityError, ConfigFidelityMachine
from .driver import NativeAcpDriver, NativeDriverError
from .event_writer import EventWriter, EventWriterOverflow
from .events import NativeAcpEventNormalizer
from .permissions import MediationEvent, PermissionBridge
from .profile import DEFAULT_REGISTRY, ProfileRegistry
from .spec import (
    AgentRunRequest,
    EffectiveRunState,
    NativeSpecError,
    RunSpecAssembler,
    spec_hash,
)

DISPATCH_STARTED_MARKER = "prompt-dispatch-started"
PROMPT_ACCEPTED_MARKER = "prompt-accepted"


class NativeRunTaskError(RuntimeError):
    """RunTask construction/coordination failure."""


@dataclass(frozen=True)
class FinalizationObservations:
    """Durable observations feeding the architecture §5 terminal table."""

    result_exists: bool = False
    persisted_terminal_event: str | None = None
    dispatch_started: bool = False
    acp_stop_reason: str | None = None
    supervisor_cancelled: bool = False
    supervisor_timed_out: bool = False
    child_exit_without_terminal: bool = False
    observation_interrupted: bool = False
    escalated_kill_after_dispatch: bool = False
    permission_violation: bool = False
    # A partial between-Run switch whose exact rollback could not be proven.
    rollback_unproven: bool = False


_COMPLETED_STOP_REASONS = frozenset(
    {"end_turn", "max_tokens", "max_turn_requests", "refusal"}
)


def finalize_run_state(
    observations: FinalizationObservations,
) -> tuple[AgentRunStatus | None, str]:
    """The terminal table as a pure function → (run_status, session_disposition).

    ``None`` status means an existing terminal result is kept (irreversible).
    Exit-code classification is subordinate by construction: no exit code is
    consulted, so a dispatched Turn without a reliable ACP terminal can never
    finalize completed/cancelled-class.
    """
    obs = observations
    if obs.result_exists:
        return (None, "keep")
    if obs.persisted_terminal_event is not None:
        return (_status_for_stop_reason(obs.persisted_terminal_event), "active")
    if not obs.dispatch_started:
        # Reusable unless a partial switch could not be rolled back with
        # exact readback proof.
        disposition = "quarantined" if obs.rollback_unproven else "active"
        return (AgentRunStatus.FAILED, disposition)
    if obs.acp_stop_reason is not None:
        if obs.permission_violation:
            return (AgentRunStatus.FAILED, "active")
        if obs.supervisor_timed_out:
            return (AgentRunStatus.TIMED_OUT, "active")
        return (_status_for_stop_reason(obs.acp_stop_reason), "active")
    if obs.escalated_kill_after_dispatch:
        if obs.supervisor_timed_out:
            return (AgentRunStatus.TIMED_OUT, "quarantined")
        if obs.supervisor_cancelled:
            # PRD R5: no trustworthy ACP terminal exists on this row — the
            # prompt may have executed, so cancelled-class would overclaim.
            return (AgentRunStatus.UNKNOWN, "quarantined")
        return (AgentRunStatus.FAILED, "quarantined")
    if obs.child_exit_without_terminal:
        return (AgentRunStatus.FAILED, "quarantined")
    if obs.observation_interrupted:
        return (AgentRunStatus.UNKNOWN, "quarantined")
    return (AgentRunStatus.UNKNOWN, "quarantined")


def _status_for_stop_reason(stop_reason: str) -> AgentRunStatus:
    if stop_reason == "cancelled":
        return AgentRunStatus.CANCELLED
    if stop_reason in _COMPLETED_STOP_REASONS:
        return AgentRunStatus.COMPLETED
    return AgentRunStatus.FAILED


@dataclass(frozen=True)
class NativeRunResult:
    run_id: str
    status: AgentRunStatus
    payload: dict[str, Any]
    run_dir: Path | None
    session_id: str | None
    session_state: str | None


class _PreDispatchFailure(Exception):
    """Internal control flow: admission/config/spawn failed before dispatch."""

    def __init__(self, reason: str, detail_code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail_code = detail_code


@dataclass
class _RunContext:
    handle: Any = None
    session_id: str | None = None
    ephemeral: bool = False
    reuse_load: bool = False
    load_external_id: str | None = None
    previous_pair: tuple[str | None, str | None] = (None, None)
    rollback_unproven: bool = False
    lock: SessionLock | None = None
    proc: ManagedProcess | None = None
    writer: EventWriter | None = None
    bridge: PermissionBridge | None = None
    client: NativeAcpClient | None = None
    profile: Any = None
    driver: NativeAcpDriver | None = None
    machine: ConfigFidelityMachine | None = None
    effective: EffectiveRunState = field(default_factory=EffectiveRunState)
    effective_written: bool = False
    dispatch_started: bool = False
    marker_ordinal: int = 0
    stop_reason: str | None = None
    usage: dict[str, Any] | None = None
    supervisor_cancelled: bool = False
    supervisor_timed_out: bool = False
    escalated_kill: bool = False
    child_exit_without_terminal: bool = False
    observation_interrupted: bool = False
    pre_dispatch: _PreDispatchFailure | None = None
    pipeline_error: BaseException | None = None
    agent_texts: list[str] = field(default_factory=list)
    exit_state: ManagedExit | None = None
    redaction: RedactionReport = field(default_factory=RedactionReport)
    error: BaseException | None = None


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


class RunTask:
    def __init__(
        self,
        *,
        request: AgentRunRequest,
        prompt_text: str,
        run_id: str,
        workspace_root: Path,
        registry: ProfileRegistry = DEFAULT_REGISTRY,
        supervisor_root: Path | None = None,
        session_store: SessionStore | None = None,
        event_store: Any = None,
        submitted_at: str | None = None,
        retry_of_run_id: str | None = None,
        cwd: str | None = None,
        prepared_handle: RunHandle | None = None,
    ) -> None:
        if supervisor_root is not None:
            session_store = storage.native_session_store(supervisor_root)
            event_store = storage.native_event_store(supervisor_root)
        if session_store is None or event_store is None:
            raise NativeRunTaskError(
                "RunTask needs either supervisor_root or a pre-built native "
                "store pair obtained through native_acp.storage"
            )
        # Fail-fast belt-and-suspenders: only seam-rooted stores are accepted.
        if Path(event_store.base_dir).name != storage.NATIVE_RUNS_DIRNAME:
            raise NativeRunTaskError(
                f"event store root {event_store.base_dir} is not a "
                f"{storage.NATIVE_RUNS_DIRNAME} root"
            )
        if Path(session_store.base_dir).name != storage.NATIVE_SESSIONS_DIRNAME:
            raise NativeRunTaskError(
                f"session store root {session_store.base_dir} is not a "
                f"{storage.NATIVE_SESSIONS_DIRNAME} root"
            )
        if prepared_handle is not None:
            # arsd admission handoff: only the exact reserved directory of
            # this run in this event store may be adopted — never an
            # arbitrary injected path.
            if not isinstance(prepared_handle, RunHandle):
                raise NativeRunTaskError(
                    "prepared_handle must be an event_store.RunHandle"
                )
            if prepared_handle.run_id != run_id:
                raise NativeRunTaskError(
                    "prepared_handle run_id does not match this RunTask run_id"
                )
            # Resolve ancestors only: resolving the final run-id entry would
            # let a symlink planted there collapse both comparison sides onto
            # the same foreign target and bypass the injection guard.
            handed_dir = Path(prepared_handle.run_dir)
            expected_dir = Path(event_store.base_dir).resolve() / run_id
            if handed_dir.parent.resolve() / handed_dir.name != expected_dir:
                raise NativeRunTaskError(
                    "prepared_handle run_dir is not the configured native "
                    "event-store run directory for this run_id"
                )
            if expected_dir.is_symlink():
                raise NativeRunTaskError(
                    "prepared_handle run_dir is a symlink, not the reserved "
                    "event-store run directory"
                )
            if not expected_dir.is_dir():
                raise NativeRunTaskError(
                    "prepared_handle run_dir is missing or not a directory"
                )
        self._prepared_handle = prepared_handle
        self._request = request
        self._prompt_text = prompt_text
        self._run_id = run_id
        self._workspace_root = Path(workspace_root)
        self._registry = registry
        self._session_store = session_store
        self._event_store = event_store
        self._submitted_at = submitted_at or _utc_now_iso()
        self._retry_of_run_id = retry_of_run_id
        self._cwd = cwd

    # -- public entry ------------------------------------------------------

    async def run(self) -> NativeRunResult:
        ctx = _RunContext()
        if self._prepared_handle is not None:
            # The admission side already performed the single exclusive
            # create_run for this key; repeating it here would break the
            # at-most-one-reservation contract.
            ctx.handle = self._prepared_handle
        else:
            try:
                ctx.handle = self._event_store.create_run(self._run_id)
            except Exception as exc:
                payload = {
                    "run_id": self._run_id,
                    "status": "failed",
                    "error": str(exc),
                }
                return NativeRunResult(
                    run_id=self._run_id,
                    status=AgentRunStatus.FAILED,
                    payload=payload,
                    run_dir=None,
                    session_id=None,
                    session_state=None,
                )
        cancelled = False
        try:
            await self._drive(ctx)
        except _PreDispatchFailure as failure:
            ctx.pre_dispatch = failure
        except asyncio.CancelledError:
            # Supervisor-side cancellation still finalizes (bounded): child
            # shutdown/reap, one terminal fact, session disposition, lease
            # release — then the cancellation is re-raised, never hidden.
            cancelled = True
            ctx.supervisor_cancelled = True
            if ctx.dispatch_started and ctx.stop_reason is None:
                # The wind-down kills the child without an ACP terminal: the
                # escalated-kill-after-dispatch row (conservative, never
                # completed).
                ctx.escalated_kill = True
        except BaseException as exc:  # top-level exception guard
            ctx.error = exc
            if ctx.dispatch_started and ctx.stop_reason is None:
                ctx.observation_interrupted = True
        result = await self._finalize_bounded(ctx)
        if cancelled:
            raise asyncio.CancelledError()
        return result

    # -- drive -------------------------------------------------------------

    async def _drive(self, ctx: _RunContext) -> None:
        spec, launch, binding, profile = self._admit(ctx)
        limits = spec.limits

        self._bind_session(ctx, spec, binding, profile)

        try:
            ctx.proc = await spawn_managed_process(
                argv=list(launch.argv),
                cwd=Path(binding.effective_cwd),
                env=self._spawn_env(launch),
                limits=ManagedProcessLimits(
                    max_stderr_bytes=limits.max_stderr_bytes,
                    cancel_grace_seconds=limits.cancel_grace_seconds,
                ),
            )
        except Exception as exc:
            raise _PreDispatchFailure(
                f"spawn failed: {exc}", "SPAWN_FAILED"
            ) from exc
        assert ctx.lock is not None and ctx.session_id is not None
        self._session_store.update_lock_holder(
            ctx.session_id,
            ctx.lock.token,
            identity=ctx.proc.identity,
            holder_kind="native_agent",
            reclaimable=False,
        )
        ctx.effective.process_identity = ctx.proc.identity

        # Architecture §8 native layout: the Run's event stream is events.jsonl.
        ctx.writer = EventWriter(
            ctx.handle,
            max_event_bytes=limits.max_event_bytes,
            max_events=limits.max_events,
            filename="events.jsonl",
        )
        await ctx.writer.start()
        normalizer = NativeAcpEventNormalizer()
        ctx.bridge = PermissionBridge(
            capabilities=spec.execution_grant.capabilities,
            workspace_root=Path(binding.effective_cwd),
            evidence_sink=self._mediation_sink(ctx),
        )
        client = NativeAcpClient(
            on_update=self._update_sink(ctx, normalizer),
            permission_handler=self._permission_handler(ctx),
            fs_read_handler=self._fs_read_handler(ctx),
        )
        ctx.client = client
        ctx.profile = profile
        ctx.machine = ConfigFidelityMachine(
            model_selector_id=profile.model_selector_id,
            effort_selector_id=profile.effort_selector_id,
            requested_model=spec.runtime.model_id,
            requested_effort=spec.runtime.effort,
        )
        ctx.driver = NativeAcpDriver(client=client, machine=ctx.machine)

        try:
            await asyncio.wait_for(
                self._startup_sequence(ctx, spec, binding),
                limits.startup_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise _PreDispatchFailure(
                "startup/config sequence timed out", "STARTUP_TIMEOUT"
            ) from None

        await self._dispatch(ctx, limits.turn_timeout_seconds)

    def _admit(self, ctx: _RunContext):
        try:
            assembler = RunSpecAssembler(self._request)
            profile = assembler.resolve_profile(self._registry)
            binding = assembler.bind_workspace(
                root=self._workspace_root, cwd=self._cwd
            )
            launch = assembler.resolve_launch()
            spec = assembler.seal(
                run_id=self._run_id,
                submitted_at=self._submitted_at,
                retry_of_run_id=self._retry_of_run_id,
            )
        except NativeSpecError as exc:
            raise _PreDispatchFailure(f"admission failed: {exc}", "ADMISSION") from exc
        spec_payload = spec.to_dict()
        spec_payload["spec_hash"] = spec_hash(spec)
        storage.write_once_json(ctx.handle.run_dir / "spec.json", spec_payload)
        launch_payload = launch.to_dict()
        launch_payload["launch_spec_hash"] = launch.launch_hash()
        storage.write_once_json(ctx.handle.run_dir / "launch.json", launch_payload)
        self._spec = spec
        return spec, launch, binding, profile

    def _bind_session(self, ctx: _RunContext, spec, binding, profile) -> None:
        if spec.session.reuse == "reuse":
            session_id = spec.session.ars_session_id
            ctx.ephemeral = False
        else:
            session_id = f"{self._run_id}-ephemeral"
            ctx.ephemeral = True
        assert session_id is not None
        ctx.session_id = session_id
        try:
            record = self._session_store.open_session(session_id)
        except SessionNotFoundError:
            record = storage.create_native_session(
                self._session_store,
                session_id=session_id,
                profile_id=spec.agent.profile_id,
                profile_revision=spec.agent.profile_revision,
                profile_hash=spec.agent.profile_hash,
                owner=spec.identity.owner,
                namespace=spec.identity.namespace,
                workspace_hash=spec.workspace.workspace_hash,
                effective_cwd=spec.workspace.cwd,
                matched_root=spec.workspace.canonical_root,
            )
        else:
            validate_native_binding(
                record,
                profile=profile,
                workspace_result=binding,
                owner=spec.identity.owner,
                namespace=spec.identity.namespace,
            )
            if record.agent_session_id is not None:
                # Later Runs on a bound session use real session/load with
                # the unchanged external ID (PRD R4).
                ctx.reuse_load = True
                ctx.load_external_id = record.agent_session_id
                ctx.previous_pair = (
                    record.last_effective_model,
                    record.last_effective_effort,
                )
        ctx.lock = self._session_store.acquire_lock(
            session_id,
            spec.identity.owner,
            reclaimable=False,
            required_state=STATE_OPEN,
        )

    async def _startup_sequence(self, ctx: _RunContext, spec, binding) -> None:
        driver = ctx.driver
        assert driver is not None and ctx.bridge is not None
        try:
            self._emit(ctx, {"type": "run_started", "method": "initialize"})
            await driver.open(ctx.proc)
            summary = await driver.initialize(
                client_capabilities=ctx.bridge.client_capabilities()
            )
            ctx.effective.agent_info = summary.agent_info
            ctx.effective.protocol_version = summary.protocol_version
            ctx.effective.capabilities = summary.capabilities
            ctx.effective.load_session_advertised = summary.load_session_advertised

            if ctx.reuse_load:
                if not summary.load_session_advertised:
                    raise _PreDispatchFailure(
                        "agent does not advertise loadSession; session reuse "
                        "is unsatisfiable — escalate per G6",
                        "LOAD_SESSION_UNADVERTISED",
                    )
                assert ctx.load_external_id is not None
                self._emit(ctx, {"type": "session_load_requested"})
                # Real session/load on the unchanged external ID; this path
                # never calls session/new — silent re-creation is failure.
                await driver.load_session(
                    agent_session_id=ctx.load_external_id,
                    cwd=binding.effective_cwd,
                )
                ctx.effective.agent_session_id = ctx.load_external_id
            else:
                self._emit(ctx, {"type": "session_new_requested"})
                external_id = await driver.new_session(cwd=binding.effective_cwd)
                ctx.effective.agent_session_id = external_id
                record = self._session_store.open_session(ctx.session_id)
                if record.agent_session_id is None:
                    storage.bind_agent_session(
                        self._session_store,
                        ctx.session_id,
                        agent_session_id=external_id,
                    )

            model, effort = await driver.set_config_exact()
        except _PreDispatchFailure:
            raise
        except (ConfigFidelityError, NativeDriverError) as exc:
            if ctx.client is not None and ctx.client.identity_violation:
                raise _PreDispatchFailure(
                    f"silent session recreation detected: "
                    f"{ctx.client.identity_violation}",
                    "SILENT_SESSION_RECREATION",
                ) from exc
            if ctx.reuse_load and ctx.machine is not None and (
                ctx.machine.phase not in ("init", "initial_options")
            ):
                # A set may have been dispatched: partial switch. No prompt;
                # roll back to the last exact-readback-proven pair or
                # quarantine.
                await self._rollback_after_partial_switch(ctx)
            raise _PreDispatchFailure(str(exc), "CONFIG_FIDELITY") from exc

        ctx.effective.effective_model = model
        ctx.effective.effective_effort = effort
        assert ctx.machine is not None
        ctx.effective.discovery_snapshots = [
            {"label": label, "options": options}
            for label, options in ctx.machine.snapshots
        ]
        self._session_store.commit_last_effective(
            ctx.session_id, model=model, effort=effort
        )
        storage.write_once_json(
            ctx.handle.run_dir / "effective.json", ctx.effective.to_dict()
        )
        ctx.effective_written = True
        self._write_progress(ctx, "running")

    async def _rollback_after_partial_switch(self, ctx: _RunContext) -> None:
        self._emit(ctx, {"type": "config_rollback_started"})
        previous_model, previous_effort = ctx.previous_pair
        if not previous_model or not previous_effort:
            ctx.rollback_unproven = True
            self._emit(ctx, {"type": "config_rollback_failed"})
            return
        assert ctx.machine is not None and ctx.driver is not None
        snapshots = ctx.machine.snapshots
        latest_options = snapshots[-1][1] if snapshots else None
        try:
            rollback_machine = ConfigFidelityMachine(
                model_selector_id=ctx.profile.model_selector_id,
                effort_selector_id=ctx.profile.effort_selector_id,
                requested_model=previous_model,
                requested_effort=previous_effort,
            )
            rollback_machine.record_initial_options(latest_options)
            # Rollback is itself exact-readback gated.
            await ctx.driver.set_config_exact(machine=rollback_machine)
            self._session_store.commit_last_effective(
                ctx.session_id, model=previous_model, effort=previous_effort
            )
            self._emit(ctx, {"type": "config_rollback_proven"})
        except Exception:
            ctx.rollback_unproven = True
            self._emit(ctx, {"type": "config_rollback_failed"})

    async def _dispatch(self, ctx: _RunContext, turn_timeout: float) -> None:
        driver = ctx.driver
        assert driver is not None
        # Evidence overflow before the prompt wire write: fail closed with no
        # prompt, never invent an uncertain post-dispatch row.
        if ctx.writer is not None and (
            ctx.pipeline_error is not None or not ctx.writer.can_accept
        ):
            raise _PreDispatchFailure(
                "event writer overflow before prompt dispatch",
                "EVIDENCE_PIPELINE",
            )
        # Conservative uncertainty boundary: created immediately before the
        # wire write attempt.
        self._write_marker(ctx, DISPATCH_STARTED_MARKER)
        ctx.dispatch_started = True
        driver.add_prompt_frame_hook(
            lambda: self._write_marker(ctx, PROMPT_ACCEPTED_MARKER)
        )
        self._emit(ctx, {"type": "session_prompt_sent"})
        try:
            outcome = await asyncio.wait_for(
                driver.prompt_once(self._prompt_text), turn_timeout
            )
        except asyncio.TimeoutError:
            ctx.supervisor_timed_out = True
            await self._escalate_kill(ctx)
            return
        except NativeDriverError:
            if await self._child_exited(ctx):
                ctx.child_exit_without_terminal = True
            else:
                ctx.observation_interrupted = True
            return
        ctx.stop_reason = outcome.stop_reason
        ctx.usage = outcome.usage
        if ctx.stop_reason is not None:
            self._emit(
                ctx, {"type": "run_completed", "stop_reason": ctx.stop_reason}
            )

    async def _escalate_kill(self, ctx: _RunContext) -> None:
        ctx.escalated_kill = True
        proc = ctx.proc
        if proc is None:
            return
        proc.terminate_group(reason="turn_timeout")
        try:
            ctx.exit_state = await asyncio.wait_for(
                proc.wait(), self._spec.limits.cancel_grace_seconds
            )
        except asyncio.TimeoutError:
            proc.kill_group(reason="turn_timeout_force_kill")
            ctx.exit_state = await proc.wait()

    async def _child_exited(self, ctx: _RunContext) -> bool:
        proc = ctx.proc
        if proc is None:
            return False
        try:
            ctx.exit_state = await asyncio.wait_for(proc.wait(), 5)
            return True
        except asyncio.TimeoutError:
            return False

    # -- sinks -------------------------------------------------------------

    def _emit(self, ctx: _RunContext, event: dict[str, Any]) -> None:
        if ctx.writer is None:
            return
        try:
            ctx.writer.emit_nowait(event)
        except EventWriterOverflow as exc:
            if ctx.pipeline_error is None:
                ctx.pipeline_error = exc

    def _update_sink(self, ctx: _RunContext, normalizer: NativeAcpEventNormalizer):
        def sink(session_id: str, update: dict[str, Any]) -> None:
            try:
                if update.get("sessionUpdate") == "agent_message_chunk":
                    content = update.get("content") or {}
                    text = content.get("text")
                    if isinstance(text, str):
                        ctx.agent_texts.append(text)
                self._emit(ctx, normalizer.normalize_update(update))
            except Exception as exc:
                if ctx.pipeline_error is None:
                    ctx.pipeline_error = exc

        return sink

    def _mediation_sink(self, ctx: _RunContext):
        def sink(event: MediationEvent) -> None:
            try:
                self._emit(ctx, event.to_event())
            except Exception as exc:
                if ctx.pipeline_error is None:
                    ctx.pipeline_error = exc

        return sink

    def _permission_handler(self, ctx: _RunContext):
        async def handler(request: dict[str, Any]) -> dict[str, Any]:
            assert ctx.bridge is not None
            return ctx.bridge.decide_permission_request(request)

        return handler

    def _fs_read_handler(self, ctx: _RunContext):
        async def handler(request: dict[str, Any]) -> str:
            assert ctx.bridge is not None
            decision = ctx.bridge.decide_fs_read(request["path"])
            if decision["decision"] != "allow":
                raise PermissionError(decision["reason"])
            # Read exactly the canonical workspace-bound path the decision
            # validated — never a supervisor-cwd-relative resolution.
            return Path(decision["resolved_path"]).read_text(encoding="utf-8")

        return handler

    # -- artifacts ---------------------------------------------------------

    def _write_marker(self, ctx: _RunContext, name: str) -> None:
        ctx.marker_ordinal += 1
        storage.write_once_json(
            ctx.handle.run_dir / name,
            {
                "marker": name,
                "run_id": self._run_id,
                "ordinal": ctx.marker_ordinal,
                "created_at": _utc_now_iso(),
            },
        )

    def _write_progress(self, ctx: _RunContext, state: str) -> None:
        last_seq = ctx.writer.last_seq if ctx.writer is not None else 0
        ctx.handle.write_json(
            "progress.json",
            {
                "schema_version": 1,
                "state": state,
                "last_seq": last_seq,
                "event_count": last_seq,
                "updated_at": _utc_now_iso(),
            },
        )

    def _spawn_env(self, launch) -> dict[str, str]:
        # Credential/env values enter only here, at spawn, from the allowlist
        # slot names — never through spec/launch serialization.
        return {
            name: os.environ[name]
            for name in launch.env_allowlist
            if name in os.environ
        }

    # -- finalization ------------------------------------------------------

    _FINALIZE_TIMEOUT_SECONDS = 60.0

    async def _finalize_bounded(self, ctx: _RunContext) -> NativeRunResult:
        """Finalization is bounded and cancellation-safe: a repeated
        cancellation or a hung finalization triggers last-resort synchronous
        cleanup instead of hanging or leaking, and repeated cancellation is
        always propagated."""
        try:
            return await asyncio.wait_for(
                self._finalize(ctx), self._FINALIZE_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            self._emergency_cleanup(ctx)
            raise
        except asyncio.TimeoutError:
            self._emergency_cleanup(ctx)
            return self._result_after_emergency(ctx)

    def _emergency_cleanup(self, ctx: _RunContext) -> None:
        """Sync-only last resort: kill the group, persist one conservative
        terminal fact if none exists, quarantine a dispatched-uncertain
        session, and release the lease. Every step is independent."""
        try:
            if ctx.proc is not None:
                ctx.proc.kill_group(reason="emergency_finalize")
        except Exception:
            pass
        try:
            if ctx.handle is not None and not (
                ctx.handle.run_dir / "result.json"
            ).exists():
                status = (
                    AgentRunStatus.UNKNOWN
                    if ctx.dispatch_started and ctx.stop_reason is None
                    else AgentRunStatus.FAILED
                )
                payload = build_result_payload(
                    run_id=self._run_id,
                    status=status,
                    origin="supervisor",
                    detail_code="EMERGENCY_FINALIZE",
                    retryable=_RETRYABLE_DEFAULT[status],
                    exit_code=None,
                    signal=None,
                    stop_reason=ctx.stop_reason,
                    usage=ctx.usage,
                    final_message="",
                    truncated=False,
                    truncate_reason=None,
                    run_dir=ctx.handle.run_dir,
                    raw_event_path="events.jsonl",
                )
                if ctx.session_id is not None:
                    payload["session_id"] = ctx.session_id
                storage.write_once_json(
                    ctx.handle.run_dir / "result.json", payload
                )
        except Exception:
            pass
        try:
            if (
                ctx.session_id is not None
                and ctx.dispatch_started
                and ctx.stop_reason is None
            ):
                self._session_store.mark_quarantined(
                    ctx.session_id,
                    reason="emergency finalization: dispatched turn without a "
                    "reliable terminal",
                    run_id=self._run_id,
                )
        except Exception:
            pass
        try:
            if ctx.lock is not None and ctx.session_id is not None:
                self._session_store.release_lock(ctx.session_id, ctx.lock.token)
        except Exception:
            pass

    def _result_after_emergency(self, ctx: _RunContext) -> NativeRunResult:
        try:
            payload = ctx.handle.read_json("result.json")
            return NativeRunResult(
                run_id=self._run_id,
                status=AgentRunStatus(payload["status"]),
                payload=payload,
                run_dir=ctx.handle.run_dir,
                session_id=ctx.session_id,
                session_state=self._session_state(ctx),
            )
        except Exception:
            return NativeRunResult(
                run_id=self._run_id,
                status=AgentRunStatus.FAILED,
                payload={
                    "run_id": self._run_id,
                    "status": "failed",
                    "error": "finalization timed out",
                },
                run_dir=getattr(ctx.handle, "run_dir", None),
                session_id=ctx.session_id,
                session_state=None,
            )

    async def _finalize(self, ctx: _RunContext) -> NativeRunResult:
        try:
            return await self._finalize_inner(ctx)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            payload = {
                "run_id": self._run_id,
                "status": "failed",
                "error": f"finalization failed: {exc}",
            }
            return NativeRunResult(
                run_id=self._run_id,
                status=AgentRunStatus.FAILED,
                payload=payload,
                run_dir=getattr(ctx.handle, "run_dir", None),
                session_id=ctx.session_id,
                session_state=None,
            )

    async def _finalize_inner(self, ctx: _RunContext) -> NativeRunResult:
        # Wind down the wire and reap the child before deciding.
        if ctx.driver is not None:
            await ctx.driver.close()
        if ctx.proc is not None and ctx.exit_state is None:
            ctx.proc.terminate_group(reason="finalize")
            try:
                ctx.exit_state = await asyncio.wait_for(ctx.proc.wait(), 10)
            except asyncio.TimeoutError:
                ctx.proc.kill_group(reason="finalize_force_kill")
                ctx.exit_state = await ctx.proc.wait()

        observations = FinalizationObservations(
            result_exists=(ctx.handle.run_dir / "result.json").exists(),
            dispatch_started=ctx.dispatch_started,
            acp_stop_reason=ctx.stop_reason,
            supervisor_cancelled=ctx.supervisor_cancelled,
            supervisor_timed_out=ctx.supervisor_timed_out,
            child_exit_without_terminal=ctx.child_exit_without_terminal,
            observation_interrupted=ctx.observation_interrupted,
            escalated_kill_after_dispatch=ctx.escalated_kill,
            permission_violation=(
                ctx.bridge.turn_failed if ctx.bridge is not None else False
            ),
            rollback_unproven=ctx.rollback_unproven,
        )
        status, disposition = finalize_run_state(observations)
        if status is None:
            payload = ctx.handle.read_json("result.json")
            return NativeRunResult(
                run_id=self._run_id,
                status=AgentRunStatus(payload["status"]),
                payload=payload,
                run_dir=ctx.handle.run_dir,
                session_id=ctx.session_id,
                session_state=self._session_state(ctx),
            )

        # Evidence-pipeline failure is the top-level boundary applied to
        # evidence: a completed-class turn with broken evidence is failed.
        detail_code: str | None = None
        if ctx.pre_dispatch is not None:
            detail_code = ctx.pre_dispatch.detail_code
        elif ctx.pipeline_error is not None and status in (
            AgentRunStatus.COMPLETED,
            AgentRunStatus.CANCELLED,
        ):
            status = AgentRunStatus.FAILED
            detail_code = "EVIDENCE_PIPELINE"
        elif ctx.supervisor_cancelled:
            detail_code = "SUPERVISOR_CANCELLED"
        elif ctx.observation_interrupted and status is AgentRunStatus.UNKNOWN:
            detail_code = "OBSERVATION_LOST"
        elif ctx.child_exit_without_terminal:
            detail_code = "CHILD_EXIT_WITHOUT_TERMINAL"
        elif ctx.error is not None:
            detail_code = "RUN_EXCEPTION"

        if not ctx.effective_written:
            # Record observed partial changes as evidence: the discovery
            # snapshots gathered before a config failure are part of the
            # effective observations.
            if ctx.machine is not None and not ctx.effective.discovery_snapshots:
                ctx.effective.discovery_snapshots = [
                    {"label": label, "options": options}
                    for label, options in ctx.machine.snapshots
                ]
            try:
                storage.write_once_json(
                    ctx.handle.run_dir / "effective.json", ctx.effective.to_dict()
                )
            except FileExistsError:
                pass

        final_message = ""
        if ctx.agent_texts:
            joined = "".join(ctx.agent_texts)
            final_message, report = redact_text(joined, location="final_message")
            ctx.redaction.merge(report)
        stderr_text = ""
        if ctx.proc is not None:
            raw_stderr = ctx.proc.stderr_bytes().decode("utf-8", errors="replace")
            stderr_text, stderr_report = redact_text(raw_stderr, location="stderr")
            ctx.redaction.merge(stderr_report)
        ctx.handle.write_text("stderr.log", stderr_text)
        ctx.handle.write_json(
            "redaction-report.json",
            {
                "matches": [
                    {"pattern": match.pattern_name, "note": match.note}
                    for match in ctx.redaction.matches
                ]
            },
        )

        payload = build_result_payload(
            run_id=self._run_id,
            status=status,
            origin="acp" if ctx.stop_reason is not None else "supervisor",
            detail_code=detail_code,
            retryable=_RETRYABLE_DEFAULT[status],
            exit_code=ctx.exit_state.exit_code if ctx.exit_state else None,
            signal=ctx.exit_state.signal if ctx.exit_state else None,
            stop_reason=ctx.stop_reason,
            usage=ctx.usage,
            final_message=final_message,
            truncated=False,
            truncate_reason=None,
            run_dir=ctx.handle.run_dir,
            raw_event_path="events.jsonl",
        )
        if ctx.session_id is not None:
            payload["session_id"] = ctx.session_id
        if ctx.pre_dispatch is not None:
            payload["failure_reason"] = ctx.pre_dispatch.reason
        elif ctx.supervisor_cancelled:
            payload["failure_reason"] = "supervisor cancellation"
        elif ctx.error is not None:
            payload["failure_reason"] = str(ctx.error)

        if ctx.writer is not None:
            if status is not AgentRunStatus.COMPLETED:
                self._emit(
                    ctx,
                    {"type": "run_failed", "code": payload.get("error_code")},
                )
            try:
                await ctx.writer.close()
            except Exception:
                pass

        try:
            storage.write_once_json(ctx.handle.run_dir / "result.json", payload)
        except FileExistsError:
            # A concurrent finalizer won the write-once race; the first
            # terminal fact stands, irreversibly.
            payload = ctx.handle.read_json("result.json")
            status = AgentRunStatus(payload["status"])
        self._write_progress(ctx, status.value)

        session_state = self._apply_session_disposition(ctx, status, disposition)

        return NativeRunResult(
            run_id=self._run_id,
            status=status,
            payload=payload,
            run_dir=ctx.handle.run_dir,
            session_id=ctx.session_id,
            session_state=session_state,
        )

    def _apply_session_disposition(
        self, ctx: _RunContext, status: AgentRunStatus, disposition: str
    ) -> str | None:
        if ctx.session_id is None:
            return None
        try:
            if disposition == "quarantined":
                reason = (
                    "observation lost after dispatch"
                    if status is AgentRunStatus.UNKNOWN
                    else f"run finalized {status.value} without a reliable terminal"
                )
                self._session_store.mark_quarantined(
                    ctx.session_id, reason=reason, run_id=self._run_id
                )
            elif ctx.ephemeral:
                self._session_store.mark_closed(ctx.session_id)
        except Exception:
            pass
        finally:
            if ctx.lock is not None:
                try:
                    self._session_store.release_lock(
                        ctx.session_id, ctx.lock.token
                    )
                except Exception:
                    pass
        return self._session_state(ctx)

    def _session_state(self, ctx: _RunContext) -> str | None:
        if ctx.session_id is None:
            return None
        try:
            return self._session_store.open_session(ctx.session_id).state
        except Exception:
            return None

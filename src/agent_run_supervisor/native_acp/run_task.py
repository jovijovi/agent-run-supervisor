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
import contextlib
import datetime as _dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_run_supervisor.event_store import RunHandle
from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.managed_process import (
    ManagedExit,
    ManagedProcess,
    ManagedProcessError,
    ManagedProcessLimits,
    spawn_managed_process,
)
from agent_run_supervisor.redaction import (
    RedactionReport,
    redact_text,
)
from agent_run_supervisor.result import (
    COMPLETED_ACP_STOP_REASONS,
    MAX_FINAL_MESSAGE_BYTES,
    build_minimal_evidence_pipeline_result,
    build_native_result_payload,
    enforce_native_result_ceiling,
    sanitize_failure_reason,
    sanitize_usage,
    truncate_utf8_bytes,
)
from agent_run_supervisor.session import (
    QUARANTINE_DISPATCH_OBSERVATION_LOST,
    derive_session_id_for_run,
    QUARANTINE_DISPATCH_WITHOUT_TERMINAL,
    QUARANTINE_SWITCH_ROLLBACK_UNPROVEN,
    QUARANTINE_UNTRUSTED_TERMINAL_EVIDENCE,
    SessionBindingError,
    SessionLock,
    SessionNotFoundError,
    SessionQuarantinedError,
    SessionRecordInvalidError,
    SessionStore,
    validate_native_binding,
    validate_native_session_record,
)

from . import storage
from .agent_registration import AgentEntry
from .client import NativeAcpClient
from .config_fidelity import ConfigFidelityError, ConfigFidelityMachine
from .driver import NativeAcpDriver, NativeDriverError
from .event_writer import EventWriter, EventWriterOverflow
from .launch_permissions import (
    LAUNCH_PERMISSION_CLEANUP_FAILED,
    LAUNCH_PERMISSION_CLEANUP_MARKER,
    LaunchPermissionError,
    MaterializedLaunchPermissions,
    discard as discard_launch_permissions,
    materialize as materialize_launch_permissions,
)
from .events import NativeAcpEventNormalizer
from .observation import (
    InitializeObservation,
    judge_initialize,
    observation_from_record,
)
from .permissions import MediationEvent, PermissionBridge
from .profile import DEFAULT_REGISTRY, ProfileRegistry
from .spec import (
    AgentRunRequest,
    ObservedRuntime,
    NativeSpecError,
    RunSpecAssembler,
    resolve_run_environment,
    spec_hash,
)

DISPATCH_STARTED_MARKER = "prompt-dispatch-started"
PROMPT_ACCEPTED_MARKER = "prompt-accepted"

# The durable configuration-switch vocabulary.
#
# Between publishing the bound Session record and writing the dispatch marker,
# ARS mutates the agent's configuration. A crash inside that window leaves a
# Session whose configuration nobody proved, and reconciliation cannot ask the
# dead process what it was doing — so the Run directory has to say it.
#
# Each marker records that one boundary was crossed and **nothing else**: no
# model literal, no option value, no readback, no child text. Which markers
# exist is the whole classification:
#
#   none                                  → no set was dispatched; reusable
#   started only                          → a set may have landed, unproven;
#                                           the Session must be quarantined
#   started + proven                      → exact readback proved the state
#   started + rollback-proven             → the switch was exactly undone
CONFIG_SWITCH_STARTED_MARKER = "config-switch-started"
CONFIG_PROVEN_MARKER = "config-proven"
CONFIG_ROLLBACK_PROVEN_MARKER = "config-rollback-proven"

CONFIG_SWITCH_MARKERS = (
    CONFIG_SWITCH_STARTED_MARKER,
    CONFIG_PROVEN_MARKER,
    CONFIG_ROLLBACK_PROVEN_MARKER,
)

# The only thing an allowed-but-failed workspace read is ever allowed to say.
FS_READ_FAILED = "FS_READ_FAILED"


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
    # Bounded evidence writer overflow / close / consumer failure.
    evidence_pipeline_failure: bool = False


def finalize_run_state(
    observations: FinalizationObservations,
) -> tuple[AgentRunStatus | None, str]:
    """The terminal table as a pure function → (run_status, session_disposition).

    The disposition vocabulary is exactly ``keep | reusable | quarantined``.
    None of the three is a Session lifecycle state: ``reusable`` is the ordinary
    outcome and means the Session is untouched, ``quarantined`` records
    independent safety evidence, and ``keep`` means an irreversible terminal
    already stands. No Run terminal ends a Session.

    ``None`` status means an existing terminal result is kept (irreversible).
    Exit-code classification is subordinate by construction: no exit code is
    consulted, so a dispatched Turn without a reliable ACP terminal can never
    finalize completed/cancelled-class.

    Evidence-pipeline failure is modeled explicitly: an existing durable result
    remains irreversible; ``unknown`` uncertainty remains the strongest status;
    otherwise a post-dispatch evidence-pipeline failure yields ``failed`` and
    keeps any already-required quarantine disposition (overriding
    completed/cancelled/timed_out).
    """
    obs = observations
    if obs.result_exists:
        return (None, "keep")
    if obs.persisted_terminal_event is not None:
        return (_status_for_stop_reason(obs.persisted_terminal_event), "reusable")
    if not obs.dispatch_started:
        # Reusable unless a partial switch could not be rolled back with
        # exact readback proof.
        disposition = "quarantined" if obs.rollback_unproven else "reusable"
        return (AgentRunStatus.FAILED, disposition)

    status, disposition = _post_dispatch_finalize(obs)
    if obs.evidence_pipeline_failure and status is not AgentRunStatus.UNKNOWN:
        if status in (
            AgentRunStatus.COMPLETED,
            AgentRunStatus.CANCELLED,
            AgentRunStatus.TIMED_OUT,
        ):
            return (AgentRunStatus.FAILED, disposition)
    return (status, disposition)


def _post_dispatch_finalize(
    obs: FinalizationObservations,
) -> tuple[AgentRunStatus, str]:
    if obs.acp_stop_reason is not None:
        if obs.permission_violation:
            return (AgentRunStatus.FAILED, "reusable")
        if obs.supervisor_timed_out:
            return (AgentRunStatus.TIMED_OUT, "reusable")
        return (_status_for_stop_reason(obs.acp_stop_reason), "reusable")
    if obs.escalated_kill_after_dispatch:
        if obs.supervisor_timed_out:
            # PRD R5 / GOAL contract 5: the supervisor killed the child with
            # no trustworthy ACP terminal, so the dispatched prompt may have
            # executed. timed_out (retryable by default) would invite a retry
            # the quarantined Session must refuse — uncertainty wins.
            return (AgentRunStatus.UNKNOWN, "quarantined")
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
    if stop_reason in COMPLETED_ACP_STOP_REASONS:
        return AgentRunStatus.COMPLETED
    return AgentRunStatus.FAILED


@dataclass(frozen=True)
class NativeRunResult:
    run_id: str
    status: AgentRunStatus
    payload: dict[str, Any]
    run_dir: Path | None
    session_id: str | None
    # Whether the Session ended this Run carrying quarantine evidence. There is
    # no Session state to report: the Session exists and stays resumable, or it
    # was never created at all (``session_id`` is then still the prospective id
    # and ``session_status`` answers unknown/not-found for it).
    session_quarantined: bool | None


@dataclass(frozen=True)
class CreateSessionPlan:
    """Create one brand-new durable Session, atomically, with its first Run.

    Constructible **only** from a request carrying no ``session_id``: it is the
    sole plan type the ``session/new`` startup arm matches, so a reuse request
    cannot reach ``driver.new_session`` structurally rather than by branch
    ordering.

    ``ar_session_id`` is the **prospective** id: nothing durable exists under it
    yet, and nothing will unless ``session/new`` returns. The process-local
    keyed admission lock is what serializes two live attempts at this id, and
    the sealed submission is what makes a repeat converge instead of creating a
    second Session.
    """

    ar_session_id: str


@dataclass(frozen=True)
class LoadSessionPlan:
    """Load an already-existing external Session by its stored id.

    ``external_session_id`` is captured exactly from an already-existing
    Native Session record and is replayed byte-for-byte: no trimming,
    normalization, parsing, case conversion, canonicalization, or
    regeneration, and no id is ever read back from the load response. It is
    ``repr=False`` so an accidental repr of the plan cannot print it.
    """

    ar_session_id: str
    external_session_id: str = field(repr=False)


# The closed union. There is no third member, no default arm, and no
# conversion between the two: every startup arm matches one member exactly.
SessionStartPlan = CreateSessionPlan | LoadSessionPlan


class _PreDispatchFailure(Exception):
    """Internal control flow: admission/config/spawn failed before dispatch."""

    def __init__(self, reason: str, detail_code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail_code = detail_code


_CATEGORICAL_FAILURE_REASON_BY_DETAIL: dict[str, str] = {
    "ADMISSION": "admission failed",
    "SPAWN_FAILED": "spawn failed",
    "STARTUP_TIMEOUT": "startup timed out",
    "LOAD_SESSION_UNADVERTISED": "session load unavailable",
    "SILENT_SESSION_RECREATION": "silent session recreation",
    "SESSION_NOT_FOUND_FOR_REUSE": "session not found for reuse",
    "SESSION_RECORD_INVALID": "session record invalid",
    "SESSION_EXTERNAL_ID_MISSING": "session external id missing",
    "SESSION_BINDING_MISMATCH": "session binding mismatch",
    "SESSION_QUARANTINED": "session quarantined",
    "CONFIG_FIDELITY": "config fidelity failed",
    "EVIDENCE_PIPELINE": "evidence pipeline failed",
    "SUPERVISOR_CANCELLED": "supervisor cancellation",
    "RUN_EXCEPTION": "run exception",
}


def _categorical_failure_reason(detail_code: str | None) -> str:
    if detail_code is None:
        return "run failed"
    return _CATEGORICAL_FAILURE_REASON_BY_DETAIL.get(detail_code, "run failed")


@dataclass
class FinalMessageAccumulator:
    """Bounded agent-message accumulation at ingestion.

    One bound: the byte ceiling that keeps evidence finite. Chunks are retained
    in arrival order and the assembled message is exactly their concatenation,
    so a value an agent split across two ``agent_message_chunk`` frames needs
    no carry, no recomposition window, and no rescan — it is simply the text
    the agent sent.
    """

    max_bytes: int = MAX_FINAL_MESSAGE_BYTES
    _parts: list[str] = field(default_factory=list)
    _byte_len: int = 0
    _finished: bool = False
    truncated: bool = False
    discarded_chunks: int = 0

    def ingest(self, text: str) -> None:
        if not isinstance(text, str) or not text or self._finished:
            return
        self._retain(text)

    def finish(self) -> None:
        """Close the accumulator. Idempotent."""
        self._finished = True

    def _retain(self, text: str) -> None:
        if not text:
            return
        raw = text.encode("utf-8")
        if self._byte_len >= self.max_bytes:
            self.truncated = True
            self.discarded_chunks += 1
            return
        remaining = self.max_bytes - self._byte_len
        if len(raw) <= remaining:
            self._parts.append(text)
            self._byte_len += len(raw)
            return
        kept = truncate_utf8_bytes(raw, remaining)
        if kept:
            self._parts.append(kept.decode("utf-8"))
            self._byte_len += len(kept)
        self.truncated = True
        self.discarded_chunks += 1

    def text(self) -> str:
        self.finish()
        return "".join(self._parts)

    @property
    def retained_bytes(self) -> int:
        return self._byte_len


@dataclass
class _RunContext:
    handle: Any = None
    session_id: str | None = None
    # True until the create path commits its one fully bound Session record.
    # While it holds, ``session_id`` names a Session that does not exist, and
    # nothing may fence, quarantine, observe, or lease it.
    session_pending_creation: bool = False
    previous_pair: tuple[str | None, str | None] = (None, None)
    rollback_unproven: bool = False
    lock: SessionLock | None = None
    proc: ManagedProcess | None = None
    writer: EventWriter | None = None
    bridge: PermissionBridge | None = None
    client: NativeAcpClient | None = None
    profile: Any = None
    # The profile/registration pair this Run resolved. Every consumer below
    # asks the pair for a fact and never asks which agent it is holding.
    instance: Any = None
    driver: NativeAcpDriver | None = None
    machine: ConfigFidelityMachine | None = None
    effective: ObservedRuntime = field(default_factory=ObservedRuntime)
    # The same Session's earlier ``initialize`` observation, when one exists.
    # Read *only* to emit drift warnings: it can never change a verdict.
    previous_observation: Any = None
    # This Run's own accepted ``initialize`` observation, held until a Session
    # record exists to carry it. A create observes before it creates.
    pending_observation: Any = None
    effective_written: bool = False
    dispatch_started: bool = False
    # Wire-order delivery ordinal of session/update callbacks (one per
    # observed frame); compared against the driver's prompt boundary.
    updates_delivered: int = 0
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
    final_message_acc: FinalMessageAccumulator = field(
        default_factory=FinalMessageAccumulator
    )
    exit_state: ManagedExit | None = None
    redaction: RedactionReport = field(default_factory=RedactionReport)
    # The private per-Run launch-permission material, when the resolved profile
    # selected a policy. It exists from before the spawn until the child is
    # proven reaped, and never one step longer or shorter.
    launch_permissions: MaterializedLaunchPermissions | None = None
    launch_permission_cleanup_failed: bool = False
    error: BaseException | None = None
    # When True, required quarantine/disposition failed: retain lease and
    # refuse to publish a terminal result.
    disposition_failed: bool = False


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
        agent_entry: AgentEntry | None = None,
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
        # The prospective Session id of a create, computed once from this Run's
        # identity by the single shared rule. It names nothing on disk until
        # ``session/new`` returns and the one bound record is written.
        self._prospective_session_id = derive_session_id_for_run(run_id)
        self._workspace_root = Path(workspace_root)
        self._registry = registry
        self._session_store = session_store
        self._event_store = event_store
        self._submitted_at = submitted_at or _utc_now_iso()
        self._retry_of_run_id = retry_of_run_id
        self._cwd = cwd
        # Admission already resolved this Run's agent against the immutable
        # startup snapshot; this is that entry, carried as a value. RunTask
        # never opens the registry, so spawn, finalization, and reconciliation
        # have no read path at all.
        if agent_entry is None:
            raise NativeRunTaskError(
                "RunTask requires the agent registry entry admission resolved"
            )
        self._agent_entry = agent_entry

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
            except Exception:
                # Stable code only: the store's exception text names paths and
                # errno detail that no caller-visible payload may carry.
                payload = {
                    "run_id": self._run_id,
                    "status": "failed",
                    "error": "run reservation failed",
                    "detail_code": "RUN_RESERVATION_FAILED",
                }
                return NativeRunResult(
                    run_id=self._run_id,
                    status=AgentRunStatus.FAILED,
                    payload=payload,
                    run_dir=None,
                    session_id=None,
                    session_quarantined=None,
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
        try:
            result = await self._finalize_bounded(ctx)
        finally:
            # Last resort for the paths that reap inside ``_emergency_cleanup``
            # and never reach ``_finalize_inner``. Idempotent, and it runs only
            # after the finalizer has proven the child exited.
            self._cleanup_launch_permissions(ctx)
        if cancelled:
            raise asyncio.CancelledError()
        return result

    # -- drive -------------------------------------------------------------

    async def _drive(self, ctx: _RunContext) -> None:
        spec, launch, binding, instance, environment = self._admit(ctx)
        limits = spec.limits

        # The final projected environment was resolved exactly once during
        # admission, and that very carrier is what the child receives — no
        # ambient re-read happens at spawn.
        plan = self._bind_session(ctx, spec, binding, instance)

        try:
            ctx.proc = await spawn_managed_process(
                argv=list(launch.argv),
                cwd=Path(binding.effective_cwd),
                env=environment,
                limits=ManagedProcessLimits(
                    max_stderr_bytes=limits.max_stderr_bytes,
                    cancel_grace_seconds=limits.cancel_grace_seconds,
                ),
            )
        except ManagedProcessError as exc:
            # The exec failure ARS observed, classified. Read as an ordinary
            # configuration error — never a security refusal — and carried as a
            # stable code, because the OS error text names the declared image
            # path and is never interpolated into a projectable reason.
            raise _PreDispatchFailure("spawn failed", exc.code) from exc
        except Exception as exc:
            raise _PreDispatchFailure("spawn failed", "SPAWN_FAILED") from exc
        assert ctx.session_id is not None
        if ctx.lock is not None:
            # The load path leased before the spawn, so the child identity is
            # recorded here. A create has no Session to lease yet; it records
            # the same identity in ``_acquire_lease`` the moment it does.
            self._session_store.update_lock_holder(
                ctx.session_id,
                ctx.lock.token,
                identity=ctx.proc.identity,
                holder_kind="native_agent",
                reclaimable=False,
            )
        ctx.effective.process_identity = ctx.proc.identity
        # Resolution facts, recorded with ``authoritative: false``. ARS performs
        # no pre-flight resolution and no ownership, mode, ancestor, symlink, or
        # digest check, so nothing here has anything to be compared to.
        ctx.effective.declared_command = instance.command
        ctx.effective.resolved_argv = tuple(launch.argv)

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
        ctx.profile = instance.profile
        ctx.instance = instance
        # Selector ids come from the instance, so a source-frozen profile and an
        # operator-registered agent take the identical code path with different
        # data — there is no agent-aware branch anywhere below this line.
        try:
            ctx.machine = ConfigFidelityMachine(
                model_selector_id=instance.model_selector_id,
                effort_selector_id=instance.effort_selector_id,
                requested_model=spec.runtime.model_id,
                requested_effort=spec.runtime.effort,
                permission_mode_selector_id=(
                    instance.profile.permission_mode_selector_id
                ),
                required_permission_mode=instance.profile.required_permission_mode,
                fidelity_mode=instance.config_fidelity_mode,
            )
        except ConfigFidelityError as exc:
            # An effort the declared fidelity mode cannot honour — a non-``N/A``
            # effort against a model-only agent — is refused here, before any
            # ACP frame and long before a prompt.
            raise _PreDispatchFailure(str(exc), "CONFIG_FIDELITY") from exc
        ctx.driver = NativeAcpDriver(
            client=client,
            machine=ctx.machine,
            on_config_switch_started=lambda: self._mark_config_switch_started(ctx),
        )

        try:
            await asyncio.wait_for(
                self._startup_sequence(ctx, spec, binding, plan),
                limits.startup_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise _PreDispatchFailure(
                "startup/config sequence timed out", "STARTUP_TIMEOUT"
            ) from None

        await self._dispatch(ctx, limits.turn_timeout_seconds)

    def _admit(self, ctx: _RunContext):
        """Resolve, bind, project the environment once, then seal — in that order.

        The environment is resolved here, before the Spec is sealed and before
        the child exists, so the sealed launch snapshot describes exactly which
        names and precedence were handed to exec with no window in which the
        daemon's own environment could change in between.
        """
        try:
            assembler = RunSpecAssembler(self._request)
            instance = assembler.resolve_agent(
                self._agent_entry, registry=self._registry
            )
            binding = assembler.bind_workspace(
                root=self._workspace_root, cwd=self._cwd
            )
            # Before the one environment resolution, because the pair it
            # produces is part of that resolution, and before the seal, because
            # its digest is part of the sealed launch evidence.
            material = self._materialize_launch_permissions(
                ctx, instance, self._request.grant_capabilities
            )
            environment = resolve_run_environment(
                arsd_env=dict(os.environ),
                profile=instance.profile,
                entry=self._agent_entry,
                launch_permission=() if material is None else material.env_pairs,
            )
            launch = assembler.resolve_launch(
                environment=environment, launch_permission=material
            )
            spec = assembler.seal(
                run_id=self._run_id,
                submitted_at=self._submitted_at,
                retry_of_run_id=self._retry_of_run_id,
            )
        except LaunchPermissionError as exc:
            # A grant this backend cannot faithfully enforce, or material that
            # could not be created safely. Either way: no child, no prompt, and
            # only the stable code.
            if exc.code == LAUNCH_PERMISSION_CLEANUP_FAILED:
                # Material this Run created survived its own failed rollback.
                # The terminal already carries the code; the marker keeps
                # "a leftover exists" one uniform, value-blind fact whether the
                # removal failed here or after the child was reaped.
                ctx.launch_permission_cleanup_failed = True
                self._record_cleanup_failure(ctx)
            raise _PreDispatchFailure(exc.code, exc.code) from exc
        except NativeSpecError as exc:
            raise _PreDispatchFailure(f"admission failed: {exc}", "ADMISSION") from exc
        spec_payload = spec.to_dict()
        spec_payload["spec_hash"] = spec_hash(spec)
        storage.write_once_json(ctx.handle.run_dir / "spec.json", spec_payload)
        launch_payload = launch.to_dict()
        launch_payload["launch_spec_hash"] = launch.launch_hash()
        storage.write_once_json(ctx.handle.run_dir / "launch.json", launch_payload)
        self._spec = spec
        return spec, launch, binding, instance, environment

    def _materialize_launch_permissions(
        self, ctx: _RunContext, instance, capabilities
    ) -> MaterializedLaunchPermissions | None:
        """Compile and write the policy the resolved **profile** selected.

        The profile answers, so no runtime path branches on which agent this is.
        A profile that selects nothing materializes nothing, and its launch
        snapshot and environment projection are byte-identical to before.
        """
        policy_id = instance.launch_permission_policy_id
        if policy_id is None:
            return None
        material = materialize_launch_permissions(
            policy_id, capabilities=capabilities, run_dir=ctx.handle.run_dir
        )
        ctx.launch_permissions = material
        return material

    def _cleanup_launch_permissions(self, ctx: _RunContext) -> bool:
        """Remove the private material. Idempotent, and never early.

        The material exists so a **live** child can consult it, so removal is
        gated on proven exit: with an un-reaped child still able to read it,
        this returns without touching anything rather than racing the agent.
        """
        material = ctx.launch_permissions
        if material is None:
            return True
        if ctx.proc is not None and ctx.exit_state is None:
            return True
        try:
            discard_launch_permissions(material)
        except LaunchPermissionError:
            # Sticky: a later successful retry clears the leftover, never the
            # fact that the in-order attempt could not complete.
            ctx.launch_permission_cleanup_failed = True
            self._record_cleanup_failure(ctx)
            return False
        ctx.launch_permissions = None
        return True

    def _record_cleanup_failure(self, ctx: _RunContext) -> None:
        """One durable, value-blind categorical fact per Run.

        An event needs a live ``EventWriter``, and the pre-spawn, spawn-failure
        and emergency paths have none — so a leftover could be durable while its
        classification was not. The marker is the surface that always exists,
        written **once**: the outer last-resort retry cannot erase it, and it
        carries a stable code and the Run id and nothing else. No path, no
        errno, no document content, no environment value, no exception text.
        """
        handle = ctx.handle
        if handle is None:
            return
        try:
            storage.write_once_json(
                Path(handle.run_dir) / LAUNCH_PERMISSION_CLEANUP_MARKER,
                {
                    "code": LAUNCH_PERMISSION_CLEANUP_FAILED,
                    "run_id": self._run_id,
                },
            )
        except FileExistsError:
            # An earlier attempt already classified this Run. Write-once is
            # exactly what keeps that first fact.
            pass
        except Exception:
            # Housekeeping evidence must never become a supervision failure.
            # The in-memory flag still stands for this process.
            pass

    def _bind_session(
        self, ctx: _RunContext, spec, binding, instance
    ) -> SessionStartPlan:
        """Derive the closed start plan from the sealed Session intent (B1).

        ``session_id is None`` versus an existing-session load decides the
        branch once, and each branch builds exactly one plan type. There is no
        third path, no conversion, and no recovery behavior that could turn a
        reuse request into a new Session: a reuse request that cannot produce a
        ``LoadSessionPlan`` fails here, before the lease and before any spawn.

        Only the load branch takes a lease here, because only it has a Session
        to lease. A create branch has a prospective id and nothing under it; its
        lease is taken in :meth:`_startup_sequence`, the instant the one fully
        bound record exists.
        """
        # The operator's own continuity epoch, or ``None``. Nothing derives it:
        # absent is not 1, and only an operator's registry edit changes it.
        epoch = instance.session_epoch
        if spec.session.session_id is not None:
            plan: SessionStartPlan = self._plan_reuse_session(
                ctx, spec, binding, instance, epoch
            )
        else:
            plan = self._plan_create_session(ctx, spec)
        if isinstance(plan, LoadSessionPlan):
            # Only a reuse has a Session to lease at this point. A create leases
            # in ``_startup_sequence``, the instant its one bound record exists.
            try:
                self._acquire_lease(ctx, plan.ar_session_id, spec.identity.owner)
            except SessionQuarantinedError as exc:
                # Committed evidence the pre-lease validation could not have
                # seen, or an unconverged quarantine fence, which is not
                # evidence yet and so passes that validation by design. The
                # in-guard refusal is the correctness mechanism; the caller
                # is owed the same code either way. Nothing was leased, so
                # there is nothing to release.
                raise _PreDispatchFailure(
                    "session is quarantined", "SESSION_QUARANTINED"
                ) from exc
            # What this Session observed last time, loaded once, under the
            # lease. Read-only and warning-only: it reaches nothing that
            # decides admission, and a Session's first Run simply has none.
            ctx.previous_observation = observation_from_record(
                self._session_store.open_session(plan.ar_session_id)
            )
        return plan

    def _acquire_lease(self, ctx: _RunContext, session_id: str, owner: str) -> None:
        """Take the Session lease, and record the child holder when one exists.

        The load path leases before the spawn, so there is no child identity to
        record yet and ``_drive`` adds it. The create path leases *after* the
        record commits, by which time the child is already running, so the
        holder identity is recorded here instead — the lock is never left
        naming only a supervisor whose child already exists.
        """
        ctx.lock = self._session_store.acquire_lock(
            session_id,
            owner,
            reclaimable=False,
            refuse_quarantined=True,
        )
        if ctx.proc is not None:
            self._session_store.update_lock_holder(
                session_id,
                ctx.lock.token,
                identity=ctx.proc.identity,
                holder_kind="native_agent",
                reclaimable=False,
            )

    def _plan_reuse_session(
        self, ctx: _RunContext, spec, binding, instance, epoch
    ) -> LoadSessionPlan:
        """Existing-only open, strict validation, then the load-time gate.

        Every refusal below happens before the lease is touched and long before
        ``session/load``, and none of them creates, repairs, or reopens a
        record. The four categories stay distinct because they mean different
        things to a caller: the Session is gone, its record is unusable, it was
        never bound to an external Session, or it belongs to a different Run
        identity.
        """
        session_id = spec.session.session_id
        assert session_id is not None  # this branch is chosen by its presence
        ctx.session_id = session_id
        try:
            record = self._session_store.open_session(session_id)
        except SessionNotFoundError as exc:
            raise _PreDispatchFailure(
                "session reuse requires an already-existing session record",
                "SESSION_NOT_FOUND_FOR_REUSE",
            ) from exc
        except Exception as exc:
            # Unreadable/malformed persisted record: never a second chance to
            # be created, and never a fallback to a new Session.
            raise _PreDispatchFailure(
                "session record could not be read", "SESSION_RECORD_INVALID"
            ) from exc
        try:
            validate_native_session_record(record, expected_session_id=session_id)
        except SessionRecordInvalidError as exc:
            raise _PreDispatchFailure(
                "session record failed strict validation", "SESSION_RECORD_INVALID"
            ) from exc
        external_id = record.agent_session_id
        if external_id is None:
            raise _PreDispatchFailure(
                "session record carries no external session id",
                "SESSION_EXTERNAL_ID_MISSING",
            )
        try:
            # Before the lease is acquired and long before session/load.
            validate_native_binding(
                record,
                profile=instance.profile,
                workspace_result=binding,
                owner=spec.identity.owner,
                namespace=spec.identity.namespace,
                for_load=True,
                expected_epoch=epoch,
                expected_agent_id=spec.agent.agent_id,
            )
        except SessionQuarantinedError as exc:
            # A sibling of SessionBindingError, not a kind of it: the record
            # matches this Run perfectly and is refused anyway, because
            # continuity was machine-proven unsafe. Caught by its own type
            # so it keeps its own caller-facing code instead of falling
            # through to the generic run-exception guard.
            raise _PreDispatchFailure(
                "session is quarantined", "SESSION_QUARANTINED"
            ) from exc
        except SessionBindingError as exc:
            raise _PreDispatchFailure(
                "session binding mismatch", "SESSION_BINDING_MISMATCH"
            ) from exc
        ctx.previous_pair = (
            record.last_effective_model,
            record.last_effective_effort,
        )
        return LoadSessionPlan(
            ar_session_id=session_id, external_session_id=external_id
        )

    def _plan_create_session(self, ctx: _RunContext, spec) -> CreateSessionPlan:
        """Name the prospective Session and write nothing under it.

        The whole create reservation is the durable submission and the sealed
        Spec, which already carry this Run's authenticated identity, plus the
        process-local keyed admission lock the daemon holds around the attempt.
        No record, no directory, no lease, and no second durable reservation
        exists until ``session/new`` returns — which is exactly what makes a
        failed creation report a terminal failed Run and an unknown Session
        rather than a resumable one.
        """
        session_id = self._prospective_session_id
        ctx.session_id = session_id
        ctx.session_pending_creation = True
        return CreateSessionPlan(ar_session_id=session_id)

    async def _startup_sequence(
        self, ctx: _RunContext, spec, binding, plan: SessionStartPlan
    ) -> None:
        driver = ctx.driver
        assert driver is not None and ctx.bridge is not None
        # The frozen session metadata comes only from the resolved profile and
        # is sent identically on session/new and session/load: the agent
        # rebuilds its underlying query from the load request, so omitting it
        # there would silently restore ambient setting sources on every reused
        # Session. No caller value can reach this argument.
        session_meta = ctx.profile.session_meta_for("new")
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

            self._observe_initialize(ctx, ctx.instance, summary)

            # Disjoint arms over the closed union: one arm per plan type, no
            # default arm, no guard, and no conversion between the two. The
            # reuse request never reaches the ``session/new`` arm because it
            # cannot produce the plan type that arm matches.
            match plan:
                case CreateSessionPlan():
                    self._emit(ctx, {"type": "session_new_requested"})
                    external_id = await driver.new_session(
                        cwd=binding.effective_cwd, meta=session_meta
                    )
                    ctx.effective.agent_session_id = external_id
                    # One exclusive write commits one fully bound record. The
                    # external id is present from the record's first byte, so
                    # there is no window in which a Session exists without the
                    # provider context it names — and a crash before this line
                    # leaves no Session at all, which is what keeps a failed
                    # creation distinguishable from a resumable one.
                    storage.create_native_session(
                        self._session_store,
                        session_id=ctx.session_id,
                        profile_id=spec.agent.profile_id,
                        profile_revision=spec.agent.profile_revision,
                        profile_hash=spec.agent.profile_hash,
                        owner=spec.identity.owner,
                        namespace=spec.identity.namespace,
                        workspace_hash=spec.workspace.workspace_hash,
                        effective_cwd=spec.workspace.cwd,
                        matched_root=spec.workspace.canonical_root,
                        agent_session_id=external_id,
                        agent_id=spec.agent.agent_id,
                        session_epoch=ctx.instance.session_epoch,
                    )
                    ctx.session_pending_creation = False
                    # Now — and only now — there is something to lease, and
                    # something to carry this Run's initialize observation.
                    self._acquire_lease(
                        ctx, ctx.session_id, spec.identity.owner
                    )
                    self._commit_pending_observation(ctx)
                case LoadSessionPlan(external_session_id=stored_external_id):
                    if not summary.load_session_advertised:
                        raise _PreDispatchFailure(
                            "agent does not advertise loadSession; session reuse "
                            "is unsatisfiable — escalate per G6",
                            "LOAD_SESSION_UNADVERTISED",
                        )
                    self._emit(ctx, {"type": "session_load_requested"})
                    # Real session/load on the unchanged external ID; this path
                    # never calls session/new — silent re-creation is failure.
                    await driver.load_session(
                        agent_session_id=stored_external_id,
                        cwd=binding.effective_cwd,
                        meta=session_meta,
                    )
                    ctx.effective.agent_session_id = stored_external_id

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
            if (
                not ctx.session_pending_creation
                and ctx.machine is not None
                and ctx.machine.phase not in ("init", "initial_options")
            ):
                # A set may have been dispatched against a Session that now
                # exists durably: partial switch. No prompt; roll back to the
                # last exact-readback-proven pair, or quarantine.
                #
                # This is deliberately *not* limited to the load path. A create
                # publishes its bound record before it configures, so the same
                # window exists there — and a create has no previously proven
                # pair to roll back to, which is why it can only quarantine.
                await self._rollback_after_partial_switch(ctx)
            # Fidelity errors quote the exact requested/observed option values,
            # which are child-advertised strings; the terminal carries the
            # stable code and a categorical reason, never this text.
            raise _PreDispatchFailure(str(exc), "CONFIG_FIDELITY") from exc

        # Exact readback succeeded, so what the agent's configuration is has
        # been proven. Recorded before anything else so a crash after this point
        # never looks like an unproven switch.
        self._write_config_marker(ctx, CONFIG_PROVEN_MARKER)
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
            ctx.handle.run_dir / "effective.json", self._effective_payload(ctx)
        )
        ctx.effective_written = True
        self._write_progress(ctx, "running")

    def _effective_payload(self, ctx: _RunContext) -> dict[str, Any]:
        """``effective.json`` is observed child data end to end."""
        return ctx.effective.to_dict()

    def _write_once(
        self, ctx: _RunContext, name: str, payload: dict[str, Any]
    ) -> None:
        storage.write_once_json(ctx.handle.run_dir / name, payload)

    def _observe_initialize(self, ctx: _RunContext, instance, summary) -> None:
        """Record what ``initialize`` reported, then apply the contract checks.

        The write-once artifact is persisted before any refusal and strictly
        before ``session/new``/``session/load`` or any prompt, so even a refused
        Run leaves durable evidence and zero Turn.

        What changed at the reset: the agent's self-reported name and version
        are **evidence**, not identity. Drift between two Runs of one Session is
        recorded, may be emitted as a policy warning, and never refuses — which
        is what makes an agent upgrade behind an unchanged registered command
        cost no ARS action. The only refusals left here are checks against the
        profile's declared contract inside this one Run.
        """
        observed = InitializeObservation(
            agent_info=summary.agent_info,
            protocol_version=summary.protocol_version,
            capabilities=summary.capabilities,
            load_session_advertised=summary.load_session_advertised,
        )
        verdict = judge_initialize(instance, observed, previous=ctx.previous_observation)
        self._write_once(ctx, "initialize_evidence.json", verdict.to_evidence())
        for warning in verdict.warnings:
            self._emit(ctx, dict(warning))
        if verdict.refusal is not None:
            raise _PreDispatchFailure(
                verdict.detail or verdict.refusal, verdict.refusal
            )
        if instance.profile.requires_session_load and not summary.load_session_advertised:
            # Session semantics are profile-frozen, so "requires a real
            # session/load" is a declared contract term and its absence is a
            # required capability being absent — one of the five, not a sixth.
            raise _PreDispatchFailure(
                "agent does not advertise loadSession; the registered profile "
                "requires it — escalate per G6",
                "CAPABILITY_MISSING",
            )
        # Held only after the observation passed every contract check, so what
        # a later Run compares against is an observation this Session actually
        # accepted. Still evidence: nothing reads it to decide anything.
        #
        # It is *held*, not written, because ``initialize`` happens before
        # ``session/new`` and a create path has no Session record to write to
        # yet. ``_commit_pending_observation`` writes it the moment one exists.
        ctx.pending_observation = observed
        self._commit_pending_observation(ctx)

    def _commit_pending_observation(self, ctx: _RunContext) -> None:
        """Write the held ``initialize`` observation once a record exists."""
        observed = ctx.pending_observation
        if observed is None or ctx.session_id is None or ctx.session_pending_creation:
            return
        name, version = observed.self_report()
        self._session_store.commit_last_observation(
            ctx.session_id,
            agent_info_name=name,
            agent_info_version=version,
            advertised_capabilities=tuple(observed.advertised()),
        )
        ctx.pending_observation = None

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
                model_selector_id=ctx.instance.model_selector_id,
                effort_selector_id=ctx.instance.effort_selector_id,
                requested_model=previous_model,
                requested_effort=previous_effort,
                # The rollback runs the declared mode, so a model-only Session
                # is restored by the same model-only sequence.
                fidelity_mode=ctx.instance.config_fidelity_mode,
                # The rollback re-runs the full exact sequence, so a frozen
                # permission mode is re-proven too — a rollback must never
                # leave the session in an unproven mode.
                permission_mode_selector_id=ctx.profile.permission_mode_selector_id,
                required_permission_mode=ctx.profile.required_permission_mode,
            )
            rollback_machine.record_initial_options(latest_options)
            # Rollback is itself exact-readback gated.
            await ctx.driver.set_config_exact(machine=rollback_machine)
            self._session_store.commit_last_effective(
                ctx.session_id, model=previous_model, effort=previous_effort
            )
            # Durable proof that the switch was exactly undone, so a crash from
            # here on does not read as an unproven switch.
            self._write_config_marker(ctx, CONFIG_ROLLBACK_PROVEN_MARKER)
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
        # Persistence barrier: await durable session_prompt_sent before any
        # prompt wire write. Marker already implies conservative
        # unknown/quarantine — never overclaim predispatch after this point.
        # Do not treat consumer_queue_healthy as this barrier.
        if ctx.writer is not None:
            try:
                await ctx.writer.emit_awaited({"type": "session_prompt_sent"})
            except Exception as exc:
                if ctx.pipeline_error is None:
                    ctx.pipeline_error = exc
                return
        if ctx.pipeline_error is not None:
            return
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
        # Both are agent-authored: the stop reason is a wire string and usage
        # is a free-form object. Both stay subject to the existing size
        # ceilings and to nothing else.
        ctx.stop_reason = outcome.stop_reason
        ctx.usage = sanitize_usage(outcome.usage)
        # The durable completed lifecycle fact is recorded only when this ACP
        # terminal can actually finalize completed permission-wise: a known
        # permission violation already forces FAILED, and a completed→failed
        # stream would contradict the terminal (finalization emits run_failed).
        permission_violated = ctx.bridge is not None and (
            ctx.bridge.turn_failed or ctx.bridge.grant_violation
        )
        if ctx.stop_reason is not None and not permission_violated:
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

    @staticmethod
    def _current_turn_chunk(ctx: _RunContext) -> bool:
        """Whether the update being delivered is causally after the prompt.

        session/load history replay (and any other pre-prompt chunk) stays
        normalized event evidence but never contributes to this Run's
        ``final_message``. The boundary is snapshotted in exact wire order by
        the driver's stream observer, so a pre-prompt update whose queued
        callback executes late can still never pass this gate. The observer
        counts only frames the locked SDK will actually dispatch, so a
        suppressed pre-prompt frame can never shift the boundary onto a
        genuine current-turn chunk.
        """
        driver = ctx.driver
        if driver is None:
            return False
        boundary = driver.prompt_wire_boundary
        return boundary is not None and ctx.updates_delivered > boundary

    def _update_sink(self, ctx: _RunContext, normalizer: NativeAcpEventNormalizer):
        def sink(session_id: str, update: dict[str, Any]) -> None:
            # One callback per SDK-dispatched session/update frame, in wire
            # order; count before any processing so a failing update keeps
            # ordinals aligned with the driver's observed count (which
            # includes only frames the locked SDK actually dispatches —
            # suppressed frames never leave phantom ordinals).
            ctx.updates_delivered += 1
            try:
                text: Any = None
                if update.get("sessionUpdate") == "agent_message_chunk":
                    content = update.get("content") or {}
                    text = content.get("text")
                    if isinstance(text, str) and self._current_turn_chunk(ctx):
                        ctx.final_message_acc.ingest(text)
                event = normalizer.normalize_update(update)
                self._emit(ctx, event)
                if ctx.bridge is not None:
                    violation = ctx.bridge.observe_tool_update(update)
                    if violation is not None:
                        self._emit(ctx, violation)
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
            try:
                content: str | None = Path(
                    decision["resolved_path"]
                ).read_text(encoding="utf-8")
            except (OSError, ValueError):
                # The refusal path was already categorical; the *allowed* path
                # was not. An OSError here names the absolute path and the
                # errno, and a decode error names the offending byte — and
                # this exception is what ARS hands the SDK, which renders it
                # into a protocol error and a log record. The path is
                # child-chosen and workspace-relative, so it routinely carries
                # projected values.
                content = None
            if content is None:
                # Raised outside the handler so no traceback, cause, or
                # context retains the original message.
                raise PermissionError(FS_READ_FAILED)
            return content

        return handler

    # -- artifacts ---------------------------------------------------------

    def _mark_config_switch_started(self, ctx: _RunContext) -> None:
        """Durably record that a configuration set is about to be written.

        Fired by the driver immediately before its first
        ``session/set_config_option``. From here until a proof marker lands, a
        crash leaves a Session whose configuration nobody proved, and
        reconciliation must quarantine it rather than hand it to a prompt.
        """
        self._write_config_marker(ctx, CONFIG_SWITCH_STARTED_MARKER)

    def _write_config_marker(self, ctx: _RunContext, name: str) -> None:
        """Write one configuration boundary marker; never fail the Run for it.

        A marker that cannot be written is not a supervision failure in itself:
        the in-process path still has ``ctx.machine`` and still decides
        correctly. Its absence only makes a *later* reconciliation more
        conservative, which is the safe direction.
        """
        if ctx.handle is None:
            return
        try:
            self._write_marker(ctx, name)
        except FileExistsError:
            pass
        except Exception:
            pass

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

    # -- finalization ------------------------------------------------------

    _FINALIZE_TIMEOUT_SECONDS = 60.0
    # Bounded backoff between fail-closed emergency reap retries (no busy spin).
    _EMERGENCY_REAP_RETRY_DELAY_SECONDS = 0.05

    async def _finalize_bounded(self, ctx: _RunContext) -> NativeRunResult:
        """Finalization is bounded and cancellation-safe: a repeated
        cancellation or a hung finalization triggers last-resort emergency
        cleanup (shielded kill+reap, then disposition) instead of hanging or
        leaking, and repeated cancellation is always propagated."""
        try:
            return await asyncio.wait_for(
                self._finalize(ctx), self._FINALIZE_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            await self._emergency_cleanup(ctx)
            raise
        except asyncio.TimeoutError:
            await self._emergency_cleanup(ctx)
            return self._result_after_emergency(ctx)

    async def _emergency_cleanup(self, ctx: _RunContext) -> None:
        """Last resort: SIGKILL, await reap, then persist one conservative
        terminal fact if none exists, quarantine a dispatched-uncertain
        session, and release the lease only after a TRUSTED terminal exists.

        Child exit/reap completes before any lease release, terminal/result
        return that permits registry deregistration, or propagated cancellation
        return from this helper. Each dedicated ``ManagedProcess.wait()`` task is
        awaited through ``asyncio.shield`` so caller cancellation cannot cancel
        the underlying reap. Ordinary/transient wait failures fail closed and
        retry after a small bounded delay until one shielded reap proves exit;
        every failed/done reap task is observed (never detached). Cancellation
        during retry delay/reap is absorbed and remembered, then propagated only
        after proven reap plus emergency disposition.

        INVALID/uncertain existing terminals must fence+quarantine and return
        without lease release. Fence/quarantine failure also refuses release.
        """
        cancelled = False

        def _absorb_cancel() -> None:
            nonlocal cancelled
            cancelled = True
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                task.uncancel()

        def _propagate_if_cancelled() -> None:
            if cancelled:
                raise asyncio.CancelledError()

        async def _retry_delay() -> None:
            delay = asyncio.create_task(
                asyncio.sleep(self._EMERGENCY_REAP_RETRY_DELAY_SECONDS),
                name=f"emergency-reap-delay:{self._run_id}",
            )

            def _observe_delay_done() -> None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    delay.result()

            try:
                while True:
                    try:
                        await asyncio.shield(delay)
                        return
                    except asyncio.CancelledError:
                        # Classify source by caller cancelling() first: a same-turn
                        # race can leave the delay done while this task is cancelling.
                        me = asyncio.current_task()
                        if me is not None and me.cancelling():
                            _absorb_cancel()
                            if delay.done():
                                _observe_delay_done()
                                return
                            continue
                        if delay.done():
                            _observe_delay_done()
                            return
                        _absorb_cancel()
            finally:
                if not delay.done():
                    while not delay.done():
                        try:
                            await asyncio.shield(delay)
                        except asyncio.CancelledError:
                            me = asyncio.current_task()
                            if me is not None and me.cancelling():
                                _absorb_cancel()
                                if delay.done():
                                    _observe_delay_done()
                                    return
                                continue
                            if delay.done():
                                _observe_delay_done()
                                return
                            _absorb_cancel()
                        except Exception:
                            return

        if ctx.proc is not None:
            try:
                ctx.proc.kill_group(reason="emergency_finalize")
            except Exception:
                pass
            # kill_group signals only; proven wait()/reap must precede
            # release/return. Retry fail-closed on ordinary wait failures.
            while True:
                reap_task = asyncio.create_task(
                    ctx.proc.wait(), name=f"emergency-reap:{self._run_id}"
                )
                reaped = False

                def _observe_reap_done() -> bool:
                    try:
                        ctx.exit_state = reap_task.result()
                        return True
                    except (asyncio.CancelledError, Exception):
                        return False

                try:
                    while True:
                        try:
                            ctx.exit_state = await asyncio.shield(reap_task)
                            reaped = True
                            break
                        except asyncio.CancelledError:
                            # Caller cancelling() wins over reap_task.done(): same-turn
                            # completion must not swallow the delivered cancellation.
                            me = asyncio.current_task()
                            if me is not None and me.cancelling():
                                _absorb_cancel()
                                if reap_task.done():
                                    reaped = _observe_reap_done()
                                    break
                                continue
                            if reap_task.done():
                                # Reap task itself ended cancelled — observe.
                                with contextlib.suppress(asyncio.CancelledError):
                                    reap_task.result()
                                break
                            _absorb_cancel()
                        except Exception:
                            # Failed wait observed via the await above.
                            break
                finally:
                    if not reap_task.done():
                        # Observe through shield so a detach never leaves an
                        # unobserved reap task if this coroutine aborts early.
                        while not reap_task.done():
                            try:
                                ctx.exit_state = await asyncio.shield(reap_task)
                                reaped = True
                            except asyncio.CancelledError:
                                me = asyncio.current_task()
                                if me is not None and me.cancelling():
                                    _absorb_cancel()
                                    if reap_task.done():
                                        reaped = _observe_reap_done()
                                        break
                                    continue
                                if reap_task.done():
                                    with contextlib.suppress(asyncio.CancelledError):
                                        reap_task.result()
                                    break
                                _absorb_cancel()
                            except Exception:
                                break
                if reaped:
                    break
                await _retry_delay()

        existing = storage.NativeTerminalState(storage.NativeTerminalKind.ABSENT)
        if ctx.handle is not None:
            try:
                existing = storage.read_native_terminal_result(
                    ctx.handle.run_dir / "result.json", run_id=self._run_id
                )
            except Exception:
                existing = storage.NativeTerminalState(
                    storage.NativeTerminalKind.INVALID
                )

        if existing.kind is storage.NativeTerminalKind.INVALID:
            if ctx.session_id is not None:
                try:
                    self._quarantine(
                        ctx, QUARANTINE_UNTRUSTED_TERMINAL_EVIDENCE
                    )
                except Exception:
                    _propagate_if_cancelled()
                    return
            _propagate_if_cancelled()
            return

        if existing.kind is storage.NativeTerminalKind.TRUSTED:
            try:
                if ctx.lock is not None and ctx.session_id is not None:
                    self._session_store.release_lock(ctx.session_id, ctx.lock.token)
                    ctx.lock = None
            except Exception:
                pass
            _propagate_if_cancelled()
            return

        # ABSENT: quarantine when dispatch is uncertain, then write one terminal.
        need_quarantine = (
            ctx.session_id is not None
            and ctx.dispatch_started
            and ctx.stop_reason is None
        )
        if need_quarantine:
            try:
                self._quarantine(ctx, QUARANTINE_DISPATCH_WITHOUT_TERMINAL)
            except Exception:
                # Required quarantine failed: no terminal result, no lease release.
                _propagate_if_cancelled()
                return
        written_trusted = False
        try:
            if ctx.handle is not None:
                status = (
                    AgentRunStatus.UNKNOWN
                    if ctx.dispatch_started and ctx.stop_reason is None
                    else AgentRunStatus.FAILED
                )
                payload = build_native_result_payload(
                    run_id=self._run_id,
                    status=status,
                    origin="supervisor",
                    detail_code="EMERGENCY_FINALIZE",
                    retryable=_RETRYABLE_DEFAULT[status],
                    signal=None,
                    stop_reason=ctx.stop_reason,
                    usage=sanitize_usage(ctx.usage),
                    final_message="",
                    truncated=False,
                    truncate_reason=None,
                    run_dir=ctx.handle.run_dir,
                    raw_event_path="events.jsonl",
                )
                if ctx.session_id is not None:
                    payload["session_id"] = ctx.session_id
                payload = enforce_native_result_ceiling(
                    payload,
                    run_id=self._run_id,
                    run_dir=ctx.handle.run_dir,
                    session_id=ctx.session_id,
                )
                storage.write_once_json(
                    ctx.handle.run_dir / "result.json", payload
                )
                written = storage.read_native_terminal_result(
                    ctx.handle.run_dir / "result.json", run_id=self._run_id
                )
                written_trusted = (
                    written.kind is storage.NativeTerminalKind.TRUSTED
                )
        except Exception:
            written_trusted = False
        if not written_trusted:
            _propagate_if_cancelled()
            return
        try:
            if ctx.lock is not None and ctx.session_id is not None:
                self._session_store.release_lock(ctx.session_id, ctx.lock.token)
                ctx.lock = None
        except Exception:
            pass
        _propagate_if_cancelled()

    def _result_after_emergency(self, ctx: _RunContext) -> NativeRunResult:
        """Return only a TRUSTED Native terminal; never raw/forged evidence."""
        failed = NativeRunResult(
            run_id=self._run_id,
            status=AgentRunStatus.FAILED,
            payload={
                "run_id": self._run_id,
                "status": "failed",
                "error": "finalization timed out",
            },
            run_dir=getattr(ctx.handle, "run_dir", None),
            session_id=ctx.session_id,
            session_quarantined=None,
        )
        if ctx.handle is None:
            return failed
        try:
            state = storage.read_native_terminal_result(
                ctx.handle.run_dir / "result.json", run_id=self._run_id
            )
        except Exception:
            return failed
        if (
            state.kind is storage.NativeTerminalKind.TRUSTED
            and state.payload is not None
        ):
            return NativeRunResult(
                run_id=self._run_id,
                status=AgentRunStatus(state.payload["status"]),
                payload=state.payload,
                run_dir=ctx.handle.run_dir,
                session_id=ctx.session_id,
                session_quarantined=self._session_quarantined(ctx),
            )
        return failed

    async def _finalize(self, ctx: _RunContext) -> NativeRunResult:
        try:
            return await self._finalize_inner(ctx)
        except asyncio.CancelledError:
            raise
        except BaseException:
            # Pre-reap / unexpected inner failure: contain the child and lease
            # before any result that lets registry deregistration proceed.
            # Caller-visible payload stays fixed/sanitized — never interpolate
            # exception text, paths, secrets, or exception class names.
            await self._emergency_cleanup(ctx)
            payload = {
                "run_id": self._run_id,
                "status": "failed",
                "error": "finalization failed",
                "detail_code": "FINALIZATION_FAILED",
            }
            return NativeRunResult(
                run_id=self._run_id,
                status=AgentRunStatus.FAILED,
                payload=payload,
                run_dir=getattr(ctx.handle, "run_dir", None),
                session_id=ctx.session_id,
                session_quarantined=None,
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

        # The child is proven exited (or never existed), so the private
        # launch-permission material may go. A failure is housekeeping, not a
        # terminal fact: it is recorded categorically and never as errno text.
        if not self._cleanup_launch_permissions(ctx):
            self._emit(ctx, {"type": "launch_permission_cleanup_failed"})

        # Provisional state only as needed to enqueue final failure evidence
        # before the writer is closed. Close/drain next; capture close failure
        # into the evidence pipeline; then freeze final state/payload once.
        # Never emit after close.
        if ctx.writer is not None:
            provisional = self._observations(ctx)
            provisional_status, _ = finalize_run_state(provisional)
            if (
                provisional_status is not None
                and provisional_status is not AgentRunStatus.COMPLETED
            ):
                self._emit(
                    ctx,
                    {
                        "type": "run_failed",
                        "code": {
                            AgentRunStatus.FAILED: "FAILED",
                            AgentRunStatus.CANCELLED: "CANCELLED",
                            AgentRunStatus.TIMED_OUT: "TIMED_OUT",
                            AgentRunStatus.UNKNOWN: "UNKNOWN",
                        }.get(provisional_status, "FAILED"),
                    },
                )
            try:
                await ctx.writer.close()
            except Exception as exc:
                if ctx.pipeline_error is None:
                    ctx.pipeline_error = exc

        observations = self._observations(ctx)
        status, disposition = finalize_run_state(observations)
        if status is None:
            # Early-result branch: only a TRUSTED Native terminal may stand.
            existing_state = storage.read_native_terminal_result(
                ctx.handle.run_dir / "result.json", run_id=self._run_id
            )
            if (
                existing_state.kind is not storage.NativeTerminalKind.TRUSTED
                or existing_state.payload is None
            ):
                self._quarantine(ctx, QUARANTINE_UNTRUSTED_TERMINAL_EVIDENCE)
                raise RuntimeError(
                    "untrusted existing terminal evidence; refusing release"
                )
            payload = existing_state.payload
            return NativeRunResult(
                run_id=self._run_id,
                status=AgentRunStatus(payload["status"]),
                payload=payload,
                run_dir=ctx.handle.run_dir,
                session_id=ctx.session_id,
                session_quarantined=self._session_quarantined(ctx),
            )

        detail_code: str | None = None
        if ctx.pre_dispatch is not None:
            detail_code = ctx.pre_dispatch.detail_code
        elif (
            ctx.bridge is not None
            and ctx.bridge.grant_violation
            and status is AgentRunStatus.FAILED
        ):
            # Stable A4-S2 classification: a write-family tool completed
            # without the required mediation under a non-write grant.
            detail_code = "PERMISSION_VIOLATION"
        elif ctx.pipeline_error is not None and status is AgentRunStatus.FAILED:
            detail_code = "EVIDENCE_PIPELINE"
        elif ctx.supervisor_cancelled:
            detail_code = "SUPERVISOR_CANCELLED"
        elif ctx.supervisor_timed_out and status is AgentRunStatus.UNKNOWN:
            detail_code = "TURN_TIMEOUT"
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
                    ctx.handle.run_dir / "effective.json",
                    self._effective_payload(ctx),
                )
            except FileExistsError:
                pass

        ctx.final_message_acc.finish()
        final_message = ""
        truncated = False
        truncate_reason: str | None = None
        if ctx.final_message_acc.retained_bytes or ctx.final_message_acc.truncated:
            joined = ctx.final_message_acc.text()
            # The static shape redactor runs on the assembled message, which is
            # a different string from any chunk that formed it.
            final_message, report = redact_text(joined, location="final_message")
            ctx.redaction.merge(report)
            if ctx.final_message_acc.truncated:
                truncated = True
                truncate_reason = "max_final_message_bytes"
        stderr_text = ""
        if ctx.proc is not None:
            decoded = ctx.proc.stderr_bytes().decode("utf-8", errors="replace")
            stderr_text, stderr_report = redact_text(decoded, location="stderr")
            ctx.redaction.merge(stderr_report)
        storage.write_run_text(ctx.handle, "stderr.log", stderr_text)
        ctx.handle.write_json(
            "redaction-report.json",
            {
                "matches": [
                    {"pattern": match.pattern_name, "note": match.note}
                    for match in ctx.redaction.matches
                ]
            },
        )

        payload = build_native_result_payload(
            run_id=self._run_id,
            status=status,
            origin="acp" if ctx.stop_reason is not None else "supervisor",
            detail_code=detail_code,
            retryable=_RETRYABLE_DEFAULT[status],
            signal=ctx.exit_state.signal if ctx.exit_state else None,
            stop_reason=ctx.stop_reason,
            usage=sanitize_usage(ctx.usage),
            final_message=final_message,
            truncated=truncated,
            truncate_reason=truncate_reason,
            run_dir=ctx.handle.run_dir,
            raw_event_path="events.jsonl",
        )
        if ctx.session_id is not None:
            payload["session_id"] = ctx.session_id
        if ctx.pre_dispatch is not None:
            payload["failure_reason"] = sanitize_failure_reason(
                _categorical_failure_reason(ctx.pre_dispatch.detail_code)
            )
        elif ctx.supervisor_cancelled:
            payload["failure_reason"] = sanitize_failure_reason(
                "supervisor cancellation"
            )
        elif ctx.error is not None:
            payload["failure_reason"] = sanitize_failure_reason(
                _categorical_failure_reason(detail_code)
            )

        before_ceiling = payload
        payload = enforce_native_result_ceiling(
            payload,
            run_id=self._run_id,
            run_dir=ctx.handle.run_dir,
            session_id=ctx.session_id,
        )
        if payload is not before_ceiling and payload.get("status") == "failed":
            status = AgentRunStatus.FAILED

        try:
            session_quarantined = self._publish_terminal_with_disposition(
                ctx, status=status, disposition=disposition, payload=payload
            )
        except Exception:
            ctx.disposition_failed = True
            # In-memory refusal only — no durable terminal, no lease release.
            return NativeRunResult(
                run_id=self._run_id,
                status=AgentRunStatus.FAILED,
                payload={
                    "run_id": self._run_id,
                    "status": "failed",
                    "error": "session disposition failed",
                    "detail_code": "SESSION_DISPOSITION_FAILED",
                },
                run_dir=ctx.handle.run_dir,
                session_id=ctx.session_id,
                session_quarantined=self._session_quarantined(ctx),
            )

        status = AgentRunStatus(payload["status"])
        return NativeRunResult(
            run_id=self._run_id,
            status=status,
            payload=payload,
            run_dir=ctx.handle.run_dir,
            session_id=ctx.session_id,
            session_quarantined=session_quarantined,
        )

    def _observations(self, ctx: _RunContext) -> FinalizationObservations:
        trusted_terminal = False
        if ctx.handle is not None:
            state = storage.read_native_terminal_result(
                ctx.handle.run_dir / "result.json", run_id=self._run_id
            )
            trusted_terminal = state.kind is storage.NativeTerminalKind.TRUSTED
        return FinalizationObservations(
            result_exists=trusted_terminal,
            dispatch_started=ctx.dispatch_started,
            acp_stop_reason=ctx.stop_reason,
            supervisor_cancelled=ctx.supervisor_cancelled,
            supervisor_timed_out=ctx.supervisor_timed_out,
            child_exit_without_terminal=ctx.child_exit_without_terminal,
            observation_interrupted=ctx.observation_interrupted,
            escalated_kill_after_dispatch=ctx.escalated_kill,
            permission_violation=(
                ctx.bridge is not None
                and (ctx.bridge.turn_failed or ctx.bridge.grant_violation)
            ),
            rollback_unproven=ctx.rollback_unproven,
            evidence_pipeline_failure=ctx.pipeline_error is not None,
        )

    def _publish_terminal_with_disposition(
        self,
        ctx: _RunContext,
        *,
        status: AgentRunStatus,
        disposition: str,
        payload: dict[str, Any],
    ) -> bool | None:
        """Quarantine-before-result when required; release lease only after both."""
        if disposition == "quarantined":
            if ctx.rollback_unproven and not ctx.dispatch_started:
                reason_code = QUARANTINE_SWITCH_ROLLBACK_UNPROVEN
            elif status is AgentRunStatus.UNKNOWN:
                reason_code = QUARANTINE_DISPATCH_OBSERVATION_LOST
            else:
                reason_code = QUARANTINE_DISPATCH_WITHOUT_TERMINAL
            # Never swallow: fence + quarantine must succeed before result.
            self._quarantine(ctx, reason_code, swallow=False)

        try:
            storage.write_once_json(ctx.handle.run_dir / "result.json", payload)
        except FileExistsError:
            # A concurrent finalizer won the write-once race; only a TRUSTED
            # first fact may stand. Invalid/forged evidence fences the Session
            # and never releases the lease.
            existing_state = storage.read_native_terminal_result(
                ctx.handle.run_dir / "result.json", run_id=self._run_id
            )
            if existing_state.kind is not storage.NativeTerminalKind.TRUSTED:
                self._quarantine(
                    ctx, QUARANTINE_UNTRUSTED_TERMINAL_EVIDENCE, swallow=False
                )
                raise RuntimeError(
                    "untrusted existing terminal evidence; refusing release"
                )
            existing = existing_state.payload
            assert existing is not None
            payload.clear()
            payload.update(existing)
            status = AgentRunStatus(payload["status"])
        self._write_progress(ctx, status.value)

        # Nothing happens to the Session here. A Run terminal is a Run fact:
        # the Session it ran under stays exactly as durable and as resumable as
        # it was a moment ago, and the only thing that changes is the lease.

        if ctx.lock is not None and ctx.session_id is not None:
            try:
                self._session_store.release_lock(ctx.session_id, ctx.lock.token)
            except Exception:
                pass
            ctx.lock = None
        return self._session_quarantined(ctx)

    def _quarantine(
        self, ctx: _RunContext, reason_code: str, *, swallow: bool = True
    ) -> None:
        """Fence, then record quarantine evidence — both idempotent.

        A Session that does not exist cannot be quarantined, and that is not a
        gap: quarantine only ever follows dispatch or an unproven switch
        rollback, and both happen strictly after the create path has committed
        its one bound record. Before that commit no prompt was sent, so there
        is nothing to be uncertain about.
        """
        if ctx.session_id is None or ctx.session_pending_creation:
            return
        try:
            self._session_store.write_quarantine_pending(
                ctx.session_id, reason_code=reason_code, run_id=self._run_id
            )
            self._session_store.mark_quarantined(
                ctx.session_id, reason_code=reason_code, run_id=self._run_id
            )
        except Exception:
            if not swallow:
                raise

    def _session_quarantined(self, ctx: _RunContext) -> bool | None:
        if ctx.session_id is None:
            return None
        try:
            return self._session_store.open_session(ctx.session_id).quarantine is not None
        except Exception:
            # No record: a create that never reached its commit. The Session
            # does not exist, so it is neither quarantined nor resumable.
            return None

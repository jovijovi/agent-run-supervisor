"""Principal-bound arsd handlers: durable admission, registry, Run/Session ops."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as _dt
import json
import logging
import math
import weakref
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping

from agent_run_supervisor import __version__ as PACKAGE_VERSION
from agent_run_supervisor.event_store import EventStore, EventStoreError
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.profile import DEFAULT_REGISTRY, ProfileRegistry
from agent_run_supervisor.native_acp.run_task import RunTask
from agent_run_supervisor.session import (
    SessionNotFoundError,
    SessionQuarantinedError,
    SessionStore,
)

from . import admission, protocol, server

_LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT_RUNS = 4
DEFAULT_EVENTS_PAGE_LIMIT = 100
MAX_EVENTS_PAGE_LIMIT = 1000
DEFAULT_EVENT_FOLLOW_QUEUE = 256
DEFAULT_FOLLOW_IDLE_SECONDS = 0.25
# Production ceiling for run_events follow idle (seconds). Documented max.
MAX_FOLLOW_IDLE_SECONDS = 3600.0
EVENTS_FILENAME = "events.jsonl"
# Conservative per-line bound for events.jsonl evidence reads (handlers-local).
MAX_EVENT_LINE_BYTES = 65_536

_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "server_info": frozenset(),
    "session_list": frozenset(),
    "run_status": frozenset({"run_id"}),
    "run_cancel": frozenset({"run_id"}),
    "run_events": frozenset(
        {"run_id", "from_seq", "limit", "follow", "follow_idle_seconds"}
    ),
    "session_status": frozenset({"session_id"}),
    "session_close": frozenset({"session_id"}),
}

RunTaskFactory = Callable[..., Any]
HandlerResult = Mapping[str, Any] | AsyncIterator[Mapping[str, Any]]


def _utc_now_iso() -> str:
    return (
        _dt.datetime.now(tz=_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "+00:00")
    )


def _require_closed_payload(op: str, payload: Mapping[str, Any]) -> None:
    allowed = _PAYLOAD_FIELDS.get(op)
    if allowed is None:
        return
    if any(key not in allowed for key in payload):
        raise protocol.ProtocolError(
            protocol.INVALID_REQUEST, "unknown payload fields present"
        )


def default_run_task_factory(
    supervisor_root: Path, *, registry: ProfileRegistry = DEFAULT_REGISTRY
) -> RunTaskFactory:
    """Production factory: RunTask bound to supervisor-root native stores."""

    root = Path(supervisor_root)
    session_store = storage.native_session_store(root)
    event_store = storage.native_event_store(root)

    def factory(*, command, run_id, prepared_handle, submitted_at):
        return RunTask(
            request=command.request,
            prompt_text=command.prompt_text,
            run_id=run_id,
            workspace_root=Path(command.workspace_root),
            registry=registry,
            session_store=session_store,
            event_store=event_store,
            submitted_at=submitted_at,
            retry_of_run_id=command.retry_of_run_id,
            cwd=command.cwd,
            prepared_handle=prepared_handle,
        )

    return factory


@dataclasses.dataclass
class _RegistryEntry:
    task: asyncio.Task[Any]
    principal_id: str
    owner: str
    namespace: str
    session_id: str | None
    submitted_at: str


class RunRegistry:
    """Bounded registry with atomic reserve → commit(register) → release."""

    def __init__(self, *, max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS) -> None:
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be >= 1")
        self._max = max_concurrent_runs
        self._entries: dict[str, _RegistryEntry] = {}
        self._reservations: dict[object, str | None] = {}
        self._lock = asyncio.Lock()

    @property
    def max_concurrent_runs(self) -> int:
        return self._max

    def is_registered(self, run_id: str) -> bool:
        return run_id in self._entries

    def task_for(self, run_id: str) -> asyncio.Task[Any] | None:
        entry = self._entries.get(run_id)
        return entry.task if entry is not None else None

    def active_count(self) -> int:
        return len(self._entries)

    def _occupied_locked(self) -> int:
        return len(self._entries) + len(self._reservations)

    def _session_held_locked(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        if any(entry.session_id == session_id for entry in self._entries.values()):
            return True
        return any(value == session_id for value in self._reservations.values())

    def session_busy(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        return (
            any(entry.session_id == session_id for entry in self._entries.values())
            or any(value == session_id for value in self._reservations.values())
        )

    async def reserve(self, *, session_id: str | None) -> object:
        """Atomically reserve capacity + optional session slot before create_run."""
        async with self._lock:
            if self._occupied_locked() >= self._max:
                raise protocol.ProtocolError(
                    protocol.CAPACITY_EXHAUSTED, "run capacity exhausted"
                )
            if self._session_held_locked(session_id):
                raise protocol.ProtocolError(
                    protocol.SESSION_BUSY, "session already has an active run"
                )
            token: object = object()
            self._reservations[token] = session_id
            return token

    async def release(self, token: object | None) -> None:
        if token is None:
            return
        async with self._lock:
            self._reservations.pop(token, None)

    async def register(
        self,
        run_id: str,
        coro,
        *,
        reservation: object,
        principal_id: str,
        owner: str,
        namespace: str,
        session_id: str | None,
        submitted_at: str,
    ) -> asyncio.Task[Any]:
        """Consume a reservation and start the guarded Run task."""
        async with self._lock:
            if reservation not in self._reservations:
                raise RuntimeError("run registration requires a live reservation")
            if run_id in self._entries:
                raise RuntimeError("run already registered")
            task = asyncio.create_task(
                self._run_guarded(run_id, coro), name=f"arsd:{run_id}"
            )
            self._entries[run_id] = _RegistryEntry(
                task=task,
                principal_id=principal_id,
                owner=owner,
                namespace=namespace,
                session_id=session_id,
                submitted_at=submitted_at,
            )
            self._reservations.pop(reservation)
            return task

    async def _run_guarded(self, run_id: str, coro) -> Any:
        try:
            runner = await coro if asyncio.iscoroutine(coro) else coro
            if hasattr(runner, "run"):
                return await runner.run()
            return await runner
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("arsd: run task failed (%s)", run_id)
            return None
        finally:
            async with self._lock:
                self._entries.pop(run_id, None)

    async def cancel(self, run_id: str, *, wait_seconds: float) -> bool:
        async with self._lock:
            entry = self._entries.get(run_id)
            task = entry.task if entry is not None else None
        if task is None:
            return False
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), wait_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            pass
        return True

    async def cancel_all(self, *, wait_seconds: float) -> None:
        async with self._lock:
            tasks = [entry.task for entry in self._entries.values()]
            self._reservations.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=wait_seconds)


class EventFollowStream:
    """Bounded per-subscription follow stream; never cancels the Run."""

    def __init__(
        self,
        *,
        run_dir: Path,
        run_id: str,
        from_seq: int,
        idle_seconds: float,
        queue_size: int,
    ) -> None:
        self._run_dir = run_dir
        self._run_id = run_id
        self._cursor = from_seq
        self._idle = idle_seconds
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=queue_size
        )
        self._stop = asyncio.Event()
        self._failure: protocol.ProtocolError | None = None
        self._fail_event = asyncio.Event()
        self._watcher: asyncio.Task[Any] | None = None
        self._closed = False
        self._wait_tasks: set[asyncio.Task[Any]] = set()

    def __aiter__(self) -> EventFollowStream:
        if self._watcher is None and not self._closed:
            self._watcher = asyncio.create_task(self._watch())
        return self

    async def __anext__(self) -> Mapping[str, Any]:
        while True:
            if self._failure is not None:
                raise self._failure
            if self._closed and self._queue.empty():
                raise StopAsyncIteration
            get_task = asyncio.create_task(self._queue.get())
            fail_task = asyncio.create_task(self._fail_event.wait())
            self._wait_tasks.add(get_task)
            self._wait_tasks.add(fail_task)
            try:
                done, _pending = await asyncio.wait(
                    {get_task, fail_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if self._failure is not None:
                    raise self._failure
                if get_task not in done:
                    continue
                item = get_task.result()
                if item is None:
                    raise StopAsyncIteration
                return {
                    "run_id": self._run_id,
                    "events": [item],
                    "follow": True,
                    "seq": item.get("seq"),
                }
            finally:
                for task in (get_task, fail_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(get_task, fail_task, return_exceptions=True)
                self._wait_tasks.discard(get_task)
                self._wait_tasks.discard(fail_task)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        watcher = self._watcher
        if watcher is not None:
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, protocol.ProtocolError):
                pass
            except Exception:
                pass
            self._watcher = None
        self._fail_event.set()
        owner = getattr(self, "_owner_set", None)
        if owner is not None:
            owner.discard(self)
            self._owner_set = None

    def _fail(self, err: protocol.ProtocolError) -> None:
        if self._failure is None:
            self._failure = err
        self._fail_event.set()

    async def _watch(self) -> None:
        try:
            while not self._stop.is_set():
                events, next_cursor, _exhausted = _read_events_page(
                    self._run_dir, from_seq=self._cursor, limit=MAX_EVENTS_PAGE_LIMIT
                )
                for event in events:
                    try:
                        self._queue.put_nowait(event)
                    except asyncio.QueueFull:
                        self._fail(
                            protocol.ProtocolError(
                                protocol.EVENT_BACKLOG_EXCEEDED,
                                "event follow backlog exceeded",
                            )
                        )
                        return
                    self._cursor = max(self._cursor, int(event["seq"]))
                if admission.has_terminal_result(self._run_dir) and not events:
                    await self._queue.put(None)
                    return
                try:
                    await asyncio.wait_for(self._stop.wait(), self._idle)
                    return
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except protocol.ProtocolError as err:
            self._fail(err)
        except Exception:
            _LOGGER.exception("arsd: follow watcher failed for %s", self._run_id)
            self._fail(
                protocol.ProtocolError(protocol.INTERNAL, "event follow failed")
            )


def _read_events_page(
    run_dir: Path, *, from_seq: int, limit: int
) -> tuple[list[dict[str, Any]], int, bool]:
    path = run_dir / EVENTS_FILENAME
    if not path.is_file():
        return [], from_seq, True
    selected: list[dict[str, Any]] = []
    last_seq = from_seq
    try:
        with open(path, encoding="utf-8") as stream:
            for raw in stream:
                line_bytes = raw.encode("utf-8")
                if len(line_bytes) > MAX_EVENT_LINE_BYTES:
                    raise protocol.ProtocolError(
                        protocol.INTERNAL, "event evidence line exceeds bound"
                    )
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    raise protocol.ProtocolError(
                        protocol.INTERNAL, "event evidence is corrupt"
                    ) from None
                if not isinstance(event, dict):
                    raise protocol.ProtocolError(
                        protocol.INTERNAL, "event evidence is corrupt"
                    )
                seq = event.get("seq")
                if not isinstance(seq, int) or isinstance(seq, bool):
                    raise protocol.ProtocolError(
                        protocol.INTERNAL, "event evidence is corrupt"
                    )
                if seq <= from_seq:
                    continue
                if selected and seq <= last_seq:
                    raise protocol.ProtocolError(
                        protocol.INTERNAL, "event evidence is corrupt"
                    )
                selected.append(event)
                last_seq = seq
                if len(selected) >= limit:
                    break
    except protocol.ProtocolError:
        raise
    except OSError:
        raise protocol.ProtocolError(
            protocol.INTERNAL, "event evidence is unreadable"
        ) from None
    exhausted = len(selected) < limit
    return selected, last_seq, exhausted


class ArsdHandlers:
    """v1 operation handlers over durable admission + the in-process registry."""

    def __init__(
        self,
        *,
        session_store: SessionStore,
        event_store: EventStore,
        run_task_factory: RunTaskFactory | None = None,
        max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
        cancel_wait_seconds: float = 30.0,
        event_follow_queue_size: int = DEFAULT_EVENT_FOLLOW_QUEUE,
        events_page_limit: int = DEFAULT_EVENTS_PAGE_LIMIT,
        supervisor_root: Path | None = None,
        profile_registry: ProfileRegistry = DEFAULT_REGISTRY,
    ) -> None:
        self._session_store = session_store
        self._event_store = event_store
        self._cancel_wait = cancel_wait_seconds
        self._follow_queue = event_follow_queue_size
        self._page_limit = min(events_page_limit, MAX_EVENTS_PAGE_LIMIT)
        self.registry = RunRegistry(max_concurrent_runs=max_concurrent_runs)
        self._locks = admission.KeyedLocks()
        if run_task_factory is not None:
            self._factory = run_task_factory
        elif supervisor_root is not None:
            self._factory = default_run_task_factory(
                supervisor_root, registry=profile_registry
            )
        else:
            raise ValueError("run_task_factory or supervisor_root is required")
        self._follow_streams: weakref.WeakSet[EventFollowStream] = weakref.WeakSet()

    async def __call__(
        self, caller: server.AuthenticatedCaller, request: protocol.ParsedRequest
    ) -> HandlerResult:
        op = request.op
        if op in _PAYLOAD_FIELDS:
            _require_closed_payload(op, request.payload)
        if op == "server_info":
            return self._server_info()
        if op == "submit":
            return await self._submit(caller, request)
        if op == "run_status":
            return await self._run_status(caller, request.payload)
        if op == "run_events":
            return await self._run_events(caller, request.payload)
        if op == "run_cancel":
            return await self._run_cancel(caller, request.payload)
        if op == "session_status":
            return await self._session_status(caller, request.payload)
        if op == "session_list":
            return await self._session_list(caller)
        if op == "session_close":
            return await self._session_close(caller, request.payload)
        raise protocol.ProtocolError(protocol.UNKNOWN_OP, "unknown v1 op")

    async def disconnect(self, caller: server.AuthenticatedCaller) -> None:
        """Connection-local hook: never cancels Runs or foreign follow streams.

        Per-connection stream finalization is owned by the server ``_emit_stream``
        path. Handlers keep only a weak registry of live streams.
        """
        del caller
        return None

    async def aclose(self) -> None:
        await self.registry.cancel_all(wait_seconds=self._cancel_wait)
        streams = list(self._follow_streams)
        for stream in streams:
            await stream.aclose()
        self._follow_streams = weakref.WeakSet()

    def _server_info(self) -> dict[str, Any]:
        return {
            "version": PACKAGE_VERSION,
            "api_version": protocol.ARSD_API_VERSION,
            "limits": {
                "max_concurrent_runs": self.registry.max_concurrent_runs,
                "max_frame_bytes": protocol.MAX_FRAME_BYTES,
                "max_prompt_bytes": protocol.MAX_PROMPT_BYTES,
                "events_page_limit": self._page_limit,
                "event_follow_queue_size": self._follow_queue,
            },
        }

    def _require_owner(
        self, caller: server.AuthenticatedCaller, owner: str, namespace: str
    ) -> None:
        if (owner, namespace) not in caller.principal.owner_namespaces:
            raise protocol.ProtocolError(
                protocol.OWNER_MISMATCH,
                "owner/namespace is not allowed for this principal",
            )

    async def _submit(
        self, caller: server.AuthenticatedCaller, request: protocol.ParsedRequest
    ) -> dict[str, Any]:
        command = protocol.parse_submit(request.payload)
        self._require_owner(
            caller, command.request.owner, command.request.namespace
        )
        try:
            key = admission.AdmissionKey(
                principal_id=caller.principal.principal_id,
                request_id=request.request_id,
            )
        except ValueError as exc:
            raise protocol.ProtocolError(protocol.INVALID_REQUEST, str(exc)) from None
        digest = admission.compute_request_digest(command)
        run_id = admission.derive_run_id(key)
        lock_key = (key.principal_id, key.request_id)

        async with self._locks.hold(lock_key):
            return await self._submit_locked(
                caller=caller,
                key=key,
                command=command,
                digest=digest,
                run_id=run_id,
            )

    async def _submit_locked(
        self,
        *,
        caller: server.AuthenticatedCaller,
        key: admission.AdmissionKey,
        command: protocol.SubmitCommand,
        digest: admission.RequestDigest,
        run_id: str,
    ) -> dict[str, Any]:
        run_dir = Path(self._event_store.base_dir) / run_id
        resolved = self._resolve_durable(key, digest, run_id, run_dir)
        if resolved is not None:
            return resolved

        session_id = self._tracked_session_id(command)
        reservation = await self.registry.reserve(session_id=session_id)
        handle = None
        consumed = False
        try:
            try:
                handle = admission.prepare_run(self._event_store, run_id)
            except EventStoreError:
                raise protocol.ProtocolError(
                    protocol.INTERNAL, "run reservation failed"
                ) from None
            except Exception:
                raise protocol.ProtocolError(
                    protocol.INTERNAL, "run reservation failed"
                ) from None

            accepted_at = _utc_now_iso()
            peer = caller.peer_credentials
            artifact = admission.build_submission_artifact(
                key=key,
                run_id=run_id,
                command=command,
                digest=digest,
                accepted_at=accepted_at,
                peer={"pid": peer.pid, "uid": peer.uid, "gid": peer.gid},
            )
            try:
                admission.write_submission(handle.run_dir, artifact)
            except Exception:
                raise protocol.ProtocolError(
                    protocol.INTERNAL, "submission persistence failed"
                ) from None

            try:
                runner = self._factory(
                    command=command,
                    run_id=run_id,
                    prepared_handle=handle,
                    submitted_at=accepted_at,
                )
            except Exception:
                try:
                    admission.finalize_registration_failure(handle, run_id)
                except Exception:
                    _LOGGER.exception(
                        "arsd: registration failure finalization failed for %s", run_id
                    )
                raise protocol.ProtocolError(
                    protocol.INTERNAL, "run registration failed"
                ) from None

            try:
                await self.registry.register(
                    run_id,
                    runner,
                    reservation=reservation,
                    principal_id=key.principal_id,
                    owner=command.request.owner,
                    namespace=command.request.namespace,
                    session_id=session_id,
                    submitted_at=accepted_at,
                )
            except protocol.ProtocolError:
                try:
                    admission.finalize_registration_failure(handle, run_id)
                except Exception:
                    _LOGGER.exception(
                        "arsd: registration failure finalization failed for %s", run_id
                    )
                raise
            except Exception:
                try:
                    admission.finalize_registration_failure(handle, run_id)
                except Exception:
                    _LOGGER.exception(
                        "arsd: registration failure finalization failed for %s", run_id
                    )
                raise protocol.ProtocolError(
                    protocol.INTERNAL, "run registration failed"
                ) from None
            consumed = True
            return {"run_id": run_id, "accepted_at": accepted_at}
        finally:
            if not consumed:
                await self.registry.release(reservation)

    def _resolve_durable(
        self,
        key: admission.AdmissionKey,
        digest: admission.RequestDigest,
        run_id: str,
        run_dir: Path,
    ) -> dict[str, Any] | None:
        if not run_dir.exists():
            return None
        # Refuse symlinked run-dir entries before any artifact reads.
        if (Path(self._event_store.base_dir) / run_id).is_symlink():
            raise protocol.ProtocolError(
                protocol.SUBMISSION_INDETERMINATE,
                "run reservation lacks a valid submission binding",
            )
        submission = admission.read_submission(run_dir)
        if submission is None:
            raise protocol.ProtocolError(
                protocol.SUBMISSION_INDETERMINATE,
                "run reservation lacks a valid submission binding",
            )
        if not admission.submission_binds_key(submission, key):
            raise protocol.ProtocolError(
                protocol.SUBMISSION_INDETERMINATE,
                "submission binding integrity failure",
            )
        if submission.get("run_id") != run_id:
            raise protocol.ProtocolError(
                protocol.SUBMISSION_INDETERMINATE,
                "submission binding integrity failure",
            )
        if submission.get("request_digest") != digest.value:
            raise protocol.ProtocolError(
                protocol.IDEMPOTENCY_CONFLICT,
                "request_id already bound to a different request digest",
            )
        registered = self.registry.is_registered(run_id)
        terminal = admission.has_terminal_result(run_dir)
        if registered or terminal:
            return {
                "run_id": run_id,
                "accepted_at": submission["accepted_at"],
            }
        raise protocol.ProtocolError(
            protocol.SUBMISSION_INDETERMINATE,
            "submission is durable but run registration is incomplete",
        )

    def _tracked_session_id(self, command: protocol.SubmitCommand) -> str | None:
        if command.request.session_reuse == "reuse":
            return command.request.ars_session_id
        return None

    def _authorize_run(
        self, caller: server.AuthenticatedCaller, run_id: Any
    ) -> tuple[Path, dict[str, Any] | None]:
        if not isinstance(run_id, str) or not run_id:
            raise protocol.ProtocolError(protocol.INVALID_REQUEST, "run_id is required")
        if not admission.is_safe_run_id(run_id):
            raise protocol.ProtocolError(protocol.INVALID_REQUEST, "run_id is invalid")
        run_entry = Path(self._event_store.base_dir) / run_id
        if run_entry.is_symlink():
            raise protocol.ProtocolError(protocol.UNKNOWN_RUN, "unknown run")
        run_dir = run_entry
        if not run_dir.is_dir():
            raise protocol.ProtocolError(protocol.UNKNOWN_RUN, "unknown run")
        submission = admission.read_submission(run_dir)
        owner: str | None = None
        namespace: str | None = None
        if submission is not None:
            owner = submission.get("owner")
            namespace = submission.get("namespace")
            spec_path = run_dir / "spec.json"
            if spec_path.is_file():
                try:
                    spec = json.loads(spec_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    raise protocol.ProtocolError(
                        protocol.UNKNOWN_RUN, "unknown run"
                    ) from None
                identity = spec.get("identity") if isinstance(spec, dict) else None
                if isinstance(identity, dict):
                    spec_owner = identity.get("owner")
                    spec_namespace = identity.get("namespace")
                    if (spec_owner, spec_namespace) != (owner, namespace):
                        raise protocol.ProtocolError(
                            protocol.UNKNOWN_RUN, "unknown run"
                        )
        else:
            spec_path = run_dir / "spec.json"
            if not spec_path.is_file():
                raise protocol.ProtocolError(protocol.UNKNOWN_RUN, "unknown run")
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                raise protocol.ProtocolError(protocol.UNKNOWN_RUN, "unknown run") from None
            identity = spec.get("identity") if isinstance(spec, dict) else None
            if not isinstance(identity, dict):
                raise protocol.ProtocolError(protocol.UNKNOWN_RUN, "unknown run")
            owner = identity.get("owner")
            namespace = identity.get("namespace")
        if not isinstance(owner, str) or not isinstance(namespace, str):
            raise protocol.ProtocolError(protocol.UNKNOWN_RUN, "unknown run")
        if (owner, namespace) not in caller.principal.owner_namespaces:
            raise protocol.ProtocolError(
                protocol.OWNER_MISMATCH,
                "owner/namespace is not allowed for this principal",
            )
        return run_dir, submission

    async def _run_status(
        self, caller: server.AuthenticatedCaller, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        run_dir, submission = self._authorize_run(caller, payload.get("run_id"))
        progress = admission.read_progress(run_dir)
        result = admission.read_result(run_dir)
        run_id = payload.get("run_id")
        if progress is None and result is None:
            accepted_at = (
                submission.get("accepted_at") if submission is not None else None
            )
            return {
                "run_id": run_id,
                "state": "accepted",
                "accepted_at": accepted_at,
            }
        body: dict[str, Any] = {"run_id": run_id}
        if progress is not None:
            body["progress"] = progress
        if result is not None:
            body["result"] = result
        return body

    async def _run_events(
        self, caller: server.AuthenticatedCaller, payload: Mapping[str, Any]
    ) -> HandlerResult:
        run_dir, _submission = self._authorize_run(caller, payload.get("run_id"))
        from_seq = payload.get("from_seq", 0)
        if isinstance(from_seq, bool) or not isinstance(from_seq, int) or from_seq < 0:
            raise protocol.ProtocolError(
                protocol.INVALID_REQUEST, "from_seq must be a non-negative integer"
            )
        limit = payload.get("limit", self._page_limit)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise protocol.ProtocolError(
                protocol.INVALID_REQUEST, "limit must be a positive integer"
            )
        limit = min(limit, MAX_EVENTS_PAGE_LIMIT)
        follow = payload.get("follow", False)
        if follow is not False and follow is not True:
            raise protocol.ProtocolError(
                protocol.INVALID_REQUEST, "follow must be a boolean"
            )
        run_id = str(payload.get("run_id"))
        if not follow:
            events, next_from_seq, exhausted = _read_events_page(
                run_dir, from_seq=from_seq, limit=limit
            )
            return {
                "run_id": run_id,
                "events": events,
                "next_from_seq": next_from_seq,
                "exhausted": exhausted,
            }
        idle = payload.get("follow_idle_seconds", DEFAULT_FOLLOW_IDLE_SECONDS)
        if isinstance(idle, bool) or not isinstance(idle, (int, float)):
            raise protocol.ProtocolError(
                protocol.INVALID_REQUEST,
                "follow_idle_seconds must be a positive finite number "
                f"at most {int(MAX_FOLLOW_IDLE_SECONDS)} seconds",
            )
        # Integers: exact numeric compare — never float() huge decoded JSON ints.
        if type(idle) is int:
            if idle <= 0 or idle > MAX_FOLLOW_IDLE_SECONDS:
                raise protocol.ProtocolError(
                    protocol.INVALID_REQUEST,
                    "follow_idle_seconds must be a positive finite number "
                    f"at most {int(MAX_FOLLOW_IDLE_SECONDS)} seconds",
                )
            idle_seconds = float(idle)
        else:
            if not math.isfinite(idle) or idle <= 0 or idle > MAX_FOLLOW_IDLE_SECONDS:
                raise protocol.ProtocolError(
                    protocol.INVALID_REQUEST,
                    "follow_idle_seconds must be a positive finite number "
                    f"at most {int(MAX_FOLLOW_IDLE_SECONDS)} seconds",
                )
            idle_seconds = float(idle)
        stream = EventFollowStream(
            run_dir=run_dir,
            run_id=run_id,
            from_seq=from_seq,
            idle_seconds=idle_seconds,
            queue_size=self._follow_queue,
        )
        stream._owner_set = self._follow_streams
        self._follow_streams.add(stream)
        return stream

    async def _run_cancel(
        self, caller: server.AuthenticatedCaller, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        run_dir, _submission = self._authorize_run(caller, payload.get("run_id"))
        run_id = str(payload.get("run_id"))
        await self.registry.cancel(run_id, wait_seconds=self._cancel_wait)
        for _ in range(50):
            if not self.registry.is_registered(run_id):
                break
            await asyncio.sleep(0.01)
        result = admission.read_result(run_dir)
        body: dict[str, Any] = {"run_id": run_id}
        if result is not None:
            body["result"] = result
            body["status"] = result.get("status")
        return body

    def _authorize_session(
        self, caller: server.AuthenticatedCaller, session_id: Any
    ):
        if not isinstance(session_id, str) or not session_id:
            raise protocol.ProtocolError(
                protocol.INVALID_REQUEST, "session_id is required"
            )
        try:
            record = self._session_store.open_session(session_id)
        except SessionNotFoundError:
            raise protocol.ProtocolError(
                protocol.UNKNOWN_SESSION, "unknown session"
            ) from None
        owner = record.owner
        namespace = record.namespace
        if not isinstance(owner, str) or not isinstance(namespace, str):
            raise protocol.ProtocolError(protocol.UNKNOWN_SESSION, "unknown session")
        if (owner, namespace) not in caller.principal.owner_namespaces:
            raise protocol.ProtocolError(
                protocol.OWNER_MISMATCH,
                "owner/namespace is not allowed for this principal",
            )
        return record

    def _session_view(self, record) -> dict[str, Any]:
        return {
            "session_id": record.session_id,
            "state": storage.to_native_state(record.state),
            "owner": record.owner,
            "namespace": record.namespace,
            "profile_id": record.native_profile_id,
            "updated_at": record.updated_at,
        }

    async def _session_status(
        self, caller: server.AuthenticatedCaller, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        record = self._authorize_session(caller, payload.get("session_id"))
        return self._session_view(record)

    async def _session_list(
        self, caller: server.AuthenticatedCaller
    ) -> dict[str, Any]:
        allowed = caller.principal.owner_namespaces
        sessions = []
        for record in self._session_store.list_records():
            if record.owner is None or record.namespace is None:
                continue
            if (record.owner, record.namespace) not in allowed:
                continue
            sessions.append(self._session_view(record))
        return {"sessions": sessions}

    async def _session_close(
        self, caller: server.AuthenticatedCaller, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        record = self._authorize_session(caller, payload.get("session_id"))
        try:
            closed = self._session_store.mark_closed(record.session_id)
        except SessionQuarantinedError:
            raise protocol.ProtocolError(
                protocol.INVALID_REQUEST,
                "quarantined session cannot be closed",
            ) from None
        except Exception:
            raise protocol.ProtocolError(
                protocol.INVALID_REQUEST, "session cannot be closed"
            ) from None
        return self._session_view(closed)

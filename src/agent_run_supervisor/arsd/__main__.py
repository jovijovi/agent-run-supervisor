"""Unprivileged arsd daemon entrypoint: ``python -m agent_run_supervisor.arsd``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import logging
import os
import signal
import stat
import sys
from pathlib import Path
from typing import Any

from agent_run_supervisor.arsd import handlers, reconcile, server
from agent_run_supervisor.arsd.server import (
    DEFAULT_MAX_CONNECTIONS,
    CallerPolicy,
    Principal,
)
from agent_run_supervisor.arsd.service_unit import ServiceUnitError, render_service_unit
from agent_run_supervisor.event_store import (
    DIR_MODE,
    FILE_MODE,
    EventStoreError,
    durable_secure_mkdir,
)
from agent_run_supervisor.native_acp import storage

_LOGGER = logging.getLogger("agent_run_supervisor.arsd")

DEFAULT_SHUTDOWN_TIMEOUT = 90.0
DEFAULT_CANCEL_WAIT_SECONDS = 30.0
# Finite nonzero delay between ordinary lifecycle retries (no busy spin).
_SHUTDOWN_LIFECYCLE_RETRY_DELAY = 0.05
_DAEMON_LOCK_DIRNAME = "arsd"
_DAEMON_LOCK_FILENAME = "daemon.lock"
_SHUTDOWN_LIFECYCLE_FAIL_LOG = (
    "arsd: shutdown lifecycle failed; holding lease and retrying"
)


class DaemonStartupError(RuntimeError):
    """Fail-closed daemon startup refusal; nothing is listening."""


class DaemonInstanceLease:
    """Exclusive per-supervisor-root daemon ownership via Linux advisory flock.

    The lock file is opened O_CREAT|O_NOFOLLOW at 0600 relative to a verified
    non-symlink lock directory fd, and never unlinked on release — close/crash
    drops the kernel flock without an inode race.
    """

    def __init__(self, lock_path: Path, fd: int) -> None:
        self.lock_path = Path(lock_path)
        self._fd = fd
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        fd = self._fd
        self._fd = -1
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _open_lock_dir_fd(lock_dir: Path) -> int:
    """Open ``lock_dir`` as a real non-symlink directory fd (0700).

    Callers must already have published ``lock_dir`` via
    :func:`durable_secure_mkdir` — this helper never creates directories.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        dir_fd = os.open(lock_dir, flags)
    except OSError as exc:
        raise DaemonStartupError("failed to prepare daemon instance lease") from exc
    try:
        st = os.fstat(dir_fd)
        if not stat.S_ISDIR(st.st_mode):
            raise DaemonStartupError("failed to prepare daemon instance lease")
        try:
            os.fchmod(dir_fd, DIR_MODE)
        except OSError as exc:
            raise DaemonStartupError("failed to prepare daemon instance lease") from exc
    except DaemonStartupError:
        _close_fd_best_effort(dir_fd)
        raise
    except OSError as exc:
        _close_fd_best_effort(dir_fd)
        raise DaemonStartupError("failed to prepare daemon instance lease") from exc
    return dir_fd


def _close_fd_best_effort(fd: int) -> None:
    if fd < 0:
        return
    with contextlib.suppress(OSError):
        os.close(fd)


def _release_lock_fd(fd: int) -> None:
    """Drop flock and close; never raises (failure paths must stay sanitized)."""
    if fd < 0:
        return
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    _close_fd_best_effort(fd)


def acquire_daemon_instance_lease(supervisor_root: Path | str) -> DaemonInstanceLease:
    """Acquire the race-safe singleton lease for ``supervisor_root``.

    Publishes the supervisor root and lease-parent directory with
    :func:`durable_secure_mkdir` (0700 + dir/parent fsync) before any flock,
    reconcile, or socket setup. Symlink/non-dir/fsync failures sanitize and
    abort — no unfynced lease directory is created.
    """
    root = Path(supervisor_root)
    lock_dir = root / _DAEMON_LOCK_DIRNAME
    lock_path = lock_dir / _DAEMON_LOCK_FILENAME
    try:
        durable_secure_mkdir(root)
        durable_secure_mkdir(lock_dir)
    except EventStoreError as exc:
        raise DaemonStartupError("failed to prepare daemon instance lease") from exc

    dir_fd = _open_lock_dir_fd(lock_dir)
    fd = -1
    try:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(_DAEMON_LOCK_FILENAME, flags, FILE_MODE, dir_fd=dir_fd)
        except OSError as exc:
            raise DaemonStartupError("failed to open daemon instance lease") from exc
        try:
            os.fchmod(fd, FILE_MODE)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            _release_lock_fd(fd)
            fd = -1
            raise DaemonStartupError(
                "another arsd daemon already holds the lease for this supervisor root"
            ) from exc
        except OSError as exc:
            _release_lock_fd(fd)
            fd = -1
            raise DaemonStartupError(
                "failed to acquire daemon instance lease"
            ) from exc
    except BaseException:
        # Close dirfd without masking the sanitized acquire error as raw OSError.
        _close_fd_best_effort(dir_fd)
        if fd >= 0:
            _release_lock_fd(fd)
            fd = -1
        raise

    try:
        os.close(dir_fd)
    except OSError as exc:
        _release_lock_fd(fd)
        fd = -1
        raise DaemonStartupError(
            "failed to prepare daemon instance lease"
        ) from exc
    return DaemonInstanceLease(lock_path, fd)


def geteuid() -> int:
    """Indirection so hermetic tests can patch effective UID without root/sudo."""
    return os.geteuid()


def default_socket_path(supervisor_root: Path | str) -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "agent-run-supervisor" / "arsd.sock"
    return Path(supervisor_root) / "arsd" / "arsd.sock"


def parse_caller_mapping(value: str) -> tuple[int, Principal]:
    """Parse ``UID:principal_id:owner:namespace`` (namespace may contain ``:``/``/``)."""
    if not isinstance(value, str) or not value:
        raise ValueError("caller mapping must be a non-empty string")
    parts = value.split(":", 3)
    if len(parts) != 4:
        raise ValueError(
            "caller mapping must be UID:principal_id:owner:namespace"
        )
    uid_text, principal_id, owner, namespace = parts
    try:
        uid = int(uid_text)
    except ValueError as exc:
        raise ValueError("caller mapping UID must be an integer") from exc
    if uid < 0:
        raise ValueError("caller mapping UID must be non-negative")
    if not principal_id or not owner or not namespace:
        raise ValueError("caller mapping principal/owner/namespace must be non-empty")
    return uid, Principal(
        principal_id=principal_id,
        owner_namespaces=frozenset({(owner, namespace)}),
    )


def build_caller_policy(values: list[str]) -> CallerPolicy:
    by_uid: dict[int, Principal] = {}
    for raw in values:
        uid, principal = parse_caller_mapping(raw)
        existing = by_uid.get(uid)
        if existing is None:
            by_uid[uid] = principal
            continue
        if existing.principal_id != principal.principal_id:
            raise ValueError(
                "caller mapping UID cannot bind to conflicting principal_id values"
            )
        by_uid[uid] = Principal(
            principal_id=existing.principal_id,
            owner_namespaces=existing.owner_namespaces | principal.owner_namespaces,
        )
    return CallerPolicy(by_uid)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent_run_supervisor.arsd",
        description="Local unprivileged arsd Unix-domain-socket daemon",
    )
    parser.add_argument(
        "--print-service-unit",
        action="store_true",
        help=(
            "Render a systemd --user unit to stdout and exit. "
            "Does not check euid, reconcile, bind, or start the daemon. "
            "Optional --socket/--supervisor-root/--caller-mapping override "
            "user-scope specifier defaults; zero mappings remain fail-closed."
        ),
    )
    parser.add_argument(
        "--supervisor-root",
        type=Path,
        default=None,
        help=(
            "Native supervisor root (native-runs/ + native-sessions/). "
            "Required for daemon mode; optional for --print-service-unit."
        ),
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=None,
        help=(
            "AF_UNIX socket path (default: "
            "$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock or "
            "<supervisor_root>/arsd/arsd.sock; print mode uses %%t/... )"
        ),
    )
    parser.add_argument(
        "--caller-mapping",
        action="append",
        default=[],
        metavar="UID:principal_id:owner:namespace",
        help=(
            "Explicit peer-UID→principal mapping. Repeatable. "
            "Zero mappings refuse to listen in daemon mode."
        ),
    )
    parser.add_argument(
        "--max-concurrent-runs",
        type=int,
        default=handlers.DEFAULT_MAX_CONCURRENT_RUNS,
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=DEFAULT_MAX_CONNECTIONS,
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
    )

    # Preserve Slice-5 fail-closed argparse contract (empty argv → SystemExit)
    # while allowing ``--print-service-unit`` alone for wheel smoke / A3 export.
    _orig_parse_args = parser.parse_args

    def parse_args(
        args: list[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        ns = _orig_parse_args(args, namespace)
        if not ns.print_service_unit and ns.supervisor_root is None:
            parser.error("--supervisor-root is required unless --print-service-unit")
        return ns

    parser.parse_args = parse_args  # type: ignore[method-assign]
    return parser


async def _shutdown_and_release_lease(
    srv: server.ArsdServer | None,
    arsd_handlers: handlers.ArsdHandlers,
    lease: DaemonInstanceLease,
    *,
    shutdown_timeout: float,
) -> None:
    """Owned cancel-resistant shutdown: admission → listener → drain → idle → lease.

    Every exit after handlers exist — normal stop, timeout, failure, or
    cancellation during ``local_stop.wait()`` — runs this single lifecycle.
    Caller cancellation is classified before target-done race, absorbed via
    ``uncancel``, and re-raised only after successful shutdown + idle + lease
    release. Ordinary lifecycle/phase failure never authorizes release: admission
    stays closed, the lease stays held, a fixed categorical error is logged, and
    the idempotent lifecycle retries with a finite nonzero delay until every
    phase completes (or the external process is killed). Bounded timeout is an
    escalation log only; it never cancels this lifecycle or releases the lease
    early. No detached task, busy spin, or swallowed ordinary failure.
    """
    cancelled = False

    def _absorb_cancel() -> None:
        nonlocal cancelled
        cancelled = True
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            task.uncancel()

    def _observe_owned_done() -> None:
        # owned completes only after positive lifecycle success; never convert
        # an ordinary failure into release authorization.
        owned.result()

    async def _run_lifecycle_once() -> None:
        # 1) Close registry admission before any later reserve/register.
        await arsd_handlers.registry.close_admission()
        # 2) Close listener / accepted connections (SHUTTING_DOWN on late frames).
        if srv is not None:
            await srv.shutdown()
            # Short straggler window for already-accepted connections.
            await asyncio.sleep(0.15)
            await srv.aclose()
        # 3) Cancel/drain handlers and Run tasks.
        await arsd_handlers.aclose()
        # 4) Wait until the registry is idle before lease release.
        await arsd_handlers.registry.wait_until_idle()

    async def _owned() -> None:
        # Retry until the ordered lifecycle completes successfully. Timeout
        # observes only — never cancels the lifecycle or releases the lease.
        bound_logged = False
        while True:
            lifecycle = asyncio.create_task(
                _run_lifecycle_once(), name="arsd:shutdown-lifecycle"
            )
            try:
                if not bound_logged:
                    await asyncio.wait_for(
                        asyncio.shield(lifecycle), timeout=shutdown_timeout
                    )
                else:
                    await asyncio.shield(lifecycle)
            except asyncio.TimeoutError:
                _LOGGER.error("arsd: shutdown exceeded bound; forcing exit path")
                bound_logged = True
            except (Exception, asyncio.CancelledError):
                # Ordinary/cancel outcomes are evaluated via lifecycle.result()
                # below — never treated as success here.
                pass
            while not lifecycle.done():
                try:
                    await asyncio.shield(lifecycle)
                except (Exception, asyncio.CancelledError):
                    pass
            try:
                lifecycle.result()
            except (Exception, asyncio.CancelledError):
                _LOGGER.error(_SHUTDOWN_LIFECYCLE_FAIL_LOG)
                await asyncio.sleep(_SHUTDOWN_LIFECYCLE_RETRY_DELAY)
                continue
            return

    owned = asyncio.create_task(_owned(), name="arsd:shutdown-owned")
    try:
        while True:
            try:
                await asyncio.shield(owned)
                break
            except asyncio.CancelledError:
                # Classify by caller cancelling() first: same-turn owned
                # completion must not be mistaken for target-only cancel.
                me = asyncio.current_task()
                if me is not None and me.cancelling():
                    _absorb_cancel()
                    if owned.done():
                        _observe_owned_done()
                        break
                    continue
                if owned.done():
                    _observe_owned_done()
                    break
                _absorb_cancel()
    finally:
        # Never detach: keep shielding the owned lifecycle until it finishes.
        while not owned.done():
            try:
                await asyncio.shield(owned)
            except asyncio.CancelledError:
                me = asyncio.current_task()
                if me is not None and me.cancelling():
                    _absorb_cancel()
                    if owned.done():
                        _observe_owned_done()
                        break
                    continue
                if owned.done():
                    _observe_owned_done()
                    break
                _absorb_cancel()

    # Positive proof: owned returned only after full successful lifecycle.
    _observe_owned_done()
    lease.release()
    if cancelled:
        raise asyncio.CancelledError()


async def serve_daemon(
    *,
    socket_path: Path | str,
    supervisor_root: Path | str,
    policy: CallerPolicy,
    max_concurrent_runs: int = handlers.DEFAULT_MAX_CONCURRENT_RUNS,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    run_task_factory: Any | None = None,
    cancel_wait_seconds: float = DEFAULT_CANCEL_WAIT_SECONDS,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    stop_event: asyncio.Event | None = None,
    install_signals: bool = True,
) -> int:
    """Reconcile → listen → serve until signal/stop; graceful bounded exit."""
    if geteuid() == 0:
        raise DaemonStartupError(
            "refusing to start arsd with effective UID 0 (no root service)"
        )
    if len(policy) == 0:
        raise DaemonStartupError(
            "caller policy has zero configured caller mappings; refusing to listen"
        )

    root = Path(supervisor_root)
    path = Path(socket_path)

    # Exclusive ownership before any reconciliation mutation or listen.
    lease = acquire_daemon_instance_lease(root)
    arsd_handlers: handlers.ArsdHandlers | None = None
    srv: server.ArsdServer | None = None
    try:
        # Startup reconciliation must complete successfully before bind/listen.
        try:
            reconcile.reconcile(root)
        except reconcile.ReconciliationError as err:
            raise DaemonStartupError(str(err)) from err

        session_store = storage.native_session_store(root)
        event_store = storage.native_event_store(root)
        handler_kwargs: dict[str, Any] = {
            "session_store": session_store,
            "event_store": event_store,
            "max_concurrent_runs": max_concurrent_runs,
            "cancel_wait_seconds": cancel_wait_seconds,
        }
        if run_task_factory is not None:
            handler_kwargs["run_task_factory"] = run_task_factory
        else:
            handler_kwargs["supervisor_root"] = root
        arsd_handlers = handlers.ArsdHandlers(**handler_kwargs)
        if run_task_factory is not None and hasattr(run_task_factory, "handlers"):
            run_task_factory.handlers = arsd_handlers

        try:
            srv = server.ArsdServer(
                socket_path=path,
                policy=policy,
                handler=arsd_handlers,
                max_connections=max_connections,
            )

            local_stop = stop_event if stop_event is not None else asyncio.Event()
            loop = asyncio.get_running_loop()
            handlers_installed: list[signal.Signals] = []

            def _request_stop() -> None:
                local_stop.set()

            if install_signals:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    try:
                        loop.add_signal_handler(sig, _request_stop)
                        handlers_installed.append(sig)
                    except (NotImplementedError, RuntimeError):
                        _LOGGER.warning(
                            "arsd: signal handler unavailable for %s", sig
                        )

            try:
                try:
                    await srv.start()
                except server.ServerStartupError as err:
                    raise DaemonStartupError(str(err)) from err

                await local_stop.wait()
                return 0
            finally:
                for sig in handlers_installed:
                    with contextlib.suppress(Exception):
                        loop.remove_signal_handler(sig)
        finally:
            # Fail-closed: once handlers/registry exist, every exit path —
            # including cancellation during stop wait — owns one shutdown
            # lifecycle before lease release. Timeout cannot bypass it.
            await _shutdown_and_release_lease(
                srv,
                arsd_handlers,
                lease,
                shutdown_timeout=shutdown_timeout,
            )
    finally:
        # Startup failures before registry creation may release normally.
        if arsd_handlers is None:
            lease.release()


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # Print mode exits before euid check, reconciliation, socket bind,
    # service/process creation, or caller-policy admission.
    if args.print_service_unit:
        try:
            unit = render_service_unit(
                socket_path=None if args.socket is None else str(args.socket),
                supervisor_root=(
                    None if args.supervisor_root is None else str(args.supervisor_root)
                ),
                caller_mappings=tuple(args.caller_mapping or ()),
                python_executable=sys.executable,
            )
        except ServiceUnitError as err:
            print(f"arsd: invalid service unit: {err}", file=sys.stderr)
            return 2
        sys.stdout.write(unit)
        if not unit.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.supervisor_root is None:
        print(
            "arsd: refusing to start without --supervisor-root",
            file=sys.stderr,
        )
        return 2
    root = Path(args.supervisor_root)
    socket_path = (
        Path(args.socket) if args.socket is not None else default_socket_path(root)
    )
    try:
        policy = build_caller_policy(list(args.caller_mapping or []))
    except ValueError as err:
        print(f"arsd: invalid caller mapping: {err}", file=sys.stderr)
        return 2
    if len(policy) == 0:
        print(
            "arsd: refusing to start with zero caller mappings",
            file=sys.stderr,
        )
        return 2
    try:
        return asyncio.run(
            serve_daemon(
                socket_path=socket_path,
                supervisor_root=root,
                policy=policy,
                max_concurrent_runs=args.max_concurrent_runs,
                max_connections=args.max_connections,
                install_signals=True,
            )
        )
    except DaemonStartupError as err:
        print(f"arsd: {err}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 — top-level CLI boundary
        print("arsd: startup failed", file=sys.stderr)
        _LOGGER.exception("arsd: unexpected startup failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())

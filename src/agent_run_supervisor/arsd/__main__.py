"""Unprivileged arsd daemon entrypoint: ``python -m agent_run_supervisor.arsd``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from agent_run_supervisor.arsd import handlers, reconcile, server
from agent_run_supervisor.arsd.server import (
    DEFAULT_MAX_CONNECTIONS,
    CallerPolicy,
    Principal,
)
from agent_run_supervisor.native_acp import storage

_LOGGER = logging.getLogger("agent_run_supervisor.arsd")

DEFAULT_SHUTDOWN_TIMEOUT = 90.0
DEFAULT_CANCEL_WAIT_SECONDS = 30.0


class DaemonStartupError(RuntimeError):
    """Fail-closed daemon startup refusal; nothing is listening."""


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
        "--supervisor-root",
        type=Path,
        required=True,
        help="Native supervisor root (native-runs/ + native-sessions/)",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=None,
        help=(
            "AF_UNIX socket path (default: "
            "$XDG_RUNTIME_DIR/agent-run-supervisor/arsd.sock or "
            "<supervisor_root>/arsd/arsd.sock)"
        ),
    )
    parser.add_argument(
        "--caller-mapping",
        action="append",
        default=[],
        metavar="UID:principal_id:owner:namespace",
        help=(
            "Explicit peer-UID→principal mapping. Repeatable. "
            "Zero mappings refuse to listen."
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
    return parser


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

    # Startup reconciliation must complete successfully before bind/listen.
    reconcile.reconcile(root)

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
                _LOGGER.warning("arsd: signal handler unavailable for %s", sig)

    try:
        try:
            await srv.start()
        except server.ServerStartupError as err:
            raise DaemonStartupError(str(err)) from err

        await local_stop.wait()

        try:
            await asyncio.wait_for(
                _graceful_shutdown(srv, arsd_handlers),
                timeout=shutdown_timeout,
            )
        except asyncio.TimeoutError:
            _LOGGER.error("arsd: shutdown exceeded bound; forcing exit path")
            with contextlib.suppress(Exception):
                await srv.aclose()
            with contextlib.suppress(Exception):
                await arsd_handlers.aclose()
        return 0
    finally:
        for sig in handlers_installed:
            with contextlib.suppress(Exception):
                loop.remove_signal_handler(sig)


async def _graceful_shutdown(
    srv: server.ArsdServer, arsd_handlers: handlers.ArsdHandlers
) -> None:
    # Stop accepting first so late frames can still observe SHUTTING_DOWN.
    await srv.shutdown()
    # Short straggler window for already-accepted connections.
    await asyncio.sleep(0.15)
    await arsd_handlers.aclose()
    await srv.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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

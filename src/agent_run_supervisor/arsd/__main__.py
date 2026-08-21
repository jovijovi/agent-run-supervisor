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
from pathlib import Path, PurePosixPath
from typing import Any

from agent_run_supervisor.arsd import handlers, reconcile, server
from agent_run_supervisor.arsd.operand import (
    OperandError,
    admit_exact_text,
    capture_agents_file,
)
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
from agent_run_supervisor.native_acp import agent_registry, storage
from agent_run_supervisor.native_acp.spec import (
    DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES,
    EventBudgetPolicy,
    NativeSpecError,
)

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


def parse_agents_file(value: str) -> str:
    """argparse adapter for :func:`~agent_run_supervisor.arsd.operand.capture_agents_file`.

    argparse hands over the exact built-in ``str`` it read from argv, so the gate
    admits it and returns the frozen text — kept as ``str`` rather than rebuilt
    as a ``Path``, so the operator's spelling reaches the rendered unit and the
    daemon byte-for-byte.
    """
    try:
        return capture_agents_file(value)
    except OperandError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _is_lexically_absolute(value: Path | str) -> bool:
    """POSIX-absolute judged from text alone: no cwd, resolve, abspath, or stat.

    A relative daemon-owned path names no fixed location until the kernel joins
    it to whatever working directory the unit happened to inherit, so
    ``relative-sv`` and ``<cwd>/relative-sv`` can be one directory while sharing
    no text at all — and :func:`_binding_query_conflict`, which compares
    component chains as text, would call them disjoint. Anchoring the relative
    spelling to the cwd would *invent* the daemon-owned location this boundary
    exists to avoid guessing, so a non-absolute spelling is refused instead.
    ``Path("")`` stringifies to ``"."`` and is refused with everything else.

    ``//srv/x`` stays absolute: Linux resolves it to ``/srv/x``, and
    ``_collapse_leading_slashes`` already makes the two spellings comparable.
    """
    return str(value).startswith("/")


def _collapse_leading_slashes(text: str) -> str:
    """Reduce a leading run of separators to exactly one. Pure string work.

    POSIX leaves a path beginning with *exactly* two slashes
    implementation-defined and ``os.path.normpath`` preserves it, but Linux
    resolves ``//srv/x`` and ``/srv/x`` to the same directory. ``pathlib`` gives
    the two spellings different anchors, so neither ``==`` nor
    ``is_relative_to`` sees through the alias on its own.
    """
    stripped = text.lstrip("/")
    return "/" + stripped if len(stripped) != len(text) else text


def _lexical_is_filesystem_root(value: Path | str) -> bool:
    """Does this spelling name ``/`` itself? Pure string work.

    Needed as its own question because the root is the one path whose component
    set is *empty* while its reach is total: ``/`` contains, and is contained
    by, every other path. Spelling matters — ``/srv/..`` is the root but leaves
    ``/srv`` behind in the as-written walk, so the set is non-empty and only the
    collapsed spelling reveals what was actually named.
    """
    raw = _collapse_leading_slashes(str(value))
    return _collapse_leading_slashes(os.path.normpath(raw)) == "/"


def _lexical_query_components(value: Path | str) -> frozenset[str]:
    """Every non-root path a filesystem operation on ``value`` may query.

    Containment was the wrong question. Asking the kernel for ``/x/sv`` — to
    lstat it, mkdir it, open it, or resolve it — makes the kernel walk ``/x``
    first: look it up, check it is a traversable directory, follow it if it is a
    symlink. So the surface a caller names is never the only path an operation
    touches; the whole ancestor chain is, and that chain is exactly what a
    read-only-operator-configuration boundary has to protect.

    Both spellings contribute, because the kernel really does visit both trees:
    ``/x/neutral/../sv`` walks ``/x/neutral`` on the way to a destination only
    the collapsed spelling names. ``os.path.normpath`` collapses ``.``, ``..``
    and duplicate separators as pure string work, ``_collapse_leading_slashes``
    handles the one alias it will not (``//x`` — POSIX leaves it
    implementation-defined, Linux resolves it to ``/x``), and ``PurePosixPath``
    enumerates ancestors without a syscall. No resolve/stat/lstat/readlink/
    open/access/listdir/scandir/realpath is involved anywhere in this file's
    operand decisions.

    The filesystem root is excluded: every absolute path shares it, so counting
    it would refuse every conceivable layout. Symlinked aliases stay out of
    scope by the same rule — exposing one needs precisely the metadata read this
    boundary forbids, and the registry reader owns its own hygiene check.
    """
    components: set[str] = set()
    raw = _collapse_leading_slashes(str(value))
    for spelling in (raw, _collapse_leading_slashes(os.path.normpath(raw))):
        node = PurePosixPath(spelling)
        for ancestor in (node, *node.parents):
            text = _collapse_leading_slashes(os.path.normpath(str(ancestor)))
            if text != "/":
                components.add(text)
    return frozenset(components)


def _agents_file_query_conflict(agents_file: Path | str, surface: Path | str) -> bool:
    """Could an ARS-owned write on ``surface`` land inside the operator's registry?

    One rule for the whole class: equality, and containment in either direction.
    ``surface`` is always a path ARS **creates or mutates** — the supervisor
    root, the instance lease, the socket directory, the socket — so the hazard
    is a layering error rather than a race: ARS never creates, writes, repairs,
    or migrates the operator's agents file, and a daemon-owned surface that sits
    on top of it (or under it) would put that guarantee in the hands of a
    directory layout.

    **Scope, stated openly.** The predecessor of this rule refused any *shared
    ancestor*, because under the retired Binding architecture the operand was a
    tree ARS had to leave entirely untouched until a per-Run reader opened it,
    and a kernel walk over a shared ancestor was itself the forbidden read. That
    premise is gone: the registry is opened by ARS itself, once, at startup,
    before the lease and before any state write. Keeping the wider rule would
    now refuse ordinary safe layouts such as ``~/ars/agents.toml`` beside
    ``~/ars/state`` while protecting nothing, which is the fail-closed rule that
    turns a startable daemon into a non-startable one for no gain.

    Fail closed on a relative operand: it names no fixed location until the
    kernel joins it to an inherited cwd, so ``relative-sv`` and
    ``<cwd>/relative-sv`` can be one directory while sharing no text at all, and
    no text comparison can decide it. The filesystem root is the mirror case —
    it contains, and is contained by, everything — and has to be asked about
    directly, since "total reach" shows up here as an *empty* component set.
    """
    for operand in (agents_file, surface):
        if not _is_lexically_absolute(operand) or _lexical_is_filesystem_root(operand):
            return True
    protected = _lexical_query_components(agents_file)
    queried = _lexical_query_components(surface)
    if not protected or not queried:
        return True
    # Containment in either direction, judged on the *deepest* spelling each
    # operand names, so ``/x/agents.toml`` and ``/x/state`` stay disjoint while
    # ``/x/state/agents.toml`` and ``/x/state`` do not.
    return bool(protected & _terminal_components(surface)) or bool(
        queried & _terminal_components(agents_file)
    )


def _terminal_components(value: Path | str) -> frozenset[str]:
    """The path(s) ``value`` itself names, both spellings, without ancestors."""
    raw = _collapse_leading_slashes(str(value))
    collapsed = _collapse_leading_slashes(os.path.normpath(raw))
    return frozenset({raw, collapsed}) - {"/"}


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
            "Requires --agents-file so a rendered unit never silently omits the "
            "agent registry; the path is argv data and is not accessed. "
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
        "--agents-file",
        type=parse_agents_file,
        default=None,
        metavar="ABSOLUTE_PATH",
        help=(
            "Operator-owned agent registry file (TOML). Absolute path, server "
            "configuration only — never caller-selectable. Required in daemon "
            "mode and for --print-service-unit. ARS opens it read-only, exactly "
            "once at startup, and never creates, writes, or repairs it; an edit "
            "takes effect at the next daemon start."
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
        "--max-run-event-budget-bytes",
        type=int,
        default=DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES,
        metavar="BYTES",
        help=(
            "Admission ceiling on one Run's normalized event ledger, in bytes: "
            "a Run whose max_event_bytes * max_events exceeds it is refused at "
            "submit. Applies to every Run this daemon accepts; each Run still "
            "seals its own limits. Default 4294967296 (4 GiB). This is a "
            "theoretical per-Run persistent event-ledger ceiling — not "
            "preallocated memory, not a Run-directory disk quota, and not a "
            "daemon-wide aggregate across concurrent Runs."
        ),
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
    max_run_event_budget_bytes: int = DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES,
    agents_file: Path | str | None = None,
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
    # Operator registry configuration is mandatory for every listening entry,
    # including this one. ``serve_daemon`` is not an internal helper: it is the
    # exported coroutine every embedder and future supervisor entry calls, and
    # whatever it binds is a production ingress for whoever can reach the
    # socket. A ``run_task_factory`` says nothing about that — the previous
    # exemption let an injected factory take the instance lease, mkdir the
    # supervisor root, reconcile, and listen with no operator file configured
    # anywhere. Whether a *particular* factory consults the registry is the
    # factory's business; requiring the daemon to be configured is the daemon's.
    if agents_file is None:
        raise DaemonStartupError(
            "no agent registry file is configured; refusing to listen "
            "(pass --agents-file)"
        )
    # Same text/type contract as argv, applied to *every* path-shaped operand of
    # this call before any of them is coerced or compared. argparse is not the
    # only door in, and the overlap matrix below is exactly as strong as its
    # weakest operand: gating the agents file while ``Path()`` runs a caller's
    # path protocol on the two surfaces it is compared against gates nothing.
    #
    # The agents-file capture stays first so that a bad operand is still reported
    # as an operand refusal when a surface is also bad — today's message
    # precedence. After each ``del`` there is no name left through which a
    # caller's object could be read a second time by accident; everything
    # downstream reads the frozen text, which cannot answer differently later.
    try:
        agents_text = capture_agents_file(agents_file)
    except OperandError as exc:
        raise DaemonStartupError(f"{exc}; refusing to listen") from exc
    del agents_file
    try:
        root_text = admit_exact_text(
            supervisor_root, label="supervisor root", allow_path=True
        )
        socket_text = admit_exact_text(
            socket_path, label="socket path", allow_path=True
        )
    except OperandError as exc:
        raise DaemonStartupError(f"{exc}; refusing to listen") from exc
    del supervisor_root, socket_path

    # Absoluteness of the daemon-owned surfaces next: the conflict rule below is
    # decided from text, and text can only be compared once both operands name a
    # fixed location. A relative supervisor root or socket would slip past that
    # matrix as "disjoint" and then have the lease, the mkdirs, and
    # reconciliation land under the inherited cwd — possibly beside the
    # operator's own configuration.
    for label, surface in (
        ("supervisor root", root_text),
        ("socket path", socket_text),
    ):
        if not _is_lexically_absolute(surface):
            raise DaemonStartupError(
                f"{label} must be an absolute path; refusing to listen"
            )

    # Built once, from text this daemon owns, and only because the lease,
    # reconciliation, the stores and the server genuinely need path objects. The
    # two derived surfaces below are likewise built from owned text, so nothing
    # in the matrix can be answered by a caller.
    root = Path(root_text)
    path = Path(socket_text)

    # Every path this daemon writes to or mutates, enumerated once and checked
    # against the agents file as pure text. ARS never creates, writes, or
    # repairs the operator's registry file, so a daemon-owned surface that could
    # reach it is a layering error rather than a race: the check is lexical,
    # asks the filesystem nothing, and runs before the lease. Ancestors need no
    # separate entries — a surface's component set already contains them.
    lock_dir = root / _DAEMON_LOCK_DIRNAME
    for label, surface in (
        # durable_secure_mkdir(root), reconciliation state, native session and
        # event stores.
        ("supervisor root", root_text),
        # durable_secure_mkdir(lock_dir) + O_NOFOLLOW open of the lease file.
        ("daemon instance lease", lock_dir / _DAEMON_LOCK_FILENAME),
        # secure_mkdir of the socket's directory, then bind/unlink of the socket.
        ("socket directory", path.parent),
        ("socket path", socket_text),
    ):
        if _agents_file_query_conflict(agents_text, surface):
            raise DaemonStartupError(
                f"agent registry file overlaps the {label}; refusing to listen"
            )

    # The admission ceiling this daemon will apply to every Run it accepts.
    # Built here, before anything is written: a misconfigured ceiling refuses to
    # listen rather than silently falling back to the default. argparse already
    # rejects a non-integer spelling, so this also covers the programmatic entry
    # a direct caller uses — where ``True`` is a positive integer.
    try:
        budget_policy = EventBudgetPolicy(
            max_run_event_budget_bytes=max_run_event_budget_bytes
        )
    except NativeSpecError as err:
        raise DaemonStartupError(
            f"invalid run event budget: {err}; refusing to listen"
        ) from err

    # Step 1, and it is first for a reason: the registry parse is the only step
    # that can fail without ARS having written anything at all. Any defect
    # refuses to listen *before any state write* — before the lease, before the
    # supervisor root is created, before reconciliation, and long before bind.
    try:
        agents = agent_registry.load_agents_file(agents_text)
    except agent_registry.RegistryRefusal as err:
        raise DaemonStartupError(
            f"agent registry refused [{err.rule}]; refusing to listen"
        ) from err

    # Step 2 onwards. Exclusive ownership before any reconciliation mutation or
    # listen.
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
            "event_budget_policy": budget_policy,
            # The immutable snapshot, handed on as a value. The registry was
            # opened exactly once, above; nothing below this line can reopen it,
            # so a serving daemon cannot be re-pointed and two concurrent Runs
            # cannot resolve different registry contents.
            #
            # It is wired identically whatever the Run path is. Which factory
            # runs a Run says nothing about which registry this daemon loaded,
            # and the roster answer is a fact about the daemon: leaving it out
            # of the injected-factory branch would make the same daemon answer
            # a read-only query differently depending on how Runs get built.
            "agents": agents,
        }
        if run_task_factory is not None:
            handler_kwargs["run_task_factory"] = run_task_factory
        else:
            handler_kwargs["supervisor_root"] = root
        arsd_handlers = handlers.ArsdHandlers(**handler_kwargs)
        try:
            # Descriptor lookup/setter and server construction are inside the
            # lifecycle-owning try: a raising handlers property must still run
            # close-admission/drain/idle/release exactly once.
            if run_task_factory is not None:
                try:
                    if hasattr(run_task_factory, "handlers"):
                        run_task_factory.handlers = arsd_handlers
                except Exception:
                    raise DaemonStartupError(
                        "failed to attach run task factory"
                    ) from None

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
            # including descriptor attach failure, cancellation during stop
            # wait — owns one shutdown lifecycle before lease release.
            # Timeout cannot bypass it.
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
        # Socket/supervisor-root have safe user-scope specifier defaults; the
        # agents file has none, and a unit rendered without one would install a
        # daemon that can resolve no agent at all. Refuse the render.
        if args.agents_file is None:
            print(
                "arsd: refusing to render a service unit without --agents-file",
                file=sys.stderr,
            )
            return 2
        try:
            unit = render_service_unit(
                socket_path=None if args.socket is None else str(args.socket),
                supervisor_root=(
                    None if args.supervisor_root is None else str(args.supervisor_root)
                ),
                agents_file=args.agents_file,
                caller_mappings=tuple(args.caller_mapping or ()),
                python_executable=sys.executable,
                # Rendered only when the operator asked for a specific ceiling.
                # At the default the flag would say exactly what its absence
                # already says, and every existing unit would grow a line.
                max_run_event_budget_bytes=(
                    None
                    if args.max_run_event_budget_bytes
                    == DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES
                    else args.max_run_event_budget_bytes
                ),
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
    # Daemon-mode runtime surfaces only. ``--socket`` may be derived from
    # ``XDG_RUNTIME_DIR`` or from the supervisor root, so the *computed* value is
    # what has to be absolute, not just the flag. Print mode is deliberately not
    # covered: the renderer's ``%t``/``%h`` defaults are unit text that systemd
    # expands before ExecStart runs, so they never reach a daemon runtime path.
    for flag, surface in (
        ("--supervisor-root", root),
        ("--socket", socket_path),
    ):
        if not _is_lexically_absolute(surface):
            print(
                f"arsd: refusing to start: {flag} must be an absolute path",
                file=sys.stderr,
            )
            return 2
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
    if args.agents_file is None:
        print(
            "arsd: refusing to start without --agents-file "
            "(operator-owned agent registry file)",
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
                max_run_event_budget_bytes=args.max_run_event_budget_bytes,
                agents_file=args.agents_file,
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

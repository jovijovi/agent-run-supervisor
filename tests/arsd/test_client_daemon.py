"""Slice 5 — typed local client + unprivileged daemon entrypoint.

Real temp-dir AF_UNIX sockets; deterministic injected run-task factories only.
No real AGENT, no production UID/owner mapping, no service install/enable.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import shutil
import signal
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath

import pytest

from agent_run_supervisor.arsd import protocol, server
from agent_run_supervisor.arsd import client as arsd_client
from agent_run_supervisor.arsd import operand as arsd_operand
from agent_run_supervisor.arsd import __main__ as arsd_main
from agent_run_supervisor.native_acp import storage

from tests.arsd.test_admission import (
    SpyFactory,
    submit_payload,
    valid_wire_request,
)
from tests.arsd.test_handlers_registry import CancelFactory, seed_events, seed_session
from tests.arsd.test_service_unit import (
    _LYING_AGENTS_FILE_KINDS,
    _lying_agents_file,
    _record_fs_queries,
)


SECRET_SENTINEL = "sk-live-" + "LEAKCANARY"


def run_async(coro, timeout: float = 30):
    return asyncio.run(asyncio.wait_for(coro, timeout))


@pytest.fixture
def sock_root():
    root = Path(tempfile.mkdtemp(prefix="arsd-s5-", dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def sock_path(root: Path) -> Path:
    return root / "s" / "arsd.sock"


def supervisor_root(root: Path) -> Path:
    return root / "sv"


def make_agents_file(root: Path) -> Path:
    """A real operator agents file, parsed once at daemon startup.

    It lives in its own directory: the daemon refuses to listen when its own
    writable surfaces contain, or sit inside, the operator's configuration.
    """
    from tests.native_acp import registry_fixtures as rfx

    conf = root / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    return rfx.write_registry(conf)


def local_principal() -> server.Principal:
    return server.Principal(
        principal_id="hermes-local",
        owner_namespaces=frozenset({("hermes", "hermes/doc-check")}),
    )


def same_uid_policy() -> server.CallerPolicy:
    return server.CallerPolicy({os.getuid(): local_principal()})


def mapping_flag(uid: int | None = None) -> str:
    uid = os.getuid() if uid is None else uid
    return f"{uid}:hermes-local:hermes:hermes/doc-check"


class CompletingFactory(SpyFactory):
    """Factory that completes quickly and can emit events for follow tests."""

    def __init__(self, *, event_count: int = 0, mode: str = "complete") -> None:
        super().__init__(mode=mode)
        self.event_count = event_count

    def __call__(self, *, command, run_id, prepared_handle, submitted_at):
        if self.event_count:
            seed_events(prepared_handle.run_dir, self.event_count)
        return super().__call__(
            command=command,
            run_id=run_id,
            prepared_handle=prepared_handle,
            submitted_at=submitted_at,
        )


class PendingFactory(SpyFactory):
    def __init__(self) -> None:
        super().__init__(mode="pending")


async def wait_for_socket(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                server.probe_connect(str(path))
                return
            except OSError:
                pass
        await asyncio.sleep(0.02)
    raise AssertionError(f"socket never became live: {path}")


def wait_for_socket_sync(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                server.probe_connect(str(path))
                return
            except OSError:
                pass
        time.sleep(0.02)
    raise AssertionError(f"socket never became live: {path}")


class ThreadedDaemon:
    """Run serve_daemon on a private loop so sync clients never block it."""

    def __init__(
        self,
        path: Path,
        root: Path,
        *,
        policy: server.CallerPolicy | None = None,
        factory=None,
        max_concurrent_runs: int = 4,
        max_connections: int = 32,
        cancel_wait_seconds: float = 2.0,
        shutdown_timeout: float = 5.0,
    ) -> None:
        self.path = path
        self.root = root
        self.policy = same_uid_policy() if policy is None else policy
        self.factory = CompletingFactory() if factory is None else factory
        self.max_concurrent_runs = max_concurrent_runs
        self.max_connections = max_connections
        self.cancel_wait_seconds = cancel_wait_seconds
        self.shutdown_timeout = shutdown_timeout
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="arsd-s5-daemon", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise AssertionError("daemon thread failed to become ready")
        if self._error is not None:
            raise self._error
        wait_for_socket_sync(self.path)

    def _run(self) -> None:
        try:
            asyncio.run(self._amain())
        except BaseException as exc:  # noqa: BLE001 — surface to starter
            self._error = exc
            self._ready.set()

    async def _amain(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready.set()
        await arsd_main.serve_daemon(
            socket_path=self.path,
            supervisor_root=self.root,
            policy=self.policy,
            agents_file=str(make_agents_file(self.root.parent)),
            max_concurrent_runs=self.max_concurrent_runs,
            max_connections=self.max_connections,
            run_task_factory=self.factory,
            cancel_wait_seconds=self.cancel_wait_seconds,
            shutdown_timeout=self.shutdown_timeout,
            stop_event=self._stop,
            install_signals=False,
        )

    def stop(self) -> None:
        loop = self._loop
        stop = self._stop
        if loop is not None and stop is not None and loop.is_running():
            loop.call_soon_threadsafe(stop.set)
        if self._thread is not None:
            self._thread.join(timeout=self.shutdown_timeout + 3)
            self._thread = None


@contextlib.contextmanager
def running_daemon(
    path: Path,
    root: Path,
    *,
    policy: server.CallerPolicy | None = None,
    factory=None,
    max_concurrent_runs: int = 4,
    max_connections: int = 32,
    cancel_wait_seconds: float = 2.0,
    shutdown_timeout: float = 5.0,
):
    daemon = ThreadedDaemon(
        path,
        root,
        policy=policy,
        factory=factory,
        max_concurrent_runs=max_concurrent_runs,
        max_connections=max_connections,
        cancel_wait_seconds=cancel_wait_seconds,
        shutdown_timeout=shutdown_timeout,
    )
    daemon.start()
    try:
        yield daemon
    finally:
        daemon.stop()


# --- argparse / mapping / defaults ----------------------------------------


def test_parse_caller_mapping_flag_shape() -> None:
    uid, principal = arsd_main.parse_caller_mapping(mapping_flag(42))
    assert uid == 42
    assert principal.principal_id == "hermes-local"
    assert principal.owner_namespaces == frozenset({("hermes", "hermes/doc-check")})


def test_parse_caller_mapping_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        arsd_main.parse_caller_mapping("not-a-mapping")
    with pytest.raises(ValueError):
        arsd_main.parse_caller_mapping("abc:hermes:hermes:ns")
    with pytest.raises(ValueError):
        arsd_main.parse_caller_mapping("1:p:owner")  # missing namespace


def test_build_policy_merges_same_uid_namespaces() -> None:
    policy = arsd_main.build_caller_policy(
        [
            "7:hermes-local:hermes:hermes/doc-check",
            "7:hermes-local:hermes:hermes/other",
        ]
    )
    principal = policy.resolve(7)
    assert principal is not None
    assert principal.owner_namespaces == frozenset(
        {("hermes", "hermes/doc-check"), ("hermes", "hermes/other")}
    )


def test_build_policy_rejects_uid_principal_conflict() -> None:
    with pytest.raises(ValueError):
        arsd_main.build_caller_policy(
            [
                "7:hermes-local:hermes:hermes/doc-check",
                "7:other-principal:hermes:hermes/doc-check",
            ]
        )


def test_default_socket_path_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg"))
    assert arsd_main.default_socket_path(tmp_path / "sv") == (
        tmp_path / "xdg" / "agent-run-supervisor" / "arsd.sock"
    )


def test_default_socket_path_supervisor_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    root = tmp_path / "sv"
    assert arsd_main.default_socket_path(root) == root / "arsd" / "arsd.sock"


def test_argparse_requires_supervisor_root_and_accepts_flags() -> None:
    parser = arsd_main.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    ns = parser.parse_args(
        [
            "--supervisor-root",
            "/tmp/sv",
            "--socket",
            "/tmp/arsd.sock",
            "--caller-mapping",
            mapping_flag(1000),
            "--max-concurrent-runs",
            "2",
            "--max-connections",
            "8",
            "--log-level",
            "DEBUG",
        ]
    )
    assert ns.supervisor_root == Path("/tmp/sv")
    assert ns.socket == Path("/tmp/arsd.sock")
    assert ns.caller_mapping == [mapping_flag(1000)]
    assert ns.max_concurrent_runs == 2
    assert ns.max_connections == 8
    assert ns.log_level == "DEBUG"


# --- operator Runtime Binding root (R13 daemon wiring) --------------------

# Deliberately synthetic and never created: configuration is a value here, and
# ARS must not create, repair, promote, or even stat the operator's root.
AGENTS_FILE_SPELLING = "/etc/agent-run-supervisor/agents.toml"


def test_argparse_accepts_absolute_agents_file() -> None:
    parser = arsd_main.build_arg_parser()
    ns = parser.parse_args(
        [
            "--supervisor-root",
            "/tmp/sv",
            "--agents-file",
            AGENTS_FILE_SPELLING,
        ]
    )
    assert ns.agents_file == AGENTS_FILE_SPELLING


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "relative/binding",
        "./binding",
        "~/binding",
        "/srv/binding\nExecStart=/bin/evil",
        "/srv/binding\r",
        "/srv/binding\x00",
        "/srv/binding\x1b",
    ],
)
def test_argparse_refuses_unsafe_agents_file(bad: str, capsys) -> None:
    """Relative / empty / control input fails closed at the operator boundary."""
    parser = arsd_main.build_arg_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(
            ["--supervisor-root", "/tmp/sv", "--agents-file", bad]
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    # Refused as an unsafe *value*, not as an unknown flag.
    assert "--agents-file" in err
    assert "unrecognized" not in err.lower()


def test_serve_daemon_refuses_without_agents_file_before_lease_or_reconcile(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        order: list[str] = []

        def boom_lease(_root):
            order.append("lease")
            raise AssertionError("lease must not run without an agents file")

        def boom_reconcile(_root):
            order.append("reconcile")
            raise AssertionError("reconcile must not run without an agents file")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                install_signals=False,
            )
        assert "agent" in str(err.value).lower()
        assert order == []
        assert not path.exists()

    run_async(case())


def test_serve_daemon_requires_agents_file_even_with_an_injected_factory(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocker regression: an injected factory used to excuse the omission.

    ``serve_daemon`` is the one programmatic entry that *listens*. Whatever it
    binds is a production ingress for whoever can reach the socket, so operator
    Binding configuration is a property of the daemon, not of the factory that
    happens to be wired into it. The old exemption let
    ``serve_daemon(agents_file=None, run_task_factory=...)`` take the instance
    lease, mkdir the supervisor root, reconcile, and listen — with no operator
    root configured anywhere.
    """

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise AssertionError("lease must not run without an agents file")

        def boom_reconcile(_root):
            reached.append("reconcile")
            raise AssertionError("reconcile must not run without an agents file")

        def boom_server(**_kwargs):
            reached.append("server")
            raise AssertionError("server must not be constructed")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
        monkeypatch.setattr(arsd_main.server, "ArsdServer", boom_server)

        stop = asyncio.Event()
        stop.set()
        with _record_fs_queries(monkeypatch) as log:
            with pytest.raises(arsd_main.DaemonStartupError) as err:
                await arsd_main.serve_daemon(
                    socket_path=path,
                    supervisor_root=root,
                    policy=same_uid_policy(),
                    run_task_factory=CompletingFactory(),
                    stop_event=stop,
                    install_signals=False,
                )

        assert "agent" in str(err.value).lower()
        assert reached == []
        assert log.calls == []
        assert not path.exists()
        assert not root.exists()

    run_async(case())


def test_serve_daemon_passes_the_snapshot_into_handlers(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """serve_daemon → ArsdHandlers carries the *snapshot*, never a path.

    The registry is opened exactly once, by startup, before the socket is
    bound. What crosses into the handlers is the immutable result of that one
    read, so nothing below the daemon entrypoint can reopen it and a serving
    daemon cannot be re-pointed.
    """

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        agents = make_agents_file(sock_root)
        seen: dict = {}
        real_cls = arsd_main.handlers.ArsdHandlers

        def capturing(**kwargs):
            seen.update(kwargs)
            passthrough = dict(kwargs)
            passthrough.pop("agents", None)
            passthrough.pop("supervisor_root", None)
            passthrough["run_task_factory"] = CompletingFactory()
            return real_cls(**passthrough)

        monkeypatch.setattr(arsd_main.handlers, "ArsdHandlers", capturing)
        stop = asyncio.Event()
        stop.set()
        rc = await arsd_main.serve_daemon(
            socket_path=path,
            supervisor_root=root,
            policy=same_uid_policy(),
            agents_file=str(agents),
            stop_event=stop,
            install_signals=False,
        )
        assert rc == 0
        snapshot = seen["agents"]
        assert snapshot.ids() == ("native-agent",)
        assert seen["supervisor_root"] == root
        # A path never reaches the handlers, so there is no seam for a reread.
        assert "agents_file" not in seen
        assert not hasattr(snapshot, "path")

    run_async(case())


def test_main_passes_agents_file_to_serve_daemon(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    seen: dict = {}

    async def fake_serve(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(arsd_main, "serve_daemon", fake_serve)
    rc = arsd_main.main(
        [
            "--supervisor-root",
            str(root),
            "--socket",
            str(path),
            "--caller-mapping",
            mapping_flag(),
            "--agents-file",
            AGENTS_FILE_SPELLING,
        ]
    )
    assert rc == 0
    assert seen["agents_file"] == AGENTS_FILE_SPELLING
    assert seen["supervisor_root"] == root


def test_main_daemon_mode_refuses_without_agents_file(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    calls = {"n": 0}

    async def boom_serve(**_kwargs):
        calls["n"] += 1
        raise AssertionError("must refuse before serve")

    monkeypatch.setattr(arsd_main, "serve_daemon", boom_serve)
    rc = arsd_main.main(
        [
            "--supervisor-root",
            str(root),
            "--socket",
            str(path),
            "--caller-mapping",
            mapping_flag(),
        ]
    )
    assert rc == 2
    assert calls["n"] == 0
    assert not path.exists()
    err = capsys.readouterr().err.lower()
    assert "agents-file" in err or "agents file" in err


# --- Binding root vs. daemon-owned surfaces (pure text, before the lease) ---
#
# The stock daemon creates the supervisor root, the lease directory, the lease
# file, reconciliation state, the socket directory, and the socket. If the
# operator's Binding root equals, contains, or sits inside any of them, that
# state lands in Binding storage and ``durable_secure_mkdir`` / ``secure_mkdir``
# lstat the Binding root itself long before the per-Run ``BindingReader`` — the
# single, sole metadata reader. The refusal must therefore be pure text work
# ordered ahead of the lease and every filesystem query.


def _daemon_overlap_cases(root: Path, path: Path) -> dict[str, dict[str, object]]:
    """Materially distinct Binding/daemon-surface overlaps for ``serve_daemon``."""
    return {
        "binding_equals_supervisor_root": {"agents_file": str(root)},
        "binding_parent_of_supervisor_root": {"agents_file": str(root.parent)},
        "binding_child_of_supervisor_root": {
            "agents_file": str(root / "native-runs")
        },
        "binding_equals_socket_path": {"agents_file": str(path)},
        "socket_directly_inside_binding": {"agents_file": str(path.parent)},
        "socket_deep_inside_binding": {
            "agents_file": str(path.parent),
            "socket_path": str(path.parent / "run" / "arsd.sock"),
        },
        "binding_inside_socket_directory": {
            "agents_file": str(path.parent / "binding")
        },
        "double_slash_binding_aliases_supervisor_root": {
            "agents_file": "//" + str(root).lstrip("/")
        },
        "double_slash_socket_aliases_binding": {
            "agents_file": str(path.parent),
            "socket_path": "//" + str(path).lstrip("/"),
        },
    }


_DAEMON_OVERLAP_CASE_IDS = sorted(
    _daemon_overlap_cases(Path("/x/sv"), Path("/x/s/arsd.sock"))
)


@pytest.mark.parametrize("case", _DAEMON_OVERLAP_CASE_IDS)
def test_serve_daemon_refuses_binding_overlap_before_lease_or_query(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    async def run_case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        overrides = _daemon_overlap_cases(root, path)[case]
        binding = Path("/" + str(overrides["agents_file"]).lstrip("/"))
        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise AssertionError("lease must not run for an overlapping Binding root")

        def boom_reconcile(_root):
            reached.append("reconcile")
            raise AssertionError("reconcile must not run")

        def boom_handlers(**_kwargs):
            reached.append("handlers")
            raise AssertionError("handlers must not be constructed")

        def boom_server(**_kwargs):
            reached.append("server")
            raise AssertionError("server must not be constructed")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
        monkeypatch.setattr(arsd_main.handlers, "ArsdHandlers", boom_handlers)
        monkeypatch.setattr(arsd_main.server, "ArsdServer", boom_server)

        kwargs: dict = {
            "socket_path": str(path),
            "supervisor_root": str(root),
            "policy": same_uid_policy(),
            "install_signals": False,
        }
        kwargs.update(overrides)

        with _record_fs_queries(monkeypatch) as log:
            with pytest.raises(arsd_main.DaemonStartupError) as err:
                await arsd_main.serve_daemon(**kwargs)

        message = str(err.value).lower()
        assert "agent" in message
        assert "overlap" in message or "inside" in message
        assert reached == []
        assert log.touching(binding) == []
        # Ordering: nothing is queried at all, so no daemon-owned surface can
        # read the Binding root as one of its path components.
        assert log.calls == []
        assert not path.exists()
        assert not root.exists()

    run_async(run_case())


def test_serve_daemon_refuses_binding_overlap_even_with_injected_factory(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An injected factory needs no root, but a supplied one is still checked."""

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)

        def boom_lease(_root):
            raise AssertionError("lease must not run for an overlapping Binding root")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=root,
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        assert "agent" in str(err.value).lower()
        assert not path.exists()

    run_async(case())


def test_serve_daemon_opens_a_disjoint_agents_file_exactly_once(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disjoint layout works, and the registry is opened once — then never again.

    The predecessor of this test asserted the operand was *never* touched at
    startup, which was true of a Binding root only a per-Run reader ever opened.
    The daemon now performs that read itself, before the lease and before any
    state write, so "exactly once for the whole daemon lifetime" is the property
    that actually holds.
    """

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        agents = make_agents_file(sock_root)
        opened: list[str] = []
        real_load = arsd_main.agent_registry.load_agents_file

        def counting_load(target):
            opened.append(str(target))
            return real_load(target)

        real_cls = arsd_main.handlers.ArsdHandlers

        def capturing(**kwargs):
            passthrough = dict(kwargs)
            passthrough.pop("agents", None)
            passthrough.pop("supervisor_root", None)
            passthrough["run_task_factory"] = CompletingFactory()
            return real_cls(**passthrough)

        monkeypatch.setattr(arsd_main.agent_registry, "load_agents_file", counting_load)
        monkeypatch.setattr(arsd_main.handlers, "ArsdHandlers", capturing)
        stop = asyncio.Event()
        stop.set()
        rc = await arsd_main.serve_daemon(
            socket_path=path,
            supervisor_root=root,
            policy=same_uid_policy(),
            agents_file=str(agents),
            stop_event=stop,
            install_signals=False,
        )
        assert rc == 0
        assert opened == [str(agents)]
        # Paired guard: the daemon-owned surfaces really were created.
        assert root.exists()

    run_async(case())


# --- daemon-owned runtime paths must be absolute ---------------------------
#
# A relative ``--supervisor-root`` / ``--socket`` has no fixed meaning until the
# kernel joins it to whatever cwd the daemon happens to inherit, so the lexical
# Binding-overlap matrix cannot judge it: ``relative-sv`` and
# ``<cwd>/relative-sv`` are the same directory but share no text. The daemon
# would then take the instance lease, mkdir, and reconcile *inside* operator
# Binding storage while the overlap gate reported "disjoint". Refusing a
# non-absolute runtime path is what makes the overlap answer meaningful, so it
# has to land before the lease and before the first filesystem query — proving
# it needs the same empty-primitive-log assertion, not just "no lease".
#
# Absolute is decided from text alone: anchoring a relative path to the cwd
# would invent exactly the daemon-owned location this boundary refuses to guess.


def _relative_runtime_cases() -> dict[str, dict[str, str]]:
    """Non-absolute daemon-owned surfaces, one materially distinct spelling each."""
    return {
        "bare_relative_supervisor_root": {"supervisor_root": "relative-sv"},
        "dot_relative_supervisor_root": {"supervisor_root": "./relative-sv"},
        "dot_dot_relative_supervisor_root": {"supervisor_root": "../relative-sv"},
        "empty_supervisor_root": {"supervisor_root": ""},
        "tilde_supervisor_root": {"supervisor_root": "~/relative-sv"},
        "bare_relative_socket": {"socket_path": "relative-arsd.sock"},
        "dot_relative_socket": {"socket_path": "./s/relative-arsd.sock"},
        "empty_socket": {"socket_path": ""},
    }


_RELATIVE_RUNTIME_CASE_IDS = sorted(_relative_runtime_cases())


@pytest.mark.parametrize("case", _RELATIVE_RUNTIME_CASE_IDS)
def test_serve_daemon_refuses_relative_runtime_path_before_lease_or_query(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Blocker regression: a relative surface reached the lease with cwd-owned state."""

    async def run_case():
        # Contain the blast radius: if the refusal regresses, the daemon
        # mkdirs its state under this disposable cwd, never the repository.
        monkeypatch.chdir(sock_root)
        overrides = _relative_runtime_cases()[case]
        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise AssertionError("lease must not run for a relative runtime path")

        def boom_reconcile(_root):
            reached.append("reconcile")
            raise AssertionError("reconcile must not run")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)

        kwargs: dict = {
            "socket_path": str(sock_path(sock_root)),
            "supervisor_root": str(supervisor_root(sock_root)),
            "policy": same_uid_policy(),
            # Deliberately disjoint *as text* from every relative spelling
            # above, so only the absoluteness rule can produce this refusal.
            "agents_file": AGENTS_FILE_SPELLING,
            "install_signals": False,
        }
        kwargs.update(overrides)

        with _record_fs_queries(monkeypatch) as log:
            with pytest.raises(arsd_main.DaemonStartupError) as err:
                await arsd_main.serve_daemon(**kwargs)

        message = str(err.value).lower()
        assert "absolute" in message
        assert reached == []
        assert log.calls == []
        # Nothing was created against the inherited cwd.
        assert sorted(p.name for p in Path(sock_root).iterdir()) == []

    run_async(run_case())


def test_serve_daemon_refuses_relative_runtime_path_with_injected_factory(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An injected factory owns Binding resolution — never the daemon's own paths."""

    async def case():
        monkeypatch.chdir(sock_root)

        def boom_lease(_root):
            raise AssertionError("lease must not run for a relative runtime path")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path="relative-arsd.sock",
                supervisor_root="relative-sv",
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        assert "absolute" in str(err.value).lower()
        assert not (Path(sock_root) / "relative-sv").exists()

    run_async(case())


def test_serve_daemon_accepts_the_double_slash_absolute_alias(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule refuses *relative*, not merely "does not start with one slash".

    Linux resolves ``//tmp/x`` and ``/tmp/x`` to the same directory, so the
    alias must clear the gate. The lease is a sentinel wall: reaching it proves
    the pure gate accepted the layout without starting a real daemon.
    """

    class _LeaseReached(Exception):
        pass

    async def case():
        alias_root = "//" + str(supervisor_root(sock_root)).lstrip("/")

        def wall(_root):
            raise _LeaseReached

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", wall)
        with pytest.raises(_LeaseReached):
            await arsd_main.serve_daemon(
                socket_path="//" + str(sock_path(sock_root)).lstrip("/"),
                supervisor_root=alias_root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                install_signals=False,
            )

    run_async(case())


@pytest.mark.parametrize("case", _RELATIVE_RUNTIME_CASE_IDS)
def test_main_refuses_relative_runtime_path_before_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, case: str
) -> None:
    """The shipped CLI is the operator's real entrypoint; it must refuse too."""
    monkeypatch.chdir(tmp_path)
    overrides = _relative_runtime_cases()[case]
    calls = {"n": 0}

    async def boom_serve(**_kwargs):
        calls["n"] += 1
        raise AssertionError("must refuse before serve_daemon")

    monkeypatch.setattr(arsd_main, "serve_daemon", boom_serve)
    argv = [
        "--supervisor-root",
        overrides.get("supervisor_root", str(tmp_path / "sv")),
        "--socket",
        overrides.get("socket_path", str(tmp_path / "s" / "arsd.sock")),
        "--caller-mapping",
        mapping_flag(),
        "--agents-file",
        AGENTS_FILE_SPELLING,
    ]
    rc = arsd_main.main(argv)

    assert rc == 2
    assert calls["n"] == 0
    err = capsys.readouterr().err.lower()
    assert "absolute" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_main_refuses_a_relative_xdg_derived_socket_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """``--socket`` is optional; the derived default is daemon-owned all the same."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "relative-runtime-dir")
    calls = {"n": 0}

    async def boom_serve(**_kwargs):
        calls["n"] += 1
        raise AssertionError("must refuse before serve_daemon")

    monkeypatch.setattr(arsd_main, "serve_daemon", boom_serve)
    rc = arsd_main.main(
        [
            "--supervisor-root",
            str(tmp_path / "sv"),
            "--caller-mapping",
            mapping_flag(),
            "--agents-file",
            AGENTS_FILE_SPELLING,
        ]
    )

    assert rc == 2
    assert calls["n"] == 0
    assert "absolute" in capsys.readouterr().err.lower()
    assert not (tmp_path / "relative-runtime-dir").exists()


def test_main_still_starts_with_absolute_runtime_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paired guard: the absoluteness rule must not refuse a valid operator layout."""
    seen: dict = {}

    async def fake_serve(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(arsd_main, "serve_daemon", fake_serve)
    rc = arsd_main.main(
        [
            "--supervisor-root",
            str(tmp_path / "sv"),
            "--socket",
            str(tmp_path / "s" / "arsd.sock"),
            "--caller-mapping",
            mapping_flag(),
            "--agents-file",
            AGENTS_FILE_SPELLING,
        ]
    )
    assert rc == 0
    assert seen["supervisor_root"] == tmp_path / "sv"


def test_service_unit_specifiers_stay_rendered_data_not_daemon_paths(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """``%h``/``%t`` survive the render and are refused if they ever reach the daemon.

    systemd expands the specifiers before ExecStart runs, so the daemon never
    sees them — which is exactly why the renderer may keep emitting them and the
    daemon may still refuse every non-absolute runtime path it is handed.
    """
    monkeypatch.setattr(
        arsd_main,
        "geteuid",
        lambda: (_ for _ in ()).throw(AssertionError("print mode: no euid check")),
    )
    rc = arsd_main.main(["--print-service-unit", "--agents-file", AGENTS_FILE_SPELLING])
    assert rc == 0
    out = capsys.readouterr().out
    assert "%t/" in out and "%h/" in out

    # Print mode is done; restore the real check for the daemon half.
    monkeypatch.setattr(arsd_main, "geteuid", os.getuid)

    async def unexpanded():
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path="%t/agent-run-supervisor/arsd.sock",
                supervisor_root="%h/.local/share/agent-run-supervisor",
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                install_signals=False,
            )
        assert "absolute" in str(err.value).lower()

    run_async(unexpanded())


# --- the programmatic Binding-root contract (blockers 2 and 3) --------------
#
# ``main()`` validates its argv through ``parse_agents_file``, but argv is not
# the only way in: ``serve_daemon`` is an exported coroutine and is what every
# embedder, test, and future supervisor entry actually calls. Whatever it binds
# is a listening ingress, so it owns the same operator contract as the CLI —
# text-only shape first, then the query-overlap gate — and both must land before
# the instance lease, which is the first side effect and the first metadata read.


class _HostileBindingRoot:
    """Outside the declared ``Path | str`` union; refused, never stringified.

    A refusal that coerces the operand first has already handed control to
    attacker code: ``__str__``/``__fspath__`` run arbitrary work and could
    return one spelling to the validator and another to the kernel. Counting the
    coercions is the only way to prove the gate judged the *type* and stopped.
    """

    def __init__(self) -> None:
        self.coercions = 0

    def __str__(self) -> str:  # pragma: no cover - must never be called
        self.coercions += 1
        return AGENTS_FILE_SPELLING

    def __fspath__(self) -> str:  # pragma: no cover - must never be called
        self.coercions += 1
        return AGENTS_FILE_SPELLING


def _malformed_agents_files() -> dict[str, object]:
    """Binding roots the programmatic contract must refuse from text/type alone."""
    return {
        # The reported blocker: relative text that lexical overlap calls
        # "disjoint" from an absolute supervisor root naming the same directory.
        "bare_relative": "ars-review-binding",
        "bare_relative_path_object": Path("ars-review-binding"),
        "dot_relative": "./ars-review-binding",
        "dot_dot_relative": "../ars-review-binding",
        "nested_relative": "ars-review/binding",
        # ``~`` is shell syntax; nothing in ARS expands it, so it would be
        # created literally in the cwd.
        "tilde": "~/ars-review-binding",
        "empty": "",
        "blank": "   ",
        "dot": ".",
        # ``Path("")`` stringifies to ``"."`` — same refusal, different spelling.
        "empty_path_object": Path(""),
        # Unexpanded systemd specifiers: unit text, never a daemon runtime path.
        "systemd_home_specifier": "%h/ars-review-binding",
        # Control characters would be carried verbatim into a rendered unit.
        "newline_control": "/opt/ars-review-binding\nExecStart=/bin/evil",
        "carriage_return_control": "/opt/ars-review-binding\r",
        "nul_control": "/opt/ars-review-binding\x00",
        "escape_control": "/opt/ars-review-binding\x1b",
        # Not in the declared union: refused without coercion.
        "bytes": b"/opt/ars-review-binding",
        "integer": 13,
        "purepath_outside_union": PurePosixPath("/opt/ars-review-binding"),
    }


@pytest.mark.parametrize("case", sorted(_malformed_agents_files()))
def test_serve_daemon_refuses_malformed_agents_file_before_lease_or_query(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Blocker regression: a relative Binding root reached the instance lease."""

    async def run_case():
        # If the refusal regresses, a relative root is created under this
        # disposable cwd rather than anywhere the operator owns.
        monkeypatch.chdir(sock_root)
        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise AssertionError("lease must not run for a malformed Binding root")

        def boom_reconcile(_root):
            reached.append("reconcile")
            raise AssertionError("reconcile must not run")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)

        with _record_fs_queries(monkeypatch) as log:
            with pytest.raises(arsd_main.DaemonStartupError) as err:
                await arsd_main.serve_daemon(
                    socket_path=str(sock_path(sock_root)),
                    supervisor_root=str(supervisor_root(sock_root)),
                    policy=same_uid_policy(),
                    agents_file=_malformed_agents_files()[case],
                    install_signals=False,
                )

        assert "agent" in str(err.value).lower()
        assert reached == []
        assert log.calls == []
        assert sorted(p.name for p in Path(sock_root).iterdir()) == []

    run_async(run_case())


def test_serve_daemon_refuses_a_hostile_agents_file_without_coercing_it(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The union is checked by type; the operand is never handed to ``str``."""

    async def case():
        hostile = _HostileBindingRoot()

        def boom_lease(_root):
            raise AssertionError("lease must not run for a hostile Binding root")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=str(sock_path(sock_root)),
                supervisor_root=str(supervisor_root(sock_root)),
                policy=same_uid_policy(),
                agents_file=hostile,
                install_signals=False,
            )
        assert "agent" in str(err.value).lower()
        assert hostile.coercions == 0

    run_async(case())


# --- the Binding root's *type* contract: exact values, frozen once -----------
#
# ``isinstance(value, (str, Path))`` is the wrong question for a boundary that
# reads its operand again afterwards. It admits *subclasses*, and a subclass owns
# every conversion hook: it can show the shape gate and the overlap matrix a
# disjoint ``/opt`` root, then hand the kernel — and the per-Run reader — a value
# sitting on the daemon's own state. No amount of text comparison sees through
# that, because the text was never a fact about the operand; it was an answer the
# operand chose. So the type is judged by identity first, and the value is then
# frozen into a plain standard path that every later reader consumes.
#
# Identity (``type(value) is str``) rather than membership (``type(value) in
# (...)``): a tuple membership test compares with ``==``, and a hostile metaclass
# can answer that. ``is`` is the one question no user code can intercept.

# Derived here rather than imported from the daemon, so the test states the
# platform fact independently: this is the type ``Path(...)`` actually produces.
_EXACT_PATH_TYPE = type(Path(os.sep))

# ``_LYING_AGENTS_FILE_KINDS`` / ``_lying_agents_file`` are imported from
# ``tests.arsd.test_service_unit``: the renderer needs the same hostile-operand
# factory, and that module is this suite's existing shared-helper home. The
# import direction is one-way, so the factory cannot live here.


@pytest.mark.parametrize("kind", _LYING_AGENTS_FILE_KINDS)
def test_capture_agents_file_refuses_inexact_types_untouched(kind: str) -> None:
    """The union means exact types: a subclass is refused before any hook runs."""
    hostile, probes = _lying_agents_file(kind, "/tmp/ars-daemon-state/sv")
    with pytest.raises(arsd_operand.OperandError) as err:
        arsd_operand.capture_agents_file(hostile)
    problem = str(err.value)
    assert "agent" in problem.lower()
    # Fixed, sanitized text: no spelling of the operand, no ``repr`` of it.
    assert AGENTS_FILE_SPELLING not in problem
    assert "/tmp/ars-daemon-state/sv" not in problem
    assert probes == []


def test_capture_agents_file_accepts_both_exact_types() -> None:
    """Paired guard: the two types the declared API promises still pass."""
    assert arsd_operand.capture_agents_file(AGENTS_FILE_SPELLING) == AGENTS_FILE_SPELLING
    assert type(AGENTS_FILE_SPELLING) is str
    assert arsd_operand.capture_agents_file(Path(AGENTS_FILE_SPELLING)) == AGENTS_FILE_SPELLING
    assert type(Path(AGENTS_FILE_SPELLING)) is _EXACT_PATH_TYPE


@pytest.mark.parametrize("kind", _LYING_AGENTS_FILE_KINDS)
def test_serve_daemon_refuses_a_lying_agents_file_before_lease_or_query(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Blocker regression: a ``str``/``Path`` subclass cleared every text gate.

    The operand's real value here *is* the supervisor root, so a gate reading the
    truth refuses the layout outright. Reading the advertised text instead, the
    shape gate passed, overlap said "disjoint", and the lease — the first side
    effect and the first metadata read — was taken on a value the daemon had
    never actually seen.
    """

    async def run_case():
        real = str(supervisor_root(sock_root))
        hostile, probes = _lying_agents_file(kind, real)
        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise AssertionError("lease must not run for a lying Binding root")

        def boom_reconcile(_root):
            reached.append("reconcile")
            raise AssertionError("reconcile must not run")

        def boom_handlers(**_kwargs):
            reached.append("handlers")
            raise AssertionError("handlers must not be constructed")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
        monkeypatch.setattr(arsd_main.handlers, "ArsdHandlers", boom_handlers)

        with _record_fs_queries(monkeypatch) as log:
            with pytest.raises(arsd_main.DaemonStartupError) as err:
                await arsd_main.serve_daemon(
                    socket_path=str(sock_path(sock_root)),
                    supervisor_root=real,
                    policy=same_uid_policy(),
                    agents_file=hostile,
                    install_signals=False,
                )

        message = str(err.value)
        assert "agent" in message.lower()
        assert AGENTS_FILE_SPELLING not in message and real not in message
        # The type decided it: not one conversion hook ran.
        assert probes == []
        assert reached == []
        assert log.calls == []

    run_async(run_case())


@pytest.mark.parametrize("kind", ("exact_path", "exact_str"))
def test_serve_daemon_accepts_an_exact_disjoint_agents_file(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Paired guard: exactness is the rule, not "refuse anything typed"."""

    class _LeaseReached(Exception):
        pass

    async def case():
        agents = make_agents_file(sock_root)
        supplied = str(agents) if kind == "exact_str" else Path(agents)

        def wall(_root):
            raise _LeaseReached

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", wall)
        with pytest.raises(_LeaseReached):
            await arsd_main.serve_daemon(
                socket_path=str(sock_path(sock_root)),
                supervisor_root=str(supervisor_root(sock_root)),
                policy=same_uid_policy(),
                agents_file=supplied,
                install_signals=False,
            )

    run_async(case())


@pytest.mark.parametrize("kind", ("exact_path", "exact_str"))
def test_serve_daemon_hands_handlers_a_frozen_standard_agents_file(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """What the handlers receive is the daemon's snapshot, never the operand.

    Freezing is the half of the contract the type gate cannot supply: a value
    validated as text and then read again is still two reads. The daemon
    therefore propagates the immutable *result* of its one read, and the caller's
    object reaches nothing downstream at all — which is strictly stronger than
    handing on a rebuilt path.
    """

    async def case():
        agents = make_agents_file(sock_root)
        supplied = str(agents) if kind == "exact_str" else Path(agents)
        seen: dict = {}
        real_cls = arsd_main.handlers.ArsdHandlers

        def capturing(**kwargs):
            seen.update(kwargs)
            passthrough = dict(kwargs)
            passthrough.pop("agents", None)
            passthrough.pop("supervisor_root", None)
            passthrough["run_task_factory"] = CompletingFactory()
            return real_cls(**passthrough)

        monkeypatch.setattr(arsd_main.handlers, "ArsdHandlers", capturing)
        stop = asyncio.Event()
        stop.set()
        rc = await arsd_main.serve_daemon(
            socket_path=sock_path(sock_root),
            supervisor_root=supervisor_root(sock_root),
            policy=same_uid_policy(),
            agents_file=supplied,
            stop_event=stop,
            install_signals=False,
        )
        assert rc == 0
        propagated = seen["agents"]
        assert isinstance(propagated, arsd_main.agent_registry.AgentRegistrySnapshot)
        assert propagated.ids() == ("native-agent",)
        # No path, and no reference to the caller's object, survives the seam.
        assert "agents_file" not in seen
        assert propagated is not supplied

    run_async(case())


def test_argparse_agents_file_stays_an_exact_concrete_path() -> None:
    """argv keeps its shape: an exact ``str`` in, the same frozen text out.

    The programmatic gate has to accept what the CLI produces, or the two doors
    disagree and ``main()`` locks itself out of its own daemon. The text is not
    rebuilt as a ``Path``, so the operator's spelling reaches the rendered unit
    and the daemon byte-for-byte.
    """
    parser = arsd_main.build_arg_parser()
    ns = parser.parse_args(
        ["--supervisor-root", "/tmp/sv", "--agents-file", AGENTS_FILE_SPELLING]
    )
    assert type(ns.agents_file) is str
    assert ns.agents_file == AGENTS_FILE_SPELLING
    assert arsd_operand.capture_agents_file(ns.agents_file) == AGENTS_FILE_SPELLING


# --- reading an operand is not inspecting it --------------------------------
#
# The type gate above settles what the operand *is*. These tests cover the two
# halves it cannot settle on its own: the *result* of the one permitted read
# has to be admitted too (a concrete path keeps its text in an assignable slot,
# so the read can hand back caller code), and the daemon's own surfaces —
# ``supervisor_root`` and ``socket_path``, the other two operands of the very
# same overlap matrix — must be admitted before they are coerced. A matrix is
# exactly as strong as its weakest operand.


class _LeaseWall(Exception):
    """Sentinel proving the daemon's pure gates let a layout through."""


def _lease_wall(_root):
    raise _LeaseWall


def _poisoned_exact_path(text: str, poison: str) -> Path:
    """An exact concrete ``Path`` whose one textual read answers ``poison``.

    ``pytest.skip`` — never a silent pass — if an interpreter makes
    ``PurePath._str`` unpoisonable: the repair does not depend on the slot being
    assignable (it refuses anything non-exact), but this test's premise does.
    """
    node = Path(text)
    str(node)
    try:
        node._str = poison  # type: ignore[attr-defined]
    except AttributeError:
        pytest.skip("PurePath._str is not assignable on this interpreter")
    if str(node) is not poison:
        pytest.skip(
            "this interpreter does not return the poisoned PurePath._str from "
            "str(); F3's premise does not hold here"
        )
    assert type(node) is _EXACT_PATH_TYPE
    return node


def test_serve_daemon_refuses_a_poisoned_agents_file_read(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED-3 (F3): a poisoned ``_str`` carried a control-bearing root to the lease.

    ``PyObject_Str`` validates its result with ``PyUnicode_Check``, which admits
    subclasses, and ``PurePath.__str__`` returns the assignable ``_str`` slot.
    So an operand of the *exact* admitted type can still hand the gate a ``str``
    subclass, and every ``strip``/``for ch in``/``startswith`` after that point
    is attacker code.

    The lies are only believed by the *text* rules: ``H[:1]`` slicing inside
    ``posixpath`` returns a genuine ``str``, so ``Path(H)`` parses the **real**
    buffer. The result is a Binding root the shape rules would have refused —
    control-bearing — reaching the lease and the per-Run factory, while the
    overlap matrix reports "disjoint" about the real spelling.
    """

    async def case():
        real = "/opt/ars-binding\nX"

        class _PoisonText(str):
            def __str__(self) -> str:
                return self

            def strip(self, *args, **kwargs) -> str:
                return "/opt/ars-binding"

            def __iter__(self):
                return iter("/opt/ars-binding")

            def startswith(self, *args, **kwargs) -> bool:
                return True

        poison = _PoisonText(real)
        supplied = _poisoned_exact_path("/opt/ars-binding", poison)

        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise _LeaseWall

        def boom_reconcile(_root):
            reached.append("reconcile")
            raise AssertionError("reconcile must not run")

        def boom_handlers(**_kwargs):
            reached.append("handlers")
            raise AssertionError("handlers must not be constructed")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
        monkeypatch.setattr(arsd_main.handlers, "ArsdHandlers", boom_handlers)

        outcome: list[str] = []
        message = ""
        with _record_fs_queries(monkeypatch) as log:
            try:
                await arsd_main.serve_daemon(
                    socket_path=str(sock_path(sock_root)),
                    supervisor_root=str(supervisor_root(sock_root)),
                    policy=same_uid_policy(),
                    agents_file=supplied,
                    install_signals=False,
                )
            except arsd_main.DaemonStartupError as err:
                outcome.append("refused")
                message = str(err)
            except _LeaseWall:
                outcome.append("lease")

        assert outcome == ["refused"], (
            "a Binding root whose text was never admitted reached the lease"
        )
        assert "agent" in message.lower()
        # Sanitized: neither the advertised nor the real spelling is quoted back.
        assert "/opt/ars-binding" not in message
        assert reached == []
        assert log.calls == []

    run_async(case())


def test_serve_daemon_admits_supervisor_root_before_any_path_protocol_call(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED-4(a) (F4): ``Path(supervisor_root)`` ran caller code before admission.

    ``Path()`` calls ``os.fspath``, which invokes ``__fspath__`` — arbitrary
    caller code — on the pre-``BindingReader`` startup path, *before* the overlap
    matrix. An embedder can therefore make daemon startup lstat the operator
    Binding root, which is precisely the read the established contract forbids.
    """

    async def case():
        class _LstatOnPathProtocol:
            def __fspath__(self) -> str:
                # The forbidden pre-BindingReader read, swallowed so the daemon
                # keeps going and the *refusal path* is what the test measures.
                with contextlib.suppress(OSError):
                    os.lstat(AGENTS_FILE_SPELLING)
                return "relative-sv"

        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise _LeaseWall

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.chdir(sock_root)

        outcome: list[str] = []
        with _record_fs_queries(monkeypatch) as log:
            try:
                await arsd_main.serve_daemon(
                    socket_path=str(sock_path(sock_root)),
                    supervisor_root=_LstatOnPathProtocol(),
                    policy=same_uid_policy(),
                    agents_file=str(make_agents_file(sock_root)),
                    install_signals=False,
                )
            except arsd_main.DaemonStartupError:
                outcome.append("refused")
            except _LeaseWall:
                outcome.append("lease")

        assert outcome == ["refused"]
        assert reached == []
        assert log.touching(Path(AGENTS_FILE_SPELLING)) == []
        assert log.calls == []

    run_async(case())


@pytest.mark.parametrize("operand", ("socket_path", "supervisor_root"))
@pytest.mark.parametrize("kind", _LYING_AGENTS_FILE_KINDS)
def test_serve_daemon_refuses_an_inexact_surface_before_lease_or_query(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, operand: str, kind: str
) -> None:
    """RED-4(b) (F4): the matrix's other two operands were never admitted.

    ``agents_file`` was gated by type identity while the two surfaces it is
    compared against were coerced with ``Path()``. A hostile object can then
    show the matrix one location and hand the kernel another.
    """

    async def case():
        real = (
            str(sock_path(sock_root))
            if operand == "socket_path"
            else str(supervisor_root(sock_root))
        )
        hostile, probes = _lying_agents_file(kind, real)

        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise _LeaseWall

        def boom_reconcile(_root):
            reached.append("reconcile")
            raise AssertionError("reconcile must not run")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)

        kwargs: dict = {
            "socket_path": str(sock_path(sock_root)),
            "supervisor_root": str(supervisor_root(sock_root)),
            "policy": same_uid_policy(),
            "agents_file": AGENTS_FILE_SPELLING,
            "install_signals": False,
        }
        kwargs[operand] = hostile

        outcome: list[str] = []
        message = ""
        with _record_fs_queries(monkeypatch) as log:
            try:
                await arsd_main.serve_daemon(**kwargs)
            except arsd_main.DaemonStartupError as err:
                outcome.append("refused")
                message = str(err)
            except _LeaseWall:
                outcome.append("lease")

        assert outcome == ["refused"], f"an inexact {operand} reached the lease"
        assert AGENTS_FILE_SPELLING not in message and real not in message
        assert probes == []
        assert reached == []
        assert log.calls == []

    run_async(case())


def test_agents_file_operand_is_unreachable_after_capture(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-1 — lasting guard, **not** RED evidence: green before and after.

    The durable replacement for the transitional read-count proof. A late read
    of the caller's operand — anywhere after capture — would answer with the
    mutated text here, and the mutated text names the supervisor root itself,
    so the overlap matrix would have refused the layout. Reaching ``rc == 0``
    with the pre-mutation value propagated is the whole property: after capture
    there is no name left through which the operand can be read again.
    """

    async def case():
        overlapping = str(supervisor_root(sock_root))
        real_agents = str(make_agents_file(sock_root))
        supplied = Path(real_agents)
        str(supplied)
        try:
            supplied._str = overlapping  # type: ignore[attr-defined]
        except AttributeError:
            pytest.skip("PurePath._str is not assignable on this interpreter")
        if str(supplied) != overlapping:
            pytest.skip("PurePath._str is not observable through str() here")
        supplied._str = real_agents  # type: ignore[attr-defined]

        real_lease = arsd_main.acquire_daemon_instance_lease

        def mutating_lease(root):
            # Any later reader of the operand now gets a different answer.
            supplied._str = overlapping  # type: ignore[attr-defined]
            return real_lease(root)

        monkeypatch.setattr(
            arsd_main, "acquire_daemon_instance_lease", mutating_lease
        )

        seen: dict = {}
        real_cls = arsd_main.handlers.ArsdHandlers

        def capturing(**kwargs):
            seen.update(kwargs)
            passthrough = dict(kwargs)
            passthrough.pop("agents", None)
            passthrough.pop("supervisor_root", None)
            passthrough["run_task_factory"] = CompletingFactory()
            return real_cls(**passthrough)

        monkeypatch.setattr(arsd_main.handlers, "ArsdHandlers", capturing)
        stop = asyncio.Event()
        stop.set()
        rc = await arsd_main.serve_daemon(
            socket_path=sock_path(sock_root),
            supervisor_root=supervisor_root(sock_root),
            policy=same_uid_policy(),
            agents_file=supplied,
            stop_event=stop,
            install_signals=False,
        )
        # The overlap decision used the pre-mutation text: the mutated spelling
        # *is* the supervisor root, and would have been refused.
        assert rc == 0
        assert seen["agents"].ids() == ("native-agent",)
        # The operand really did start answering differently, and nothing after
        # capture ever asked it again.
        assert str(supplied) == overlapping

    run_async(case())


# A synthetic tree no test creates: only its *spelling* is used, so a shared
# ancestor is a text fact, never a directory anyone has to make.
_SHARED = "/opt/ars-shared-ancestor"


def _containment_cases() -> dict[str, dict[str, str]]:
    """Layouts where an ARS-owned surface would land inside the operator's file.

    ARS never creates, writes, repairs, or migrates the agents file, so a
    daemon-owned surface that sits on top of it — or under it — would put that
    guarantee in the hands of a directory layout. Every case here is equality or
    containment, including the alias spellings of both, because ``//x``, ``.``
    and ``..`` hops all reach the same place the kernel does.
    """
    return {
        "agents_file_equals_supervisor_root": {
            "agents_file": f"{_SHARED}/sv",
            "supervisor_root": f"{_SHARED}/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "supervisor_root_contains_agents_file": {
            "agents_file": f"{_SHARED}/sv/agents.toml",
            "supervisor_root": f"{_SHARED}/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "agents_file_contains_supervisor_root": {
            "agents_file": _SHARED,
            "supervisor_root": f"{_SHARED}/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "socket_directory_contains_agents_file": {
            "agents_file": f"{_SHARED}/s/agents.toml",
            "supervisor_root": "/tmp/ars-elsewhere/sv",
            "socket_path": f"{_SHARED}/s/arsd.sock",
        },
        # Alias spellings of the same containment, on either operand.
        "double_slash_agents_file_alias": {
            "agents_file": f"/{_SHARED}/sv",
            "supervisor_root": f"{_SHARED}/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "double_slash_surface_alias": {
            "agents_file": f"{_SHARED}/sv",
            "supervisor_root": f"/{_SHARED}/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "dot_hop_in_surface": {
            "agents_file": f"{_SHARED}/sv",
            "supervisor_root": f"{_SHARED}/./sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "dot_dot_hop_in_surface": {
            "agents_file": f"{_SHARED}/sv",
            "supervisor_root": f"{_SHARED}/neutral/../sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "dot_dot_hop_in_agents_file": {
            "agents_file": f"{_SHARED}/neutral/../sv",
            "supervisor_root": f"{_SHARED}/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        # The as-written spelling walks a *different* tree than the collapsed
        # one, and the kernel really does traverse both.
        "surface_normalizes_into_the_agents_file": {
            "agents_file": f"{_SHARED}/sv",
            "supervisor_root": f"/tmp/ars-elsewhere/../..{_SHARED}/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        # The filesystem root contains — and is contained by — everything.
        "agents_file_is_the_filesystem_root": {
            "agents_file": "/",
            "supervisor_root": "/tmp/ars-elsewhere/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "agents_file_normalizes_to_the_filesystem_root": {
            "agents_file": "/opt/..",
            "supervisor_root": "/tmp/ars-elsewhere/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
    }


def _sibling_layouts() -> dict[str, dict[str, str]]:
    """Layouts that merely *share a parent*, and are ordinary and admitted.

    The predecessor of this rule refused these too, because under the retired
    Binding architecture any kernel walk over a shared ancestor was itself the
    forbidden read — the per-Run reader had to be the first and only one. That
    premise is gone: the registry is opened by ARS itself, once, at startup,
    before the lease and before any state write. Refusing a sibling layout now
    would protect nothing while making ``~/ars/agents.toml`` beside
    ``~/ars/state`` an unstartable daemon.
    """
    return {
        "supervisor_root_beside_agents_file": {
            "agents_file": f"{_SHARED}/conf/agents.toml",
            "supervisor_root": f"{_SHARED}/sv",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "socket_beside_agents_file": {
            "agents_file": f"{_SHARED}/conf/agents.toml",
            "supervisor_root": "/tmp/ars-elsewhere/sv",
            "socket_path": f"{_SHARED}/s/arsd.sock",
        },
        "prefix_sibling_supervisor_root": {
            "agents_file": f"{_SHARED}/conf",
            "supervisor_root": f"{_SHARED}/conf-state",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
        "top_level_component_shared": {
            "agents_file": "/opt/ars-conf/agents.toml",
            "supervisor_root": "/opt/ars-state",
            "socket_path": "/tmp/ars-elsewhere/s/arsd.sock",
        },
    }


@pytest.mark.parametrize("case", sorted(_sibling_layouts()))
def test_serve_daemon_admits_a_sibling_layout_and_reaches_the_parse(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """The gate refuses containment, not every layout under one parent.

    Reaching the registry parse — which then fails closed on a file that was
    never created — is the proof that the overlap matrix let the layout through.
    Without this the whole class could be "fixed" by refusing everything.
    """

    async def run_case():
        overrides = _sibling_layouts()[case]

        def boom_lease(_root):  # pragma: no cover - must never run
            raise AssertionError("the parse must fail closed before the lease")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                policy=same_uid_policy(), install_signals=False, **overrides
            )
        message = str(err.value).lower()
        assert "overlap" not in message
        assert "registry_absent" in message

    run_async(run_case())


@pytest.mark.parametrize("case", sorted(_containment_cases()))
def test_serve_daemon_refuses_containment_before_lease_or_query(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """A daemon-owned write must never be able to land in operator configuration."""

    async def run_case():
        overrides = _containment_cases()[case]
        reached: list[str] = []

        def boom_lease(_root):
            reached.append("lease")
            raise AssertionError("lease must not run for an overlapping layout")

        def boom_reconcile(_root):
            reached.append("reconcile")
            raise AssertionError("reconcile must not run")

        def boom_handlers(**_kwargs):
            reached.append("handlers")
            raise AssertionError("handlers must not be constructed")

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
        monkeypatch.setattr(arsd_main.handlers, "ArsdHandlers", boom_handlers)

        with _record_fs_queries(monkeypatch) as log:
            with pytest.raises(arsd_main.DaemonStartupError) as err:
                await arsd_main.serve_daemon(
                    policy=same_uid_policy(), install_signals=False, **overrides
                )

        message = str(err.value).lower()
        assert "agent" in message
        assert "overlap" in message or "inside" in message
        assert reached == []
        # The ordering claim, stated as the absence of every query: a refusal
        # that merely precedes the *lease* would still have let the surface
        # resolution walk the Binding root's own ancestors.
        assert log.calls == []

    run_async(run_case())


def test_serve_daemon_accepts_a_genuinely_disjoint_operator_layout(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paired guard: the gate refuses shared components, not every real layout.

    Binding under ``/opt``, daemon state and socket under the disposable test
    root: no non-root component is shared, so the layout must clear the pure
    gate and reach the lease. Without this the whole class could be "fixed" by
    refusing everything.
    """

    class _LeaseReached(Exception):
        pass

    async def case():
        def wall(_root):
            raise _LeaseReached

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", wall)
        with pytest.raises(_LeaseReached):
            await arsd_main.serve_daemon(
                socket_path=str(sock_path(sock_root)),
                supervisor_root=str(supervisor_root(sock_root)),
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                install_signals=False,
            )

    run_async(case())


# --- startup fail-closed --------------------------------------------------


def test_zero_mappings_refuse_before_listen(sock_root: Path) -> None:
    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=server.CallerPolicy({}),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        assert "zero" in str(err.value).lower() or "mapping" in str(err.value).lower()
        assert not path.exists()

    run_async(case())


def test_root_euid_refused_before_reconcile_or_listen(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        order: list[str] = []

        def boom_reconcile(_root):
            order.append("reconcile")
            raise AssertionError("reconcile must not run under root refusal")

        monkeypatch.setattr(arsd_main, "geteuid", lambda: 0)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        assert "root" in str(err.value).lower()
        assert SECRET_SENTINEL not in str(err.value)
        assert order == []
        assert not path.exists()

    run_async(case())


def test_reconcile_runs_before_listen_and_failure_means_nothing_listens(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        order: list[str] = []

        def failing_reconcile(_root):
            order.append("reconcile")
            raise RuntimeError("injected reconcile failure")

        real_start = server.ArsdServer.start

        async def tracking_start(self):
            order.append("listen")
            return await real_start(self)

        monkeypatch.setattr(arsd_main.reconcile, "reconcile", failing_reconcile)
        monkeypatch.setattr(server.ArsdServer, "start", tracking_start)
        with pytest.raises(RuntimeError, match="injected reconcile failure"):
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        assert order == ["reconcile"]
        assert not path.exists()

    run_async(case())


def test_reconcile_before_listen_ordering_success(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    order: list[str] = []

    real_reconcile = arsd_main.reconcile.reconcile

    def tracking_reconcile(supervisor_root_path):
        order.append("reconcile")
        return real_reconcile(supervisor_root_path)

    real_start = server.ArsdServer.start

    async def tracking_start(self):
        order.append("listen")
        return await real_start(self)

    monkeypatch.setattr(arsd_main.reconcile, "reconcile", tracking_reconcile)
    monkeypatch.setattr(server.ArsdServer, "start", tracking_start)
    with running_daemon(path, root):
        assert order == ["reconcile", "listen"]


def test_same_root_second_daemon_fails_before_reconcile(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    path2 = sock_root / "s2" / "arsd.sock"
    order: list[str] = []

    def boom_reconcile(_root):
        order.append("reconcile")
        raise AssertionError("second daemon must not reconcile")

    with running_daemon(path, root):
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)

        async def contender():
            with pytest.raises(arsd_main.DaemonStartupError) as err:
                await arsd_main.serve_daemon(
                    socket_path=path2,
                    supervisor_root=root,
                    policy=same_uid_policy(),
                    agents_file=str(make_agents_file(sock_root)),
                    run_task_factory=CompletingFactory(),
                    install_signals=False,
                )
            message = str(err.value).lower()
            assert "already" in message or "lease" in message or "lock" in message
            assert SECRET_SENTINEL not in str(err.value)
            assert order == []
            assert not path2.exists()

        run_async(contender())


def test_different_supervisor_roots_do_not_conflict(sock_root: Path) -> None:
    path_a = sock_path(sock_root)
    root_a = supervisor_root(sock_root)
    path_b = sock_root / "s-b" / "arsd.sock"
    root_b = sock_root / "sv-b"
    with running_daemon(path_a, root_a):
        with running_daemon(path_b, root_b):
            wait_for_socket_sync(path_a)
            wait_for_socket_sync(path_b)


def test_daemon_lease_acquired_before_reconcile_and_released_on_shutdown(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    order: list[str] = []

    real_acquire = arsd_main.acquire_daemon_instance_lease

    def tracking_acquire(supervisor_root_path):
        order.append("lease")
        lease = real_acquire(supervisor_root_path)
        real_release = lease.release

        def tracking_release():
            order.append("release")
            return real_release()

        lease.release = tracking_release  # type: ignore[method-assign]
        return lease

    real_reconcile = arsd_main.reconcile.reconcile

    def tracking_reconcile(supervisor_root_path):
        order.append("reconcile")
        return real_reconcile(supervisor_root_path)

    real_start = server.ArsdServer.start

    async def tracking_start(self):
        order.append("listen")
        return await real_start(self)

    monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)
    monkeypatch.setattr(arsd_main.reconcile, "reconcile", tracking_reconcile)
    monkeypatch.setattr(server.ArsdServer, "start", tracking_start)

    with running_daemon(path, root):
        assert order == ["lease", "reconcile", "listen"]
        lock_path = root / "arsd" / "daemon.lock"
        assert lock_path.is_file()
        assert not lock_path.is_symlink()
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600

    assert order == ["lease", "reconcile", "listen", "release"]
    # No unlink/inode race: lock file may remain; lease must be released.
    assert lock_path.is_file()

    # After clean release, a new daemon for the same root can acquire again.
    order.clear()
    with running_daemon(path, root):
        assert order[:3] == ["lease", "reconcile", "listen"]


# -- R8 B1 durable supervisor root + lease parent ------------------------------


def test_r8_b1_durable_root_before_flock_reconcile_socket(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """durable_secure_mkdir(root) must precede flock, reconcile, and listen."""
    from agent_run_supervisor.event_store import durable_secure_mkdir

    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    order: list[str] = []
    real_durable = durable_secure_mkdir
    real_flock = arsd_main.fcntl.flock

    def tracking_durable(target):
        order.append(f"durable:{Path(target).name}")
        return real_durable(target)

    def tracking_flock(fd, op):
        order.append("flock")
        return real_flock(fd, op)

    real_reconcile = arsd_main.reconcile.reconcile

    def tracking_reconcile(supervisor_root_path):
        order.append("reconcile")
        return real_reconcile(supervisor_root_path)

    real_start = server.ArsdServer.start

    async def tracking_start(self):
        order.append("listen")
        return await real_start(self)

    monkeypatch.setattr(arsd_main, "durable_secure_mkdir", tracking_durable)
    monkeypatch.setattr(
        "agent_run_supervisor.arsd.__main__.durable_secure_mkdir", tracking_durable
    )
    monkeypatch.setattr(arsd_main.fcntl, "flock", tracking_flock)
    monkeypatch.setattr(arsd_main.reconcile, "reconcile", tracking_reconcile)
    monkeypatch.setattr(server.ArsdServer, "start", tracking_start)

    with running_daemon(path, root):
        assert any(x.startswith("durable:") for x in order)
        assert "flock" in order
        assert "reconcile" in order
        assert "listen" in order
        root_idx = next(i for i, x in enumerate(order) if x.startswith("durable:"))
        assert root_idx < order.index("flock")
        assert order.index("flock") < order.index("reconcile")
        assert order.index("reconcile") < order.index("listen")
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert (root / "arsd").is_dir()
        assert stat.S_IMODE((root / "arsd").stat().st_mode) == 0o700
        assert not (root / "arsd").is_symlink()


def test_r8_b1_parent_fsync_failure_blocks_flock_reconcile_listen(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parent-dir fsync failure during durable mkdir must abort before lease ops."""
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    parent = sock_root.resolve()
    real_fsync = os.fsync
    order: list[str] = []

    def boom_parent(fd: int) -> None:
        st = os.fstat(fd)
        if (
            parent.exists()
            and stat.S_ISDIR(st.st_mode)
            and st.st_ino == parent.stat().st_ino
        ):
            order.append("parent_fsync_boom")
            raise OSError(5, "injected parent fsync failure")
        return real_fsync(fd)

    def boom_flock(*_a, **_k):
        order.append("flock")
        raise AssertionError("flock must not run after durable mkdir failure")

    def boom_reconcile(_root):
        order.append("reconcile")
        raise AssertionError("reconcile must not run after durable mkdir failure")

    monkeypatch.setattr(os, "fsync", boom_parent)
    monkeypatch.setattr(arsd_main.fcntl, "flock", boom_flock)
    monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)

    async def case():
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        message = str(err.value).lower()
        assert "lease" in message or "prepare" in message or "daemon" in message
        assert "injected" not in message
        assert SECRET_SENTINEL not in message
        assert "flock" not in order
        assert "reconcile" not in order
        assert not path.exists()

    run_async(case())


@pytest.mark.parametrize("kind", ("symlink_root", "file_root"))
def test_r8_b1_supervisor_root_symlink_or_nondir_refused(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    target = sock_root / "evil-root-target"
    target.mkdir(parents=True, exist_ok=True)
    if kind == "symlink_root":
        root.symlink_to(target)
    else:
        root.parent.mkdir(parents=True, exist_ok=True)
        root.write_bytes(b"not-a-directory")

    order: list[str] = []

    def boom_reconcile(_root):
        order.append("reconcile")
        raise AssertionError("reconcile must not run for unsafe supervisor root")

    monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)

    async def case():
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        message = str(err.value)
        assert SECRET_SENTINEL not in message
        assert str(target) not in message
        assert "lease" in message.lower() or "prepare" in message.lower()
        assert order == []
        assert not path.exists()

    run_async(case())


def test_r8_b1_existing_root_corrected_to_0700(
    sock_root: Path,
) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o755)
    assert stat.S_IMODE(root.stat().st_mode) == 0o755

    with running_daemon(path, root):
        assert root.is_dir() and not root.is_symlink()
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        lock_dir = root / "arsd"
        assert lock_dir.is_dir() and not lock_dir.is_symlink()
        assert stat.S_IMODE(lock_dir.stat().st_mode) == 0o700


def test_r8_b1_no_service_or_systemd_in_durable_lease_path(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Durable lease prep must not touch service/systemd surfaces."""
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    hits: list[str] = []

    def boom_render(*_a, **_k):
        hits.append("render_service_unit")
        raise AssertionError("service unit render must not run in daemon start")

    monkeypatch.setattr(arsd_main, "render_service_unit", boom_render)
    with running_daemon(path, root):
        assert path.exists()
    assert hits == []


def test_root_refusal_still_precedes_lease_and_reconcile(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        order: list[str] = []

        def boom_lease(_root):
            order.append("lease")
            raise AssertionError("lease must not run under root refusal")

        def boom_reconcile(_root):
            order.append("reconcile")
            raise AssertionError("reconcile must not run under root refusal")

        monkeypatch.setattr(arsd_main, "geteuid", lambda: 0)
        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", boom_lease)
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        assert "root" in str(err.value).lower()
        assert order == []

    run_async(case())


@pytest.mark.parametrize(
    "kind",
    ("symlink_dir", "dangling_symlink", "regular_file"),
)
def test_lease_lock_dir_symlink_or_nondir_refused_before_reconcile(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Lock-directory must be a real non-symlink dir; never follow/write through it."""
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_dir = root / "arsd"
    target = sock_root / "evil-lock-target"
    target.mkdir(parents=True, exist_ok=True)
    sentinel = target / "SENTINEL_MUST_NOT_BE_TOUCHED"
    sentinel.write_bytes(b"untouched")
    target_before = sorted(p.name for p in target.iterdir())

    if kind == "symlink_dir":
        lock_dir.symlink_to(target)
    elif kind == "dangling_symlink":
        lock_dir.symlink_to(sock_root / "missing-lock-target")
    else:
        lock_dir.write_bytes(b"not-a-directory")

    order: list[str] = []

    def boom_reconcile(_root):
        order.append("reconcile")
        raise AssertionError("reconcile must not run when lease dir is unsafe")

    monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)

    async def case():
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        message = str(err.value)
        assert SECRET_SENTINEL not in message
        assert str(target) not in message
        assert "lease" in message.lower() or "lock" in message.lower()
        assert order == []
        assert not path.exists()
        assert sorted(p.name for p in target.iterdir()) == target_before
        assert sentinel.read_bytes() == b"untouched"
        if kind == "symlink_dir":
            assert lock_dir.is_symlink()
            assert not (target / "daemon.lock").exists()
        elif kind == "dangling_symlink":
            assert lock_dir.is_symlink()
        else:
            assert lock_dir.is_file() and not lock_dir.is_dir()

    run_async(case())


def test_lease_dirfd_close_failure_after_acquire_releases_lock_fd(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dirfd close failure after lock acquire must not leak the lock or raw OSError."""
    root = supervisor_root(sock_root)
    root.mkdir(parents=True, exist_ok=True)

    real_open = arsd_main.os.open
    real_close = arsd_main.os.close
    real_flock = arsd_main.fcntl.flock
    state: dict[str, object] = {
        "dir_fd": None,
        "lock_fd": None,
        "close_dir_calls": 0,
    }
    closed: set[int] = set()
    unlocked: set[int] = set()

    def tracking_open(path, flags, mode=0o777, *args, dir_fd=None, **kwargs):
        if dir_fd is None:
            return real_open(path, flags, mode, *args, **kwargs)
        # Lease lock file is opened relative to the verified lock-dir fd.
        fd = real_open(path, flags, mode, *args, dir_fd=dir_fd, **kwargs)
        state["dir_fd"] = dir_fd
        state["lock_fd"] = fd
        return fd

    def tracking_close(fd: int) -> None:
        if fd == state["dir_fd"] and state["close_dir_calls"] == 0:
            state["close_dir_calls"] = 1
            try:
                real_close(fd)
            finally:
                closed.add(fd)
                raise OSError(5, "injected dirfd close failure")
        closed.add(fd)
        return real_close(fd)

    def tracking_flock(fd: int, operation: int) -> None:
        if operation == fcntl.LOCK_UN:
            unlocked.add(fd)
        return real_flock(fd, operation)

    monkeypatch.setattr(arsd_main.os, "open", tracking_open)
    monkeypatch.setattr(arsd_main.os, "close", tracking_close)
    monkeypatch.setattr(arsd_main.fcntl, "flock", tracking_flock)

    with pytest.raises(arsd_main.DaemonStartupError) as err:
        arsd_main.acquire_daemon_instance_lease(root)
    assert type(err.value) is arsd_main.DaemonStartupError
    assert "lease" in str(err.value).lower() or "lock" in str(err.value).lower()
    assert "injected dirfd" not in str(err.value)
    assert state["lock_fd"] is not None
    lock_fd = int(state["lock_fd"])  # type: ignore[arg-type]
    assert lock_fd in closed
    assert lock_fd in unlocked
    assert state["close_dir_calls"] == 1

    # No surviving lease: a fresh acquire must succeed.
    monkeypatch.undo()
    lease = arsd_main.acquire_daemon_instance_lease(root)
    try:
        assert lease._fd >= 0
    finally:
        lease.release()


def test_lease_dirfd_close_failure_on_open_error_does_not_leak_or_raw_oserror(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Earlier acquire failure + dirfd close failure: no lock leak, sanitized error."""
    root = supervisor_root(sock_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_dir = (root / "arsd").resolve()

    real_open = arsd_main.os.open
    real_close = arsd_main.os.close
    state: dict[str, object] = {"dir_fd": None, "lock_opened": False}
    closed: set[int] = set()

    def tracking_open(path, flags, mode=0o777, *args, dir_fd=None, **kwargs):
        if dir_fd is not None:
            state["lock_opened"] = True
            raise OSError(5, "injected lock open failure")
        fd = real_open(path, flags, mode, *args, **kwargs)
        # Track the lease-parent fd from `_open_lock_dir_fd` (last O_DIRECTORY
        # open of the lock dir after durable_secure_mkdir fsync opens).
        try:
            opened = Path(path).resolve()
        except (TypeError, OSError, RuntimeError):
            opened = None
        if (
            opened == lock_dir
            and (flags & getattr(os, "O_DIRECTORY", 0))
            and (flags & getattr(os, "O_NOFOLLOW", 0))
        ):
            state["dir_fd"] = fd
        return fd

    def tracking_close(fd: int) -> None:
        if fd == state["dir_fd"] and fd not in closed:
            try:
                real_close(fd)
            finally:
                closed.add(fd)
                raise OSError(5, "injected dirfd close failure after open error")
        closed.add(fd)
        return real_close(fd)

    monkeypatch.setattr(arsd_main.os, "open", tracking_open)
    monkeypatch.setattr(arsd_main.os, "close", tracking_close)

    with pytest.raises(arsd_main.DaemonStartupError) as err:
        arsd_main.acquire_daemon_instance_lease(root)
    message = str(err.value).lower()
    assert "lease" in message or "lock" in message or "open" in message
    assert "injected" not in str(err.value)
    assert state["lock_opened"] is True
    assert state["dir_fd"] in closed

    # No lock held: subsequent acquire works.
    monkeypatch.undo()
    lease = arsd_main.acquire_daemon_instance_lease(root)
    lease.release()


# --- client typed errors --------------------------------------------------


@pytest.mark.parametrize("code", sorted(protocol.ERROR_CODES))
def test_client_maps_every_v1_error_code(code: str, sock_root: Path) -> None:
    path = sock_path(sock_root)
    path.parent.mkdir(mode=0o700)
    ready = threading.Event()

    def serve_once() -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(1)
        ready.set()
        conn, _addr = listener.accept()
        try:
            raw = b""
            while b"\n" not in raw:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            frame = protocol.decode_frame(raw)
            reply = protocol.build_error(
                frame.get("request_id"),
                code,
                f"synthetic {code} contains {SECRET_SENTINEL}",
            )
            conn.sendall(protocol.encode_frame(reply))
        finally:
            conn.close()
            listener.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)

    exc_type = arsd_client.ERROR_CODE_TO_EXCEPTION[code]
    with arsd_client.ArsdClient(path) as cli:
        with pytest.raises(exc_type) as err:
            cli.server_info()
        assert err.value.code == code
        assert SECRET_SENTINEL not in str(err.value)
        assert SECRET_SENTINEL not in err.value.message
        assert isinstance(err.value, arsd_client.ArsdClientError)
    thread.join(timeout=5)


def test_error_frame_null_request_id_is_permitted_wire_shape(sock_root: Path) -> None:
    """v1 ``build_error(None, ...)`` is an explicit connection/pre-parse shape."""
    path = sock_path(sock_root)
    path.parent.mkdir(mode=0o700)
    ready = threading.Event()

    def serve_once() -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(1)
        ready.set()
        conn, _addr = listener.accept()
        try:
            _ = conn.recv(4096)
            reply = protocol.build_error(
                None, protocol.SHUTTING_DOWN, f"down {SECRET_SENTINEL}"
            )
            conn.sendall(protocol.encode_frame(reply))
        finally:
            conn.close()
            listener.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)
    with arsd_client.ArsdClient(path) as cli:
        with pytest.raises(arsd_client.ArsdShuttingDownError) as err:
            cli.server_info(request_id="any-id")
        assert err.value.code == protocol.SHUTTING_DOWN
        assert SECRET_SENTINEL not in str(err.value)
        assert SECRET_SENTINEL not in err.value.message
    thread.join(timeout=5)


def test_mismatched_non_null_error_request_id_is_correlation_failure(
    sock_root: Path,
) -> None:
    path = sock_path(sock_root)
    path.parent.mkdir(mode=0o700)
    ready = threading.Event()

    def serve_once() -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(1)
        ready.set()
        conn, _addr = listener.accept()
        try:
            _ = conn.recv(4096)
            reply = protocol.build_error(
                "other-id",
                protocol.CAPACITY_EXHAUSTED,
                f"capacity {SECRET_SENTINEL}",
            )
            conn.sendall(protocol.encode_frame(reply))
        finally:
            conn.close()
            listener.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)
    cli = arsd_client.ArsdClient(path)
    cli.connect()
    with pytest.raises(arsd_client.ArsdClientError) as err:
        cli.server_info(request_id="expected-id")
    assert err.value.code == protocol.INVALID_REQUEST
    assert "mismatch" in str(err.value).lower()
    assert not isinstance(err.value, arsd_client.ArsdCapacityExhaustedError)
    assert SECRET_SENTINEL not in str(err.value)
    assert SECRET_SENTINEL not in err.value.message
    assert cli.closed is True
    thread.join(timeout=5)


def test_malformed_unknown_error_frame_closes_client(sock_root: Path) -> None:
    path = sock_path(sock_root)
    path.parent.mkdir(mode=0o700)
    ready = threading.Event()

    def serve_once() -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(1)
        ready.set()
        conn, _addr = listener.accept()
        try:
            raw = b""
            while b"\n" not in raw:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            frame = protocol.decode_frame(raw)
            rid = frame.get("request_id")
            bad = {"request_id": rid, "error": {"code": "NOT_A_V1_CODE", "message": "x"}}
            conn.sendall(protocol.encode_frame(bad))
        finally:
            conn.close()
            listener.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)
    cli = arsd_client.ArsdClient(path)
    cli.connect()
    with pytest.raises(arsd_client.ArsdClientError):
        cli.server_info()
    assert cli.closed is True
    thread.join(timeout=5)


def _follow_fake_server(path: Path, responses: list[bytes]) -> threading.Thread:
    ready = threading.Event()

    def serve() -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(1)
        ready.set()
        conn, _addr = listener.accept()
        try:
            # Consume the follow request frame.
            raw = b""
            while b"\n" not in raw:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            for payload in responses:
                try:
                    conn.sendall(payload)
                except OSError:
                    break
        finally:
            conn.close()
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)
    return thread


def test_follow_malformed_oversized_mismatched_propagate_and_close(
    sock_root: Path,
) -> None:
    path = sock_path(sock_root)
    path.parent.mkdir(mode=0o700)

    cases = [
        (b"{not-json\n", arsd_client.ArsdMalformedFrameError),
        (
            b"{" + (b"x" * (protocol.MAX_FRAME_BYTES + 8)),
            arsd_client.ArsdFrameTooLargeError,
        ),
        (
            protocol.encode_frame(protocol.build_result("wrong-id", {"follow": True})),
            arsd_client.ArsdClientError,
        ),
    ]
    for payload, exc_type in cases:
        # Unique socket path per case under the shared root.
        case_path = path.parent / f"f-{exc_type.__name__}.sock"
        thread = _follow_fake_server(case_path, [payload])
        cli = arsd_client.ArsdClient(case_path)
        cli.connect()
        stream = cli.run_events("run-x", follow=True, request_id="follow-1")
        with pytest.raises(exc_type) as err:
            next(stream)
        if exc_type is arsd_client.ArsdClientError:
            assert "mismatch" in str(err.value).lower()
        assert cli.closed is True
        thread.join(timeout=5)


def test_follow_clean_eof_ends_normally_and_closes_client(sock_root: Path) -> None:
    path = sock_path(sock_root)
    path.parent.mkdir(mode=0o700)
    # Peer accepts the follow request then closes with no bytes → clean EOF.
    thread = _follow_fake_server(path, [])
    cli = arsd_client.ArsdClient(path)
    cli.connect()
    frames = list(cli.run_events("run-x", follow=True, request_id="follow-eof"))
    assert frames == []
    assert cli.closed is True
    thread.join(timeout=5)


def test_follow_break_refuses_same_client_ops_run_persists(sock_root: Path) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    factory = PendingFactory()
    with running_daemon(path, root, factory=factory):
        sessions = storage.native_session_store(root)
        seed_session(sessions, session_id="sess-follow-break")

        cli = arsd_client.ArsdClient(path)
        cli.connect()
        accepted = cli.submit(
            request_id="s5-follow-break",
            payload=submit_payload(
                request=valid_wire_request(session_id="sess-follow-break")
            ),
        )
        run_id = accepted["run_id"]
        run_dir = Path(storage.native_event_store(root).base_dir) / run_id
        seed_events(run_dir, 3)

        stream = cli.run_events(
            run_id, from_seq=0, follow=True, follow_idle_seconds=0.1
        )
        assert hasattr(stream, "close")
        first = next(stream)
        assert first.get("follow") is True
        # Break / explicit close tears down the subscription by closing the client.
        stream.close()
        assert cli.closed is True
        with pytest.raises(arsd_client.ArsdClientError):
            cli.run_status(run_id)
        with pytest.raises(arsd_client.ArsdClientError):
            cli.server_info()

        # Fresh client: accepted Run remains; never cancelled by follow teardown.
        with arsd_client.ArsdClient(path) as fresh:
            status = fresh.run_status(run_id)
            assert status["run_id"] == run_id
            assert "result" not in status or status.get("state") == "accepted"
        assert factory.handlers is not None
        assert factory.handlers.registry.is_registered(run_id)


def test_follow_for_break_closes_client(sock_root: Path) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    factory = PendingFactory()
    with running_daemon(path, root, factory=factory):
        sessions = storage.native_session_store(root)
        seed_session(sessions, session_id="sess-follow-for")
        cli = arsd_client.ArsdClient(path)
        cli.connect()
        accepted = cli.submit(
            request_id="s5-follow-for",
            payload=submit_payload(
                request=valid_wire_request(session_id="sess-follow-for")
            ),
        )
        run_id = accepted["run_id"]
        seed_events(Path(storage.native_event_store(root).base_dir) / run_id, 3)
        for frame in cli.run_events(
            run_id, from_seq=0, follow=True, follow_idle_seconds=0.1
        ):
            assert frame.get("follow") is True
            break
        assert cli.closed is True
        with arsd_client.ArsdClient(path) as fresh:
            status = fresh.run_status(run_id)
            assert status["run_id"] == run_id
            assert "result" not in status or status.get("state") == "accepted"
        assert factory.handlers is not None
        assert factory.handlers.registry.is_registered(run_id)


def test_client_rejects_malformed_oversized_and_mismatched_responses(
    sock_root: Path,
) -> None:
    path = sock_path(sock_root)
    path.parent.mkdir(mode=0o700)
    ready = threading.Event()

    def serve_script(frames: list[bytes]) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(8)
        ready.set()
        for payload in frames:
            conn, _addr = listener.accept()
            try:
                raw = b""
                while b"\n" not in raw:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                conn.sendall(payload)
            finally:
                conn.close()
        listener.close()

    malformed = b"{not-json\n"
    oversized = b"{" + (b"x" * (protocol.MAX_FRAME_BYTES + 8))
    mismatched = protocol.encode_frame(
        protocol.build_result("other-id", {"ok": True})
    )

    thread = threading.Thread(
        target=serve_script, args=([malformed, oversized, mismatched],), daemon=True
    )
    thread.start()
    assert ready.wait(timeout=5)

    with arsd_client.ArsdClient(path) as cli:
        with pytest.raises(arsd_client.ArsdMalformedFrameError):
            cli.server_info()
    with arsd_client.ArsdClient(path) as cli:
        with pytest.raises(arsd_client.ArsdFrameTooLargeError):
            cli.server_info()
    with arsd_client.ArsdClient(path) as cli:
        with pytest.raises(arsd_client.ArsdClientError) as err:
            cli.server_info(request_id="expected-id")
        assert "mismatch" in str(err.value).lower()
        assert SECRET_SENTINEL not in str(err.value)
    thread.join(timeout=5)


def test_client_bounded_read_does_not_buffer_unbounded(sock_root: Path) -> None:
    path = sock_path(sock_root)
    path.parent.mkdir(mode=0o700)
    ready = threading.Event()

    def serve_huge() -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(1)
        ready.set()
        conn, _addr = listener.accept()
        try:
            conn.recv(64)
            chunk = b"a" * 65_536
            sent = 0
            target = protocol.MAX_FRAME_BYTES + 100_000
            while sent < target:
                try:
                    conn.sendall(chunk)
                except OSError:
                    break
                sent += len(chunk)
        finally:
            conn.close()
            listener.close()

    thread = threading.Thread(target=serve_huge, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)

    with arsd_client.ArsdClient(path) as cli:
        with pytest.raises(arsd_client.ArsdFrameTooLargeError):
            cli.server_info()
    thread.join(timeout=5)


def test_client_context_cleanup_closes_connection(sock_root: Path) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    with running_daemon(path, root):
        with arsd_client.ArsdClient(path) as cli:
            info = cli.server_info()
            assert info["api_version"] == protocol.ARSD_API_VERSION
            assert cli.closed is False
        assert cli.closed is True
        with pytest.raises(arsd_client.ArsdClientError):
            cli.server_info()


def test_client_never_silently_reconnects(sock_root: Path) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    with running_daemon(path, root):
        cli = arsd_client.ArsdClient(path)
        cli.connect()
        cli.close()
        with pytest.raises(arsd_client.ArsdClientError):
            cli.server_info()


# --- end-to-end daemon + client round-trips --------------------------------


def test_submit_status_events_cancel_session_roundtrips(sock_root: Path) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    factory = CancelFactory(mode="cancelled-terminal")
    with running_daemon(path, root, factory=factory):
        sessions = storage.native_session_store(root)
        seed_session(sessions, session_id="sess-s5-1")

        with arsd_client.ArsdClient(path) as cli:
            info = cli.server_info()
            assert info["api_version"] == 3
            assert info["supported_api_versions"] == [3]
            assert "limits" in info

            accepted = cli.submit(
                request_id="s5-submit-1",
                payload=submit_payload(
                    request=valid_wire_request(session_id="sess-s5-1")
                ),
            )
            run_id = accepted["run_id"]
            assert accepted["accepted_at"]

            status = cli.run_status(run_id)
            assert status["run_id"] == run_id

            run_dir = Path(storage.native_event_store(root).base_dir) / run_id
            seed_events(run_dir, 3)
            snapshot = cli.run_events(run_id, from_seq=0, limit=10, follow=False)
            assert len(snapshot["events"]) == 3
            assert snapshot["exhausted"] is True

            cancel = cli.run_cancel(run_id)
            assert cancel["run_id"] == run_id

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                st = cli.run_status(run_id)
                if "result" in st:
                    break
                time.sleep(0.05)
            assert "result" in cli.run_status(run_id)

            listed = cli.session_list()
            assert any(s["session_id"] == "sess-s5-1" for s in listed["sessions"])
            one = cli.session_status("sess-s5-1")
            assert one["session_id"] == "sess-s5-1"
            # The Run reached a terminal, and the Session it ran under is still
            # there and still reusable. There is no close to call on it.
            assert one["quarantine"] is None
            assert not hasattr(cli, "session_close")


def test_follow_consumes_stream_without_cancelling_run(sock_root: Path) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    factory = PendingFactory()
    with running_daemon(path, root, factory=factory):
        sessions = storage.native_session_store(root)
        seed_session(sessions, session_id="sess-follow")

        with arsd_client.ArsdClient(path) as cli:
            accepted = cli.submit(
                request_id="s5-follow-1",
                payload=submit_payload(
                    request=valid_wire_request(session_id="sess-follow")
                ),
            )
            run_id = accepted["run_id"]
            run_dir = Path(storage.native_event_store(root).base_dir) / run_id
            seed_events(run_dir, 2)

            frames = []
            for frame in cli.run_events(
                run_id, from_seq=0, follow=True, follow_idle_seconds=0.1
            ):
                frames.append(frame)
                if len(frames) >= 2:
                    break
            assert frames
            assert all(frame.get("follow") is True for frame in frames)
            # Break tears down the follow subscription by closing this client.
            assert cli.closed is True
            with pytest.raises(arsd_client.ArsdClientError):
                cli.run_status(run_id)

        # Fresh connection: follow teardown must not cancel the accepted Run.
        with arsd_client.ArsdClient(path) as cli:
            status = cli.run_status(run_id)
            assert "result" not in status or status.get("state") == "accepted"
        assert factory.handlers is not None
        assert factory.handlers.registry.is_registered(run_id)


def test_explicit_same_uid_allow_mapping_works(sock_root: Path) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    policy = arsd_main.build_caller_policy([mapping_flag()])
    with running_daemon(path, root, policy=policy):
        with arsd_client.ArsdClient(path) as cli:
            info = cli.server_info()
            assert info["api_version"] == 3
            assert info["supported_api_versions"] == [3]


# --- SIGTERM lifecycle ----------------------------------------------------


def test_sigterm_shutdown_shutting_down_unlink_bounded_exit(sock_root: Path) -> None:
    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        factory = PendingFactory()
        started = time.monotonic()
        task = asyncio.create_task(
            arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=factory,
                cancel_wait_seconds=1.0,
                shutdown_timeout=5.0,
                install_signals=True,
            )
        )
        await wait_for_socket(path)

        def submit_once() -> str:
            with arsd_client.ArsdClient(path) as cli:
                accepted = cli.submit(
                    request_id="s5-term-1",
                    payload=submit_payload(
                        request=valid_wire_request(
                            session_id=None
                        )
                    ),
                )
                return accepted["run_id"]

        run_id = await asyncio.to_thread(submit_once)

        late = arsd_client.ArsdClient(path)
        await asyncio.to_thread(late.connect)

        seen_shutting_down = threading.Event()

        def late_probe() -> None:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    late.server_info(request_id="late-after-term")
                except arsd_client.ArsdShuttingDownError:
                    seen_shutting_down.set()
                    return
                except arsd_client.ArsdClientError:
                    time.sleep(0.01)
            late.close()

        probe = threading.Thread(target=late_probe, daemon=True)
        probe.start()
        await asyncio.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)
        rc = await asyncio.wait_for(task, timeout=8)
        probe.join(timeout=3)
        elapsed = time.monotonic() - started
        assert rc == 0
        assert elapsed < 8
        assert not path.exists()
        assert seen_shutting_down.is_set()
        with contextlib.suppress(Exception):
            late.close()

        assert factory.handlers is not None
        assert not factory.handlers.registry.is_registered(run_id)

    run_async(case())


def test_r11_b3_shutdown_holds_lease_until_registry_drained(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Singleton lease stays held while a sticky Run remains registered."""

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        release = asyncio.Event()
        run_started = asyncio.Event()
        stop = asyncio.Event()
        release_order: list[str] = []

        class _StickyRunner:
            async def run(self):
                run_started.set()
                while not release.is_set():
                    try:
                        await asyncio.wait_for(release.wait(), 0.01)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        task = asyncio.current_task()
                        if task is not None:
                            task.uncancel()
                        continue

        class _StickyFactory:
            def __init__(self) -> None:
                self.handlers = None
                self.calls: list[dict] = []

            def __call__(self, *, command, run_id, prepared_handle, submitted_at):
                self.calls.append(
                    {
                        "run_id": run_id,
                        "prepared_handle": prepared_handle,
                        "submitted_at": submitted_at,
                        "command": command,
                    }
                )
                return _StickyRunner()

        factory = _StickyFactory()
        real_acquire = arsd_main.acquire_daemon_instance_lease

        def tracking_acquire(supervisor_root_path):
            lease = real_acquire(supervisor_root_path)
            real_release = lease.release

            def tracking_release():
                release_order.append("release")
                return real_release()

            lease.release = tracking_release  # type: ignore[method-assign]
            return lease

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)

        serve_task = asyncio.create_task(
            arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=factory,
                cancel_wait_seconds=0.05,
                shutdown_timeout=0.2,
                stop_event=stop,
                install_signals=False,
            )
        )
        await wait_for_socket(path)

        def submit_once() -> str:
            with arsd_client.ArsdClient(path) as cli:
                accepted = cli.submit(
                    request_id="r11-lease-1",
                    payload=submit_payload(
                        request=valid_wire_request(
                            session_id=None
                        )
                    ),
                )
                return accepted["run_id"]

        run_id = await asyncio.to_thread(submit_once)
        await asyncio.wait_for(run_started.wait(), 2.0)
        assert factory.handlers is not None
        assert factory.handlers.registry.is_registered(run_id)

        stop.set()
        # Past cancel_wait + shutdown_timeout: serve must remain pending and
        # must not release the lease while the sticky Run is still registered.
        await asyncio.sleep(0.5)
        assert not serve_task.done()
        assert release_order == []
        assert factory.handlers.registry.is_registered(run_id)

        with pytest.raises(arsd_main.DaemonStartupError) as err:
            arsd_main.acquire_daemon_instance_lease(root)
        message = str(err.value).lower()
        assert "already" in message or "lease" in message or "lock" in message
        assert SECRET_SENTINEL not in str(err.value)

        release.set()
        rc = await asyncio.wait_for(serve_task, 2.0)
        assert rc == 0
        assert release_order == ["release"]
        assert not factory.handlers.registry.is_registered(run_id)

        lease = arsd_main.acquire_daemon_instance_lease(root)
        lease.release()

    run_async(case())


def test_r11b_serve_task_cancel_holds_lease_until_registry_idle(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling serve_daemon must not release the lease while a Run is live."""

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        release = asyncio.Event()
        run_started = asyncio.Event()
        stop = asyncio.Event()
        release_order: list[str] = []

        class _StickyRunner:
            async def run(self):
                run_started.set()
                while not release.is_set():
                    try:
                        await asyncio.wait_for(release.wait(), 0.01)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        task = asyncio.current_task()
                        if task is not None:
                            task.uncancel()
                        continue

        class _StickyFactory:
            def __init__(self) -> None:
                self.handlers = None
                self.calls: list[dict] = []

            def __call__(self, *, command, run_id, prepared_handle, submitted_at):
                self.calls.append(
                    {
                        "run_id": run_id,
                        "prepared_handle": prepared_handle,
                        "submitted_at": submitted_at,
                        "command": command,
                    }
                )
                return _StickyRunner()

        factory = _StickyFactory()
        real_acquire = arsd_main.acquire_daemon_instance_lease

        def tracking_acquire(supervisor_root_path):
            lease = real_acquire(supervisor_root_path)
            real_release = lease.release

            def tracking_release():
                release_order.append("release")
                return real_release()

            lease.release = tracking_release  # type: ignore[method-assign]
            return lease

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)

        serve_task = asyncio.create_task(
            arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=factory,
                cancel_wait_seconds=0.05,
                shutdown_timeout=0.2,
                stop_event=stop,
                install_signals=False,
            )
        )
        await wait_for_socket(path)

        def submit_once() -> str:
            with arsd_client.ArsdClient(path) as cli:
                accepted = cli.submit(
                    request_id="r11b-lease-1",
                    payload=submit_payload(
                        request=valid_wire_request(
                            session_id=None
                        )
                    ),
                )
                return accepted["run_id"]

        run_id = await asyncio.to_thread(submit_once)
        await asyncio.wait_for(run_started.wait(), 2.0)
        assert factory.handlers is not None
        assert factory.handlers.registry.is_registered(run_id)

        stop.set()
        # Enter shutdown / final idle drain, then cancel the serve task itself.
        await asyncio.sleep(0.3)
        assert not serve_task.done()
        serve_task.cancel()
        await asyncio.sleep(0.5)
        assert not serve_task.done()
        assert release_order == []
        assert factory.handlers.registry.is_registered(run_id)

        with pytest.raises(arsd_main.DaemonStartupError) as err:
            arsd_main.acquire_daemon_instance_lease(root)
        message = str(err.value).lower()
        assert "already" in message or "lease" in message or "lock" in message
        assert SECRET_SENTINEL not in str(err.value)

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(serve_task, 2.0)
        assert release_order == ["release"]
        assert not factory.handlers.registry.is_registered(run_id)

        lease = arsd_main.acquire_daemon_instance_lease(root)
        lease.release()

    run_async(case())


def test_r12_cancel_during_stop_wait_closes_admission_before_lease_release(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel while blocked on stop must close admission before idle/lease release.

    Holds an accepted submit at a pre-reserve seam (stop never set). Proves the
    owned shutdown closes admission, rejects late reserve/register/dispatch, keeps
    the lease until shutdown settles, then propagates cancellation.
    """

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        stop = asyncio.Event()
        release_order: list[str] = []
        at_seam = asyncio.Event()
        release_seam = asyncio.Event()
        factory = PendingFactory()
        real_acquire = arsd_main.acquire_daemon_instance_lease

        def tracking_acquire(supervisor_root_path):
            lease = real_acquire(supervisor_root_path)
            real_release = lease.release

            def tracking_release():
                release_order.append("release")
                return real_release()

            lease.release = tracking_release  # type: ignore[method-assign]
            return lease

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)

        serve_task = asyncio.create_task(
            arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=factory,
                cancel_wait_seconds=0.05,
                shutdown_timeout=0.2,
                stop_event=stop,
                install_signals=False,
            )
        )
        await wait_for_socket(path)
        assert factory.handlers is not None
        registry = factory.handlers.registry
        real_reserve = registry.reserve

        async def gated_reserve(*, session_id):
            at_seam.set()
            await release_seam.wait()
            return await real_reserve(session_id=session_id)

        registry.reserve = gated_reserve  # type: ignore[method-assign]

        submit_error: list[BaseException] = []
        submit_done = threading.Event()

        def submit_once() -> None:
            try:
                with arsd_client.ArsdClient(path) as cli:
                    cli.submit(
                        request_id="r12-pre-reserve-1",
                        payload=submit_payload(
                            request=valid_wire_request(
                                session_id=None
                            )
                        ),
                    )
            except BaseException as exc:  # noqa: BLE001 — capture for assertions
                submit_error.append(exc)
            finally:
                submit_done.set()

        probe = threading.Thread(target=submit_once, daemon=True)
        probe.start()
        await asyncio.wait_for(at_seam.wait(), 2.0)
        # Still blocked on stop_event — R11b covered cancel after stop-triggered
        # shutdown; this proves cancel before stop.set().
        assert not stop.is_set()
        assert registry.admission_open is True
        assert factory.calls == []

        serve_task.cancel()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and registry.admission_open:
            await asyncio.sleep(0.01)
        assert registry.admission_open is False
        assert not serve_task.done()
        assert release_order == []
        with pytest.raises(arsd_main.DaemonStartupError) as held:
            arsd_main.acquire_daemon_instance_lease(root)
        held_msg = str(held.value).lower()
        assert "already" in held_msg or "lease" in held_msg or "lock" in held_msg

        release_seam.set()
        await asyncio.to_thread(submit_done.wait, 5.0)
        assert submit_done.is_set()
        assert factory.calls == []
        assert registry.active_count() == 0
        assert registry._reservations == {}
        assert submit_error
        err = submit_error[0]
        if isinstance(err, arsd_client.ArsdClientError):
            code = getattr(err, "code", None)
            if code is not None:
                assert code in {
                    protocol.SHUTTING_DOWN,
                    protocol.INTERNAL,
                    protocol.SUBMISSION_INDETERMINATE,
                }

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(serve_task, 2.0)
        assert release_order == ["release"]
        probe.join(timeout=2.0)

        lease = arsd_main.acquire_daemon_instance_lease(root)
        lease.release()

    run_async(case())


def test_r12b_lifecycle_ordinary_failure_retries_before_lease_release(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One-shot critical-phase failure must hold lease, retry, then release once."""

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        stop = asyncio.Event()
        release_order: list[str] = []
        idle_calls: list[float] = []
        at_second_idle = asyncio.Event()
        release_second_idle = asyncio.Event()
        factory = PendingFactory()
        real_acquire = arsd_main.acquire_daemon_instance_lease

        def tracking_acquire(supervisor_root_path):
            lease = real_acquire(supervisor_root_path)
            real_release = lease.release

            def tracking_release():
                release_order.append("release")
                return real_release()

            lease.release = tracking_release  # type: ignore[method-assign]
            return lease

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)

        serve_task = asyncio.create_task(
            arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=factory,
                cancel_wait_seconds=0.05,
                shutdown_timeout=0.2,
                stop_event=stop,
                install_signals=False,
            )
        )
        await wait_for_socket(path)
        assert factory.handlers is not None
        registry = factory.handlers.registry
        real_idle = registry.wait_until_idle

        async def one_shot_fail_idle():
            idle_calls.append(time.monotonic())
            if len(idle_calls) == 1:
                raise RuntimeError("injected-r12b-ordinary-lifecycle-failure")
            # The real idle wait returns without suspending on an empty
            # registry, so the retry could otherwise complete and release the
            # lease before this test task wakes. Hold the retry attempt at a
            # seam until the mid-flight assertions have run.
            at_second_idle.set()
            await release_second_idle.wait()
            return await real_idle()

        registry.wait_until_idle = one_shot_fail_idle  # type: ignore[method-assign]

        with caplog.at_level("ERROR", logger="agent_run_supervisor.arsd"):
            stop.set()
            try:
                await asyncio.wait_for(at_second_idle.wait(), 5.0)
                # Second idle attempt is provably in progress (held at the
                # seam), so these observations cannot race a legal completion.
                assert len(idle_calls) == 2
                assert release_order == []
                assert not serve_task.done()
                assert registry.admission_open is False
                gap = idle_calls[1] - idle_calls[0]
                assert gap + 1e-3 >= arsd_main._SHUTDOWN_LIFECYCLE_RETRY_DELAY
                assert any(
                    arsd_main._SHUTDOWN_LIFECYCLE_FAIL_LOG in rec.message
                    for rec in caplog.records
                )
                assert "injected-r12b-ordinary-lifecycle-failure" not in caplog.text
                assert SECRET_SENTINEL not in caplog.text

                with pytest.raises(arsd_main.DaemonStartupError) as held:
                    arsd_main.acquire_daemon_instance_lease(root)
                held_msg = str(held.value).lower()
                assert (
                    "already" in held_msg
                    or "lease" in held_msg
                    or "lock" in held_msg
                )
            finally:
                # Always unblock the held lifecycle and join the daemon task,
                # so a failed mid-flight assertion cannot orphan serve_task in
                # loop shutdown; the bare finally re-raises that original
                # assertion failure once the join succeeds.
                release_second_idle.set()
                rc = await asyncio.wait_for(serve_task, 2.0)

            assert rc == 0
            assert release_order == ["release"]

        lease = arsd_main.acquire_daemon_instance_lease(root)
        lease.release()

    run_async(case())


def test_r12b_cancel_during_lifecycle_retry_propagates_after_release(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller cancel racing one-shot lifecycle failure releases only after success."""

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        stop = asyncio.Event()
        release_order: list[str] = []
        idle_calls = 0
        gate = asyncio.Event()
        factory = PendingFactory()
        real_acquire = arsd_main.acquire_daemon_instance_lease

        def tracking_acquire(supervisor_root_path):
            lease = real_acquire(supervisor_root_path)
            real_release = lease.release

            def tracking_release():
                release_order.append("release")
                return real_release()

            lease.release = tracking_release  # type: ignore[method-assign]
            return lease

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)

        serve_task = asyncio.create_task(
            arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=factory,
                cancel_wait_seconds=0.05,
                shutdown_timeout=0.2,
                stop_event=stop,
                install_signals=False,
            )
        )
        await wait_for_socket(path)
        assert factory.handlers is not None
        registry = factory.handlers.registry
        real_idle = registry.wait_until_idle

        async def gated_one_shot_fail_idle():
            nonlocal idle_calls
            idle_calls += 1
            if idle_calls == 1:
                raise RuntimeError("injected-r12b-cancel-race-failure")
            await gate.wait()
            return await real_idle()

        registry.wait_until_idle = gated_one_shot_fail_idle  # type: ignore[method-assign]

        stop.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and idle_calls < 2:
            await asyncio.sleep(0.01)
        assert idle_calls >= 2
        assert release_order == []
        assert not serve_task.done()

        serve_task.cancel()
        await asyncio.sleep(0.2)
        assert not serve_task.done()
        assert release_order == []
        with pytest.raises(arsd_main.DaemonStartupError) as held:
            arsd_main.acquire_daemon_instance_lease(root)
        held_msg = str(held.value).lower()
        assert "already" in held_msg or "lease" in held_msg or "lock" in held_msg

        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(serve_task, 2.0)
        assert release_order == ["release"]

        lease = arsd_main.acquire_daemon_instance_lease(root)
        lease.release()

    run_async(case())


def test_r12b_permanent_lifecycle_failure_holds_lease_until_unblocked(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permanent ordinary failure keeps serve pending and lease held until unblocked."""

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        stop = asyncio.Event()
        release_order: list[str] = []
        idle_calls = 0
        allow_success = asyncio.Event()
        factory = PendingFactory()
        real_acquire = arsd_main.acquire_daemon_instance_lease

        def tracking_acquire(supervisor_root_path):
            lease = real_acquire(supervisor_root_path)
            real_release = lease.release

            def tracking_release():
                release_order.append("release")
                return real_release()

            lease.release = tracking_release  # type: ignore[method-assign]
            return lease

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)

        serve_task = asyncio.create_task(
            arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=factory,
                cancel_wait_seconds=0.05,
                shutdown_timeout=0.2,
                stop_event=stop,
                install_signals=False,
            )
        )
        await wait_for_socket(path)
        assert factory.handlers is not None
        registry = factory.handlers.registry
        real_idle = registry.wait_until_idle

        async def permanent_fail_idle():
            nonlocal idle_calls
            idle_calls += 1
            if not allow_success.is_set():
                raise RuntimeError("injected-r12b-permanent-lifecycle-failure")
            return await real_idle()

        registry.wait_until_idle = permanent_fail_idle  # type: ignore[method-assign]

        stop.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and idle_calls < 3:
            await asyncio.sleep(0.01)
        assert idle_calls >= 3
        assert not serve_task.done()
        assert release_order == []
        assert registry.admission_open is False
        with pytest.raises(arsd_main.DaemonStartupError) as held:
            arsd_main.acquire_daemon_instance_lease(root)
        held_msg = str(held.value).lower()
        assert "already" in held_msg or "lease" in held_msg or "lock" in held_msg

        allow_success.set()
        rc = await asyncio.wait_for(serve_task, 2.0)
        assert rc == 0
        assert release_order == ["release"]

        lease = arsd_main.acquire_daemon_instance_lease(root)
        lease.release()

    run_async(case())


@pytest.mark.parametrize("mode", ("getter", "setter"))
def test_r13_b1_handlers_descriptor_raise_cleans_lifecycle_once(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Raising handlers getter/setter must not leak singleton lease ownership."""

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        release_order: list[str] = []
        lifecycle_calls = 0
        real_acquire = arsd_main.acquire_daemon_instance_lease
        real_shutdown = arsd_main._shutdown_and_release_lease

        def tracking_acquire(supervisor_root_path):
            lease = real_acquire(supervisor_root_path)
            real_release = lease.release

            def tracking_release():
                release_order.append("release")
                return real_release()

            lease.release = tracking_release  # type: ignore[method-assign]
            return lease

        async def tracking_shutdown(*args, **kwargs):
            nonlocal lifecycle_calls
            lifecycle_calls += 1
            return await real_shutdown(*args, **kwargs)

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)
        monkeypatch.setattr(
            arsd_main, "_shutdown_and_release_lease", tracking_shutdown
        )

        if mode == "getter":

            class _HostileFactory:
                @property
                def handlers(self):
                    raise RuntimeError(
                        f"injected-r13-getter-raise {SECRET_SENTINEL}"
                    )

                def __call__(self, *, command, run_id, prepared_handle, submitted_at):
                    raise AssertionError("factory must not construct a Run")

        else:

            class _HostileFactory:
                def __init__(self) -> None:
                    self._handlers = None

                @property
                def handlers(self):
                    return self._handlers

                @handlers.setter
                def handlers(self, value) -> None:
                    raise RuntimeError(
                        f"injected-r13-setter-raise {SECRET_SENTINEL}"
                    )

                def __call__(self, *, command, run_id, prepared_handle, submitted_at):
                    raise AssertionError("factory must not construct a Run")

        factory = _HostileFactory()
        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=factory,
                cancel_wait_seconds=0.05,
                shutdown_timeout=0.2,
                stop_event=asyncio.Event(),
                install_signals=False,
            )
        message = str(err.value)
        assert "failed to attach run task factory" in message
        assert SECRET_SENTINEL not in message
        assert "injected-r13" not in message
        assert err.value.__cause__ is None
        assert lifecycle_calls == 1
        assert release_order == ["release"]
        assert not path.exists()
        # No orphan arsd lifecycle/serve tasks after owned cleanup.
        pending = [
            t
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task()
            and not t.done()
            and str(t.get_name()).startswith("arsd:")
        ]
        assert pending == []
        # Lease must be reacquirable only after the lifecycle release.
        lease = arsd_main.acquire_daemon_instance_lease(root)
        lease.release()

    run_async(case())


def test_r13_b1_startup_before_handlers_still_releases_lease(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failures before ArsdHandlers exist keep the normal outer lease release."""

    async def case():
        path = sock_path(sock_root)
        root = supervisor_root(sock_root)
        release_order: list[str] = []
        lifecycle_calls = 0
        real_acquire = arsd_main.acquire_daemon_instance_lease
        real_shutdown = arsd_main._shutdown_and_release_lease

        def tracking_acquire(supervisor_root_path):
            lease = real_acquire(supervisor_root_path)
            real_release = lease.release

            def tracking_release():
                release_order.append("release")
                return real_release()

            lease.release = tracking_release  # type: ignore[method-assign]
            return lease

        async def tracking_shutdown(*args, **kwargs):
            nonlocal lifecycle_calls
            lifecycle_calls += 1
            return await real_shutdown(*args, **kwargs)

        def failing_reconcile(_root):
            raise arsd_main.reconcile.ReconciliationError(
                "untrusted or corrupt terminal evidence; refusing reconciliation"
            )

        monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", tracking_acquire)
        monkeypatch.setattr(
            arsd_main, "_shutdown_and_release_lease", tracking_shutdown
        )
        monkeypatch.setattr(arsd_main.reconcile, "reconcile", failing_reconcile)

        with pytest.raises(arsd_main.DaemonStartupError) as err:
            await arsd_main.serve_daemon(
                socket_path=path,
                supervisor_root=root,
                policy=same_uid_policy(),
                agents_file=str(make_agents_file(sock_root)),
                run_task_factory=CompletingFactory(),
                install_signals=False,
            )
        assert SECRET_SENTINEL not in str(err.value)
        assert lifecycle_calls == 0
        assert release_order == ["release"]
        assert not path.exists()
        lease = arsd_main.acquire_daemon_instance_lease(root)
        lease.release()

    run_async(case())


def test_follow_list_terminates_after_terminal_stream_exhaustion(
    sock_root: Path,
) -> None:
    """Real UDS/client: list(subscription) ends after terminal/stream EOF.

    No sleep-as-correctness: CompletingFactory writes result.json so the
    follow watcher reaches natural StopAsyncIteration; the server must close
    the connection so the client iterator terminates and releases resources.
    """
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    factory = CompletingFactory(event_count=3, mode="complete")
    with running_daemon(path, root, factory=factory):
        sessions = storage.native_session_store(root)
        seed_session(sessions, session_id="sess-follow-term")

        cli = arsd_client.ArsdClient(path)
        cli.connect()
        accepted = cli.submit(
            request_id="follow-term-1",
            payload=submit_payload(
                request=valid_wire_request(session_id="sess-follow-term")
            ),
        )
        run_id = accepted["run_id"]
        # Wait until the injected run has written its terminal fact (no sleep
        # bound as correctness — poll on the durable artifact).
        run_dir = Path(storage.native_event_store(root).base_dir) / run_id
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (run_dir / "result.json").exists():
            time.sleep(0.02)
        assert (run_dir / "result.json").exists()

        frames = list(
            cli.run_events(
                run_id, from_seq=0, follow=True, follow_idle_seconds=0.05
            )
        )
        assert frames
        assert all(frame.get("follow") is True for frame in frames)
        assert cli.closed is True
        # Peer disconnect / natural EOF cancels only the subscription: Run
        # already terminal and not cancelled by follow teardown.
        with arsd_client.ArsdClient(path) as fresh:
            status = fresh.run_status(run_id)
            assert status["run_id"] == run_id
            assert "result" in status
            assert status["result"]["status"] == "completed"


def test_cli_main_zero_mappings_exits_nonzero(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    path = sock_path(sock_root)
    root = supervisor_root(sock_root)
    monkeypatch.setattr(
        "sys.argv",
        [
            "arsd",
            "--supervisor-root",
            str(root),
            "--socket",
            str(path),
        ],
    )
    rc = arsd_main.main()
    assert rc != 0
    assert not path.exists()
    err = capsys.readouterr().err
    assert "mapping" in err.lower() or "caller" in err.lower() or "zero" in err.lower()
    assert SECRET_SENTINEL not in err

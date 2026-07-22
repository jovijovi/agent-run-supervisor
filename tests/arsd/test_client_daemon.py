"""Slice 5 — typed local client + unprivileged daemon entrypoint.

Real temp-dir AF_UNIX sockets; deterministic injected run-task factories only.
No real AGENT, no production UID/owner mapping, no service install/enable.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import protocol, server
from agent_run_supervisor.arsd import client as arsd_client
from agent_run_supervisor.arsd import __main__ as arsd_main
from agent_run_supervisor.native_acp import storage

from tests.arsd.test_admission import (
    SpyFactory,
    submit_payload,
    valid_wire_request,
)
from tests.arsd.test_handlers_registry import CancelFactory, seed_events, seed_session


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


# --- client typed errors --------------------------------------------------


@pytest.mark.parametrize("code", sorted(protocol.ERROR_CODES_V1))
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
                request=valid_wire_request(ars_session_id="sess-follow-break")
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
                request=valid_wire_request(ars_session_id="sess-follow-for")
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
            assert info["api_version"] == 1
            assert "limits" in info

            accepted = cli.submit(
                request_id="s5-submit-1",
                payload=submit_payload(
                    request=valid_wire_request(ars_session_id="sess-s5-1")
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
            closed = cli.session_close("sess-s5-1")
            assert closed["session_id"] == "sess-s5-1"


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
                    request=valid_wire_request(ars_session_id="sess-follow")
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
            assert info["api_version"] == 1


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
                            ars_session_id=None, session_reuse="none"
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

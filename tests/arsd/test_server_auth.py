"""Slice 2 — UDS server host with SO_PEERCRED auth and caller policy (plan §6/§7).

Real temp-dir AF_UNIX sockets only; request-operation handling is an
injected seam. Caller mappings are explicit synthetic test-scoped values —
no production UID or owner mapping appears anywhere. Socket dirs live
directly under /tmp so paths stay inside the AF_UNIX length limit.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import errno
import os
import shutil
import socket
import stat
import tempfile
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import protocol, server

SECRET_SENTINEL = "sk-live-" + "LEAKCANARY"  # concatenated; no scannable literal


def run_async(coro):
    return asyncio.run(asyncio.wait_for(coro, 15))


@pytest.fixture
def sock_root():
    root = Path(tempfile.mkdtemp(prefix="arsd-s2-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def sock_path(root: Path) -> Path:
    return root / "s" / "arsd.sock"


def local_principal() -> server.Principal:
    return server.Principal(
        principal_id="hermes-local",
        owner_namespaces=frozenset({("hermes", "hermes/doc-check")}),
    )


def same_uid_policy() -> server.CallerPolicy:
    return server.CallerPolicy({os.getuid(): local_principal()})


def other_uid_policy() -> server.CallerPolicy:
    return server.CallerPolicy({os.getuid() + 1: local_principal()})


def make_handler(seen: list):
    async def handler(caller, request):
        seen.append((caller, request))
        if request.payload.get("poison"):
            raise RuntimeError("handler poisoned")
        return {"op": request.op}

    return handler


@contextlib.asynccontextmanager
async def serving(path: Path, *, policy=None, seen=None, **kwargs):
    srv = server.ArsdServer(
        socket_path=path,
        policy=same_uid_policy() if policy is None else policy,
        handler=make_handler([] if seen is None else seen),
        **kwargs,
    )
    await srv.start()
    try:
        yield srv
    finally:
        await srv.aclose()


def info_frame(request_id: str = "req-1") -> dict:
    return {"api_version": 1, "op": "server_info", "request_id": request_id}


async def connect(path: Path):
    return await asyncio.open_unix_connection(str(path))


async def roundtrip(reader, writer, frame: dict) -> dict:
    writer.write(protocol.encode_frame(frame))
    await writer.drain()
    line = await reader.readline()
    assert line, "server closed the connection before replying"
    return protocol.decode_frame(line)


async def close(writer) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def read_denial(path: Path) -> dict:
    reader, writer = await connect(path)
    line = await reader.readline()
    assert line, "expected a denial frame before close"
    frame = protocol.decode_frame(line)
    assert await reader.readline() == b""  # closed right after the denial
    await close(writer)
    return frame


# --- socket placement and startup fail-closed -----------------------------


def test_socket_dir_0700_and_socket_0600(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
            assert stat.S_ISSOCK(os.stat(path).st_mode)
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            reader, writer = await connect(path)
            reply = await roundtrip(reader, writer, info_frame())
            assert reply == {"request_id": "req-1", "result": {"op": "server_info"}}
            await close(writer)

    run_async(scenario())


def test_socket_path_length_fail_closed(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_root / ("x" * 120) / "arsd.sock"
        srv = server.ArsdServer(
            socket_path=path,
            policy=same_uid_policy(),
            handler=make_handler([]),
        )
        with pytest.raises(server.ServerStartupError) as err:
            await srv.start()
        assert "AF_UNIX" in str(err.value)
        assert not path.parent.exists()

    run_async(scenario())


def test_peercred_capability_fail_closed(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        monkeypatch.delattr("socket.SO_PEERCRED")
        srv = server.ArsdServer(
            socket_path=path,
            policy=same_uid_policy(),
            handler=make_handler([]),
        )
        with pytest.raises(server.ServerStartupError) as err:
            await srv.start()
        assert "SO_PEERCRED" in str(err.value)
        assert not path.exists()

    run_async(scenario())


def test_zero_mappings_refuse_to_listen(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        srv = server.ArsdServer(
            socket_path=path,
            policy=server.CallerPolicy({}),
            handler=make_handler([]),
        )
        with pytest.raises(server.ServerStartupError) as err:
            await srv.start()
        assert "zero configured caller mappings" in str(err.value)
        assert not path.exists()
        assert not path.parent.exists()

    run_async(scenario())


# --- peer authentication and caller policy --------------------------------


def test_handler_receives_immutable_authenticated_caller_context(
    sock_root: Path,
) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        seen: list = []
        source = {os.getuid(): local_principal()}
        async with serving(path, policy=server.CallerPolicy(source), seen=seen):
            reader, writer = await connect(path)
            assert "result" in await roundtrip(reader, writer, info_frame("ctx-1"))
            await close(writer)
            # Mutating the source configuration afterwards changes nothing
            # for new connections or already-captured contexts.
            source[os.getuid()] = server.Principal(
                principal_id="intruder", owner_namespaces=frozenset({("x", "y")})
            )
            reader, writer = await connect(path)
            assert "result" in await roundtrip(reader, writer, info_frame("ctx-2"))
            await close(writer)

        assert len(seen) == 2
        for caller, parsed in seen:
            assert isinstance(caller, server.AuthenticatedCaller)
            # Authorization source: exactly the policy-resolved principal.
            assert isinstance(caller.principal, server.Principal)
            assert caller.principal.principal_id == "hermes-local"
            assert caller.principal.owner_namespaces == frozenset(
                {("hermes", "hermes/doc-check")}
            )
            # Evidence: the kernel-returned accept-time peer credentials of
            # this very client process — never a caller-supplied claim (the
            # frames carried no identity fields at all).
            creds = caller.peer_credentials
            assert isinstance(creds, server.PeerCredentials)
            assert creds.uid == os.getuid()
            assert creds.gid == os.getgid()
            assert creds.pid == os.getpid()
            assert parsed.op == "server_info"

        caller = seen[0][0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            caller.principal = caller.principal  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            caller.peer_credentials = caller.peer_credentials  # type: ignore[misc]

    run_async(scenario())


def test_unmapped_uid_denied_no_implicit_same_uid(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        seen: list = []
        # Only a synthetic foreign UID is mapped: the connecting same-UID
        # caller must be denied — there is no implicit same-UID mapping.
        async with serving(path, policy=other_uid_policy(), seen=seen):
            frame = await read_denial(path)
        assert frame["request_id"] is None
        assert frame["error"]["code"] == "PEER_UID_DENIED"
        assert str(os.getuid()) not in frame["error"]["message"]
        assert seen == []

    run_async(scenario())


def test_unreadable_peercred_unauthenticated(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)

        def broken_reader(sock: socket.socket) -> server.PeerCredentials:
            raise OSError("peercred unavailable")

        seen: list = []
        async with serving(path, seen=seen):
            monkeypatch.setattr(server, "read_peer_credentials", broken_reader)
            frame = await read_denial(path)
        assert frame["error"]["code"] == "UNAUTHENTICATED_PEER"
        assert seen == []

    run_async(scenario())


def test_policy_frozen_at_daemon_start(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        source = {os.getuid() + 1: local_principal()}
        policy = server.CallerPolicy(source)
        async with serving(path, policy=policy):
            # Mutating the source mapping after construction changes nothing.
            source[os.getuid()] = local_principal()
            frame = await read_denial(path)
        assert frame["error"]["code"] == "PEER_UID_DENIED"

    run_async(scenario())


# --- bounds ---------------------------------------------------------------


def test_finite_bound_defaults() -> None:
    assert server.DEFAULT_BACKLOG == 16
    assert server.DEFAULT_MAX_CONNECTIONS == 32


def test_connection_cap_fail_closed_then_recovers(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path, max_connections=2):
            r1, w1 = await connect(path)
            assert "result" in await roundtrip(r1, w1, info_frame("cap-1"))
            r2, w2 = await connect(path)
            assert "result" in await roundtrip(r2, w2, info_frame("cap-2"))

            over = await read_denial(path)
            assert over["error"]["code"] == "CAPACITY_EXHAUSTED"

            await close(w1)
            for _ in range(80):
                reader, writer = await connect(path)
                writer.write(protocol.encode_frame(info_frame("cap-4")))
                await writer.drain()
                line = await reader.readline()
                await close(writer)
                if line and "result" in protocol.decode_frame(line):
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("server never freed a connection slot")
            await close(w2)

    run_async(scenario())


# --- isolation ------------------------------------------------------------


def test_poisoned_handler_never_kills_accept_loop(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            reader, writer = await connect(path)
            frame = info_frame("poison-1")
            frame["payload"] = {"poison": True}
            reply = await roundtrip(reader, writer, frame)
            assert reply["error"]["code"] == "INTERNAL"
            assert reply["request_id"] == "poison-1"
            assert await reader.readline() == b""  # poisoned connection closed
            await close(writer)

            reader, writer = await connect(path)
            reply = await roundtrip(reader, writer, info_frame("healthy"))
            assert reply == {"request_id": "healthy", "result": {"op": "server_info"}}
            await close(writer)

    run_async(scenario())


def test_request_error_isolated_connection_survives(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            reader, writer = await connect(path)
            bad = {"api_version": 1, "op": "bogus", "request_id": "r1"}
            reply = await roundtrip(reader, writer, bad)
            assert reply["error"]["code"] == "UNKNOWN_OP"
            assert reply["request_id"] == "r1"
            reply = await roundtrip(reader, writer, info_frame("r2"))
            assert reply == {"request_id": "r2", "result": {"op": "server_info"}}
            await close(writer)

    run_async(scenario())


def test_parse_errors_echo_valid_request_id_on_wire(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            reader, writer = await connect(path)

            unknown = await roundtrip(
                reader,
                writer,
                {"api_version": 1, "op": "nope", "request_id": "corr-op"},
            )
            assert unknown["request_id"] == "corr-op"
            assert unknown["error"]["code"] == "UNKNOWN_OP"
            assert SECRET_SENTINEL not in unknown["error"]["message"]

            unsupported = await roundtrip(
                reader,
                writer,
                {
                    "api_version": 99,
                    "op": "server_info",
                    "request_id": "corr-ver",
                    "payload": {},
                },
            )
            assert unsupported["request_id"] == "corr-ver"
            assert unsupported["error"]["code"] == "UNSUPPORTED_API_VERSION"

            invalid_payload = await roundtrip(
                reader,
                writer,
                {
                    "api_version": 1,
                    "op": "server_info",
                    "request_id": "corr-pay",
                    "payload": ["x"],
                },
            )
            assert invalid_payload["request_id"] == "corr-pay"
            assert invalid_payload["error"]["code"] == "INVALID_REQUEST"

            bad_id = await roundtrip(
                reader,
                writer,
                {"api_version": 1, "op": "bogus", "request_id": "bad id"},
            )
            assert bad_id["request_id"] is None
            assert bad_id["error"]["code"] == "INVALID_REQUEST"

            await close(writer)

    run_async(scenario())


def test_malformed_frame_replies_then_closes(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            reader, writer = await connect(path)
            writer.write(b"not json at all " + SECRET_SENTINEL.encode() + b"\n")
            await writer.drain()
            raw = await reader.readline()
            frame = protocol.decode_frame(raw)
            assert frame["error"]["code"] == "MALFORMED_FRAME"
            assert SECRET_SENTINEL.encode() not in raw
            assert await reader.readline() == b""
            await close(writer)

    run_async(scenario())


def test_oversize_frame_replies_then_closes(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            reader, writer = await connect(path)
            writer.write(b'{"k":"' + b"a" * protocol.MAX_FRAME_BYTES + b'"}\n')
            drain = asyncio.create_task(writer.drain())
            raw = await reader.readline()
            frame = protocol.decode_frame(raw)
            assert frame["error"]["code"] == "FRAME_TOO_LARGE"
            assert await reader.readline() == b""
            drain.cancel()
            with contextlib.suppress(Exception):
                await drain
            await close(writer)

    run_async(scenario())


def test_error_frames_never_echo_caller_field_names(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            reader, writer = await connect(path)
            frame = info_frame("leak-1")
            frame[SECRET_SENTINEL] = 1
            writer.write(protocol.encode_frame(frame))
            await writer.drain()
            raw = await reader.readline()
            reply = protocol.decode_frame(raw)
            assert reply["error"]["code"] == "INVALID_REQUEST"
            assert SECRET_SENTINEL.encode() not in raw
            await close(writer)

    run_async(scenario())


# --- shutdown and stale-socket handling -----------------------------------


def test_graceful_shutdown(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path) as srv:
            reader, writer = await connect(path)
            assert "result" in await roundtrip(reader, writer, info_frame("pre"))

            await srv.shutdown()
            assert not path.exists()  # socket unlinked

            writer.write(protocol.encode_frame(info_frame("late")))
            await writer.drain()
            line = await reader.readline()
            frame = protocol.decode_frame(line)
            assert frame["error"]["code"] == "SHUTTING_DOWN"
            assert await reader.readline() == b""
            await close(writer)

            with pytest.raises((ConnectionRefusedError, FileNotFoundError, OSError)):
                await connect(path)

    run_async(scenario())


def test_stale_socket_replaced_after_probe(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        path.parent.mkdir(mode=0o700)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()  # leaves a dead socket file: connect now refuses
        assert path.exists()

        async with serving(path):
            reader, writer = await connect(path)
            assert "result" in await roundtrip(reader, writer, info_frame("stale"))
            await close(writer)

    run_async(scenario())


def test_live_socket_never_replaced(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            second = server.ArsdServer(
                socket_path=path,
                policy=same_uid_policy(),
                handler=make_handler([]),
            )
            with pytest.raises(server.ServerStartupError) as err:
                await second.start()
            assert "live" in str(err.value)

            # The original daemon still owns the socket and still serves.
            reader, writer = await connect(path)
            assert "result" in await roundtrip(reader, writer, info_frame("alive"))
            await close(writer)

    run_async(scenario())


# --- probe outcome classification (stale vs uncertain vs live) ------------


def seed_dead_socket(path: Path) -> int:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(str(path))
    dead.close()  # file remains; nothing listens
    return os.stat(path).st_ino


def make_server(path: Path) -> server.ArsdServer:
    return server.ArsdServer(
        socket_path=path, policy=same_uid_policy(), handler=make_handler([])
    )


def test_probe_eacces_preserves_socket_and_fails_closed(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        inode = seed_dead_socket(path)
        os.chmod(path, 0o000)  # a real connect now fails EACCES, not refused
        with pytest.raises(server.ServerStartupError) as err:
            await make_server(path).start()
        message = str(err.value)
        assert "probe" in message
        assert str(path) not in message
        assert err.value.__cause__ is None
        status = os.stat(path)
        assert stat.S_ISSOCK(status.st_mode)
        assert status.st_ino == inode  # the existing socket was not replaced

    run_async(scenario())


@pytest.mark.parametrize(
    "code", [errno.EACCES, errno.EINTR, errno.EMFILE, errno.ENOBUFS]
)
def test_probe_uncertain_errors_fail_closed(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        inode = seed_dead_socket(path)

        def uncertain(_path: str) -> None:
            raise OSError(code, "synthetic probe failure detail")

        monkeypatch.setattr(server, "probe_connect", uncertain)
        with pytest.raises(server.ServerStartupError) as err:
            await make_server(path).start()
        message = str(err.value)
        assert str(path) not in message
        assert "synthetic probe failure detail" not in message
        assert err.value.__cause__ is None
        status = os.stat(path)
        assert stat.S_ISSOCK(status.st_mode)
        assert status.st_ino == inode

    run_async(scenario())


def test_probe_refused_is_provably_stale_and_replaced(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        seed_dead_socket(path)

        def refused(_path: str) -> None:
            raise OSError(errno.ECONNREFUSED, "refused")

        monkeypatch.setattr(server, "probe_connect", refused)
        async with serving(path):
            reader, writer = await connect(path)
            assert "result" in await roundtrip(reader, writer, info_frame("stale-2"))
            await close(writer)

    run_async(scenario())


@pytest.mark.parametrize("outcome", ["accepted", "timeout"])
def test_probe_accept_or_timeout_refuses_as_live(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        inode = seed_dead_socket(path)

        def live(_path: str) -> None:
            if outcome == "timeout":
                raise TimeoutError("timed out")

        monkeypatch.setattr(server, "probe_connect", live)
        with pytest.raises(server.ServerStartupError) as err:
            await make_server(path).start()
        assert "live" in str(err.value)
        status = os.stat(path)
        assert stat.S_ISSOCK(status.st_mode)
        assert status.st_ino == inode

    run_async(scenario())


def test_probe_path_vanished_race_is_safe(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        seed_dead_socket(path)

        def vanished(path_str: str) -> None:
            os.unlink(path_str)  # the file disappears mid-probe
            raise OSError(errno.ENOENT, "gone")

        monkeypatch.setattr(server, "probe_connect", vanished)
        async with serving(path):
            reader, writer = await connect(path)
            assert "result" in await roundtrip(reader, writer, info_frame("race"))
            await close(writer)

    run_async(scenario())


# --- streaming Handler seam (blocker 3 repair) --------------------------------


class _StreamResult:
    """Minimal async stream with explicit aclose for server seam tests."""

    def __init__(self, items, *, hang_after: int | None = None, fail_code=None):
        self._items = list(items)
        self._hang_after = hang_after
        self._fail_code = fail_code
        self._index = 0
        self.closed = False
        self._hang = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._fail_code is not None and self._index >= len(self._items):
            raise protocol.ProtocolError(self._fail_code, "stream failed safely")
        if self._index >= len(self._items):
            raise StopAsyncIteration
        if self._hang_after is not None and self._index == self._hang_after:
            await self._hang.wait()
        item = self._items[self._index]
        self._index += 1
        return item

    async def aclose(self) -> None:
        self.closed = True
        self._hang.set()


def test_streaming_handler_pushes_two_correlated_result_frames(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        streams: list[_StreamResult] = []

        async def handler(caller, request):
            if request.op != "server_info":
                return {"op": request.op}
            stream = _StreamResult([{"part": 1}, {"part": 2}])
            streams.append(stream)
            return stream

        srv = server.ArsdServer(
            socket_path=path, policy=same_uid_policy(), handler=handler
        )
        await srv.start()
        try:
            reader, writer = await connect(path)
            writer.write(protocol.encode_frame(info_frame("stream-1")))
            await writer.drain()
            first = protocol.decode_frame(await reader.readline())
            second = protocol.decode_frame(await reader.readline())
            assert first["request_id"] == "stream-1"
            assert second["request_id"] == "stream-1"
            assert first["result"] == {"part": 1}
            assert second["result"] == {"part": 2}
            # Natural exhaustion closes the connection after aclose.
            assert await reader.readline() == b""
            assert streams[0].closed is True
            await close(writer)
        finally:
            await srv.aclose()

    run_async(scenario())


def test_streaming_natural_exhaustion_closes_connection(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        streams: list[_StreamResult] = []

        async def handler(caller, request):
            stream = _StreamResult([{"done": True}])
            streams.append(stream)
            return stream

        srv = server.ArsdServer(
            socket_path=path, policy=same_uid_policy(), handler=handler
        )
        await srv.start()
        try:
            reader, writer = await connect(path)
            writer.write(protocol.encode_frame(info_frame("eof-stream")))
            await writer.drain()
            frame = protocol.decode_frame(await reader.readline())
            assert frame["result"] == {"done": True}
            assert await reader.readline() == b""
            assert streams[0].closed is True
            await close(writer)
        finally:
            await srv.aclose()

    run_async(scenario())


def test_streaming_disconnect_closes_stream_not_handler_state(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        streams: list[_StreamResult] = []
        handler_alive = {"ok": True}

        async def handler(caller, request):
            if request.request_id == "disc-stream":
                stream = _StreamResult([{"part": 1}, {"part": 2}], hang_after=1)
                streams.append(stream)
                return stream
            return {"op": request.op, "still_serving": True}

        srv = server.ArsdServer(
            socket_path=path, policy=same_uid_policy(), handler=handler
        )
        await srv.start()
        try:
            reader, writer = await connect(path)
            writer.write(protocol.encode_frame(info_frame("disc-stream")))
            await writer.drain()
            first = protocol.decode_frame(await reader.readline())
            assert first["result"] == {"part": 1}
            await close(writer)
            # Disconnect finalizes only the stream.
            for _ in range(50):
                if streams and streams[0].closed:
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("stream aclose was not invoked on disconnect")
            assert handler_alive["ok"] is True
            # Server still serves a later single-response request.
            reader2, writer2 = await connect(path)
            reply = await roundtrip(reader2, writer2, info_frame("after-disc"))
            assert reply["request_id"] == "after-disc"
            assert reply["result"]["still_serving"] is True
            await close(writer2)
        finally:
            await srv.aclose()
            assert streams[0].closed is True

    run_async(scenario())


def test_streaming_protocol_error_emits_safe_error_frame(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)

        async def handler(caller, request):
            return _StreamResult(
                [{"part": 1}],
                fail_code=protocol.EVENT_BACKLOG_EXCEEDED,
            )

        srv = server.ArsdServer(
            socket_path=path, policy=same_uid_policy(), handler=handler
        )
        await srv.start()
        try:
            reader, writer = await connect(path)
            writer.write(protocol.encode_frame(info_frame("err-stream")))
            await writer.drain()
            first = protocol.decode_frame(await reader.readline())
            assert first["result"] == {"part": 1}
            # Force a third __anext__ by making fail trigger after items — adjust
            # stream to fail on second pull after yielding one.
            err = protocol.decode_frame(await reader.readline())
            assert err["request_id"] == "err-stream"
            assert err["error"]["code"] == protocol.EVENT_BACKLOG_EXCEEDED
            assert SECRET_SENTINEL not in err["error"]["message"]
            # Protocol error: one correlated typed error, then close.
            assert await reader.readline() == b""
            await close(writer)
        finally:
            await srv.aclose()

    run_async(scenario())


def test_single_mapping_response_unchanged(sock_root: Path) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        async with serving(path):
            reader, writer = await connect(path)
            reply = await roundtrip(reader, writer, info_frame("single-1"))
            assert reply == {
                "request_id": "single-1",
                "result": {"op": "server_info"},
            }
            await close(writer)

    run_async(scenario())


def test_two_connection_streams_independent_on_peer_disconnect(
    sock_root: Path,
) -> None:
    async def scenario() -> None:
        path = sock_path(sock_root)
        streams: dict[str, _StreamResult] = {}

        async def handler(caller, request):
            stream = _StreamResult(
                [{"part": 1}, {"part": 2}], hang_after=1
            )
            streams[request.request_id] = stream
            return stream

        srv = server.ArsdServer(
            socket_path=path, policy=same_uid_policy(), handler=handler
        )
        await srv.start()
        try:
            r1, w1 = await connect(path)
            r2, w2 = await connect(path)
            w1.write(protocol.encode_frame(info_frame("conn-a")))
            w2.write(protocol.encode_frame(info_frame("conn-b")))
            await w1.drain()
            await w2.drain()
            assert protocol.decode_frame(await r1.readline())["result"] == {"part": 1}
            assert protocol.decode_frame(await r2.readline())["result"] == {"part": 1}
            await close(w1)
            for _ in range(50):
                if streams["conn-a"].closed:
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("conn-a stream not closed")
            assert streams["conn-b"].closed is False
            # Unblock conn-b and expect its second frame.
            streams["conn-b"]._hang.set()
            second = protocol.decode_frame(await r2.readline())
            assert second["request_id"] == "conn-b"
            assert second["result"] == {"part": 2}
            await close(w2)
        finally:
            await srv.aclose()

    run_async(scenario())

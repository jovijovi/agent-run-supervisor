"""asyncio UDS server host: SO_PEERCRED authentication and caller policy.

Server host only — framing/envelope come from ``arsd.protocol`` and request
operation handling is an injected seam. Linux AF_UNIX + SO_PEERCRED is the
only supported transport; anything else fails closed at startup. Log lines
and error frames never carry caller payload text or secrets.
"""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import logging
import os
import socket
import stat
import struct
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from ..event_store import secure_mkdir
from . import protocol

_LOGGER = logging.getLogger(__name__)

DEFAULT_BACKLOG = 16
DEFAULT_MAX_CONNECTIONS = 32

# Linux sun_path holds 108 bytes including the trailing NUL.
MAX_SOCKET_PATH_BYTES = 107

SOCKET_FILE_MODE = 0o600

_PEERCRED_STRUCT = struct.Struct("3i")
_PROBE_TIMEOUT_SECONDS = 0.2


class ServerStartupError(RuntimeError):
    """The server refused to start; fail-closed, nothing is listening."""


@dataclasses.dataclass(frozen=True)
class PeerCredentials:
    pid: int
    uid: int
    gid: int


@dataclasses.dataclass(frozen=True)
class Principal:
    """Trusted caller identity resolved from explicit configuration only.

    ``owner_namespaces`` is the exact, closed set of ``(owner, namespace)``
    pairs this principal may act as; ownership never derives from a caller
    claim or from the raw UID.
    """

    principal_id: str
    owner_namespaces: frozenset[tuple[str, str]]

    def __post_init__(self) -> None:
        if not isinstance(self.principal_id, str) or not self.principal_id:
            raise ValueError("principal_id must be a non-empty string")
        pairs = frozenset(self.owner_namespaces)
        for pair in pairs:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not all(isinstance(part, str) and part for part in pair)
            ):
                raise ValueError(
                    "owner_namespaces must contain (owner, namespace) string pairs"
                )
        object.__setattr__(self, "owner_namespaces", pairs)


class CallerPolicy:
    """Immutable peer-UID → principal mapping frozen at construction.

    No implicit mapping exists — an unmapped UID (same-UID included)
    resolves to nothing and the peer is denied.
    """

    def __init__(self, mappings: Mapping[int, Principal]) -> None:
        frozen: dict[int, Principal] = {}
        for uid, principal in mappings.items():
            if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
                raise ValueError("caller policy UIDs must be non-negative integers")
            if not isinstance(principal, Principal):
                raise ValueError("caller policy values must be Principal instances")
            frozen[uid] = principal
        self._by_uid = frozen

    def __len__(self) -> int:
        return len(self._by_uid)

    def resolve(self, uid: int) -> Principal | None:
        return self._by_uid.get(uid)


@dataclasses.dataclass(frozen=True)
class AuthenticatedCaller:
    """Immutable per-connection authentication outcome.

    ``principal`` is the sole authorization source. ``peer_credentials`` is
    the kernel-attested accept-time evidence (pid/uid/gid) the admission
    layer must record durably — it never grants authority.
    """

    principal: Principal
    peer_credentials: PeerCredentials


Handler = Callable[
    [AuthenticatedCaller, protocol.ParsedRequest], Awaitable[Mapping[str, Any]]
]


def probe_connect(path: str) -> None:
    """One bounded connect attempt against ``path``.

    Returns when a live daemon accepted; raises the underlying ``OSError``
    otherwise so the caller can classify the outcome.
    """
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(_PROBE_TIMEOUT_SECONDS)
    try:
        probe.connect(path)
    finally:
        probe.close()


def read_peer_credentials(sock: socket.socket) -> PeerCredentials:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise OSError(errno.ENOPROTOOPT, "SO_PEERCRED is unavailable")
    data = sock.getsockopt(socket.SOL_SOCKET, option, _PEERCRED_STRUCT.size)
    pid, uid, gid = _PEERCRED_STRUCT.unpack(data)
    return PeerCredentials(pid=pid, uid=uid, gid=gid)


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except (ConnectionError, OSError, RuntimeError):
        pass


class ArsdServer:
    def __init__(
        self,
        *,
        socket_path: Path | str,
        policy: CallerPolicy,
        handler: Handler,
        backlog: int = DEFAULT_BACKLOG,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._policy = policy
        self._handler = handler
        self._backlog = backlog
        self._max_connections = max_connections
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._shutting_down = False
        self._listening = False

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    async def start(self) -> None:
        if getattr(socket, "SO_PEERCRED", None) is None:
            raise ServerStartupError(
                "SO_PEERCRED peer authentication is unavailable on this "
                "platform; refusing to serve"
            )
        if len(self._policy) == 0:
            raise ServerStartupError(
                "caller policy has zero configured caller mappings; refusing to listen"
            )
        if len(os.fsencode(str(self._socket_path))) > MAX_SOCKET_PATH_BYTES:
            raise ServerStartupError(
                f"socket path exceeds the AF_UNIX limit of "
                f"{MAX_SOCKET_PATH_BYTES} bytes"
            )
        secure_mkdir(self._socket_path.parent)
        self._replace_stale_socket()
        listen_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listen_sock.bind(str(self._socket_path))
            os.chmod(self._socket_path, SOCKET_FILE_MODE)
            # start_unix_server applies the backlog via listen() on this
            # already-bound socket, so the 0600 chmod precedes listen.
            self._server = await asyncio.start_unix_server(
                self._serve_connection,
                sock=listen_sock,
                backlog=self._backlog,
                limit=protocol.MAX_FRAME_BYTES,
            )
        except BaseException:
            listen_sock.close()
            raise
        self._listening = True

    async def shutdown(self) -> None:
        self._shutting_down = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._listening:
            self._listening = False
            try:
                self._socket_path.unlink()
            except FileNotFoundError:
                pass

    async def aclose(self) -> None:
        await self.shutdown()
        for writer in list(self._writers):
            writer.close()
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _replace_stale_socket(self) -> None:
        path = self._socket_path
        try:
            mode = os.stat(path).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise ServerStartupError(
                "socket path is occupied by a non-socket file; refusing to replace it"
            )
        try:
            probe_connect(str(path))
        except ConnectionRefusedError:
            # The kernel proved no listener owns this socket file: stale.
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            return
        except FileNotFoundError:
            # Vanished between stat and probe: nothing left to replace.
            return
        except TimeoutError:
            raise ServerStartupError(
                "socket path is owned by a live daemon; refusing to replace it"
            ) from None
        except OSError:
            # EACCES/EINTR/EMFILE/… prove nothing about liveness: fail
            # closed, keep the path, expose no OS or path detail.
            raise ServerStartupError(
                "socket liveness probe failed; refusing to replace the "
                "existing socket path"
            ) from None
        raise ServerStartupError(
            "socket path is owned by a live daemon; refusing to replace it"
        )

    async def _serve_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            if self._shutting_down:
                await self._send_error(
                    writer, None, protocol.SHUTTING_DOWN, "server is shutting down"
                )
                return
            if len(self._writers) >= self._max_connections:
                await self._send_error(
                    writer,
                    None,
                    protocol.CAPACITY_EXHAUSTED,
                    "connection capacity exhausted",
                )
                return
            self._writers.add(writer)
            try:
                await self._authenticated_session(reader, writer)
            finally:
                self._writers.discard(writer)
        finally:
            if task is not None:
                self._tasks.discard(task)
            await _close_writer(writer)

    async def _authenticated_session(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        sock = writer.get_extra_info("socket")
        try:
            credentials = read_peer_credentials(sock)
        except (OSError, struct.error):
            await self._send_error(
                writer,
                None,
                protocol.UNAUTHENTICATED_PEER,
                "peer credentials are unreadable",
            )
            return
        principal = self._policy.resolve(credentials.uid)
        if principal is None:
            _LOGGER.warning(
                "arsd: denied unmapped peer uid=%d pid=%d",
                credentials.uid,
                credentials.pid,
            )
            await self._send_error(
                writer,
                None,
                protocol.PEER_UID_DENIED,
                "peer uid has no configured caller mapping",
            )
            return
        caller = AuthenticatedCaller(
            principal=principal, peer_credentials=credentials
        )
        await self._request_loop(reader, writer, caller)

    async def _request_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        caller: AuthenticatedCaller,
    ) -> None:
        while True:
            try:
                line = await reader.readline()
            except ValueError:
                # The stream limit tripped before a newline arrived.
                await self._send_error(
                    writer,
                    None,
                    protocol.FRAME_TOO_LARGE,
                    f"frame exceeds {protocol.MAX_FRAME_BYTES} bytes",
                )
                return
            if not line:
                return
            if self._shutting_down:
                await self._send_error(
                    writer, None, protocol.SHUTTING_DOWN, "server is shutting down"
                )
                return
            try:
                frame = protocol.decode_frame(line)
            except protocol.ProtocolError as err:
                await self._send_error(writer, None, err.code, err.message)
                if err.code == protocol.MALFORMED_FRAME or (
                    err.code == protocol.FRAME_TOO_LARGE
                ):
                    # Framing integrity is gone; resynchronization is unsafe.
                    return
                continue
            request_id: str | None = None
            try:
                parsed = protocol.parse_request(frame)
                request_id = parsed.request_id
                result = await self._handler(caller, parsed)
                reply = protocol.build_result(parsed.request_id, result)
            except protocol.ProtocolError as err:
                await self._send_error(writer, request_id, err.code, err.message)
                continue
            except Exception as exc:
                # Class name only: handler exceptions may embed caller text.
                _LOGGER.error(
                    "arsd: request handler failed (%s); closing connection",
                    type(exc).__name__,
                )
                await self._send_error(
                    writer, request_id, protocol.INTERNAL, "internal error"
                )
                return
            try:
                writer.write(protocol.encode_frame(reply))
                await writer.drain()
            except (ConnectionError, OSError, RuntimeError):
                return

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        request_id: str | None,
        code: str,
        message: str,
    ) -> None:
        try:
            writer.write(
                protocol.encode_frame(protocol.build_error(request_id, code, message))
            )
            await writer.drain()
        except (ConnectionError, OSError, RuntimeError):
            pass

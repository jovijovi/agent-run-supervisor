"""Supervised live-process surface for Native ACP (architecture §2).

The supervision layer owns spawn, PID/PGID, full :class:`ProcessIdentity`,
bounded stderr, group termination, wait, and reap. The ACP SDK exclusively
owns the live stdin/stdout JSON-RPC wire, exposed here in the C1-pinned
stream form (``asyncio.StreamWriter`` / ``asyncio.StreamReader``). The legacy
completion-oriented ``runner.execute_subprocess`` stays acpx-only; nothing
here touches it. Startup/turn timeouts are owned by the caller via
``asyncio.wait_for``.
"""

from __future__ import annotations

import asyncio
import os
import signal as _signal
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from agent_run_supervisor.process_liveness import ProcessIdentity, identity_for_pid

DEFAULT_MAX_STDERR_BYTES = 262_144
STDERR_TRUNCATION_MARKER = b"\n[stderr truncated by supervisor byte cap]\n"


class ManagedProcessError(RuntimeError):
    """Spawn/supervision failure of a managed native-agent process."""


@dataclass(frozen=True)
class ManagedProcessLimits:
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    # Grace between SIGTERM and SIGKILL on the internal fail-closed path
    # (spawn-callback failure); normal escalation timing is caller-owned.
    cancel_grace_seconds: float = 5.0


@dataclass(frozen=True)
class ManagedExit:
    exit_code: int | None
    signal: int | None
    kill_signal: str | None
    kill_reason: str | None


class ManagedProcess:
    """One live supervised child: identity + wire streams + bounded stderr."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        identity: ProcessIdentity,
        pgid: int,
        limits: ManagedProcessLimits,
    ) -> None:
        self._process = process
        self.identity = identity
        self.pgid = pgid
        self._limits = limits
        self._stderr_chunks: list[bytes] = []
        self._stderr_size = 0
        self._stderr_truncated = False
        self._stderr_task = asyncio.ensure_future(self._drain_stderr())
        self._kill_signal: str | None = None
        self._kill_reason: str | None = None
        self._exit: ManagedExit | None = None

    # -- wire (handed exclusively to the ACP SDK) --------------------------

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def stdin(self) -> asyncio.StreamWriter:
        assert self._process.stdin is not None
        return self._process.stdin

    @property
    def stdout(self) -> asyncio.StreamReader:
        assert self._process.stdout is not None
        return self._process.stdout

    # -- bounded stderr (owned by the supervisor, never the SDK) -----------

    async def _drain_stderr(self) -> None:
        reader = self._process.stderr
        assert reader is not None
        cap = max(self._limits.max_stderr_bytes, 0)
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            # Keep draining past the cap so the child can never block on a
            # full stderr pipe; bytes beyond the cap are dropped.
            room = cap - self._stderr_size
            if room > 0:
                kept = chunk[:room]
                self._stderr_chunks.append(kept)
                self._stderr_size += len(kept)
                if len(kept) < len(chunk):
                    self._stderr_truncated = True
            else:
                self._stderr_truncated = True

    @property
    def stderr_truncated(self) -> bool:
        return self._stderr_truncated

    def stderr_bytes(self) -> bytes:
        collected = b"".join(self._stderr_chunks)
        if self._stderr_truncated:
            return collected + STDERR_TRUNCATION_MARKER
        return collected

    # -- termination and reap ----------------------------------------------

    def _record_kill(self, kill_signal: str, kill_reason: str) -> None:
        # Escalation wins: SIGKILL overwrites a prior SIGTERM record.
        if self._kill_signal is None or kill_signal == "SIGKILL":
            self._kill_signal = kill_signal
            self._kill_reason = kill_reason

    def _signal_group(self, signum: int) -> None:
        try:
            os.killpg(self.pgid, signum)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
        try:
            if signum == _signal.SIGKILL:
                self._process.kill()
            else:
                self._process.terminate()
        except ProcessLookupError:
            pass

    def terminate_group(self, *, reason: str = "terminate_group") -> None:
        self._record_kill("SIGTERM", reason)
        self._signal_group(_signal.SIGTERM)

    def kill_group(self, *, reason: str = "kill_group") -> None:
        self._record_kill("SIGKILL", reason)
        self._signal_group(_signal.SIGKILL)

    async def wait(self) -> ManagedExit:
        """Await child exit and reap it. Never reads stdout."""
        if self._exit is not None:
            return self._exit
        returncode = await self._process.wait()
        # stderr reaches EOF once the pipe closes; bound the join defensively.
        try:
            await asyncio.wait_for(self._stderr_task, self._limits.cancel_grace_seconds)
        except asyncio.TimeoutError:
            self._stderr_task.cancel()
        if returncode is not None and returncode < 0:
            exit_code: int | None = None
            exit_signal: int | None = -returncode
        else:
            exit_code = returncode
            exit_signal = None
        self._exit = ManagedExit(
            exit_code=exit_code,
            signal=exit_signal,
            kill_signal=self._kill_signal,
            kill_reason=self._kill_reason,
        )
        return self._exit


async def spawn_managed_process(
    *,
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    limits: ManagedProcessLimits,
    on_spawn: Callable[[int], None] | None = None,
) -> ManagedProcess:
    """Spawn a supervised child in its own POSIX session/process group.

    ``on_spawn`` failure is fail-closed: the just-spawned group is terminated
    (SIGTERM → grace → SIGKILL), reaped, and the error re-raised as
    :class:`ManagedProcessError` — an untracked child is never left running.
    """
    if os.name != "posix":
        raise ManagedProcessError(
            "spawn_managed_process requires POSIX: start_new_session is mandatory"
        )
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=dict(env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise ManagedProcessError(f"failed to spawn managed process: {exc}") from exc

    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        # The child is its own session leader, so its pgid equals its pid even
        # when it already exited before the query.
        pgid = process.pid

    managed = ManagedProcess(
        process,
        identity=identity_for_pid(process.pid),
        pgid=pgid,
        limits=limits,
    )

    if on_spawn is not None:
        try:
            on_spawn(process.pid)
        except Exception as exc:
            managed.terminate_group(reason="spawn_callback_failed")
            try:
                await asyncio.wait_for(managed.wait(), limits.cancel_grace_seconds)
            except asyncio.TimeoutError:
                managed.kill_group(reason="spawn_callback_failed_force_kill")
                await managed.wait()
            raise ManagedProcessError(f"spawn callback failed: {exc}") from exc

    return managed

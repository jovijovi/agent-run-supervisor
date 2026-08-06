"""Supervised live-process surface for Native ACP (architecture §2).

The supervision layer owns spawn, PID/PGID, full :class:`ProcessIdentity`,
bounded stderr, group termination, wait, and reap. The ACP SDK exclusively
owns the live stdin/stdout JSON-RPC wire, exposed here in the C1-pinned
stream form (``asyncio.StreamWriter`` / ``asyncio.StreamReader``). This is the
only supervision layer: the completion-oriented ``execute_subprocess`` shape of
the retired runtime is gone, and its stdout-drain / wait-before-return contract
must not be reintroduced here. Startup/turn timeouts are owned by the caller via
``asyncio.wait_for``.
"""

from __future__ import annotations

import asyncio
import errno as _errno
import os
import signal as _signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from agent_run_supervisor.process_liveness import ProcessIdentity, identity_for_pid

DEFAULT_MAX_STDERR_BYTES = 262_144
STDERR_TRUNCATION_MARKER = b"\n[stderr truncated by supervisor byte cap]\n"

# Stable spawn classifications. These are ordinary configuration errors — "you
# upgraded and the shim moved" — never security refusals, and no process exists
# in any of these cases.
COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
COMMAND_NOT_EXECUTABLE = "COMMAND_NOT_EXECUTABLE"
SPAWN_FAILED = "SPAWN_FAILED"

# Exactly two errnos are classified, and everything else falls through. The
# narrowness is the point: each of these tells an operator something specific
# and actionable about the command they declared, and nothing else does.
#
# EPERM and EISDIR look like they belong here and do not. EPERM is raised for
# ptrace scope, seccomp, and no_new_privs situations that say nothing about the
# file's mode, and EISDIR says the operand was never a program at all —
# reporting either as COMMAND_NOT_EXECUTABLE would send an operator to fix a
# permission bit that is not the problem.
_ERRNO_CLASSIFICATION = {
    _errno.ENOENT: COMMAND_NOT_FOUND,
    _errno.EACCES: COMMAND_NOT_EXECUTABLE,
}


class ManagedProcessError(RuntimeError):
    """Spawn/supervision failure of a managed native-agent process.

    ``code`` is one of the stable classifications above. The message is fixed
    text plus that code: the OS error names the image path and errno detail,
    both of which are child- or operator-controlled, so neither is interpolated
    into anything projectable.
    """

    def __init__(self, message: str, *, code: str = SPAWN_FAILED) -> None:
        super().__init__(message)
        self.code = code


def classify_spawn_errno(exc: BaseException) -> str:
    """Classify the exec failure ARS observed. There is no pre-flight check.

    Classification happens *after* the attempt, deliberately: a pre-flight
    resolution gate would have to re-implement ``execvp`` lookup, and would then
    disagree with the kernel exactly when it mattered.
    """
    number = getattr(exc, "errno", None)
    return _ERRNO_CLASSIFICATION.get(number, SPAWN_FAILED)


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

    def group_is_gone(self) -> bool:
        """Is the launched process group empty?

        A separate question from :meth:`wait`, which reaps the **leader** and
        reports on nothing else. A descendant that inherited the group outlives
        its parent: if it also ignores SIGTERM and does not hold the inherited
        pipes open, the leader's exit and stderr EOF both arrive promptly and
        the reap looks completely clean while the descendant keeps running.

        ``killpg(pgid, 0)`` is the POSIX existence probe for the whole group.
        ESRCH means empty. Every other answer — including EPERM, which says
        something is there that this process may not signal — is *present*, so
        an ambiguous probe fails closed.

        Ask this only after the leader has been reaped: until then the leader's
        own zombie is a group member and the answer is trivially "present".
        """
        try:
            os.killpg(self.pgid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

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


def _exec_environment(env: Any) -> dict[str, str]:
    """Take the child environment from the one resolution, never from ambient.

    A :class:`~agent_run_supervisor.native_acp.spec.ResolvedEnvironment` hands
    over its own copy; a plain mapping is copied here. Either way the mapping
    this function returns is what the child receives, and nothing re-reads
    ``os.environ`` at spawn.
    """
    exec_mapping = getattr(env, "exec_mapping", None)
    if exec_mapping is not None:
        return dict(exec_mapping)
    return dict(env)


async def spawn_managed_process(
    *,
    argv: list[str],
    cwd: Path,
    env: Any,
    limits: ManagedProcessLimits,
    on_spawn: Callable[[int], None] | None = None,
) -> ManagedProcess:
    """Spawn a supervised child in its own POSIX session/process group.

    Declared command semantics are preserved exactly: ``argv[0]`` is the
    declared string as handed in, and the exec image is located by ordinary
    ``execvp``-style lookup over the **child's** projected ``PATH``. There is no
    ``executable=`` override, no descriptor-based image, and no realpath — which
    is what lets version-manager shims, symlink farms, package-relative
    resolution, multicall ``argv[0]`` dispatch, and an agent's own self-relaunch
    keep working.

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
            env=_exec_environment(env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        # The errno survives as a stable code; the exception's own text does
        # not, because it names the image path the operator declared.
        raise ManagedProcessError(
            "failed to spawn managed process", code=classify_spawn_errno(exc)
        ) from exc

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

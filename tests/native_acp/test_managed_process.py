"""C3: supervised live-process surface (spawn_managed_process / ManagedProcess).

Stdlib child processes only — no SDK dependency, so this suite runs without
the native extra. The test plays the wire-owner role the ACP SDK takes in
production (single stdin/stdout consumer) while the supervisor surface owns
identity, bounded stderr, group termination, wait, and reap.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from agent_run_supervisor.managed_process import (
    STDERR_TRUNCATION_MARKER,
    ManagedProcessError,
    ManagedProcessLimits,
    spawn_managed_process,
)
from agent_run_supervisor.process_liveness import ProcessIdentity

ECHO_CHILD = (
    "import sys\n"
    "for line in sys.stdin:\n"
    "    sys.stdout.write(line.upper())\n"
    "    sys.stdout.flush()\n"
)

SLEEPER = "import time; time.sleep(600)"

NOISY_STDERR = (
    "import sys\n"
    "sys.stderr.write('x' * 100000)\n"
    "sys.stderr.flush()\n"
)

QUIET_STDERR = (
    "import sys\n"
    "sys.stderr.write('brief note')\n"
    "sys.stderr.flush()\n"
)

GRANDCHILD_SPAWNER = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(600)\n"
)

SIGTERM_IGNORER = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready', flush=True)\n"
    "time.sleep(600)\n"
)


def _spawn(script: str, *, limits: ManagedProcessLimits | None = None, on_spawn=None):
    return spawn_managed_process(
        argv=[sys.executable, "-u", "-c", script],
        cwd=Path.cwd(),
        env=dict(os.environ),
        limits=limits or ManagedProcessLimits(),
        on_spawn=on_spawn,
    )


async def _assert_pid_gone(pid: int, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.02)
    pytest.fail(f"pid {pid} still exists")


def test_live_wire_round_trips_while_running() -> None:
    async def case() -> None:
        proc = await _spawn(ECHO_CHILD)
        try:
            # The C1-pinned stream form: asyncio streams, not raw pipes.
            assert isinstance(proc.stdin, asyncio.StreamWriter)
            assert isinstance(proc.stdout, asyncio.StreamReader)
            proc.stdin.write(b"hello native\n")
            await proc.stdin.drain()
            assert await asyncio.wait_for(proc.stdout.readline(), 10) == b"HELLO NATIVE\n"
            # A second round-trip proves the wire stays live between writes
            # (non-completion-oriented I/O with a single stdout consumer).
            proc.stdin.write(b"second turn\n")
            await proc.stdin.drain()
            assert await asyncio.wait_for(proc.stdout.readline(), 10) == b"SECOND TURN\n"
        finally:
            proc.stdin.close()
            exit_state = await asyncio.wait_for(proc.wait(), 10)
        assert exit_state.exit_code == 0
        assert exit_state.signal is None

    asyncio.run(case())


def test_identity_captured_and_new_session_pgid() -> None:
    async def case() -> None:
        proc = await _spawn(SLEEPER)
        try:
            assert isinstance(proc.identity, ProcessIdentity)
            assert proc.identity.pid == proc.pid
            assert proc.identity.host
            # start_new_session=True makes the child its own group leader.
            assert proc.pgid == proc.pid
        finally:
            proc.kill_group()
            await asyncio.wait_for(proc.wait(), 10)

    asyncio.run(case())


def test_stderr_is_bounded_with_truncation_marker() -> None:
    async def case() -> None:
        limits = ManagedProcessLimits(max_stderr_bytes=1024)
        proc = await _spawn(NOISY_STDERR, limits=limits)
        await asyncio.wait_for(proc.wait(), 10)
        collected = proc.stderr_bytes()
        assert proc.stderr_truncated is True
        assert STDERR_TRUNCATION_MARKER in collected
        assert len(collected) <= 1024 + len(STDERR_TRUNCATION_MARKER)

    asyncio.run(case())


def test_small_stderr_is_kept_verbatim() -> None:
    async def case() -> None:
        proc = await _spawn(QUIET_STDERR)
        await asyncio.wait_for(proc.wait(), 10)
        assert proc.stderr_truncated is False
        assert proc.stderr_bytes() == b"brief note"

    asyncio.run(case())


def test_group_termination_kills_grandchild() -> None:
    async def case() -> None:
        proc = await _spawn(GRANDCHILD_SPAWNER)
        line = await asyncio.wait_for(proc.stdout.readline(), 10)
        grandchild_pid = int(line)
        proc.terminate_group()
        exit_state = await asyncio.wait_for(proc.wait(), 10)
        assert exit_state.signal == signal.SIGTERM
        assert exit_state.kill_signal == "SIGTERM"
        await _assert_pid_gone(grandchild_pid)

    asyncio.run(case())


def test_escalation_kills_sigterm_ignoring_child() -> None:
    async def case() -> None:
        proc = await _spawn(SIGTERM_IGNORER)
        await asyncio.wait_for(proc.stdout.readline(), 10)
        proc.terminate_group()
        waiter = asyncio.ensure_future(proc.wait())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(waiter), 0.5)
        proc.kill_group()
        exit_state = await asyncio.wait_for(waiter, 10)
        assert exit_state.signal == signal.SIGKILL
        assert exit_state.kill_signal == "SIGKILL"

    asyncio.run(case())


def test_wait_reaps_child_no_zombie() -> None:
    async def case() -> None:
        proc = await _spawn("pass")
        exit_state = await asyncio.wait_for(proc.wait(), 10)
        assert exit_state.exit_code == 0
        # A reaped child has no /proc entry; a zombie would still have one.
        assert not Path(f"/proc/{proc.pid}").exists()

    asyncio.run(case())


def test_on_spawn_failure_fails_closed() -> None:
    async def case() -> None:
        seen: list[int] = []

        def veto(pid: int) -> None:
            seen.append(pid)
            raise RuntimeError("registration veto")

        with pytest.raises(ManagedProcessError, match="spawn callback"):
            await _spawn(SLEEPER, on_spawn=veto)
        assert seen, "on_spawn was never called"
        await _assert_pid_gone(seen[0])

    asyncio.run(case())


def test_missing_executable_raises_typed_error() -> None:
    async def case() -> None:
        with pytest.raises(ManagedProcessError):
            await spawn_managed_process(
                argv=["/nonexistent/native-agent-binary"],
                cwd=Path.cwd(),
                env=dict(os.environ),
                limits=ManagedProcessLimits(),
            )

    asyncio.run(case())

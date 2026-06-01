"""K1 process-liveness classification.

These tests pin the conservative, fail-safe liveness decision used for safe
crash recovery: a recorded lease holder is classified ALIVE, CRASHED, or UNKNOWN
without ever sending a terminating signal or killing anything. The classifier is
driven through an injected :class:`LivenessProbe` so crashed/alive/unknown
scenarios are deterministic and need no real crashed PIDs. A small number of
tests exercise the real (stdlib) probe against the running process.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_run_supervisor.process_liveness import (
    ALIVE,
    CRASHED,
    UNKNOWN,
    LivenessProbe,
    ProcessIdentity,
    classify_holder,
    current_identity,
    pid_is_running,
    read_boot_id,
    read_process_start,
)

_HAVE_PROC = sys.platform.startswith("linux") and os.path.isdir("/proc")
_proc_only = pytest.mark.skipif(not _HAVE_PROC, reason="requires /proc")


# --- helpers --------------------------------------------------------------


def _current(*, host="host-1", boot="boot-A", pid=1000, start="100") -> ProcessIdentity:
    return ProcessIdentity(pid=pid, process_start=start, boot_id=boot, host=host)


def _probe(current: ProcessIdentity, *, running, start) -> LivenessProbe:
    """A fake probe: ``running``/``start`` are the verdicts for the probed PID."""
    return LivenessProbe(
        is_running=lambda pid: running,
        read_start=lambda pid: start,
        current=lambda: current,
    )


def _holder(*, pid=2000, start="222", boot="boot-A", host="host-1") -> dict:
    return {"pid": pid, "process_start": start, "boot_id": boot, "host": host}


# --- classify_holder ------------------------------------------------------


def test_classify_alive_same_host_pid_running_start_matches() -> None:
    current = _current()
    probe = _probe(current, running=True, start="222")
    assert classify_holder(_holder(start="222"), probe=probe) == ALIVE


def test_classify_crashed_when_pid_absent() -> None:
    current = _current()
    probe = _probe(current, running=False, start=None)
    assert classify_holder(_holder(), probe=probe) == CRASHED


def test_classify_crashed_when_start_differs_pid_reuse() -> None:
    current = _current()
    # PID is running but its start time no longer matches what we recorded: the
    # original holder is gone and the PID was reused by a different process.
    probe = _probe(current, running=True, start="999")
    assert classify_holder(_holder(start="222"), probe=probe) == CRASHED


def test_classify_crashed_when_boot_id_differs_reboot() -> None:
    current = _current(boot="boot-NEW")
    # Same host but a different boot id => the machine rebooted since the lease,
    # so the recorded PID is definitely dead. Even a "running" probe must not
    # flip this to ALIVE.
    probe = _probe(current, running=True, start="222")
    assert classify_holder(_holder(boot="boot-OLD", start="222"), probe=probe) == CRASHED


def test_classify_unknown_when_host_differs() -> None:
    current = _current(host="host-1")
    probe = _probe(current, running=True, start="222")
    # We cannot validate a PID that lives on another machine.
    assert classify_holder(_holder(host="host-2"), probe=probe) == UNKNOWN


def test_classify_unknown_when_holder_has_no_pid() -> None:
    current = _current()
    probe = _probe(current, running=True, start="222")
    holder = {"host": "host-1", "boot_id": "boot-A"}  # old-format lock, no pid
    assert classify_holder(holder, probe=probe) == UNKNOWN


@pytest.mark.parametrize("bad_pid", [0, -1, None, "1000", True])
def test_classify_unknown_for_invalid_pid(bad_pid) -> None:
    current = _current()
    probe = _probe(current, running=True, start="222")
    assert classify_holder(_holder(pid=bad_pid), probe=probe) == UNKNOWN


def test_classify_unknown_when_recorded_start_missing() -> None:
    current = _current()
    probe = _probe(current, running=True, start="222")
    assert classify_holder(_holder(start=None), probe=probe) == UNKNOWN


def test_classify_unknown_when_current_start_unreadable() -> None:
    current = _current()
    probe = _probe(current, running=True, start=None)
    assert classify_holder(_holder(start="222"), probe=probe) == UNKNOWN


def test_classify_unknown_when_probe_indeterminate() -> None:
    current = _current()
    probe = _probe(current, running=None, start="222")
    assert classify_holder(_holder(start="222"), probe=probe) == UNKNOWN


def test_classify_alive_when_boot_unknown_falls_back_to_pid_and_start() -> None:
    # Missing boot ids on either side must not short-circuit; rely on PID + start.
    current = _current(boot=None)
    probe = _probe(current, running=True, start="222")
    assert classify_holder(_holder(boot=None, start="222"), probe=probe) == ALIVE


# --- real (stdlib) probe --------------------------------------------------


def test_pid_is_running_true_for_current_process() -> None:
    assert pid_is_running(os.getpid()) is True


@pytest.mark.parametrize("bad_pid", [0, -1, -1000])
def test_pid_is_running_none_for_nonpositive_pid(bad_pid) -> None:
    # Never probe a process group / "all processes" sentinel.
    assert pid_is_running(bad_pid) is None


@_proc_only
def test_pid_is_running_false_for_free_pid() -> None:
    pid = 999_999
    while Path(f"/proc/{pid}").exists():
        pid -= 1
    assert pid_is_running(pid) is False


@_proc_only
def test_read_process_start_is_digit_string_for_current_process() -> None:
    start = read_process_start(os.getpid())
    assert start is not None and start.isdigit()


@_proc_only
def test_read_process_start_stable_across_calls() -> None:
    assert read_process_start(os.getpid()) == read_process_start(os.getpid())


@_proc_only
def test_read_process_start_none_for_free_pid() -> None:
    pid = 999_999
    while Path(f"/proc/{pid}").exists():
        pid -= 1
    assert read_process_start(pid) is None


@_proc_only
def test_read_boot_id_non_empty() -> None:
    boot = read_boot_id()
    assert boot is not None and boot.strip() == boot and boot


def test_current_identity_reports_running_process() -> None:
    identity = current_identity()
    assert identity.pid == os.getpid()
    assert identity.host  # non-empty hostname


@_proc_only
def test_current_identity_classifies_self_as_alive() -> None:
    # The running process, recorded as a holder, must classify ALIVE against the
    # real probe — the core invariant that prevents reclaiming a live holder.
    identity = current_identity()
    holder = {
        "pid": identity.pid,
        "process_start": identity.process_start,
        "boot_id": identity.boot_id,
        "host": identity.host,
    }
    assert classify_holder(holder) == ALIVE


@_proc_only
def test_reaped_child_classifies_as_crashed() -> None:
    # A finished + reaped child PID (recorded with the current host/boot/start)
    # is gone. Probing it must classify CRASHED, not ALIVE.
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    identity = current_identity()
    holder = {
        "pid": proc.pid,
        "process_start": read_process_start(proc.pid),  # None once reaped
        "boot_id": identity.boot_id,
        "host": identity.host,
    }
    assert classify_holder(holder) == CRASHED

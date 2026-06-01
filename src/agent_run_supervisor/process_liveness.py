"""K1 process-liveness classification for safe crash recovery.

Stdlib-only. This module decides whether the process that recorded a session
lease is still :data:`ALIVE`, has provably :data:`CRASHED`, or is in an
unverifiable :data:`UNKNOWN` state — **without ever sending a terminating signal
or killing anything**. The only syscall it issues against a recorded PID is a
no-op ``os.kill(pid, 0)`` existence probe (POSIX delivers no signal for signal
``0``); on Linux it additionally reads ``/proc/<pid>/stat`` field 22 (process
start time) to defeat PID reuse, and ``/proc/sys/kernel/random/boot_id`` to
detect a reboot.

Fail-safe by construction: the verdict is :data:`CRASHED` only when liveness can
be *positively proven* (the PID is absent, its start time no longer matches, or
the machine rebooted). Whenever liveness cannot be proven — a missing/foreign
PID, a different host, an unreadable start time, or an indeterminate probe — the
verdict is :data:`UNKNOWN`, never :data:`CRASHED`. A live holder is therefore
never mistaken for a crashed one, so a session is never wrongly reclaimed.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Any, Callable, Mapping

#: A recorded holder is still running.
ALIVE = "alive"
#: A recorded holder has provably crashed/exited; its lease is safe to reclaim.
CRASHED = "crashed"
#: Liveness could not be proven; treat the holder as possibly-alive (refuse).
UNKNOWN = "unknown"

_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"


@dataclass(frozen=True)
class ProcessIdentity:
    """The identity of a process that records (or is checked against) a lease."""

    pid: int
    process_start: str | None
    boot_id: str | None
    host: str


@dataclass(frozen=True)
class LivenessProbe:
    """Injection seam for liveness signals.

    Bundling the three signals (PID existence, PID start time, and the current
    process identity) lets tests drive deterministic crashed/alive/unknown
    scenarios without real crashed PIDs, and keeps the real probe stdlib-only.
    """

    is_running: Callable[[int], bool | None]
    read_start: Callable[[int], str | None]
    current: Callable[[], ProcessIdentity]


def _valid_pid(pid: Any) -> bool:
    """A usable PID is a positive int (never a bool, never <= 0)."""
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0


def pid_is_running(pid: int) -> bool | None:
    """Read-only existence probe. Returns ``True``/``False``/``None``.

    Uses ``os.kill(pid, 0)``, which delivers **no signal** and only reports
    whether the PID exists and is signalable: ``True`` when present (including a
    ``PermissionError`` — it exists but is owned by another user), ``False`` when
    absent (``ProcessLookupError``), and ``None`` for any other ``OSError`` or an
    invalid PID. It never sends a terminating signal and never kills a process.
    """
    if not _valid_pid(pid):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def read_process_start(pid: int) -> str | None:
    """Return ``/proc/<pid>/stat`` field 22 (start time) as a string, or ``None``.

    The ``comm`` field (field 2) is parenthesized and may itself contain spaces
    or parentheses, so the remaining fields are parsed after the final ``)``.
    Any error (no ``/proc``, missing PID, malformed content) returns ``None`` so
    a platform without ``/proc`` degrades safely.
    """
    if not _valid_pid(pid):
        return None
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return None
    _, sep, rest = content.rpartition(")")
    if not sep:
        return None
    fields = rest.split()
    # After comm, fields[0] is `state` (field 3); starttime is field 22 -> index 19.
    if len(fields) < 20:
        return None
    return fields[19]


def read_boot_id() -> str | None:
    """Return the kernel boot id, or ``None`` where it is unavailable."""
    try:
        with open(_BOOT_ID_PATH, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def identity_for_pid(pid: int) -> ProcessIdentity:
    """Capture a specific local PID's identity for recording into a lease."""
    return ProcessIdentity(
        pid=pid,
        process_start=read_process_start(pid),
        boot_id=read_boot_id(),
        host=socket.gethostname(),
    )


def current_identity() -> ProcessIdentity:
    """Capture this process's identity for recording into a lease."""
    return identity_for_pid(os.getpid())


#: The real, stdlib-backed probe used in production.
REAL_PROBE = LivenessProbe(
    is_running=pid_is_running,
    read_start=read_process_start,
    current=current_identity,
)


def classify_holder(
    holder: Mapping[str, Any],
    *,
    probe: LivenessProbe = REAL_PROBE,
) -> str:
    """Classify a recorded lease ``holder`` as ALIVE / CRASHED / UNKNOWN.

    ``holder`` is the lock record's identity view (``pid``/``process_start``/
    ``boot_id``/``host``). The decision is fail-safe: see the module docstring.
    """
    pid = holder.get("pid")
    if not _valid_pid(pid):
        return UNKNOWN  # no usable PID (e.g. an old-format lock)

    current = probe.current()
    host = holder.get("host")
    if not host or host != current.host:
        return UNKNOWN  # cannot validate a PID on another (or unknown) host

    holder_boot = holder.get("boot_id")
    if holder_boot and current.boot_id and holder_boot != current.boot_id:
        return CRASHED  # machine rebooted since the lease -> the PID is dead

    running = probe.is_running(pid)
    if running is False:
        return CRASHED  # no such process
    if running is None:
        return UNKNOWN  # indeterminate probe -> assume possibly-alive (refuse)

    # PID is present: defeat PID reuse via the recorded vs current start time.
    holder_start = holder.get("process_start")
    current_start = probe.read_start(pid)
    if holder_start and current_start:
        return ALIVE if holder_start == current_start else CRASHED
    return UNKNOWN  # start time unverifiable -> assume possibly-alive (refuse)


def _child_holder_view(lock_data: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project the additive child-subprocess identity, or ``None`` if absent.

    After a supervisor spawns its acpx subprocess it records the child identity
    in ``child_pid``/``child_process_start``/``child_boot_id``/``child_host``
    *alongside* (never replacing) its own top-level identity. This returns that
    child identity shaped like a holder for :func:`classify_holder`, or ``None``
    when no child has been recorded (a pre-subprocess holder).
    """
    if lock_data.get("child_pid") is None:
        return None
    return {
        "pid": lock_data.get("child_pid"),
        "process_start": lock_data.get("child_process_start"),
        "boot_id": lock_data.get("child_boot_id"),
        "host": lock_data.get("child_host"),
    }


def _combine_liveness(supervisor: str, child: str) -> str:
    """Fail-safe combine of two holder verdicts.

    The composite is :data:`CRASHED` only when *both* identities are provably
    crashed. A single :data:`ALIVE` dominates (a live identity blocks reclaim);
    any remaining uncertainty is :data:`UNKNOWN`. So a lock is reclaimable by
    liveness only when neither the supervisor nor its child can still be running.
    """
    if supervisor == ALIVE or child == ALIVE:
        return ALIVE
    if supervisor == CRASHED and child == CRASHED:
        return CRASHED
    return UNKNOWN


def classify_lock(
    lock_data: Mapping[str, Any],
    *,
    probe: LivenessProbe = REAL_PROBE,
) -> str:
    """Classify a lock's combined supervisor + child-subprocess liveness.

    The lock's top-level ``pid``/``process_start``/``boot_id``/``host`` identify
    the *supervisor* that owns artifact mutation. Once it spawns its acpx
    subprocess it ALSO records that child's identity in the additive ``child_*``
    fields, WITHOUT dropping the supervisor identity. Such a composite lock is
    provably reclaimable only when **both** identities are provably crashed; if
    either is alive — or either cannot be proven crashed — the verdict is
    :data:`ALIVE`/:data:`UNKNOWN` respectively. This is what prevents reclaiming
    a still-live supervisor that is finishing turn/close artifacts after its
    child subprocess has already exited.

    A lock with no child identity classifies by its single recorded holder,
    preserving the original single-holder contract for pre-subprocess locks.
    """
    supervisor = classify_holder(lock_data, probe=probe)
    child_view = _child_holder_view(lock_data)
    if child_view is None:
        return supervisor
    return _combine_liveness(supervisor, classify_holder(child_view, probe=probe))

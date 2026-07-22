"""Typed systemd --user unit renderer for the shipped arsd daemon.

The module is the single source of truth for the service artifact (no
repository-only ``.service`` template). Rendering is pure data → text;
it never installs, enables, or starts a unit.

Specifier rule: only renderer-owned defaults may emit expandable ``%t`` /
``%h``. Every caller-supplied token escapes ``%`` → ``%%`` so systemd cannot
rewrite operator data via ``%n`` / ``%i`` / ``%s`` / etc.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

__all__ = [
    "DEFAULT_RESTART_SEC",
    "DEFAULT_TIMEOUT_STOP_SEC",
    "DEFAULT_USER_SOCKET",
    "DEFAULT_USER_SUPERVISOR_ROOT",
    "MODULE_NAME",
    "ServiceUnitError",
    "render_service_unit",
]

# User-scope systemd specifiers (intentionally preserved for A3 operators).
DEFAULT_USER_SOCKET = "%t/agent-run-supervisor/arsd.sock"
DEFAULT_USER_SUPERVISOR_ROOT = "%h/.local/share/agent-run-supervisor"
MODULE_NAME = "agent_run_supervisor.arsd"
# Bounded above the daemon's graceful shutdown window (90s) without hanging forever.
DEFAULT_TIMEOUT_STOP_SEC = 120
# Observation window for old-cgroup death before Restart=on-failure respawn.
DEFAULT_RESTART_SEC = 10

_SAFE_UNQUOTED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "/._-:@+="
)


class ServiceUnitError(ValueError):
    """Fail-closed refusal to render an unsafe or injectable unit fragment."""


def _reject_controls(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ServiceUnitError(f"{label} must be a non-empty string")
    for ch in value:
        if ord(ch) < 32 or ord(ch) == 127:
            raise ServiceUnitError(
                f"{label} contains control characters (newline/directive injection refused)"
            )
    return value


def _escape_data_percent(value: str) -> str:
    """Escape every ``%`` in caller/data tokens so systemd will not expand them."""
    return value.replace("%", "%%")


def _systemd_quote(value: str, *, escape_percent: bool) -> str:
    """Quote a single ExecStart argv token for systemd (data, not shell)."""
    _reject_controls(value, label="exec argument")
    if escape_percent:
        value = _escape_data_percent(value)
        if all(ch in _SAFE_UNQUOTED for ch in value):
            return value
    else:
        # Renderer-owned defaults may intentionally include %t / %h.
        if all(ch in _SAFE_UNQUOTED or ch == "%" for ch in value):
            return value
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def render_service_unit(
    *,
    socket_path: str | None = None,
    supervisor_root: str | None = None,
    caller_mappings: Sequence[str] = (),
    python_executable: str | None = None,
    timeout_stop_sec: int = DEFAULT_TIMEOUT_STOP_SEC,
    restart_sec: int = DEFAULT_RESTART_SEC,
) -> str:
    """Render a systemd **user** unit for ``python -m agent_run_supervisor.arsd``.

    Defaults use user-scope specifiers and **no** caller mapping (fail-closed
    until an A3/G12-approved mapping is explicitly supplied). Paths and
    mappings are argv data — never shell, never root/system-scope directives.
    """
    if not isinstance(timeout_stop_sec, int) or not (30 <= timeout_stop_sec <= 300):
        raise ServiceUnitError("TimeoutStopSec must be an int in [30, 300]")
    if not isinstance(restart_sec, int) or not (1 <= restart_sec <= 60):
        raise ServiceUnitError("RestartSec must be an int in [1, 60]")

    python = python_executable if python_executable is not None else sys.executable
    python = _reject_controls(python, label="python_executable")
    if not python.startswith("/"):
        raise ServiceUnitError("python_executable must be an absolute path")

    socket_is_default = socket_path is None
    root_is_default = supervisor_root is None
    socket = DEFAULT_USER_SOCKET if socket_is_default else str(socket_path)
    root = (
        DEFAULT_USER_SUPERVISOR_ROOT if root_is_default else str(supervisor_root)
    )
    socket = _reject_controls(socket, label="socket_path")
    root = _reject_controls(root, label="supervisor_root")

    # python / mappings / caller paths = data (escape %). Defaults keep %t/%h.
    argv_parts: list[tuple[str, bool]] = [
        (python, True),
        ("-m", True),
        (MODULE_NAME, True),
        ("--socket", True),
        (socket, not socket_is_default),
        ("--supervisor-root", True),
        (root, not root_is_default),
    ]
    for raw in caller_mappings:
        mapping = _reject_controls(str(raw), label="caller_mapping")
        parts = mapping.split(":", 3)
        if len(parts) != 4 or not all(parts):
            raise ServiceUnitError(
                "caller_mapping must be UID:principal_id:owner:namespace"
            )
        try:
            uid = int(parts[0])
        except ValueError as exc:
            raise ServiceUnitError("caller_mapping UID must be an integer") from exc
        if uid < 0:
            raise ServiceUnitError("caller_mapping UID must be non-negative")
        argv_parts.append(("--caller-mapping", True))
        argv_parts.append((mapping, True))

    exec_start = "ExecStart=" + " ".join(
        _systemd_quote(part, escape_percent=escape) for part, escape in argv_parts
    )
    lines = [
        "[Unit]",
        "Description=agent-run-supervisor arsd (user)",
        "Documentation=https://github.com/jovijovi/agent-run-supervisor",
        "",
        "[Service]",
        "Type=simple",
        exec_start,
        "Restart=on-failure",
        f"RestartSec={restart_sec}",
        "KillMode=control-group",
        f"TimeoutStopSec={timeout_stop_sec}",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    unit = "\n".join(lines)
    lowered = unit.lower()
    if "user=root" in lowered or "\nsudo" in lowered:
        raise ServiceUnitError("refusing to emit root-mode directives")
    return unit

#!/usr/bin/env python3
"""S4 crash-containment driver for user-scope arsd (A3-gated execution).

Production-usable harness source. Execution requires an explicit A3
acknowledgment flag/env plus operator-supplied user-unit name, socket,
supervisor root, test-scoped caller mapping, and evidence directory.

Import, ``--help``, and dry validation must not mutate the host (no
systemctl/loginctl/cgroup writes). Never embeds real mapping/credentials.
Never enables a unit; always cleans up a unit file this run created.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_A3_ENV = "ARS_ARSD_A3_CRASH_HARNESS"
_A3_FLAG = "--i-acknowledge-a3-crash-harness"
_UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9@_.+-]+\.service$")
_REPO_ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_MODEL = "kimi-for-coding/k3"
_REQUIRED_EFFORT = "max"
_FRESH_MARKER = "S4_FRESH_OK"
# Conservative bound for crash-run effective.json evidence (1 MiB).
_MAX_EFFECTIVE_JSON_BYTES = 1 * 1024 * 1024
_DECIMAL_START_RE = re.compile(r"^[0-9]+$")


class HarnessGateError(RuntimeError):
    """Fail-closed refusal when A3 gates or required host facts are missing."""


def _eprint(message: str) -> None:
    print(f"arsd-crash-harness: {message}", file=sys.stderr)


def _user_unit_path(unit_name: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / unit_name


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arsd_crash_containment_harness.py",
        description=(
            "A3-gated S4 crash containment driver for systemd --user arsd. "
            "Import/help/dry-run never mutate the host."
        ),
    )
    parser.add_argument(
        _A3_FLAG,
        action="store_true",
        help=f"Explicit A3 acknowledgment (also require {_A3_ENV}=1)",
    )
    parser.add_argument(
        "--dry-validate",
        action="store_true",
        help="Validate gates/inputs only; print plan; exit without host mutation",
    )
    parser.add_argument("--unit-name", default="", help="Basename ending in .service")
    parser.add_argument("--socket", default="", help="Absolute AF_UNIX socket path")
    parser.add_argument(
        "--supervisor-root",
        default="",
        help="Absolute dedicated fresh supervisor root (nonexistent or empty)",
    )
    parser.add_argument(
        "--caller-mapping",
        default="",
        help="UID:principal:owner:namespace (UID must equal current user)",
    )
    parser.add_argument(
        "--evidence-dir",
        default="",
        help="Absolute out-of-repo evidence directory",
    )
    parser.add_argument(
        "--workspace",
        default="",
        help="Absolute known-empty disposable workspace",
    )
    return parser


def _require_a3(args: argparse.Namespace) -> None:
    if os.environ.get(_A3_ENV) != "1":
        raise HarnessGateError(
            f"refusing: set {_A3_ENV}=1 (A3 acknowledgment env missing)"
        )
    if not getattr(args, "i_acknowledge_a3_crash_harness", False):
        raise HarnessGateError(
            f"refusing: pass {_A3_FLAG} (A3 acknowledgment flag missing)"
        )


def _reject_controls(text: str, *, label: str) -> str:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise HarnessGateError(f"refusing: {label} contains control characters")
    return text


def _require_absolute(path_text: str, *, label: str) -> Path:
    _reject_controls(path_text, label=label)
    path = Path(path_text)
    if not path.is_absolute():
        raise HarnessGateError(f"refusing: {label} must be an absolute path")
    return path


def _resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise HarnessGateError(f"refusing: cannot resolve path") from exc


def _forbid_inside_repo(path: Path, *, label: str) -> None:
    resolved = _resolve(path)
    repo = _REPO_ROOT.resolve()
    try:
        resolved.relative_to(repo)
    except ValueError:
        return
    raise HarnessGateError(
        f"refusing: {label} must not be inside the repository/worktree"
    )


def _same_or_nested(a: Path, b: Path) -> bool:
    ar, br = _resolve(a), _resolve(b)
    if ar == br:
        return True
    try:
        ar.relative_to(br)
        return True
    except ValueError:
        pass
    try:
        br.relative_to(ar)
        return True
    except ValueError:
        pass
    return False


def _require_operator_inputs(args: argparse.Namespace) -> dict[str, str]:
    required = {
        "unit_name": args.unit_name,
        "socket": args.socket,
        "supervisor_root": args.supervisor_root,
        "caller_mapping": args.caller_mapping,
        "evidence_dir": args.evidence_dir,
        "workspace": args.workspace,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise HarnessGateError(
            "refusing: missing operator-supplied inputs: " + ", ".join(missing)
        )

    unit_name = _reject_controls(required["unit_name"].strip(), label="unit_name")
    if "/" in unit_name or "\\" in unit_name or unit_name in {".", ".."}:
        raise HarnessGateError("refusing: unit_name must be a basename (no slash)")
    if not _UNIT_NAME_RE.fullmatch(unit_name):
        raise HarnessGateError(
            "refusing: unit_name must match "
            r"'^[A-Za-z0-9@_.+-]+\.service$'"
        )
    required["unit_name"] = unit_name

    mapping = _reject_controls(required["caller_mapping"], label="caller_mapping")
    parts = mapping.split(":", 3)
    if len(parts) != 4 or not all(parts):
        raise HarnessGateError(
            "refusing: --caller-mapping must be UID:principal_id:owner:namespace"
        )
    try:
        uid = int(parts[0])
    except ValueError as exc:
        raise HarnessGateError(
            "refusing: caller_mapping UID must be an integer"
        ) from exc
    if uid != os.getuid():
        raise HarnessGateError(
            "refusing: caller_mapping UID must match the current user UID"
        )
    required["caller_mapping"] = mapping

    socket = _require_absolute(required["socket"], label="socket")
    root = _require_absolute(required["supervisor_root"], label="supervisor_root")
    evidence = _require_absolute(required["evidence_dir"], label="evidence_dir")
    workspace = _require_absolute(required["workspace"], label="workspace")

    for label, path in (
        ("socket", socket),
        ("supervisor_root", root),
        ("evidence_dir", evidence),
        ("workspace", workspace),
        ("socket_parent", socket.parent),
    ):
        _forbid_inside_repo(path, label=label)

    # Dedicated/fresh supervisor root: nonexistent or existing empty only.
    if root.exists():
        if not root.is_dir():
            raise HarnessGateError("refusing: supervisor_root must be a directory")
        if any(root.iterdir()):
            raise HarnessGateError(
                "refusing: supervisor_root must be nonexistent or empty "
                "(dedicated test state only)"
            )

    if not workspace.is_dir():
        raise HarnessGateError("refusing: workspace must be an existing directory")
    if any(workspace.iterdir()):
        raise HarnessGateError("refusing: workspace must be known-empty")

    if socket.exists() or socket.is_symlink():
        raise HarnessGateError("refusing: socket path already exists")

    # Path overlap / alias guards among durable surfaces.
    durable = {
        "supervisor_root": root,
        "workspace": workspace,
        "evidence_dir": evidence,
    }
    labels = list(durable)
    for i, left in enumerate(labels):
        for right in labels[i + 1 :]:
            if _same_or_nested(durable[left], durable[right]):
                raise HarnessGateError(
                    f"refusing: path overlap between {left} and {right}"
                )
    for label, path in durable.items():
        if _same_or_nested(socket, path):
            raise HarnessGateError(
                f"refusing: path overlap between socket and {label}"
            )
        if _resolve(socket.parent) == _resolve(path):
            raise HarnessGateError(
                f"refusing: socket must not live directly inside {label}"
            )

    required["socket"] = str(socket)
    required["supervisor_root"] = str(root)
    required["evidence_dir"] = str(evidence)
    required["workspace"] = str(workspace)
    return required


def _systemctl_user(*cmd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *cmd],
        check=check,
        text=True,
        capture_output=True,
    )


def _sanitize_cmd_failure(err: subprocess.CalledProcessError) -> str:
    argv0 = "?"
    if isinstance(err.cmd, (list, tuple)) and err.cmd:
        argv0 = str(err.cmd[0])
    elif isinstance(err.cmd, str):
        argv0 = err.cmd.split()[0] if err.cmd else "?"
    return f"command failed: exit={err.returncode} argv0={argv0}"


def _render_unit(socket: str, supervisor_root: str, mapping: str) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_run_supervisor.arsd",
            "--print-service-unit",
            "--socket",
            socket,
            "--supervisor-root",
            supervisor_root,
            "--caller-mapping",
            mapping,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    unit = proc.stdout
    if "Restart=on-failure" not in unit or "KillMode=control-group" not in unit:
        raise HarnessGateError("rendered unit missing required containment directives")
    if "RestartSec=" not in unit:
        raise HarnessGateError("rendered unit missing RestartSec")
    return unit


def _exclusive_create_unit_file(
    unit_path: Path,
    content: str,
    *,
    on_created: Callable[[], None] | None = None,
) -> None:
    """Create the unit file with O_EXCL|O_NOFOLLOW; refuse races/dangling links.

    ``on_created`` runs inside the write/close-protected try, immediately after
    the exclusive open, so callers can mark the file for cleanup even if the
    subsequent write fails — and the fd is still closed if the callback fails.
    Write loops until every UTF-8 byte is committed; write/fsync/close failures
    become a sanitized ``HarnessGateError`` (raw OS/path/content never surfaces).
    """
    if unit_path.exists() or unit_path.is_symlink():
        raise HarnessGateError(
            "refusing: target unit file already exists (will not overwrite)"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(unit_path), flags, 0o644)
    except FileExistsError as exc:
        raise HarnessGateError(
            "refusing: target unit file already exists (will not overwrite)"
        ) from exc
    except OSError as exc:
        raise HarnessGateError("refusing: exclusive unit create failed") from exc
    primary: BaseException | None = None
    try:
        if on_created is not None:
            on_created()
        data = content.encode("utf-8")
        offset = 0
        while offset < len(data):
            try:
                written = os.write(fd, data[offset:])
            except OSError as exc:
                raise HarnessGateError("refusing: unit file write failed") from exc
            if written <= 0:
                raise HarnessGateError("refusing: unit file write failed")
            offset += written
        try:
            os.fsync(fd)
        except OSError as exc:
            raise HarnessGateError("refusing: unit file write failed") from exc
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            os.close(fd)
        except OSError as close_exc:
            if primary is None:
                raise HarnessGateError(
                    "refusing: unit file write failed"
                ) from close_exc


def _write_evidence(evidence_dir: Path, name: str, payload: dict) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _main_pid(unit_name: str) -> int:
    proc = _systemctl_user("show", unit_name, "-p", "MainPID", check=False)
    if proc.returncode != 0:
        raise HarnessGateError("systemctl show MainPID failed")
    for line in proc.stdout.splitlines():
        if line.startswith("MainPID="):
            return int(line.split("=", 1)[1])
    raise HarnessGateError("MainPID field missing")


def _cgroup_procs_for_unit(unit_name: str) -> list[int]:
    proc = _systemctl_user("show", unit_name, "-p", "ControlGroup", check=False)
    if proc.returncode != 0:
        raise HarnessGateError("systemctl show ControlGroup failed")
    cg = ""
    for line in proc.stdout.splitlines():
        if line.startswith("ControlGroup="):
            cg = line.split("=", 1)[1].strip()
            break
    if not cg:
        raise HarnessGateError("empty ControlGroup from systemctl show")
    candidates = [
        Path("/sys/fs/cgroup") / cg.lstrip("/") / "cgroup.procs",
        Path("/sys/fs/cgroup/systemd") / cg.lstrip("/") / "cgroup.procs",
    ]
    procs_path = next((p for p in candidates if p.is_file()), None)
    if procs_path is None:
        raise HarnessGateError("cgroup.procs not found for unit")
    pids: list[int] = []
    for line in procs_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            pids.append(int(line))
    return pids


def _process_start_identity(pid: int) -> tuple[int, int] | None:
    """Return ``(pid, starttime)`` from ``/proc`` or None if the process is gone."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    rparen = data.rfind(")")
    if rparen < 0:
        return None
    fields = data[rparen + 2 :].split()
    if len(fields) < 20:
        return None
    return (pid, int(fields[19]))


def _identity_alive(identity: tuple[int, int]) -> bool:
    current = _process_start_identity(identity[0])
    return current == identity


def _read_fd_capped(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = os.read(fd, min(65_536, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _read_crash_run_effective(run_dir: Path) -> dict[str, Any]:
    """Load crash-run ``effective.json`` as a regular, non-symlink, bounded object."""
    path = run_dir / "effective.json"
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError as exc:
        raise HarnessGateError("crash-run effective.json missing") from exc
    except OSError as exc:
        raise HarnessGateError("crash-run effective.json unreadable") from exc

    raw: bytes | None = None
    primary: BaseException | None = None
    try:
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise HarnessGateError("crash-run effective.json is not a regular file")
            if st.st_size > _MAX_EFFECTIVE_JSON_BYTES:
                raise HarnessGateError("crash-run effective.json exceeds size bound")
            raw = _read_fd_capped(fd, _MAX_EFFECTIVE_JSON_BYTES + 1)
            if len(raw) > _MAX_EFFECTIVE_JSON_BYTES:
                raise HarnessGateError("crash-run effective.json exceeds size bound")
        except HarnessGateError as exc:
            primary = exc
            raise
        except OSError as exc:
            primary = HarnessGateError("crash-run effective.json unreadable")
            raise primary from exc
    finally:
        try:
            os.close(fd)
        except OSError as exc:
            if primary is None:
                raise HarnessGateError("crash-run effective.json unreadable") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessGateError("crash-run effective.json is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HarnessGateError("crash-run effective.json must be a JSON object")
    return payload


def _parse_agent_harness_identity(effective: dict[str, Any]) -> tuple[int, int]:
    """Extract ``process_identity`` into harness ``(pid, starttime)`` form."""
    identity = effective.get("process_identity")
    if not isinstance(identity, dict):
        raise HarnessGateError("crash-run process_identity missing or malformed")

    pid = identity.get("pid")
    if type(pid) is not int or isinstance(pid, bool) or pid <= 1:
        raise HarnessGateError("crash-run process_identity pid invalid")

    process_start = identity.get("process_start")
    if (
        not isinstance(process_start, str)
        or not process_start
        or _DECIMAL_START_RE.fullmatch(process_start) is None
    ):
        raise HarnessGateError("crash-run process_identity process_start invalid")

    host = identity.get("host")
    if not isinstance(host, str) or not host:
        raise HarnessGateError("crash-run process_identity host invalid")

    boot_id = identity.get("boot_id")
    if boot_id is not None and not isinstance(boot_id, str):
        raise HarnessGateError("crash-run process_identity boot_id invalid")

    return (pid, int(process_start))


def _require_agent_identity_before_sigkill(
    *,
    run_dir: Path,
    main_pid: int,
    cgroup_pids: list[int],
) -> tuple[int, int]:
    """Prove crash-run AGENT identity from effective.json and unit cgroup membership.

    An unrelated second PID in ``cgroup.procs`` is never evidence of AGENT
    containment. The recorded identity must match live ``/proc`` identity, differ
    from MainPID, and have its PID in this unit's cgroup before SIGKILL.
    """
    effective = _read_crash_run_effective(run_dir)
    agent_ident = _parse_agent_harness_identity(effective)
    live = _process_start_identity(agent_ident[0])
    if live is None or live != agent_ident:
        raise HarnessGateError("crash-run agent identity does not match live /proc")
    if agent_ident[0] == main_pid:
        raise HarnessGateError("crash-run agent pid must differ from MainPID")
    if agent_ident[0] not in cgroup_pids:
        raise HarnessGateError(
            "crash-run agent pid is not a member of the unit cgroup"
        )
    return agent_ident


def _capture_original_cgroup_identities_for_sigkill(
    *,
    cgroup_pids: list[int],
    agent_ident: tuple[int, int],
) -> list[tuple[int, int]]:
    """Final pre-SIGKILL identity snapshot; exact AGENT identity must still be present.

    Captures every cgroup PID identity fail-closed. The exact ``agent_ident``
    returned by the earlier effective.json gate must appear byte/value-exact in
    this snapshot; missing, unreadable, or PID-reused AGENT identity refuses
    before pidfd SIGKILL. Unrelated identities never substitute.
    """
    original_idents: list[tuple[int, int]] = []
    for pid in cgroup_pids:
        ident = _process_start_identity(pid)
        if ident is None:
            raise HarnessGateError("failed to read process start identity")
        original_idents.append(ident)
    if agent_ident not in original_idents:
        raise HarnessGateError(
            "crash-run agent identity missing from final pre-SIGKILL snapshot"
        )
    return original_idents


def _final_pre_sigkill_cgroup_snapshot(
    *,
    unit_name: str,
    agent_ident: tuple[int, int],
) -> tuple[list[int], list[tuple[int, int]]]:
    """Refresh ``cgroup.procs`` immediately before identity capture and SIGKILL.

    Call order (mechanically auditable):
    1. ``_cgroup_procs_for_unit(unit_name)`` — fresh membership, never a stale
       list from the earlier pre-check.
    2. ``_capture_original_cgroup_identities_for_sigkill`` — exact AGENT must
       still be present; unrelated PIDs never substitute.
    Returns ``(fresh_pids, original_idents)`` for evidence and kill tracking.
    """
    fresh_pids = _cgroup_procs_for_unit(unit_name)
    original_idents = _capture_original_cgroup_identities_for_sigkill(
        cgroup_pids=fresh_pids,
        agent_ident=agent_ident,
    )
    return fresh_pids, original_idents


def _require_agent_identity_dead_after_crash(agent_ident: tuple[int, int]) -> None:
    """Require the exact pre-crash AGENT identity is dead (PID-reuse safe)."""
    if _identity_alive(agent_ident):
        raise HarnessGateError("crash-run agent identity still alive after SIGKILL")


def _require_linux_pidfd() -> None:
    """Fail closed unless Linux pidfd open/send APIs are available."""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise HarnessGateError("linux pidfd support required; refusing SIGKILL")


def _pidfd_sigkill_verified_main(
    *,
    unit_name: str,
    main_pid: int,
    main_ident: tuple[int, int],
    agent_ident: tuple[int, int],
) -> tuple[list[int], list[tuple[int, int]]]:
    """PID-reuse-safe SIGKILL of the captured MainPID via pidfd.

    Never uses raw ``os.kill``. Captures a pidfd for the initial MainPID, then
    immediately before the signal re-reads ``systemctl MainPID``, live start
    identity, and a fresh cgroup snapshot. Requires the exact same PID/start
    identity, MainPID membership, and exact AGENT identity in that snapshot.
    Closes the pidfd on every path. Any mismatch or pidfd failure becomes a
    sanitized ``HarnessGateError`` with no signal delivered to an unrelated
    process.
    """
    _require_linux_pidfd()
    pidfd: int | None = None
    try:
        try:
            pidfd = os.pidfd_open(main_pid)
        except OSError as exc:
            raise HarnessGateError("pidfd open failed") from exc

        # Immediate pre-signal re-validation (never trust the earlier snapshot).
        live_main = _main_pid(unit_name)
        if live_main != main_pid:
            raise HarnessGateError("MainPID changed before pidfd SIGKILL")
        live_ident = _process_start_identity(main_pid)
        if live_ident is None or live_ident != main_ident:
            raise HarnessGateError(
                "MainPID start identity changed before pidfd SIGKILL"
            )
        fresh_pids, original_idents = _final_pre_sigkill_cgroup_snapshot(
            unit_name=unit_name,
            agent_ident=agent_ident,
        )
        if main_pid not in fresh_pids:
            raise HarnessGateError(
                "MainPID absent from refreshed cgroup before pidfd SIGKILL"
            )
        if main_ident not in original_idents:
            raise HarnessGateError(
                "MainPID identity missing from refreshed cgroup snapshot"
            )
        if agent_ident not in original_idents:
            raise HarnessGateError(
                "crash-run agent identity missing from final pre-SIGKILL snapshot"
            )
        try:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        except OSError as exc:
            raise HarnessGateError("pidfd send_signal failed") from exc
        return fresh_pids, original_idents
    finally:
        if pidfd is not None:
            try:
                os.close(pidfd)
            except OSError:
                pass


def _s4_evidence_payload(
    *,
    unit_name: str,
    crashed_run_id: str,
    original_cgroup_pids: list[int],
    agent_identity_from_effective: bool,
    agent_pid_in_cgroup_before_crash: bool,
    agent_identity_dead_after_crash: bool,
    fresh_run_id: str,
    prompt_sent: int,
) -> dict[str, Any]:
    return {
        "unit_name": unit_name,
        "crashed_run_id": crashed_run_id,
        "original_cgroup_pids": original_cgroup_pids,
        "original_identities_gone": True,
        "had_descendant_beyond_mainpid": True,
        "agent_identity_from_effective": agent_identity_from_effective,
        "agent_pid_in_cgroup_before_crash": agent_pid_in_cgroup_before_crash,
        "agent_identity_dead_after_crash": agent_identity_dead_after_crash,
        "new_main_pid_observed": True,
        "reconciled_status": "unknown",
        "reconciled_detail_code": "RECONCILED_UNKNOWN",
        "retryable": False,
        "session_quarantined": True,
        "prompt_sent_events": prompt_sent,
        "dispatch_marker_preserved": True,
        "fresh_run_id": fresh_run_id,
        "fresh_status": "completed",
        "fresh_marker_matched": True,
        "fresh_effective_model": _REQUIRED_MODEL,
        "fresh_effective_effort": _REQUIRED_EFFORT,
        "workspace_empty": True,
        "primary_model": _REQUIRED_MODEL,
        "primary_effort": _REQUIRED_EFFORT,
    }


def _require_s4_evidence_success(payload: dict[str, Any]) -> None:
    if (
        payload.get("agent_identity_from_effective") is not True
        or payload.get("agent_pid_in_cgroup_before_crash") is not True
        or payload.get("agent_identity_dead_after_crash") is not True
    ):
        raise HarnessGateError(
            "refusing success: agent identity evidence gates are not all true"
        )


def _cleanup_created_unit(*, unit_name: str, unit_path: Path, created: bool) -> None:
    """Stop → unlink this run's unit → daemon-reload. Raise if anything remains."""
    if not created:
        return
    failures: list[str] = []
    try:
        proc = _systemctl_user("stop", unit_name, check=False)
        if proc.returncode != 0:
            failures.append("stop")
    except Exception:
        failures.append("stop")
    try:
        if unit_path.exists() or unit_path.is_symlink():
            unit_path.unlink()
    except OSError:
        failures.append("unlink")
    if unit_path.exists() or unit_path.is_symlink():
        failures.append("file-remains")
    try:
        proc = _systemctl_user("daemon-reload", check=False)
        if proc.returncode != 0:
            failures.append("daemon-reload")
    except Exception:
        failures.append("daemon-reload")
    if failures:
        # Sanitized only — never dump command output or mappings.
        raise HarnessGateError("cleanup failed: " + ",".join(failures))


def run_s4(inputs: dict[str, str]) -> int:
    """Mutating S4 path — only after A3 gates and input validation."""
    from agent_run_supervisor.arsd import client as arsd_client
    from agent_run_supervisor.native_acp import storage
    from agent_run_supervisor.session import STATE_QUARANTINED

    unit_name = inputs["unit_name"]
    socket = inputs["socket"]
    root = inputs["supervisor_root"]
    mapping = inputs["caller_mapping"]
    evidence_dir = Path(inputs["evidence_dir"])
    workspace = Path(inputs["workspace"])
    unit_path = _user_unit_path(unit_name)
    created_unit = False
    primary: BaseException | None = None
    crash_session = f"arsd-s4-crash-{int(time.time())}"
    fresh_session = f"arsd-s4-fresh-{int(time.time())}"

    try:
        unit_text = _render_unit(socket, root, mapping)
        unit_path.parent.mkdir(parents=True, exist_ok=True)

        def _mark_created() -> None:
            nonlocal created_unit
            created_unit = True

        _exclusive_create_unit_file(unit_path, unit_text, on_created=_mark_created)

        _systemctl_user("daemon-reload")
        _systemctl_user("start", unit_name)

        # Healthy typed server_info — not socket existence alone.
        deadline = time.monotonic() + 30
        healthy = False
        while time.monotonic() < deadline:
            try:
                with arsd_client.ArsdClient(socket) as probe:
                    info = probe.server_info(request_id="s4-preflight")
                if info.get("api_version") == 1:
                    healthy = True
                    break
            except arsd_client.ArsdClientError:
                time.sleep(0.1)
        if not healthy:
            raise HarnessGateError("healthy server_info not observed before submit")

        owner, namespace = mapping.split(":", 3)[2], mapping.split(":", 3)[3]
        request_id = f"s4-crash-{int(time.time())}"
        payload = {
            "request": {
                "owner": owner,
                "namespace": namespace,
                "profile_id": "opencode-1.18.4",
                "session_reuse": "reuse",
                "ars_session_id": crash_session,
                "expected_binding_hash": None,
                "input_refs": [
                    {"ref": "prompt:inline", "content_hash": "sha256:" + "0" * 64}
                ],
                "requested_model": _REQUIRED_MODEL,
                "requested_effort": _REQUIRED_EFFORT,
                "grant_ref": "grant:s4-crash",
                "grant_hash": "sha256:" + "1" * 64,
                "grant_role_hash": "sha256:" + "2" * 64,
                "grant_capabilities": ["read"],
                "mcp_snapshot_hashes": [],
                "credential_refs": ["kimi-for-coding", "deepseek"],
                "limits": {},
                "evidence_policy_hash": "sha256:" + "3" * 64,
                "recovery_policy_hash": "sha256:" + "4" * 64,
            },
            "prompt_text": (
                "Remain busy without finishing quickly so crash containment "
                "can be observed. Do not write files."
            ),
            "workspace_root": str(workspace),
            "cwd": None,
            "retry_of_run_id": None,
        }
        with arsd_client.ArsdClient(socket) as cli:
            accepted = cli.submit(request_id=request_id, payload=payload)
            run_id = accepted["run_id"]

        run_dir = Path(storage.native_event_store(Path(root)).base_dir) / run_id
        marker = run_dir / "prompt-dispatch-started"
        result_path = run_dir / "result.json"
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if marker.exists() and not result_path.exists():
                break
            time.sleep(0.1)
        if not marker.exists():
            raise HarnessGateError("prompt-dispatch-started never appeared")
        if result_path.exists():
            raise HarnessGateError("result present before SIGKILL; crash window missed")

        main_pid = _main_pid(unit_name)
        if main_pid <= 1:
            raise HarnessGateError("could not resolve MainPID for SIGKILL")
        main_ident = _process_start_identity(main_pid)
        if main_ident is None:
            raise HarnessGateError("could not resolve MainPID start identity")
        before_pids = _cgroup_procs_for_unit(unit_name)
        agent_ident = _require_agent_identity_before_sigkill(
            run_dir=run_dir,
            main_pid=main_pid,
            cgroup_pids=before_pids,
        )
        agent_identity_from_effective = True
        agent_pid_in_cgroup_before_crash = True
        # PID-reuse-safe kill: pidfd + immediate MainPID/start/cgroup re-check.
        # Never raw os.kill; never reuse the stale before_pids list above.
        original_cgroup_pids, original_idents = _pidfd_sigkill_verified_main(
            unit_name=unit_name,
            main_pid=main_pid,
            main_ident=main_ident,
            agent_ident=agent_ident,
        )

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not any(_identity_alive(ident) for ident in original_idents):
                break
            time.sleep(0.1)
        else:
            raise HarnessGateError(
                "original cgroup process identities still alive after SIGKILL"
            )
        _require_agent_identity_dead_after_crash(agent_ident)
        agent_identity_dead_after_crash = True

        deadline = time.monotonic() + 60
        new_main = 0
        while time.monotonic() < deadline:
            try:
                candidate = _main_pid(unit_name)
            except HarnessGateError:
                time.sleep(0.2)
                continue
            if candidate > 1:
                cand_ident = _process_start_identity(candidate)
                if cand_ident is not None and cand_ident not in original_idents:
                    try:
                        with arsd_client.ArsdClient(socket) as probe:
                            info = probe.server_info(request_id="s4-health")
                        if info.get("api_version") == 1:
                            new_main = candidate
                            break
                    except arsd_client.ArsdClientError:
                        pass
            time.sleep(0.2)
        if new_main <= 1:
            raise HarnessGateError("new MainPID/healthy server_info not observed")

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not result_path.exists():
            time.sleep(0.1)
        if not result_path.exists():
            raise HarnessGateError("reconciled result.json missing after restart")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("status") != "unknown":
            raise HarnessGateError("reconciled status is not unknown")
        if result.get("detail_code") != "RECONCILED_UNKNOWN":
            raise HarnessGateError("reconciled detail_code is not RECONCILED_UNKNOWN")
        if result.get("retryable") is not False:
            raise HarnessGateError("reconciled retryable is not false")

        store = storage.native_session_store(Path(root))
        try:
            record = store.open_session(crash_session)
        except Exception as exc:
            raise HarnessGateError("crash session missing after reconcile") from exc
        if record.state != STATE_QUARANTINED:
            raise HarnessGateError("crash session is not quarantined")

        events_path = run_dir / "events.jsonl"
        prompt_sent = 0
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                event = json.loads(line)
                if event.get("type") == "session_prompt_sent":
                    prompt_sent += 1
        if prompt_sent != 1:
            raise HarnessGateError("expected exactly one session_prompt_sent event")
        if not marker.exists():
            raise HarnessGateError("dispatch marker missing after reconcile")

        fresh_id = f"s4-fresh-{int(time.time())}"
        fresh_payload = {
            **payload,
            "request": {
                **payload["request"],
                "ars_session_id": fresh_session,
            },
            "prompt_text": (
                f"Reply with exactly {_FRESH_MARKER} and nothing else. "
                "Do not use any tools."
            ),
        }
        with arsd_client.ArsdClient(socket) as cli:
            fresh = cli.submit(request_id=fresh_id, payload=fresh_payload)
            fresh_run = fresh["run_id"]
            deadline = time.monotonic() + 900
            terminal = None
            while time.monotonic() < deadline:
                status = cli.run_status(fresh_run)
                if "result" in status:
                    terminal = status["result"]
                    break
                time.sleep(0.5)
        if terminal is None or terminal.get("status") != "completed":
            raise HarnessGateError("fresh success Run did not complete")
        if _FRESH_MARKER not in str(terminal.get("final_message", "")):
            raise HarnessGateError("fresh success marker text missing")
        fresh_dir = Path(storage.native_event_store(Path(root)).base_dir) / fresh_run
        effective = json.loads(
            (fresh_dir / "effective.json").read_text(encoding="utf-8")
        )
        if effective.get("effective_model") != _REQUIRED_MODEL:
            raise HarnessGateError("fresh success effective_model mismatch")
        if effective.get("effective_effort") != _REQUIRED_EFFORT:
            raise HarnessGateError("fresh success effective_effort mismatch")
        if sorted(entry.name for entry in workspace.iterdir()) != []:
            raise HarnessGateError("workspace not empty after fresh success")

        evidence = _s4_evidence_payload(
            unit_name=unit_name,
            crashed_run_id=run_id,
            original_cgroup_pids=original_cgroup_pids,
            agent_identity_from_effective=agent_identity_from_effective,
            agent_pid_in_cgroup_before_crash=agent_pid_in_cgroup_before_crash,
            agent_identity_dead_after_crash=agent_identity_dead_after_crash,
            fresh_run_id=fresh_run,
            prompt_sent=prompt_sent,
        )
        _require_s4_evidence_success(evidence)
        _write_evidence(
            evidence_dir,
            "s4-crash-containment",
            evidence,
        )
        print("S4 crash containment harness completed successfully")
    except BaseException as exc:
        primary = exc
    finally:
        cleanup_err: HarnessGateError | None = None
        try:
            _cleanup_created_unit(
                unit_name=unit_name, unit_path=unit_path, created=created_unit
            )
        except HarnessGateError as ce:
            cleanup_err = ce

    # Residual host mutation overrides a prior failure (higher-priority alert).
    if cleanup_err is not None:
        raise cleanup_err
    if primary is not None:
        raise primary
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        _require_a3(args)
        inputs = _require_operator_inputs(args)
    except HarnessGateError as err:
        _eprint(str(err))
        return 2

    if args.dry_validate:
        plan = {
            "mode": "dry-validate",
            "mutates_host": False,
            "unit_name": inputs["unit_name"],
            "socket": inputs["socket"],
            "supervisor_root": inputs["supervisor_root"],
            "evidence_dir": inputs["evidence_dir"],
            "workspace": inputs["workspace"],
            "caller_mapping_supplied": True,
            "unit_file_exists": _user_unit_path(inputs["unit_name"]).exists(),
            "next_step_if_authorized": (
                "re-run without --dry-validate under A3; harness cleans up "
                "the created user unit on success and failure"
            ),
        }
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    try:
        return run_s4(inputs)
    except HarnessGateError as err:
        _eprint(str(err))
        return 1
    except subprocess.CalledProcessError as err:
        _eprint(_sanitize_cmd_failure(err))
        return 1


if __name__ == "__main__":
    sys.exit(main())

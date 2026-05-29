"""Environment preflight probes for `doctor`.

Current probes only run `--version`; they never launch real AGENT processes
and never invoke `acpx exec`. Probes return structured dicts so the CLI can
serialize them deterministically without raising on a missing binary.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

NODE_MIN_VERSION = "22.12.0"
ACPX_EXPECTED_VERSION = "0.10.0"
_PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ProbeRun:
    """Outcome of a single `--version` probe invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


ProbeRunner = Callable[[list[str]], ProbeRun]


def _default_runner(argv: list[str]) -> ProbeRun:
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    return ProbeRun(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def probe_node(
    *,
    binary: str | None = None,
    runner: ProbeRunner | None = None,
) -> dict:
    resolved_binary = binary or "node"
    return _run_version_probe(
        binary=resolved_binary,
        runner=runner,
        version_parser=_parse_node_version,
        ok_predicate=lambda version: _meets_minimum(version, NODE_MIN_VERSION),
        base_payload={
            "binary": "node",
            "requirement_minimum": NODE_MIN_VERSION,
        },
        mismatch_detail=lambda version: (
            f"node {version} is below required minimum {NODE_MIN_VERSION}"
        ),
        parse_failed_detail="could not parse node --version output",
    )


def probe_acpx(
    *,
    binary: str | None = None,
    runner: ProbeRunner | None = None,
) -> dict:
    resolved_binary = binary or "acpx"
    return _run_version_probe(
        binary=resolved_binary,
        runner=runner,
        version_parser=_parse_acpx_version,
        ok_predicate=lambda version: version == ACPX_EXPECTED_VERSION,
        base_payload={
            "binary": resolved_binary,
            "expected_version": ACPX_EXPECTED_VERSION,
        },
        mismatch_detail=lambda version: (
            f"acpx {version} does not match expected {ACPX_EXPECTED_VERSION}"
        ),
        parse_failed_detail="could not parse acpx --version output",
    )


def _run_version_probe(
    *,
    binary: str,
    runner: ProbeRunner | None,
    version_parser: Callable[[str], str | None],
    ok_predicate: Callable[[str], bool],
    base_payload: dict,
    mismatch_detail: Callable[[str], str],
    parse_failed_detail: str,
) -> dict:
    payload = {
        **base_payload,
        "available": False,
        "version": None,
        "ok": False,
        "error_detail": None,
    }

    runner = runner or _default_runner
    argv = [binary, "--version"]

    try:
        outcome = runner(argv)
    except FileNotFoundError:
        payload["error_detail"] = f"binary not found on PATH: {binary}"
        return payload
    except subprocess.TimeoutExpired:
        payload["error_detail"] = f"version probe timed out for {binary}"
        return payload
    except OSError as exc:
        payload["error_detail"] = f"could not invoke {binary}: {exc.__class__.__name__}"
        return payload

    if outcome.returncode != 0:
        payload["error_detail"] = (
            f"{binary} --version exited with code {outcome.returncode}"
        )
        return payload

    payload["available"] = True
    version = version_parser(outcome.stdout)
    if version is None:
        payload["error_detail"] = parse_failed_detail
        return payload

    payload["version"] = version
    if not ok_predicate(version):
        payload["error_detail"] = mismatch_detail(version)
        return payload

    payload["ok"] = True
    return payload


def _parse_node_version(stdout: str) -> str | None:
    token = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
    if not token:
        return None
    if token.startswith("v") or token.startswith("V"):
        token = token[1:]
    if not token or not token[0].isdigit():
        return None
    parts = token.split(".")
    if len(parts) < 1:
        return None
    for part in parts[:3]:
        digits = part.split("-", 1)[0]
        if not digits.isdigit():
            return None
    return token


def _parse_acpx_version(stdout: str) -> str | None:
    token = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
    if not token:
        return None
    if token.lower().startswith("acpx"):
        token = token[4:].strip()
    if token.startswith("v"):
        token = token[1:]
    if not token or not token[0].isdigit():
        return None
    if not all(part.split("-", 1)[0].isdigit() for part in token.split(".")):
        return None
    return token


def _meets_minimum(version: str, minimum: str) -> bool:
    return _version_tuple(version) >= _version_tuple(minimum)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = version.split("-", 1)[0].split(".")
    out: list[int] = []
    for part in parts:
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def discover_node_binary() -> str | None:
    return shutil.which("node")


def discover_acpx_binary() -> str | None:
    return shutil.which("acpx")

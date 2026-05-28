from __future__ import annotations

import subprocess

import pytest

from agent_run_supervisor.preflight import (
    ProbeRun,
    probe_acpx,
    probe_node,
)


def _runner(returncode: int, stdout: str = "", stderr: str = ""):
    def _fake(argv):
        assert isinstance(argv, list)
        return ProbeRun(returncode=returncode, stdout=stdout, stderr=stderr)

    return _fake


def _raising_runner(exc: Exception):
    def _fake(argv):
        raise exc

    return _fake


def test_probe_node_reports_version_when_runner_succeeds() -> None:
    result = probe_node(runner=_runner(0, stdout="v22.13.0\n"))

    assert result["binary"] == "node"
    assert result["available"] is True
    assert result["version"] == "22.13.0"
    assert result["requirement_minimum"] == "22.12.0"
    assert result["ok"] is True
    assert result["error_detail"] is None


def test_probe_node_below_minimum_is_not_ok() -> None:
    result = probe_node(runner=_runner(0, stdout="v18.0.0\n"))

    assert result["available"] is True
    assert result["version"] == "18.0.0"
    assert result["ok"] is False
    assert result["error_detail"]


def test_probe_node_when_binary_missing_is_unavailable_not_raising() -> None:
    result = probe_node(runner=_raising_runner(FileNotFoundError("node")))

    assert result["available"] is False
    assert result["version"] is None
    assert result["ok"] is False
    assert "not found" in result["error_detail"].lower()


def test_probe_node_with_unparseable_output_marks_not_ok() -> None:
    result = probe_node(runner=_runner(0, stdout="garbage\n"))

    assert result["available"] is True
    assert result["version"] is None
    assert result["ok"] is False
    assert result["error_detail"]


def test_probe_node_nonzero_exit_is_unavailable() -> None:
    result = probe_node(runner=_runner(127, stdout="", stderr="not found"))

    assert result["available"] is False
    assert result["ok"] is False
    assert result["version"] is None


def test_probe_acpx_reports_version_when_runner_succeeds() -> None:
    result = probe_acpx(runner=_runner(0, stdout="0.10.0\n"))

    assert result["binary"] == "acpx"
    assert result["available"] is True
    assert result["version"] == "0.10.0"
    assert result["expected_version"] == "0.10.0"
    assert result["ok"] is True
    assert result["error_detail"] is None


def test_probe_acpx_missing_binary_is_unavailable_not_ok() -> None:
    result = probe_acpx(runner=_raising_runner(FileNotFoundError("acpx")))

    assert result["available"] is False
    assert result["ok"] is False
    assert result["version"] is None
    assert "not found" in result["error_detail"].lower()


def test_probe_acpx_version_mismatch_fails_ok() -> None:
    result = probe_acpx(runner=_runner(0, stdout="0.9.5\n"))

    assert result["available"] is True
    assert result["version"] == "0.9.5"
    assert result["expected_version"] == "0.10.0"
    assert result["ok"] is False
    assert result["error_detail"]


def test_probe_acpx_does_not_launch_acpx_exec() -> None:
    seen: list[list[str]] = []

    def _record(argv):
        seen.append(list(argv))
        return ProbeRun(returncode=0, stdout="0.10.0\n", stderr="")

    probe_acpx(runner=_record)

    assert seen, "runner not invoked"
    for argv in seen:
        assert "exec" not in argv
        assert argv[-1] == "--version"


def test_probe_acpx_honors_explicit_binary_override() -> None:
    seen: list[list[str]] = []

    def _record(argv):
        seen.append(list(argv))
        return ProbeRun(returncode=0, stdout="0.10.0\n", stderr="")

    probe_acpx(binary="/opt/acpx/bin/acpx", runner=_record)

    assert seen[0][0] == "/opt/acpx/bin/acpx"


def test_probe_node_timeout_is_unavailable_not_raising() -> None:
    result = probe_node(
        runner=_raising_runner(subprocess.TimeoutExpired(cmd="node", timeout=1.0)),
    )

    assert result["available"] is False
    assert result["ok"] is False
    assert result["version"] is None
    assert result["error_detail"]

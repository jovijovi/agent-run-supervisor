"""Slice 6a — shipped systemd user-unit renderer + --print-service-unit.

Hermetic only: no systemctl, no service install/enable, no real AGENT, no
credentials, no cgroup mutation. Print mode must exit before euid /
reconcile / bind / process creation. Harness tests monkeypatch subprocess.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_harness():
    path = REPO_ROOT / "scripts" / "arsd_crash_containment_harness.py"
    spec = importlib.util.spec_from_file_location("arsd_crash_containment_harness", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _required_lines(unit: str) -> None:
    assert "Restart=on-failure" in unit
    assert "KillMode=control-group" in unit
    timeout = re.search(r"^TimeoutStopSec=(\d+)\s*$", unit, re.M)
    assert timeout is not None
    assert 30 <= int(timeout.group(1)) <= 300
    restart = re.search(r"^RestartSec=(\d+)\s*$", unit, re.M)
    assert restart is not None
    assert 1 <= int(restart.group(1)) <= 60
    assert "ExecStart=" in unit
    assert "-m" in unit
    assert "agent_run_supervisor.arsd" in unit


def _forbid_root_system(unit: str) -> None:
    lowered = unit.lower()
    assert "user=root" not in lowered
    assert "uid=0" not in lowered
    assert "/etc/systemd/system" not in lowered
    assert "sudo" not in lowered
    assert "systemctl" not in lowered
    assert "loginctl" not in lowered
    assert not re.search(r"\bsk-[A-Za-z0-9_\-]{8,}\b", unit)
    assert "Bearer " not in unit
    assert "AKIA" not in unit


def _assert_no_expandable_caller_specifiers(unit: str) -> None:
    """Caller/data tokens must not leave expandable single-% systemd specifiers.

    Renderer-owned defaults may keep ``%t`` / ``%h``; everything else that looks
    like ``%X`` must be doubled (``%%X``).
    """
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    # Strip the intentional default forms when present.
    scrubbed = exec_line.replace("%t/", "\0t/").replace("%h/", "\0h/")
    # Any remaining %[A-Za-z] that is not preceded by % is an expandable leak.
    leaks = re.findall(r"(?<!%)%[A-Za-z]", scrubbed)
    assert leaks == [], f"expandable caller specifiers leaked: {leaks}"


def test_service_unit_module_importable_from_source() -> None:
    from agent_run_supervisor.arsd import service_unit

    assert callable(service_unit.render_service_unit)
    assert hasattr(service_unit, "DEFAULT_USER_SOCKET")
    assert hasattr(service_unit, "DEFAULT_USER_SUPERVISOR_ROOT")
    assert hasattr(service_unit, "DEFAULT_RESTART_SEC")


def test_print_service_unit_flag_in_help(capsys) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    with pytest.raises(SystemExit) as exc:
        arsd_main.build_arg_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--print-service-unit" in out


@pytest.mark.parametrize(
    ("socket_path", "supervisor_root"),
    [
        ("/tmp/arsd-test.sock", "/tmp/arsd-sv-root"),
        (
            "/tmp/arsd with spaces/arsd.sock",
            "/tmp/arsd root with spaces",
        ),
    ],
)
def test_render_contains_required_semantics(
    socket_path: str, supervisor_root: str
) -> None:
    from agent_run_supervisor.arsd.service_unit import render_service_unit

    mapping = "4242:hermes-test:hermes:hermes/slice6a-test"
    unit = render_service_unit(
        socket_path=socket_path,
        supervisor_root=supervisor_root,
        caller_mappings=(mapping,),
        python_executable=sys.executable,
    )
    _required_lines(unit)
    _forbid_root_system(unit)
    assert socket_path in unit
    assert supervisor_root in unit
    assert mapping in unit
    assert "bash" not in unit.lower()
    assert "/bin/sh" not in unit
    assert sys.executable in unit
    assert sum(1 for line in unit.splitlines() if line.startswith("ExecStart=")) == 1
    _assert_no_expandable_caller_specifiers(unit)


def test_render_defaults_preserve_user_scope_specifiers_without_mapping() -> None:
    from agent_run_supervisor.arsd.service_unit import (
        DEFAULT_USER_SOCKET,
        DEFAULT_USER_SUPERVISOR_ROOT,
        render_service_unit,
    )

    unit = render_service_unit(python_executable=sys.executable)
    _required_lines(unit)
    _forbid_root_system(unit)
    assert DEFAULT_USER_SOCKET.startswith("%t/")
    assert DEFAULT_USER_SUPERVISOR_ROOT.startswith("%h/")
    assert DEFAULT_USER_SOCKET in unit
    assert DEFAULT_USER_SUPERVISOR_ROOT in unit
    assert "--caller-mapping" not in unit
    assert "%t/" in unit
    assert "%h/" in unit
    assert "%%t" not in unit
    assert "%%h" not in unit


@pytest.mark.parametrize(
    ("socket_path", "supervisor_root", "mapping"),
    [
        ("/tmp/arsd-%n.sock", "/tmp/ok-root", "1000:p:o:n"),
        ("/tmp/ok.sock", "/tmp/root-%i", "1000:p:o:n"),
        ("/tmp/ok.sock", "/tmp/ok-root", "1000:p:o:ns-%s"),
        ("/tmp/%n/%i.sock", "/tmp/%s-root", "1000:p:o:n"),
    ],
)
def test_caller_supplied_percent_specifiers_are_escaped(
    socket_path: str, supervisor_root: str, mapping: str
) -> None:
    from agent_run_supervisor.arsd.service_unit import render_service_unit

    unit = render_service_unit(
        socket_path=socket_path,
        supervisor_root=supervisor_root,
        caller_mappings=(mapping,),
        python_executable=sys.executable,
    )
    _required_lines(unit)
    # Literal operator data survives as %%… — systemd will not expand.
    if "%n" in socket_path or "%n" in supervisor_root or "%n" in mapping:
        assert "%%n" in unit
    if "%i" in socket_path or "%i" in supervisor_root or "%i" in mapping:
        assert "%%i" in unit
    if "%s" in socket_path or "%s" in supervisor_root or "%s" in mapping:
        assert "%%s" in unit
    _assert_no_expandable_caller_specifiers(unit)
    # Defaults alone may keep %t/%h; this render used caller paths, so none.
    assert "%t/" not in unit
    assert "%h/" not in unit


def test_caller_supplied_default_looking_paths_are_still_escaped() -> None:
    """Even ``%t`` / ``%h`` from the caller are data, not renderer defaults."""
    from agent_run_supervisor.arsd.service_unit import render_service_unit

    unit = render_service_unit(
        socket_path="%t/evil.sock",
        supervisor_root="%h/evil-root",
        python_executable=sys.executable,
    )
    assert "%%t/evil.sock" in unit
    assert "%%h/evil-root" in unit
    assert "%t/evil.sock" not in unit.replace("%%t/evil.sock", "")
    _assert_no_expandable_caller_specifiers(unit)


@pytest.mark.parametrize(
    ("socket_path", "supervisor_root", "mapping"),
    [
        ("/tmp/arsd\nsock", "/tmp/ok-root", None),
        ("/tmp/ok.sock", "/tmp/arsd\rroot", None),
        ("/tmp/ok.sock", "/tmp/ok-root", "1000:p:o:n\nUser=root"),
        ("/tmp/ok.sock", "/tmp/ok-root", "1000:p:o:n\nExecStart=/bin/evil"),
        ("/tmp/ok.sock", "/tmp/ok-root", "1000:p:o:ns\x1b"),
        ("/tmp/ok\0sock", "/tmp/ok-root", None),
    ],
)
def test_render_rejects_control_and_directive_injection(
    socket_path: str, supervisor_root: str, mapping: str | None
) -> None:
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )

    mappings = () if mapping is None else (mapping,)
    with pytest.raises(ServiceUnitError):
        render_service_unit(
            socket_path=socket_path,
            supervisor_root=supervisor_root,
            caller_mappings=mappings,
            python_executable=sys.executable,
        )


def test_print_service_unit_zero_side_effects(monkeypatch, capsys) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    called = {"geteuid": 0, "reconcile": 0, "serve": 0}

    def boom_geteuid() -> int:
        called["geteuid"] += 1
        raise AssertionError("geteuid must not run in print-service-unit mode")

    def boom_reconcile(*_a, **_k):
        called["reconcile"] += 1
        raise AssertionError("reconcile must not run in print-service-unit mode")

    async def boom_serve(*_a, **_k):
        called["serve"] += 1
        raise AssertionError("serve_daemon must not run in print-service-unit mode")

    monkeypatch.setattr(arsd_main, "geteuid", boom_geteuid)
    monkeypatch.setattr(arsd_main.reconcile, "reconcile", boom_reconcile)
    monkeypatch.setattr(arsd_main, "serve_daemon", boom_serve)

    rc = arsd_main.main(["--print-service-unit"])
    assert rc == 0
    out = capsys.readouterr().out
    _required_lines(out)
    _forbid_root_system(out)
    assert "--caller-mapping" not in out
    assert "%t/" in out and "%h/" in out
    assert called == {"geteuid": 0, "reconcile": 0, "serve": 0}


def test_print_service_unit_with_explicit_args(monkeypatch, capsys) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    monkeypatch.setattr(
        arsd_main,
        "geteuid",
        lambda: (_ for _ in ()).throw(AssertionError("no euid")),
    )
    mapping = f"{os.getuid()}:hermes-local:hermes:hermes/slice6a-print"
    rc = arsd_main.main(
        [
            "--print-service-unit",
            "--socket",
            "/tmp/arsd-print.sock",
            "--supervisor-root",
            "/tmp/arsd-print-root",
            "--caller-mapping",
            mapping,
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    _required_lines(out)
    assert "/tmp/arsd-print.sock" in out
    assert "/tmp/arsd-print-root" in out
    assert mapping in out


def test_normal_daemon_mode_still_fail_closed_without_mapping(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    root = tmp_path / "sv"
    sock = tmp_path / "arsd.sock"
    monkeypatch.setattr(arsd_main, "geteuid", lambda: 1000)
    serve_calls = {"n": 0}

    async def boom_serve(*_a, **_k):
        serve_calls["n"] += 1
        raise AssertionError("must refuse before serve")

    monkeypatch.setattr(arsd_main, "serve_daemon", boom_serve)
    rc = arsd_main.main(
        [
            "--supervisor-root",
            str(root),
            "--socket",
            str(sock),
        ]
    )
    assert rc != 0
    assert serve_calls["n"] == 0
    assert not sock.exists()
    err = capsys.readouterr().err.lower()
    assert "mapping" in err or "caller" in err or "zero" in err


def test_normal_daemon_mode_requires_supervisor_root(monkeypatch, capsys) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    monkeypatch.setattr(arsd_main, "geteuid", lambda: 1000)
    serve_calls = {"n": 0}

    async def boom_serve(*_a, **_k):
        serve_calls["n"] += 1
        raise AssertionError("must refuse before serve")

    monkeypatch.setattr(arsd_main, "serve_daemon", boom_serve)
    with pytest.raises(SystemExit) as exc:
        arsd_main.main(
            ["--caller-mapping", f"{os.getuid()}:hermes-local:hermes:hermes/slice6a"]
        )
    assert exc.value.code == 2
    assert serve_calls["n"] == 0
    err = capsys.readouterr().err.lower()
    assert "supervisor-root" in err


# --- hermetic crash-harness gate / cleanup tests (no real systemctl) --------


def test_harness_help_never_invokes_systemctl(monkeypatch) -> None:
    harness = _load_harness()
    calls: list[tuple] = []

    def boom_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("subprocess must not run for --help")

    monkeypatch.setattr(harness.subprocess, "run", boom_run)
    with pytest.raises(SystemExit) as exc:
        harness.main(["--help"])
    assert exc.value.code == 0
    assert calls == []


def test_harness_cleanup_order_on_failure(monkeypatch, tmp_path: Path) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    unit_dir = tmp_path / "systemd-user"
    unit_dir.mkdir()
    unit_name = "arsd-slice6a-cleanup.service"
    unit_path = unit_dir / unit_name

    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)
    actions: list[str] = []
    reload_count = {"n": 0}

    def fake_systemctl(*cmd, check=True):
        actions.append("systemctl:" + " ".join(cmd))
        if cmd and cmd[0] == "daemon-reload":
            reload_count["n"] += 1
            if reload_count["n"] == 1:
                raise harness.HarnessGateError("injected failure after unit create")
        return SimpleNamespace(returncode=0, stdout="MainPID=0\n", stderr="")

    def fake_render(*_a, **_k):
        actions.append("render")
        return (
            "[Unit]\nDescription=x\n[Service]\n"
            "Restart=on-failure\nKillMode=control-group\nRestartSec=10\n"
        )

    monkeypatch.setattr(harness, "_systemctl_user", fake_systemctl)
    monkeypatch.setattr(harness, "_render_unit", fake_render)

    inputs = {
        "unit_name": unit_name,
        "socket": str(tmp_path / "arsd.sock"),
        "supervisor_root": str(tmp_path / "sv"),
        "caller_mapping": f"{os.getuid()}:p:o:n",
        "evidence_dir": str(evidence),
        "workspace": str(ws),
    }
    with pytest.raises(harness.HarnessGateError):
        harness.run_s4(inputs)

    assert unit_path.exists() is False
    assert any(a.startswith("systemctl:stop") for a in actions)
    assert sum(1 for a in actions if a.startswith("systemctl:daemon-reload")) >= 2
    stop_idx = next(i for i, a in enumerate(actions) if a.startswith("systemctl:stop"))
    reload_indices = [
        i for i, a in enumerate(actions) if a.startswith("systemctl:daemon-reload")
    ]
    assert stop_idx < reload_indices[-1]
    assert not any("enable" in a for a in actions)


def test_harness_rejects_bad_unit_names(tmp_path: Path) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    root = tmp_path / "sv-missing"
    sock = tmp_path / "arsd.sock"
    for bad in (
        "../escape.service",
        "foo/bar.service",
        "no-suffix",
        "bad\nname.service",
        "",
        "x.service.extra",
    ):
        args = SimpleNamespace(
            i_acknowledge_a3_crash_harness=True,
            unit_name=bad,
            socket=str(sock),
            supervisor_root=str(root),
            caller_mapping=f"{os.getuid()}:p:o:n",
            evidence_dir=str(evidence),
            workspace=str(ws),
            dry_validate=True,
        )
        with pytest.raises(harness.HarnessGateError):
            harness._require_operator_inputs(args)


def test_harness_rejects_evidence_inside_repo(tmp_path: Path) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    inside = REPO_ROOT / "docs"
    args = SimpleNamespace(
        i_acknowledge_a3_crash_harness=True,
        unit_name="arsd-slice6a-test.service",
        socket=str(tmp_path / "arsd.sock"),
        supervisor_root=str(tmp_path / "sv"),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(inside),
        workspace=str(ws),
        dry_validate=True,
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    assert "evidence" in str(err.value).lower() or "repo" in str(err.value).lower()


def test_harness_rejects_nonempty_workspace(tmp_path: Path) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "stale.txt").write_text("nope", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    args = SimpleNamespace(
        i_acknowledge_a3_crash_harness=True,
        unit_name="arsd-slice6a-test.service",
        socket=str(tmp_path / "arsd.sock"),
        supervisor_root=str(tmp_path / "sv"),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(evidence),
        workspace=str(ws),
        dry_validate=True,
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    assert "empty" in str(err.value).lower() or "workspace" in str(err.value).lower()


def test_harness_rejects_nonempty_supervisor_root(tmp_path: Path) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    root = tmp_path / "sv"
    root.mkdir()
    (root / "native-runs").mkdir()
    args = SimpleNamespace(
        i_acknowledge_a3_crash_harness=True,
        unit_name="arsd-slice6a-test.service",
        socket=str(tmp_path / "arsd.sock"),
        supervisor_root=str(root),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(evidence),
        workspace=str(ws),
        dry_validate=True,
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    assert "supervisor_root" in str(err.value).lower() or "empty" in str(err.value).lower()


def test_harness_rejects_preexisting_socket(tmp_path: Path) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    sock = tmp_path / "arsd.sock"
    sock.write_text("stale", encoding="utf-8")
    args = SimpleNamespace(
        i_acknowledge_a3_crash_harness=True,
        unit_name="arsd-slice6a-test.service",
        socket=str(sock),
        supervisor_root=str(tmp_path / "sv"),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(evidence),
        workspace=str(ws),
        dry_validate=True,
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    assert "socket" in str(err.value).lower()


def test_harness_rejects_path_overlap(tmp_path: Path) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    # Socket living directly inside workspace → refuse.
    sock = ws / "arsd.sock"
    args = SimpleNamespace(
        i_acknowledge_a3_crash_harness=True,
        unit_name="arsd-slice6a-test.service",
        socket=str(sock),
        supervisor_root=str(tmp_path / "sv"),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(evidence),
        workspace=str(ws),
        dry_validate=True,
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    assert "socket" in str(err.value).lower() or "overlap" in str(err.value).lower()


def test_harness_rejects_preexisting_unit_file(
    monkeypatch, tmp_path: Path
) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    unit_dir = tmp_path / "systemd-user"
    unit_dir.mkdir()
    unit_path = unit_dir / "arsd-slice6a-test.service"
    unit_path.write_text("[Unit]\nDescription=preexisting\n", encoding="utf-8")

    monkeypatch.setattr(
        harness,
        "_user_unit_path",
        lambda name: unit_dir / name,
    )
    calls: list[list[str]] = []

    def fake_systemctl(*cmd, check=True):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(harness, "_systemctl_user", fake_systemctl)
    monkeypatch.setattr(
        harness,
        "_render_unit",
        lambda *a, **k: (
            "[Unit]\nDescription=x\n[Service]\n"
            "Restart=on-failure\nKillMode=control-group\nRestartSec=10\n"
        ),
    )

    inputs = {
        "unit_name": "arsd-slice6a-test.service",
        "socket": str(tmp_path / "arsd.sock"),
        "supervisor_root": str(tmp_path / "sv"),
        "caller_mapping": f"{os.getuid()}:p:o:n",
        "evidence_dir": str(evidence),
        "workspace": str(ws),
    }
    with pytest.raises(harness.HarnessGateError) as err:
        harness.run_s4(inputs)
    assert "already exists" in str(err.value).lower()
    assert "preexisting" in unit_path.read_text(encoding="utf-8")
    assert calls == []


def test_harness_exclusive_create_refuses_dangling_symlink(tmp_path: Path) -> None:
    harness = _load_harness()
    unit_path = tmp_path / "dangling.service"
    unit_path.symlink_to(tmp_path / "missing-target")
    with pytest.raises(harness.HarnessGateError) as err:
        harness._exclusive_create_unit_file(unit_path, "[Unit]\nDescription=x\n")
    assert "already exists" in str(err.value).lower() or "exclusive" in str(err.value).lower()
    assert unit_path.is_symlink()
    assert "mapping" not in str(err.value).lower()
    assert "sk-" not in str(err.value).lower()


def test_harness_exclusive_create_loops_short_writes(
    monkeypatch, tmp_path: Path
) -> None:
    harness = _load_harness()
    unit_path = tmp_path / "short-write.service"
    content = "[Unit]\nDescription=short-write-loop\n# payload: αβγ\n"
    expected = content.encode("utf-8")
    real_write = os.write
    observed: list[int] = []

    def short_write(fd: int, data: bytes) -> int:
        # POSIX short-write: commit at most one byte per call.
        chunk = data[:1]
        n = real_write(fd, chunk)
        observed.append(n)
        return n

    monkeypatch.setattr(os, "write", short_write)
    harness._exclusive_create_unit_file(unit_path, content)
    assert unit_path.read_bytes() == expected
    assert len(observed) == len(expected)
    assert all(n == 1 for n in observed)


def test_harness_exclusive_create_zero_progress_is_sanitized_failure(
    monkeypatch, tmp_path: Path
) -> None:
    harness = _load_harness()
    unit_path = tmp_path / "zero-write.service"
    marked = {"created": False}
    closed = {"ok": False}
    real_close = os.close

    def zero_write(fd: int, data: bytes) -> int:
        return 0

    def tracking_close(fd: int) -> None:
        closed["ok"] = True
        real_close(fd)

    monkeypatch.setattr(os, "write", zero_write)
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(
        harness,
        "_systemctl_user",
        lambda *cmd, check=True: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._exclusive_create_unit_file(
            unit_path,
            "[Unit]\nDescription=zero\n",
            on_created=lambda: marked.__setitem__("created", True),
        )
    msg = str(err.value)
    assert "unit file write failed" in msg
    assert "mapping" not in msg
    assert marked["created"] is True
    assert closed["ok"] is True
    assert unit_path.exists()
    harness._cleanup_created_unit(
        unit_name="zero-write.service",
        unit_path=unit_path,
        created=True,
    )
    assert not unit_path.exists()


def test_harness_partial_write_still_marks_created_for_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    harness = _load_harness()
    unit_path = tmp_path / "partial.service"
    marked = {"created": False}
    closed = {"ok": False}
    real_close = os.close

    def boom_write(fd, data):
        raise OSError("injected write failure /tmp/secret-path mapping=leak")

    def tracking_close(fd: int) -> None:
        closed["ok"] = True
        real_close(fd)

    monkeypatch.setattr(os, "write", boom_write)
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(
        harness,
        "_systemctl_user",
        lambda *cmd, check=True: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._exclusive_create_unit_file(
            unit_path,
            "[Unit]\nDescription=x\n",
            on_created=lambda: marked.__setitem__("created", True),
        )
    msg = str(err.value)
    assert "unit file write failed" in msg
    assert "injected" not in msg
    assert "secret-path" not in msg
    assert "mapping" not in msg
    assert isinstance(err.value.__cause__, OSError)
    assert marked["created"] is True
    assert closed["ok"] is True
    assert unit_path.exists()
    harness._cleanup_created_unit(
        unit_name="partial.service",
        unit_path=unit_path,
        created=True,
    )
    assert not unit_path.exists()


def test_harness_standalone_close_failure_is_sanitized(
    monkeypatch, tmp_path: Path
) -> None:
    harness = _load_harness()
    unit_path = tmp_path / "close-fail.service"
    real_close = os.close
    closed_fds: list[int] = []

    def boom_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)
        raise OSError("close leaked /tmp/secret-path mapping=leak")

    monkeypatch.setattr(os, "close", boom_close)
    with pytest.raises(harness.HarnessGateError) as err:
        harness._exclusive_create_unit_file(unit_path, "[Unit]\nDescription=close\n")
    msg = str(err.value)
    assert "unit file write failed" in msg
    assert "leaked" not in msg
    assert "secret-path" not in msg
    assert "mapping" not in msg
    assert isinstance(err.value.__cause__, OSError)
    assert closed_fds
    assert unit_path.exists()


def test_harness_write_failure_primary_when_close_also_fails(
    monkeypatch, tmp_path: Path
) -> None:
    harness = _load_harness()
    unit_path = tmp_path / "write-and-close-fail.service"
    marked = {"created": False}
    real_close = os.close
    closed = {"ok": False}

    def boom_write(fd, data):
        raise OSError("write leaked /tmp/secret-path mapping=write-leak")

    def boom_close(fd: int) -> None:
        closed["ok"] = True
        real_close(fd)
        raise OSError("close leaked /tmp/other-path mapping=close-leak")

    monkeypatch.setattr(os, "write", boom_write)
    monkeypatch.setattr(os, "close", boom_close)
    monkeypatch.setattr(
        harness,
        "_systemctl_user",
        lambda *cmd, check=True: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._exclusive_create_unit_file(
            unit_path,
            "[Unit]\nDescription=x\n",
            on_created=lambda: marked.__setitem__("created", True),
        )
    msg = str(err.value)
    assert "unit file write failed" in msg
    assert "write leaked" not in msg
    assert "close leaked" not in msg
    assert "secret-path" not in msg
    assert "other-path" not in msg
    assert "mapping" not in msg
    assert "write-leak" not in msg
    assert "close-leak" not in msg
    assert isinstance(err.value.__cause__, OSError)
    assert "write leaked" in str(err.value.__cause__)
    assert marked["created"] is True
    assert closed["ok"] is True
    harness._cleanup_created_unit(
        unit_name="write-and-close-fail.service",
        unit_path=unit_path,
        created=True,
    )
    assert not unit_path.exists()


def test_harness_on_created_failure_still_closes_fd(
    monkeypatch, tmp_path: Path
) -> None:
    harness = _load_harness()
    unit_path = tmp_path / "callback-fail.service"
    real_close = os.close
    closed_fds: list[int] = []
    writes = {"n": 0}

    def tracking_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    def tracking_write(fd: int, data: bytes) -> int:
        writes["n"] += 1
        return len(data)

    def boom_created() -> None:
        raise RuntimeError("callback boom mapping=leak /tmp/secret-path")

    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(os, "write", tracking_write)
    with pytest.raises(RuntimeError, match="callback boom"):
        harness._exclusive_create_unit_file(
            unit_path,
            "[Unit]\nDescription=cb\n",
            on_created=boom_created,
        )
    assert closed_fds
    assert writes["n"] == 0
    assert unit_path.exists()


def test_harness_cleanup_failure_propagates_sanitized(monkeypatch, tmp_path: Path) -> None:
    harness = _load_harness()
    unit_path = tmp_path / "leftover.service"
    unit_path.write_text("[Unit]\n", encoding="utf-8")

    def fake_systemctl(*cmd, check=True):
        if cmd and cmd[0] == "stop":
            return SimpleNamespace(
                returncode=1,
                stdout="RAW_STDOUT=must-not-appear-in-error",
                stderr="RAW_STDERR=must-not-appear-either",
            )
        if cmd and cmd[0] == "daemon-reload":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(harness, "_systemctl_user", fake_systemctl)
    # Prevent unlink so file-remains trips.
    monkeypatch.setattr(Path, "unlink", lambda self: (_ for _ in ()).throw(OSError("no")))

    with pytest.raises(harness.HarnessGateError) as err:
        harness._cleanup_created_unit(
            unit_name="leftover.service",
            unit_path=unit_path,
            created=True,
        )
    msg = str(err.value)
    assert "cleanup failed" in msg
    assert "stop" in msg
    assert "RAW_STDOUT" not in msg
    assert "RAW_STDERR" not in msg
    assert "must-not-appear" not in msg
    assert "mapping" not in msg


def test_harness_dry_validate_no_mutation(monkeypatch, tmp_path: Path, capsys) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    calls: list = []

    def boom_run(*a, **k):
        calls.append(a)
        raise AssertionError("no subprocess in dry-validate")

    monkeypatch.setattr(harness.subprocess, "run", boom_run)
    monkeypatch.setenv("ARS_ARSD_A3_CRASH_HARNESS", "1")
    rc = harness.main(
        [
            "--i-acknowledge-a3-crash-harness",
            "--dry-validate",
            "--unit-name",
            "arsd-slice6a-dry.service",
            "--socket",
            str(tmp_path / "arsd-dry.sock"),
            "--supervisor-root",
            str(tmp_path / "arsd-dry-root"),
            "--caller-mapping",
            f"{os.getuid()}:hermes-test:hermes:hermes/dry",
            "--evidence-dir",
            str(evidence),
            "--workspace",
            str(ws),
        ]
    )
    assert rc == 0
    assert calls == []
    plan = json.loads(capsys.readouterr().out)
    assert plan["mutates_host"] is False


# --- A1 Codex-review repair R3: AGENT identity gates (hermetic) -------------


def _write_effective(
    run_dir: Path,
    *,
    pid: object,
    process_start: object,
    host: object = "testhost",
    boot_id: object = "boot-1",
) -> Path:
    path = run_dir / "effective.json"
    path.write_text(
        json.dumps(
            {
                "process_identity": {
                    "pid": pid,
                    "process_start": process_start,
                    "host": host,
                    "boot_id": boot_id,
                },
                "effective_model": "kimi-for-coding/k3",
                "effective_effort": "max",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_r3_old_harness_falsely_accepted_mainpid_plus_unrelated_helper(
    monkeypatch, tmp_path: Path
) -> None:
    """RED regression: [MainPID, unrelated helper] is not AGENT containment.

    The pre-R3 harness treated any cgroup descendant beyond MainPID as evidence.
    With AGENT PID outside the unit cgroup, that must now fail closed.
    """
    harness = _load_harness()
    run_dir = tmp_path / "native-runs" / "run-crash"
    run_dir.mkdir(parents=True)
    main_pid = 4100
    helper_pid = 4200
    agent_pid = 4300  # not in cgroup
    agent_start = "9001"
    _write_effective(run_dir, pid=agent_pid, process_start=agent_start)

    monkeypatch.setattr(
        harness,
        "_process_start_identity",
        lambda pid: {
            main_pid: (main_pid, 111),
            helper_pid: (helper_pid, 222),
            agent_pid: (agent_pid, int(agent_start)),
        }.get(pid),
    )

    # Document the false-positive criterion the old harness used.
    cgroup_pids = [main_pid, helper_pid]
    old_descendants = [pid for pid in cgroup_pids if pid != main_pid]
    assert old_descendants == [helper_pid]  # old harness would have accepted

    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_agent_identity_before_sigkill(
            run_dir=run_dir,
            main_pid=main_pid,
            cgroup_pids=cgroup_pids,
        )
    msg = str(err.value).lower()
    assert "agent" in msg or "cgroup" in msg or "identity" in msg
    assert "mapping" not in msg
    assert "sk-" not in msg


def test_r3_agent_identity_in_cgroup_accepted(monkeypatch, tmp_path: Path) -> None:
    harness = _load_harness()
    run_dir = tmp_path / "native-runs" / "run-ok"
    run_dir.mkdir(parents=True)
    main_pid = 5100
    agent_pid = 5200
    agent_start = "4242"
    _write_effective(run_dir, pid=agent_pid, process_start=agent_start, boot_id=None)
    monkeypatch.setattr(
        harness,
        "_process_start_identity",
        lambda pid: {
            main_pid: (main_pid, 11),
            agent_pid: (agent_pid, int(agent_start)),
        }.get(pid),
    )
    ident = harness._require_agent_identity_before_sigkill(
        run_dir=run_dir,
        main_pid=main_pid,
        cgroup_pids=[main_pid, agent_pid],
    )
    assert ident == (agent_pid, int(agent_start))


def test_r3_rejects_effective_pid_equal_mainpid(monkeypatch, tmp_path: Path) -> None:
    harness = _load_harness()
    run_dir = tmp_path / "native-runs" / "run-main"
    run_dir.mkdir(parents=True)
    main_pid = 6100
    _write_effective(run_dir, pid=main_pid, process_start="77")
    monkeypatch.setattr(
        harness,
        "_process_start_identity",
        lambda pid: (main_pid, 77) if pid == main_pid else None,
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_agent_identity_before_sigkill(
            run_dir=run_dir,
            main_pid=main_pid,
            cgroup_pids=[main_pid],
        )
    assert "mainpid" in str(err.value).lower() or "agent" in str(err.value).lower()


def test_r3_rejects_live_start_mismatch(monkeypatch, tmp_path: Path) -> None:
    harness = _load_harness()
    run_dir = tmp_path / "native-runs" / "run-mismatch"
    run_dir.mkdir(parents=True)
    main_pid = 7100
    agent_pid = 7200
    _write_effective(run_dir, pid=agent_pid, process_start="100")
    monkeypatch.setattr(
        harness,
        "_process_start_identity",
        lambda pid: (agent_pid, 999) if pid == agent_pid else (main_pid, 1),
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_agent_identity_before_sigkill(
            run_dir=run_dir,
            main_pid=main_pid,
            cgroup_pids=[main_pid, agent_pid],
        )
    assert "identity" in str(err.value).lower() or "start" in str(err.value).lower()


@pytest.mark.parametrize(
    "mutate",
    [
        "missing",
        "symlink",
        "oversized",
        "not_object",
        "pid_bool",
        "pid_one",
        "pid_str",
        "start_empty",
        "start_int",
        "host_empty",
        "boot_id_int",
        "identity_missing",
    ],
)
def test_r3_rejects_malformed_symlink_oversized_effective(
    mutate: str, tmp_path: Path
) -> None:
    harness = _load_harness()
    run_dir = tmp_path / "native-runs" / f"run-{mutate}"
    run_dir.mkdir(parents=True)
    path = run_dir / "effective.json"
    if mutate == "missing":
        pass
    elif mutate == "symlink":
        target = tmp_path / "elsewhere.json"
        target.write_text("{}", encoding="utf-8")
        path.symlink_to(target)
    elif mutate == "oversized":
        # Just over the harness bound.
        pad = "x" * (harness._MAX_EFFECTIVE_JSON_BYTES + 8)
        path.write_text(json.dumps({"process_identity": {"pad": pad}}), encoding="utf-8")
    elif mutate == "not_object":
        path.write_text("[1,2,3]", encoding="utf-8")
    elif mutate == "pid_bool":
        _write_effective(run_dir, pid=True, process_start="1")
    elif mutate == "pid_one":
        _write_effective(run_dir, pid=1, process_start="1")
    elif mutate == "pid_str":
        _write_effective(run_dir, pid="99", process_start="1")
    elif mutate == "start_empty":
        _write_effective(run_dir, pid=99, process_start="")
    elif mutate == "start_int":
        _write_effective(run_dir, pid=99, process_start=123)
    elif mutate == "host_empty":
        _write_effective(run_dir, pid=99, process_start="1", host="")
    elif mutate == "boot_id_int":
        _write_effective(run_dir, pid=99, process_start="1", boot_id=1)
    elif mutate == "identity_missing":
        path.write_text(json.dumps({"effective_model": "x"}), encoding="utf-8")
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_agent_identity_before_sigkill(
            run_dir=run_dir,
            main_pid=100,
            cgroup_pids=[100, 200],
        )
    msg = str(err.value).lower()
    assert "effective" in msg or "identity" in msg or "process" in msg
    assert "mapping" not in msg
    assert "sk-" not in msg


def test_r3_pid_reuse_after_crash_is_original_identity_dead(monkeypatch) -> None:
    harness = _load_harness()
    agent_ident = (8300, 555)
    # Same PID reused under a new starttime — original identity is dead.
    monkeypatch.setattr(
        harness, "_process_start_identity", lambda pid: (8300, 999) if pid == 8300 else None
    )
    assert harness._identity_alive(agent_ident) is False
    harness._require_agent_identity_dead_after_crash(agent_ident)


def test_r3_agent_still_alive_after_crash_rejected(monkeypatch) -> None:
    harness = _load_harness()
    agent_ident = (8400, 555)
    monkeypatch.setattr(
        harness, "_process_start_identity", lambda pid: (8400, 555) if pid == 8400 else None
    )
    with pytest.raises(harness.HarnessGateError):
        harness._require_agent_identity_dead_after_crash(agent_ident)


def test_r3_report_fields_cannot_be_true_without_gates() -> None:
    harness = _load_harness()
    report = harness._s4_evidence_payload(
        unit_name="arsd-r3.service",
        crashed_run_id="run-x",
        original_cgroup_pids=[1, 2],
        agent_identity_from_effective=False,
        agent_pid_in_cgroup_before_crash=False,
        agent_identity_dead_after_crash=False,
        fresh_run_id="run-y",
        prompt_sent=1,
    )
    assert report["agent_identity_from_effective"] is False
    assert report["agent_pid_in_cgroup_before_crash"] is False
    assert report["agent_identity_dead_after_crash"] is False
    with pytest.raises(harness.HarnessGateError):
        harness._require_s4_evidence_success(report)

    ok = harness._s4_evidence_payload(
        unit_name="arsd-r3.service",
        crashed_run_id="run-x",
        original_cgroup_pids=[1, 2],
        agent_identity_from_effective=True,
        agent_pid_in_cgroup_before_crash=True,
        agent_identity_dead_after_crash=True,
        fresh_run_id="run-y",
        prompt_sent=1,
    )
    harness._require_s4_evidence_success(ok)
    assert ok["agent_identity_from_effective"] is True
    assert ok["agent_pid_in_cgroup_before_crash"] is True
    assert ok["agent_identity_dead_after_crash"] is True


def _assert_no_host_mutation(monkeypatch, harness) -> None:
    """Snapshot helper tests must never kill, systemctl, or run the harness."""

    def boom_kill(*_a, **_k):
        raise AssertionError("os.kill must not run in snapshot helper tests")

    def boom_systemctl(*_a, **_k):
        raise AssertionError("systemctl must not run in snapshot helper tests")

    def boom_run_s4(*_a, **_k):
        raise AssertionError("run_s4 must not run in snapshot helper tests")

    def boom_main(*_a, **_k):
        raise AssertionError("harness.main must not run in snapshot helper tests")

    monkeypatch.setattr(harness.os, "kill", boom_kill)
    monkeypatch.setattr(harness, "_systemctl_user", boom_systemctl)
    monkeypatch.setattr(harness, "run_s4", boom_run_s4)
    monkeypatch.setattr(harness, "main", boom_main)


def test_r3_final_snapshot_keeps_exact_agent_identity(monkeypatch) -> None:
    """Unchanged AGENT identity must remain in the final pre-SIGKILL snapshot."""
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 9100
    agent_ident = (9200, 4242)
    helper_ident = (9300, 111)
    cgroup_pids = [main_pid, agent_ident[0], helper_ident[0]]
    live = {
        main_pid: (main_pid, 7),
        agent_ident[0]: agent_ident,
        helper_ident[0]: helper_ident,
    }
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))
    snap = harness._capture_original_cgroup_identities_for_sigkill(
        cgroup_pids=cgroup_pids,
        agent_ident=agent_ident,
    )
    assert agent_ident in snap
    assert snap == [live[main_pid], agent_ident, helper_ident]


def test_r3_final_snapshot_rejects_missing_agent_on_second_read(monkeypatch) -> None:
    """If AGENT exits between first check and snapshot, refuse before SIGKILL."""
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 9100
    agent_ident = (9200, 4242)
    helper_ident = (9300, 111)
    # Second read: AGENT PID gone from readable identities (natural exit).
    live = {
        main_pid: (main_pid, 7),
        helper_ident[0]: helper_ident,
        # agent pid unreadable / exited
    }
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))
    with pytest.raises(harness.HarnessGateError) as err:
        harness._capture_original_cgroup_identities_for_sigkill(
            cgroup_pids=[main_pid, agent_ident[0], helper_ident[0]],
            agent_ident=agent_ident,
        )
    msg = str(err.value).lower()
    assert "agent" in msg or "identity" in msg or "snapshot" in msg
    assert "mapping" not in msg
    assert "sk-" not in msg


def test_r3_final_snapshot_rejects_pid_reuse_different_starttime(monkeypatch) -> None:
    """Same PID with a new starttime is not the exact AGENT identity."""
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 9100
    agent_ident = (9200, 4242)
    reused = (9200, 9999)
    live = {
        main_pid: (main_pid, 7),
        9200: reused,
    }
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))
    with pytest.raises(harness.HarnessGateError) as err:
        harness._capture_original_cgroup_identities_for_sigkill(
            cgroup_pids=[main_pid, 9200],
            agent_ident=agent_ident,
        )
    msg = str(err.value).lower()
    assert "agent" in msg or "identity" in msg or "snapshot" in msg
    assert "mapping" not in msg


def test_r3_final_snapshot_unrelated_identities_do_not_substitute(monkeypatch) -> None:
    """Another descendant identity must not stand in for the exact AGENT tuple."""
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 9100
    agent_ident = (9200, 4242)
    other_ident = (9300, 4242)  # same starttime digits, different pid
    live = {
        main_pid: (main_pid, 7),
        other_ident[0]: other_ident,
        # agent pid absent from snapshot identities
    }
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))
    with pytest.raises(harness.HarnessGateError) as err:
        harness._capture_original_cgroup_identities_for_sigkill(
            cgroup_pids=[main_pid, other_ident[0]],
            agent_ident=agent_ident,
        )
    msg = str(err.value).lower()
    assert "agent" in msg or "identity" in msg or "snapshot" in msg
    assert "mapping" not in msg


def test_r4_b6_pre_kill_refreshes_cgroup_and_refuses_when_agent_omitted(
    monkeypatch,
) -> None:
    """Initial cgroup list includes AGENT; refreshed list omits it → refuse, no kill."""
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 9100
    agent_ident = (9200, 4242)
    helper_ident = (9300, 111)
    calls: list[list[int]] = []

    def fake_cgroup(_unit: str) -> list[int]:
        # First membership check includes AGENT; final refresh drops it.
        if not calls:
            pids = [main_pid, agent_ident[0], helper_ident[0]]
        else:
            pids = [main_pid, helper_ident[0]]
        calls.append(list(pids))
        return pids

    live = {
        main_pid: (main_pid, 7),
        agent_ident[0]: agent_ident,
        helper_ident[0]: helper_ident,
    }
    monkeypatch.setattr(harness, "_cgroup_procs_for_unit", fake_cgroup)
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))

    first = harness._cgroup_procs_for_unit("unit.service")
    assert agent_ident[0] in first
    with pytest.raises(harness.HarnessGateError) as err:
        harness._final_pre_sigkill_cgroup_snapshot(
            unit_name="unit.service",
            agent_ident=agent_ident,
        )
    assert len(calls) == 2
    assert agent_ident[0] not in calls[1]
    msg = str(err.value).lower()
    assert "agent" in msg or "identity" in msg or "snapshot" in msg
    assert "mapping" not in msg


def test_r4_b6_pre_kill_refreshes_cgroup_and_refuses_replaced_agent(
    monkeypatch,
) -> None:
    """Refreshed list replaces AGENT PID with an unrelated descendant → refuse."""
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 9100
    agent_ident = (9200, 4242)
    impostor = (9400, 7777)
    calls: list[list[int]] = []

    def fake_cgroup(_unit: str) -> list[int]:
        if not calls:
            pids = [main_pid, agent_ident[0]]
        else:
            pids = [main_pid, impostor[0]]
        calls.append(list(pids))
        return pids

    live = {
        main_pid: (main_pid, 7),
        agent_ident[0]: agent_ident,
        impostor[0]: impostor,
    }
    monkeypatch.setattr(harness, "_cgroup_procs_for_unit", fake_cgroup)
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))

    # Earlier membership check (stale) still saw the AGENT.
    first = harness._cgroup_procs_for_unit("unit.service")
    assert agent_ident[0] in first
    with pytest.raises(harness.HarnessGateError):
        harness._final_pre_sigkill_cgroup_snapshot(
            unit_name="unit.service",
            agent_ident=agent_ident,
        )
    assert len(calls) == 2
    assert impostor[0] in calls[-1]
    assert agent_ident[0] not in calls[-1]


def test_r4_b6_pre_kill_refresh_keeps_exact_agent_and_returns_fresh_pids(
    monkeypatch,
) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 9100
    agent_ident = (9200, 4242)
    helper_ident = (9300, 111)
    calls = 0

    def fake_cgroup(_unit: str) -> list[int]:
        nonlocal calls
        calls += 1
        return [main_pid, agent_ident[0], helper_ident[0]]

    live = {
        main_pid: (main_pid, 7),
        agent_ident[0]: agent_ident,
        helper_ident[0]: helper_ident,
    }
    monkeypatch.setattr(harness, "_cgroup_procs_for_unit", fake_cgroup)
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))
    fresh_pids, idents = harness._final_pre_sigkill_cgroup_snapshot(
        unit_name="unit.service",
        agent_ident=agent_ident,
    )
    assert calls == 1
    assert fresh_pids == [main_pid, agent_ident[0], helper_ident[0]]
    assert agent_ident in idents

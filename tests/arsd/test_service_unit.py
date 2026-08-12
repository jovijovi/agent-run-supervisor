"""Slice 6a — shipped systemd user-unit renderer + --print-service-unit.

Hermetic only: no systemctl, no service install/enable, no real AGENT, no
credentials, no cgroup mutation. Print mode must exit before euid /
reconcile / bind / process creation. Harness tests monkeypatch subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Operator-owned Runtime agents file (PRD R13). Deliberately synthetic and
# never created: print mode renders it as argv data and never accesses it.
AGENTS_FILE = "/etc/agent-run-supervisor/agents.toml"


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
    # A rendered production unit must never silently omit Binding config: a
    # daemon started without it refuses every registered profile at admission.
    assert "--agents-file" in unit
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
        agents_file=AGENTS_FILE,
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

    # Socket/supervisor-root keep renderer-owned %t/%h defaults; the Binding
    # root has no safe default and stays an explicit operator input.
    unit = render_service_unit(
        agents_file=AGENTS_FILE, python_executable=sys.executable
    )
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
        agents_file=AGENTS_FILE,
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
        agents_file=AGENTS_FILE,
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
    # A valid agents file is supplied so the refusal is provably about the
    # injected socket/root/mapping, not about the missing-root gate below.
    with pytest.raises(ServiceUnitError):
        render_service_unit(
            socket_path=socket_path,
            supervisor_root=supervisor_root,
            agents_file=AGENTS_FILE,
            caller_mappings=mappings,
            python_executable=sys.executable,
        )


# --- operator Runtime agents file in the rendered unit (PRD R13) ---------


def test_agents_file_flag_in_help(capsys) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    with pytest.raises(SystemExit) as exc:
        arsd_main.build_arg_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--agents-file" in out


def test_render_carries_operator_agents_file_as_argv_data() -> None:
    from agent_run_supervisor.arsd.service_unit import render_service_unit

    unit = render_service_unit(
        socket_path="/tmp/arsd-b.sock",
        supervisor_root="/tmp/arsd-b-root",
        agents_file=AGENTS_FILE,
        caller_mappings=("4242:hermes-test:hermes:hermes/r13",),
        python_executable=sys.executable,
    )
    _required_lines(unit)
    _forbid_root_system(unit)
    exec_line = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    assert "--agents-file" in exec_line
    assert AGENTS_FILE in exec_line
    assert exec_line.count("--agents-file") == 1
    _assert_no_expandable_caller_specifiers(unit)


@pytest.mark.parametrize(
    "agents_file",
    ["/srv/%n/binding", "/srv/binding-%i", "/srv/%s-binding", "/srv/%t/binding"],
)
def test_agents_file_percent_specifiers_are_escaped(agents_file: str) -> None:
    from agent_run_supervisor.arsd.service_unit import render_service_unit

    unit = render_service_unit(
        socket_path="/tmp/ok.sock",
        supervisor_root="/tmp/ok-root",
        agents_file=agents_file,
        python_executable=sys.executable,
    )
    assert agents_file.replace("%", "%%") in unit
    _assert_no_expandable_caller_specifiers(unit)


@pytest.mark.parametrize(
    "agents_file",
    [
        "/srv/binding\nExecStart=/bin/evil",
        "/srv/binding\nUser=root",
        "/srv/binding\r",
        "/srv/binding\x00",
        "/srv/binding\x1b",
    ],
)
def test_render_rejects_agents_file_control_injection(agents_file: str) -> None:
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )

    with pytest.raises(ServiceUnitError):
        render_service_unit(
            socket_path="/tmp/ok.sock",
            supervisor_root="/tmp/ok-root",
            agents_file=agents_file,
            python_executable=sys.executable,
        )


@pytest.mark.parametrize(
    "agents_file",
    [
        "",
        "   ",
        "relative/binding",
        "./binding",
        "~/binding",
        # No service-UID-owned default: a %h-rooted path is not an absolute
        # operator path and must not become the agents file by accident.
        "%h/.local/share/agent-run-supervisor/binding",
    ],
)
def test_render_refuses_non_absolute_agents_file(agents_file: str) -> None:
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )

    with pytest.raises(ServiceUnitError):
        render_service_unit(
            socket_path="/tmp/ok.sock",
            supervisor_root="/tmp/ok-root",
            agents_file=agents_file,
            python_executable=sys.executable,
        )


def test_render_refuses_to_omit_the_agents_file() -> None:
    """The **pure renderer** — not just print mode — must fail closed.

    ``--print-service-unit`` refuses first, but any other caller of the
    renderer would otherwise get a syntactically valid unit that installs a
    daemon refusing every registered profile. Omission is a refusal, never a
    silently shorter ExecStart.
    """
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )

    with pytest.raises(ServiceUnitError) as err:
        render_service_unit(
            socket_path="/tmp/ok.sock",
            supervisor_root="/tmp/ok-root",
            caller_mappings=("4242:hermes-test:hermes:hermes/r13",),
            python_executable=sys.executable,
        )
    assert "agents" in str(err.value).lower()


def test_render_refuses_explicit_none_agents_file() -> None:
    """The ``None`` default exists only to make the refusal catchable."""
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )

    with pytest.raises(ServiceUnitError) as err:
        render_service_unit(
            socket_path="/tmp/ok.sock",
            supervisor_root="/tmp/ok-root",
            agents_file=None,
            python_executable=sys.executable,
        )
    assert "agents" in str(err.value).lower()


def test_render_all_defaults_still_refuses_without_agents_file() -> None:
    """No ``%h``-rooted or service-UID-owned default may be invented."""
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )

    with pytest.raises(ServiceUnitError):
        render_service_unit(python_executable=sys.executable)


# --- inexact operands at the renderer boundary ------------------------------
#
# Every path-shaped token of ``render_service_unit`` is caller data that ends
# up inside a systemd unit, and the renderer used to *coerce* each one with
# ``str()`` before inspecting it. Coercion is not inspection: it runs the
# operand's own code, and it hands the guards back whatever that code chose.
#
# The shared hostile-operand factory below lives here rather than in
# ``test_client_daemon.py`` because both product-contract suites and
# ``test_operand.py`` need it, and this module is already the shared-helper
# home for this package's tests (``_record_fs_queries``). The dependency runs
# one way only — ``test_client_daemon`` imports from here, never the reverse —
# so promoting it in the other direction would be an import cycle.


# Derived here rather than imported from the daemon, so the test states the
# platform fact independently: this is the type ``Path(...)`` actually produces.
_EXACT_PATH_TYPE = type(Path(os.sep))

_LYING_AGENTS_FILE_KINDS = (
    "class_property_liar",
    "concrete_path_subclass",
    "metaclass_equality_liar",
    "pathlike_object",
    "str_subclass",
)


def _lying_agents_file(kind: str, real: str) -> tuple[object, list[str]]:
    """An operand whose advertised text is safe and whose real value is not.

    ``__str__`` shows the pure gates a disjoint operator root; ``__fspath__`` —
    what the kernel would be handed — keeps the daemon-owned one. Which of the
    two wins in ``Path(operand)`` is a CPython implementation detail that has
    changed across supported versions, which is the point: a value that answers
    differently to different readers cannot be validated by reading it.

    Each class is built per call so the probe log lives in this closure. No
    instance state is used, so nothing here depends on ``str``/``PurePath`` slot
    layout.
    """
    probes: list[str] = []
    advertised = AGENTS_FILE

    if kind == "str_subclass":

        class _LyingStr(str):
            def __str__(self) -> str:
                probes.append("__str__")
                return advertised

            def __fspath__(self) -> str:
                probes.append("__fspath__")
                return real

        return _LyingStr(real), probes

    if kind == "concrete_path_subclass":

        class _LyingPath(_EXACT_PATH_TYPE):  # type: ignore[misc,valid-type]
            def __str__(self) -> str:
                probes.append("__str__")
                return advertised

            def __fspath__(self) -> str:
                probes.append("__fspath__")
                return real

        return _LyingPath(real), probes

    if kind == "class_property_liar":
        # Not a subclass at all: when the concrete type does not match,
        # ``isinstance`` falls back to the operand's ``__class__`` attribute, and
        # a property can answer ``str``.
        class _ClassLiar:
            @property
            def __class__(self):  # type: ignore[override]
                return str

            def __str__(self) -> str:
                probes.append("__str__")
                return advertised

            def __fspath__(self) -> str:
                probes.append("__fspath__")
                return real

        return _ClassLiar(), probes

    if kind == "metaclass_equality_liar":
        # Guards the shape of the repair rather than the reported hole: a type
        # claiming equality with anything satisfies ``type(value) in (str,
        # Path)`` and fails ``type(value) is str``.
        class _EqualToAnything(type):
            def __eq__(cls, other: object) -> bool:
                return True

            def __hash__(cls) -> int:
                return hash(_EXACT_PATH_TYPE)

        class _MetaLiar(metaclass=_EqualToAnything):
            def __str__(self) -> str:
                probes.append("__str__")
                return advertised

            def __fspath__(self) -> str:
                probes.append("__fspath__")
                return real

        return _MetaLiar(), probes

    if kind == "pathlike_object":
        # A genuine ``os.PathLike`` (the ABC hooks on ``__fspath__``) offering
        # every other hook a gate might reach for: text, repr, truthiness,
        # equality, length, iteration.
        class _PathLike:
            def __str__(self) -> str:
                probes.append("__str__")
                return advertised

            def __repr__(self) -> str:
                probes.append("__repr__")
                return advertised

            def __fspath__(self) -> str:
                probes.append("__fspath__")
                return real

            def __bool__(self) -> bool:
                probes.append("__bool__")
                return True

            def __eq__(self, other: object) -> bool:
                probes.append("__eq__")
                return True

            def __hash__(self) -> int:
                probes.append("__hash__")
                return 0

            def __len__(self) -> int:
                probes.append("__len__")
                return len(advertised)

            def __iter__(self):
                probes.append("__iter__")
                return iter(advertised)

        return _PathLike(), probes

    raise AssertionError(f"unknown lying agents file kind: {kind}")


_INJECTED_DIRECTIVE = "ExecStartPre=/bin/evil"


class _InjectingStr(str):
    # Load-bearing, not decoration: PyObject_Str validates tp_str's result
    # with PyUnicode_Check, which admits subclasses, so str(x) is x. Without
    # this, str.__str__ hands back an exact-str copy of the real buffer at
    # service_unit.py:144 and both lies below are discarded before they fire.
    def __str__(self):
        return self

    def __iter__(self):
        return iter("/opt/b")          # safe prefix only

    def replace(self, *args, **kwargs):
        return self


def test_render_refuses_a_str_subclass_that_hides_a_directive() -> None:
    """RED-1 (F5/F1, highest severity): a lying ``str`` subclass injects a directive.

    One call, one thread: no concurrency, no timing, no filesystem, and no
    interpreter-conditional premise, so this test has no skip path and must
    never be given one. Every guard on the way through reads what the operand
    chose to show it — ``_reject_controls`` clears ``isinstance`` and sees only
    the safe prefix through the lying ``__iter__``; ``startswith`` reads the
    real buffer and is genuinely true; ``_escape_data_percent``'s ``replace``
    returns ``self`` and the safe-character scan is lied to again, so
    ``_systemd_quote`` returns the operand itself — and then ``" ".join`` copies
    its **raw buffer**, which no hook ever showed anyone. The ``user=root`` /
    ``sudo`` backstop matches neither token.

    The repair refuses at type identity, before ``__str__`` ever runs.
    """
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )

    hostile = _InjectingStr("/opt/b\n" + _INJECTED_DIRECTIVE)
    # Premise, asserted rather than assumed: identity survives the coercion,
    # which is what carries both lies past the guards.
    assert str(hostile) is hostile

    rendered: str | None = None
    refused = False
    try:
        rendered = render_service_unit(
            socket_path="/tmp/ok.sock",
            supervisor_root="/tmp/ok-root",
            agents_file=hostile,
            python_executable=sys.executable,
        )
    except ServiceUnitError:
        refused = True

    assert _INJECTED_DIRECTIVE not in (rendered or ""), (
        "the rendered unit carries an attacker-authored systemd directive"
    )
    assert refused, "the renderer returned a unit for an inexact agents_file"


# Absolute and control-free, so nothing but the missing type admission can
# produce a refusal on the cells where the operand bypasses ``str()`` today.
_INEXACT_OPERAND_REAL = "/tmp/ars-inexact-operand-real"

_RENDERER_OPERANDS = (
    "agents_file",
    "caller_mapping",
    "python_executable",
    "socket_path",
    "supervisor_root",
)


def _renderer_kwargs(operand: str, hostile: object) -> dict:
    """A fully valid render call with exactly one operand replaced."""
    kwargs: dict = {
        "socket_path": "/tmp/ok.sock",
        "supervisor_root": "/tmp/ok-root",
        "agents_file": AGENTS_FILE,
        "python_executable": sys.executable,
    }
    if operand == "caller_mapping":
        kwargs["caller_mappings"] = (hostile,)
    else:
        kwargs[operand] = hostile
    return kwargs


@pytest.mark.parametrize("operand", _RENDERER_OPERANDS)
@pytest.mark.parametrize("kind", _LYING_AGENTS_FILE_KINDS)
def test_render_refuses_every_inexact_operand_without_running_a_hook(
    monkeypatch, operand: str, kind: str
) -> None:
    """RED-2 (F5/F1): the whole token set, not only the Binding flag.

    A renderer that hardens ``agents_file`` while ``socket_path``,
    ``supervisor_root``, ``caller_mappings`` and ``python_executable`` remain
    injectable by the identical construct has hardened nothing: they are the
    same argv list, joined into the same artifact.
    """
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )

    hostile, probes = _lying_agents_file(kind, _INEXACT_OPERAND_REAL)
    kwargs = _renderer_kwargs(operand, hostile)

    rendered: str | None = None
    refused = False
    with _record_fs_queries(monkeypatch) as log:
        try:
            rendered = render_service_unit(**kwargs)
        except ServiceUnitError:
            refused = True

    assert refused, f"{operand} accepted an inexact {kind} operand"
    assert rendered is None
    # The type decided it: not one conversion hook ran, and nothing was queried.
    assert probes == []
    assert log.calls == []


@pytest.mark.parametrize("agents_file", ["/opt/x/", "/srv//x"])
def test_render_preserves_the_operator_agents_file_spelling(
    agents_file: str,
) -> None:
    """G-2 — lasting guard, **not** RED evidence: green before and after.

    The emitted ``ExecStart=`` is an operator-facing artifact that
    ``scripts/smoke_installed_artifact.sh`` compares literally, so the admission
    boundary must return frozen *text*. A ``Path`` round-trip would silently
    rewrite ``/opt/x/`` to ``/opt/x`` and ``/srv//x`` to ``/srv/x``; this guard
    is what makes a future ``-> Path`` return type fail loudly.
    """
    from agent_run_supervisor.arsd.service_unit import render_service_unit

    unit = render_service_unit(
        socket_path="/tmp/ok.sock",
        supervisor_root="/tmp/ok-root",
        agents_file=agents_file,
        python_executable=sys.executable,
    )
    exec_line = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    assert f"--agents-file {agents_file}" in exec_line


def test_print_service_unit_requires_agents_file(monkeypatch, capsys) -> None:
    """A rendered production unit must not silently omit Binding configuration."""
    from agent_run_supervisor.arsd import __main__ as arsd_main

    def boom_geteuid() -> int:
        raise AssertionError("geteuid must not run in print-service-unit mode")

    async def boom_serve(*_a, **_k):
        raise AssertionError("serve_daemon must not run in print-service-unit mode")

    monkeypatch.setattr(arsd_main, "geteuid", boom_geteuid)
    monkeypatch.setattr(arsd_main, "serve_daemon", boom_serve)

    rc = arsd_main.main(["--print-service-unit"])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "agents" in captured.err.lower()


def test_print_service_unit_renders_agents_file_without_touching_it(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    from agent_run_supervisor.arsd import __main__ as arsd_main

    missing = tmp_path / "never-created" / "binding-root"

    monkeypatch.setattr(
        arsd_main,
        "geteuid",
        lambda: (_ for _ in ()).throw(AssertionError("no euid")),
    )
    rc = arsd_main.main(
        ["--print-service-unit", "--agents-file", str(missing)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    _required_lines(out)
    _forbid_root_system(out)
    assert "--agents-file" in out
    assert str(missing) in out
    # Print mode is pure text: it never creates, repairs, or promotes a root.
    assert not missing.exists()
    assert not missing.parent.exists()


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

    rc = arsd_main.main(["--print-service-unit", "--agents-file", AGENTS_FILE])
    assert rc == 0
    out = capsys.readouterr().out
    _required_lines(out)
    _forbid_root_system(out)
    assert "--caller-mapping" not in out
    assert "%t/" in out and "%h/" in out
    assert AGENTS_FILE in out
    assert called == {"geteuid": 0, "reconcile": 0, "serve": 0}
    assert not Path(AGENTS_FILE).exists()


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
            "--agents-file",
            AGENTS_FILE,
            "--caller-mapping",
            mapping,
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    _required_lines(out)
    assert "/tmp/arsd-print.sock" in out
    assert "/tmp/arsd-print-root" in out
    assert AGENTS_FILE in out
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


# A agents file that shares no path component with ``tmp_path``, ``$HOME``, or
# the worktree, so every recorded query against it — or against ``/opt`` — is
# unambiguously a Binding-root read. It is deliberately never created: nothing
# in ARS may create, repair, or promote operator storage.
_UNQUERIED_AGENTS_FILE = "/opt/ars-operator-binding-root-never-queried"


def _harness_agents_file(tmp_path: Path) -> str:
    """Operator-owned agents file fixture for the crash harness.

    Reusable pre-existing operator storage — never a harness-owned surface, and
    deliberately **not** created here so tests can prove the harness never
    creates it.

    It used to be a *sibling* of the harness surfaces under ``tmp_path``, which
    the containment-only gate accepted. That fixture was the bug's shape: every
    resolve/stat of a sibling surface walks the parent they share, which is a
    component of the agents file. A valid operator layout separates the two at
    the top level instead, so the fixture ignores ``tmp_path`` and models that.
    """
    return _UNQUERIED_AGENTS_FILE


def _harness_socket(tmp_path: Path, name: str = "arsd.sock") -> str:
    """Socket inside its own directory, disjoint from the agents file.

    The socket's parent is a writable surface: the daemon creates it before it
    binds, and both the daemon and this harness refuse a agents file that
    shares any component with it. A valid operator layout therefore cannot park
    the socket beside Binding storage — the fixture models that instead of
    weakening the production check.
    """
    return str(tmp_path / "sock" / name)


def test_operator_agents_file_fixture_is_genuinely_disjoint(tmp_path: Path) -> None:
    """Guard the fixture itself: a shared component would mask every gate below.

    If the checkout, ``$HOME``, or the pytest temp root ever moved under the same
    top-level directory as this synthetic root, the harness and daemon tests
    would start refusing their own valid-layout fixture and fail obscurely. Fail
    loudly and locally instead.
    """
    from agent_run_supervisor.arsd import __main__ as arsd_main

    for surface in (tmp_path, REPO_ROOT, Path.home(), Path(tempfile.gettempdir())):
        assert not arsd_main._agents_file_query_conflict(
            _UNQUERIED_AGENTS_FILE, surface
        ), surface


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
        "socket": _harness_socket(tmp_path),
        "supervisor_root": str(tmp_path / "sv"),
        "caller_mapping": f"{os.getuid()}:p:o:n",
        "evidence_dir": str(evidence),
        "workspace": str(ws),
        "agents_file": _harness_agents_file(tmp_path),
        "agent_id": "s4-agent",
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
    sock = Path(_harness_socket(tmp_path))
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
            agents_file=_harness_agents_file(tmp_path),
            agent_id="s4-agent",
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
        socket=_harness_socket(tmp_path),
        supervisor_root=str(tmp_path / "sv"),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(inside),
        workspace=str(ws),
        agents_file=_harness_agents_file(tmp_path),
        agent_id="s4-agent",
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
        socket=_harness_socket(tmp_path),
        supervisor_root=str(tmp_path / "sv"),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(evidence),
        workspace=str(ws),
        agents_file=_harness_agents_file(tmp_path),
        agent_id="s4-agent",
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
        socket=_harness_socket(tmp_path),
        supervisor_root=str(root),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(evidence),
        workspace=str(ws),
        agents_file=_harness_agents_file(tmp_path),
        agent_id="s4-agent",
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
    sock = Path(_harness_socket(tmp_path))
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_text("stale", encoding="utf-8")
    args = SimpleNamespace(
        i_acknowledge_a3_crash_harness=True,
        unit_name="arsd-slice6a-test.service",
        socket=str(sock),
        supervisor_root=str(tmp_path / "sv"),
        caller_mapping=f"{os.getuid()}:p:o:n",
        evidence_dir=str(evidence),
        workspace=str(ws),
        agents_file=_harness_agents_file(tmp_path),
        agent_id="s4-agent",
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
        agents_file=_harness_agents_file(tmp_path),
        agent_id="s4-agent",
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
        "socket": _harness_socket(tmp_path),
        "supervisor_root": str(tmp_path / "sv"),
        "caller_mapping": f"{os.getuid()}:p:o:n",
        "evidence_dir": str(evidence),
        "workspace": str(ws),
        "agents_file": _harness_agents_file(tmp_path),
        "agent_id": "s4-agent",
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
            _harness_socket(tmp_path, "arsd-dry.sock"),
            "--supervisor-root",
            str(tmp_path / "arsd-dry-root"),
            "--caller-mapping",
            f"{os.getuid()}:hermes-test:hermes:hermes/dry",
            "--evidence-dir",
            str(evidence),
            "--workspace",
            str(ws),
            "--agents-file",
            _harness_agents_file(tmp_path),
            "--agent-id",
            "s4-agent",
        ]
    )
    assert rc == 0
    assert calls == []
    plan = json.loads(capsys.readouterr().out)
    assert plan["mutates_host"] is False


# --- crash-harness operator Runtime agents file (PRD R13) ------------------
#
# The harness submits ``opencode-native-acp``, so the agents file is not
# optional: the shipped ``--print-service-unit`` refuses without it, which made
# ``run_s4`` die with CalledProcessError before rendering or installing
# anything. The root is reusable pre-existing operator storage: the harness
# validates operator-input shape and non-overlap only, and never creates,
# empties, writes, promotes, or mutates it. Binding/artifact trust validation
# stays with BindingReader.


_STUB_RENDERED_UNIT = (
    "[Unit]\nDescription=agent-run-supervisor arsd (user)\n\n"
    "[Service]\nType=simple\n"
    "ExecStart=/usr/bin/python3 -m agent_run_supervisor.arsd "
    "--agents-file /srv/ars-binding\n"
    "Restart=on-failure\nRestartSec=10\nKillMode=control-group\n"
    "TimeoutStopSec=120\n"
)


def _harness_args(tmp_path: Path, **overrides) -> SimpleNamespace:
    """Valid operator inputs for ``_require_operator_inputs``, minus overrides."""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "i_acknowledge_a3_crash_harness": True,
        "unit_name": "arsd-r13-test.service",
        "socket": _harness_socket(tmp_path),
        "supervisor_root": str(tmp_path / "sv"),
        "caller_mapping": f"{os.getuid()}:p:o:n",
        "evidence_dir": str(evidence),
        "workspace": str(ws),
        "agents_file": _harness_agents_file(tmp_path),
        "agent_id": "s4-agent",
        "dry_validate": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# --- S4 submit request follows the Stage 3 wire ------------------------------
#
# The harness constructs one submit payload. After the reset that payload must
# name a registered ``agent_id`` and must parse under the v2 wire; the retired
# ``profile_id`` selector no longer exists and has no alias. None of this runs
# S4: the request is built and parsed, and nothing is sent, started, or killed.


def test_harness_requires_an_operator_supplied_agent_id(tmp_path: Path) -> None:
    harness = _load_harness()
    for blank in ("", "   "):
        args = _harness_args(tmp_path, agent_id=blank)
        with pytest.raises(harness.HarnessGateError) as err:
            harness._require_operator_inputs(args)
        assert "agent_id" in str(err.value).lower()


@pytest.mark.parametrize(
    "agent_id", ["Upper", "-leading", "with space", "a" * 65, "a/b", "opencode-1.18.4/"]
)
def test_harness_refuses_an_agent_id_outside_the_registry_grammar(
    tmp_path: Path, agent_id: str
) -> None:
    """One grammar. The harness selects an entry; it never invents an id shape."""
    harness = _load_harness()
    args = _harness_args(tmp_path, agent_id=agent_id)
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    assert "agent_id" in str(err.value).lower()


def test_harness_accepts_a_registry_shaped_agent_id(tmp_path: Path) -> None:
    harness = _load_harness()
    resolved = harness._require_operator_inputs(_harness_args(tmp_path))
    assert resolved["agent_id"] == "s4-agent"


def test_harness_submit_request_parses_under_the_v2_wire(tmp_path: Path) -> None:
    """The constructed request is fed to the production parser, not eyeballed."""
    from agent_run_supervisor.arsd import protocol

    harness = _load_harness()
    payload = harness._s4_submit_payload(
        owner="hermes",
        namespace="hermes/s4",
        agent_id="s4-agent",
        session_id="arsd-s4-crash-1",
        workspace=str(tmp_path / "ws"),
    )
    command = protocol.parse_submit(payload)
    assert command.request.agent_id == "s4-agent"
    assert command.request.schema_version == 3
    assert not hasattr(command.request, "profile_id")


def test_harness_submit_request_names_no_retired_profile(tmp_path: Path) -> None:
    harness = _load_harness()
    payload = harness._s4_submit_payload(
        owner="hermes",
        namespace="hermes/s4",
        agent_id="s4-agent",
        session_id="arsd-s4-crash-1",
        workspace=str(tmp_path / "ws"),
    )
    rendered = json.dumps(payload, sort_keys=True)
    for retired in ("profile_id", "opencode-native-acp", "opencode-1.18.4"):
        assert retired not in rendered


def test_harness_source_names_no_retired_profile_or_v1_wait() -> None:
    """Both health checks and every comment moved with the contract."""
    text = (REPO_ROOT / "scripts" / "arsd_crash_containment_harness.py").read_text(
        encoding="utf-8"
    )
    for retired in ("opencode-native-acp", "opencode-1.18.4", '"profile_id"'):
        assert retired not in text
    assert 'api_version") == 1' not in text
    # And it waits on the version the daemon actually reports.
    assert text.count("_REQUIRED_API_VERSION") >= 3


def test_harness_required_api_version_is_the_production_constant() -> None:
    from agent_run_supervisor.arsd import protocol

    harness = _load_harness()
    assert harness._REQUIRED_API_VERSION == protocol.ARSD_API_VERSION == 3


def _tree_snapshot(root: Path) -> list[tuple[str, str]]:
    return sorted(
        (
            str(path.relative_to(root)),
            path.read_text(encoding="utf-8") if path.is_file() else "<dir>",
        )
        for path in root.rglob("*")
    )


def test_harness_help_lists_agents_file(capsys) -> None:
    harness = _load_harness()
    with pytest.raises(SystemExit) as exc:
        harness.main(["--help"])
    assert exc.value.code == 0
    assert "--agents-file" in capsys.readouterr().out


@pytest.mark.parametrize("blank", ["", "   "])
def test_harness_requires_operator_agents_file(blank: str, tmp_path: Path) -> None:
    """Missing agents file is a fail-closed operator-input error, not a render.

    Blank/whitespace input joins the same "missing operator-supplied inputs"
    bucket as every other required path.
    """
    harness = _load_harness()
    args = _harness_args(tmp_path, agents_file=blank)
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "missing" in msg


@pytest.mark.parametrize(
    "bad",
    [
        "relative/binding",
        "./binding",
        "~/binding",
        "srv/binding",
        "/srv/binding\nExecStart=/bin/evil",
        "/srv/binding\r",
        "/srv/binding\x00",
        "/srv/binding\x1b",
    ],
)
def test_harness_rejects_unsafe_agents_file(bad: str, tmp_path: Path) -> None:
    harness = _load_harness()
    args = _harness_args(tmp_path, agents_file=bad)
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "absolute" in msg or "control" in msg


@pytest.mark.parametrize(
    "case",
    [
        "same_as_workspace",
        "parent_of_supervisor_root",
        "inside_evidence_dir",
        "contains_socket",
    ],
)
def test_harness_rejects_agents_file_overlap(case: str, tmp_path: Path) -> None:
    """Nothing the harness creates, empties, or writes may alias operator storage."""
    harness = _load_harness()
    overrides: dict[str, object] = {}
    if case == "same_as_workspace":
        overrides["agents_file"] = str(tmp_path / "ws")
    elif case == "parent_of_supervisor_root":
        overrides["agents_file"] = str(tmp_path / "outer")
        overrides["supervisor_root"] = str(tmp_path / "outer" / "sv")
    elif case == "inside_evidence_dir":
        overrides["agents_file"] = str(tmp_path / "evidence" / "binding")
    elif case == "contains_socket":
        binding = tmp_path / "operator-binding-root"
        overrides["agents_file"] = str(binding)
        overrides["socket"] = str(binding / "arsd.sock")
    args = _harness_args(tmp_path, **overrides)
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)
    msg = str(err.value).lower()
    assert "overlap" in msg or "inside" in msg
    assert "agents_file" in msg or "agent registry" in msg


def test_harness_never_reads_or_mutates_a_prepopulated_agents_file(
    monkeypatch, tmp_path: Path
) -> None:
    """The agents file is the operator's, not fresh test state.

    A sibling layout is admitted now, so this drives the property directly: an
    already-populated file reaches the harness gate, is neither read nor written
    on the way through, and comes out byte-identical. No emptiness or freshness
    gate may ever reach the operator's own configuration.
    """
    harness = _load_harness()
    conf = tmp_path / "conf"
    conf.mkdir()
    agents = conf / "agents.toml"
    agents.write_text("schema_version = 1\n", encoding="utf-8")
    before = _tree_snapshot(conf)

    with _record_fs_queries(monkeypatch) as log:
        resolved = harness._require_operator_inputs(
            _harness_args(tmp_path, agents_file=str(agents))
        )

    assert resolved["agents_file"] == str(agents)
    assert [call for call in log.calls if str(agents) in str(call)] == []
    assert _tree_snapshot(conf) == before


def test_harness_never_creates_a_missing_agents_file(tmp_path: Path) -> None:
    """Existence/trust checks belong to BindingReader, not to this harness."""
    harness = _load_harness()
    binding = Path(_harness_agents_file(tmp_path))
    resolved = harness._require_operator_inputs(_harness_args(tmp_path))
    assert resolved["agents_file"] == str(binding)
    assert not binding.exists()


def test_harness_render_unit_passes_agents_file_to_the_cli(monkeypatch) -> None:
    """Regression: the harness rendered with no ``--agents-file`` at all."""
    harness = _load_harness()
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        return SimpleNamespace(returncode=0, stdout=_STUB_RENDERED_UNIT, stderr="")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    harness._render_unit(
        "/tmp/s4.sock",
        "/tmp/s4-root",
        f"{os.getuid()}:p:o:n",
        agents_file=AGENTS_FILE,
    )
    cmd = seen["cmd"]
    assert "--print-service-unit" in cmd
    assert cmd.count("--agents-file") == 1
    assert cmd[cmd.index("--agents-file") + 1] == AGENTS_FILE
    # argv data only — never shell, never an env/default substitute.
    assert all(isinstance(token, str) for token in cmd)


def test_harness_render_unit_refuses_a_unit_that_dropped_the_agents_file(
    monkeypatch,
) -> None:
    """Defense in depth: propagation is verified in the rendered artifact."""
    harness = _load_harness()
    stripped = _STUB_RENDERED_UNIT.replace(
        "--agents-file /srv/ars-binding", ""
    )

    monkeypatch.setattr(
        harness.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(
            returncode=0, stdout=stripped, stderr=""
        ),
    )
    with pytest.raises(harness.HarnessGateError) as err:
        harness._render_unit(
            "/tmp/s4.sock",
            "/tmp/s4-root",
            f"{os.getuid()}:p:o:n",
            agents_file=AGENTS_FILE,
        )
    assert "agents" in str(err.value).lower()


def test_harness_rendered_argv_is_accepted_by_the_shipped_cli(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """The exact harness argv must render — not exit 2 / CalledProcessError.

    Hermetic: the harness argv is captured, then replayed in-process through
    the real ``arsd`` entrypoint, so this holds against the source under test
    rather than an installed wheel.
    """
    harness = _load_harness()
    from agent_run_supervisor.arsd import __main__ as arsd_main

    binding = tmp_path / "operator-binding-root"
    seen: dict[str, list[str]] = {}

    def capture(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return SimpleNamespace(returncode=0, stdout=_STUB_RENDERED_UNIT, stderr="")

    monkeypatch.setattr(harness.subprocess, "run", capture)
    harness._render_unit(
        "/tmp/s4.sock",
        "/tmp/s4-root",
        f"{os.getuid()}:hermes-test:hermes:hermes/s4",
        agents_file=str(binding),
    )
    cmd = seen["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "agent_run_supervisor.arsd"]

    def boom_geteuid() -> int:
        raise AssertionError("geteuid must not run in print-service-unit mode")

    monkeypatch.setattr(arsd_main, "geteuid", boom_geteuid)
    rc = arsd_main.main(cmd[3:])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    _required_lines(captured.out)
    _forbid_root_system(captured.out)
    assert str(binding) in captured.out
    assert not binding.exists()


def test_harness_run_s4_renders_with_the_operator_agents_file(
    monkeypatch, tmp_path: Path
) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    unit_dir = tmp_path / "systemd-user"
    unit_dir.mkdir()
    # Pre-existing unit file → run_s4 fails right after the render, so the
    # captured call is the real propagation path with no host mutation.
    unit_path = unit_dir / "arsd-r13-test.service"
    unit_path.write_text("[Unit]\nDescription=preexisting\n", encoding="utf-8")
    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)
    calls: list[list[str]] = []

    def fake_systemctl(*cmd, check=True):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(harness, "_systemctl_user", fake_systemctl)
    seen: dict[str, object] = {}

    def recording_render(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return _STUB_RENDERED_UNIT

    monkeypatch.setattr(harness, "_render_unit", recording_render)
    binding = _harness_agents_file(tmp_path)
    inputs = {
        "unit_name": "arsd-r13-test.service",
        "socket": _harness_socket(tmp_path),
        "supervisor_root": str(tmp_path / "sv"),
        "caller_mapping": f"{os.getuid()}:p:o:n",
        "evidence_dir": str(evidence),
        "workspace": str(ws),
        "agents_file": binding,
        "agent_id": "s4-agent",
    }
    with pytest.raises(harness.HarnessGateError):
        harness.run_s4(inputs)
    assert seen["kwargs"]["agents_file"] == binding
    assert calls == []
    assert not Path(binding).exists()


def test_harness_dry_validate_plan_reports_agents_file(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    binding = Path(_harness_agents_file(tmp_path))

    def boom_run(*a, **k):
        raise AssertionError("no subprocess in dry-validate")

    monkeypatch.setattr(harness.subprocess, "run", boom_run)
    monkeypatch.setenv("ARS_ARSD_A3_CRASH_HARNESS", "1")
    rc = harness.main(
        [
            "--i-acknowledge-a3-crash-harness",
            "--dry-validate",
            "--unit-name",
            "arsd-r13-dry.service",
            "--socket",
            _harness_socket(tmp_path, "arsd-dry.sock"),
            "--supervisor-root",
            str(tmp_path / "arsd-dry-root"),
            "--caller-mapping",
            f"{os.getuid()}:hermes-test:hermes:hermes/dry",
            "--evidence-dir",
            str(evidence),
            "--workspace",
            str(ws),
            "--agents-file",
            str(binding),
            "--agent-id",
            "s4-agent",
        ]
    )
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["agents_file"] == str(binding)
    assert plan["mutates_host"] is False
    # Dry validation is a path/config fact only: nothing is created or probed
    # into existence.
    assert not binding.exists()


# --- crash-harness Binding-root metadata boundary ---------------------------
#
# The agents file is operator-supplied configuration *text* until the per-Run
# BindingReader opens it. Help, dry validation, and full-harness preflight must
# therefore never resolve/stat/lstat/readlink/open/list the agents file or any
# of its path components — non-creation and unchanged-contents assertions do not
# cover that, because ``Path.resolve(strict=False)`` reads symlink and directory
# metadata without writing anything. These tests instrument the filesystem-query
# primitives themselves so any reintroduced query fails here.


_FS_QUERY_PRIMITIVES = (
    "stat",
    "lstat",
    "readlink",
    "open",
    "access",
    "listdir",
    "scandir",
)


class _FsQueryLog:
    """Every filesystem metadata query made while the recorder is installed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def touching(self, target: Path) -> list[tuple[str, str]]:
        """Queries whose operand is ``target``, anything under it, or a component of it.

        Component (ancestor) hits count: ``Path.resolve`` walks the whole chain,
        so lstat-ing ``/opt`` on the way to ``/opt/<root>`` is exactly the
        metadata read this boundary forbids.
        """
        wanted = os.path.normpath(str(target))
        under = wanted.rstrip(os.sep) + os.sep
        components = {
            str(parent) for parent in Path(wanted).parents if str(parent) != os.sep
        }
        return [
            (op, raw)
            for op, raw in self.calls
            for operand in [os.path.normpath(raw)]
            if operand == wanted or operand.startswith(under) or operand in components
        ]


@contextlib.contextmanager
def _record_fs_queries(monkeypatch):
    """Record every path-metadata primitive pathlib and the harness can reach.

    ``pathlib`` delegates ``exists``/``is_dir``/``is_symlink``/``iterdir`` to the
    ``os`` functions at call time, and ``Path.resolve`` delegates to
    ``os.path.realpath``, so wrapping this set observes every filesystem query
    regardless of which spelling the production code uses.
    """
    log = _FsQueryLog()

    def wrap(op, real):
        def probe(path=".", *args, **kwargs):
            log.calls.append((op, str(path)))
            return real(path, *args, **kwargs)

        return probe

    for name in _FS_QUERY_PRIMITIVES:
        monkeypatch.setattr(os, name, wrap(f"os.{name}", getattr(os, name)))
    monkeypatch.setattr(os.path, "realpath", wrap("realpath", os.path.realpath))

    real_resolve = Path.resolve

    def recording_resolve(self, strict=False):
        log.calls.append(("Path.resolve", str(self)))
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", recording_resolve)
    yield log


def test_harness_preflight_never_queries_the_agents_file(
    monkeypatch, tmp_path: Path
) -> None:
    """Blocker regression: validation resolved the agents file before BindingReader."""
    harness = _load_harness()
    binding = Path(_UNQUERIED_AGENTS_FILE)
    args = _harness_args(tmp_path, agents_file=str(binding))

    with _record_fs_queries(monkeypatch) as log:
        resolved = harness._require_operator_inputs(args)

    assert resolved["agents_file"] == str(binding)
    assert log.touching(binding) == []
    # Paired guard: the surfaces this harness really does own are still checked
    # through the resolution boundary, so the fix cannot be "validate nothing".
    assert log.touching(Path(resolved["workspace"]))
    assert log.touching(Path(resolved["supervisor_root"]))
    assert log.touching(Path(resolved["evidence_dir"]))


def test_harness_dry_validate_never_queries_the_agents_file(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """The whole ``--dry-validate`` path, not just the input validator."""
    harness = _load_harness()
    ws = tmp_path / "ws"
    ws.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    binding = Path(_UNQUERIED_AGENTS_FILE)

    monkeypatch.setattr(
        harness.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no subprocess in dry-validate")
        ),
    )
    monkeypatch.setenv("ARS_ARSD_A3_CRASH_HARNESS", "1")
    with _record_fs_queries(monkeypatch) as log:
        rc = harness.main(
            [
                "--i-acknowledge-a3-crash-harness",
                "--dry-validate",
                "--unit-name",
                "arsd-r13-noquery.service",
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
                "--agents-file",
                str(binding),
                "--agent-id",
                "s4-agent",
            ]
        )

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["agents_file"] == str(binding)
    assert log.touching(binding) == []


@pytest.mark.parametrize("case", ["parent_of_supervisor_root", "inside_evidence_dir"])
def test_harness_agents_file_overlap_refusal_never_resolves_it(
    monkeypatch, tmp_path: Path, case: str
) -> None:
    """Overlap protection stays fail-closed, but proven from the supplied text.

    Overlapping roots share ancestors with the writable surfaces, so this pairs
    with the query-boundary test by instrumenting the harness resolution helper
    instead: on an overlap the refusal comes first, so *nothing* is resolved.
    Resolving even a harness-owned surface here would walk the agents file as
    one of its path components. The paired "surfaces are still validated" guard
    therefore belongs to the disjoint case, not this one — see
    ``test_harness_disjoint_agents_file_still_passes_through_as_a_value``.
    """
    harness = _load_harness()
    overrides: dict[str, object] = {}
    if case == "parent_of_supervisor_root":
        overrides["agents_file"] = str(tmp_path / "outer")
        overrides["supervisor_root"] = str(tmp_path / "outer" / "sv")
    else:
        overrides["agents_file"] = str(tmp_path / "evidence" / "binding")
    args = _harness_args(tmp_path, **overrides)

    operands: list[str] = []
    real_resolve = harness._resolve

    def recording_resolve(path):
        operands.append(str(path))
        return real_resolve(path)

    monkeypatch.setattr(harness, "_resolve", recording_resolve)
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)

    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "overlap" in msg or "inside" in msg
    assert str(overrides["agents_file"]) not in operands
    assert operands == []


def test_harness_rejects_agents_file_inside_repo_without_resolving_it(
    monkeypatch, tmp_path: Path
) -> None:
    """Repo containment stays refused for the agents file — lexically."""
    harness = _load_harness()
    inside = REPO_ROOT / "docs"
    args = _harness_args(tmp_path, agents_file=str(inside))

    operands: list[str] = []
    real_resolve = harness._resolve

    def recording_resolve(path):
        operands.append(str(path))
        return real_resolve(path)

    monkeypatch.setattr(harness, "_resolve", recording_resolve)
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(args)

    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "repository" in msg or "worktree" in msg
    assert str(inside) not in operands


def test_harness_rejects_dot_dot_spelled_agents_file_alias(tmp_path: Path) -> None:
    """A ``..`` spelling that only normalization exposes is still an overlap.

    Guard against a lexical check so naive it compares raw text only. The
    ``neutral`` hop is deliberately not one of the validated surfaces, so no
    as-written prefix of ``<tmp>/neutral/../ws`` matches the workspace, the
    supervisor root, the evidence dir, the socket, or the socket's parent —
    only the collapsed spelling reveals that it *is* the workspace.
    """
    harness = _load_harness()
    alias = str(tmp_path / "neutral" / ".." / "ws")
    with pytest.raises(harness.HarnessGateError) as err:
        harness._require_operator_inputs(_harness_args(tmp_path, agents_file=alias))
    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "overlap" in msg


# --- the whole Binding-root path-safety class (one gate, not four bugs) -----
#
# Refusing an overlapping agents file is not enough: the refusal has to land
# before the *first* filesystem query of any surface. Resolving a writable
# surface that overlaps the agents file reads the agents file itself as a
# path component — ``os.path.realpath`` lstats every prefix — so an overlap
# that is only caught after ``_forbid_inside_repo`` has already leaked the
# operator's storage to the harness. The matrix below therefore asserts an
# empty primitive log, not merely that the Binding text never reached
# ``_resolve``. The derived systemd user-unit path is in the matrix because the
# harness mkdirs its parent and exclusively creates the file; the ``//`` cases
# are in it because Linux aliases ``//x`` to ``/x`` while ``os.path.normpath``
# and ``pathlib`` both keep the two spellings distinct.

_UNIT_NAME = "arsd-r13-test.service"


def _double_slash(path: Path | str) -> str:
    """The exactly-two-leading-slash alias of an absolute path (text only)."""
    return "//" + str(path).lstrip("/")


def _binding_overlap_cases(tmp_path: Path, unit_dir: Path) -> dict[str, dict[str, str]]:
    """Materially distinct overlaps between the agents file and a written surface."""
    ws = tmp_path / "ws"
    unit_path = unit_dir / _UNIT_NAME
    return {
        "binding_equals_supervisor_root": {"agents_file": str(tmp_path / "sv")},
        "binding_parent_of_supervisor_root": {
            "agents_file": str(tmp_path / "outer"),
            "supervisor_root": str(tmp_path / "outer" / "sv"),
        },
        "binding_child_of_supervisor_root": {
            "agents_file": str(tmp_path / "sv" / "generations")
        },
        "binding_equals_workspace": {"agents_file": str(ws)},
        "binding_parent_of_workspace": {
            "agents_file": str(tmp_path / "outer-ws"),
            "workspace": str(tmp_path / "outer-ws" / "ws"),
        },
        "binding_child_of_workspace": {"agents_file": str(ws / "inner")},
        "binding_equals_evidence_dir": {"agents_file": str(tmp_path / "evidence")},
        "binding_parent_of_evidence_dir": {
            "agents_file": str(tmp_path / "outer-ev"),
            "evidence_dir": str(tmp_path / "outer-ev" / "evidence"),
        },
        "binding_child_of_evidence_dir": {
            "agents_file": str(tmp_path / "evidence" / "inner")
        },
        "binding_equals_socket_path": {
            "agents_file": _harness_socket(tmp_path)
        },
        "socket_directly_inside_binding": {
            "agents_file": str(tmp_path / "bind"),
            "socket": str(tmp_path / "bind" / "arsd.sock"),
        },
        "socket_deep_inside_binding": {
            "agents_file": str(tmp_path / "bind"),
            "socket": str(tmp_path / "bind" / "run" / "arsd.sock"),
        },
        # The socket's *directory* is written too — the daemon mkdirs it before
        # it binds — so it belongs in the same symmetric matrix as the socket
        # path itself. Checking only Binding-vs-socket calls ``/x/binding`` and
        # ``/x/arsd.sock`` disjoint while the daemon (correctly) refuses it.
        "binding_equals_socket_directory": {
            "agents_file": str(tmp_path / "sockdir"),
            "socket": str(tmp_path / "sockdir" / "arsd.sock"),
        },
        "binding_inside_socket_directory": {
            "agents_file": str(tmp_path / "sockdir" / "binding"),
            "socket": str(tmp_path / "sockdir" / "arsd.sock"),
        },
        "binding_parent_of_socket_directory": {
            "agents_file": str(tmp_path / "outer-sock"),
            "socket": str(tmp_path / "outer-sock" / "sockdir" / "arsd.sock"),
        },
        "double_slash_binding_aliases_socket_directory": {
            "agents_file": _double_slash(tmp_path / "sockdir"),
            "socket": str(tmp_path / "sockdir" / "arsd.sock"),
        },
        "double_slash_socket_aliases_binding_directory": {
            "agents_file": str(tmp_path / "sockdir"),
            "socket": _double_slash(tmp_path / "sockdir" / "arsd.sock"),
        },
        # Derived writable surface: dry validation stats it, full execution
        # mkdirs its parent and exclusively creates the unit file.
        "binding_equals_unit_path": {"agents_file": str(unit_path)},
        "binding_child_of_unit_path": {"agents_file": str(unit_path / "inner")},
        "binding_equals_unit_directory": {"agents_file": str(unit_dir)},
        "binding_inside_unit_directory": {"agents_file": str(unit_dir / "binding")},
        # Exactly-two-leading-slash aliases, on either operand.
        "double_slash_binding_aliases_workspace": {"agents_file": _double_slash(ws)},
        "double_slash_binding_aliases_unit_directory": {
            "agents_file": _double_slash(unit_dir)
        },
        "double_slash_workspace_aliases_binding": {
            "agents_file": str(ws),
            "workspace": _double_slash(ws),
        },
        "double_slash_supervisor_root_aliases_binding_parent": {
            "agents_file": str(tmp_path / "outer"),
            "supervisor_root": _double_slash(tmp_path / "outer" / "sv"),
        },
    }


_BINDING_OVERLAP_CASE_IDS = sorted(_binding_overlap_cases(Path("/x"), Path("/x/u")))


@pytest.mark.parametrize("case", _BINDING_OVERLAP_CASE_IDS)
def test_harness_refuses_binding_overlap_before_any_filesystem_query(
    monkeypatch, tmp_path: Path, case: str
) -> None:
    """Every overlap direction refuses from text alone, querying nothing."""
    harness = _load_harness()
    unit_dir = tmp_path / "systemd-user"
    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)
    overrides = _binding_overlap_cases(tmp_path, unit_dir)[case]
    args = _harness_args(tmp_path, unit_name=_UNIT_NAME, **overrides)
    # The Linux-equivalent spelling: '//x' and '/x' name the same directory.
    binding = Path("/" + str(args.agents_file).lstrip("/"))

    with _record_fs_queries(monkeypatch) as log:
        with pytest.raises(harness.HarnessGateError) as err:
            harness._require_operator_inputs(args)

    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "overlap" in msg or "inside" in msg
    assert log.touching(binding) == []
    # The ordering claim: refusal precedes the first query of *any* surface,
    # because resolving an overlapping surface reads the agents file itself.
    assert log.calls == []


def _harness_shared_ancestor_cases(
    tmp_path: Path, unit_dir: Path
) -> dict[str, dict[str, str]]:
    """agents files that share an ancestor with a queried surface, containing none.

    Every surface below is resolved, stat-ed, listed, mkdir-ed, or created by
    this harness before any ``BindingReader`` exists, and each of those walks the
    surface's whole component chain. A sibling under a shared parent therefore
    leaks exactly the metadata read the boundary forbids, even though no path
    contains any other.
    """
    ws = tmp_path / "ws"
    return {
        "inside_supervisor_root": {
            "agents_file": str(tmp_path / "sv" / "agents.toml"),
            "supervisor_root": str(tmp_path / "sv"),
        },
        "inside_workspace": {
            "agents_file": str(ws / "agents.toml"),
            "workspace": str(ws),
        },
        "beside_evidence_dir": {
            "agents_file": str(tmp_path / "evidence" / "agents.toml"),
            "evidence_dir": str(tmp_path / "evidence"),
        },
        "inside_socket_directory": {
            "agents_file": str(tmp_path / "sock" / "agents.toml"),
            "socket": _harness_socket(tmp_path),
        },
        "equals_socket_directory": {
            "agents_file": str(tmp_path / "sock"),
            "socket": _harness_socket(tmp_path),
        },
        "inside_unit_directory": {
            "agents_file": str(unit_dir / "agents.toml"),
        },
        "equals_unit_directory": {
            "agents_file": str(unit_dir),
        },
        "contains_supervisor_root": {
            "agents_file": str(tmp_path / "sv"),
            "supervisor_root": str(tmp_path / "sv" / "inner"),
        },
        # Alias spellings of the same containment.
        "double_slash_containment": {
            "agents_file": _double_slash(tmp_path / "sv"),
            "supervisor_root": str(tmp_path / "sv"),
        },
        "dot_hop_containment": {
            "agents_file": str(tmp_path / "." / "sv"),
            "supervisor_root": str(tmp_path / "sv"),
        },
        "dot_dot_hop_containment": {
            "agents_file": str(tmp_path / "neutral" / ".." / "sv"),
            "supervisor_root": str(tmp_path / "sv"),
        },
    }


@pytest.mark.parametrize(
    "case", sorted(_harness_shared_ancestor_cases(Path("/x"), Path("/x/u")))
)
def test_harness_refuses_a_containing_agents_file_before_any_query(
    monkeypatch, tmp_path: Path, case: str
) -> None:
    """Blocker regression: sibling layouts resolved a Binding component pre-reader."""
    harness = _load_harness()
    unit_dir = tmp_path / "systemd-user"
    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)
    overrides = _harness_shared_ancestor_cases(tmp_path, unit_dir)[case]
    args = _harness_args(tmp_path, unit_name=_UNIT_NAME, **overrides)

    with _record_fs_queries(monkeypatch) as log:
        with pytest.raises(harness.HarnessGateError) as err:
            harness._require_operator_inputs(args)

    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "overlap" in msg or "inside" in msg
    assert log.calls == []


def test_harness_refuses_an_agents_file_inside_the_repository(
    monkeypatch, tmp_path: Path
) -> None:
    """The worktree is a written surface too — operator storage never lives in it."""
    harness = _load_harness()
    binding = REPO_ROOT / "ars-operator-agents-inside-the-worktree.toml"

    with _record_fs_queries(monkeypatch) as log:
        with pytest.raises(harness.HarnessGateError) as err:
            harness._require_operator_inputs(
                _harness_args(tmp_path, agents_file=str(binding))
            )

    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "overlap" in msg or "inside" in msg
    assert log.calls == []


def test_harness_disjoint_agents_file_still_passes_through_as_a_value(
    monkeypatch, tmp_path: Path
) -> None:
    """The gate refuses overlap only — a disjoint root stays an accepted value."""
    harness = _load_harness()
    unit_dir = tmp_path / "systemd-user"
    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)
    binding = Path(_UNQUERIED_AGENTS_FILE)
    args = _harness_args(tmp_path, unit_name=_UNIT_NAME, agents_file=str(binding))

    with _record_fs_queries(monkeypatch) as log:
        resolved = harness._require_operator_inputs(args)

    assert resolved["agents_file"] == str(binding)
    assert log.touching(binding) == []
    # Paired guard: the real checks on harness-owned surfaces are still run, so
    # the fix cannot be "stop validating".
    assert log.touching(Path(resolved["workspace"]))
    assert log.touching(Path(resolved["supervisor_root"]))
    assert log.touching(Path(resolved["evidence_dir"]))
    assert not binding.exists()


def test_harness_dry_validate_refuses_unit_path_overlap_before_probing_it(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """``--dry-validate`` reports ``unit_file_exists`` — that probe must not run."""
    harness = _load_harness()
    unit_dir = tmp_path / "systemd-user"
    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)
    monkeypatch.setattr(
        harness.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess")),
    )
    monkeypatch.setenv("ARS_ARSD_A3_CRASH_HARNESS", "1")
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    argv = [
        "--i-acknowledge-a3-crash-harness",
        "--dry-validate",
        "--unit-name",
        _UNIT_NAME,
        "--socket",
        _harness_socket(tmp_path),
        "--supervisor-root",
        str(tmp_path / "sv"),
        "--caller-mapping",
        f"{os.getuid()}:hermes-test:hermes:hermes/dry",
        "--evidence-dir",
        str(evidence),
        "--workspace",
        str(ws),
        "--agents-file",
        str(unit_dir),
        "--agent-id",
        "s4-agent",
    ]

    with _record_fs_queries(monkeypatch) as log:
        rc = harness.main(argv)

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "agents_file" in captured.err.lower()
    assert log.touching(unit_dir) == []
    # No operator-supplied or derived surface is probed at all before the
    # refusal: every one of them lives under tmp_path. (``log.calls`` itself is
    # not empty here — argparse stats gettext catalogs under the interpreter
    # prefix, which touches nothing this gate is about.)
    assert log.touching(tmp_path) == []


def test_harness_full_preflight_refuses_unit_path_overlap_before_any_unit_write(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """Same refusal without ``--dry-validate``: no mkdir, no unit file, no systemctl."""
    harness = _load_harness()
    unit_dir = tmp_path / "systemd-user"
    unit_path = unit_dir / _UNIT_NAME
    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)

    def boom(*_a, **_k):
        raise AssertionError("must refuse before any host mutation")

    monkeypatch.setattr(harness.subprocess, "run", boom)
    monkeypatch.setattr(harness, "_systemctl_user", boom)
    monkeypatch.setattr(harness, "_render_unit", boom)
    monkeypatch.setattr(harness, "run_s4", boom)
    monkeypatch.setenv("ARS_ARSD_A3_CRASH_HARNESS", "1")
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)

    rc = harness.main(
        [
            "--i-acknowledge-a3-crash-harness",
            "--unit-name",
            _UNIT_NAME,
            "--socket",
            _harness_socket(tmp_path),
            "--supervisor-root",
            str(tmp_path / "sv"),
            "--caller-mapping",
            f"{os.getuid()}:hermes-test:hermes:hermes/full",
            "--evidence-dir",
            str(evidence),
            "--workspace",
            str(ws),
            "--agents-file",
            str(unit_path),
            "--agent-id",
            "s4-agent",
        ]
    )

    assert rc == 2
    assert "agents_file" in capsys.readouterr().err.lower()
    assert not unit_dir.exists()
    assert not unit_path.exists()


_QUERY_CONFLICT_TABLE = [
    # Equality and containment, both directions — the whole rule.
    ("/srv/agents.toml", "/srv/agents.toml", True),
    ("/srv/conf", "/srv/conf/sub", True),
    ("/srv/conf/sub", "/srv/conf", True),
    # Alias spellings of the same containment, on either operand.
    ("//srv/conf", "/srv/conf/sub", True),
    ("/srv/conf", "//srv/conf/sub", True),
    ("///srv/conf", "/srv/conf", True),
    ("/srv/conf", "/srv/neutral/../conf/sub", True),
    ("/srv/neutral/../conf", "/srv/conf/sub", True),
    # The filesystem root contains everything, however it is spelled.
    ("/", "/srv/sv", True),
    ("/srv/..", "/other/sv", True),
    ("/srv/conf", "/", True),
    # Siblings under one parent are an ordinary, safe operator layout: the
    # daemon writes into its own directory and never into the operator's. The
    # predecessor of this rule refused these too, because a shared ancestor was
    # itself a forbidden read under the retired first-and-only-reader
    # invariant; that premise is gone, and refusing them now would protect
    # nothing while making ``~/ars/agents.toml`` beside ``~/ars/state``
    # unstartable.
    ("/srv/agents.toml", "/srv/sv", False),
    ("/srv/a/agents.toml", "/srv/b/sv", False),
    ("/opt/ars-conf", "/opt/ars-state", False),
    ("/srv/conf", "/srv/conf-sibling", False),
    ("/srv/conf", "/srv/./sv", False),
    ("/srv/conf", "/other/../srv/sv", False),
    ("/srv/conf", "/srv/x/../../other/sv", False),
    # Genuinely disjoint.
    ("/srv/conf", "/other/root", False),
    ("//srv/conf", "//other/root", False),
    ("/opt/conf", "/tmp/sv", False),
    ("/opt/conf", "/home/op/.config/systemd/user", False),
    ("/srv/conf", "/srv2/sv", False),
    # Fail-closed: a relative operand names no fixed location, so no text
    # comparison can decide it and the answer must be "conflict".
    ("/srv/conf", "relative-sv", True),
    ("relative-conf", "/srv/sv", True),
]


@pytest.mark.parametrize(("binding", "surface", "expected"), _QUERY_CONFLICT_TABLE)
def test_harness_and_daemon_agree_on_the_agents_file_query_conflict_rule(
    binding: str, surface: str, expected: bool
) -> None:
    """One rule, mirrored in two modules; divergence is how aliases slip through.

    The harness cannot import the package (it must stay runnable from a bare
    checkout), so the daemon helper is duplicated rather than shared. That makes
    semantic parity a test obligation: the harness installs the unit that this
    very daemon runs, so a layout one accepts and the other refuses is either a
    unit that can never start or an ARS-owned write inside operator configuration.
    """
    from agent_run_supervisor.arsd import __main__ as arsd_main

    harness = _load_harness()
    assert harness._agents_file_query_conflict(binding, surface) is expected
    assert arsd_main._agents_file_query_conflict(binding, surface) is expected
    # ``Path`` operands must answer identically to their text spelling: the
    # daemon's declared union accepts both and the two must not diverge.
    assert harness._agents_file_query_conflict(Path(binding), Path(surface)) is expected
    assert arsd_main._agents_file_query_conflict(Path(binding), Path(surface)) is expected


def test_harness_and_daemon_agree_on_the_queried_component_set() -> None:
    """Parity of the underlying enumeration, not merely of its boolean verdict."""
    from agent_run_supervisor.arsd import __main__ as arsd_main

    harness = _load_harness()
    for spelling in (
        "/srv/binding",
        "//srv/binding",
        "///srv/binding/",
        "/srv/./binding",
        "/srv/neutral/../binding",
        "/srv/x/../../other/sv",
        "/",
        "/srv/..",
    ):
        assert harness._lexical_query_components(
            spelling
        ) == arsd_main._lexical_query_components(spelling), spelling


def test_queried_components_enumerate_the_whole_ancestor_chain() -> None:
    """The rule's premise: an operation on a path may query every component of it."""
    from agent_run_supervisor.arsd import __main__ as arsd_main

    assert arsd_main._lexical_query_components("/srv/a/b") == frozenset(
        {"/srv", "/srv/a", "/srv/a/b"}
    )
    # ``/`` is excluded: every absolute path shares it, so counting it would
    # refuse every possible layout.
    assert "/" not in arsd_main._lexical_query_components("/srv/a/b")
    assert arsd_main._lexical_query_components("/") == frozenset()
    # Both spellings contribute: the kernel walks ``/srv/neutral`` on the way
    # to a destination that only the collapsed spelling names.
    assert arsd_main._lexical_query_components("/srv/neutral/../b") == frozenset(
        {"/srv", "/srv/neutral", "/srv/b"}
    )


# --- the socket directory is a written surface on both boundaries -----------
#
# Helper agreement (above) is necessary but not sufficient: the two boundaries
# can answer the same containment question and still disagree about *which*
# surfaces they ask it about. The harness asked about the socket path and then
# only about equality of the socket's parent, so Binding ``/x/binding`` beside
# socket ``/x/arsd.sock`` passed here and was refused by the very daemon this
# harness renders and installs — after the harness had already resolved the
# shared writable directory. These tests compare the two boundaries end to end,
# by running the shipped ``serve_daemon`` gate rather than restating its rules.


class _LeaseReached(Exception):
    """Sentinel wall proving the daemon's pure gate accepted a layout."""


def _daemon_refuses_layout(
    monkeypatch, *, binding: str, socket: str, supervisor_root: str
) -> bool:
    """Does the shipped ``serve_daemon`` refuse this *layout* before the lease?

    The layout verdict is the overlap gate's alone. A daemon that clears the
    gate and then fails closed on a registry file the test never created has
    accepted the layout, so that refusal is reported as acceptance here — the
    harness has no registry parse to reach, and comparing the two verdicts is
    only meaningful when both answer the same question.
    """
    from agent_run_supervisor.arsd import __main__ as arsd_main
    from agent_run_supervisor.arsd.server import CallerPolicy, Principal

    def wall(_root):
        raise _LeaseReached

    monkeypatch.setattr(arsd_main, "acquire_daemon_instance_lease", wall)
    principal = Principal(
        principal_id="hermes-test", owner_namespaces=frozenset([("hermes", "hermes/x")])
    )
    try:
        asyncio.run(
            arsd_main.serve_daemon(
                socket_path=socket,
                supervisor_root=supervisor_root,
                policy=CallerPolicy(dict([(os.getuid(), principal)])),
                agents_file=binding,
                install_signals=False,
            )
        )
    except arsd_main.DaemonStartupError as err:
        message = str(err).lower()
        assert "agent" in message, err
        if "overlap" in message:
            return True
        assert "registry_absent" in message, err
        return False
    except _LeaseReached:
        return False
    raise AssertionError("serve_daemon returned without reaching the lease")


def _harness_refuses_layout(
    harness, tmp_path: Path, *, binding: str, socket: str, supervisor_root: str
) -> bool:
    """Does the crash harness refuse the same layout, for the same reason?"""
    args = _harness_args(
        tmp_path,
        agents_file=binding,
        socket=socket,
        supervisor_root=supervisor_root,
    )
    try:
        harness._require_operator_inputs(args)
    except harness.HarnessGateError as err:
        assert "agents_file" in str(err), err
        return True
    return False


def _agreement_layouts(tmp_path: Path) -> dict[str, dict[str, object]]:
    """Layouts differing only in how the agents file relates to written surfaces."""
    sock_dir = tmp_path / "sock"
    return {
        "binding_inside_socket_directory": {
            "binding": str(sock_dir / "binding"),
            "socket": str(sock_dir / "arsd.sock"),
            "refused": True,
        },
        "binding_equals_socket_directory": {
            "binding": str(sock_dir),
            "socket": str(sock_dir / "arsd.sock"),
            "refused": True,
        },
        "binding_parent_of_socket_directory": {
            "binding": str(tmp_path / "outer-sock"),
            "socket": str(tmp_path / "outer-sock" / "sock" / "arsd.sock"),
            "refused": True,
        },
        "socket_inside_binding": {
            "binding": str(tmp_path / "bind"),
            "socket": str(tmp_path / "bind" / "arsd.sock"),
            "refused": True,
        },
        "binding_equals_supervisor_root": {
            "binding": str(tmp_path / "sv"),
            "socket": str(sock_dir / "arsd.sock"),
            "refused": True,
        },
        "double_slash_binding_aliases_socket_directory": {
            "binding": _double_slash(sock_dir),
            "socket": str(sock_dir / "arsd.sock"),
            "refused": True,
        },
        "double_slash_socket_aliases_binding": {
            "binding": str(sock_dir),
            "socket": _double_slash(sock_dir / "arsd.sock"),
            "refused": True,
        },
        # A sibling of the socket directory contains nothing and is
        # contained by nothing, so no ARS-owned write can reach it: both
        # copies of the rule admit it, and agreeing on that is the point.
        "sibling_prefix_of_socket_directory": {
            "binding": str(tmp_path / "sock-sibling"),
            "socket": str(sock_dir / "arsd.sock"),
            "refused": False,
        },
        # Control: neither boundary may refuse a genuinely disjoint layout —
        # no non-root component in common with the socket, the supervisor root,
        # the workspace, the evidence dir, the unit path, or the worktree.
        "disjoint_control": {
            "binding": _UNQUERIED_AGENTS_FILE,
            "socket": str(sock_dir / "arsd.sock"),
            "refused": False,
        },
    }


_AGREEMENT_CASE_IDS = sorted(_agreement_layouts(Path("/x")))


@pytest.mark.parametrize("case", _AGREEMENT_CASE_IDS)
def test_harness_and_daemon_agree_on_written_surface_overlap(
    monkeypatch, tmp_path: Path, case: str
) -> None:
    """The harness must accept exactly the layouts its generated daemon can start."""
    harness = _load_harness()
    layout = _agreement_layouts(tmp_path)[case]
    supervisor = str(tmp_path / "sv")

    daemon_refused = _daemon_refuses_layout(
        monkeypatch,
        binding=str(layout["binding"]),
        socket=str(layout["socket"]),
        supervisor_root=supervisor,
    )
    harness_refused = _harness_refuses_layout(
        harness,
        tmp_path,
        binding=str(layout["binding"]),
        socket=str(layout["socket"]),
        supervisor_root=supervisor,
    )

    assert daemon_refused is layout["refused"], ("daemon", case)
    assert harness_refused is layout["refused"], ("harness", case)


def test_harness_refuses_binding_beside_the_socket_before_any_query(
    monkeypatch, tmp_path: Path
) -> None:
    """Blocker regression: ``/x/binding`` + ``/x/arsd.sock`` was accepted and probed.

    The old check asked Binding-vs-socket (disjoint) and then only whether the
    socket's parent *equalled* the agents file (it does not). The refusal has
    to come from the same symmetric containment rule as every other surface,
    and it has to precede the first query — resolving the shared directory
    ``/x`` is a metadata read of the agents file's own parent.
    """
    harness = _load_harness()
    shared = tmp_path / "shared"
    binding = shared / "binding"
    args = _harness_args(
        tmp_path, agents_file=str(binding), socket=str(shared / "arsd.sock")
    )

    with _record_fs_queries(monkeypatch) as log:
        with pytest.raises(harness.HarnessGateError) as err:
            harness._require_operator_inputs(args)

    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "overlap" in msg or "inside" in msg
    assert log.touching(shared) == []
    assert log.calls == []


def test_harness_dry_validate_refuses_socket_directory_overlap(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """The whole ``--dry-validate`` path refuses before ``unit_file_exists`` probing."""
    harness = _load_harness()
    unit_dir = tmp_path / "systemd-user"
    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)
    monkeypatch.setattr(
        harness.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess")),
    )
    monkeypatch.setenv("ARS_ARSD_A3_CRASH_HARNESS", "1")
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    shared = tmp_path / "shared"

    with _record_fs_queries(monkeypatch) as log:
        rc = harness.main(
            [
                "--i-acknowledge-a3-crash-harness",
                "--dry-validate",
                "--unit-name",
                _UNIT_NAME,
                "--socket",
                str(shared / "arsd.sock"),
                "--supervisor-root",
                str(tmp_path / "sv"),
                "--caller-mapping",
                f"{os.getuid()}:hermes-test:hermes:hermes/dry",
                "--evidence-dir",
                str(evidence),
                "--workspace",
                str(ws),
                "--agents-file",
                str(shared / "binding"),
                "--agent-id",
                "s4-agent",
            ]
        )

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "agents_file" in captured.err.lower()
    assert log.touching(tmp_path) == []


def test_harness_full_preflight_refuses_socket_directory_overlap(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """Same refusal without ``--dry-validate``: no mkdir, no unit file, no systemctl."""
    harness = _load_harness()
    unit_dir = tmp_path / "systemd-user"
    monkeypatch.setattr(harness, "_user_unit_path", lambda name: unit_dir / name)

    def boom(*_a, **_k):
        raise AssertionError("must refuse before any host mutation")

    monkeypatch.setattr(harness.subprocess, "run", boom)
    monkeypatch.setattr(harness, "_systemctl_user", boom)
    monkeypatch.setattr(harness, "_render_unit", boom)
    monkeypatch.setattr(harness, "run_s4", boom)
    monkeypatch.setenv("ARS_ARSD_A3_CRASH_HARNESS", "1")
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    shared = tmp_path / "shared"

    rc = harness.main(
        [
            "--i-acknowledge-a3-crash-harness",
            "--unit-name",
            _UNIT_NAME,
            "--socket",
            str(shared / "arsd.sock"),
            "--supervisor-root",
            str(tmp_path / "sv"),
            "--caller-mapping",
            f"{os.getuid()}:hermes-test:hermes:hermes/full",
            "--evidence-dir",
            str(evidence),
            "--workspace",
            str(ws),
            "--agents-file",
            str(shared / "binding"),
            "--agent-id",
            "s4-agent",
        ]
    )

    assert rc == 2
    assert "agents_file" in capsys.readouterr().err.lower()
    assert not shared.exists()
    assert not unit_dir.exists()


# --- repository containment: lexical at import, refused in both directions ---
#
# Two independent defects lived here. ``_REPO_ROOT = Path(__file__).resolve()``
# lstats every component of the repository path the moment the module is
# executed — a filesystem query performed before the operator's Binding input is
# even parsed, in a module whose whole contract is "query nothing before
# BindingReader". And ``_forbid_inside_repo_lexically`` only asked whether the
# agents file was *inside* the repository; a agents file that **contains** the
# worktree makes every harness-owned surface a Binding subpath, which is the
# same collision seen from the other end.


def test_harness_repo_root_is_derived_without_any_filesystem_query(monkeypatch) -> None:
    """Blocker regression: import resolved the repository path.

    The instrumentation must be installed *before* module execution, so this
    re-executes the module source in a fresh namespace rather than inspecting a
    module ``_load_harness()`` already imported. Stdlib imports are warmed
    first, so the recorder observes the module's own derivation and not Python's
    import machinery.
    """
    source_path = REPO_ROOT / "scripts" / "arsd_crash_containment_harness.py"
    code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    _load_harness()

    namespace: dict = {
        "__file__": str(source_path),
        "__name__": "arsd_crash_harness_import_probe",
    }
    cwd_reads: list[int] = []
    with _record_fs_queries(monkeypatch) as log:
        # ``os.getcwd`` is not a path-metadata query, but the absolute spelling
        # must not need it at all: it is reserved for a relative ``__file__``.
        monkeypatch.setattr(os, "getcwd", lambda: cwd_reads.append(1) or "/")
        exec(code, namespace)
        import_calls = list(log.calls)
        # Positive control: the recorder is demonstrably live at this point, so
        # an empty import log is a real observation and not a broken probe.
        os.stat(str(source_path))

    assert import_calls == []
    assert log.calls
    assert cwd_reads == []
    assert namespace["_REPO_ROOT"] == REPO_ROOT


def test_harness_repo_root_anchors_a_relative_file_spelling_at_import(
    monkeypatch, tmp_path: Path
) -> None:
    """The ``os.getcwd()`` branch, exercised end to end rather than by injection.

    Python has made ``__file__`` absolute since 3.9, so this drives the branch
    the way an older interpreter or an odd loader would: a relative spelling
    executed from a known cwd. The derived root must still be absolute — a
    relative one would compare against nothing and silently disable the gate.
    """
    source_path = REPO_ROOT / "scripts" / "arsd_crash_containment_harness.py"
    code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    _load_harness()

    monkeypatch.chdir(REPO_ROOT)
    namespace: dict = {
        "__file__": "scripts/arsd_crash_containment_harness.py",
        "__name__": "arsd_crash_harness_relative_probe",
    }
    with _record_fs_queries(monkeypatch) as log:
        exec(code, namespace)

    assert log.calls == []
    assert namespace["_REPO_ROOT"] == REPO_ROOT
    assert namespace["_REPO_ROOT"].is_absolute()


@pytest.mark.parametrize(
    ("spelling", "cwd", "expected"),
    [
        ("/srv/checkout/scripts/harness.py", None, "/srv/checkout"),
        ("//srv/checkout/scripts/harness.py", None, "/srv/checkout"),
        ("///srv/checkout/scripts/harness.py", None, "/srv/checkout"),
        ("/srv/checkout/./scripts/harness.py", None, "/srv/checkout"),
        ("/srv/checkout/tools/../scripts/harness.py", None, "/srv/checkout"),
        ("scripts/harness.py", "/srv/checkout", "/srv/checkout"),
        ("./scripts/harness.py", "/srv/checkout", "/srv/checkout"),
        ("harness.py", "/srv/checkout/scripts", "/srv/checkout"),
    ],
)
def test_harness_repo_root_handles_absolute_and_relative_file_spellings(
    spelling: str, cwd: str | None, expected: str
) -> None:
    """Both ``__file__`` spellings resolve to one absolute root, from text alone.

    A relative spelling must never degrade into a relative root: the Binding
    root is required absolute, so a relative repository root would silently
    turn the containment gate into a no-op.
    """
    harness = _load_harness()
    derived = (
        harness._lexical_repo_root(spelling)
        if cwd is None
        else harness._lexical_repo_root(spelling, cwd=cwd)
    )
    assert derived == Path(expected)
    assert derived.is_absolute()


def _repo_overlap_cases() -> dict[str, str]:
    """agents files that collide with the repository/worktree, in both directions."""
    return {
        "equals_repo": str(REPO_ROOT),
        "inside_repo": str(REPO_ROOT / "docs"),
        "deep_inside_repo": str(REPO_ROOT / "src" / "agent_run_supervisor"),
        "dot_dot_alias_of_repo": str(REPO_ROOT / "docs" / ".."),
        "contains_repo": str(REPO_ROOT.parent),
        "far_ancestor_of_repo": str(REPO_ROOT.parents[2]),
        "filesystem_root": "/",
        "double_slash_equals_repo": _double_slash(REPO_ROOT),
        "double_slash_contains_repo": _double_slash(REPO_ROOT.parent),
    }


@pytest.mark.parametrize("case", sorted(_repo_overlap_cases()))
def test_harness_refuses_repository_overlap_in_both_directions(
    monkeypatch, tmp_path: Path, case: str
) -> None:
    """Inside-the-repo and containing-the-repo are the same collision."""
    harness = _load_harness()
    binding = _repo_overlap_cases()[case]
    args = _harness_args(
        tmp_path, agents_file=binding, socket=_harness_socket(tmp_path)
    )

    with _record_fs_queries(monkeypatch) as log:
        with pytest.raises(harness.HarnessGateError) as err:
            harness._require_operator_inputs(args)

    msg = str(err.value).lower()
    assert "agents_file" in msg or "agent registry" in msg
    assert "repository" in msg or "worktree" in msg
    assert log.calls == []


def test_harness_admits_an_agents_file_that_only_shares_a_repo_prefix(
    monkeypatch, tmp_path: Path
) -> None:
    """A worktree *sibling* is not inside the worktree, and is admitted.

    Sharing a parent with the repository is not containment, and the harness
    writes nothing into a sibling directory. Refusing it would refuse an
    ordinary operator layout for no gain — the rule the harness and the daemon
    both apply is equality-or-containment, and this case is neither.
    """
    harness = _load_harness()
    sibling = REPO_ROOT.parent / (REPO_ROOT.name + "-operator-agents.toml")
    args = _harness_args(
        tmp_path, agents_file=str(sibling), socket=_harness_socket(tmp_path)
    )

    resolved = harness._require_operator_inputs(args)
    assert resolved["agents_file"] == str(sibling)
    assert not sibling.exists()


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


# -- R5 B4: PID-reuse-safe harness kill via pidfd -----------------------------


def _patch_pidfd(monkeypatch, harness, *, open_fd=77, on_send=None, on_open=None):
    """Install pidfd APIs even when the interpreter build lacks them."""
    closed: list[int] = []

    def default_open(pid, flags=0):
        return open_fd

    def default_send(pidfd, sig, *a, **k):
        return None

    monkeypatch.setattr(
        harness.os, "pidfd_open", on_open or default_open, raising=False
    )
    monkeypatch.setattr(
        harness.signal,
        "pidfd_send_signal",
        on_send or default_send,
        raising=False,
    )
    monkeypatch.setattr(
        harness.os, "close", lambda fd: closed.append(fd), raising=False
    )
    return closed


def test_r5_b4_pidfd_sigkill_success_exact_identity(monkeypatch) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 5100
    main_ident = (5100, 11)
    agent_ident = (5200, 22)
    helper = (5300, 33)
    live = {
        main_pid: main_ident,
        agent_ident[0]: agent_ident,
        helper[0]: helper,
    }
    sent: list[tuple[int, int]] = []

    def on_send(pidfd, sig, *a, **k):
        sent.append((pidfd, sig))

    closed = _patch_pidfd(monkeypatch, harness, open_fd=77, on_send=on_send)
    monkeypatch.setattr(harness, "_main_pid", lambda _u: main_pid)
    monkeypatch.setattr(
        harness,
        "_cgroup_procs_for_unit",
        lambda _u: [main_pid, agent_ident[0], helper[0]],
    )
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))

    pids, idents = harness._pidfd_sigkill_verified_main(
        unit_name="unit.service",
        main_pid=main_pid,
        main_ident=main_ident,
        agent_ident=agent_ident,
    )
    assert sent == [(77, harness.signal.SIGKILL)]
    assert 77 in closed
    assert main_pid in pids
    assert agent_ident in idents
    assert main_ident in idents


def test_r5_b4_pidfd_refuses_mainpid_changed(monkeypatch) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 6100
    main_ident = (6100, 1)
    agent_ident = (6200, 2)

    def boom_send(*_a, **_k):
        raise AssertionError("must not signal")

    closed = _patch_pidfd(monkeypatch, harness, open_fd=88, on_send=boom_send)
    monkeypatch.setattr(harness, "_main_pid", lambda _u: 6111)
    with pytest.raises(harness.HarnessGateError, match="MainPID changed"):
        harness._pidfd_sigkill_verified_main(
            unit_name="unit.service",
            main_pid=main_pid,
            main_ident=main_ident,
            agent_ident=agent_ident,
        )
    assert 88 in closed


def test_r5_b4_pidfd_refuses_pid_reuse_new_start(monkeypatch) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 7100
    main_ident = (7100, 1)
    agent_ident = (7200, 2)

    def boom_send(*_a, **_k):
        raise AssertionError("must not signal")

    closed = _patch_pidfd(monkeypatch, harness, open_fd=99, on_send=boom_send)
    monkeypatch.setattr(harness, "_main_pid", lambda _u: main_pid)
    monkeypatch.setattr(
        harness,
        "_process_start_identity",
        lambda pid: (pid, 999) if pid == main_pid else None,
    )
    with pytest.raises(harness.HarnessGateError, match="start identity"):
        harness._pidfd_sigkill_verified_main(
            unit_name="unit.service",
            main_pid=main_pid,
            main_ident=main_ident,
            agent_ident=agent_ident,
        )
    assert 99 in closed


def test_r5_b4_pidfd_refuses_mainpid_absent_from_cgroup(monkeypatch) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 8100
    main_ident = (8100, 5)
    agent_ident = (8200, 6)
    live = {main_pid: main_ident, agent_ident[0]: agent_ident}

    def boom_send(*_a, **_k):
        raise AssertionError("must not signal")

    closed = _patch_pidfd(monkeypatch, harness, open_fd=101, on_send=boom_send)
    monkeypatch.setattr(harness, "_main_pid", lambda _u: main_pid)
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))
    monkeypatch.setattr(harness, "_cgroup_procs_for_unit", lambda _u: [agent_ident[0]])
    with pytest.raises(harness.HarnessGateError, match="MainPID absent"):
        harness._pidfd_sigkill_verified_main(
            unit_name="unit.service",
            main_pid=main_pid,
            main_ident=main_ident,
            agent_ident=agent_ident,
        )
    assert 101 in closed


def test_r5_b4_pidfd_refuses_agent_moved_out(monkeypatch) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 9100
    main_ident = (9100, 7)
    agent_ident = (9200, 8)
    live = {main_pid: main_ident, 9400: (9400, 1)}

    def boom_send(*_a, **_k):
        raise AssertionError("must not signal")

    closed = _patch_pidfd(monkeypatch, harness, open_fd=102, on_send=boom_send)
    monkeypatch.setattr(harness, "_main_pid", lambda _u: main_pid)
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))
    monkeypatch.setattr(harness, "_cgroup_procs_for_unit", lambda _u: [main_pid, 9400])
    with pytest.raises(harness.HarnessGateError):
        harness._pidfd_sigkill_verified_main(
            unit_name="unit.service",
            main_pid=main_pid,
            main_ident=main_ident,
            agent_ident=agent_ident,
        )
    assert 102 in closed


def test_r5_b4_pidfd_open_failure_sanitized(monkeypatch) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)

    def boom_open(*_a, **_k):
        raise OSError("raw open path must not surface")

    def boom_send(*_a, **_k):
        raise AssertionError("must not signal")

    _patch_pidfd(monkeypatch, harness, on_open=boom_open, on_send=boom_send)
    with pytest.raises(harness.HarnessGateError, match="pidfd open") as err:
        harness._pidfd_sigkill_verified_main(
            unit_name="unit.service",
            main_pid=1,
            main_ident=(1, 1),
            agent_ident=(2, 2),
        )
    assert "raw open path" not in str(err.value)


def test_r5_b4_pidfd_send_failure_closes_fd(monkeypatch) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)
    main_pid = 10100
    main_ident = (10100, 3)
    agent_ident = (10200, 4)
    live = {main_pid: main_ident, agent_ident[0]: agent_ident}

    def boom_send(*_a, **_k):
        raise OSError("send failed raw")

    closed = _patch_pidfd(monkeypatch, harness, open_fd=103, on_send=boom_send)
    monkeypatch.setattr(harness, "_main_pid", lambda _u: main_pid)
    monkeypatch.setattr(harness, "_process_start_identity", lambda pid: live.get(pid))
    monkeypatch.setattr(
        harness, "_cgroup_procs_for_unit", lambda _u: [main_pid, agent_ident[0]]
    )
    with pytest.raises(harness.HarnessGateError, match="pidfd send"):
        harness._pidfd_sigkill_verified_main(
            unit_name="unit.service",
            main_pid=main_pid,
            main_ident=main_ident,
            agent_ident=agent_ident,
        )
    assert 103 in closed


def test_r5_b4_os_kill_never_used_by_pidfd_helper(monkeypatch) -> None:
    harness = _load_harness()
    _assert_no_host_mutation(monkeypatch, harness)

    def boom_kill(*_a, **_k):
        raise AssertionError("os.kill must never be used")

    def boom_open(*_a, **_k):
        raise OSError("no pidfd")

    monkeypatch.setattr(harness.os, "kill", boom_kill, raising=False)
    _patch_pidfd(monkeypatch, harness, on_open=boom_open)
    with pytest.raises(harness.HarnessGateError):
        harness._pidfd_sigkill_verified_main(
            unit_name="unit.service",
            main_pid=1,
            main_ident=(1, 1),
            agent_ident=(2, 2),
        )


# --- the rendered unit preserves the effective event-budget ceiling ---------


def _exec_start(unit: str) -> str:
    lines = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
    assert len(lines) == 1
    return lines[0]


def test_print_service_unit_preserves_a_configured_event_budget(
    monkeypatch, capsys
) -> None:
    """A rendered unit must start the daemon the operator asked for.

    Dropping the flag renders a unit that silently runs under the source
    default, which is a different daemon from the one the operator configured
    on the command line they just ran.
    """
    from agent_run_supervisor.arsd import __main__ as arsd_main

    monkeypatch.setattr(
        arsd_main,
        "geteuid",
        lambda: (_ for _ in ()).throw(AssertionError("no euid")),
    )
    rc = arsd_main.main(
        [
            "--print-service-unit",
            "--agents-file",
            AGENTS_FILE,
            "--max-run-event-budget-bytes",
            "123456789",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    _required_lines(out)
    _forbid_root_system(out)
    assert "--max-run-event-budget-bytes 123456789" in _exec_start(out)


def test_print_service_unit_omits_the_flag_at_the_default(monkeypatch, capsys) -> None:
    """Default rendering is unchanged: the flag says nothing the default doesn't."""
    from agent_run_supervisor.arsd import __main__ as arsd_main

    monkeypatch.setattr(
        arsd_main,
        "geteuid",
        lambda: (_ for _ in ()).throw(AssertionError("no euid")),
    )
    for argv in (
        ["--print-service-unit", "--agents-file", AGENTS_FILE],
        [
            "--print-service-unit",
            "--agents-file",
            AGENTS_FILE,
            "--max-run-event-budget-bytes",
            str(4 * 1024 * 1024 * 1024),
        ],
    ):
        assert arsd_main.main(argv) == 0
        assert "--max-run-event-budget-bytes" not in _exec_start(
            capsys.readouterr().out
        )


def test_print_service_unit_refuses_an_unusable_event_budget(
    monkeypatch, capsys
) -> None:
    """Never render a unit whose ExecStart the daemon would refuse to start."""
    from agent_run_supervisor.arsd import __main__ as arsd_main
    from agent_run_supervisor.native_acp.spec import (
        STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES,
    )

    monkeypatch.setattr(
        arsd_main,
        "geteuid",
        lambda: (_ for _ in ()).throw(AssertionError("no euid")),
    )
    for bad in ("0", "-1", str(STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES + 1)):
        rc = arsd_main.main(
            [
                "--print-service-unit",
                "--agents-file",
                AGENTS_FILE,
                "--max-run-event-budget-bytes",
                bad,
            ]
        )
        captured = capsys.readouterr()
        assert rc == 2
        assert captured.out == ""
        assert "budget" in captured.err.lower()


def test_the_renderer_emits_the_budget_flag_as_argv_data() -> None:
    from agent_run_supervisor.arsd.service_unit import (
        ServiceUnitError,
        render_service_unit,
    )
    from agent_run_supervisor.native_acp.spec import (
        STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES,
    )

    unit = render_service_unit(
        agents_file=AGENTS_FILE, max_run_event_budget_bytes=123456789
    )
    assert "--max-run-event-budget-bytes 123456789" in _exec_start(unit)
    # Omission keeps today's exact ExecStart.
    assert "--max-run-event-budget-bytes" not in _exec_start(
        render_service_unit(agents_file=AGENTS_FILE)
    )
    # The exact structural bound renders; anything past it is refused.
    assert str(STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES) in _exec_start(
        render_service_unit(
            agents_file=AGENTS_FILE,
            max_run_event_budget_bytes=STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES,
        )
    )
    for bad in (0, -1, True, "123456789", 1.0, STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES + 1):
        with pytest.raises(ServiceUnitError):
            render_service_unit(
                agents_file=AGENTS_FILE, max_run_event_budget_bytes=bad
            )

"""WP3.10 — the daemon operand is the agents file, and startup order is fixed.

``--agents-file`` replaces ``--binding-root`` in both doors: daemon mode and
unit rendering. Both are required, because a unit rendered without one installs
a daemon that can resolve no agent at all, and a daemon started without one has
nothing to resolve against.

Startup order is strictly sequential and fail-closed at every step:

1. resolve and parse the agents file once into an immutable snapshot — any
   defect refuses to listen, **before any state write**;
2. reconcile durable Run and Session facts — any fail-closed rule refuses to
   listen;
3. bind the Unix socket (``0600`` inside a ``0700`` directory) and accept.

The operand admission rule itself is unchanged and is **not duplicated**: the
entry in :mod:`agent_run_supervisor.arsd.operand` is re-pointed, and the same
one function serves argv and the programmatic door.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import operand, service_unit
from agent_run_supervisor.arsd.operand import OperandError, capture_agents_file
from agent_run_supervisor.arsd.service_unit import ServiceUnitError, render_service_unit

from tests.native_acp import registry_fixtures as fx

MAIN_SOURCE = (
    Path(service_unit.__file__).resolve().parent / "__main__.py"
)
AGENTS_FILE = "/etc/agent-run-supervisor/agents.toml"


# -- the operand rule is re-pointed, not duplicated --------------------------


def test_the_binding_root_entry_is_gone_and_the_agents_file_entry_is_here():
    assert not hasattr(operand, "capture_binding_root")
    assert callable(capture_agents_file)
    assert "capture_agents_file" in operand.__all__


def test_the_shape_rule_is_unchanged():
    assert capture_agents_file(AGENTS_FILE) == AGENTS_FILE
    assert capture_agents_file(Path(AGENTS_FILE)) == AGENTS_FILE
    for bad in ("", "   ", "relative/agents.toml", "a\nb", "\x7f/x"):
        with pytest.raises(OperandError):
            capture_agents_file(bad)


def test_the_type_identity_rule_is_unchanged():
    class Lying(str):
        def __str__(self) -> str:  # pragma: no cover - must never be admitted
            return AGENTS_FILE

    with pytest.raises(OperandError):
        capture_agents_file(Lying("/x"))
    with pytest.raises(OperandError):
        capture_agents_file(b"/x")
    with pytest.raises(OperandError):
        capture_agents_file(None)


def test_there_is_exactly_one_capture_function():
    """One rule, two doors. A second copy is how the weaker one becomes the real one."""
    tree = ast.parse(Path(operand.__file__).read_text(encoding="utf-8"))
    captures = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("capture_")
    ]
    assert captures == ["capture_agents_file"]


def test_the_operand_module_still_asks_the_filesystem_nothing():
    text = Path(operand.__file__).read_text(encoding="utf-8")
    for banned in ("os.stat", "os.lstat", "os.open", "resolve()", "exists", "is_file"):
        assert banned not in text


# -- unit rendering ----------------------------------------------------------


def test_rendering_requires_the_agents_file():
    with pytest.raises(ServiceUnitError):
        render_service_unit(python_executable="/usr/bin/python3")


def test_a_rendered_unit_carries_the_agents_file_and_not_a_binding_root():
    unit = render_service_unit(
        python_executable="/usr/bin/python3", agents_file=AGENTS_FILE
    )
    assert "--agents-file" in unit
    assert AGENTS_FILE in unit
    assert "--binding-root" not in unit


def test_the_renderer_signature_carries_no_binding_root():
    parameters = inspect.signature(render_service_unit).parameters
    assert "agents_file" in parameters
    assert "binding_root" not in parameters


def test_the_renderer_preserves_the_operator_spelling_byte_for_byte():
    spelled = "/srv/ars/agents.toml"
    unit = render_service_unit(
        python_executable="/usr/bin/python3", agents_file=spelled
    )
    assert f"--agents-file {spelled}" in unit


def test_the_renderer_refuses_a_relative_or_injecting_agents_file():
    for bad in ("agents.toml", "%h/agents.toml", "/x\nExecStartPost=/bin/true"):
        with pytest.raises(ServiceUnitError):
            render_service_unit(python_executable="/usr/bin/python3", agents_file=bad)


# -- daemon mode -------------------------------------------------------------


def load_main():
    import agent_run_supervisor.arsd.__main__ as main

    return main


def test_the_daemon_flag_is_agents_file():
    main = load_main()
    parser = main.build_arg_parser()
    options = {
        option for action in parser._actions for option in action.option_strings
    }
    assert "--agents-file" in options
    assert "--binding-root" not in options


def test_daemon_mode_refuses_without_an_agents_file(capsys):
    main = load_main()
    code = main.main(
        [
            "--supervisor-root",
            "/tmp/ars-supervisor-root",
            "--caller-mapping",
            "1000:p:o:ns",
        ]
    )
    assert code == 2
    assert "--agents-file" in capsys.readouterr().err


def test_print_mode_refuses_without_an_agents_file(capsys):
    main = load_main()
    assert main.main(["--print-service-unit"]) == 2
    assert "--agents-file" in capsys.readouterr().err


def test_print_mode_renders_the_agents_file(capsys):
    main = load_main()
    assert main.main(["--print-service-unit", "--agents-file", AGENTS_FILE]) == 0
    out = capsys.readouterr().out
    assert "--agents-file" in out
    assert AGENTS_FILE in out


def test_serve_daemon_refuses_without_an_agents_file(tmp_path):
    main = load_main()
    policy = main.build_caller_policy(["1000:p:o:ns"])
    with pytest.raises(main.DaemonStartupError) as excinfo:
        asyncio.run(
            main.serve_daemon(
                socket_path=str(tmp_path / "s.sock"),
                supervisor_root=str(tmp_path / "root"),
                policy=policy,
                install_signals=False,
            )
        )
    assert "--agents-file" in str(excinfo.value)


def test_serve_daemon_refuses_a_defective_agents_file_before_any_state_write(tmp_path):
    """Step 1 fails closed, and nothing downstream of it has run."""
    main = load_main()
    policy = main.build_caller_policy(["1000:p:o:ns"])
    root = tmp_path / "root"
    conf = tmp_path / "conf"
    conf.mkdir()
    agents = fx.write_registry(conf, text="schema_version = 99\n")
    with pytest.raises(main.DaemonStartupError) as excinfo:
        asyncio.run(
            main.serve_daemon(
                socket_path=str(tmp_path / "run" / "s.sock"),
                supervisor_root=str(root),
                policy=policy,
                agents_file=str(agents),
                install_signals=False,
            )
        )
    assert "REGISTRY_SCHEMA_VERSION" in str(excinfo.value)
    assert not root.exists(), "the supervisor root was created before the parse"
    assert not (tmp_path / "run").exists()


def test_serve_daemon_refuses_an_agents_file_inside_a_daemon_owned_surface(tmp_path):
    """ARS never writes into the operator's registry location, by layout too."""
    main = load_main()
    policy = main.build_caller_policy(["1000:p:o:ns"])
    root = tmp_path / "root"
    root.mkdir()
    agents = fx.write_registry(root)
    with pytest.raises(main.DaemonStartupError) as excinfo:
        asyncio.run(
            main.serve_daemon(
                socket_path=str(tmp_path / "run" / "s.sock"),
                supervisor_root=str(root),
                policy=policy,
                agents_file=str(agents),
                install_signals=False,
            )
        )
    assert "overlaps the supervisor root" in str(excinfo.value)


def test_sibling_directories_under_one_parent_are_a_valid_layout(tmp_path):
    """``~/ars/agents.toml`` beside ``~/ars/state`` is ordinary and admitted."""
    main = load_main()
    conf = tmp_path / "conf"
    conf.mkdir()
    agents = fx.write_registry(conf)
    assert not main._agents_file_query_conflict(str(agents), str(tmp_path / "state"))
    assert not main._agents_file_query_conflict(str(agents), str(tmp_path / "run"))


def test_serve_daemon_parses_then_reconciles_then_binds(tmp_path, monkeypatch):
    """The order *is* the guard, so it is asserted as an order."""
    main = load_main()
    order: list[str] = []

    from agent_run_supervisor.native_acp import agent_registry

    real_load = agent_registry.load_agents_file

    def watched_load(path):
        order.append("parse")
        return real_load(path)

    def watched_reconcile(root):
        order.append("reconcile")

    async def watched_start(self):
        order.append("bind")
        raise main.server.ServerStartupError("stop here")

    monkeypatch.setattr(main.agent_registry, "load_agents_file", watched_load)
    monkeypatch.setattr(main.reconcile, "reconcile", watched_reconcile)
    monkeypatch.setattr(main.server.ArsdServer, "start", watched_start)

    agents = fx.write_registry(tmp_path)
    policy = main.build_caller_policy(["1000:p:o:ns"])
    with pytest.raises(main.DaemonStartupError):
        asyncio.run(
            main.serve_daemon(
                socket_path=str(tmp_path / "run" / "s.sock"),
                supervisor_root=str(tmp_path / "root"),
                policy=policy,
                agents_file=str(agents),
                install_signals=False,
            )
        )
    assert order == ["parse", "reconcile", "bind"]


def test_the_registry_is_never_opened_again_after_startup(tmp_path, monkeypatch):
    main = load_main()
    opens: list[str] = []
    from agent_run_supervisor.native_acp import agent_registry

    real_load = agent_registry.load_agents_file

    def counting_load(path):
        opens.append(str(path))
        return real_load(path)

    async def stop_at_bind(self):
        raise main.server.ServerStartupError("stop here")

    monkeypatch.setattr(main.agent_registry, "load_agents_file", counting_load)
    monkeypatch.setattr(main.server.ArsdServer, "start", stop_at_bind)

    agents = fx.write_registry(tmp_path)
    policy = main.build_caller_policy(["1000:p:o:ns"])
    with pytest.raises(main.DaemonStartupError):
        asyncio.run(
            main.serve_daemon(
                socket_path=str(tmp_path / "run" / "s.sock"),
                supervisor_root=str(tmp_path / "root"),
                policy=policy,
                agents_file=str(agents),
                install_signals=False,
            )
        )
    assert opens == [str(agents)]


def test_the_daemon_entrypoint_names_no_binding_root():
    text = MAIN_SOURCE.read_text(encoding="utf-8")
    assert "binding_root" not in text
    assert "--binding-root" not in text
    assert "capture_binding_root" not in text

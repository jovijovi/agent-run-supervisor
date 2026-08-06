"""The opt-in real-agent harnesses must speak the contract they advertise.

These suites are skipped by ``make verify`` — they need a live agent or a live
socket — so nothing else in the repository notices when the wire moves under
them. That is exactly how a harness rots into a false promise: it still claims
to prove the real-agent path, and it would fail at the first frame.

So the *request shapes and orchestration* of those harnesses are exercised here,
by default, with no agent and no socket. This does not run the canaries. It runs
the builders and the sequencing they use, and holds them to the v3 contract:
select ``agent_id``, omit ``session_id`` on the first Run, and reuse the id that
Run actually returned.
"""

from __future__ import annotations

import ast
import builtins
import inspect
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import protocol

pytest.importorskip("acp")

import tests.arsd.test_real_socket_acceptance as socket_acceptance
import tests.native_acp.test_real_opencode_smoke as opencode_smoke


# -- the native opt-in smoke -------------------------------------------------


def test_the_native_smoke_builds_a_create_request_with_no_session_id() -> None:
    """The first Run of a real smoke has no Session to name yet."""
    request = opencode_smoke._request(
        model="provider/model", effort="high", session_id=None
    )
    assert request.session_id is None
    assert request.agent_id
    assert not hasattr(request, "profile_id")


def test_the_native_smoke_builds_a_reuse_request_from_a_returned_id() -> None:
    request = opencode_smoke._request(
        model="provider/model", effort="high", session_id="sess-from-run-1"
    )
    assert request.session_id == "sess-from-run-1"


def test_the_native_smoke_never_names_a_profile_or_a_reuse_mode() -> None:
    source = Path(opencode_smoke.__file__).read_text(encoding="utf-8")
    for retired in ("profile_id=", "session_reuse", "ars_session_id"):
        assert retired not in source, retired


def test_both_suites_continue_run_one_rather_than_inventing_an_id() -> None:
    """Structural: a multi-Run leg must consume its own first Run's identity.

    An invented Session id is the exact failure this guards: a leg that names a
    Session nothing created fails at admission, so it can never have proven the
    continuity it advertises.
    """
    native = inspect.getsource(opencode_smoke)
    assert "session_id = r1.session_id" in native, (
        "the native smoke must reuse the Session identity its first Run returned"
    )
    assert "smoke-s1" not in native and "smoke-s2" not in native, (
        "an invented Session id survives in the native smoke"
    )

    socket = inspect.getsource(socket_acceptance)
    assert 'session_id = a["session_id"]' in socket, (
        "the socket suite must reuse the Session identity its first Run returned"
    )
    assert "arsd-accept-s" not in socket, (
        "an invented Session id survives in the socket suite"
    )


# -- the arsd socket acceptance ----------------------------------------------


@pytest.fixture()
def acceptance_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env the socket builder reads. No socket, no daemon, no agent."""
    for name, value in (
        ("ARS_ARSD_ACCEPTANCE_OWNER", "hermes"),
        ("ARS_ARSD_ACCEPTANCE_NAMESPACE", "hermes/acceptance"),
        ("ARS_ARSD_ACCEPTANCE_AGENT_ID", "acceptance-agent"),
        ("ARS_ARSD_ACCEPTANCE_WORKSPACE", "/tmp/acceptance-ws"),
    ):
        monkeypatch.setenv(name, value)


def test_the_socket_payload_builder_omits_session_id_for_a_create(
    acceptance_env,
) -> None:
    payload = socket_acceptance._request_payload(
        model="provider/model", effort="high", session_id=None
    )
    assert "session_id" not in payload, "a create omits the field entirely"
    assert payload["agent_id"]
    assert "profile_id" not in payload


def test_the_socket_payload_builder_carries_a_returned_id_for_a_reuse(
    acceptance_env,
) -> None:
    payload = socket_acceptance._request_payload(
        model="provider/model", effort="high", session_id="sess-from-run-1"
    )
    assert payload["session_id"] == "sess-from-run-1"


def test_the_socket_payload_parses_under_the_production_v3_parser(
    acceptance_env,
) -> None:
    """The strongest available check without a socket: the real parser."""
    payload = socket_acceptance._request_payload(
        model="provider/model", effort="high", session_id=None
    )
    command = protocol.parse_submit(
        {
            "request": payload,
            "prompt_text": "hello",
            "workspace_root": "/tmp/ws",
        }
    )
    assert command.request.session_id is None
    assert command.request.agent_id


def test_the_socket_suite_expects_only_the_served_api_version() -> None:
    """No pinned old version, and the live-ness check reads the constant.

    Reading the constant is stronger than pinning a literal: the assertion
    cannot go stale the next time the wire moves.
    """
    source = Path(socket_acceptance.__file__).read_text(encoding="utf-8")
    assert 'info["api_version"] == 1' not in source
    assert 'info["api_version"] == 2' not in source
    assert 'info["api_version"] == protocol.ARSD_API_VERSION' in source


def test_neither_opt_in_suite_still_speaks_a_retired_wire() -> None:
    for module in (opencode_smoke, socket_acceptance):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for retired in ('"profile_id"', "session_reuse", "ars_session_id"):
            assert retired not in source, f"{module.__name__}: {retired}"


# -- variable-flow integrity of the skipped harness bodies -------------------
#
# A skipped suite is never executed, so an undefined local in one of its bodies
# is invisible to every gate: `compileall` accepts it, and pytest never runs it.
# It surfaces only when an operator finally starts a real canary — which is the
# worst moment to discover the harness cannot run at all.
#
# So the bodies are analysed instead of executed. For each function, every name
# it *loads* must be bound somewhere the function can see: its own parameters or
# assignments, a module global, or a builtin. That is exactly the class the
# review found (`idem_sess`, left dangling when its assignment moved), and it
# generalizes to any equivalent variable-flow break.


def _bound_names(function: ast.AST) -> set[str]:
    """Every name the function binds, anywhere inside it.

    Deliberately generous: names bound in nested scopes count as bound here.
    That direction only ever *misses* an error, so the check cannot invent one
    on legitimate code — and the errors it does report are real.
    """
    bound: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.arguments):
            for arg in (*node.posonlyargs, *node.args, *node.kwonlyargs):
                bound.add(arg.arg)
            for extra in (node.vararg, node.kwarg):
                if extra is not None:
                    bound.add(extra.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def _statement_bindings(body: list[ast.stmt]) -> set[str]:
    """Names bound by a statement list, **without descending into a new scope**.

    A function body is a new scope, so its locals are not bindings of the block
    that contains it — that distinction is the whole point. Module-level
    ``if``/``try``/``for``/``with`` bodies are *not* new scopes, so they are
    walked: a name bound in one of them really is a module global.
    """
    names: set[str] = set()
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(statement.name)
            continue  # its body is a different scope
        for node in ast.walk(statement):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # A nested definition contributes only its own name; anything
                # inside it belongs to that scope.
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                names.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
    return names


def _module_scope(tree: ast.Module) -> set[str]:
    """Builtins plus the module's own top-level bindings — and nothing else.

    Walking the whole tree here would promote every function local to a module
    global, which silently answers "defined" for every name in the file and
    makes the analyzer incapable of failing.
    """
    return set(dir(builtins)) | _statement_bindings(tree.body)


def _analyzable_functions(tree: ast.Module) -> list[tuple[ast.AST, set[str]]]:
    """Every function to check, paired with the extra names its scope can see.

    Only *outermost* functions are checked, because ``_bound_names`` already
    covers everything nested inside one — including a closure's view of its
    enclosing function's locals. Analysing a nested function on its own would
    hide those bindings and invent errors.
    """
    found: list[tuple[ast.AST, set[str]]] = []
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((statement, set()))
        elif isinstance(statement, ast.ClassDef):
            class_names = _statement_bindings(statement.body)
            for member in statement.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append((member, class_names))
    return found


def _undefined_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = _module_scope(tree)
    problems: list[str] = []
    for function, extra in _analyzable_functions(tree):
        visible = module_names | extra | _bound_names(function)
        for inner in ast.walk(function):
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load):
                if inner.id not in visible:
                    problems.append(
                        f"{path.name}:{inner.lineno}: {function.name}() loads "
                        f"undefined name {inner.id!r}"
                    )
    return problems


@pytest.mark.parametrize(
    "module", [opencode_smoke, socket_acceptance], ids=lambda m: m.__name__
)
def test_every_opt_in_harness_body_has_intact_variable_flow(module) -> None:
    """A canary that cannot even run proves nothing when an operator starts it."""
    problems = _undefined_names(Path(module.__file__))
    assert problems == [], "\n".join(problems)


# -- the analyzer is itself under test ---------------------------------------
#
# A checker that cannot fail is worse than no checker: it reports "clean" and
# stops anyone looking. So the analyzer is held to the one thing it exists to
# do — reject a name bound only in a *different* function — and to the thing
# that would make it useless in the other direction, inventing errors in code
# that is fine.


def _analyze(tmp_path: Path, source: str) -> list[str]:
    module = tmp_path / "sample_harness.py"
    module.write_text(source, encoding="utf-8")
    return _undefined_names(module)


def test_the_analyzer_rejects_a_name_bound_only_in_another_function(
    tmp_path: Path,
) -> None:
    """The exact shape the real break had: an assignment that moved away.

    ``s5`` reads ``session_id``. Nothing binds it at module level and nothing
    binds it in ``s5`` — it is a local of ``s3``. Calling ``s5`` raises
    ``NameError``, so the analyzer must say so.
    """
    problems = _analyze(
        tmp_path,
        "def s3():\n"
        "    session_id = 'ok'\n"
        "    return session_id\n"
        "\n"
        "def s5():\n"
        "    return session_id\n",
    )

    assert len(problems) == 1, problems
    assert "s5()" in problems[0]
    assert "session_id" in problems[0]


def test_the_analyzer_accepts_code_that_is_actually_fine(tmp_path: Path) -> None:
    """No false positives, or the check gets switched off the first time it lies.

    Every binding form the real harnesses use: module constants, imports,
    closures over an enclosing function's locals, comprehension targets,
    ``with``/``for``/``except`` targets, walrus, and class methods.
    """
    problems = _analyze(
        tmp_path,
        "import os\n"
        "from pathlib import Path as _Path\n"
        "\n"
        "CONSTANT = 1\n"
        "if os.environ.get('X'):\n"
        "    CONDITIONAL = 2\n"
        "else:\n"
        "    CONDITIONAL = 3\n"
        "\n"
        "def outer(alpha, *rest, beta=None, **kw):\n"
        "    captured = alpha\n"
        "\n"
        "    def inner():\n"
        "        return captured + CONSTANT + CONDITIONAL\n"
        "\n"
        "    for item in rest:\n"
        "        _ = item\n"
        "    with open(os.devnull) as handle:\n"
        "        _ = handle\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError as err:\n"
        "        _ = err\n"
        "    squares = [value * 2 for value in rest]\n"
        "    if (walrus := len(squares)):\n"
        "        _ = walrus\n"
        "    return inner, beta, kw, _Path\n"
        "\n"
        "class Holder:\n"
        "    FIELD = 4\n"
        "\n"
        "    def method(self):\n"
        "        return self.FIELD + CONSTANT\n",
    )

    assert problems == []

"""Unit tests for the shared boundary-operand admission rule.

These are **implementation-detail** tests of ``arsd/operand.py`` and are not
sufficient evidence on their own — the product contract is proven at the two
surfaces that call this module (``tests/arsd/test_service_unit.py`` for the unit
renderer, ``tests/arsd/test_client_daemon.py`` for daemon startup). What lives
here is what belongs to neither caller: the admitted-type table, the read count,
refusal of a non-exact read result, the fixed refusal texts, and the module's
freedom from filesystem primitives.

Hermetic: no filesystem, no daemon, no renderer, no service.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path, PurePath, PurePosixPath

import pytest

from agent_run_supervisor.arsd import operand

from tests.arsd.test_service_unit import (
    _LYING_AGENTS_FILE_KINDS,
    _lying_agents_file,
)

# Never created; only its spelling is used.
AGENTS_FILE = "/etc/agent-run-supervisor/agents.toml"

_EXACT_PATH_TYPE = type(Path(os.sep))


# --- the admitted-type table, one row per calling surface -------------------


def test_exact_path_type_is_derived_from_the_platform_not_named() -> None:
    """``Path`` builds the concrete class; the module must not hardcode a name."""
    assert operand.EXACT_PATH_TYPE is _EXACT_PATH_TYPE
    assert operand.EXACT_PATH_TYPE is type(Path(os.sep))


def test_renderer_surface_admits_exact_str_only() -> None:
    """``render_service_unit`` declares ``str | None``; admission matches it."""
    assert operand.admit_exact_text("/opt/x", label="socket_path") == "/opt/x"
    with pytest.raises(operand.OperandError) as err:
        operand.admit_exact_text(Path("/opt/x"), label="socket_path")
    assert str(err.value) == "socket_path must be a plain str"


def test_daemon_surface_admits_exact_str_and_exact_path() -> None:
    """``serve_daemon`` declares ``Path | str``; admission matches that too."""
    assert (
        operand.admit_exact_text("/opt/x", label="supervisor root", allow_path=True)
        == "/opt/x"
    )
    assert (
        operand.admit_exact_text(
            Path("/opt/x"), label="supervisor root", allow_path=True
        )
        == "/opt/x"
    )


def test_a_pure_path_is_not_the_concrete_path_type() -> None:
    """The union means the class ``Path()`` produces, not any ``PurePath``."""
    with pytest.raises(operand.OperandError):
        operand.admit_exact_text(
            PurePosixPath("/opt/x"), label="supervisor root", allow_path=True
        )


@pytest.mark.parametrize("kind", _LYING_AGENTS_FILE_KINDS)
def test_rejected_operands_are_never_touched(kind: str) -> None:
    """No hook runs on a refused operand: not text, repr, truth, equality, or iteration.

    ``metaclass_equality_liar`` and the ``__class__``-property liar are the two
    that a membership test or a subclass check would admit; the ``PathLike``
    object offers every remaining hook a gate might reach for.
    """
    hostile, probes = _lying_agents_file(kind, "/tmp/ars-daemon-owned/sv")
    with pytest.raises(operand.OperandError):
        operand.admit_exact_text(hostile, label="supervisor root", allow_path=True)
    assert probes == []

    hostile, probes = _lying_agents_file(kind, "/tmp/ars-daemon-owned/sv")
    with pytest.raises(operand.OperandError):
        operand.capture_agents_file(hostile)
    assert probes == []


# --- exactly one read, and the result of that read is admitted too ----------


def test_an_exact_str_is_returned_without_any_read() -> None:
    """An exact ``str`` already *is* the text; re-reading it could only weaken it."""
    text = "/opt/ars-binding"
    admitted = operand.admit_exact_text(text, label="agents file", allow_path=True)
    assert admitted is text


def test_an_exact_path_is_read_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One textual read, counted on the instance so unrelated paths cannot pollute it."""
    target = Path("/opt/ars-binding")
    reads: list[str] = []
    real_str = PurePath.__str__

    def counting(self):
        if self is target:
            reads.append("__str__")
        return real_str(self)

    monkeypatch.setattr(PurePath, "__str__", counting)
    text = operand.admit_exact_text(target, label="agents file", allow_path=True)
    monkeypatch.undo()

    assert reads == ["__str__"]
    assert type(text) is str
    assert text == "/opt/ars-binding"


def test_capture_agents_file_reads_an_exact_path_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape rules run against the frozen text, never against the operand."""
    target = Path(AGENTS_FILE)
    reads: list[str] = []
    real_str = PurePath.__str__

    def counting(self):
        if self is target:
            reads.append("__str__")
        return real_str(self)

    monkeypatch.setattr(PurePath, "__str__", counting)
    text = operand.capture_agents_file(target)
    monkeypatch.undo()

    assert reads == ["__str__"]
    assert type(text) is str
    assert text == AGENTS_FILE


def test_a_non_exact_read_result_is_refused_not_coerced() -> None:
    """The operand's type was exact; what its read *returned* was not.

    ``PyObject_Str`` validates the result with a check that admits subclasses,
    and a concrete path keeps its text in an assignable slot. Coercing the result
    a second time would accept text the operand chose, after having run it.
    """
    poison = type("_PoisonText", (str,), {"__str__": lambda self: self})(
        "/opt/ars-binding"
    )
    node = Path("/opt/ars-binding")
    str(node)
    try:
        node._str = poison  # type: ignore[attr-defined]
    except AttributeError:
        pytest.skip("PurePath._str is not assignable on this interpreter")
    if str(node) is not poison:
        pytest.skip(
            "this interpreter does not return the poisoned PurePath._str from "
            "str(); the premise of this test does not hold here"
        )

    assert type(node) is operand.EXACT_PATH_TYPE
    with pytest.raises(operand.OperandError) as err:
        operand.admit_exact_text(node, label="agents file", allow_path=True)
    assert str(err.value) == "agents file did not read back as a plain str"


# --- the shape rules live in the capture, not in the admission --------------


def test_admit_exact_text_applies_no_shape_rules() -> None:
    """Blank and control-bearing text are admissible *as text*.

    Keeping non-empty/control-free/absolute in ``capture_agents_file`` alone is
    what keeps an empty ``supervisor_root`` reporting ``"absolute"`` at the
    daemon rather than acquiring a new message here.
    """
    for text in ("", "   ", "relative/sv", "with\nnewline"):
        assert (
            operand.admit_exact_text(
                text, label="supervisor root", allow_path=True
            )
            is text
        )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "agents file must be a non-empty absolute path"),
        ("   ", "agents file must be a non-empty absolute path"),
        ("/opt/b\nExecStartPre=/bin/evil", "agents file contains control characters"),
        ("/opt/b\x7f", "agents file contains control characters"),
        ("relative/b", "agents file must be an absolute path"),
        ("~/b", "agents file must be an absolute path"),
    ],
)
def test_capture_agents_file_shape_messages_are_fixed(
    value: str, message: str
) -> None:
    """Byte-identical to the texts this boundary has always reported."""
    with pytest.raises(operand.OperandError) as err:
        operand.capture_agents_file(value)
    assert str(err.value) == message


def test_refusal_texts_quote_no_spelling_of_the_operand() -> None:
    """Reporting the operand would print back text its own code chose."""
    secret = "/opt/ars-operator-secret-layout"
    hostile, _probes = _lying_agents_file("pathlike_object", secret)
    with pytest.raises(operand.OperandError) as err:
        operand.capture_agents_file(hostile)
    problem = str(err.value)
    assert problem == "agents file must be a plain str or Path"
    assert secret not in problem
    assert AGENTS_FILE not in problem


def test_capture_agents_file_accepts_and_freezes_operator_spelling() -> None:
    """Frozen exact ``str``, and the operator's spelling survives unrewritten."""
    for text in ("/opt/x/", "/srv//x", "/opt/ars-binding"):
        captured = operand.capture_agents_file(text)
        assert type(captured) is str
        assert captured == text
    # A concrete path operand still yields plain text, not another path.
    captured = operand.capture_agents_file(Path(AGENTS_FILE))
    assert type(captured) is str


# --- the module itself ------------------------------------------------------


_FORBIDDEN_IN_OPERAND = (
    "resolve",
    "stat",
    "lstat",
    "readlink",
    "os.open",
    "access",
    "listdir",
    "scandir",
    "realpath",
    "exists",
    "is_dir",
    "fspath",
    "environ",
    "getcwd",
    "isinstance",
)


def test_operand_module_touches_no_filesystem_or_process_primitive() -> None:
    """Asserted against the source text, so prose cannot drift from behaviour.

    ``isinstance`` is in the list for the same reason as the rest: it is the one
    spelling of the type question that a caller can answer.
    """
    source = Path(operand.__file__).read_text(encoding="utf-8")
    found = [token for token in _FORBIDDEN_IN_OPERAND if token in source]
    assert found == []


def test_operand_module_is_a_leaf() -> None:
    """Imports ``os`` and ``pathlib`` only — no package import, so no cycle.

    Parsed rather than pattern-matched: a line of prose that happens to begin
    with ``from`` is not an import, and this guard must not be satisfiable (or
    breakable) by rewording a docstring.
    """
    source = Path(operand.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert sorted(imported) == ["__future__", "os", "pathlib"]
    assert "agent_run_supervisor" not in source

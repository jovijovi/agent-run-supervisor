#!/usr/bin/env python3
"""Static safety scan for release/acceptance gates.

The scan is intentionally conservative and repo-specific:

- secret-shaped values across tracked text files, with documented synthetic
  redaction samples allow-listed only when nearby text marks them as fake;
- dangerous runtime calls/imports in ``src/`` via Python AST;
- stale acceptance phrases that previously survived green functional tests;
- acpx containment: the removed runtime, package path, CLI leaf, fixture tree,
  and product claim may not come back.

It is not a replacement for focused review; it is a CI backstop for the exact
classes of drift this repository has already hit.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".ndjson",
    ".svg",
    ".txt",
}
TEXT_NAMES = {"LICENSE", "AGENTS.md", "GOAL.md", "CLAUDE.md"}
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}

FORBIDDEN_IMPORT_ROOTS = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "feishu",
    "lark",
    "sachima",
    "gateway",
    "temporalio",
}

SYNTHETIC_SECRET_PATHS = {
    Path("docs/plans/archive/2026-06-01-h1-operational-hardening.md"),
}

SECRET_PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    "github_pat": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "bearer_long": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{30,}\b"),
}

STALE_PATTERNS = {
    "ready_for_pr": re.compile(r"ready[- ]for[- ]PR", re.I),
    "implementation_candidate": re.compile(r"implementation candidate", re.I),
    "closure_requires_pr": re.compile(r"closure requires PR", re.I),
    "pr_ci_pending": re.compile(r"PR/CI pending|pending PR review|waiting for CI", re.I),
    "session_unimplemented_tail": re.compile(
        r"S1\s+(?:remain|remains)\s+Planned|"
        r"S1 session support .*unimplemented|"
        r"support remains Planned|"
        r"persistent-session runtime is not implemented",
        re.I,
    ),
    "red_expectation_tail": re.compile(r"RED\s+expectation", re.I),
    "old_test_count_455": re.compile(r"\b455\s+(?:passed|tests|collected)\b", re.I),
}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str
    snippet: str


def _line_number(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _snippet(text: str, line: int) -> str:
    lines = text.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:180]
    return ""


def iter_text_files(root: Path, *, suffixes: set[str] = TEXT_SUFFIXES) -> Iterable[Path]:
    """Walk tracked text files under ``root``.

    ``suffixes`` widens the walk for a single category without widening it for
    the others: the acpx containment scan also has to read shell gates, and
    letting the secret/stale scans start reading them would be a silent change
    to what those two report.
    """
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        # Build artifacts are not repository content for any category. ``dist``
        # and ``build`` are named above; ``*.egg-info`` is generated beside the
        # sources it lists, so it needs a suffix rule rather than a name.
        if any(
            part in SKIP_PARTS or part.endswith(".egg-info") for part in rel.parts
        ):
            continue
        if path.suffix in suffixes or path.name in TEXT_NAMES or path.name.startswith(".env"):
            yield path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _is_allowlisted_synthetic_secret(root: Path, path: Path, text: str, pos: int) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if rel not in SYNTHETIC_SECRET_PATHS:
        return False
    nearby = text[max(0, pos - 900) : pos].lower()
    return any(marker in nearby for marker in ("synthetic", "non-secret", "fabricated", "fake"))


def scan_secrets(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_text_files(root):
        text = _read(path)
        for name, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                if _is_allowlisted_synthetic_secret(root, path, text, match.start()):
                    continue
                line = _line_number(text, match.start())
                findings.append(Finding(str(path.relative_to(root)), line, f"secret:{name}", _snippet(text, line)))
    return findings


def _import_roots(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name.split(".")[0]
    elif isinstance(node, ast.ImportFrom):
        yield (node.module or "").split(".")[0]


def scan_source_ast(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in (root / "src").rglob("*.py"):
        rel = path.relative_to(root)
        text = _read(path)
        try:
            tree = ast.parse(text, filename=str(rel))
        except SyntaxError as exc:
            findings.append(Finding(str(rel), exc.lineno or 1, "syntax_error", exc.msg))
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _import_roots(node):
                    if module in FORBIDDEN_IMPORT_ROOTS:
                        findings.append(
                            Finding(str(rel), getattr(node, "lineno", 1), f"forbidden_import:{module}", _snippet(text, getattr(node, "lineno", 1)))
                        )
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    obj = func.value.id if isinstance(func.value, ast.Name) else None
                    if obj == "os" and func.attr == "system":
                        findings.append(Finding(str(rel), node.lineno, "dangerous_call:os.system", _snippet(text, node.lineno)))
                    if obj == "pickle" and func.attr in {"load", "loads"}:
                        findings.append(Finding(str(rel), node.lineno, f"dangerous_call:pickle.{func.attr}", _snippet(text, node.lineno)))
                elif isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
                    findings.append(Finding(str(rel), node.lineno, f"dangerous_call:{func.id}", _snippet(text, node.lineno)))
                for keyword in node.keywords:
                    if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                        findings.append(Finding(str(rel), node.lineno, "dangerous_call:shell=True", _snippet(text, node.lineno)))
    return findings


# Environment-value sink boundary. Names that hold, or stand for, a projected
# environment mapping anywhere in ``src/``.
ENV_MAPPING_NAMES = {
    "env",
    "spawn_env",
    "launch_env",
    "exec_mapping",
    "environ",
    "fixed_env",
    "permission_env",
    "resolved_env",
}

def scan_environment_value_sinks(root: Path) -> list[Finding]:
    """The structural rule that survives the removal of the per-Run guard.

    ARS keeps environment values out of its *structured* durable material and
    hands the resolved mapping to exactly one consumer, process spawn. The way
    that boundary is most easily lost is a value being rendered straight out of
    the mapping into a log line or an exception message, so rendering it stays
    unrepresentable rather than discouraged.
    """
    findings: list[Finding] = []
    src = root / "src"
    if src.is_dir():
        for path in sorted(src.rglob("*.py")):
            rel = path.relative_to(root)
            text = _read(path)
            try:
                tree = ast.parse(text, filename=str(rel))
            except SyntaxError:
                continue  # scan_source_ast already reports it
            findings.extend(_scan_env_rendering(rel, text, tree))
    return findings


def _scan_env_rendering(rel: Path, text: str, tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "repr"
            and any(
                isinstance(arg, ast.Name) and arg.id in ENV_MAPPING_NAMES
                for arg in node.args
            )
        ):
            findings.append(
                Finding(str(rel), node.lineno, "env_value:raw_repr", _snippet(text, node.lineno))
            )
        if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name):
            if node.value.id in ENV_MAPPING_NAMES:
                findings.append(
                    Finding(
                        str(rel),
                        node.lineno,
                        "env_value:interpolated_mapping",
                        _snippet(text, node.lineno),
                    )
                )
    return findings


# -- acpx containment ---------------------------------------------------------
#
# One category, four shapes. The rule is deliberately about *shape* rather than
# about the word: a document that names acpx in order to refuse it, or to record
# what was once true, is the exact prose the boundary statements in GOAL, the
# PRD, and the design layer are made of, and banning the token would ban them.
# What may not come back is an import of a removed module, an argv that names
# the acpx/npx binary, a reference to a removed repository surface, and a
# current document presenting a removed command, field, or runtime as available.

#: The retained package. A removed module is a direct child of it, so an import
#: is judged by resolving what it imports rather than by matching a spelling.
PACKAGE_ROOT = "agent_run_supervisor"

#: Modules deleted with the acpx runtime. Leaf names under ``agent_run_supervisor``.
REMOVED_PACKAGE_MODULES = frozenset(
    {
        "caller",
        "fixtures",
        "goal",
        "hermes_caller",
        "live_stream",
        "mcp_config",
        "parser",
        "policy",
        "preflight",
        "retention",
        "role",
        "runner",
        "session_inspect",
        "session_runtime",
        "workspace",
    }
)

#: CLI leaves deleted from the console script. ``run`` survives only as the
#: parent of ``inspect``, and ``doctor`` only as ``agents doctor``.
REMOVED_CLI_LEAVES = ("validate-role", "replay", "doctor", "session", "cleanup")

#: Repository surfaces the removal deleted. Naming one is naming something gone.
REMOVED_SURFACE_PATTERNS = (
    re.compile(r"fixtures/acpx-"),
    re.compile(r"\bacpx-0\.1[02]\.0\b"),
    re.compile(r"\bacpx-stdout\.ndjson\b"),
    re.compile(
        r"agent_run_supervisor/(?:%s)\.py" % "|".join(sorted(REMOVED_PACKAGE_MODULES))
    ),
    re.compile(
        r"scripts/(?:smoke_codex_acpx|smoke_persistent_session|capture_acpx_contract"
        r"|validate_contract_fixtures)\.py"
    ),
)

#: A removed command presented as invocable.
_REMOVED_CLI_INVOCATION = re.compile(
    r"agent[-_]run[-_]supervisor\s+(?:%s)\b" % "|".join(REMOVED_CLI_LEAVES)
)
#: ``run`` on its own is the deleted exec leaf; ``run inspect`` is supported.
_REMOVED_RUN_INVOCATION = re.compile(r"agent[-_]run[-_]supervisor\s+run\b(?!\s+inspect)")
#: A schema table row whose first cell is a backticked acpx field.
#: Where each rule applies. Cold archives, dated history, execution plans, and
#: the generated index are out of scope: history may record what was once true,
#: a plan must name what it removes, and the index is regenerated from titles.
_ACPX_CODE_ROOTS = ("src", "scripts", "tools", "tests")
_ACPX_SURFACE_ROOTS = ("src", "scripts", "tools")
_ACPX_AUTHORITY_DOC_FILES = frozenset(
    {"GOAL.md", "AGENTS.md", "README.md", "README.zh-CN.md", "docs/AI_FLOW.md"}
)
_ACPX_AUTHORITY_DOC_ROOTS = (
    "docs/product",
    "docs/design",
    "docs/roadmap",
    # An active plan is current authority in flight: it states what the project
    # is doing now, so it is scanned. Only the archive below is history.
    "docs/plans",
)
#: The exact cold-archive roots, and nothing else. History is allowed to record
#: what was once true; every other current-authority surface is judged.
_ACPX_EXCLUDED_DOC_ROOTS = (
    "docs/archive",
    "docs/plans/archive",
    "docs/roadmap/archive",
)
#: Operator-facing surfaces that advertise commands the same way a doc does.
_ACPX_COMMAND_SURFACE_ROOTS = ("scripts",)
_ACPX_COMMAND_SURFACE_FILES = frozenset({"Makefile"})
#: This file declares the rules; scan call sites, not the declarations.
_ACPX_SELF = Path("tools/static_safety_scan.py")


def _posix(rel: Path) -> str:
    return rel.as_posix()


def _under(rel: Path, roots: tuple[str, ...]) -> bool:
    text = _posix(rel)
    return any(text == root or text.startswith(root + "/") for root in roots)


def _is_authority_doc(rel: Path) -> bool:
    text = _posix(rel)
    if _under(rel, _ACPX_EXCLUDED_DOC_ROOTS):
        return False
    if text in _ACPX_AUTHORITY_DOC_FILES:
        return True
    return rel.suffix == ".md" and _under(rel, _ACPX_AUTHORITY_DOC_ROOTS)


def _is_command_surface(rel: Path) -> bool:
    return _posix(rel) in _ACPX_COMMAND_SURFACE_FILES or _under(
        rel, _ACPX_COMMAND_SURFACE_ROOTS
    )


# -- current-authority prose: statement-level default reject -------------------
#
# Every acpx-bearing *statement* in a current-authority document must be a
# member of ALLOWED_ACPX_STATEMENTS. Anything else is a finding.
#
# Two properties do the work, and both were missing before:
#
# *Statement, not clause.* Splitting on commas and semicolons let a compound
# sentence through — the allowlisted half satisfied the gate, and the half that
# made the claim carried no acpx token, so nothing examined it. A statement ends
# at a sentence terminator or a cell boundary, never at a comma, so the claim
# and its refusal are judged together or not at all.
#
# *Canonicalize before looking.* ``ac**px**`` is the token with emphasis inside
# it. Markup is stripped before the containment test, so a document cannot hide
# a mention from the gate by formatting it.
#
# Wrapped lines are joined into their block first, so a sentence that spans two
# source lines is one statement rather than two fragments.

#: Inline markup that decorates text without changing what it says. Stripped
#: *inside* words, which is what defeats ``ac**px**``.
_INLINE_MARKUP = re.compile(r"[`*_~]")
#: Block-leading markup: quote, heading, list bullet, ordered marker.
_LEADING_MARKUP = re.compile(r"^\s*(?:>+\s*|#+\s*|[-*+]\s+|\d+[.)]\s+)+")
#: A line that begins a new block rather than continuing the previous one.
_BLOCK_START = re.compile(r"^\s*(?:$|>|#{1,6}\s|[-*+]\s|\d+[.)]\s|\||```|~~~)")
#: A statement ends at a sentence terminator or a table-cell boundary.
_STATEMENT_SPLIT = re.compile(r"[.!?|]")

#: The complete set of acpx statements current authority may make. Each is a
#: removal, non-support, or history fact. Any other statement naming acpx — in
#: any document this gate scans — is a finding.
ALLOWED_ACPX_STATEMENTS = frozenset(
    {
        # GOAL.md — the boundary section and the removal facts.
        "acpx boundary",
        "acpx is not a supported product, runtime, fallback, or compatibility baseline",
        "the acpx runtime was removed from ars",
        "no acpx fixture was retained",
        # AGENTS.md / design — the same refusal where it is enforced.
        "acpx is not a product, runtime, or compatibility surface",
        "acpx was never a product, runtime, fallback, driver, compatibility layer, or session store",
        "the retired acpx path was removed from source",
        "the acpx-named keys were removed with it",
        # Roadmap — the recorded decision and its status.
        "acpx as native production driver, fallback, compatibility layer, or shared/imported session store",
        "source removal of the acpx product, runtime, and compatibility content",
        "not approved by that decision: expanding the acpx cleanup",
        "the acpx removal is implemented on a task branch",
        "the legacy acpx line",
    }
)


def _normalize_statement(text: str) -> str:
    return " ".join(_INLINE_MARKUP.sub("", text).lower().split()).strip(" -–—:;,")


def _canonical_statements(text: str) -> list[tuple[int, str]]:
    """Every statement in a Markdown document, with the line it starts on."""
    blocks: list[tuple[int, list[str]]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _BLOCK_START.match(line) or not blocks:
            blocks.append((number, [line]))
        else:
            blocks[-1][1].append(line)
    statements: list[tuple[int, str]] = []
    for number, block in blocks:
        joined = " ".join(_LEADING_MARKUP.sub("", part) for part in block)
        for piece in _STATEMENT_SPLIT.split(joined):
            normalized = _normalize_statement(piece)
            if normalized:
                statements.append((number, normalized))
    return statements


def _scan_prose_claims(rel: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[int] = set()
    for number, statement in _canonical_statements(text):
        if "acpx" not in statement or number in seen:
            continue
        if statement in ALLOWED_ACPX_STATEMENTS:
            continue
        seen.add(number)
        findings.append(
            Finding(str(rel), number, "acpx:doc_capability_claim", _snippet(text, number))
        )
    return findings


def _scan_removed_cli_leaves(rel: Path, text: str) -> list[Finding]:
    """A removed command presented as invocable, wherever commands are listed."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _REMOVED_CLI_INVOCATION.search(line) or _REMOVED_RUN_INVOCATION.search(line):
            findings.append(
                Finding(
                    str(rel),
                    number,
                    "acpx:doc_capability_claim",
                    line.strip()[:180],
                )
            )
    return findings


# -- imports: resolved against the scanned file's own package ------------------


def _package_of(rel: Path) -> tuple[str, ...]:
    """The dotted package the scanned file lives in, from its path.

    ``src/`` is a source root rather than a package, so it is dropped: a file at
    ``src/agent_run_supervisor/native_acp/x.py`` is in package
    ``agent_run_supervisor.native_acp``, while ``tests/helpers/x.py`` is in
    ``tests.helpers``. That difference is the whole point — ``from . import
    runner`` names a *different module* in each, and only one of them is the
    removed one.
    """
    parts = rel.parts[:-1]
    if parts and parts[0] == "src":
        parts = parts[1:]
    return parts


def _import_targets(node: ast.AST, package: tuple[str, ...]) -> list[str]:
    """Absolute dotted names an import statement resolves to, in this package."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level:
        # level 1 is the current package, 2 its parent, and so on.
        climbed = len(package) - (node.level - 1)
        if climbed < 0:
            return []
        base = list(package[:climbed])
    else:
        base = []
    prefix = base + ([node.module] if node.module else [])
    if not prefix:
        return []
    stem = ".".join(prefix)
    # ``from <pkg> import <name>`` imports ``<pkg>.<name>`` as well as ``<pkg>``.
    return [stem] + [f"{stem}.{alias.name}" for alias in node.names]


def _removed_module_of(dotted: str) -> str | None:
    """The removed module a resolved absolute name refers to, if any."""
    parts = dotted.split(".")
    if len(parts) < 2 or parts[0] != PACKAGE_ROOT:
        return None
    return parts[1] if parts[1] in REMOVED_PACKAGE_MODULES else None


def _scan_removed_imports(rel: Path, text: str, tree: ast.AST) -> list[Finding]:
    package = _package_of(rel)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if any(
            _removed_module_of(target) for target in _import_targets(node, package)
        ):
            findings.append(
                Finding(
                    str(rel),
                    node.lineno,
                    "acpx:removed_module_import",
                    _snippet(text, node.lineno),
                )
            )
    return findings


# -- argv: a bounded static evaluator over literal constructions ---------------
#
# The gate asks a process-starting call one question: *what executable does this
# argv name?* The previous rule asked instead whether an acpx token appeared
# anywhere in it, and that is wrong in both directions — it missed
# ``"acpx|exec".split("|")``, because it re-split the literal with the default
# separator and never saw the tokens, and it flagged
# ``["printf", "%s", "acpx"]``, where acpx is an ordinary argument to a
# different program.
#
# The evaluator is deliberately small: literal lists/tuples, a literal string,
# and ``str.split()`` with a literal separator, plus the module-level name
# indirection the gate already supported. It is not a Python interpreter, and
# anything it cannot evaluate statically is reported as unknown.

#: Executable basenames that may not be run.
_COMMAND_NAMES = frozenset({"acpx", "npx"})
#: Names that hold a command or its argument vector.
_ARGV_NAMES = frozenset(
    {"argv", "args", "cmd", "cmdline", "command", "executable", "exe"}
)
#: Callables that start a process. The first positional argument carries argv.
_SPAWN_FUNCTIONS = frozenset(
    {
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "spawn_managed_process",
        "create_subprocess_exec",
        "execv",
        "execvp",
        "execve",
    }
)
#: Keywords that name the executable or the vector explicitly.
_EXECUTABLE_KEYWORDS = frozenset({"executable"})


def _is_argv_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in _ARGV_NAMES or any(
        lowered.endswith("_" + suffix) for suffix in _ARGV_NAMES
    )


def _literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    return None


def _static_tokens(node: ast.AST, assigned: dict[str, ast.AST]) -> list[str] | None:
    """Evaluate an argv expression to its literal tokens, or ``None`` if unknown.

    ``None`` is a real answer and is kept distinct from ``[]``: it means the
    construction is dynamic, and the caller applies the conservative policy for
    that case rather than guessing at a runtime value.
    """
    literal = _literal_str(node)
    if literal is not None:
        return [literal]
    if isinstance(node, (ast.List, ast.Tuple)):
        tokens: list[str] = []
        for element in node.elts:
            value = _literal_str(element)
            if value is None:
                return None
            tokens.append(value)
        return tokens
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "split"
        and not node.keywords
    ):
        subject = _literal_str(node.func.value)
        if subject is None:
            return None
        if not node.args:
            return subject.split()
        separator = _literal_str(node.args[0])
        if separator is None or separator == "":
            return None
        return subject.split(separator)
    if isinstance(node, ast.Name) and node.id in assigned:
        return _static_tokens(assigned[node.id], dict(assigned, **{node.id: ast.Pass()}))
    return None


def _executes_a_removed_command(tokens: list[str], executable: str | None) -> bool:
    """Does this argv run the removed runtime?

    The executable is ``argv[0]`` unless an explicit ``executable=`` overrides
    it. ``npx`` counts only when it is being used to fetch and run acpx, which
    is how that runtime was actually invoked; ``npx`` running anything else is
    an ordinary tool call.
    """
    program = executable if executable is not None else (tokens[0] if tokens else None)
    if program is None:
        return False
    name = PurePosixPath(program.strip()).name
    if name == "acpx":
        return True
    if name != "npx":
        return False
    return any(
        PurePosixPath(token.strip()).name == "acpx" or token.startswith("acpx@")
        for token in tokens[1:]
    )


def _argv_constructions(
    node: ast.AST, assigned: dict[str, ast.AST]
) -> Iterable[tuple[list[str] | None, str | None]]:
    """``(tokens, explicit_executable)`` pairs this statement constructs."""
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if node.value is not None and any(_is_argv_name(name) for name in names):
            yield _static_tokens(node.value, assigned), None
        return
    if not isinstance(node, ast.Call):
        return
    func = node.func
    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    executable = None
    for keyword in node.keywords:
        if keyword.arg in _EXECUTABLE_KEYWORDS:
            executable = _literal_str(keyword.value)
    if called in _SPAWN_FUNCTIONS and node.args:
        first = _static_tokens(node.args[0], assigned)
        if first is not None and len(first) == 1 and len(node.args) > 1:
            # Varargs form: ``create_subprocess_exec(program, *args)``.
            rest = [_static_tokens(arg, assigned) for arg in node.args[1:]]
            if all(part is not None and len(part) == 1 for part in rest):
                first = first + [part[0] for part in rest]  # type: ignore[index]
        yield first, executable
    elif executable is not None:
        yield [], executable
    for keyword in node.keywords:
        if keyword.arg and _is_argv_name(keyword.arg) and keyword.arg not in _EXECUTABLE_KEYWORDS:
            yield _static_tokens(keyword.value, assigned), executable


def _module_assignments(tree: ast.AST) -> dict[str, ast.AST]:
    assigned: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value
    return assigned


def _scan_acpx_argv(rel: Path, text: str, tree: ast.AST) -> list[Finding]:
    assigned = _module_assignments(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        for tokens, executable in _argv_constructions(node, assigned):
            if tokens is None and executable is None:
                # Dynamic construction: not statically knowable, so not judged.
                continue
            if _executes_a_removed_command(tokens or [], executable):
                findings.append(
                    Finding(
                        str(rel),
                        getattr(node, "lineno", 1),
                        "acpx:argv_construction",
                        _snippet(text, getattr(node, "lineno", 1)),
                    )
                )
    return findings


def _scan_removed_surface_paths(rel: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for pattern in REMOVED_SURFACE_PATTERNS:
        for match in pattern.finditer(text):
            line = _line_number(text, match.start())
            findings.append(
                Finding(
                    str(rel), line, "acpx:removed_surface_path", _snippet(text, line)
                )
            )
    return findings


def scan_acpx_containment(root: Path) -> list[Finding]:
    """The removed acpx runtime, package path, CLI leaf, fixture, and claim."""
    findings: list[Finding] = []
    for path in iter_text_files(root, suffixes=TEXT_SUFFIXES | {".sh"}):
        rel = path.relative_to(root)
        if rel == _ACPX_SELF:
            continue
        text = _read(path)
        authority_doc = _is_authority_doc(rel)
        if path.suffix == ".py" and _under(rel, _ACPX_CODE_ROOTS):
            try:
                tree = ast.parse(text, filename=str(rel))
            except SyntaxError:
                tree = None  # scan_source_ast already reports it
            if tree is not None:
                findings.extend(_scan_removed_imports(rel, text, tree))
                if _under(rel, ("src",)):
                    findings.extend(_scan_acpx_argv(rel, text, tree))
        if _under(rel, _ACPX_SURFACE_ROOTS) or authority_doc:
            findings.extend(_scan_removed_surface_paths(rel, text))
        if authority_doc:
            findings.extend(_scan_prose_claims(rel, text))
        if authority_doc or _is_command_surface(rel):
            findings.extend(_scan_removed_cli_leaves(rel, text))
    # One line that violates one shape is one finding, however many patterns of
    # that shape it happens to match.
    seen: set[tuple[str, int, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.file, finding.line, finding.kind)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def scan_stale_phrases(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_text_files(root):
        rel = path.relative_to(root)
        if rel == Path("tools/static_safety_scan.py"):
            # This file defines the stale patterns; scan call sites and docs, not
            # the literal pattern declarations themselves.
            continue
        text = _read(path)
        for name, pattern in STALE_PATTERNS.items():
            for match in pattern.finditer(text):
                line = _line_number(text, match.start())
                snippet = _snippet(text, line)
                lowered = snippet.lower()
                if any(token in lowered for token in ("backlog", "not approved", "non-approval", "requires separate approval")):
                    continue
                findings.append(Finding(str(path.relative_to(root)), line, f"stale:{name}", snippet))
    return findings


def run_scan(root: Path) -> dict[str, object]:
    root = root.resolve()
    secret_findings = scan_secrets(root)
    ast_findings = scan_source_ast(root)
    stale_findings = scan_stale_phrases(root)
    env_findings = scan_environment_value_sinks(root)
    acpx_findings = scan_acpx_containment(root)
    findings = [
        *secret_findings,
        *ast_findings,
        *stale_findings,
        *env_findings,
        *acpx_findings,
    ]
    return {
        "ok": not findings,
        "counts": {
            "secret": len(secret_findings),
            "source_ast": len(ast_findings),
            "stale": len(stale_findings),
            "env_value_sink": len(env_findings),
            "acpx": len(acpx_findings),
            "total": len(findings),
        },
        "findings": [asdict(finding) for finding in findings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repo-specific static safety scans.")
    parser.add_argument("root", nargs="?", default=".", help="Repository root to scan")
    args = parser.parse_args(argv)
    report = run_scan(Path(args.root))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

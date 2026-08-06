from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import tools.static_safety_scan as static_safety_scan

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_static_safety_scan_passes_on_current_repository() -> None:
    report = static_safety_scan.run_scan(REPO_ROOT)

    assert report["ok"] is True
    assert report["counts"] == {
        "secret": 0,
        "source_ast": 0,
        "stale": 0,
        "env_value_sink": 0,
        "acpx": 0,
        "total": 0,
    }


def test_static_safety_scan_detects_environment_mapping_rendering(
    tmp_path: Path,
) -> None:
    """The two ways the resolved mapping stops being value-blind.

    ``ResolvedEnvironment`` has exactly one consumer, process spawn. The way
    that is most easily lost is a value rendered straight out of the mapping
    into a log line or an exception message, so both rendering forms are
    structurally refused.
    """
    package = tmp_path / "src" / "agent_run_supervisor"
    (package / "native_acp").mkdir(parents=True)
    (package / "leaky.py").write_text(
        'def report(env):\n    return f"env={env}" + repr(env)\n', encoding="utf-8"
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert report["ok"] is False
    kinds = {finding["kind"] for finding in report["findings"]}
    assert "env_value:raw_repr" in kinds
    assert "env_value:interpolated_mapping" in kinds


def test_static_safety_scan_detects_secret_danger_and_stale_phrase(tmp_path: Path) -> None:
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    stale_red_phrase = "RED " + "expectation: old TDD text\n"
    (tmp_path / "README.md").write_text(stale_red_phrase, encoding="utf-8")
    openai_project_key = "sk-" + "proj-" + "A" * 24
    (tmp_path / ".env").write_text("OPENAI_API_KEY=" + openai_project_key + "\n", encoding="utf-8")
    (src / "bad.py").write_text("import requests\nimport os\nos.system('echo unsafe')\n", encoding="utf-8")

    report = static_safety_scan.run_scan(tmp_path)

    assert report["ok"] is False
    kinds = {finding["kind"] for finding in report["findings"]}
    assert "secret:openai_key" in kinds
    assert "forbidden_import:requests" in kinds
    assert "dangerous_call:os.system" in kinds
    assert "stale:red_expectation_tail" in kinds


def test_static_safety_scan_cli_returns_nonzero_on_findings(tmp_path: Path) -> None:
    stale_pr_phrase = "ready-for" + "-PR\n"
    (tmp_path / "README.md").write_text(stale_pr_phrase, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "static_safety_scan.py"), str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["counts"]["stale"] == 1


# -- acpx containment ---------------------------------------------------------
#
# One scanner category, four shapes. Every input below is synthesized in a
# temporary tree at runtime: nothing violating is committed, and the category is
# proven non-vacuous without the repository ever carrying a violation.


def _kinds(report: dict) -> set[str]:
    return {finding["kind"] for finding in report["findings"]}


def test_acpx_scan_detects_an_absolute_removed_module_import(tmp_path: Path) -> None:
    package = tmp_path / "src" / "agent_run_supervisor" / "native_acp"
    package.mkdir(parents=True)
    (package / "leaf.py").write_text(
        "from agent_run_supervisor.runner import SupervisorRunner\n", encoding="utf-8"
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert report["ok"] is False
    assert "acpx:removed_module_import" in _kinds(report)
    assert report["counts"]["acpx"] == 1


def test_acpx_scan_detects_a_relative_removed_module_import(tmp_path: Path) -> None:
    """``from ..role import`` is the same reach as the absolute spelling."""
    package = tmp_path / "src" / "agent_run_supervisor" / "native_acp"
    package.mkdir(parents=True)
    (package / "spec.py").write_text(
        "from ..role import PERMISSION_KINDS\n", encoding="utf-8"
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:removed_module_import" in _kinds(report)


def test_acpx_scan_covers_tests_and_scripts_for_removed_imports(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_leftover.py").write_text(
        "import agent_run_supervisor.hermes_caller\n", encoding="utf-8"
    )
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "leftover.py").write_text(
        "from agent_run_supervisor.preflight import probe_acpx\n", encoding="utf-8"
    )

    report = static_safety_scan.run_scan(tmp_path)

    files = {finding["file"] for finding in report["findings"]}
    assert files == {"tests/test_leftover.py", "scripts/leftover.py"}
    assert report["counts"]["acpx"] == 2


def test_acpx_scan_detects_argv_construction(tmp_path: Path) -> None:
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "spawn.py").write_text(
        'ARGV = ["npx", "--yes", "acpx", "exec"]\n', encoding="utf-8"
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:argv_construction" in _kinds(report)


def test_acpx_scan_detects_a_packaged_fixture_read(tmp_path: Path) -> None:
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "commands.py").write_text(
        'REPLAY = "fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson"\n',
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:removed_surface_path" in _kinds(report)


def test_acpx_scan_detects_a_removed_surface_path_in_a_gate_script(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "verify_local.sh").write_text(
        "uv run python scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0\n",
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:removed_surface_path" in _kinds(report)


def test_acpx_scan_detects_a_removed_cli_leaf_in_current_docs(tmp_path: Path) -> None:
    roadmap = tmp_path / "docs" / "roadmap"
    roadmap.mkdir(parents=True)
    (roadmap / "verification.md").write_text(
        "```bash\n"
        "agent-run-supervisor replay events.ndjson\n"
        "agent-run-supervisor doctor\n"
        "agent-run-supervisor run --role role.json --prompt-file p.txt\n"
        "```\n",
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:doc_capability_claim" in _kinds(report)
    assert report["counts"]["acpx"] == 3


def test_acpx_scan_detects_an_acpx_result_field_row(tmp_path: Path) -> None:
    design = tmp_path / "docs" / "design"
    design.mkdir(parents=True)
    (design / "result-event-schema.md").write_text(
        "| Field | Type | Required | Notes |\n"
        "|---|---|---|---|\n"
        "| `acpx_exit_code` | `number` | yes | Observed process exit code. |\n",
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:doc_capability_claim" in _kinds(report)


def test_acpx_scan_keeps_the_three_supported_cli_leaves_legal(tmp_path: Path) -> None:
    """D2's surviving commands are documentation, not a violation."""
    roadmap = tmp_path / "docs" / "roadmap"
    roadmap.mkdir(parents=True)
    (roadmap / "verification.md").write_text(
        "```bash\n"
        "agent-run-supervisor agents validate --agents-file <path>\n"
        "agent-run-supervisor agents doctor --agents-file <path>\n"
        "agent-run-supervisor run inspect --run-dir <path>\n"
        "```\n",
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert report["counts"]["acpx"] == 0


def test_acpx_scan_ignores_cold_archives_and_dated_history(tmp_path: Path) -> None:
    """History is allowed to record what was once true."""
    archive = tmp_path / "docs" / "archive"
    archive.mkdir(parents=True)
    (archive / "old.md").write_text(
        "The acpx runtime is the supported compatibility surface.\n"
        "Run `agent-run-supervisor replay fixtures/acpx-0.12.0/x/stdout.ndjson`.\n",
        encoding="utf-8",
    )
    plans = tmp_path / "docs" / "plans" / "archive"
    plans.mkdir(parents=True)
    (plans / "old-plan.md").write_text(
        "Delete `src/agent_run_supervisor/runner.py` and `fixtures/acpx-0.12.0/`.\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "## 0.1.7\n\n- `agent-run-supervisor doctor` replays fixtures/acpx-0.10.0.\n",
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert report["counts"]["acpx"] == 0


# -- regressions: the three shapes the first scanner missed --------------------
#
# Each was a *false negative* — the scan reported a clean tree while the tree
# carried the thing the scan exists to forbid, which is the worst failure a gate
# has. The fixes are structural (resolve the import, take the command basename,
# scope the exemption to the clause), so these cases are examples of a rule
# rather than the rule itself.


def test_acpx_scan_detects_an_alias_imported_from_the_package_root(
    tmp_path: Path,
) -> None:
    """``from agent_run_supervisor import runner`` imports the module too.

    The module path names only the package here; the removed module is the
    *alias*, so a scan that reads `node.module` alone sees nothing.
    """
    package = tmp_path / "src" / "agent_run_supervisor" / "native_acp"
    package.mkdir(parents=True)
    (package / "leaf.py").write_text(
        "from agent_run_supervisor import runner\n", encoding="utf-8"
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:removed_module_import" in _kinds(report)
    assert report["counts"]["acpx"] == 1


def test_acpx_scan_detects_a_bare_relative_import_alias(tmp_path: Path) -> None:
    """``from . import runner`` has no module path at all."""
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "leaf.py").write_text("from . import runner\n", encoding="utf-8")

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:removed_module_import" in _kinds(report)


def test_acpx_scan_detects_a_multi_alias_import_of_a_removed_module(
    tmp_path: Path,
) -> None:
    """One offending alias among several is still an offending import."""
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "leaf.py").write_text(
        "from agent_run_supervisor import session, retention, result\n",
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert "acpx:removed_module_import" in _kinds(report)


def test_acpx_scan_preserves_legitimate_retained_imports(tmp_path: Path) -> None:
    """The retained package must stay importable in every spelling.

    Tightening the import rule is only correct if it stays silent on the imports
    the surviving modules actually use — including a symbol whose *name* happens
    to sit under the package.
    """
    package = tmp_path / "src" / "agent_run_supervisor" / "native_acp"
    package.mkdir(parents=True)
    (package / "leaf.py").write_text(
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "from typing import Any\n"
        "from pathlib import Path\n"
        "from agent_run_supervisor import process_liveness as _liveness\n"
        "from agent_run_supervisor.session import SessionStore, is_valid_session_id\n"
        "from agent_run_supervisor.native_acp import spec\n"
        "from .spec import AgentRunSpec\n"
        "from . import storage\n"
        "from ..result import build_result_payload\n",
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert report["counts"]["acpx"] == 0, report["findings"]
    assert report["ok"] is True


def test_acpx_scan_does_not_read_prose_or_a_path_fragment_as_a_command(
    tmp_path: Path,
) -> None:
    """A command token is one token. A sentence is not, and neither is a suffix."""
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "boundary.py").write_text(
        '"""ARS never invokes acpx, and nothing here falls back to acpx."""\n'
        '\n'
        'NOTE = "the retired acpx runtime is gone"\n'
        'SUFFIXED = "/var/lib/acpx-archive/readme"\n'
        'PREFIXED = "acpxlike"\n',
        encoding="utf-8",
    )

    report = static_safety_scan.run_scan(tmp_path)

    assert report["counts"]["acpx"] == 0, report["findings"]


# -- import semantics ----------------------------------------------------------


def test_relative_import_of_a_removed_module_inside_the_package_is_refused(
    tmp_path: Path,
) -> None:
    """``from . import runner`` at the package root IS the removed module."""
    path = tmp_path / "src" / "agent_run_supervisor" / "leaf.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("from . import runner\n", encoding="utf-8")

    assert "acpx:removed_module_import" in _kinds(static_safety_scan.run_scan(tmp_path))


def test_a_subpackage_sibling_of_the_same_name_is_not_the_removed_module(
    tmp_path: Path,
) -> None:
    """``from . import runner`` inside ``native_acp`` names a different module."""
    path = tmp_path / "src" / "agent_run_supervisor" / "native_acp" / "leaf.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("from . import runner\n", encoding="utf-8")

    assert static_safety_scan.run_scan(tmp_path)["counts"]["acpx"] == 0


@pytest.mark.parametrize(
    "rel", ["tests/helpers/test_local.py", "tests/helpers/support.py", "scripts/tooling/local.py"]
)
def test_relative_import_outside_the_package_is_legal(tmp_path: Path, rel: str) -> None:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("from . import runner\n", encoding="utf-8")
    (path.parent / "runner.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert static_safety_scan.run_scan(tmp_path)["counts"]["acpx"] == 0


def test_relative_import_escaping_into_the_package_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "src" / "agent_run_supervisor" / "native_acp" / "leaf.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("from .. import retention\n", encoding="utf-8")

    assert "acpx:removed_module_import" in _kinds(static_safety_scan.run_scan(tmp_path))


def test_preserves_legitimate_retained_imports(tmp_path: Path) -> None:
    path = tmp_path / "src" / "agent_run_supervisor" / "native_acp" / "leaf.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "from typing import Any\n"
        "from agent_run_supervisor import process_liveness as _liveness\n"
        "from agent_run_supervisor.session import SessionStore\n"
        "from .spec import AgentRunSpec\n"
        "from . import storage\n"
        "from ..result import build_result_payload\n",
        encoding="utf-8",
    )

    assert static_safety_scan.run_scan(tmp_path)["counts"]["acpx"] == 0


# -- argv: a bounded static evaluator over literal constructions ---------------
#
# The gate asks one question of a process-starting call: *what executable does
# this argv name?* Not "does an acpx token appear somewhere in it" — that both
# missed `"acpx|exec".split("|")`, whose separator the old reader ignored, and
# falsely flagged `["printf", "%s", "acpx"]`, where acpx is an ordinary
# non-executable argument.

ARGV_REJECT = [
    ("list literal", 'subprocess.run(["acpx", "exec"])'),
    ("tuple literal", 'subprocess.run(("acpx", "exec"))'),
    ("bare string program", 'subprocess.run("acpx")'),
    ("path-qualified", 'subprocess.run(["/usr/bin/acpx", "exec"])'),
    ("split default sep", 'subprocess.run("acpx exec".split())'),
    ("split custom sep", 'subprocess.run("acpx|exec".split("|"))'),
    ("split custom sep multi", 'subprocess.run("acpx::exec".split("::"))'),
    ("npx running acpx", 'subprocess.run(["npx", "-y", "acpx@0.12.0"])'),
    ("npx varargs", 'asyncio.create_subprocess_exec("npx", "acpx")'),
    ("executable keyword", 'subprocess.run(["x"], executable="/usr/bin/acpx")'),
    ("assignment indirection", 'ARGV = ["acpx", "exec"]'),
    ("assignment split", 'cmd = "acpx exec".split()'),
    ("argv keyword", 'spawn_managed_process(argv=["acpx"], cwd=None)'),
    ("Popen", 'subprocess.Popen(["acpx"])'),
]

ARGV_ALLOW = [
    ("acpx as ordinary argument", 'subprocess.run(["printf", "%s", "acpx"])'),
    ("acpx as grep pattern", 'subprocess.run(["grep", "-r", "acpx", "src"])'),
    ("npx without acpx", 'subprocess.run(["npx", "-y", "prettier"])'),
    ("label constant", 'TOOL_LABEL = "npx"'),
    ("retired name constant", 'RETIRED_RUNTIME_NAME = "acpx"'),
    ("banned-name set", 'BANNED_NAMES = {"acpx", "npx"}'),
    ("prose docstring", '"""ARS never invokes acpx."""'),
    ("logging call", 'log.info("acpx")'),
    ("echo of a name", 'subprocess.run(["echo", "acpx"])'),
    ("split producing a non-executable", 'subprocess.run("printf %s acpx".split())'),
]


@pytest.mark.parametrize(("label", "source"), ARGV_REJECT, ids=[c[0] for c in ARGV_REJECT])
def test_argv_matrix_rejects_acpx_execution(tmp_path: Path, label: str, source: str) -> None:
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "spawn.py").write_text(source + "\n", encoding="utf-8")

    assert "acpx:argv_construction" in _kinds(static_safety_scan.run_scan(tmp_path)), source


@pytest.mark.parametrize(("label", "source"), ARGV_ALLOW, ids=[c[0] for c in ARGV_ALLOW])
def test_argv_matrix_allows_non_execution(tmp_path: Path, label: str, source: str) -> None:
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "spawn.py").write_text(source + "\n", encoding="utf-8")

    report = static_safety_scan.run_scan(tmp_path)
    assert report["counts"]["acpx"] == 0, (source, report["findings"])


@pytest.mark.parametrize(
    "source",
    [
        "subprocess.run(build_argv())",
        "subprocess.run(argv_from_config)",
        'subprocess.run([binary, "exec"])',
        'subprocess.run(prefix + ["exec"])',
    ],
)
def test_argv_matrix_dynamic_construction_is_not_flagged(
    tmp_path: Path, source: str
) -> None:
    """Explicit policy for what cannot be evaluated statically: no finding.

    A static gate that guesses at a runtime value produces noise on every
    ordinary dynamic argv, and noise is what gets suppressed. Reintroducing the
    runtime dynamically would still have to name it somewhere a literal rule
    sees — an import, a removed path, or a literal token.
    """
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "spawn.py").write_text(source + "\n", encoding="utf-8")

    assert static_safety_scan.run_scan(tmp_path)["counts"]["acpx"] == 0, source


# -- the current-authority prose gate ------------------------------------------
#
# Default reject, at **statement** granularity. Every acpx-bearing statement in
# a current-authority document — including an active plan — must be a member of
# one committed finite allowlist.
#
# Statement, not clause: splitting on commas and semicolons was what let a
# compound sentence pass, because the allowlisted half satisfied the gate while
# the positive half carried no acpx token and was never examined. Markdown is
# canonicalized before the token test, so emphasis inside a word cannot hide it.

PROSE_REJECT = [
    ("compound sentence", "The acpx runtime was removed from ARS; it remains the supported runtime."),
    ("implicit subject", "acpx is not a supported product, but is a supported runtime."),
    ("markdown-split token", "ac**px** is the supported runtime."),
    ("emphasis around token", "*acpx* is the supported runtime."),
    ("bare claim", "acpx is the supported runtime."),
    ("negated negative", "acpx is not unsupported."),
    ("object position", "ARS supports acpx."),
    ("verb variant", "acpx powers the supported runtime."),
    ("trailing clause", "No acpx peer; acpx is the supported runtime."),
    ("removal subject, positive predicate", "The acpx removal is the supported runtime."),
    ("unlisted mention", "Consider restoring acpx for legacy roles."),
]

SCANNED_ROOTS = [
    "GOAL.md",
    "AGENTS.md",
    "docs/product/prd.md",
    "docs/design/architecture.md",
    "docs/roadmap/features.md",
    "docs/plans/active/2026-08-06-example.md",
]
COLD_ARCHIVE_ROOTS = [
    "docs/archive/old.md",
    "docs/plans/archive/old-plan.md",
    "docs/roadmap/archive/old-phase.md",
]


def _write(tmp_path: Path, rel: str, body: str) -> dict:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    return static_safety_scan.run_scan(tmp_path)


@pytest.mark.parametrize(("label", "markdown"), PROSE_REJECT, ids=[c[0] for c in PROSE_REJECT])
def test_prose_matrix_rejects_a_capability_claim(
    tmp_path: Path, label: str, markdown: str
) -> None:
    report = _write(tmp_path, "GOAL.md", markdown)

    assert "acpx:doc_capability_claim" in _kinds(report), markdown


@pytest.mark.parametrize("rel", SCANNED_ROOTS)
def test_prose_matrix_scans_every_current_authority_root(tmp_path: Path, rel: str) -> None:
    """Active plans are current authority in flight; they are scanned."""
    report = _write(tmp_path, rel, "acpx is the supported runtime.")

    assert "acpx:doc_capability_claim" in _kinds(report), rel


@pytest.mark.parametrize("rel", COLD_ARCHIVE_ROOTS)
def test_prose_matrix_ignores_the_three_cold_archive_roots(tmp_path: Path, rel: str) -> None:
    """History may record what was once true — but only these exact roots."""
    report = _write(tmp_path, rel, "acpx is the supported runtime.")

    assert report["counts"]["acpx"] == 0, rel


PROSE_ALLOW = [
    ("boundary heading", "GOAL.md", "## acpx boundary"),
    ("removal fact", "GOAL.md", "The acpx runtime was removed from ARS."),
    ("fixture fact", "GOAL.md", "No acpx fixture was retained."),
    ("refusal", "AGENTS.md", "`acpx` is **not** a product, runtime, or compatibility surface."),
    ("decision record", "docs/roadmap/non-approvals.md", "Source removal of the acpx product, runtime, and compatibility content."),
]


@pytest.mark.parametrize(("label", "rel", "markdown"), PROSE_ALLOW, ids=[c[0] for c in PROSE_ALLOW])
def test_prose_matrix_allows_the_finite_current_authority_statements(
    tmp_path: Path, label: str, rel: str, markdown: str
) -> None:
    report = _write(tmp_path, rel, markdown)

    assert report["counts"]["acpx"] == 0, report["findings"]


def test_prose_allowlist_is_a_committed_finite_set_of_statements() -> None:
    """The policy is a set of strings, readable in one place, matched whole."""
    allowed = static_safety_scan.ALLOWED_ACPX_STATEMENTS
    assert isinstance(allowed, frozenset)
    assert all(s == s.lower().strip() for s in allowed)


def test_an_allowlisted_statement_cannot_be_used_as_a_prefix(tmp_path: Path) -> None:
    """Whole-statement matching: an allowed opening does not license a claim."""
    report = _write(
        tmp_path, "GOAL.md",
        "The acpx runtime was removed from ARS and is the supported runtime.",
    )

    assert "acpx:doc_capability_claim" in _kinds(report)


def test_acpx_scan_category_is_not_vacuous(tmp_path: Path) -> None:
    """Every shape fires at once, through the CLI, on a runtime-built tree.

    Non-vacuity has to be proven against a tree that actually violates the rule.
    Committing one would make the gate green on a repository that contains the
    thing the gate exists to forbid, so the violating tree is synthesized here
    and thrown away.
    """
    package = tmp_path / "src" / "agent_run_supervisor"
    package.mkdir(parents=True)
    (package / "relapse.py").write_text(
        "from agent_run_supervisor.session_runtime import SessionRuntime\n"
        '\n'
        'ARGV = ["acpx", "exec"]\n'
        'REPLAY = "fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson"\n',
        encoding="utf-8",
    )
    design = tmp_path / "docs" / "design"
    design.mkdir(parents=True)
    (design / "technical-solution.md").write_text(
        "acpx is the supported runtime for legacy roles.\n"
        "\n"
        "Run `agent-run-supervisor replay <file>` to inspect a stream.\n"
        "\n"
        "| `acpx_exit_code` | `number` | yes | process exit code |\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "static_safety_scan.py"),
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert _kinds(payload) == {
        "acpx:removed_module_import",
        "acpx:argv_construction",
        "acpx:removed_surface_path",
        "acpx:doc_capability_claim",
    }
    assert payload["counts"]["acpx"] == payload["counts"]["total"]
    assert payload["counts"]["acpx"] >= 6

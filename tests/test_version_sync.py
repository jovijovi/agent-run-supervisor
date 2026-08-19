from __future__ import annotations

import subprocess
import sys
import tomllib
from importlib.metadata import metadata
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

import tools.bump_version as bump_version
import tools.check_version_sync as check_version_sync

REPO_ROOT = Path(__file__).resolve().parents[1]

MINIMAL_PYPROJECT = """\
[project]
name = "agent-run-supervisor"
version = "0.1.0"
"""

MINIMAL_INIT = """\
\"\"\"test package\"\"\"

__version__ = "0.1.0"
"""

MINIMAL_CHANGELOG = """\
# Changelog

Intro text.

## [0.1.0] - 2026-01-01

### Added

- Initial release.
"""

MINIMAL_UV_LOCK = """\
version = 1

[[package]]
name = "agent-run-supervisor"
version = "0.1.0"
source = { editable = "." }
"""


def _write_minimal_project(root: Path, *, lock_version: str = "0.1.0") -> None:
    (root / "src/agent_run_supervisor").mkdir(parents=True)
    (root / "pyproject.toml").write_text(MINIMAL_PYPROJECT, encoding="utf-8")
    (root / "src/agent_run_supervisor/__init__.py").write_text(
        MINIMAL_INIT, encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(MINIMAL_CHANGELOG, encoding="utf-8")
    lock = MINIMAL_UV_LOCK.replace("0.1.0", lock_version, 1)
    (root / "uv.lock").write_text(lock, encoding="utf-8")


def test_check_version_sync_passes_on_current_repository() -> None:
    ok, errors = check_version_sync.check_version_sync(REPO_ROOT)

    assert ok is True
    assert errors == []


def test_check_version_sync_detects_lock_drift(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path, lock_version="9.9.9")

    ok, errors = check_version_sync.check_version_sync(tmp_path)

    assert ok is False
    assert any("uv.lock" in err for err in errors)


def test_check_version_sync_cli_returns_nonzero_on_drift(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path, lock_version="9.9.9")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "check_version_sync.py"),
            "--root",
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "version sync check FAILED" in result.stderr


def test_bump_version_rejects_invalid_semver(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)

    with pytest.raises(ValueError, match="invalid semver"):
        bump_version.validate_new_version("0.1.0", "not-a-version")


def test_bump_version_rejects_non_incrementing_version(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)

    with pytest.raises(ValueError, match="must be greater"):
        bump_version.validate_new_version("0.1.0", "0.1.0")

    with pytest.raises(ValueError, match="must be greater"):
        bump_version.validate_new_version("0.1.0", "0.0.9")


def test_bump_version_updates_metadata_and_changelog_stub(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)

    current = bump_version.bump_version(
        "0.1.1",
        root=tmp_path,
        release_date="2026-07-07",
        skip_lock=True,
    )

    assert current == "0.1.0"
    assert check_version_sync.read_pyproject_version(tmp_path) == "0.1.1"
    assert check_version_sync.read_init_version(tmp_path) == "0.1.1"

    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.1.1] - 2026-07-07" in changelog
    assert "### Added" in changelog
    assert changelog.index("## [0.1.1]") < changelog.index("## [0.1.0]")


def test_bump_version_rejects_existing_changelog_section(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        changelog.read_text(encoding="utf-8")
        + "\n## [0.1.2] - 2026-07-07\n\n### Notes\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already contains section"):
        bump_version.bump_version("0.1.2", root=tmp_path, skip_lock=True)


# -- the supported interpreter range -------------------------------------------
#
# ``requires-python`` is the only thing that keeps an unsupported interpreter
# from installing this package, and like the version above it is stated in more
# than one place that has to agree: the project table, the lock
# ``uv sync --locked`` resolves against, and the metadata of the built
# distribution.
#
# The built metadata is the one that matters. That string is what pip and uv
# read at resolution time; the ``pyproject.toml`` entry is only a claim until a
# build has copied it into ``METADATA``. So the range is asserted there too, and
# the admit/refuse behaviour is evaluated against the built specifier rather
# than against the source text -- a substring search would pass on
# ``">=3.11,<3.15.0.dev0"`` or on a range this project does not test.
#
# Only the project table is compared as an exact string, because that is the
# text this repository authors. The other two are compared as specifier *sets*:
# setuptools rewrites the field to ``<3.15,>=3.11`` and uv writes
# ``>=3.11, <3.15``, so string equality there would assert a formatting habit of
# whichever version of those tools ran last, and would break on an upgrade that
# changed nothing about which interpreters are supported.

DISTRIBUTION = "agent-run-supervisor"

#: 3.11 through 3.14 inclusive; 3.15 and later refused. Matches the interpreter
#: matrix in .github/workflows/verify.yml.
REQUIRES_PYTHON = ">=3.11,<3.15"

SUPPORTED_PYTHON = [
    "3.11",
    "3.11.9",
    "3.12",
    "3.12.11",
    "3.13",
    "3.13.7",
    "3.14",
    "3.14.9",
]
UNSUPPORTED_PYTHON = ["3.9", "3.10", "3.10.14", "3.15", "3.15.0", "3.16", "4.0"]


def read_pyproject_requires_python(root: Path = REPO_ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["requires-python"])


def read_lock_requires_python(root: Path = REPO_ROOT) -> str:
    with (root / "uv.lock").open("rb") as handle:
        return str(tomllib.load(handle)["requires-python"])


def read_built_requires_python() -> str:
    """``Requires-Python`` from the installed distribution's ``METADATA``.

    Reads the built artifact, not the source tree. A stale value here means the
    environment predates the metadata change -- re-run ``uv sync --locked``.
    """
    return str(metadata(DISTRIBUTION)["Requires-Python"])


def test_built_metadata_declares_the_supported_python_range() -> None:
    assert SpecifierSet(read_built_requires_python()) == SpecifierSet(REQUIRES_PYTHON)


def test_pyproject_declares_the_supported_python_range() -> None:
    assert read_pyproject_requires_python() == REQUIRES_PYTHON


def test_lock_resolves_against_the_same_python_range() -> None:
    assert SpecifierSet(read_lock_requires_python()) == SpecifierSet(REQUIRES_PYTHON)


@pytest.mark.parametrize("version", SUPPORTED_PYTHON)
def test_built_metadata_admits_a_supported_interpreter(version: str) -> None:
    assert SpecifierSet(read_built_requires_python()).contains(Version(version))


@pytest.mark.parametrize("version", UNSUPPORTED_PYTHON)
def test_built_metadata_refuses_an_unsupported_interpreter(version: str) -> None:
    assert not SpecifierSet(read_built_requires_python()).contains(Version(version))

"""The distribution boundary: what a built artifact is allowed to contain.

An exact set comparison against a committed allowlist, for the wheel **and** the
sdist. Exactness is the point: ``grep -c acpx | test 0`` is not a gate — it
observes a count, and its exit status inverts precisely when the desired count
is zero, so the "no acpx in the wheel" check passes for the same reason a broken
one would.

Two layers, deliberately:

* set equality against the allowlist — catches anything that appears or
  disappears, including files nobody thought to ban;
* explicit negative rules — catch a *careless regeneration* of the allowlist
  itself, which set equality alone would happily accept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import check_dist_manifest as gate

REPO_ROOT = Path(__file__).resolve().parents[1]


def _expected(kind: str) -> set[str]:
    return gate.load_expected(REPO_ROOT, kind)


# -- the committed allowlists themselves --------------------------------------


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_the_committed_allowlist_is_non_empty_and_names_the_package(kind: str) -> None:
    expected = _expected(kind)
    assert expected, f"{kind} allowlist is empty"
    assert any("agent_run_supervisor/__init__.py" in name for name in expected)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_the_committed_allowlist_carries_no_forbidden_path(kind: str) -> None:
    """If the allowlist is regenerated carelessly, this is what notices."""
    assert gate.forbidden_paths(_expected(kind)) == []


# -- the comparison ------------------------------------------------------------


def test_an_exact_match_reports_no_problem() -> None:
    expected = _expected("wheel")
    assert gate.check_manifest(expected, expected, kind="wheel") == []


def test_an_extra_file_is_reported() -> None:
    expected = _expected("wheel")
    actual = expected | {"agent_run_supervisor/surprise.py"}
    problems = gate.check_manifest(actual, expected, kind="wheel")
    assert any("surprise.py" in problem for problem in problems)


def test_a_missing_file_is_reported() -> None:
    expected = _expected("wheel")
    actual = expected - {"agent_run_supervisor/cli.py"}
    problems = gate.check_manifest(actual, expected, kind="wheel")
    assert any("cli.py" in problem for problem in problems)


@pytest.mark.parametrize(
    "intruder",
    [
        "agent_run_supervisor/fixtures/acpx-0.12.0/x/stdout.ndjson",
        "agent_run_supervisor/runner.py",
        "agent_run_supervisor/hermes_caller/__init__.py",
        "agent_run_supervisor/fixtures/anything.json",
    ],
)
def test_a_forbidden_path_is_reported_even_when_allowlisted(intruder: str) -> None:
    """The negative rules do not trust the allowlist.

    An allowlist that has been regenerated from a bad build would make set
    equality pass. The shipped artifact would still contain the thing the gate
    exists to keep out, so the rules are asserted against the manifest itself.
    """
    expected = _expected("wheel") | {intruder}
    problems = gate.check_manifest(expected, expected, kind="wheel")
    assert any(intruder in problem for problem in problems), problems


def test_normalization_removes_the_version_so_a_bump_is_not_a_diff() -> None:
    assert (
        gate.normalize_wheel_entry("agent_run_supervisor-9.9.9.dist-info/METADATA")
        == "<dist-info>/METADATA"
    )
    assert (
        gate.normalize_sdist_entry("agent_run_supervisor-9.9.9/src/x/y.py")
        == "src/x/y.py"
    )

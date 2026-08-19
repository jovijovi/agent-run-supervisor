"""Contract for the CI uv-cache writer policy.

``astral-sh/setup-uv`` reserves a GitHub Actions cache key in its post step
whenever it saves. Two jobs that reserve the *same* key concurrently — the
``verify``/``coverage`` matrices of one push, or the documentation build and
the Pages publication of one push to ``main`` — make the loser log a benign
"Unable to reserve cache" warning.

The policy that removes those warnings without losing a restore is:

* exactly one deterministic writer per cache key;
* every other job restores that same key with ``save-cache: false``
  (``restore-cache`` defaults to ``true``, so the restore survives);
* the documentation toolchain gets its own key namespace via
  ``cache-suffix: docs``, so it cannot collide with the general Python 3.11
  cache written by ``verify``.

``EXPECTED_INPUTS`` pins the complete ``with:`` mapping of all four governed
steps, and the equality is exact on purpose. The action's key is built from
``prune-cache``, ``cache-python``, ``cache-dependency-glob``,
``working-directory``, the Python version and the suffix; every one of those is
absent here, so a reader and its writer agree on the key by declaring nothing
that could move it. Admitting any new input therefore has to be a conscious
edit of this table, which is where the "does this diverge a shared key?"
question gets asked.

The workflows are repository-owned and canonically formatted, so the helpers
slice the controlled indentation blocks they need — a job body under ``jobs:``
and its ``Set up uv`` step — instead of parsing YAML. Every slice asserts what
it expected to find. ``release.yml`` is out of scope: it is tag-triggered and
shares no push with these workflows.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

#: Workflows this policy governs, in trigger-overlap order.
POLICY_WORKFLOWS = ("verify.yml", "docs.yml", "pages-publish.yml")

SETUP_UV_STEP = "Set up uv"
SETUP_UV_ACTION = "astral-sh/setup-uv@d31148d669074a8d0a63714ba94f3201e7020bc3"
RUNNER = "ubuntu-latest"
MATRIX_PYTHONS = ("3.11", "3.12", "3.13", "3.14")
MATRIX_PYTHON_INPUT = "${{ matrix.python-version }}"

#: The complete, exact ``with:`` mapping each governed step may declare.
EXPECTED_INPUTS = {
    ("verify.yml", "verify"): {
        "enable-cache": "true",
        "python-version": MATRIX_PYTHON_INPUT,
    },
    ("verify.yml", "coverage"): {
        "enable-cache": "true",
        "python-version": MATRIX_PYTHON_INPUT,
        "save-cache": "false",
    },
    ("docs.yml", "build"): {
        "enable-cache": "true",
        "python-version": "3.11",
        "cache-suffix": "docs",
        "save-cache": "true",
    },
    ("pages-publish.yml", "build"): {
        "enable-cache": "true",
        "python-version": "3.11",
        "cache-suffix": "docs",
        "save-cache": "false",
    },
}

#: (writer, reader) pairs that must land on one key: one job saves it, the
#: other only restores it.
SHARED_KEYS = (
    (("verify.yml", "verify"), ("verify.yml", "coverage")),
    (("docs.yml", "build"), ("pages-publish.yml", "build")),
)

#: Inputs that gate whether the cache is used, not which key it is.
NON_KEY_INPUTS = frozenset({"enable-cache", "save-cache", "restore-cache"})

# Canonical block boundaries: jobs at 2 spaces, job keys and steps at 4 and 6,
# step keys at 8, ``with:`` inputs at 10. A block runs until the first line
# indented less.
_JOBS_KEY = "\njobs:\n"
_JOB_BODY = r"^  {job}:\n((?:(?: {{4}}.*)?\n)*)"
_RUNS_ON = r"^    runs-on: (.+)$"
_MATRIX = r"^      matrix:\n {8}python-version: \[(.+)\]$"
_STEP = r"^      - name: (?P<name>.+)\n(?P<body>(?:(?: {8}.*)?\n)*)"
_USES = r"^        uses: (\S+)$"
_WITH_BODY = r"^        with:\n((?:(?: {10}.*)?\n)*)"


# -- controlled block slicing -------------------------------------------------


def _text(workflow: str, override: str | None = None) -> str:
    return _read(workflow) if override is None else override


def _read(workflow: str) -> str:
    return (WORKFLOWS / workflow).read_text(encoding="utf-8")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _job_body(workflow: str, job: str, override: str | None = None) -> str:
    """The indented body of ``jobs.<job>``, and nothing else."""
    text = _text(workflow, override)
    where = f"{workflow}:{job}"
    assert text.count(_JOBS_KEY) == 1, f"{where}: expected one jobs: mapping"
    match = re.search(
        _JOB_BODY.format(job=re.escape(job)), text.split(_JOBS_KEY, 1)[1], re.M
    )
    assert match is not None, f"{where}: no job {job!r} at the jobs: boundary"
    return match.group(1)


def _setup_uv_step(workflow: str, job: str, override: str | None = None) -> str:
    body = _job_body(workflow, job, override)
    steps = [
        match.group("body")
        for match in re.finditer(_STEP, body, re.M)
        if match.group("name").strip() == SETUP_UV_STEP
    ]
    assert len(steps) == 1, (
        f"{workflow}:{job}: {len(steps)} {SETUP_UV_STEP!r} steps, expected 1"
    )
    return steps[0]


def _action_ref(workflow: str, job: str) -> str:
    match = re.search(_USES, _setup_uv_step(workflow, job), re.M)
    assert match is not None, f"{workflow}:{job}: {SETUP_UV_STEP!r} declares no uses:"
    return match.group(1)


def _cache_inputs(workflow: str, job: str, override: str | None = None) -> dict[str, str]:
    """The complete ``with:`` mapping of this job's ``Set up uv`` step."""
    where = f"{workflow}:{job}"
    match = re.search(_WITH_BODY, _setup_uv_step(workflow, job, override), re.M)
    assert match is not None, f"{where}: {SETUP_UV_STEP!r} declares no with:"
    inputs: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        assert separator, f"{where}: unreadable input {line!r}"
        inputs[key.strip()] = _unquote(value.strip())
    assert inputs, f"{where}: {SETUP_UV_STEP!r} declares no inputs"
    return inputs


def _runs_on(workflow: str, job: str) -> str:
    match = re.search(_RUNS_ON, _job_body(workflow, job), re.M)
    assert match is not None, f"{workflow}:{job}: job declares no runs-on"
    return _unquote(match.group(1).strip())


def _matrix_pythons(workflow: str, job: str) -> tuple[str, ...]:
    match = re.search(_MATRIX, _job_body(workflow, job), re.M)
    assert match is not None, f"{workflow}:{job}: job declares no python-version matrix"
    return tuple(_unquote(item.strip()) for item in match.group(1).split(","))


def _key_inputs(inputs: dict[str, str]) -> dict[str, str]:
    """The declared inputs that select the cache key."""
    return {key: value for key, value in inputs.items() if key not in NON_KEY_INPUTS}


# -- the policy ---------------------------------------------------------------


def test_governed_steps_are_the_only_setup_uv_steps() -> None:
    """Non-vacuous inventory: every step is found, none rides along."""
    for workflow in POLICY_WORKFLOWS:
        jobs = [job for governed, job in EXPECTED_INPUTS if governed == workflow]
        found = _read(workflow).count(SETUP_UV_ACTION)
        assert found == len(jobs), (
            f"{workflow}: {found} {SETUP_UV_ACTION} steps, policy governs {jobs}"
        )
        for job in jobs:
            # Raises at the workflow:job boundary if the step moved or lost its
            # inputs, so the assertions below can never pass vacuously.
            assert _cache_inputs(workflow, job), f"{workflow}:{job}: no inputs"


def test_each_governed_step_declares_exactly_its_allowed_cache_inputs() -> None:
    for (workflow, job), expected in EXPECTED_INPUTS.items():
        assert _cache_inputs(workflow, job) == expected, (
            f"{workflow}:{job}: {SETUP_UV_STEP!r} inputs changed; an input that "
            "moves the cache key (prune-cache, cache-python, "
            "cache-dependency-glob, working-directory, cache-suffix, "
            "python-version) must be admitted in EXPECTED_INPUTS for this job "
            "and its shared-key partner together"
        )


def test_governed_steps_share_the_pinned_action_and_runner() -> None:
    for workflow, job in EXPECTED_INPUTS:
        assert _action_ref(workflow, job) == SETUP_UV_ACTION, f"{workflow}:{job}"
        assert _runs_on(workflow, job) == RUNNER, f"{workflow}:{job}"


def test_verify_and_coverage_keep_the_same_python_matrix() -> None:
    for job in ("verify", "coverage"):
        assert _matrix_pythons("verify.yml", job) == MATRIX_PYTHONS, job


def test_documentation_keys_are_namespaced_away_from_the_python_cache() -> None:
    general = _cache_inputs("verify.yml", "verify")
    assert "cache-suffix" not in general, f"verify.yml:verify: {general}"
    for workflow, job in (("docs.yml", "build"), ("pages-publish.yml", "build")):
        inputs = _cache_inputs(workflow, job)
        assert inputs.get("cache-suffix") == "docs", (
            f"{workflow}:{job}: the documentation cache must stay in its own "
            f"key namespace, away from the general Python 3.11 cache; got {inputs}"
        )


def test_each_shared_key_has_one_writer_and_a_restore_only_reader() -> None:
    for writer, reader in SHARED_KEYS:
        saved = _cache_inputs(*writer)
        restored = _cache_inputs(*reader)
        assert saved.get("save-cache", "true") == "true", f"{writer} must write"
        assert restored.get("save-cache") == "false", f"{reader} must not write"
        assert saved.get("enable-cache") == "true", f"{writer} must use the cache"
        assert restored.get("enable-cache") == "true", f"{reader} must restore"
        assert _key_inputs(saved) == _key_inputs(restored), (
            f"{reader} restores a different key than {writer} writes"
        )


def test_a_coverage_only_prune_mode_change_breaks_the_shared_key_contract() -> None:
    """A key-moving input on one side of a shared key must fail the contract."""
    text = _read("verify.yml")
    anchor = "          save-cache: false\n"
    assert text.count(anchor) == 1, "expected save-cache: false only in coverage"
    mutated = text.replace(anchor, anchor + "          prune-cache: false\n", 1)

    unpruned = _cache_inputs("verify.yml", "coverage", override=mutated)
    assert unpruned != EXPECTED_INPUTS[("verify.yml", "coverage")]
    assert unpruned["prune-cache"] == "false"
    assert _key_inputs(unpruned) != _key_inputs(_cache_inputs("verify.yml", "verify"))

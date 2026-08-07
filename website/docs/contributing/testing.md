---
title: Testing
description: The verification gate — what runs, what counts as evidence, and what does not.
---

# Testing

## One gate

```bash
make verify
```

`make verify` runs `./scripts/verify_local.sh`, the same script CI runs. **Only a
green full run is completion evidence.**

Focused checks are useful while iterating and are never a substitute:

```bash
uv run pytest -q                                   # tests
uv run python -m compileall -q src scripts tests   # syntax/import smoke
uv run python tools/check_docs_site.py             # the documentation-site gate
```

!!! warning "Do not present a partial check as proof"

    A passing subset says the subset passed. The repository's culture treats
    "tests pass" without the full gate as an unverified claim, because the gate
    includes package build, distribution-manifest comparison, installed-artifact
    smokes, and documentation checks that unit tests do not reach.

## What the gate covers

| Step | Checks |
|---|---|
| lock consistency | `uv lock --check` — a stale lock fails the build |
| tests | the full `pytest` suite |
| compile | every source, script, and test file imports |
| docs index and drift | the generated index and drift report are current |
| static safety scan | secrets, stale phrases, forbidden imports, removed surfaces |
| documentation site | navigation allowlist, content rules, links, API symbols, assets, dormant publication |
| version sync | `pyproject.toml`, the package `__version__`, and the lock agree |
| build | sdist and wheel, plus `twine check` |
| distribution manifest | both artifacts compared against committed allowlists as an exact set, in both directions |
| installed smokes | wheel and sdist installed into throwaway virtual environments and exercised |
| roadmap governance | the roadmap documents stay internally consistent |
| whitespace | `git diff --check` |

Neither installed smoke touches an operator environment, enables a service, or
starts a real agent.

## Writing tests

Write the test first. The suite drives the Native ACP core and `arsd` against a
**hermetic fake agent over temporary sockets** — no network, no real model call,
no external agent runtime.

- New tests for the documentation site go under `tests/docs_site/`.
- Suites that need a real agent runtime are opt-in and never run in CI.
- A test that needs a real credential, a real socket path, or a real agent is
  the wrong test.

!!! note "Why new test files go in a subdirectory"

    The distribution-manifest gate compares the built sdist against a committed
    allowlist as an exact set, in both directions. The default sdist picks up
    top-level `tests/test*.py` but not files in subdirectories, so a test placed
    in `tests/docs_site/` needs no manifest edit — and the manifest files are not
    meant to be edited casually.

## Coverage

```bash
uv run pytest --cov --cov-branch --cov-report=xml
```

Coverage runs in CI across the supported Python versions. It is a signal, not a
gate: a test that exists only to move a percentage is worse than no test.

## When something fails

Read the failure before changing anything. The gates are written to name the
failing rule, and most of them refuse rather than repair on purpose — a check
that quietly fixes its input cannot tell you what was wrong.

If a gate looks wrong, say so in the pull request rather than working around it.
Weakening a gate to make a change pass is the one review comment guaranteed to
block a merge.

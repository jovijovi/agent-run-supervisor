---
title: Contributing
description: How to work in the agent-run-supervisor repository — branches, gates, and the rules that are not negotiable.
---

# Contributing

Issues and pull requests are welcome.

## Documentation precedes code

This project decides what to build in documents, then builds it. Read the
repository's authority chain before proposing a change: the project goal, then
the product requirements, then the design layer, then the roadmap. A plan
derives from those; it never redefines them.

That ordering is why a pull request that changes behaviour is expected to say
which documented requirement it serves.

## Branches and commits

Branch from `main` with a short-lived task branch. Four prefixes, and no ad-hoc
fifth:

| Prefix | For |
|---|---|
| `feat/` | new capability |
| `fix/` | defect repair |
| `docs/` | documentation |
| `cicd/` | build, gates, workflows |

Use [Conventional Commits](https://www.conventionalcommits.org/). Keep the branch
short-lived and rebase rather than accumulating merge noise.

## The rules that are not negotiable

- **The runtime stays standard library only.** Third-party packages belong in an
  optional extra, never in `[project].dependencies`. Test, release, ACP-client,
  and documentation tooling each live in their own extra.
- **Write the test first.** See [Testing](testing.md).
- **Never commit secrets.** No keys, tokens, real UID mappings, socket paths, or
  other deployment values. Use `[REDACTED]` in documents and examples, and keep
  real runtime values in a local environment file that version control ignores.
- **Never commit a host- or user-specific path.** Committed documents and
  examples use repository-relative paths or a neutral placeholder such as
  `<repo-root>` or `/path/to/<thing>`. The checkout path and the branch state are
  environment facts, not repository content.
- **`make verify` must be green before you open a pull request.** It is the same
  gate CI runs.

## Local setup

```bash
make sync      # uv sync --locked --extra dev --extra release --extra native
make verify    # the single local gate — identical to CI
make build     # sdist/wheel + twine check
make help      # list all targets
```

Without Make:

```bash
uv sync --locked --extra dev --extra release --extra native
./scripts/verify_local.sh
```

Without [uv](https://docs.astral.sh/uv/):

```bash
pip install -e '.[dev,release,native]'
python3 -m pytest -q
```

The `uv lock --check` step is uv-only and is skipped on the pip path.

## Working on this documentation site

The site has its own extra and its own targets, deliberately separate from the
package gates:

```bash
make docs-sync     # uv sync --locked --extra docs
make docs-check    # the site content gate, standard library only
make docs          # docs-sync + docs-check + mkdocs build --strict
make docs-serve    # local preview
```

Public content lives under `website/docs/`, which is **not** the repository's
`docs/` tree. That separation is load-bearing: `docs/` holds the governed
authority chain, and MkDocs copies every file under its `docs_dir` into the
output regardless of navigation. Pointing the site at `docs/` would publish
internal governance surfaces at real URLs.

Adding a page is a deliberate two-file edit: create it under `website/docs/`,
then list it in **both** `mkdocs.yml`'s `nav:` and `website/nav-allowlist.txt`.
The gate fails if the two disagree, or if a Markdown file exists in the tree
without being listed.

!!! contract "What the site gate enforces"

    Navigation allowlisting in three directions; no reference to internal
    governance surfaces; no host paths or private runtime state; internal links
    resolve; every `:::` API identifier is allowlisted and resolves in `src/`;
    Mermaid is configured and locally vendored; fonts are local-only; no external
    asset is loaded; publication stays dormant; and a set of product-contract
    statements is present and un-negated.

    It is standard library only and runs inside `make verify`, so it does not
    require the documentation extra to be installed.

## Reviews

Authority-bearing and implementation changes get an independent, fresh-context
review. Scope control, deterministic verification, evidence arbitration, and
every side effect stay with the maintainer — a green gate authorizes merging
code, and nothing beyond that.

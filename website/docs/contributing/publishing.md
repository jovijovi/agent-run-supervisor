---
title: Publishing
description: How releases and documentation publication are authorized — and why neither happens automatically.
---

# Publishing

Two separate pipelines exist, and **neither triggers the other**. A
documentation change never implies a package release, and a package release never
implies a documentation deploy.

## Nothing publishes itself

!!! danger "Publication is an explicit human decision, every time"

    Git tag creation, GitHub Release publication, and PyPI package publishing are
    **not** part of implementation work. They happen only after explicit
    authorization — typically once development is finished, documentation is
    synced, and the full gate is green.

    Nothing tags, publishes, or uploads proactively, during active
    implementation, or immediately after a merge. A green gate authorizes a
    merge, and nothing beyond it.

The same rule covers installing an artifact, writing production configuration,
enabling or restarting a service, rollout, cutover, migration, and integration
with a caller platform. Source work authorizes source, tests, and documentation
— that is all.

## Preparing a package release

When a release *is* authorized:

```bash
make bump VERSION=X.Y.Z     # syncs pyproject, __init__, uv.lock, and a CHANGELOG stub
# edit CHANGELOG.md
make verify                 # includes the version-sync check
```

`make bump` keeps four things in agreement — `pyproject.toml`, the package
`__version__`, `uv.lock`, and the changelog stub — because a release where they
disagree is the one failure mode that reaches users.

Production PyPI publishing goes through GitHub Actions Trusted Publishing, from a
tag, on `main`, after the gate passes:

```bash
make release-tag            # prints the exact tag commands; runs none of them
```

`make release-tag` deliberately prints rather than executes. Reviewing what will
happen and choosing to do it are different acts.

For a dry run against TestPyPI:

```bash
make release-test
```

## Publishing this documentation site

Publishing the documentation site is not enabled.

The repository builds the site on pull requests and on `main` as a **validation
step only** (`docs.yml`), with read-only permissions. Alongside it, one reviewed
publication workflow exists: `pages-publish.yml`, whose only trigger is
`workflow_dispatch`. Publication happens when a human dispatches that workflow
by hand — never because a pull request merges, a branch is pushed, or a
schedule fires.

The capability stays dormant until two further human decisions are taken:
configuring GitHub Pages in the repository settings, and manually dispatching
the workflow. Committing the workflow took neither decision — it enabled
nothing and deployed nothing.

| Exists | Does not exist |
|---|---|
| a build-and-validate workflow (`docs.yml`) that runs `mkdocs build --strict` and the content gate | any automatic publication trigger — nothing deploys on merge, push, or schedule |
| one manual publication workflow (`pages-publish.yml`), triggered by `workflow_dispatch` only | a configured Pages site, or any deployment that has happened |
| a content gate that pins publication to that one manual workflow | a `gh-pages` branch or `mkdocs gh-deploy` anywhere |

!!! contract "Publication stays a manual human act"

    The publication workflow runs only when a maintainer manually dispatches it.
    The content gate enforces that shape: a publication marker in any other
    active workflow, a trigger other than `workflow_dispatch` on the publication
    workflow, a write grant beyond `pages` and `id-token`, or a permissions
    block that is missing or widened fails the repository gate. The complete
    `pages-publish.yml` file is also pinned by SHA-256, so changing a step,
    input, job placement, or even a comment requires an explicit digest update
    in the same reviewed change. That digest is a drift detector, not a
    substitute for code review: reviewers must examine workflow and digest
    changes together. Dormancy is enforced rather than merely intended.

## Local preview

You never need to publish to see the site:

```bash
make docs-serve     # local preview
make docs           # docs-sync + docs-check + mkdocs build --strict
```

`make docs` writes to `site/`, which version control ignores. Do not commit
generated output.

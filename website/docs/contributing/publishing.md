---
title: Publishing
description: How releases and documentation publication are governed — releases stay manual; the documentation site publishes from main under one pinned workflow.
---

# Publishing

Two separate pipelines exist, and **neither triggers the other**. A
documentation change never implies a package release, and a package release never
implies a documentation deploy.

## Releases never publish themselves

!!! danger "A package release is an explicit human decision, every time"

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

The documentation site publishes automatically: every push to `main` runs the
reviewed publication workflow, which re-validates the content boundary, builds
strictly, and deploys the result to GitHub Pages. The published site is
[jovijovi.github.io/agent-run-supervisor](https://jovijovi.github.io/agent-run-supervisor/).

Publication deploys only from `refs/heads/main` — never from a pull request,
another branch, a tag, or a schedule. Pull requests build and validate the
site (`docs.yml`) with read-only permissions and deploy nothing. The
publication trigger deliberately has no path filter: the API reference is
generated from source docstrings, so a change outside `website/` can still
change the site.

Exactly one reviewed publication workflow exists: `pages-publish.yml`,
triggered by a push to `main` and by manual `workflow_dispatch`. The manual
trigger is a re-run path — an operator can republish `main` by hand, for
example after a Pages incident — not a second publication surface: both of
the workflow's jobs are guarded to `refs/heads/main`, so dispatching it from
any other ref builds nothing and deploys nothing.

| Exists | Does not exist |
|---|---|
| a validation workflow (`docs.yml`) that runs the content gate and `mkdocs build --strict` on pull requests, deploying nothing | a deploy from a pull request, another branch, a tag, or a schedule |
| one reviewed publication workflow (`pages-publish.yml`): push to `main`, plus manual `workflow_dispatch` as a re-run path | a second workflow that can publish, or a publication surface outside the reviewed one |
| a content gate that pins publication to that one reviewed workflow | a `gh-pages` branch or `mkdocs gh-deploy` anywhere |

!!! contract "Publication is automatic on main, and pinned to exactly that"

    The content gate holds the workflow to exactly that decision: a publication
    marker in any other active workflow, a trigger beyond push-to-`main` and
    `workflow_dispatch`, a widened branch filter, a missing `refs/heads/main`
    job guard, a write grant beyond `pages` and `id-token`, or a permissions
    block that is missing or widened fails the repository gate. The complete
    `pages-publish.yml` file is also pinned by SHA-256, so changing a trigger,
    step, input, job placement, or even a comment requires an explicit digest
    update in the same reviewed change. That digest is a drift detector, not a
    substitute for code review: reviewers must examine workflow and digest
    changes together.

## Local preview

You never need to publish to see the site:

```bash
make docs-serve     # local preview
make docs           # docs-sync + docs-check + mkdocs build --strict
```

`make docs` writes to `site/`, which version control ignores. Do not commit
generated output.

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
step only**. There is no active job that deploys anything, no Pages environment
in use, and no `gh-pages` branch — the build produces an artifact that is checked
and discarded.

| Exists | Does not exist |
|---|---|
| a build-and-validate workflow that runs `mkdocs build --strict` and the content gate | any workflow job that publishes, deploys, or uploads a Pages artifact |
| an explicit content gate that rejects Pages publication markers in active workflows | an enabled deploy trigger, a Pages environment, or `pages: write` permission on an active workflow |

Enabling publication would take three separate decisions — configuring the
repository's Pages settings, enabling the workflow, and authorizing the first
deploy — and none of them follows from this documentation, from a merged change,
or from a green build.

!!! contract "Why no publication workflow is committed yet"

    The eventual publication workflow will be a separate reviewed change after
    Pages configuration and the first deploy are explicitly authorized. Until
    then, the site's gate asserts that no active workflow acquires a deploy job;
    dormancy is enforced rather than merely intended.

## Local preview

You never need to publish to see the site:

```bash
make docs-serve     # local preview
make docs           # docs-sync + docs-check + mkdocs build --strict
```

`make docs` writes to `site/`, which version control ignores. Do not commit
generated output.

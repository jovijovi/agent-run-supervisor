---
title: Licenses
description: Licensing for agent-run-supervisor and for every third-party asset this site vendors.
---

# Licenses

## agent-run-supervisor

© the `agent-run-supervisor` authors. Released under the
[MIT License](https://opensource.org/license/mit). The full text ships in the
repository as `LICENSE`.

## This documentation

The documentation content is part of the same repository and carries the same
MIT license.

## Vendored assets

This site loads **no external asset**. Fonts, diagram rendering, and every image
are served from the site's own origin, so no third-party request is made when a
page loads. That means the site vendors a small number of third-party files, and
each is listed here with its license.

Provenance and upgrade instructions for each file are recorded in
`website/ASSET-PROVENANCE.md` in the repository.

### IBM Plex Sans and IBM Plex Mono

- **Copyright** © 2017 IBM Corp., with Reserved Font Name "Plex".
- **License:** [SIL Open Font License, Version 1.1](https://openfontlicense.org/).
- **Full text:** [`OFL.txt`](../assets/fonts/OFL.txt), served alongside the fonts.
- **Files:** six WOFF2 faces under `website/docs/assets/fonts/` — Sans regular,
  regular italic, semibold, and bold; Mono regular and semibold.

The fonts are used unmodified and are not renamed, which is what the Reserved
Font Name clause requires.

### Mermaid

- **License:** MIT.
- **File:** `website/docs/assets/javascript/mermaid.min.js`, a single
  self-contained distribution bundle, used unmodified.

Mermaid is vendored rather than loaded from a CDN so that diagram rendering works
offline, under a strict content-security policy, and without any third-party
request. The theme's own integration only fetches Mermaid when it is not already
present, so defining it locally first is sufficient.

### Material for MkDocs

The site is built with [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)
(MIT), installed as a build-time dependency in the `docs` extra. Its assets are
generated into the built site by the build itself; they are not vendored into the
repository.

!!! note "Build dependencies are not runtime dependencies"

    The ARS runtime has **zero** third-party dependencies. Everything named above
    is either a vendored static asset or a documentation build-time package in a
    separate optional extra. Installing `agent-run-supervisor` installs none of
    it.

## Trademarks

Product and agent names used in the [how-to guides](../how-to/index.md) belong to
their respective owners. Their appearance describes integration only: ARS
supervises the command an operator registered, and does not install, package, or
endorse any agent.

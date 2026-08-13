# Vendored asset provenance

Every third-party file the documentation site serves, where it came from, and how
to replace it.

This file lives **outside** `docs_dir` on purpose: it is a maintenance record for
the repository, not a public page. The public-facing license summary is
`website/docs/about/licenses.md`.

## Why anything is vendored at all

The built site makes **no external request**. Fonts and diagram rendering are
served from the site's own origin, which keeps the site working offline, under a
strict content-security policy, and without leaking a reader's address to a
third-party host. `tools/check_docs_site.py` enforces that property, so removing
a vendored file in favour of a CDN URL fails the gate rather than silently
changing the site's behaviour.

## IBM Plex Sans / IBM Plex Mono

| Field | Value |
|---|---|
| Upstream | `@ibm/plex-sans` and `@ibm/plex-mono`, version `1.1.0` |
| Source path | `fonts/complete/woff2/` within each package |
| License | SIL Open Font License 1.1 — vendored as `docs/assets/fonts/OFL.txt` |
| Copyright | © 2017 IBM Corp., with Reserved Font Name "Plex" |
| Modified | no. Files are byte-identical to upstream and renamed only for clarity |

| Repository file | Upstream file |
|---|---|
| `docs/assets/fonts/ibm-plex-sans-400.woff2` | `IBMPlexSans-Regular.woff2` |
| `docs/assets/fonts/ibm-plex-sans-400-italic.woff2` | `IBMPlexSans-Italic.woff2` |
| `docs/assets/fonts/ibm-plex-sans-600.woff2` | `IBMPlexSans-SemiBold.woff2` |
| `docs/assets/fonts/ibm-plex-sans-700.woff2` | `IBMPlexSans-Bold.woff2` |
| `docs/assets/fonts/ibm-plex-mono-400.woff2` | `IBMPlexMono-Regular.woff2` |
| `docs/assets/fonts/ibm-plex-mono-600.woff2` | `IBMPlexMono-SemiBold.woff2` |

Renaming the file does not rename the font: the `font-family` in
`docs/assets/stylesheets/ars.css` stays `IBM Plex Sans` / `IBM Plex Mono`, which
is what the Reserved Font Name clause governs.

Six faces are vendored and no more. Each additional face is a real download for a
reader, so a new weight has to earn its place.

### Replacing them

Download the same six files from the same paths at the new version, keep the
repository names, and update the version in the table above. Then confirm
`@font-face` blocks in `ars.css` still point at every file — the gate checks that
each `src:` resolves to a file that exists, and that no `@font-face` references a
remote host.

## Mermaid

| Field | Value |
|---|---|
| Upstream | `mermaid`, version `11.16.1` |
| Source path | `dist/mermaid.min.js` |
| Repository file | `docs/assets/javascript/mermaid.min.js` |
| License | MIT |
| Modified | no |

Two properties make single-file vendoring viable, and both must be rechecked on
upgrade:

1. **The bundle is self-contained.** It performs no dynamic `import()`, so no
   chunk directory has to be vendored beside it. Verify with a search for
   `import(` in the downloaded file before committing it.
2. **It assigns the global.** The file ends by assigning `globalThis["mermaid"]`.
   Material for MkDocs only fetches Mermaid from a CDN when `window.mermaid` is
   still undefined, so `website/overrides/main.html` loads this file *before* the
   theme bundle and the CDN fetch never happens.

If a future version breaks either property, the correct response is to pin the
last version that holds them and open an issue — not to fall back to the CDN,
which would make the "no external asset" gate decorative.

The file is large (~3.5 MB). That is the accepted cost of an offline-capable,
CSP-clean site; it is downloaded by a reader only on pages that contain a
diagram.

## Authored assets

These are ours, not vendored, and carry the repository's MIT license:

| File | Notes |
|---|---|
| `docs/assets/logo/logo-mark-64.png` | 64×64 web derivative of the canonical project logo; used by the header and favicon |
| `docs/assets/stylesheets/ars.css` | brand tokens, self-hosted type, homepage shell |
| `docs/assets/javascript/ars.js` | two reading affordances; no network access |
| `overrides/main.html` | loads the vendored Mermaid before the theme bundle |
| `overrides/home.html` | drops nav and TOC on the landing page |

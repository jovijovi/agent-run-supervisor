---
title: "ARS Public Documentation Site Design"
status: active
created_at: 2026-08-07
last_validated_at: 2026-08-07
---
# ARS Public Documentation Site Design

## Status and decision

This document records the accepted design for a public ARS documentation site. It is a design record: it
describes what was decided and what the target looks like, and it authorizes nothing by its existence.
Implementation, repository/Pages configuration, publication, and deployment are each separate, explicitly
authorized steps. Where this document names target files, workflows, or behaviors, those are design
statements about the approved direction, not claims that they exist.

Authority for product facts is the current authority chain: `GOAL.md`, `docs/product/prd.md`,
`docs/design/architecture.md`, `docs/design/technical-solution.md`, `docs/design/agent-registry.md`,
`docs/roadmap/features.md`, `docs/roadmap/current-status.md`, `docs/roadmap/non-approvals.md`, and
`docs/AI_FLOW.md`. Nothing under `docs/archive/`, `docs/plans/archive/`, or `docs/roadmap/archive/` informs
this design.

## Goals and audience

- The site is public, GitHub-hosted technical documentation for first-time ARS integrators.
- Primary success path: a new developer understands what ARS is and reaches a first local AGENT Run in
  about five minutes.
- The site is documentation only. It is not a runtime console, a roadmap, a release log, a
  deployment-status board, or a private operational dashboard, and it never presents itself as one.

## Design principles

1. **Authority-aligned.** Public pages restate the current product authority in neutral public language;
   they never invent capabilities, security claims, implementation state, or adjacent work.
2. **Task-oriented.** Navigation is organized around what an integrator is trying to do, not around the
   repository's internal document layout.
3. **Deliberately bounded.** Public navigation is an explicit allowlist. Internal roadmap, active plans,
   lessons, archives, private runtime details, and superseded documents stay out of the site.
4. **Restrained identity.** A distinctive "flight recorder / precision instrument" brand, kept accessible
   and bounded, without generic AI-gradient or glassmorphism styling and without copying any other
   project's visuals.
5. **Low-maintenance tooling.** A mature static-site stack with no Node application toolchain, and a docs
   build that stays independent from the Python package release/publish workflows.

## Technical architecture

Decisions:

- **MkDocs Material** builds the site and provides search. The mature approach seen in PyPepper is
  inspiration for structure and polish, not a source of copied branding or content.
- **mkdocstrings** generates the Python API reference from source docstrings.
- **Mermaid** renders diagrams.
- **Small custom CSS/JS** carries the brand identity and limited interaction. This layer must stay bounded
  and accessible.
- **No React, no Docusaurus, no Node application toolchain.**
- **Self-hosted fonts.** IBM Plex Sans and IBM Plex Mono are vendored and served from the site; there are
  no external font calls.
- **Hosting is GitHub Pages through GitHub Actions.** Pages consumes Actions artifacts; no `gh-pages`
  commit branch is maintained.
- **The docs build/deploy pipeline is independent** from the Python package release and publish workflows.
  A documentation change never implies a package release, and a package release never implies a docs
  deploy.

## Information architecture

Top-level utility/navigation: **Docs**, **API Reference**, **GitHub**, **search**, and a version surface
only when its source and behavior are implemented accurately — no placeholder version widget.

Task-oriented documentation navigation:

- Overview
- Quickstart
- Core Concepts
- How-to Guides
- Deployment
- Reference
- Contributing

Initial content set:

- Branded homepage.
- A 5-minute Quickstart.
- Core concept pages: Agent, Run, Session, Binding/Profile, Native ACP.
- Integration guides for Claude Code, Codex CLI, OpenCode, and Cursor CLI.
- Deployment guidance for the local daemon and systemd.
- Reference pages: Socket API, configuration, events, results, and error codes.
- Python API reference (mkdocstrings).
- Contribution, testing, and publishing guidance.

Content sourcing:

- Existing active docs are reorganized and reused where accurate; missing user-facing pages are written
  only as needed.
- Internal roadmap, active plans, lessons, archives, private runtime details, and superseded documents are
  excluded from public navigation through an explicit allowlist.

## Homepage and UX

- Hero message: **"Supervise local AGENT runs. Preserve context. Recover with evidence."**
- Immediate paths from the hero: **Quickstart** and **Runs & Sessions**.
- The homepage shows a **static** product-model trajectory: Agent → Session → Run → Events → Result. It is
  an illustration of the model and must not pretend to be a live console.
- There is no "Open Console" link or button anywhere: ARS has no public web console.
- One-minute comprehension goals for the homepage: what ARS is, Run vs Session, supported local AGENTs,
  how to submit the first Run, and where to find configuration, API, errors, and deployment material.
- Documentation pages use left navigation, a main article column, and a right table of contents;
  recorder-style code blocks; and semantic Note / Contract / Warning / Danger callouts.

## Brand direction

"Flight recorder / precision instrument": restrained and trustworthy. Explicitly not generic AI gradients,
glassmorphism, or copied PyPepper visuals.

Palette:

| Role | Value |
|---|---|
| Deep ink | `#111820` |
| Warm paper | `#F7F6F2` |
| Cool teal | `#17A6A6` |
| Amber | `#D8922B` |

Typography: IBM Plex Sans for headings and body; IBM Plex Mono for code.

## Product-contract accuracy

Public pages must hold to the current product contract, in these terms:

- Neutral public product language: trusted local caller (local AGENT or CLI) → `arsd` → ars-core /
  Native ACP → external ACP AGENT.
- Runs terminate. Sessions are durable, resumable indefinitely, and have no normal close, expiry, or
  terminal lifecycle. Quarantine, the concurrency lease, and retention are orthogonal concerns — none of
  them is a Session lifecycle.
- ARS supervises external ACP AGENTs; it does not own, install, or package their software, homes,
  credentials, plugins, caches, configuration, or conversation stores.
- ACP permission mediation is cooperative policy enforcement, not an OS sandbox. The site makes no
  hostile-code isolation claim.
- The site claims no public network service and no web console.
- The site never mentions private host paths, account names, private socket paths, service state, credentials,
  current deployment, current PRs, or current roadmap gates.
- The four named AGENT integration guides are the initial guide set, not a protocol-level closed universe:
  operator registration and profile contracts govern which AGENTs are supported, and the site must not
  present a fixed AGENT list as a protocol boundary.

## Repository target shape

Proposed layout (design target, not implemented by this record):

```text
mkdocs.yml
docs/index.md
docs/getting-started/
docs/concepts/
docs/how-to/
docs/deployment/
docs/reference/
docs/contributing/
docs/assets/logo/
docs/assets/fonts/
docs/assets/stylesheets/
docs/assets/javascript/
docs/overrides/home.html
```

This is a target layout and may need collision-safe adaptation: the repository already has authoritative
docs under `docs/` (product, design, roadmap, plans, lessons, and other governed surfaces). Implementation
must preserve that authority — the existing documents remain the product authority chain — and must avoid
accidental publication of internal surfaces. The allowlist, not the directory tree, decides what the
public site exposes.

## CI/CD

- Pull requests build and check the site but never deploy production.
- Deploys from `main` may happen only after separate repository/Pages implementation and explicit
  authorization.
- GitHub Pages consumes Actions artifacts; no `gh-pages` commit branch is created or maintained.
- The site build and deploy workflows are separate from, and never trigger or are triggered by, the Python
  package release/publish workflows.

## Quality and acceptance gates

Build and content gates:

- `mkdocs build --strict` passes.
- Internal link and navigation validation passes.
- API symbol resolution (mkdocstrings) passes.
- Mermaid diagram validation passes.
- A secret / private-path / private-runtime-state scan passes.
- The public-navigation allowlist is enforced and excludes archive, superseded, and internal governance
  surfaces.
- The site stays consistent with the README and the product authority chain.

Review gates:

- Real-browser smoke and screenshot review at desktop, tablet, and mobile sizes.
- Verified behavior of: search, code copy, Mermaid rendering, long code blocks, tables, keyboard focus,
  color contrast, and absence of horizontal overflow.
- Screenshot inspection of at least the homepage, Quickstart, Concepts, and Reference pages.
- After a separately authorized Pages deployment, a smoke check of the real public URL.

Repository gates for this document itself:

- The repository-native generated docs index and drift contracts
  (`tools/build_docs_index.py`, `tools/docs_drift_signal.py`) and `make verify` remain required for the
  design-document change.

## Alternatives considered

- **Minimal/default Material clone.** Rejected: too generic and too weak for the explicit brand and UX
  requirement.
- **Docusaurus or custom React.** Rejected for now: unnecessary Node/front-end maintenance burden and a
  poorer fit for Python API reference generation at this stage.
- **Separate documentation repository.** Rejected: creates sync and provenance overhead; same-repo docs
  keep code and the public contract reviewable together.

## Consequences and trade-offs

- The small custom theme layer must stay bounded and accessible; it is a liability if it grows into a
  bespoke frontend.
- Strict public allowlisting requires deliberate, ongoing content curation; reuse of existing docs is
  never automatic publication.
- mkdocstrings introduces documentation-build dependencies, but no ARS runtime dependencies; the runtime
  stays stdlib-only.
- GitHub Pages/Actions hosting requires separate repository configuration and implementation approval
  before any deploy occurs.

## Non-goals and first-release exclusions

- No web console and no live Run data.
- No fake online demo.
- No multilingual site initially.
- No multi-version documentation initially.
- No separate documentation repository.
- No Docusaurus/React.
- No third-party search service initially.
- No ARS product, protocol, runtime, or deployment changes.
- No implementation, Pages enablement, publication, or deployment authorization follows from this design
  record.

## Implementation boundary

This document records a design decision only. It does not authorize creating `mkdocs.yml` or any site
content, configuring GitHub Pages or Actions deploys, publishing anything, or changing any ARS product,
runtime, or deployment surface. Each of those is a separate, explicit operator decision. Approvals are
narrow and non-transitive: accepting this design approves none of the steps that would realize it.
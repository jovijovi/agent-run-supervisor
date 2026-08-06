# AGENTS.md

Project-local instructions for AI agents working in this repository.

## Portable paths and identity

Committed documents, examples, and rules use **repository-relative paths only**. Never commit a host- or
user-specific absolute path, home directory, workspace or worktree root, checkout location, account name,
or remote/visibility fact. Use a repository-relative path (`docs/design/architecture.md`,
`scripts/verify_local.sh`) or a neutral placeholder (`<repo-root>`, `/path/to/<thing>`,
`<service-home>/...`) instead.

Discover the checkout path, worktree layout, remotes, and branch state at runtime. They are environment
facts, not repository content, and recording them here makes the instructions wrong for the next
environment.

## Product and documentation preflight

Documentation precedes code. For roadmap, design, implementation, PR, CI, review, merge, or
next-phase-readiness work, read in order:

1. `GOAL.md`
2. `docs/product/prd.md`
3. `docs/design/architecture.md`
4. `docs/design/technical-solution.md`
5. `docs/roadmap/features.md`
6. `docs/roadmap/current-status.md`
7. `docs/AI_FLOW.md`

`docs/design/agent-registry.md` is the operator-facing registry contract and is read with the design layer.
`docs/design/result-event-schema.md` describes emitted JSON shapes and is derivative — never a source of
product scope.

Before changing files, state the current product position, the feature/phase target, open tails, explicit
non-approvals (`docs/roadmap/non-approvals.md`), and whether the requested task is allowed by the roadmap.

The authority chain above is the sole basis for new development. Everything under `docs/archive/`,
`docs/plans/archive/`, and `docs/roadmap/archive/` is cold history: never load it by default, and never use
it to choose product scope, modules, branches, gates, acceptance, or approval. Git history remains the
implementation audit trail.

## Development workflow

Use short-lived task branches and isolated worktrees for AI-assisted work. Branch prefixes: `feat/`,
`fix/`, `docs/`, `cicd/` — see `docs/AI_FLOW.md` § Branch model. Do not use `cursor/` or other ad-hoc
prefixes. Derive implementation plans from PRD/design/roadmap; a plan must not redefine product goals.

Task role/model assignment comes from the current user/controller authorization, not from archived plans or
a repository-pinned AGENT. Preserve independent, fresh-context review for authority-bearing or
implementation changes. The controlling human/operator role owns scope control, deterministic verification,
evidence arbitration, and all side-effect authority.

### Post-implementation documentation sync

When implementation work is **fully complete** (code, tests, and verification gates pass), check whether
project documentation needs updating. Update when needed — do not treat doc sync as optional tail work.
Typical surfaces:

- `docs/` — board, features, `docs/plans/active/` or archive moves, design/product when behavior or
  acceptance changed; closed phase detail → `docs/roadmap/archive/phases/`.
- `README.md` and `README.zh-CN.md` — when CLI usage, library API, install/dev/publish instructions, or
  examples changed.
- `CHANGELOG.md` — when preparing user-visible release notes (usually before a release).
- When a plan's work merges: `git mv` from `docs/plans/active/` to `docs/plans/archive/`, and update the
  board `active_plan:` and phase archive as needed.

Run `python tools/build_docs_index.py --write` and `python tools/docs_drift_signal.py --write` after
governed docs changes (see Verification and tooling).

### Implementation plan context

- Read **`docs/plans/active/`** only for in-flight execution plans (plus the board `active_plan:`).
- Do **not** load `docs/plans/archive/` or `docs/roadmap/archive/` by default — audit/dispute only.

### Release and publishing authorization

Git tag creation, GitHub Release publication, and PyPI package publishing are **not** part of default
implementation work. Perform them **only after explicit human permission and authorization** — typically
after development is finished, documentation is synced, and verification passes.

Do not tag, publish a release, or upload to PyPI proactively, during active implementation, or immediately
after a merge without an explicit request.

## Product boundaries

ARS supervises external ACP AGENTs it does not own: local caller-authenticated `arsd` UDS ingress,
ars-core / Native ACP, one supervised process per Run with real Session load, an immutable per-Run request
and grant, and fail-closed evidence and recovery. `GOAL.md` and the design layer own the detail; do not
restate architecture here.

`acpx` is **not** a product, runtime, or compatibility surface. It was never a Native ACP driver,
fallback, or degraded path, and is never a reason to restore archived product requirements. Its product,
runtime, and compatibility content — code, CLI leaves, fixtures, and the result field named after its
process exit — **has been removed**. Do not reintroduce it under any spelling: no shim, alias, flag, bridge,
dual runtime, renamed field, or re-added capability. `tools/static_safety_scan.py` and the wheel/sdist
manifest allowlists refuse its return; naming it in prose to refuse it, or in the past tense, stays legal.

Native ACP state stays in its own isolated run/session roots. Native code never reads, writes, imports,
mirrors, or migrates any pre-existing legacy session storage in either direction.

**Source work authorizes source, tests, and docs only.** It never authorizes installing an artifact,
writing production configuration, enabling or restarting a service, rollout, cutover, migration, release,
publication, deployment, or integration with a caller platform. Each of those is a separate explicit
operator decision, and none of them is implied by a merged change or a green gate.

Do not infer service enablement, release or publication, public ingress, delivery, live or default-on
behavior, real AGENT auto-replies, or agent-to-agent auto-routing from documentation or governance changes.

## Secrets and credentials

Never commit secrets, API keys, tokens, cookies, raw environment values, real webhook secrets, or private
platform identifiers.

Use `[REDACTED]` in docs and examples when referring to sensitive values. Keep real runtime values in local
environment files that are ignored by git.

## Verification and tooling

- **`make verify` is the canonical complete gate.** It runs `./scripts/verify_local.sh`, the underlying
  verifier that CI runs as well. Only a green `make verify` (or a full `./scripts/verify_local.sh`) is
  completion evidence.
- **Focused checks are partial**, useful while iterating and never a substitute for the full gate. Do not
  present any of them as completion proof:
  - `python3 -m pytest -q` — tests
  - `python3 -m compileall -q src scripts tests` — syntax/import smoke
  - `PYTHONPATH=src python3 -m agent_run_supervisor ...` — local CLI smoke, unless the package is installed
    in the active environment
  - `python tools/build_docs_index.py --check`, `python tools/docs_drift_signal.py --check` — docs signals
- Runtime should stay Python stdlib-only unless a phase explicitly approves dependencies.
- Run `python tools/build_docs_index.py --write` after docs changes, and never hand-edit `docs/INDEX.md`.
- Run `python tools/docs_drift_signal.py --write` after governed docs changes.
- Before a release: `make bump VERSION=X.Y.Z` (or `uv run python tools/bump_version.py X.Y.Z`) to sync
  `pyproject.toml`, `src/agent_run_supervisor/__init__.py`, `uv.lock`, and a CHANGELOG stub; then edit
  CHANGELOG and run `make verify` (includes `tools/check_version_sync.py`).

## Knowledge document validation

Lessons and practices carry `last_validated_at`. Validation is use-driven: when a commit, PR body, or
active project doc cites a specific lesson or practice path, the citing change must either bump
`last_validated_at`, refine the document and bump it, or deprecate/supersede it.

`tools/docs_drift_signal.py` writes `docs/lessons/_drift_report.md`. Treat it as a signal that the next
citing change must process the named knowledge docs.

---
title: "S1a Persistent-Session Contract Spike Plan"
status: archived
created_at: 2026-05-30
last_validated_at: 2026-05-30T00:00:00+0800
archived_at: 2026-07-07
---
> **Archived plan（冷区）：** 非 active 上下文。Roadmap 章节迁移见
> [`docs/roadmap/MIGRATION.md`](../roadmap/MIGRATION.md)。
> 验收摘要见对应 [`docs/roadmap/archive/phases/`](../roadmap/archive/phases/) 条目。

# S1a Persistent-Session Contract Spike Plan

> **For Hermes:** this is a **contract-evidence** spike, not an implementation phase.
> Claude Code may be the main worker; Codex CLI is the primary reviewer; Hermes owns
> scope, verification, evidence, and arbitration. This plan captures observed
> `acpx@0.10.0` persistent-session command grammar and stdout schemas so that the
> later S1 implementation phase builds on proven evidence. It does **not** implement
> session support.

## Goal

Capture and validate the observed `acpx@0.10.0` persistent-session **command grammar**
and **stdout schemas** as contract fixtures and documentation, so S1 implementation can
later derive a session store, locks, and lifecycle from proven evidence rather than
guesses.

S1a delivers **command/schema evidence only**. It does not create a session store,
session CLI/library surface, locks, leases, stale-lock recovery, or any persistent
session runtime. `F-SESSION-001` and roadmap phase `S1` remain **Planned** and
unimplemented after this spike.

## Source-of-truth trace

- Product goal: `GOAL.md`
- Product requirements: `docs/product/prd.md`, especially FR-5 (persistent session
  supervision), and the FR-2/FR-6/FR-7/FR-8/FR-9 session tails that depend on proven
  session fixtures.
- System architecture: `docs/design/architecture.md` §4 (persistent session lifecycle —
  🟦 planned S1) and §2.1 (contract anchor).
- Technical design: `docs/design/technical-solution.md` session/parser sections.
- Feature tracker: `docs/roadmap/features.md`, `F-SESSION-001` (Planned) and
  `F-PARSER-001` session tail.
- Roadmap phase: `docs/roadmap/current-status.md`, `S1 — Persistent session support`,
  first checklist item ("Capture fresh acpx session command fixtures and observed event
  shapes"), and tail `ARS-SESSIONS`.
- Workflow: `docs/AI_FLOW.md`.

Per `docs/design/architecture.md` §4: "exact command grammar, event shapes, and
artifact filenames remain open until fresh acpx session fixtures are captured and
validated." S1a closes that evidence gap; it does not open S1 implementation.

## Scope

In scope:

- Capture real `acpx@0.10.0` persistent-session fixtures under a single scratch cwd and
  one deterministic session name (`s1a-session-contract`).
- Record the **prompt-turn** stdout family (raw newline-delimited ACP/JSON-RPC stream,
  stored as `stdout.ndjson`) including a follow-up turn that proves multi-turn
  continuity.
- Record the **management-command** stdout family (`sessions new/ensure/show/history/read/close`,
  `status`, `cancel`), each a single summarized JSON object stored as `stdout.json`.
- Extend the contract validator and its tests to validate the new fixtures' naming,
  stdout-file kind, exit semantics, schema markers, and secret-shaped content.
- Cross-check evidence in a machine-readable summary (`session-contract-summary.json`)
  and a manifest section (`session_contract`), and document findings in the fixtures
  README.
- Update `docs/roadmap/current-status.md` and `docs/roadmap/features.md` narrowly to
  reflect captured contract evidence while keeping S1 implementation Planned.
- Regenerate `docs/INDEX.md` and `docs/lessons/_drift_report.md`.

Out of scope / still not approved (unchanged by this spike):

- Session store layout, session create/open/send/close/abort runtime.
- `AgentRoleSpec` session config changes, session parser/event code, session classifier
  details, session CLI/library surface.
- Locks, leases, stale-lock recovery, crash/interruption handling, mismatch refusal.
- Sachima/Hermes behavior integration; real AGENT automatic replies.
- public ingress; real IM delivery; Gateway restart/reload/replace; production config
  writes; live/default-on behavior; worker auto-routing; participant persistence or
  management UI; `@all` fanout; agent-to-agent automatic routing; trusted Markdown/HTML
  rendering; treating `allowed_roots` as an OS/filesystem sandbox; per-run human approval
  as the default authorization model.

## Definition of Ready

- R0 documentation authority realignment is merged and closed (PR #6, `7dcbe4f`).
- C0 acpx exec fixtures and validator exist and pass.
- E1 one-shot exec runner is merged and closed (PR #8, `21b3393`); `F-EXEC-001` is Done.
- S1 acceptance and tail `ARS-SESSIONS` are recorded in `docs/roadmap/current-status.md`.
- This plan records the spike scope and the unchanged non-approvals.
- Work starts from a clean `origin/main` worktree.

## Implementation goals (checklist)

This spike captures **evidence**; "implementation" below means fixtures, validator
coverage, and documentation — never session runtime code.

- [x] Capture prompt-turn fixtures `session-prompt-turn1` and `session-prompt-turn2`
      with raw `stdout.ndjson`, proving turn2 reuses the same ACP session id and skips
      `initialize`/`session/new`.
- [x] Capture management-command fixtures for new/ensure/show(open/after/closed)/history/
      read/status/cancel/close with single-object `stdout.json`.
- [x] Record both stdout families as distinct schemas (prompt NDJSON vs management
      single-object JSON) in `session-contract-summary.json` and manifest `session_contract`.
- [x] Extend `scripts/validate_contract_fixtures.py` to validate the session fixtures
      (naming, stdout-file kind, exit codes, schema markers, secret-shaped scan).
- [x] Extend `tests/test_validate_contract_fixtures.py` to cover the session validation.
- [x] Document command grammar, per-fixture semantics, and S1-constraining findings in
      `fixtures/acpx-0.10.0/README.md` with an explicit evidence-only scope banner.
- [x] Update `docs/roadmap/current-status.md` S1 checklist (fixture capture only) and
      `docs/roadmap/features.md` `F-SESSION-001` evidence, keeping S1 Planned.
- [x] Regenerate `docs/INDEX.md` and `docs/lessons/_drift_report.md`.

## Acceptance criteria

- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0` passes with the
  session fixtures included.
- `python3 -m pytest -q` passes, including the extended validator tests.
- The fixtures README and `session-contract-summary.json`/manifest `session_contract`
  agree on fixture names, exit codes, stdout-file kinds, schema markers, and the joined
  agent text for each prompt turn.
- Evidence proves the four S1-constraining findings: prompt vs management schemas
  differ; session continuity is real (shared ACP session id across turns); idle cancel
  is not close; close is observable (`closed=true` + `closedAt`); and `acpSessionId`
  rotates from the stable `acpxRecordId`.
- No fixture or doc implies session support is implemented. `F-SESSION-001` and `S1`
  remain Planned; tail `ARS-SESSIONS` remains Open.
- Secret-shaped scans over captured fixtures and added doc lines find no real secrets.
- No session runtime code is added in this spike.

## Files

Captured / changed by the spike (evidence + validation):

- `fixtures/acpx-0.10.0/session-prompt-turn1/`, `fixtures/acpx-0.10.0/session-prompt-turn2/`
- `fixtures/acpx-0.10.0/session-new-named/`, `session-ensure-existing/`,
  `session-show-open/`, `session-show-after-turns/`, `session-show-closed/`,
  `session-history-after-turns/`, `session-read-tail-after-turns/`,
  `session-status-after-turns/`, `session-cancel-no-active/`, `session-close-named/`
- `fixtures/acpx-0.10.0/session-contract-summary.json`
- `fixtures/acpx-0.10.0/manifest.json` (`session_contract` section)
- `fixtures/acpx-0.10.0/README.md` (S1a contract spike section)
- `scripts/validate_contract_fixtures.py`, `tests/test_validate_contract_fixtures.py`

Documentation updated by this plan:

- `docs/plans/archive/2026-05-30-s1a-session-contract-spike.md` (this file)
- `docs/roadmap/current-status.md` (S1 fixture-capture checklist + evidence note)
- `docs/roadmap/features.md` (`F-SESSION-001` evidence)
- `docs/INDEX.md`, `docs/lessons/_drift_report.md` (regenerated, not hand-edited)

Explicitly **not** touched (would be S1 implementation): `src/agent_run_supervisor/`
session store/parser/CLI surfaces.

## Verification gates

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
git diff --check
```

Additional spike gates:

- Secret-shaped scan over captured fixture stdout and added doc lines.
- Confirm `src/agent_run_supervisor/` has no session runtime additions in the diff.
- Codex CLI primary review of the final diff; Hermes scope/evidence arbitration.

## Risks / open questions

- **Schema drift risk.** Evidence is pinned to `acpx@0.10.0`. A future acpx version may
  change session command grammar or stdout shapes; S1 must re-validate fixtures before
  trusting them. Drift fails closed via the contract validator.
- **`acpSessionId` vs `acpxRecordId` (open for S1).** The live `acpSessionId` rotates
  from the stable record id once the agent process starts; S1 persistence must record
  both. This spike only documents the observation.
- **Lock/lease semantics not captured.** Concurrency, stale-lock recovery, and
  crash/interruption behavior are S1 design/implementation concerns; no fixture proves
  them and none is claimed.
- **Management vs prompt parsing (open for S1).** A future session parser must branch on
  command kind; management JSON must never be treated as an exec/prompt success stream.
  S1a records the distinction but adds no parser.
- **Scratch-only capture.** Fixtures use a single scratch cwd and one session name; they
  are connectivity/contract sentinels, not coverage of every session state transition.

## Rollback strategy

- Keep all work on branch `ai/s1a-session-contract-2026-05-30` in an isolated worktree.
- The spike adds only fixtures, validator/test coverage, and documentation; reverting the
  branch removes all of it with no runtime or production state touched.
- No `src/` runtime code, no external service, and no production config is modified, so
  rollback is a clean branch discard.
- Captured fixtures are redacted local evidence committed as contract artifacts; if any
  fixture is later found to leak or to misrepresent the contract, delete that fixture and
  its validator/summary/manifest/README references together.

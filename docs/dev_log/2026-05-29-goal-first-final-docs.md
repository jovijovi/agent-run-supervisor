---
title: "Goal-first final docs dev log"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T11:09:05+0800
---
# Goal-first final docs dev log

## Task

Create the final documentation set requested by the user:

1. PRD with features, requirements, and goals.
2. Complete technical solution.
3. Phased engineering implementation plan with goals, checklist, completion markers, and acceptance standards.
4. Add a V0.1a design conformance matrix comparing current implementation with `docs/design/v0.1a-design.md`.

## Preflight

Read and used:

- `GOAL.md`
- `docs/roadmap/current-status.md`
- `docs/AI_FLOW.md`
- `docs/design/v0.1a-design.md`
- current implementation under `src/agent_run_supervisor/`
- tests under `tests/`

Repository state at start:

```text
branch: main
HEAD: cc86f8cdc42221a2a2750f8404fb6193b3dba279
status: clean
```

`session_search` did not return the previous detailed transcript for `agent-run-supervisor`; this task therefore used repo authority docs and the compacted handoff context rather than inventing missing chat memory.

## Key correction

The V0.1c manual approval branch is deprecated as product direction. It conflicts with the established decision that authorization/permissions are bound to long-lived `AgentRoleSpec` roles, not per-run human approval artifacts.

Current source of truth is restored to `docs/design/v0.1a-design.md`.

## Files created

- `docs/product/prd.md`
- `docs/design/technical-solution.md`
- `docs/roadmap/implementation-plan.md`
- `docs/roadmap/v0.1a-design-conformance.md`
- `docs/plans/2026-05-29-goal-first-final-docs.md`
- `docs/dev_log/2026-05-29-goal-first-final-docs.md`

## Files updated

- `GOAL.md`
- `docs/roadmap/current-status.md`
- `docs/design/v0.1c-hitl-manual-real-run-design.md`
- `docs/INDEX.md`
- `docs/lessons/_drift_report.md`

## Implementation vs v0.1a-design summary

Completed or mostly complete:

- Phase -1 fixtures and validator.
- AgentRoleSpec schema/validation.
- role-bound policy/argv compiler.
- cwd/allowed_roots gate.
- exit classifier.
- observed stdout parser.
- EventStore permissions.
- redaction helpers.
- CLI `validate-role`, `replay`, `doctor` baseline, and `run --no-real-run`.

Incomplete:

- true CLI `run` exec-only subprocess launch path;
- outer watchdog/process-group lifecycle and detailed kill metadata;
- final stdout/stderr capture from real subprocess into CLI run;
- full doctor probes for adapter availability, runtime `npx` fetch, policy parseability, role cwd, and redaction;
- retention/cleanup knobs before long-lived use.

## Verification log

Initial pre-doc baseline:

```text
python3 -m pytest -q -> pass (123 tests collected)
python tools/build_docs_index.py --check -> OK: 20 docs in sync with INDEX.md
python tools/docs_drift_signal.py --check -> OK: drift report up to date
git diff --check -> pass
```

Final verification after docs index/drift regeneration:

```text
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0 -> OK
python3 -m pytest -q -> pass (123 tests)
python3 -m compileall -q src scripts tests -> pass
PYTHONPATH=src python3 -m agent_run_supervisor doctor -> ok=true, fixture final_message=CODEX_ACPX_OK
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson -> protocol_error=false, final_message=CODEX_ACPX_OK
python tools/build_docs_index.py --check -> OK: 26 docs in sync with INDEX.md
python tools/docs_drift_signal.py --check -> OK: drift report up to date
git diff --check -> pass
secret-shaped scan over changed/untracked files -> 0 findings
static source scan over changed source files -> 0 findings
Codex read-only sandbox review -> BLOCK due sandbox environment only (`bwrap: loopback: Failed RTM_NEWADDR` before file reads)
Codex danger-full-access review-only rerun -> PASS; no blockers; non-blocking stale wording note in conformance matrix patched
```

## Boundary statement

This task is documentation/governance only. It does not approve or implement:

- real AGENT automatic replies;
- persistent sessions;
- Sachima behavior integration;
- public ingress;
- real IM delivery;
- Gateway restart/reload/replace;
- production config writes;
- live/default-on behavior;
- `@all` fanout;
- agent-to-agent automatic routing;
- treating `allowed_roots` as an OS/filesystem sandbox.

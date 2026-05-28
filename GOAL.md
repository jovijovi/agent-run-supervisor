# agent-run-supervisor Project Goal

## One-sentence goal

`agent-run-supervisor` should be a small, local-first Python library and dev CLI that supervises external AGENT runs through pinned `acpx` / ACP, turns raw runner behavior into normalized, redacted, auditable evidence, and lets caller projects decide business meaning without inheriting runner chaos.

## Role in the larger system

```text
Caller project / human operator
  -> chooses an AgentRoleSpec, task prompt, cwd, and business contract
agent-run-supervisor
  -> validates role/cwd, compiles acpx argv/policy, runs or replays exec-only runs, classifies status, writes redacted artifacts
acpx@0.10.0 / ACP runner boundary
  -> launches and streams the external AGENT
External AGENT
  -> Codex, Claude Code, or another ACP-capable worker/reviewer
```

The project is an independent repo. It is not Sachima, not a Gateway plugin, not an IM adapter, and not a daemon.

## Non-negotiable principles

- `AgentRoleSpec` is the durable role/policy boundary.
- V0.1a is exec-only: no persistent sessions, no session registry, no stale-lock recovery.
- `allowed_roots` validates cwd/config intent only; it is not an OS sandbox.
- A completed runner status is not a business PASS. In V0.1a, `business_verdict` remains `null`.
- Artifacts must be redacted by default and written with restrictive permissions.
- The project never implies Sachima integration, real AGENT auto-replies, public ingress, real delivery, Gateway lifecycle operations, production config writes, live/default-on behavior, `@all`, or agent-to-agent auto-routing without a separately approved phase.
- Future work follows `docs/AI_FLOW.md`: clean task branch, plan artifact, dev log, fresh gates, review, PR, and post-merge verification.

## Current phase line

```text
Phase -1 acpx@0.10.0 contract spike: complete with checked-in fixtures and validator.
V0.1a exec-only vertical slice: implemented on main with role validation, policy/argv compile, exit classification, observed stdout replay, EventStore, redaction, CLI smoke, pytest coverage, and Codex review evidence.
AI_FLOW bootstrap: this repository now uses GOAL.md, docs/roadmap/current-status.md, docs/AI_FLOW.md, docs/plans/, docs/dev_log/, generated docs index, and docs drift gates for future work.
```

## Planning basis

Read these documents before roadmap, implementation, PR, CI, review, merge, or next-phase-readiness work:

1. `GOAL.md`;
2. `docs/roadmap/current-status.md`;
3. `docs/AI_FLOW.md`;
4. the latest relevant plan and dev log linked from current status.

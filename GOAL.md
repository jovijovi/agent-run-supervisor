# agent-run-supervisor Project Goal

## One-sentence product positioning

`agent-run-supervisor` is a small, local-first Python library and dev CLI that supervises ACP/acpx-powered external AGENT runs, normalizes runner/protocol behavior into redacted auditable evidence, and keeps caller projects free from runner lifecycle chaos.

## Product identity

The project is an independent supervisor layer. It is not Sachima, not a Gateway plugin, not an IM adapter, and not a daemon.

```text
Caller project / human operator
  -> chooses AgentRoleSpec, task prompt/context, cwd, and business contract
agent-run-supervisor
  -> validates role/workspace, compiles acpx policy/argv, supervises exec or session runs,
     parses observed events, classifies status, writes redacted artifacts
acpx / ACP runner boundary
  -> launches or resumes the external AGENT
External AGENT
  -> Codex, Claude Code, or another ACP-capable worker/reviewer
```

## What this project owns

- `AgentRoleSpec` as the durable role, policy, and authorization boundary.
- acpx/ACP invocation compilation for supported execution modes.
- Local runner/session lifecycle supervision.
- Observed stdout/event parsing and status classification.
- Redacted local artifacts and audit evidence.
- Dev CLI and Python library surfaces for caller projects.

## What caller projects own

- Product/business intent and final verdict interpretation.
- User-facing rendering, progress display, delivery, and integration policy.
- Any platform-specific behavior such as Sachima, IM, Gateway, or production deployment.

## Source-of-truth index

Read these in order for product, design, roadmap, and implementation work:

1. Product requirements: `docs/product/prd.md`
2. System architecture (diagrams and boundaries): `docs/design/architecture.md`
3. Technical design (module detail): `docs/design/technical-solution.md`
4. Feature completion tracking: `docs/roadmap/features.md`
5. Roadmap, phase status, and implementation-stage acceptance: `docs/roadmap/current-status.md`
6. Development workflow: `docs/AI_FLOW.md`
7. Generated documentation index: `docs/INDEX.md`

`GOAL.md` is intentionally stable. It defines product positioning and points to the living documents above; it is not a phase tracker.

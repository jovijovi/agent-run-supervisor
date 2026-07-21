---
title: "Pre-vNext-reset GOAL snapshot"
status: archived
created_at: 2026-07-21
archived_at: 2026-07-21
deprecated_reason: "Superseded by the vNext-only authority reset"
---
> **Cold archive — not development authority.** Preserved from the pre-vNext-reset tree for audit,
> historical compatibility, and dispute resolution only. Links and status statements describe the
> former location/time. New development must use `GOAL.md`, `docs/product/prd.md`, the active
> vNext design documents, the living roadmap, and `docs/plans/active/`.

# agent-run-supervisor Project Goal

## One-sentence product positioning

`agent-run-supervisor` is an independent, local-first supervision system for external ACP AGENT runs: it binds already-approved execution inputs, supervises run/session/process/protocol lifecycle, and normalizes observed behavior into redacted auditable evidence, keeping caller projects free from runner lifecycle chaos.

## Product identity

The project is an independent local supervision system. It is not Sachima, not a Gateway plugin, not an IM adapter, and not a business-authorization engine.

Callers (Hermes / FlowWeaver / a human operator) own business authorization, risk decisions, operator approval, task admission, and final business meaning. `agent-run-supervisor` only authenticates local callers, binds already-approved resources, manages Run/Session/process/protocol/evidence lifecycle, and enforces immutable per-Run execution grants. It never widens a grant and is not a general RBAC system.

### Current implemented surface (released, v0.1.7)

The released product is a small local Python library and dev CLI that supervises acpx-powered exec and persistent-session runs. It contains no daemon.

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

### Production target (settled architecture; documentation target, not yet implemented)

The settled ARS vNext production form is a reusable `ars-core` plus a thin, unprivileged, local `arsd` daemon reached over a Unix domain socket. `arsd` is the sole production ingress and the single supervision authority for Native ACP runs: it directly owns Native ACP connections and Agent process trees, with no durable per-Run worker.

```text
Hermes / FlowWeaver / CLI
  -> local Unix domain socket
  -> arsd
  -> ars-core / Native ACP Driver
  -> external ACP Agent
```

Native ACP is additive beside the unchanged acpx legacy paths, and Native failure never falls back to acpx. Recording this target here is documentation authority only: `arsd`, Native ACP source (Stage 0/1), service/cgroup deployment, release, and any Sachima/Gateway integration remain unimplemented and each requires separate explicit authorization. Target requirements: `docs/product/prd.md` §8. Target architecture: `docs/design/architecture.md` §9.

## What this project owns

Current (implemented):

- `AgentRoleSpec` as the durable role, policy, and authorization boundary for acpx runs.
- acpx/ACP invocation compilation for supported execution modes.
- Local runner/session lifecycle supervision.
- Observed stdout/event parsing and status classification.
- Redacted local artifacts and audit evidence.
- Dev CLI and Python library surfaces for caller projects.

Target (planned, per the settled vNext architecture):

- Versioned `AgentProfile` launch/config descriptions and immutable per-Run `AgentRunSpec` execution grants.
- Native ACP driver, supervised live process surface, and the `arsd` local Unix-socket ingress.
- Session continuity across process-per-Run via external Agent session identity.

## What caller projects own

- Product/business intent, business authorization, risk decisions, operator approval, task admission, and final verdict interpretation.
- User-facing rendering, progress display, delivery, and integration policy.
- Any platform-specific behavior such as Sachima, IM, Gateway, or production deployment.

## Source-of-truth index

Read these in order for product, design, roadmap, and implementation work:

1. Product requirements: `docs/product/prd.md`
2. System architecture (diagrams and boundaries): `docs/design/architecture.md`
3. Technical design (module detail): `docs/design/technical-solution.md`
4. Feature completion tracking: `docs/roadmap/features.md`
5. Living roadmap board: `docs/roadmap/current-status.md`
6. Non-approvals: `docs/roadmap/non-approvals.md`
7. Verification gates: `docs/roadmap/verification.md`
8. Development workflow: `docs/AI_FLOW.md`
9. Active implementation plans: `docs/plans/active/` (board `active_plan:` pointer)
10. Generated documentation index: `docs/INDEX.md`

`GOAL.md` is intentionally stable. It defines product positioning and points to the living documents above; it is not a phase tracker.

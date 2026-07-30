---
title: "Binding-era authority archive (2026-07)"
status: archived
created_at: 2026-07-30
archived_at: 2026-07-30
deprecated_reason: "Retired target architecture; preserved as migration source and audit history only"
---
# Binding-era authority archive (2026-07)

This directory is the cold snapshot of the **Runtime Binding / artifact-identity authority** that ARS
carried from `v0.5.0` through `v0.5.3`. It is preserved because production ran it, because old Run and
Session records were written under it, and because an auditor must be able to read what the project
claimed at the time.

It is **history, not authority.** Nothing here defines current scope, modules, gates, acceptance, or
approval, and nothing here may be used to reopen the retired architecture.

## What was retired

| Retired authority | Snapshot in this directory |
|---|---|
| `GOAL.md` load-bearing contracts 9, 10, 11 and the artifact-closure/`/opt` prefix statements | [`goal-contracts-9-11.md`](goal-contracts-9-11.md) |
| `docs/product/prd.md` R13 (Runtime Binding) and R14 (Agent Registration) | [`prd-r13-r14.md`](prd-r13-r14.md) |
| `docs/design/architecture.md` §3.1 runtime authority layers, §3.2 Binding layout/validation/operator surface, §3.3 launch kinds and artifact code closure | [`architecture-3.1-3.3.md`](architecture-3.1-3.3.md) |
| The implementation plans that built the Binding era | [`binding-era-plans.md`](binding-era-plans.md) |
| Why all of the above was retired | [`retirement-rationale.md`](retirement-rationale.md) |

Each snapshot preserves the tracked wording of the retired sections. Read every claim inside them as a
**past claim of the Binding era**, never as a current ARS guarantee — in particular the artifact-digest,
package-closure, ownership/mode, and promotion claims, which the current authority chain does not make.

## Current authority

Current authority starts at repository-root [`GOAL.md`](../../../GOAL.md), then
[`docs/product/prd.md`](../../product/prd.md) → [`docs/design/architecture.md`](../../design/architecture.md)
→ [`docs/design/technical-solution.md`](../../design/technical-solution.md) →
[`docs/design/agent-registry.md`](../../design/agent-registry.md) →
[`docs/roadmap/features.md`](../../roadmap/features.md) →
[`docs/roadmap/current-status.md`](../../roadmap/current-status.md) → `docs/plans/active/`.

## Use only for

- audit and provenance of what was claimed and merged before the boundary reset;
- reading Run, launch, and Session records written under the Binding-era schemas;
- dispute resolution when comparing the reset against the previous authority chain.

## Never use for

- new implementation scope, module design, sequencing, acceptance, or authorization;
- restoring artifact identity, promotion, attestation, `--binding-root`, or an ARS-owned artifact tree;
- claiming that any Binding-era guarantee still holds.

## What retirement did not do

Retirement is a **documentation and target-architecture** act. It deleted no operator storage, no
promoted generation, no `/opt` tree, and no historical Run or Session bytes. Those remain untouched
migration source and are removed, if ever, by a separate operator decision.

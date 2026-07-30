---
title: "Binding-era implementation plans (retired architecture)"
status: archived
created_at: 2026-07-30
archived_at: 2026-07-30
deprecated_reason: "Index of plans that built a retired architecture; kept for provenance only"
---
# Binding-era implementation plans (retired architecture)

The plans below built the artifact-identity and Runtime Binding architecture that the V4 boundary reset
retires. They stay where they are, under `docs/plans/archive/`, and they stay `status: archived`. This
page records **which** archived plans belong to the retired era and **what each one actually landed**, so
a later reader does not mistake a completed plan for current target authority.

> An archived plan can never define new scope, select a branch, approve work, or override the current
> authority chain. Each of these was complete when archived; what changed is the *architecture they
> implemented*, not their completion.

| Plan | What it landed | Status under the reset |
|---|---|---|
| [`2026-07-24-official-adapter-run-boundaries.md`](../../plans/archive/2026-07-24-official-adapter-run-boundaries.md) | shared official-adapter run-boundary repair | implemented scope merged; the per-agent adapter profiles it served are superseded |
| [`2026-07-25-codex-official-adapter-admission.md`](../../plans/archive/2026-07-25-codex-official-adapter-admission.md) | the Codex official-adapter closed profile, its frozen launch environment, and spawn-boundary attestation | implemented scope merged; the dedicated profile is superseded by an operator-registered adapter command |
| [`2026-07-25-claude-official-adapter-b3-b5-closure.md`](../../plans/archive/2026-07-25-claude-official-adapter-b3-b5-closure.md) | the Claude official-adapter closed profile, its enforced permission mode, and frozen session metadata on new + load | implemented scope merged; the ACP-semantic part survives as an evidenced compatibility profile, the artifact part is retired |
| [`2026-07-26-runtime-binding-refactor.md`](../../plans/archive/2026-07-26-runtime-binding-refactor.md) | the contract/Binding split, read-once sealed admission, the epoch reuse gate, and the `runtime-binding` operator commands | implemented scope merged; the whole Binding layer is retired as target architecture |
| [`2026-07-29-multi-profile-runtime-binding.md`](../../plans/archive/2026-07-29-multi-profile-runtime-binding.md) | the profile-scoped active-selection namespace, one independent generation per registered profile | implemented scope merged and released; retired with the Binding layer |
| [`2026-07-29-standard-native-acp-v1.md`](../../plans/archive/2026-07-29-standard-native-acp-v1.md) | the versioned conformance profile, the typed Agent Registration leaf, and the agent-anchored Binding subtree | implemented scope merged; the Binding anchor is retired, and the "one conformance contract, many registered agents" idea is what the operator registry now delivers |

The closed phase summary for the source work is
[`docs/roadmap/archive/phases/vnext-runtime-binding-source-closure.md`](../../roadmap/archive/phases/vnext-runtime-binding-source-closure.md).
It is also cold history and is not reopened by the reset.

## What carried forward

One idea from this era survives intact and is worth naming, because the reset is often mis-read as
discarding all of it: **one source contract can serve many agents, and the facts that vary between
conforming agents are a small typed bounded set.** The Binding era expressed that as an Agent
Registration anchored inside an artifact-identity tree. The reset expresses the same idea as one
operator-owned registry file read once at daemon startup, with no artifact identity underneath it.

## What did not

Artifact materialization, package closures and tree digests, generations, promotion and rollback,
acceptance receipts, contract-hash and epoch staleness as a continuity gate, credential-root structural
inspection, and the ownership/mode/ancestor rules that made them meaningful. Those are retired as target
architecture and are described only in this archive.

---
title: "L1 — Concrete caller integration design"
status: archived
created_at: 2026-07-07
archived_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
phase_id: l1-caller-design
---

# L1 — Concrete caller integration design

> **Closed phase archive.** Living board:
> [`docs/roadmap/current-status.md`](../../current-status.md).
> Execution plan(s): [`docs/plans/archive/`](../../../plans/archive/).

## L1 — Concrete caller integration design (design-only)

Goal: design — **without implementing** — how a concrete local caller (Hermes) drives the
supervisor through the generic I1 boundary for a document-check scenario, in both `exec` and
`persistent session` modes.

- **Type:** design-only. Implements no runtime code; changes no runtime module, CLI, config,
  or `caller.py`; grants no live/runtime approval.
- **Scope:** named concrete caller (Hermes); Feishu document-check scenario as a
  **presentation target only**; exec + persistent-session flows; input/output contracts
  reused unchanged from I1; normalized-event → caller-owned view-model mapping; ownership
  matrix; defined-but-unapproved Sachima seam.
- **Invariants preserved:** supervisor stays generic (no platform field in
  `CallerInvocationSpec`/`CallerResult`); `business_verdict` stays `null` and caller-owned;
  Feishu rich cards stay a caller-owned view-model/presentation adapter with **no delivery**;
  agent output stays untrusted (no trusted Markdown/HTML rendering).
- **Plan:** `docs/plans/archive/2026-06-01-l1-concrete-caller-integration-design.md`.
- **Parked / unapproved (unchanged):** real Feishu/IM delivery, platform ingress, Sachima
  behavior, Gateway lifecycle, automatic replies, live/default-on. Connecting Hermes to
  Sachima for real debugging/testing is a separate later approved phase; L1 only defines the
  seam.

Status: **Closed on `main` via PR #24 (`5e34f5c`).** PR #24 passed CI, Codex primary post-PR review, Mermaid render checks, docs index/drift gates, full local gates, and post-merge verification.
No implementation, live behavior, Sachima/Feishu/Gateway integration, or scope change was
introduced; all standing non-approvals (§5) remain in force.

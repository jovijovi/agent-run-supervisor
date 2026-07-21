---
title: "Pre-vNext-reset non-approvals snapshot"
status: archived
created_at: 2026-05-28
last_validated_at: 2026-07-21T20:30:00+0800
archived_at: 2026-07-21
deprecated_reason: "Superseded by the vNext-only authority reset"
---
> **Cold archive — not development authority.** Preserved from the pre-vNext-reset tree for audit,
> historical compatibility, and dispute resolution only. Links and status statements describe the
> former location/time. New development must use `GOAL.md`, `docs/product/prd.md`, the active
> vNext design documents, the living roadmap, and `docs/plans/active/`.

# Current explicit non-approvals

Current docs/code work does not approve:

- Sachima behavior integration;
- real AGENT automatic replies;
- public ingress;
- real IM delivery;
- Gateway restart/reload/replace;
- production config writes;
- live/default-on behavior;
- worker auto-routing;
- participant persistence or management UI;
- `@all` fanout;
- agent-to-agent automatic routing;
- trusted Markdown/HTML rendering;
- treating `allowed_roots` as an OS/filesystem sandbox;
- per-run human approval as the default authorization model;
- vNext Stage 0/1 source implementation (slices C1–C10, including the `agent-client-protocol` dependency change);
- `arsd` implementation, service/cgroup enablement, or any deployment (Stage 2);
- Sachima `ArsdBackend` integration or supervisor pin changes.

Persistent sessions are **not** a non-goal; they are a product requirement now closed for the local lifecycle, originally sequenced after the exec runner implementation phase.

The 2026-07-21 G2 authority refresh records the settled vNext arsd/Native ACP architecture in GOAL/PRD/design as an **approved documentation target only**; it grants none of the approvals above. Each implementation stage requires its own separate explicit operator approval.

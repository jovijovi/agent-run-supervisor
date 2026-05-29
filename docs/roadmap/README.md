---
title: "Roadmap governance"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-29T12:20:00+0800
---
# Roadmap governance

`docs/roadmap/current-status.md` is the living roadmap, phase-status tracker, and implementation-stage acceptance register for `agent-run-supervisor`.

`docs/roadmap/features.md` is the feature/capability completion tracker.

Use roadmap documents to answer:

- which product features exist and how complete they are;
- which engineering phase is current;
- what the next allowed implementation request is;
- what tails remain open;
- which explicit non-approvals remain in force;
- which evidence proves phase completion.

Roadmap documents may sequence implementation, for example exec before persistent sessions. They must not shrink PRD/DESIGN product requirements.

Roadmap documents own roadmap, status, and feature tracking. They do **not** own task-level execution plans: concrete task/phase implementation plans live under `docs/plans/`, named `YYYY-MM-DD-<task-slug>.md` (see `docs/plans/README.md`).

Do not use roadmap documents to imply live/runtime approval. A phase may start only after its scope is named and approved.

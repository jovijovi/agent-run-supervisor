---
title: "ARS vNext Current Explicit Non-Approvals"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-21
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/non-approvals.md"
---
# ARS vNext Current Explicit Non-Approvals

The vNext authority reset is documentation-only. It does not approve:

- Stage 0/1 C1–C10 source, dependency, lock, test, or CI implementation;
- creation of an implementation branch/worktree before the active plan's explicit approval;
- push, PR creation, merge, release tag, GitHub Release, PyPI publication, or CHANGELOG release work;
- Stage 2 `arsd` source, UDS service enablement, caller UID policy activation, cgroup/service harness or deployment;
- Sachima `ArsdBackend`, supervisor pin changes, Gateway/IM/Feishu behavior, delivery, automatic replies, or live/default-on wiring;
- public ingress, TCP/root service, distributed scheduling, multi-tenant control plane, participant UI, `@all`, or agent-to-agent auto-routing;
- arbitrary executable/command/argv/env/JSON/config/credential passthrough;
- acpx as Native production driver, fallback, compatibility layer, or shared/imported Session store;
- durable per-Run Worker, cross-`arsd` Run survival, generalized Session rebind, cross-AGENT Session reuse, or automatic prompt retry/replay/resume;
- treating `allowed_roots`, UDS authentication, ACP mediation, workspace binding, or cgroup cleanup as an OS sandbox or hostile-process isolation;
- a workspace content-digest service, filesystem watcher, broad RBAC/policy engine, or second conversation database inside ARS;
- using any document under `docs/archive/`, `docs/plans/archive/`, or `docs/roadmap/archive/` as new-development authority or implicit approval.

The released v0.1.7 acpx path may receive separately approved compatibility/security maintenance. Such
maintenance does not reopen its archived requirements as the vNext product direction.

Every implementation, publication, production-enablement, and integration stage requires its own explicit
operator approval. Approvals are narrow and non-transitive.

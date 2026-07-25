---
title: "ARS vNext Current Explicit Non-Approvals"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-25
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/non-approvals.md"
---
# ARS vNext Current Explicit Non-Approvals

Stage 0/1, Stage 2 A1–A5, and the three registered closed profiles are implemented on `main`, and
operator-held local socket-path acceptance passed for each profile. None of that approves further
work: source registration plus local acceptance is not publication, not deployment approval, not
Sachima integration, not public ingress, and not a reusable or transitive approval for the next
change. This document does not approve:

- any source expansion or repair beyond the currently authorized task scope, including source,
  test, script, dependency, lockfile, `pyproject.toml`, or CI/workflow changes;
- exact real UID→principal/owner/namespace mappings in the repository, or any production
  caller-policy/configuration value, unit change, or activation (G12 closure recorded the operator
  decision, not the values);
- new profile registrations or revisions of a registered profile without a fresh
  install → discovery → permission-canary cycle, revision bump, and independent review;
- deployment, rollout, service install/enable/restart, production config writes, or live traffic
  changes beyond the already-enabled local user service;
- follow-on source work and Git/GitHub side effects, including commits, pushes, PR creation,
  merge, or other GitHub mutation, without separate operator authorization;
- version bump, release metadata, release tag, GitHub Release, PyPI publication, or CHANGELOG
  release-section work;
- Sachima `ArsdBackend`/UDS integration, supervisor pin changes, Gateway/IM/Feishu behavior, delivery, automatic replies, or live/default-on wiring;
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

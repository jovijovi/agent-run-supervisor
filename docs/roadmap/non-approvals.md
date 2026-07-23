---
title: "ARS vNext Current Explicit Non-Approvals"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-23
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/non-approvals.md"
---
# ARS vNext Current Explicit Non-Approvals

The merged A1 `arsd` source/default-closed foundation does not approve further work. This
document does not approve:

- any source expansion or repair beyond merged A1 `arsd` scope, including source, test, script,
  dependency, lockfile, `pyproject.toml`, or CI/workflow changes;
- A2/G12 policy ownership or exact real UID→principal/owner/namespace mappings, including any
  production caller-policy/configuration value or activation;
- A3 user-service/cgroup harness installation, activation, or execution;
- A4 real external-AGENT S1–S5 acceptance, including use of real credentials;
- A5 production/default-on enablement, production config writes, deployment, or live traffic;
- follow-on source work and Git/GitHub side effects, including commits, pushes, PR creation,
  merge, or other GitHub mutation, without separate operator authorization;
- release metadata, release tag, GitHub Release, PyPI publication, or CHANGELOG release work;
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

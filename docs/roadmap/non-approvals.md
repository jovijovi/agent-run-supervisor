---
title: "ARS vNext Current Explicit Non-Approvals"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-26
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

The accepted Runtime Binding design (PRD R13) and the activation of its plan add no approval either.
This document specifically does not approve:

- creating, authoring, validating, promoting, or rolling back a Runtime Binding generation against any
  real deployment, or writing any file under a real Binding root;
- installing, relocating, or re-owning an external CLI artifact root, or changing artifact/ancestor
  ownership or permissions on a host;
- freezing any OpenCode identity, capability, or selector constant before the required non-prompt ACP
  `initialize` discovery and code-owned CLI version probe evidence exist, or asserting that
  `agentInfo.version` equals a CLI `--version`;
- treating a Binding acceptance receipt as self-authorization, a `--force`/unvalidated promotion path,
  any ARS-internal privilege escalation, or an `arsd` restart;
- retaining a `session_compatibility_epoch` across a Binding change without an approved continuity
  canary;
- treating the PR-A authority update or the active plan as approval to land PR-B source, to publish, to
  deploy, or to run a real provider — each remains a separate, narrow operator decision.

The standard Native ACP (v1) contract and the Agent Registration layer (PRD R14, GOAL contract 11) add
no approval either. This document specifically does not approve:

- registering any **real** agent against `standard-native-acp-v1`, or any step of that sequence:
  installing an agent artifact under a root-owned prefix, running ACP `initialize` discovery, running
  the code-owned CLI probe, running the mandatory denied-action mediation canary, authoring a
  `registration.json` or a generation manifest, or `validate --agent`/`promote --agent` against any
  real Binding root;
- freezing any real agent identity, capability, selector, or value domain in source or documentation —
  the only registrations that exist are two fabricated test fixtures, and no OpenCode or Cursor
  registration fact is frozen anywhere;
- claiming real OpenCode or Cursor end-to-end evidence for the standard-native path; no
  standard-native agent is runnable at merge, and real acceptance is a future operator stage;
- cutting any caller over to `standard-native-acp-v1` or to any agent id;
- **retiring, deprecating, disabling, deleting, aliasing, or redirecting `opencode-native-acp` or any
  other registered profile, or introducing any mechanism capable of doing so.** No such mechanism
  exists in source, and adding one is a separate design and a separate approval from using it — two
  decisions, neither taken;
- treating a registration's provenance block — its acceptance, discovery, or permission-canary receipt
  — as self-authorization for anything;
- adding a `standard-native-acp-v2` profile, registration, Binding, or Session domain.

The released v0.1.7 acpx path may receive separately approved compatibility/security maintenance. Such
maintenance does not reopen its archived requirements as the vNext product direction.

Every implementation, publication, production-enablement, and integration stage requires its own explicit
operator approval. Approvals are narrow and non-transitive.

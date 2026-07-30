---
title: "ARS vNext Current Explicit Non-Approvals"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-30
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/non-approvals.md"
---
# ARS vNext Current Explicit Non-Approvals

Stage 0/1 and Stage 2 A1–A5 are implemented on `main`, operator-held local socket-path acceptance passed,
and the tracked authority chain has moved to the V4 external-AGENT boundary reset. None of that approves
further work: authority alignment plus local acceptance is not source implementation, not publication, not
deployment approval, not Sachima integration, not public ingress, and not a reusable or transitive approval
for the next change. This document does not approve:

- any source expansion or repair beyond the currently authorized task scope, including source,
  test, script, dependency, lockfile, `pyproject.toml`, or CI/workflow changes;
- exact real UID→principal/owner/namespace mappings in the repository, or any production
  caller-policy/configuration value, unit change, or activation (G12 closure recorded the operator
  decision, not the values);
- new profile registrations or revisions of a registered profile without cited ACP-level evidence, a
  fresh discovery → permission-canary cycle, a revision bump, and independent review;
- deployment, rollout, service install/enable/restart, production config writes, or live traffic
  changes beyond the already-enabled local user service;
- follow-on source work and Git/GitHub side effects, including commits, pushes, PR creation,
  merge, or other GitHub mutation, without separate operator authorization;
- version bump, release metadata, release tag, GitHub Release, PyPI publication, or CHANGELOG
  release-section work;
- Sachima `ArsdBackend`/UDS integration, supervisor pin changes, Gateway/IM/Feishu behavior, delivery, automatic replies, or live/default-on wiring;
- public ingress, TCP/root service, distributed scheduling, multi-tenant control plane, participant UI, `@all`, or agent-to-agent auto-routing;
- arbitrary executable/command/argv/env/JSON/config/credential passthrough **from the wire**;
- acpx as Native production driver, fallback, compatibility layer, or shared/imported Session store;
- durable per-Run Worker, cross-`arsd` Run survival, generalized Session rebind, cross-AGENT Session reuse, or automatic prompt retry/replay/resume;
- treating `allowed_roots`, UDS authentication, ACP mediation, workspace binding, or cgroup cleanup as an OS sandbox or hostile-process isolation;
- a workspace content-digest service, filesystem watcher, broad RBAC/policy engine, or second conversation database inside ARS;
- using any document under `docs/archive/`, `docs/plans/archive/`, or `docs/roadmap/archive/` as new-development authority or implicit approval.

## The V4 boundary reset adds no approval

The reset is a **documentation and target-architecture** change. Retiring an authority is not deleting an
implementation, and the tracked authority describing a capability is never evidence that it exists. This
document specifically does not approve:

- **artifact installation or hosting** of any kind — no ARS-owned artifact prefix, package closure, tree
  digest, frozen interpreter identity, materialization, or relocation, and no re-owning of a path;
- **an ARS-managed AGENT home**, or creating, populating, staging, mirroring, repairing, or deleting any
  AGENT auth, configuration, cache, plugin, or Session state;
- **credential-store inspection** — no stat audit, mode or ownership enforcement, digest, or
  required-absence check over an AGENT's auth or config surfaces, and no modelling of a path inside them;
- **ARS credential resolution** — ARS discovers, resolves, mints, refreshes, stores, and manages no
  credential, and no schema may carry a placeholder for one;
- **persisting an environment value** in any ARS artifact, hash input, log, exception, event, inspect
  response, or API response, including any digest, keyed digest, length, prefix, suffix, equality token, or
  matcher table computed to represent one;
- **any attestation, artifact-integrity, supply-chain, or isolation claim**, or any statement that ARS
  verifies what it launched, contains hostile code, unconditionally terminates every descendant, retroactively
  erases legacy bytes, prevents transformed disclosure, or ensures that no sensitive value reaches the child;
- **remote transport, attach-to-running-agent, plugin loading, containers, sandboxing, or multi-tenancy in
  v1** — including any seam, key, field, branch, or dependency that anticipates one; `transport` is refused
  as an unknown registry key;
- **silent replay or fallback in either direction** — no automatic prompt replay, resume, or retry, no
  `session/new` on a reuse path, no dual-write, no dual-read, no shim, and no alias;
- **wire-supplied command, argv, or environment** — those are not fields on the request and may not become
  fields;
- **operator-authored or operator-disabled mediation environment** — the binding is source-owned in key and
  value, a registry entry may select one or none, and there is no way to author a pair or turn mediation off;
- **any automatic `session_epoch` bump** — no code path may derive, increment, or infer an epoch from an
  observation, a digest, a version, or a file's bytes; only an operator's edit changes it.

## Retirement: what was decided, and what was not

- **Decided (2026-07-30): Decision 1, option (a)** — the policy-level retirement of the three registered
  per-agent profiles. That decision authorized the authority retirement recorded in
  [`docs/archive/binding-era-2026-07/`](../archive/binding-era-2026-07/README.md) and the tracked authority
  chain that replaced it.
- **Not approved: retiring, deprecating, disabling, deleting, aliasing, or redirecting a registered profile
  in source.** Introducing a retirement capability and using one remain two separate decisions, and the
  second is **not taken**. It is required, in writing, before that source work runs — and no such mechanism
  may be added in the meantime as a field defaulting to `False`, an unused rule constant, or a marker.
- **Not approved: source implementation of any V4 stage.** That is a distinct approval, recordable only
  after the authority alignment merges. Recording Decision 1, activating a plan, and merging the authority
  alignment each individually and jointly do **not** imply it.
- **Not approved: production cutover** (Decision 2, including the one-time legacy-Session load refusal) or
  any decision about the **legacy `v0.5.x` line lifetime** (Decision 3). Both remain open.
- **Not approved: touching the retired deployment.** The `/opt` artifact trees, the Binding roots, their
  promoted generations, and every historical Run and Session byte are untouched migration source. The reset
  stops referencing them; it deletes, migrates, re-hashes, and rewrites nothing, and their removal is a
  separate, later operator decision.
- **Not approved: a per-agent canary shortcut.** The mandatory denied-action mediation canary stays required
  per registered agent before that agent's use, and a registry entry's existence is not evidence that it ran.

The standing rule is unchanged and this reset depends on it: **an active plan is not approval to land
source, to publish, to deploy, or to run a real provider.** Activating a plan records only that it is the
board-linked planning artifact.

The legacy v0.1.7 acpx path holds no product, runtime, or compatibility authority, and while its code exists
it may receive separately approved compatibility/security maintenance. Such maintenance does not reopen its
archived requirements as the vNext product direction and creates no V4 compatibility obligation. **Removing
the acpx product, runtime, and compatibility content** — the code and the documentation that describes it —
is likewise its own separately authorized decision, and nothing in this reset approves it.

Every implementation, publication, production-enablement, and integration stage requires its own explicit
operator approval. Approvals are narrow and non-transitive.

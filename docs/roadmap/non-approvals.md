---
title: "ARS vNext Current Explicit Non-Approvals"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-01
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

The reset's Stage 3 source now exists on branch `feat/v4-boundary-reset`, and that changes nothing here:
local source is not a merge, a release, a deployment, or an enablement, and the tracked authority
describing a capability is never evidence that the capability is reachable in production. This document
specifically does not approve:

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

## Threat-model scope: what the environment-value rule does and does not cover

**Decided 2026-08-02, and settled.** The approved invariant is that **ARS production writers must not
persist an environment value.** It is a rule about what ARS writes.

It is **not** a tamper-resistance claim about artifacts ARS already wrote. Defending `run inspect` against
an actor who can arbitrarily rewrite a field of an ARS-owned reset `launch.json` — planting a secret in
`profile_id`, say, and then asking the inspector to catch it — is **outside this Stage 3 threat model**: an
actor with arbitrary local write access to the Run root has already defeated every projection boundary
downstream of it, and closed-domain validation of each top-level launch field buys nothing against them.

So: no top-level launch-field closed-domain validation is to be added, `launch_payload_shape_is_exact`,
`_classify_launch_schema`, `_inspect_reset_record`, `_inspect_legacy_record`, and `_recompute_launch_hash`
stay as they are, and tamper resistance is not to be broadened. This is recorded so the finding is not
reopened as a blocker; reversing it would take a new explicit decision that says so.

The `env` block's closed-domain check is a **different** rule and stands: it decides whether a document is
a value-blind production projection at all, which is what selects the digest path over the withholding path.

## Retirement: what was decided, and what was not

- **Decided (2026-07-30): Decision 1, option (a)** — the policy-level retirement of the three registered
  per-agent profiles. That decision authorized the authority retirement recorded in
  [`docs/archive/binding-era-2026-07/`](../archive/binding-era-2026-07/README.md) and the tracked authority
  chain that replaced it.
- **Decided (2026-08-01): deleting the three registered per-agent profiles from source.** Introducing a
  retirement capability and using one were always two separate decisions; this is the second, taken in
  writing, and it is narrow. It authorized exactly one source act, executed locally on branch
  `feat/v4-boundary-reset`: the source registry now holds exactly `standard-native-acp-v1` and
  `claude-agent-acp-compat-v1`. V4 retires profiles by **deleting** them — no alias, redirect, disable
  flag, field defaulting to `False`, unused rule constant, or marker was added, and a test asserts that no
  such mechanism exists. It authorized nothing else, and it is neither merged nor released.
- **Decided (2026-07-30, after the authority alignment merged): local source implementation of V4 Stages
  1–3.** It authorizes local source, test, and status work on task branches, taken serially at the stage
  gates — and stops there: the Stage 3 candidate stays in its worktree, uncommitted. It authorizes nothing
  else: **commit, push, PR creation, merge, release,
  deployment, service restart, migration/cutover, the real-agent canary, and production changes all remain
  separately unapproved.** Recording Decision 1, activating a plan, merging the authority alignment, and
  the narrow source-retirement approval above still do not imply any of those, and a green local
  verification transfers approval to none of them.
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

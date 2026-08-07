---
title: "ARS vNext Current Explicit Non-Approvals"
status: active
created_at: 2026-07-21
last_validated_at: 2026-08-06
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/non-approvals.md"
---
# ARS vNext Current Explicit Non-Approvals

Stage 0/1, Stage 2 A1–A5, and the V4 external-AGENT boundary reset are merged on `main`, and operator-held
local socket-path acceptance passed. Published package/release facts come from live GitHub Releases and PyPI;
deployed/running facts come from operator-held runtime/live checks.
[`current-status.md`](current-status.md) carries only lean task state, the active plan, and open gates. None
of it approves further work — merged source is not publication, a prior publication is not approval for the
next one, and neither is deployment approval, Sachima integration, public ingress, or a reusable or
transitive approval for the next change. This document does not approve:

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
- any release **act**: a release tag, a GitHub Release, or a PyPI publication. Past releases were taken
  under their own separate authorizations and approve nothing further; live GitHub Releases and PyPI
  establish published package/release facts. Any *further* version bump, release-metadata change, tag,
  publication, deployment, restart, or cutover is again unapproved and needs its own decision;
- Sachima `ArsdBackend`/UDS integration, supervisor pin changes, Gateway/IM/Feishu behavior, delivery, automatic replies, or live/default-on wiring;
- public ingress, TCP/root service, distributed scheduling, multi-tenant control plane, participant UI, `@all`, or agent-to-agent auto-routing;
- arbitrary executable/command/argv/env/JSON/config/credential passthrough **from the wire**;
- acpx as Native production driver, fallback, compatibility layer, or shared/imported Session store;
- durable per-Run Worker, cross-`arsd` Run survival, generalized Session rebind, cross-AGENT Session reuse, or automatic prompt retry/replay/resume;
- treating `allowed_roots`, UDS authentication, ACP mediation, workspace binding, or cgroup cleanup as an OS sandbox or hostile-process isolation;
- a workspace content-digest service, filesystem watcher, broad RBAC/policy engine, or second conversation database inside ARS;
- using any document under `docs/archive/`, `docs/plans/archive/`, or `docs/roadmap/archive/` as new-development authority or implicit approval.

## The V4 boundary reset adds no approval

The reset is merged on `main`, and that changes nothing here: a merge is not a release, a deployment, or an
enablement, and source implementing a capability is never evidence that the capability is reachable in
production. This document specifically does not approve:

- **artifact installation or hosting** of any kind — no ARS-owned artifact prefix, package closure, tree
  digest, frozen interpreter identity, materialization, or relocation, and no re-owning of a path;
- **an ARS-managed AGENT home**, or creating, populating, staging, mirroring, repairing, or deleting any
  AGENT auth, configuration, cache, plugin, or Session state;
- **credential-store inspection** — no stat audit, mode or ownership enforcement, digest, or
  required-absence check over an AGENT's auth or config surfaces, and no modelling of a path inside them;
- **ARS credential resolution** — ARS discovers, resolves, mints, refreshes, stores, and manages no
  credential, and no schema may carry a placeholder for one;
- **ARS itself projecting or serializing an environment value** — taking a value out of the resolved
  carrier and writing it, or anything value-derived from it (a digest, keyed digest, length, prefix, suffix,
  equality token, or matcher table), into structured launch/Spec/environment material, a hash input, a
  configuration-inspection response, a log line, or an exception message. This is a rule about **what ARS
  emits from the carrier**. It is deliberately *not* a claim that an environment value can never appear in
  Run evidence: free-form text an AGENT authored is not scanned against this Run's projected values, so an
  AGENT echo may be retained, and re-adding a per-Run exact-literal guard to prevent that is itself a
  separate decision that this document does not take;
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

**Decided 2026-08-02; narrowed 2026-08-03.** The approved invariant is that **ARS must not project or
serialize an environment value out of the resolved carrier** — into structured material, a hash input, an
inspection response, a log line, or an exception. It is a rule about what ARS *emits*, and it stands.

The broader reading that once accompanied it — that no environment value may appear in any ARS artifact at
all, enforced by a per-Run exact-literal guard over free-form Run text — is **retired**, under the decision
that removed that guard. Retention of an AGENT-authored echo is therefore an accepted outcome, not a
violation of this document, and the surviving controls over such text are static credential-shape and
sensitive-key redaction, categorical exception containment, and bounded evidence.

It is also **not** a tamper-resistance claim about artifacts ARS already wrote. Defending `run inspect` against
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
  writing, and it is narrow. It authorized exactly one source act, now merged: the three per-agent profiles
  left the source registry. V4 retires profiles by **deleting**
  them — no alias, redirect, disable flag, field defaulting to `False`, unused rule constant, or marker was
  added, and a test asserts that no such mechanism exists. It authorized nothing else.
- **Decided (2026-07-30, after the authority alignment merged): source implementation of V4 Stages 1–3.**
  Those stages are merged under their own serial gates, and that exhausts the approval. It authorized
  nothing else: **release, publication, deployment, service restart, migration/cutover, the real-agent
  canary, and production changes all remain separately unapproved**, and a green verification transfers
  approval to none of them.
- **Not approved: any further cutover, migration, or rollout act.** Decision 2 — production cutover with its
  one-time legacy-Session load refusal — was taken by the operator; deployed/running facts remain
  operator-held runtime/live checks. Nothing about that transfers to the next deployment, restart, or
  migration, and the **legacy line's lifetime** (Decision 3) is closed by the later operator decision
  recorded below: no old runtime line is active.
- **Not approved: touching the retired deployment.** The `/opt` artifact trees, the Binding roots, their
  promoted generations, and every historical Run and Session byte are untouched migration source. The reset
  stops referencing them; it deletes, migrates, re-hashes, and rewrites nothing, and their removal is a
  separate, later operator decision.
- **Not approved: a per-agent canary shortcut.** The mandatory denied-action mediation canary stays required
  per registered agent before that agent's use, and a registry entry's existence is not evidence that it ran.

- **Decided (2026-08-06): source implementation of the Session no-close model**,
  `docs/plans/archive/2026-08-06-session-no-close-model.md` stages D0–D4, including the targeted removal of
  Session-close and Session-kind logic from the then-present retired runtime. It authorizes **source,
  tests, and documentation only**, and it exhausts itself there.
- **Not approved by that decision, and not implied by a green gate or a merge:** runtime-data reset or
  archival, `arsd` restart or any service action, runtime cutover, release, publication, deployment,
  real Claude/Codex canaries, push, pull request, merge, tag, and Sachima integration. Because the change
  deliberately adds no dual-schema compatibility, cutover must be one operator-controlled action pairing the
  v3 package and caller with the operator-selected development-data decision — never a side effect of
  landing source.
- **Not approved by that decision: expanding the acpx cleanup.** Under the no-close decision only
  Session-close concepts, Session-lifetime classification, and their direct dependencies left the retired
  runtime; complete removal needed, and later received, its own decision below. Ordinary resource-level
  `close()` for sockets, streams, ACP connections, files, and process teardown is preserved throughout.

- **Decided (2026-08-06).** Source removal of the acpx product, runtime, and compatibility content. The
  runtime and its package modules, the CLI leaves outside `agents validate` / `agents doctor` /
  `run inspect`, the fixture trees, and the unreleased API v3 process-exit result field leave the
  repository. The differential/comparison keep set was audited and found **empty**.
  No acpx fixture was retained.
  It authorizes **source, tests, and documentation only**, and it exhausts itself there.
- **Not approved by that decision, and not implied by a green gate:** push, pull request, merge, tag,
  release, publication, deployment, install, `arsd` restart or any service action, runtime cutover,
  runtime-data migration/reset/archival, a real-agent canary, or Sachima integration. Records already
  written by the retired line stay on disk untouched: nothing rewrites, migrates, re-hashes, or deletes
  them. **Superseded 2026-08-06 by the operator's single-contract decision:** API v3 is the only contract,
  there is no historical persisted data to keep readable, and a persisted terminal carrying an undefined key
  is untrusted evidence rather than a tolerated extension. No tolerant reader, passthrough, projection,
  alias, migration, or dual format is authorized. No shim, alias, flag, bridge, dual runtime, dual-format writer, or renamed replacement field is
  authorized, now or later.

- **Decided (2026-08-07): the Session no-close runtime cutover and the pre-reset execution line's lifetime
  (Decision 3).** The operator took the cutover as one controlled action and closed Decision 3: no old
  runtime or configuration line is active, and unreferenced older runtime and configuration were retired
  from active use. Historical Session/Run state and evidence remain preserved — nothing was deleted,
  migrated, re-hashed, rewritten, or purged — and unrelated retired artifact trees and Binding roots stay
  governed by their own decisions. This decision is recorded distinctly from the earlier source-only
  decisions above: those decisions truly authorized source, tests, and documentation only, and their
  non-approvals keep their historical meaning. The completed act exhausts itself — every future release,
  publication, deployment, install, restart, migration, cutover, runtime cleanup, canary, or integration
  still requires its own separate authorization.

The standing rule is unchanged: **an active plan is not approval to land source, to publish, to deploy, or
to run a real provider.** Activating a plan records only that it is the board-linked planning artifact, and
an approval of one stage is not an approval of the next. An archived plan authorizes strictly less than an
active one — nothing.

acpx is not a product, runtime, or compatibility surface.
Its code, and the documentation describing it as available, are removed under the 2026-08-06 decision
above. Its archived
requirements do not reopen as the vNext product direction and create no V4 compatibility obligation.
Reintroducing any part of it — as a driver, fallback, compatibility layer, shared Session store, renamed
field, or re-added capability — would need a new explicit decision that says so.

Every implementation, publication, production-enablement, and integration stage requires its own explicit
operator approval. Approvals are narrow and non-transitive.

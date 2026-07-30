# agent-run-supervisor — vNext Goal

## Product identity

**ARS is a local, unprivileged, caller-authenticated execution supervisor for external ACP AGENTs.** It
accepts a structured, caller-authorized Run request; starts one operator-registered external command
exactly as declared; supervises the resulting local process; drives the ACP lifecycle with exact
configuration fidelity; mediates permission requests against an immutable per-Run grant; and produces
bounded, redacted, per-Run evidence with irreversible terminal facts.

ARS does **not** install, package, copy, unpack, freeze, promote, pin, host, own, or attest external
AGENTs, their ACP adapters, their homes, credential stores, plugin trees, caches, or configuration. Those
are installed, configured, authenticated, upgraded, and removed by users and operators through their own
package managers, entirely outside ARS.

ARS v1 also does not discover, resolve, mint, refresh, or manage credentials, and never persists an
environment value.

Process lifecycle ownership is not software-entity ownership. ARS owns the *process it started*; the user
owns the *software it started*.

ARS is not a business orchestrator and never converts process/ACP completion into business success.

## Only production shape for new development

```text
Hermes / FlowWeaver / trusted local CLI
        │  AgentRunRequest + frozen execution_grant
        │  agent_id · model · effort · grant · limits
        │  NO command / argv / env / path / secret value
        ▼
arsd — local Unix domain socket; sole production ingress
        │  startup: parse the agents file once → immutable snapshot → reconcile → then bind
        ▼
ars-core / RunTask / Native ACP Driver
        │  one ARS-owned local ManagedProcess per Run, ACP JSON-RPC over stdio
        ▼
external ACP AGENT — the operator-registered command, launched exactly as declared
        ▼
model / provider
```

- `arsd` is a thin, unprivileged local service host, not a root daemon, network service, scheduler,
  multi-tenant platform, or second runtime.
- Direct `ars-core` use is test/dev-only. Production fails closed when `arsd` is unavailable.
- There is no durable per-Run Worker. One `arsd` directly owns each in-process `RunTask`, Native ACP
  connection, and external AGENT process tree.
- Native ACP never falls back to acpx. acpx is not a product, runtime, or compatibility surface; it
  is retained only as a bounded differential/comparison test reference.

## Authority split

Callers own user intent, business authorization, task decomposition, role/AGENT choice, retries,
approvals, delivery, and business verdicts.

Operators own which command is which AGENT here: installation, the agent registry file, configuration,
credentials, `HOME` and AGENT state, environment declarations, and upgrades.

ARS owns caller authentication, approved-resource binding, immutable per-Run execution grants, ACP
compatibility-profile resolution, process/ACP lifecycle, Session/Run technical state, permission
mediation, recovery semantics, and redacted evidence. ARS enforces the frozen grant but never widens it
and is not a broad RBAC or policy-decision engine.

External AGENTs own their actual conversation/context state. ARS stores only the minimal Session binding
and runtime ledger needed for supervision, recovery, duplicate prevention, progress, configuration, and
result verification.

## vNext load-bearing contracts

1. Four authority layers stay separate and are never merged, and no fifth layer exists: a source-owned
   **ACP compatibility profile** (how to speak ACP to a class of agent), an **operator-owned agent
   registry snapshot** (which command is that agent, here), a **per-Run sealed `AgentRunSpec` plus launch
   snapshot** (what was requested, sealed before spawn), and **observed evidence** (what was resolved and
   observed, non-authoritative). A profile never learns from operator data or from an observation; the
   per-Run seal is a projection of profile × registry entry × request taken exactly once; observations
   never flow backward; and the wire never reaches the registry's value space.
2. A supervised `ManagedProcess` owns PID/PGID/identity, bounded stderr, timeout, terminate/kill/reap;
   the ACP SDK exclusively owns the live stdin/stdout JSON-RPC wire. Every Run owns exactly one such
   local process, from spawn to reap, and it is non-optional after spawn.
3. v1 uses process-per-Run. Same-Session continuity uses one external session ID and real
   `session/load`; AGENT processes do not survive between Runs. A reuse request can never become
   `session/new` — not as a fallback, not after a failure, not under any error class.
4. model/effort are immutable per Run, switchable only between completed Runs on the same external
   AGENT Session: load → discovery → set model → rediscovery → set effort → exact readback → prompt.
   The live-advertised option set is the domain authority; no source-frozen value domain gates it.
5. A prompt that may have been dispatched without a trustworthy terminal result ends as
   `Run=unknown`, `Session=quarantined`, `retryable=false`. It is never replayed, resumed, or retried
   automatically; successor work is a separate caller-authorized Run.
6. Permission mediation is default-deny and must be proven by a real denied-action canary. It is
   cooperative-agent policy enforcement, not an OS sandbox. The mediation environment binding is
   source-owned in key and value, applied last, and a registry entry may select one or none but can
   never author, replace, or disable it.
7. Native state uses isolated `native-runs/` and `native-sessions/` roots. Native code never reads,
   writes, imports, mirrors, or migrates acpx/legacy session storage.
8. Production crash containment uses a user-level service manager/cgroup: an `arsd` crash terminates
   all AGENT descendants that remain in its cgroup; restart performs reconciliation only and never
   resends a prompt.
9. A source profile freezes **ACP protocol and compatibility semantics only** — protocol major, required
   capabilities, a forbidden-capability floor, session semantics including required real `session/load`,
   selector-id conventions, the base environment allowlist, permission-mediation semantics, and, only
   where cited ACP-level evidence requires it, frozen ACP session metadata and a required permission-mode
   selector. It contains no path, version, digest, model literal, agent name, value domain, launch kind,
   artifact identity, or deployment fact. Every AGENT is instead one **operator-owned registry entry**
   that carries the command and its argv, read once at daemon startup into an immutable snapshot. There
   is no ARS-owned artifact, ARS-managed AGENT home, or attestation of anything ARS does not own.
   Therefore an AGENT or adapter upgrade behind an unchanged registered command costs no ARS action at
   all, while a registry edit costs exactly one drain-and-restart and no Session invalidation.

## Environment and credential guarantee

Every environment value is sensitive, regardless of key name, source class, length, or apparent shape. A
value may exist at its operator- or source-owned origin, in `arsd` memory, and in the child environment.
No complete projected literal — and no digest, fingerprint, length-by-value, or other metadata computed to
represent that value — may flow into an ARS durable artifact, hash input, log, exception or error message,
event stream, inspect response, or daemon API response. Durable environment evidence records the name, its
source class, its precedence layer, and its redaction status, and nothing else.

ARS v1 does not discover, resolve, mint, refresh, store, or manage any credential. AGENTs authenticate
through their own stores under their own `HOME`, exactly as they do interactively. `credential_refs` stay
caller-supplied **references** recorded as admission evidence and grant material; they are never resolved
to values and never reach the child environment.

**ARS does not claim that no sensitive value reaches the child.** An operator who declares a provider
token, a proxy URL with embedded credentials, an SSH agent socket, or any overlay literal transmits that
value to the child process in memory, by their own declaration, recorded only by name and source class.

## Filesystem boundary

ARS-owned **writable** surfaces are exactly two: the supervisor root (`native-runs/`, `native-sessions/`)
through the single storage seam, and the configured UDS runtime path. Nothing else.

Read-only access is permitted wherever the paths live, including below `$HOME` and through symlinks and
PATH shims: the operator registry file once at startup, everything the kernel and loader need to resolve
and launch the declared command, `/proc/<pid>/exe` and liveness reads for its own child, and the
caller-bound workspace.

ARS never creates, writes, populates, stages, mirrors, repairs, deletes, or otherwise **manages** AGENT
auth, configuration, cache, plugin, or Session state, and never **inspects** those surfaces as a control
surface — no stat audit, no mode or ownership enforcement, no digest, no required-absence check. The child
AGENT may mutate its own `HOME` and state normally, and such a Run completes normally.

## Process boundary

ARS guarantees a new POSIX session and process group for the child, `ProcessIdentity` recorded immediately
after spawn, signals delivered to the process group on terminate and kill, and `wait()` plus reap of its
direct child before releasing a lease or returning a terminal that permits deregistration.

ARS therefore reliably terminates the direct child and every descendant that **remains in the process
group ARS created**. It does not control a wrapper or descendant that calls `setsid()`/`setpgid()` and
leaves the group, a payload handed to a service manager in a separate transient unit, a container runtime
that relocates the payload to another namespace and cgroup, or an agent that double-forks and daemonizes
itself. Crash containment through the user-level service manager cgroup is a real, load-bearing dependency
that is **external** to ARS. When work continues elsewhere anyway, the uncertainty rules apply and the Run
fails loudly as `unknown`/`quarantined`/`retryable=false`.

ARS claims no isolation or containment of hostile code. An operator who wants isolation registers the
isolation wrapper as the command; a wrapper that `exec`s into the payload composes cleanly, while one that
relocates the payload to another supervisor breaks ARS's termination and timeout guarantees. That is the
operator's knowing choice, and ARS makes no claim about it.

## acpx removal direction

acpx is not a supported product, runtime, or compatibility baseline. The product direction is to
**remove all acpx product, runtime, and compatibility content from ARS** and to retain acpx only as a
bounded differential/comparison test reference.

That removal has not landed. acpx code paths still exist in source, and removing them — together with
the user-facing and design documentation that describes them — is separately authorized source/docs
work that this document does not perform or approve. Until it lands, acpx receives no new capability,
is never a Native ACP driver, fallback, or degraded path, and never directs vNext development. Its
old requirements, architecture, plans, and phase vocabulary are archived.

The cold snapshots are `docs/archive/pre-vnext-reset-2026-07-21/` and, for the retired artifact/Binding
era, `docs/archive/binding-era-2026-07/`. Closed plans and phases remain under their archive
directories. Git history remains the implementation audit trail.

## Current status and authorization

This goal, the PRD, architecture, technical solution, and the operator-facing agent-registry document are
documentation authority. They describe the target and do not authorize work by their existence. Volatile
implementation status belongs in `docs/roadmap/current-status.md`, which is where each merged change is
expected to be recorded; that board is not restated here and does not, by itself, evidence source facts.

**Authority and source differ right now, deliberately and visibly.** This document describes the V4
boundary reset: the operator agent registry, the four-way boundary, the environment-value sink boundary,
total ordered reconciliation, and fail-closed load-only Session reuse. Source on `main` still implements
the retired artifact/Binding architecture that `docs/archive/binding-era-2026-07/` preserves. Closing that
gap is staged source work that this document does not authorize; the board carries the exact delta and the
sequence.

On current `main`, Stage 0/1 Native ACP and Stage 2 `arsd` are merged, and the closed source registry
still holds four profiles. Retiring the three per-agent profiles is approved as **policy** and is not
approved as a source act: deleting them from source needs its own separate confirmation. Release and
publication, production cutover, service restart, migration, `/opt` and Binding-root removal, Sachima
integration, public ingress, and any Gateway/IM/live behavior each still require separate, explicit
authorization, and no local acceptance transfers approval to the next change.

## Non-goals

Public ingress, TCP/root service, distributed scheduling, multi-tenant cloud control plane, broad RBAC,
per-Run Worker, runtime plugin platform, remote transport, attach-to-running-agent, plugin loading,
containers, sandboxing, ARS credential resolution, acpx fallback, shared or imported acpx session storage,
generalized Session rebind, cross-AGENT Session reuse, automatic replay, workspace content-digest service,
filesystem watcher, second conversation database, hostile-process sandbox claims, and embedding
Feishu/Gateway/business semantics in ARS.

The agent registry adds no exception. The caller still supplies no command, argv, environment key or
value, path, digest, version, or secret — those are not fields on the request. A registry entry still
declares no capability, permission, protocol version, mediation pair, or transport: `transport` is refused
as an unknown key, because v1 is stdio by definition and a one-valued key is remote scaffolding. Remote
transport and attach remain future scope with their own design and their own approval, and no seam, key,
field, dependency, or branch anticipating them exists in v1.

Artifact identity, promotion, attestation, ARS-hosted artifact trees, ARS-managed AGENT homes, and
credential-store inspection are non-goals, not deferred work.

## Development source of truth

New work reads, in order:

1. `GOAL.md`
2. `docs/product/prd.md`
3. `docs/design/architecture.md`
4. `docs/design/technical-solution.md`
5. `docs/roadmap/features.md`
6. `docs/roadmap/current-status.md`
7. `docs/plans/active/`

`docs/design/agent-registry.md` is the operator-facing registry contract and is read with the design
layer. `docs/design/result-event-schema.md` describes emitted JSON shapes and is derivative, never a
source of product scope. Archive documents are never default development context.

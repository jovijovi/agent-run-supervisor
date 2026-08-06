# agent-run-supervisor — Product Goal

## What ARS is

**ARS is a local, unprivileged, caller-authenticated execution supervisor for external ACP AGENTs.** It
accepts a structured, caller-authorized Run request; starts one operator-registered external command
exactly as declared; supervises the resulting local process; drives the ACP lifecycle with exact
configuration fidelity; mediates permission requests against an immutable per-Run grant; and produces
bounded, redacted, per-Run evidence with irreversible terminal facts.

ARS does **not** install, package, copy, unpack, freeze, promote, pin, host, own, or attest external
AGENTs, their ACP adapters, their homes, credential stores, plugin trees, caches, or configuration. Those
are installed, configured, authenticated, upgraded, and removed by users and operators through their own
package managers, entirely outside ARS.

ARS also does not discover, resolve, mint, refresh, or manage credentials, and never itself serializes a
projected environment value into durable structured material. What an AGENT chooses to echo back is a
separate matter, bounded rather than erased — see *Environment and credential guarantee*.

Process lifecycle ownership is not software-entity ownership. ARS owns the *process it started*; the user
owns the *software it started*.

ARS is not a business orchestrator and never converts process/ACP completion into business success.

## Production shape

```text
trusted local caller (local AGENT or CLI)
        │  AgentRunRequest + frozen execution grant
        │  agent_id · model · effort · grant · limits
        │  NO command / argv / env / path / secret value
        ▼
arsd — local Unix domain socket; sole production ingress
        │  startup: parse the agents file once → immutable snapshot → reconcile → then bind
        ▼
ars-core / RunTask / Native ACP driver
        │  one ARS-owned local supervised process per Run, ACP JSON-RPC over stdio
        ▼
external ACP AGENT — the operator-registered command, launched exactly as declared
        ▼
model / provider
```

The **caller-side local AGENT** is a client that submits Runs on a user's behalf; it is never the thing
being supervised. The **external ACP AGENT** is the operator-registered command ARS starts and supervises.
The two are separate roles and are never conflated.

- `arsd` is a thin, unprivileged local service host — not a root daemon, network service, scheduler,
  multi-tenant platform, or second runtime.
- Direct `ars-core` use is test/dev-only. Production fails closed when `arsd` is unavailable.
- There is no durable per-Run Worker. One `arsd` directly owns each in-process `RunTask`, Native ACP
  connection, and external AGENT process tree.
- Native ACP never falls back to acpx.

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

## Load-bearing contracts

1. Four authority layers stay separate and are never merged, and no fifth layer exists: a source-owned
   **ACP compatibility profile** (how to speak ACP to a class of agent), an **operator-owned agent registry
   snapshot** (which command is that agent, here), a **per-Run sealed request plus launch snapshot** (what
   was requested, sealed before spawn), and **observed evidence** (what was resolved and observed,
   non-authoritative). A profile never learns from operator data or from an observation; the per-Run seal
   is a projection of profile × registry entry × request taken exactly once; observations never flow
   backward; and the wire never reaches the registry's value space.
2. Every Run owns exactly one supervised local process, from spawn to reap, and it is non-optional after
   spawn. Supervision owns process identity, bounded stderr, timeout, and terminate/kill/reap; the ACP
   client exclusively owns the live stdin/stdout JSON-RPC wire.
3. One process per Run. Same-Session continuity uses one external session id and a real `session/load`;
   AGENT processes do not survive between Runs. A reuse request can never become `session/new` — not as a
   fallback, not after a failure, not under any error class.
   **Runs terminate; Sessions do not close.** There is one Session kind, durable and indefinitely
   resumable, and no normal Session terminal state: a Run reaching `completed`, `failed`, `cancelled`,
   `timed_out`, or `unknown` never ends the Session it ran under. ARS cannot observe "the user is finished
   with this conversation" — Run completion and caller silence prove nothing about abandonment — so it
   never infers one. A `submit` without `session_id` atomically creates one durable Session and its first
   Run; a `submit` with `session_id` is existing-only reuse. Concurrency is a lease concern, machine-proven
   unsafe continuity is a quarantine concern, and storage is a data-governance concern; none of the three
   is a Session lifecycle.
4. Model and effort are immutable within a Run, switchable only between completed Runs on the same external
   AGENT Session, and proved by exact literal readback before any prompt. An unadvertised value, an alias,
   a coercion, or an inexact readback yields zero Turn and no prompt. The live-advertised option set is the
   domain authority; no source-frozen value domain gates it. A profile may declare that its class of agent
   exposes no independent effort selector, in which case no effort is discovered or set and the effective
   effort is a shared `N/A` sentinel.
5. A prompt that may have been dispatched without a trustworthy terminal result ends as `Run=unknown`,
   `Session=quarantined`, `retryable=false`. It is never replayed, resumed, or retried automatically;
   successor work is a separate caller-authorized Run.
6. Permission mediation is default-deny and must be proven by a real denied-action canary per registered
   agent, before that agent's use. It is cooperative-agent policy enforcement, **not** an OS sandbox. The
   mediation environment binding is source-owned in key and value, applied last, and a registry entry may
   select one or none but can never author, replace, or disable it.
7. Native ACP state uses its own isolated run and session roots. Native code never reads, writes, imports,
   mirrors, or migrates acpx/legacy session storage.
8. Crash containment depends on a user-level service manager cgroup, which is real, load-bearing, and
   **external** to ARS: an `arsd` crash terminates the AGENT descendants that remain in its cgroup, and
   restart performs reconciliation only — it never resends a prompt.
9. A source profile freezes **ACP protocol and compatibility semantics only**, and carries no path,
   version, digest, model literal, agent name, value domain, artifact identity, or deployment fact. Every
   AGENT is instead one **operator-owned registry entry** carrying the command and its argv, read once at
   daemon startup into an immutable snapshot. An AGENT or adapter upgrade behind an unchanged registered
   command therefore costs no ARS action at all, while any registry edit costs exactly one
   drain-and-restart. That restart invalidates no Session by itself, because no Session identity field
   derives from registry bytes, mtimes, digests, command paths, or observed runtime facts: an
   identity-preserving edit keeps reuse. Continuity is cut only by a deliberate change to a semantic
   identity choice — the operator's continuity epoch, the entry's `agent_id`, or its selected profile.

## Environment and credential guarantee

Every environment value is sensitive, regardless of key name, source class, length, or apparent shape. The
guarantee has exactly two parts, and they are deliberately unequal.

**What ARS will not do.** ARS never serializes a projected value — and never a digest, fingerprint,
length-by-value, or other metadata computed to represent one — out of the resolved carrier and into
structured launch, Spec, or environment material, into any hash input, or into a configuration-inspection
response. The carrier is non-serializable, has exactly one consumer, and is handed to exec and to nothing
else; it is never rendered into a log line or an exception message. Durable environment evidence is
value-blind: per name, the name, its source class, its precedence layer, and its redaction status, and
nothing else.

**What ARS does not attempt.** ARS does **not** scan free-form Run text against the set of values it
projected. An AGENT that echoes a projected value back — through a final message, an event field, a
tool-call id, agent self-report metadata, usage metadata, stderr, or the external Session id it mints —
may therefore have that value **retained** in bounded Run and Session evidence, unless it is caught by the
controls that remain: static credential-shape and sensitive-key redaction, categorical containment of
exception and dependency text, bounded evidence ceilings, and the value-blind structured projection above.
That is a deliberate trade, and it means an operator who treats a projected value as a secret must reason
about what the AGENT does with it, exactly as they already must for what the AGENT sends to its provider.

ARS does not discover, resolve, mint, refresh, store, or manage any credential. AGENTs authenticate
through their own stores under their own `HOME`, exactly as they do interactively. `credential_refs` stay
caller-supplied **references** recorded as admission evidence and grant material; they are never resolved
to values and never reach the child environment.

**ARS does not claim that no sensitive value reaches the child.** An operator who declares a provider
token, a proxy URL with embedded credentials, an agent socket, or any overlay literal transmits that value
to the child process in memory, by their own declaration, recorded only by name and source class.

## Filesystem boundary

ARS-owned **writable** surfaces are exactly two: the supervisor root holding Run and Session state, through
a single storage seam, and the configured UDS runtime path. Nothing else. The private per-Run
launch-permission material a profile may select lives inside the first of the two — under that Run's own
directory — and is removed once the child is proven reaped; it is not a third surface.

Read-only access is permitted wherever the paths live, including below a user's home directory and through
symlinks and PATH shims: the operator registry file once at startup, everything the kernel and loader need
to resolve and launch the declared command, liveness reads for its own child, and the caller-bound
workspace.

ARS never creates, writes, populates, stages, mirrors, repairs, deletes, or otherwise **manages** AGENT
auth, configuration, cache, plugin, or Session state, and never **inspects** those surfaces as a control
surface — no stat audit, no mode or ownership enforcement, no digest, no required-absence check. The child
AGENT may mutate its own home and state normally, and such a Run completes normally.

## Process boundary

ARS guarantees a new POSIX session and process group for the child, process identity recorded immediately
after spawn, signals delivered to the process group on terminate and kill, and wait plus reap of its direct
child before releasing a lease or returning a terminal that permits deregistration.

ARS therefore reliably terminates the direct child and every descendant that **remains in the process group
ARS created**. It does not control a payload that leaves that group or is relocated to another supervisor,
namespace, or cgroup. When work continues elsewhere anyway, the uncertainty rules apply and the Run fails
loudly as `unknown`/`quarantined`/`retryable=false`.

ARS claims no isolation or containment of hostile code. An operator who wants isolation registers the
isolation wrapper as the command; a wrapper that `exec`s into the payload composes cleanly, while one that
relocates the payload to another supervisor breaks ARS's termination and timeout guarantees. That is the
operator's knowing choice, and ARS makes no claim about it.

## acpx boundary

acpx is not a supported product, runtime, fallback, or compatibility baseline, and is never a Native ACP
driver or degraded path. The product direction is to **remove all acpx product, runtime, and compatibility
content from ARS** and to retain acpx only as a bounded differential/comparison test reference.

That removal is separately authorized source and documentation work that this document neither performs
nor approves. Until it lands, acpx receives no new capability and never directs development, and its
archived requirements never reopen as product direction.

## Authority and approval boundary

This goal, the PRD, the architecture, the technical solution, and the operator-facing agent-registry
contract are documentation authority. They describe the target and do not authorize work by their
existence. Source/main capability state comes from repository/source and the feature tracker where
applicable. Published package/release facts come from live GitHub Releases and PyPI; deployed/running facts
come from operator-held runtime/live checks. `docs/roadmap/current-status.md` is limited to lean task state,
the active plan, and open gates.

Release and publication, production cutover, service install/enable/restart, migration, integration with
any caller platform, public ingress, and live or default-on behavior each require separate, explicit
authorization. Approvals are narrow and non-transitive: a merge is not a release, a release is not a
deployment, and no green verification and no prior approval transfers to the next change.

## Non-goals

Public ingress, TCP/root service, distributed scheduling, multi-tenant cloud control plane, broad RBAC,
durable per-Run Worker, runtime plugin platform, remote transport, attach-to-running-agent, plugin loading,
containers, sandboxing, ARS credential resolution, acpx fallback, shared or imported acpx session storage,
generalized Session rebind, cross-AGENT Session reuse, automatic replay, Session close/revoke, one-shot or
ephemeral Sessions, standalone empty-Session creation, automatic Session expiry by age or silence,
destructive Session purge, workspace content-digest service,
filesystem watcher, second conversation database, hostile-process sandbox claims, and embedding
caller-side business, messaging, or delivery semantics in ARS.

The agent registry adds no exception. The caller supplies no command, argv, environment key or value,
path, digest, version, or secret — those are not fields on the request. A registry entry declares no
capability, permission, protocol version, mediation pair, or transport: stdio is the transport, and an
unknown key is refused. Remote transport and attach remain future scope with their own design and their own
approval, and no seam, key, field, dependency, or branch anticipating them exists.

Artifact identity, promotion, attestation, ARS-hosted artifact trees, ARS-managed AGENT homes, and
credential-store inspection are non-goals, not deferred work.

## Related authority

- [`docs/product/prd.md`](docs/product/prd.md) — product requirements
- [`docs/design/architecture.md`](docs/design/architecture.md) — system shape and ownership
- [`docs/design/technical-solution.md`](docs/design/technical-solution.md) — module design
- [`docs/design/agent-registry.md`](docs/design/agent-registry.md) — the operator registry contract
- [`docs/roadmap/features.md`](docs/roadmap/features.md),
  [`docs/roadmap/current-status.md`](docs/roadmap/current-status.md),
  [`docs/roadmap/non-approvals.md`](docs/roadmap/non-approvals.md) — current roadmap, status, and explicit
  non-approvals

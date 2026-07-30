# agent-run-supervisor — vNext Goal

## Product identity

`agent-run-supervisor` (ARS) is local execution and supervision infrastructure for external ACP AGENTs.
It accepts a caller-authorized, structured Run request and proves whether that Run happened under the
frozen identity, configuration, workspace, permission, process, and evidence constraints.

ARS is not a business orchestrator and never converts process/ACP completion into business success.

## Only production shape for new development

```text
Hermes / FlowWeaver / trusted local CLI
        │  AgentRunRequest + frozen execution_grant
        ▼
arsd — local Unix domain socket; sole production ingress
        ▼
ars-core / RunTask / Native ACP Driver
        ▼
registered external ACP AGENT process
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

ARS owns caller authentication, approved-resource binding, immutable per-Run execution grants,
AgentProfile resolution, process/ACP lifecycle, Session/Run technical state, permission mediation,
recovery semantics, and redacted evidence. ARS enforces the frozen grant but never widens it and is
not a broad RBAC or policy-decision engine.

External AGENTs own their actual conversation/context state. ARS stores only the minimal SessionBinding
and runtime ledger needed for supervision, recovery, duplicate prevention, progress, configuration,
and result verification.

## vNext load-bearing contracts

1. Three runtime-authority layers stay separate and are never merged: a code-closed
   `AgentProfile`/`AdapterContract`, an operator-owned Runtime Binding, and a per-Run sealed
   `ResolvedLaunchSpec` plus runtime provenance. Admission resolves the closed contract, projects the
   accepted Binding slots, materializes a controlled `ResolvedLaunchSpec`, and seals immutable
   `AgentRunSpec/spec_hash` before spawn.
2. A supervised `ManagedProcess` owns PID/PGID/identity, bounded stderr, timeout, terminate/kill/reap;
   the ACP SDK exclusively owns the live stdin/stdout JSON-RPC wire.
3. v1 uses process-per-Run. Same-Session continuity uses one external session ID and real
   `session/load`; AGENT processes do not survive between Runs.
4. model/effort are immutable per Run, switchable only between completed Runs on the same external
   AGENT Session: load → discovery → set model → rediscovery → set effort → exact readback → prompt.
5. A prompt that may have been dispatched without a trustworthy terminal result ends as
   `Run=unknown`, `Session=quarantined`, `retryable=false`. It is never replayed, resumed, or retried
   automatically; successor work is a separate caller-authorized Run.
6. Permission mediation is default-deny and must be proven by a real denied-action canary. It is
   cooperative-agent policy enforcement, not an OS sandbox.
7. Native state uses isolated `native-runs/` and `native-sessions/` roots. Native code never reads,
   writes, imports, mirrors, or migrates acpx/legacy session storage.
8. Production crash containment uses a user-level service manager/cgroup: an `arsd` crash terminates
   all AGENT descendants; restart performs reconciliation only and never resends a prompt.
9. Every AGENT is reached through a typed, versioned, code-registered closed profile with registered
   selectors and no arbitrary command/argv/env/JSON passthrough. The profile's `AdapterContract` is
   the source-frozen compatibility contract: stable profile ID, revision, `adapter_contract_hash`,
   `launch_kind` (`wrapped_acp` or `direct_acp`), the accepted Binding schema and slot projection,
   executable/argv construction, code-known env keys only, ACP protocol/name and required plus
   forbidden capabilities, permission/config/model/effort/session semantics, wrapped
   adapter/interpreter artifact identity, and a code-owned safe version-probe rule. The registry
   holds one direct-ACP profile (OpenCode), two wrapped official adapters (Codex ACP, Claude Agent
   ACP), and one versioned standard direct-ACP profile that freezes ACP-v1 conformance only and is
   instantiated per operator-owned Agent Registration.
10. A Runtime Binding carries operator-owned deployment facts only: the external CLI artifact
    descriptor (immutable versioned path, actual version, digest), optional values for
    Profile-declared config-root slots, a positive `session_compatibility_epoch`, and an acceptance
    receipt reference recorded as provenance — never as self-authorization. A Binding never declares
    a command, argv, env key, adapter, launch kind, capability, permission, or selector. Every slot
    binds to the exact profile ID, revision, and `adapter_contract_hash` that accepted it; after a
    contract revision a stale generation fails closed instead of being reinterpreted by a new source
    contract.
11. A profile whose contract declares `requires_agent_registration` is instantiated by a typed,
    bounded, operator-owned **Agent Registration** anchored inside its Binding root at
    `profiles/<profile_id>/agents/<agent_id>/`. A registration may only *select within* or *narrow*
    a bound the source contract already declared — ACP name, bounded argv tokens, selector ids and
    their value domains, a superset of the source forbidden-capability floor, one source-registered
    permission-mediation binding or none, and credential slot names. It supplies no executable,
    path, digest, version, env key, launch kind, protocol version, or capability requirement, and it
    is never a runtime plugin surface. Its `agent_registration_hash` — computed over everything
    except provenance — is sealed into the Run spec, the launch record, and Session identity, so an
    edit fails stale work closed rather than reinterpreting it. Agent identity is carried, generic:
    no runtime path branches on an agent name. A profile id that names an ACP generation
    (`…-v<N>`) must freeze exactly that protocol major, so a future generation is a separate
    profile, registration, Binding, and Session domain rather than a revision of this one.

## acpx removal direction

acpx is not a supported product, runtime, or compatibility baseline. The product direction is to
**remove all acpx product, runtime, and compatibility content from ARS** and to retain acpx only as a
bounded differential/comparison test reference.

That removal has not landed. acpx code paths still exist in source, and removing them — together with
the user-facing and design documentation that describes them — is separately authorized source/docs
work that this document does not perform or approve. Until it lands, acpx receives no new capability,
is never a Native ACP driver, fallback, or degraded path, and never directs vNext development. Its
old requirements, architecture, plans, and phase vocabulary are archived.

The cold snapshot is `docs/archive/pre-vnext-reset-2026-07-21/`. Closed plans and phases remain under
their archive directories. Git history remains the implementation audit trail.

## Current status and authorization

This goal, the PRD, architecture, and technical solution are documentation authority. They describe the
target and do not authorize work by their existence. Volatile implementation status belongs in
`docs/roadmap/current-status.md`, which is where each merged change is expected to be recorded; that
board is not restated here and does not, by itself, evidence the source facts below.

On current `main`, Stage 0/1 Native ACP and Stage 2 `arsd` (A1–A5, including the caller-UID policy and
user-service/cgroup enablement) are merged, and the closed registry holds three profiles.
Release/publication, Sachima integration, public ingress, and any Gateway/IM/live behavior each still
require separate, explicit authorization — and registration plus local acceptance never transfers
approval to the next change.

The three-layer split in contracts 1, 9, and 10 is implemented in source on `main`. Each registered
profile carries a source-frozen `AdapterContract`; a single Binding reader module is the only reader of
an operator-owned Binding root; admission performs exactly one Binding read per Run, projects the
accepted slots, and seals the resulting `ResolvedLaunchSpec` plus runtime provenance before spawn;
`arsd` requires an explicit Binding root (`--binding-root`) both in daemon mode and when rendering a
service unit; and every registered profile refuses admission fail-closed when no Binding is configured,
validated, or promoted.

The wrapped-adapter artifact identity is now a complete package closure in source: each `wrapped_acp`
contract freezes the adapter's npm **install root** and that root's whole tree digest beside the
interpreter and entry, so the sibling code and hoisted dependencies the entry resolves are frozen too;
it also freezes the interpreter argv prefix that closes the runtime's out-of-closure module search,
because a frozen tree is not a closure while the interpreter can still load code from elsewhere. Both
wrapped profiles bumped a revision for it, and their frozen paths name the root-owned artifact location
a later materialization step is expected to create — not the service account's home, whose ancestors no
per-leaf ownership change could ever make non-writable by the service UID.

Implemented source is still not closure of the Runtime Binding *layer*: operator gates are separate and
open in their own right — materializing that immutable, non-service-writable artifact root, whose
ancestor chain must also carry no further module-resolution root, authoring/validating/promoting a
Binding generation, and re-accepting each profile at its current revision are operator actions. Nothing
under that artifact prefix exists yet, and no Binding root creation, generation promotion, profile
acceptance, artifact installation, service restart, rollout, or deployment is approved by this
document.

## Non-goals

Public ingress, TCP/root service, distributed scheduling, multi-tenant cloud control plane, broad RBAC,
per-Run Worker, runtime plugin platform, arbitrary launch/config passthrough, acpx fallback, shared or
imported acpx session storage, generalized Session rebind, cross-AGENT Session reuse, automatic replay,
workspace content-digest service, filesystem watcher, hostile-process sandbox claims, and embedding
Feishu/Gateway/business semantics in ARS.

Runtime Binding adds no exception to that list: operator-declared launch commands, argv, env keys,
adapters, launch kinds, capabilities, permissions, or selectors are a non-goal, and so is any
caller-selected runtime, path, version, digest, or Binding generation.

## Development source of truth

New work reads, in order:

1. `GOAL.md`
2. `docs/product/prd.md`
3. `docs/design/architecture.md`
4. `docs/design/technical-solution.md`
5. `docs/roadmap/features.md`
6. `docs/roadmap/current-status.md`
7. `docs/plans/active/`

Archive documents are never default development context.

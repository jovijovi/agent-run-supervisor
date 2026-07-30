---
title: "Binding-era GOAL contracts 9-11 (retired)"
status: archived
created_at: 2026-07-30
archived_at: 2026-07-30
deprecated_reason: "Replaced by one profile/registry contract in the current GOAL.md"
---
# Binding-era GOAL contracts 9-11 (retired)

Preserved from tracked `GOAL.md` as it stood on the `v0.5.3` line (source document `created_at`
2026-07-21). The three contracts below were **deleted** from `GOAL.md` by the V4 boundary reset and
replaced by a single profile/registry contract. Read them as the historical record of what ARS
required at the time.

> Every claim in this file is a **past** claim. ARS no longer freezes artifact identity, no longer owns
> a Binding root, and makes no integrity or attestation claim about software it does not own.

## Contract 9 — closed profile registry with source-frozen `AdapterContract`

> 9. Every AGENT is reached through a typed, versioned, code-registered closed profile with registered
>    selectors and no arbitrary command/argv/env/JSON passthrough. The profile's `AdapterContract` is
>    the source-frozen compatibility contract: stable profile ID, revision, `adapter_contract_hash`,
>    `launch_kind` (`wrapped_acp` or `direct_acp`), the accepted Binding schema and slot projection,
>    executable/argv construction, code-known env keys only, ACP protocol/name and required plus
>    forbidden capabilities, permission/config/model/effort/session semantics, wrapped
>    adapter/interpreter artifact identity, and a code-owned safe version-probe rule. The registry
>    holds one direct-ACP profile (OpenCode), two wrapped official adapters (Codex ACP, Claude Agent
>    ACP), and one versioned standard direct-ACP profile that freezes ACP-v1 conformance only and is
>    instantiated per operator-owned Agent Registration.

## Contract 10 — operator-owned Runtime Binding

> 10. A Runtime Binding carries operator-owned deployment facts only: the external CLI artifact
>     descriptor (immutable versioned path, actual version, digest), optional values for
>     Profile-declared config-root slots, a positive `session_compatibility_epoch`, and an acceptance
>     receipt reference recorded as provenance — never as self-authorization. A Binding never declares
>     a command, argv, env key, adapter, launch kind, capability, permission, or selector. Every slot
>     binds to the exact profile ID, revision, and `adapter_contract_hash` that accepted it; after a
>     contract revision a stale generation fails closed instead of being reinterpreted by a new source
>     contract.

## Contract 11 — Agent Registration inside the Binding root

> 11. A profile whose contract declares `requires_agent_registration` is instantiated by a typed,
>     bounded, operator-owned **Agent Registration** anchored inside its Binding root at
>     `profiles/<profile_id>/agents/<agent_id>/`. A registration may only *select within* or *narrow*
>     a bound the source contract already declared — ACP name, bounded argv tokens, selector ids and
>     their value domains, a superset of the source forbidden-capability floor, one source-registered
>     permission-mediation binding or none, and credential slot names. It supplies no executable,
>     path, digest, version, env key, launch kind, protocol version, or capability requirement, and it
>     is never a runtime plugin surface. Its `agent_registration_hash` — computed over everything
>     except provenance — is sealed into the Run spec, the launch record, and Session identity, so an
>     edit fails stale work closed rather than reinterpreting it. Agent identity is carried, generic:
>     no runtime path branches on an agent name. A profile id that names an ACP generation
>     (`…-v<N>`) must freeze exactly that protocol major, so a future generation is a separate
>     profile, registration, Binding, and Session domain rather than a revision of this one.

## Retired contract-1 wording (three runtime-authority layers)

Contract 1 was rewritten rather than deleted. Its Binding-era wording was:

> 1. Three runtime-authority layers stay separate and are never merged: a code-closed
>    `AgentProfile`/`AdapterContract`, an operator-owned Runtime Binding, and a per-Run sealed
>    `ResolvedLaunchSpec` plus runtime provenance. Admission resolves the closed contract, projects the
>    accepted Binding slots, materializes a controlled `ResolvedLaunchSpec`, and seals immutable
>    `AgentRunSpec/spec_hash` before spawn.

## Retired artifact-closure and `/opt` statements

These paragraphs stood in `GOAL.md` under "Current status and authorization" and were deleted whole:

> The wrapped-adapter artifact identity is now a complete package closure in source: each `wrapped_acp`
> contract freezes the adapter's npm **install root** and that root's whole tree digest beside the
> interpreter and entry, so the sibling code and hoisted dependencies the entry resolves are frozen too;
> it also freezes the interpreter argv prefix that closes the runtime's out-of-closure module search,
> because a frozen tree is not a closure while the interpreter can still load code from elsewhere. Both
> wrapped profiles bumped a revision for it, and their frozen paths name the root-owned artifact location
> a later materialization step is expected to create — not the service account's home, whose ancestors no
> per-leaf ownership change could ever make non-writable by the service UID.

> Implemented source is still not closure of the Runtime Binding *layer*: operator gates are separate and
> open in their own right — materializing that immutable, non-service-writable artifact root, whose
> ancestor chain must also carry no further module-resolution root, authoring/validating/promoting a
> Binding generation, and re-accepting each profile at its current revision are operator actions. Nothing
> under that artifact prefix exists yet, and no Binding root creation, generation promotion, profile
> acceptance, artifact installation, service restart, rollout, or deployment is approved by this
> document.

The source-frozen artifact prefix named by those statements was `/opt/agent-run-supervisor/artifacts/`.
The reset stops referencing it. It is not deleted by the reset, and deleting it is a separate operator
decision that the current authority chain does not take.

## Retired non-goal wording

> Runtime Binding adds no exception to that list: operator-declared launch commands, argv, env keys,
> adapters, launch kinds, capabilities, permissions, or selectors are a non-goal, and so is any
> caller-selected runtime, path, version, digest, or Binding generation.

Under the reset the operator **does** declare the command and its argv, in the agent registry, while the
caller still selects none of them. The current non-goal list states that boundary directly.

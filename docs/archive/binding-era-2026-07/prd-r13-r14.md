---
title: "Binding-era PRD R13 and R14 (retired)"
status: archived
created_at: 2026-07-30
archived_at: 2026-07-30
deprecated_reason: "Replaced by PRD R13 agent registry, R14 observed evidence, and R15 environment projection"
---
# Binding-era PRD R13 and R14 (retired)

Preserved from tracked `docs/product/prd.md` as it stood on the `v0.5.3` line (source document
`created_at` 2026-07-21). R13 and R14 were **deleted** from the PRD by the V4 boundary reset. Their
numbers were reused for different requirements; do not confuse the two.

> Every claim in this file is a **past** claim. In particular, ARS no longer verifies artifact digests,
> no longer freezes package closures, and no longer asserts any ownership or mode requirement over
> software it does not own.

## R13 — Runtime Binding: operator-owned deployment facts (retired)

> Three authority layers stay separate and are never merged:
>
> | Layer | Owner | Freezes | Never carries |
> |---|---|---|---|
> | `AgentProfile` / `AdapterContract` | code (registry) | compatibility semantics | deployment paths, versions, digests |
> | Runtime Binding | operator (outside the repository) | deployment facts | command, argv, env key, adapter, launch kind, capability, permission, selector |
> | `ResolvedLaunchSpec` / runtime provenance | one Run | the resolved, sealed launch and runtime identity | anything re-read after sealing |
>
> **Contract side.** `AdapterContract` source-freezes: stable profile ID, revision, and
> `adapter_contract_hash`; `launch_kind` (`wrapped_acp` or `direct_acp`); the accepted Binding schema and
> slot projection; the fixed executable/argv construction and code-known env keys only; ACP
> protocol/name plus required and forbidden capabilities; permission, config, model, effort, and session
> semantics; the wrapped adapter/interpreter artifact identity; and a code-owned safe version-probe rule.
>
> **Binding side.** A Binding generation supplies only: its declared contract identity (profile ID, profile
> revision, `adapter_contract_hash`), the external CLI artifact descriptor (immutable versioned path, actual
> version, digest), optional values for Profile-declared config-root slots, a positive
> `session_compatibility_epoch`, and a provenance block. Every slot binds to the exact profile ID, revision,
> and `adapter_contract_hash` that accepted it. After a contract revision, stale generations fail closed; a
> Binding is never reinterpreted by a new source contract.
>
> **Acceptance authority.** A generation is accepted only on those explicit machine identity fields plus
> trusted owner and artifact validation. Provenance metadata — creation time, `accepted_by`, `accepted_at`,
> and the acceptance receipt reference/hash — is recorded and reported, never consulted: it never
> self-authorizes, never substitutes for a missing or mismatched machine field, and never becomes a profile
> identity field. A generation with a valid receipt but the wrong declared contract identity is refused.
>
> **Artifact identity covers the complete executable code closure.**
>
> - Standalone native binary: regular-file SHA-256, plus the interpreter/dynamic-loader policy where one
>   applies.
> - Package or launcher CLI: an immutable package root/tree or canonical manifest digest, the launcher
>   identity, and the required interpreter/runtime identity. A launcher-file hash alone never freezes the
>   sibling code that launcher loads, and ARS must not claim that it does.
> - Wrapped ACP adapter: the same rule applied to the source-frozen side. The closure root is the
>   smallest root the runtime's own module resolution cannot escape downward from — for a Node adapter,
>   the npm install root the entry's parent walk reaches, because dependencies hoist above the package
>   directory. The frozen entry must lie inside that root, judged on path components so a sibling
>   directory sharing a name prefix is never mistaken for a member, and no further module-resolution
>   root may exist on the ancestor chain above it. A tree digest is necessary and not sufficient: the
>   contract must also freeze the interpreter argv prefix that disables the runtime's *path-independent*
>   search roots, and that prefix is contract identity — hashed, sealed, and re-proven against the real
>   argv at the spawn boundary — never an incidental launch literal.
> - Every source-frozen runtime path names the root-owned artifact location a separate materialization
>   step is expected to create. A path under the service account's home can never satisfy the ownership
>   rule, because its ancestors are service-owned and no per-leaf change fixes that; declaring such a
>   path would push the contradiction into deployment. Declaring the expected path is not creating it.
> - The artifact and every path ancestor are operator- or root-owned and non-writable by the `arsd`/AGENT
>   UID.
>
> **Layout and validation.** One daemon takes one Binding root and the registry is closed at several
> profiles, so the root's active-selection namespace is **profile-scoped**: it holds one independently
> promotable active selection per registered profile, under
> `profiles/<profile_id>/active.json` plus `profiles/<profile_id>/generations/<id>/manifest.json`. The
> pointer is a regular, atomically replaced file and there is no active symlink. The pointer declares its
> own `profile_id` as a machine field, so a pointer or generation belonging to one profile can never
> satisfy another — by path separation and by explicit identity, not by filename. The subtree component
> is derived from the already-resolved closed profile; no request field reaches it, and an id that is not
> a safe path component is refused. Validation requires strict canonical JSON within a finite size bound,
> `O_NOFOLLOW`/dirfd walks, verified ownership, modes, and ancestors, and refusal of traversal, symlink,
> FIFO, device, unknown fields, and unknown slots. ARS creates no directory in a Binding root: a
> promotion into a subtree the operator has not authored is refused, never materialized.
>
> **Promotion and admission.** `validate` and `promote` obtain the real external CLI version through the
> Profile's code-owned version probe and compare it with the Binding; a manifest's version string alone is
> not proof. Promotion and rollback replace exactly one profile's pointer, so updating one profile can
> never disable or overwrite another's selection, concurrently or in sequence. Admission reads that
> profile's `active.json` and the selected generation exactly once per Run, revalidates
> contract match and artifact digest against the trusted immutable paths, resolves the complete
> launch/runtime identity, writes write-once `launch.json`, and seals `launch_spec_hash`. Spawn,
> finalization, and reconciliation never reread the active Binding, and admission never accepts caller
> selection.
>
> **Operator surface.** The installed commands are `runtime-binding validate`, `promote`, `rollback`, and
> `inspect-run`. There is no `--force` and no internal `sudo`. Pure Binding promotion does not restart
> `arsd`; changing the Binding root, the service unit, or the runtime does, and remains separately
> approved. `inspect-run` recomputes the launch hash after excluding only the top-level
> `launch_spec_hash`, and reports profile/contract, adapter/protocol, Binding generation/set/slot hashes,
> the complete CLI artifact identity/version/digest, and the epoch.
>
> **Compatibility.** `AgentRunRequest` and `AgentRunSpec` field sets, the `arsd` v1 public wire, the
> result/event grammar, reconcile semantics, and the `ManagedProcess` public API are unchanged. Old Runs
> stay readable. Old Native Sessions stay status/list/close-readable, but `session/load` on a record
> without a matching epoch fails closed.
>
> The pre-0.5.2 single-pointer root layout — one `active.json` at the root — is **rejected, not read**.
> It could hold only one activation, so honouring it would silently fail every other registered profile
> on a contract mismatch, and its pointer body cannot say which profile it activates. A root still
> carrying it is refused with the stable rule `LEGACY_BINDING_LAYOUT`, and a root with no subtree for the
> resolving profile with `PROFILE_BINDING_ABSENT`. ARS neither migrates nor repairs operator storage: the
> operator moves each generation under `profiles/<profile_id>/generations/` and re-promotes per profile,
> which is a separate operator decision.

## R14 — Agent Registration: one standard contract, many registered agents (retired)

> A profile whose contract sets `requires_agent_registration` is not frozen agent by agent in source. It
> is instantiated by a typed, bounded, operator-owned **Agent Registration** — a fourth authority that
> sits strictly *inside* layer 2, never beside layer 1.
>
> **Anchor.** An agent-scoped profile descends one level deeper than the profile-scoped layout above:
>
> ```text
> <binding_root>/profiles/<profile_id>/
> ├── active.json                            # non-agent-scoped only — unchanged
> ├── generations/<gen>/manifest.json        # non-agent-scoped only — unchanged
> └── agents/<agent_id>/                     # agent-scoped only
>     ├── registration.json
>     ├── active.json
>     └── generations/<gen>/manifest.json
> ```
>
> Field-set widening is contract-dependent, never global: a non-agent-scoped profile's pointer and
> `contract_identity` field sets are byte-identical to R13's, so its promoted generations keep resolving
> unchanged. An agent-scoped pointer additionally declares `agent_id`, and its `contract_identity`
> additionally declares `agent_id` and `agent_registration_hash` — so a pointer or generation moved
> between two agent subtrees is refused on an explicit machine field (`POINTER_AGENT_MISMATCH`,
> `REGISTRATION_CONTRACT_MISMATCH`) rather than by path separation alone. The registration is deliberately
> *not* folded into the generation manifest, despite costing a third read: folding would put agent
> identity inside `generation_hash`, so an artifact-only bump would force re-authoring agent facts and a
> rollback would silently change the agent's ACP name.
>
> **What a registration may say.** Only values that select within, or narrow, a bound source already
> declared: the ACP `agent_name`; 1..4 bounded ASCII `argv_tokens` structurally incapable of being a path
> or a shell fragment; a `version_probe_argv_suffix` validated by the contract's own probe rule, which
> keeps parser, timeout, and output bound code-owned; selector ids and their 1..32-entry value domains
> with each default inside its own domain; a `forbidden_capabilities` set that is a **superset** of the
> source floor and disjoint from the required capabilities; one `permission_binding_id` from a
> source-closed mediation registry, or `null`; credential slot names with `required_refs` a subset of
> them; and a shape-validated provenance block that is recorded and never consulted. It supplies no
> executable, path, digest, version, env key, launch kind, protocol version, or capability requirement —
> those are not fields, so the refusal is structural rather than filtered.
>
> **Identity and staleness.** `agent_registration_hash` is computed over the whole payload except
> provenance, so re-recording a receipt does not retire an agent's Sessions while any
> compatibility-bearing edit does. The generation **freezes** it: the digest a generation declares is
> compared with the digest of the Registration that is live at admission, so an in-place Registration edit
> under a promoted generation fails closed rather than being launched. That comparison is a single
> invariant carried by the runtime pair that holds both halves, and operator validation applies the same
> one, so a drifted Registration can be neither admitted nor promoted. It is also sealed into
> `AgentRunSpec.agent`, into `launch.json`, and into Session identity, and equality there is symmetric: a
> Session created under one agent is refused for another, and an agent-bearing record is refused by a
> runtime carrying none. A registration must also re-declare
> `(profile_id, profile_revision, adapter_contract_hash)`, so a contract revision retires every
> registration accepted under the old hash, closed.
>
> **Caller surface.** `AgentRunRequest` gains one optional field, `agent_id`. Admission refuses
> `requires_agent_registration XOR agent_id` in both directions before sealing, which makes the absence of
> agent identity in a sealed spec a total function of the `profile_id` in the same record. Neither
> `SPEC_SCHEMA_VERSION` nor `DIGEST_SCHEMA_VERSION` moves: the digest material drops exactly one named
> field when it is `None`, so a pre-upgrade frame digests byte-identically while a request naming an agent
> digests differently. `agent_id` is **not** a forbidden runtime-selection field — it selects among
> operator-authored, source-bounded registrations exactly as `profile_id` selects among source-registered
> profiles, and names no path, executable, argv, env key, digest, or version.
>
> **The one new exposure.** `agent_id` is the first caller-supplied value in this system to become a path
> component. It is fail-closed because the value passes the component grammar **before any filesystem
> query**; the type is judged by exact identity rather than `isinstance` and frozen once, because a `str`
> subclass with a lying `__str__`/`__eq__` is the class of bug this system has already paid for; the
> descent is dirfd-relative and `O_NOFOLLOW` under an ownership-verified directory; ARS creates nothing,
> so a caller can only name a directory an operator authored under a trusted root; and the registration
> re-declares the same `agent_id` as an explicit machine field.
>
> **Operator surface.** `validate`, `promote`, and `rollback` take `--agent`. It is required for an
> agent-scoped profile and refused for any other, both by a stable rule. No new command, no `--force`, no
> new daemon flag, and no `arsd` restart: admission re-reads the pointer per Run.
>
> **Registering a real agent is an operator sequence, not a source change.** Install the artifact under a
> root-owned immutable prefix; run zero-prompt ACP `initialize` discovery for name, protocol,
> `loadSession`, selector ids, and the model-dependent effort domain read *after* the exact model is set;
> record the code-owned CLI `--version` probe as a separate fact; run the mandatory denied-action
> mediation canary; author `registration.json` and the manifest; then `validate --agent` and
> `promote --agent`. Each step is a separate decision, and none is implied by a merged source change.

## What replaced them

| Retired | Replacement in the current PRD |
|---|---|
| R13 Runtime Binding, artifact identity, promotion, Binding operator surface | **R13 — Agent registry and preserved command semantics** |
| R14 Agent Registration inside the Binding root | **R14 — Observed evidence, explicitly non-authoritative** |
| — (new) | **R15 — Environment projection and ARS sink non-persistence** |

The retained ideas are: the caller still supplies no command, argv, env value, path, digest, or version;
the operator still owns deployment facts; one Run still seals its own launch material before spawn; and
observations still never flow backward into a frozen record. What is gone is the artifact-identity layer
that sat between them.

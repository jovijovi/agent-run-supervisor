---
title: "Binding-era architecture sections 3.1-3.3 (retired)"
status: archived
created_at: 2026-07-30
archived_at: 2026-07-30
deprecated_reason: "Deleted wholesale by the V4 boundary reset; replaced by the agent registry and the four-way boundary"
---
# Binding-era architecture sections 3.1-3.3 (retired)

Preserved from tracked `docs/design/architecture.md` as it stood on the `v0.5.3` line (source document
`created_at` 2026-07-21). Sections §3.1, §3.2, and §3.3 were **deleted wholesale** by the V4 boundary
reset.

> Every claim in this file is a **past** claim. The current architecture has no Binding root, no
> generation, no promotion, no artifact digest, no package closure, and no ownership/mode/ancestor gate.

## §3.1 Runtime authority layers (retired)

> ```text
> LAYER 1 — code-closed AgentProfile / AdapterContract          owner: the registry
>   stable profile ID · revision · adapter_contract_hash
>   launch_kind: wrapped_acp | direct_acp
>   accepted Binding schema + slot projection
>   fixed executable/argv construction · code-known env keys only
>   ACP protocol/name · required + forbidden capabilities
>   permission / config / model / effort / session semantics
>   wrapped adapter + interpreter artifact identity
>   code-owned safe version-probe rule
>         │
>         │ accepts (profile_id, revision, adapter_contract_hash)
>         ▼
> LAYER 2 — operator-owned Runtime Binding generation           owner: the operator
>   declared contract identity: profile_id · profile_revision ·
>     adapter_contract_hash          ← the only acceptance inputs
>   external CLI artifact descriptor: immutable versioned path,
>     actual version, digest, complete executable code closure
>   optional values for Profile-declared config-root slots
>   positive session_compatibility_epoch
>   provenance block: created_at, accepted_by, accepted_at,
>     acceptance receipt ref/hash   ← recorded and reported, never consulted
>         │
>         │ read exactly once per Run, at admission
>         ▼
> LAYER 2b — operator-owned Agent Registration (agent-scoped profiles only)
>   registration.json under profiles/<profile_id>/agents/<agent_id>/
>   ACP agent_name · bounded argv tokens · probe argv suffix
>   selector ids + value domains · forbidden-capability superset
>   one source-registered mediation binding id, or null
>   credential slot names · provenance   ← recorded and reported, never consulted
>   agent_registration_hash (excludes provenance) → frozen by the generation,
>     and sealed into spec, launch, and Session identity
>         │
>         ▼
> LAYER 3 — per-Run sealed ResolvedLaunchSpec + provenance      owner: the Run
>   write-once launch.json · launch_spec_hash · never re-read after sealing
> ```
>
> Layer 2b exists so one source contract can serve many standards-conforming agents without a plugin
> platform. It is strictly *inside* layer 2, never beside layer 1: every value it supplies selects within
> or narrows a bound layer 1 already declared, and it supplies no executable, path, digest, version, env
> key, launch kind, protocol version, or capability requirement. `adapter_contract_hash` deliberately does
> **not** become agent-derived — synthesizing a per-agent contract from a registration would make the
> contract hash a function of operator data, so the operator's registration would satisfy the operator's
> own manifest. The registration carries its own `agent_registration_hash` instead.
>
> A Binding never declares a command, argv, env key, adapter, launch kind, capability, permission, or
> selector. Every slot binds to the exact profile ID, revision, and `adapter_contract_hash` that accepted
> it, so a contract revision fails stale generations closed rather than letting a new source contract
> reinterpret operator-authored values.
>
> Acceptance rests on those explicit machine fields plus trusted ownership and digest validation, and on
> nothing else. The provenance block is recorded and reported for audit; it never authorizes a generation,
> never substitutes for a missing or mismatched machine field, and never becomes part of profile identity.
> A generation with a valid acceptance receipt but the wrong declared contract identity is refused.
>
> Read-once is structural, not advisory: `arsd` admission opens the Binding root once per Run — one
> pointer read and one generation read, both inside the resolved profile's own subtree — and spawn,
> finalization, and reconciliation have no Binding read path at all. Two Runs admitted on either side of a
> promotion are each sealed to what they read; an in-flight Run is never re-pointed, and a promotion for
> one profile is invisible to every other.

## §3.2 Binding layout, validation, and operator surface (retired)

> ```text
> <binding_root>/                            # operator/root-owned; outside the repository
> └── profiles/<profile_id>/                 # one independent selection per registered profile
>     ├── active.json                        # non-agent-scoped only — regular file, never a symlink
>     ├── generations/<generation_id>/
>     │   └── manifest.json                  # immutable once written
>     └── agents/<agent_id>/                 # agent-scoped profiles only
>         ├── registration.json              # operator-owned Agent Registration
>         ├── active.json
>         └── generations/<generation_id>/manifest.json
> ```
>
> The agent anchor is a **new subtree that only an agent-scoped profile descends into**, never a rewrite
> of the existing one: a non-agent-scoped profile's descent, pointer field set, and `contract_identity`
> field set are byte-identical to what they were, so its already-promoted generations keep resolving
> without migration, re-promotion, or restart. An agent-scoped pointer adds `agent_id`, and its
> `contract_identity` adds `agent_id` and `agent_registration_hash`, so separation is proven one level
> deeper than `POINTER_PROFILE_MISMATCH`: a pointer or generation moved between agent subtrees is refused
> on `POINTER_AGENT_MISMATCH` or `REGISTRATION_CONTRACT_MISMATCH`.
>
> That frozen digest is **compared against the Registration that is actually live**, not merely recorded.
> The comparison is one invariant in one place — the runtime pair that holds both halves — and operator
> validation applies it through that same object rather than restating it, so a generation whose
> Registration has drifted can be neither admitted nor promoted. Reading a generation is deliberately a
> separate, weaker act: it returns what the generation says and never claims to have admitted a
> Registration, because the object that reads one manifest is not the object that can compare two facts.
> Without that split the manifest would satisfy itself, and every other check — pointer bytes, manifest
> bytes, manifest digest, epoch, contract identity — would still pass on an in-place Registration swap,
> since none of them is about the Registration's contents.
>
> `agent_id` is the one place caller text becomes a path component. It is judged by the component grammar
> **before any filesystem query**, by exact type identity rather than `isinstance` and frozen once (a
> `str` subclass with a lying `__str__`/`__eq__` is the failure this codebase has already paid for once),
> and the descent below it is dirfd-relative and `O_NOFOLLOW` under an ownership-verified directory. ARS
> creates nothing, so a caller can only name a directory an operator authored under a trusted root, and
> the registration inside re-declares the same `agent_id` as a machine field.
>
> Reads stay exactly-once and are instrumented: three per agent-scoped Run — one `registration.json`, one
> `active.json`, one `manifest.json` — two for a non-agent-scoped Run, and **zero** during spawn,
> finalization, and reconciliation.
>
> The active-selection namespace is profile-scoped because the shape of the deployment demands it: one
> `arsd` takes one `--binding-root`, the registry is closed at several profiles, and each refuses admission
> until a generation is promoted **for that profile**. A single root-level pointer could satisfy exactly
> one of them at a time. Each subtree is therefore independent — promoting or rolling back one
> profile replaces one file inside that profile's own directory and cannot disable, overwrite, or race
> another's, concurrently or in sequence — and the generation namespace is per profile too, so two
> profiles may carry the same generation id without either meaning the other.
>
> Separation is proven twice over. The subtree component is derived from the already-resolved closed
> profile, never from request text, and is refused unless it is a safe path component; and the pointer
> declares its own `profile_id` as a machine field, so a pointer moved or copied between subtrees is
> refused (`POINTER_PROFILE_MISMATCH`) rather than inheriting authority from its filename. A generation
> still has to declare the matching contract identity on top of that.
>
> Validation is fail-closed on every read: strict canonical JSON, finite size bound, `O_NOFOLLOW`/dirfd
> walks, verified ownership, modes, and full ancestor chain, and refusal of traversal, symlink, FIFO,
> device, unknown fields, and unknown slots. There is no active symlink to retarget. ARS creates nothing
> in a Binding root: `profiles/<profile_id>/generations/` is operator-authored, an absent subtree is
> `PROFILE_BINDING_ABSENT`, and a root still carrying a pre-0.5.2 root-level `active.json` is
> `LEGACY_BINDING_LAYOUT` — refused and never read, because that layout can hold only one activation and
> its pointer cannot say whose it is.
>
> The operator command surface is exactly these, and no command beyond them is defined:
>
> ```text
> agent-run-supervisor runtime-binding validate     # probe-backed check of a generation
> agent-run-supervisor runtime-binding promote      # atomically replace active.json
> agent-run-supervisor runtime-binding rollback     # re-promote a previously validated generation
> agent-run-supervisor runtime-binding inspect-run  # per-Run provenance recomputation
> ```
>
> No `--force` is defined and no command escalates privilege internally; preparing an immutable artifact root
> is an operator action outside ARS. Each command names one registered profile — and, for an agent-scoped
> profile, one registered agent through `--agent`, which is required there and refused anywhere else — and
> touches only that subtree. Without `--agent` an operator would have no way to promote an agent-scoped
> generation at all, which would leave such a profile permanently unusable. `validate`/`promote` obtain the real external CLI version through the
> Profile's code-owned probe and compare it with the Binding — a manifest's version string alone is not
> proof. A pure Binding promotion does not restart `arsd`, because admission re-reads the active pointer
> per Run; changing the Binding root, the service unit, or the runtime does require a restart and stays
> separately approved.
>
> `inspect-run` recomputes the launch hash from the sealed launch record after excluding only the
> top-level `launch_spec_hash`, and reports profile/contract identity, adapter/protocol identity, Binding
> generation/set/slot hashes, the complete CLI artifact identity/version/digest, and the epoch.

## §3.3 Launch kinds and artifact code closure (retired)

> | Launch kind | Source freezes | Binding freezes |
> |---|---|---|
> | `wrapped_acp` (Codex ACP, Claude Agent ACP) | interpreter/Node identity, the ACP adapter's complete package closure (install root + tree digest + entry), argv construction, env keys, protocol/capability contract | downstream CLI artifact identity/version/digest, config-root slot values |
> | `direct_acp` (OpenCode) | direct launch, protocol, and capability semantics | that one executable's identity/version/digest |
> | `direct_acp`, agent-scoped (`standard-native-acp-v1`) | ACP-v1 conformance only: protocol major, required `loadSession`, real `session/load`, the accepted slot schema, the code-known env key set, the probe rule, and the forbidden-capability **floor** | that one executable's identity/version/digest, plus — through the Agent Registration (§3.1 layer 2b) — the ACP name, argv tokens, selector ids and domains, capability narrowing, and mediation selection |
>
> OpenCode is one artifact, not two: the same executable is the AGENT CLI and the ACP implementation, and
> the documentation must not pretend otherwise.
>
> The agent-scoped row freezes no agent-specific constant at all, and that is deliberate rather than
> incomplete: across standards-conforming native ACP agents the facts that actually vary are a small typed
> bounded set, while launch kind, slot schema, env allowlist, session-load requirement, required
> capabilities, protocol version, permission/config/session semantics, and the probe parser and bounds are
> shared ACP-v1 conformance that belongs in one source contract. Its `executable_key` appears in neither
> the registered-executable map nor the profile-keyed mediation map, so its executable can only arrive
> through the slot and its mediation can only arrive through a registration's selection from the
> source-closed mediation registry — the two mediation registries are disjoint by profile-construction
> invariant rather than by convention.
>
> Artifact identity must cover the complete executable code closure:
>
> - **Standalone native binary** — regular-file SHA-256, plus the interpreter/dynamic-loader policy where
>   one applies.
> - **Package or launcher CLI** — an immutable package root/tree or canonical manifest digest, the
>   launcher identity, and the required interpreter/runtime identity. A launcher-file hash alone never
>   freezes the sibling code the launcher loads.
> - The artifact and every path ancestor are operator- or root-owned and non-writable by the `arsd`/AGENT
>   UID.
>
> A `direct_acp` executable is pinned by descriptor and exec'd from that descriptor, with TOCTOU rechecks
> on both sides of the spawn window. A wrapped downstream CLI is reopened later by the adapter, which ARS
> cannot fd-pin on the adapter's behalf; the guarantee there is that the path and package closure remain
> under an immutable operator-owned root that the `arsd`/AGENT UID cannot rewrite.
>
> The same rule now holds on the wrapped **adapter** side. `WrappedRuntimeArtifacts` freezes an
> `adapter_package_root` plus its `adapter_tree_sha256` beside the interpreter and entry, and the closure
> root is chosen so the runtime's own resolution cannot leave it:
>
> - the root is the adapter's npm **install root**, not its package directory, because a Node entry at
>   `<root>/node_modules/<scope>/<pkg>/dist/index.js` resolves bare specifiers by walking its parent
>   chain — hoisted dependencies live in `<root>/node_modules`, which the package directory does not
>   contain;
> - the frozen entry is required to lie inside the root, judged on path components, so a sibling like
>   `…/1.0.0-evil` can never pass as a member of `…/1.0.0`;
> - everything at or below the root is covered by the tree digest, and the spawn boundary refuses any
>   `node_modules` on the ancestor chain **above** the root, which is the only place that parent walk can
>   still reach. Preparing an artifact root with no such directory above it is therefore part of
>   preparing an immutable root, and is an operator action.
>
> A parent walk is not Node's only way out. Its CommonJS resolution also searches path-independent
> *global folders* — `$HOME/.node_modules`, `$HOME/.node_libraries`, `<node prefix>/lib/node` — which no
> closure root can contain, and both wrapped profiles forward `HOME`. The contract therefore also freezes
> an `interpreter_argv_prefix`, which for the frozen Node is exactly `--no-global-search-paths`:
>
> - it is required and non-empty for every `wrapped_acp` contract — an interpreter with no way to close
>   that search cannot honestly carry one;
> - it is the literal head of the profile's argv, and profile construction refuses any argv that does not
>   begin with exactly the declared tokens, so the declared prefix and the real launch cannot drift;
> - it rides in `adapter_contract_hash`, is sealed into `launch.json`, and the spawn boundary compares it
>   against the real argv as a token *sequence*, with the adapter entry bound to the position immediately
>   after it — so a dropped, reordered, altered, or padded prefix refuses the Run before spawn.
>
> `NODE_PATH` and any other resolution root that would come from the environment are closed by the
> profile's own closed env allowlist rather than by these checks.
>
> The consequence the earlier gap made concrete: one adapter's `dist/index.js` stayed byte-identical
> across two adapter versions while the siblings it imports moved, so an entry digest could not tell the
> two apart. A tree digest does.
>
> Every source-frozen runtime path names the root-owned artifact location a separate materialization
> step is expected to create, under `/opt/agent-run-supervisor/artifacts/`. That is a declaration, not an
> installation: nothing under it exists, ARS never creates, copies, or re-owns it, and admission simply
> fails closed until an operator materializes it. A path under the service account's home was not an
> option — C5 requires the artifact *and every ancestor* to be non-writable by the `arsd`/AGENT UID, and
> no per-leaf ownership change can make a service-owned home satisfy that, so freezing such a path would
> only have deferred the contradiction to deployment. The currently installed adapter trees remain
> discovery and measurement sources — the frozen digests are byte identity, which ownership, mode, and
> path do not enter — and are not activation targets.

## Also retired from the same document

- the `attestation.json` artifact and every Binding-root reference in §8 storage;
- the Binding-rollback paragraph in §10, which described `runtime-binding rollback` as a narrower
  mechanism than a source revert;
- the §9 deployment-stage note that the Binding refactor "changed the authority shape on the closed
  Stage 2 line".

## What replaced them

The current `docs/design/architecture.md` replaces all three sections with the four-way boundary — source
compatibility profile, operator registry snapshot read once at daemon startup, the per-Run sealed Spec and
launch snapshot, and non-authoritative observed evidence — plus the two-writable-surface filesystem
boundary. Operator-facing registry detail lives in
[`docs/design/agent-registry.md`](../../design/agent-registry.md).

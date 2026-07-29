---
title: "Standard Native ACP (v1) — one conformance contract, many registered agents"
status: active
created_at: 2026-07-29
---
# Standard Native ACP (v1) — one conformance contract, many registered agents

Authority: `GOAL.md` contracts 9–11 · PRD R12/R13/**R14** · architecture §3.1–§3.3/§4 ·
technical-solution §1.2/§1.4/§2/§3 · [board](../../roadmap/current-status.md) ·
[features](../../roadmap/features.md) F-STANDARD-NATIVE-ACP-001.

This plan implements the smallest coherent additive vertical cut of the standard Native ACP
architecture. It defines no product goal, opens no stage, and implies no operator or runtime
approval.

## Problem

Every registered profile today freezes one specific agent: its ACP name, its argv, its selector ids,
its model and effort domains, its mediation binding. Registering the *next* standards-conforming
native ACP agent therefore means editing source, bumping a revision, and re-reviewing the registry —
even when the agent conforms to exactly the ACP v1 semantics three profiles already implement.

The facts that actually vary across conforming agents are a small typed bounded set. Launch kind,
binding-slot schema, `env_allowlist`, required capabilities, real `session/load`, protocol version,
permission/config/session semantics, and the probe parser and bounds are shared conformance that
belongs in **one** source contract.

## Scope

Additive only. One new source profile, one new pure leaf module, an agent anchor inside the existing
Binding layout, a generic `(profile, registration)` seam, optional request/spec/session agent
identity, and `--agent` on the three generation commands.

**Explicitly not in scope:** any retirement, deprecation, disablement, alias, or redirect mechanism —
not a field defaulting to `False`, not an unused rule constant, not a marker. `opencode-native-acp`
stays registered at r3, resolvable, admissible, launchable, and hash-identical, and remains the
authoritative OpenCode path throughout. Retiring it later would need its own design, its own
mechanism, and its own approval — two separate decisions, neither taken here.

Also out of scope: `inspect-run` registration reporting, any new daemon flag or `arsd` surface, any
permission-bridge or attestation change, any driver/client refactor, any version bump, and any real
OpenCode or Cursor identity, capability, or selector constant.

## Merge safety — production is live

Production runs v0.5.2 with three profile-scoped Bindings promoted under a live root. This branch must
be safe to merge with no migration, no re-promotion, and no restart, and that is a set of gates rather
than an intention:

- **Zero hash movement.** The two new `AdapterContract` fields are omit-when-default in the canonical
  projection, exactly as `wrapped_runtime` already is. All three live `profile_hash` and
  `adapter_contract_hash` values are byte-identical to the deployed ones. Any drift would fail *every*
  promoted generation on the next Run — a simultaneous outage for all three agents.
- **Zero layout movement.** Pointer and `contract_identity` field sets are contract-dependent, not
  global. A non-agent-scoped profile's descent and field sets are unchanged, so an unmodified pre-merge
  root resolves all three byte-identically.
- **Zero digest movement.** No `DIGEST_SCHEMA_VERSION` bump. The digest material drops exactly one
  named field when it is `None`, so a pre-upgrade frame hashes byte-identically while a request naming
  an agent hashes differently. A blanket null-strip is explicitly rejected: it would collapse the four
  meaningful existing nulls and change every digest in the other direction.
- **Zero daemon-surface movement.** No new `arsd` flag, no `service_unit.py` or `__main__.py` change,
  no `api_version` change. The registration reuses `--binding-root`.
- **Inert at merge.** `standard-native-acp-v1` is registered but has no subtree in the deployed root,
  so any Run naming it refuses with `PROFILE_BINDING_ABSENT` before reading anything — and no
  caller-side path names a profile id at all.

**Rollout note, not a merge effect.** The deployed root is named after the deployed commit. If that
convention holds, a *future deploy* implies a new root and re-promotion of all three profiles. Merge
alone does not: the running daemon keeps the `--binding-root` it was started with. That is an operator
consequence of the naming convention, surfaced here rather than discovered during a rollout.

## Work items

| # | Item | Files |
|---|---|---|
| W1 | Pure registration leaf: typed value, bounded grammars, provenance-excluding hash | `native_acp/agent_registration.py` |
| W2 | Two contract fields, source-closed mediation registry, `AgentInstance`, four construction invariants, the versioned profile and its registry entry | `native_acp/profile.py` |
| W3 | `agent_component`, agent anchor, `read_registration`, contract-dependent field sets, third read counter, the `AdmittedRuntimeBinding` Registration-freeze invariant, `--agent`-aware operator entry points | `native_acp/runtime_binding.py` |
| W4 | Optional `agent_id`; agent identity in `SpecAgent` and `ResolvedLaunchSpec`; explicit `to_dict` projection; instance-driven launch | `native_acp/spec.py` |
| W5 | Instance threaded through config fidelity, initialize attestation, rollback, and Session binding | `native_acp/run_task.py` |
| W6 | Agent identity in the Session record, omit-when-unset, symmetric reuse gate | `session.py`, `native_acp/storage.py` |
| W7 | Digest omit-set, agent-aware Binding read, wire nullability, factory wiring | `arsd/{admission,protocol,handlers}.py` |
| W8 | `--agent` on validate/promote/rollback | `cli.py`, `commands.py` |
| W9 | Two fake registrations and the agent-anchored fixture root — tests only, never the installed package | `tests/native_acp/binding_fixtures.py` |

## Gates

RED before implementation, GREEN after, grouped by the claim each defends.

- **G0 non-retirement.** OpenCode resolves, admits, and launches unchanged; its hashes equal the
  frozen goldens; no profile or contract field named `retired`/`deprecated`/`disabled` exists; no
  source rule constant can refuse a registered profile on identity alone.
- **G1 merge safety.** Live hash goldens · unchanged field sets and an unmodified pre-merge root ·
  byte-identical legacy digest with every other null still contributing · unchanged schema and API
  versions · `PROFILE_BINDING_ABSENT` for the new profile against a root with no agent subtree, with
  the other three still resolving from it.
- **G2 requested identity.** The biconditional — agent identity present in a sealed spec **iff** the
  profile requires a registration, refused in both directions before sealing · the legacy spec golden
  byte-identical plus a new agent-scoped golden · projection completeness over every spec dataclass
  field · two Runs differing only by agent differ in spec, launch, and digest · agent identity
  readable from `spec.json` alone.
- **G3 registration grammar.** Happy path plus unknown/missing field, non-canonical JSON, agent and
  contract-identity mismatch, path-shaped or oversized argv tokens, equal selector ids, empty domain,
  out-of-domain default, lowered capability floor, forbidden-required conflict, unknown mediation
  binding id, and a flawless provenance rescuing none of it.
- **G4 path safety.** Ten unsafe `agent_id` values refused **before any filesystem query**, asserted
  by call instrumentation rather than by inspection · a `str` subclass with a hostile `__str__`/`__eq__`
  refused on type identity · a non-string refused as an invalid request, never as an internal error ·
  each scope and absence rule by its own stable name.
- **G5 two agents, one path.** One root resolves both fakes and all three live profiles concurrently ·
  the same `generation_id` used independently · promoting one leaves every other pointer byte-identical ·
  a pointer or generation moved between agent subtrees refused on a machine field · the full
  admission→Binding→launch→Session sequence completing identically for both.
- **G8 Registration freeze.** A compatibility-bearing in-place Registration edit under an unchanged,
  promoted generation is refused by the runtime pair before anything launches, and by operator
  validation so it can never be promoted or blessed — on one machine rule, `REGISTRATION_HASH_MISMATCH`,
  raised from exactly one place. Symmetric: an agent-scoped generation with no Registration, and a
  Registration carried alongside a generation that freezes none, are both refused. A provenance-only
  edit stays compatible, because the hash excludes provenance. Read-once is unchanged: one Registration,
  one pointer, one generation.
- **G6 Session isolation.** A Session under agent A refused for agent B before the lease and before
  `session/load` · symmetric `None` rejection · a compatibility-bearing edit retires the Session and a
  provenance-only edit does not · legacy `session.json` bytes unchanged.
- **G7 structural.** Read-once — one registration, one pointer, one generation per agent-scoped Run;
  zero and two for legacy; zero during spawn/finalization/reconciliation · an AST scan proving no
  agent-name literal reaches code outside the registry module's module-level constants · a scan proving
  the registration leaf queries no filesystem · the operator surface still exactly four subcommands with
  `--agent` added and no `--force`.

## Verification ladder

Focused new/changed tests → `tests/native_acp/` and `tests/arsd/` → runtime-binding and CLI suites →
full `./scripts/verify_local.sh` → `git diff --check` → static safety and secret-shaped scans.

## Non-approvals

This plan authorizes design, implementation, and local verification on a branch. It does **not**
approve or perform: commit, push, PR, or merge; release, version bump, tag, GitHub Release, PyPI, or
CHANGELOG release-section work; deployment, rollout, service restart, or Binding promotion; creating a
Binding root or writing any file under a real one; authoring any real Agent Registration; installing or
re-owning an artifact root; freezing any real agent's identity, capability, or selector constant before
its own zero-prompt ACP discovery and code-owned probe evidence exist; claiming real Cursor E2E;
retiring, disabling, deleting, aliasing, or redirecting `opencode-native-acp`, or introducing any
mechanism capable of doing so; caller cutover; acpx removal; or Sachima/Gateway work.

Until at least one agent completes its operator registration sequence, the honest post-merge state is:
**three runnable profiles unchanged, plus one profile awaiting operator registration data.** No
standard-native agent is runnable at merge.

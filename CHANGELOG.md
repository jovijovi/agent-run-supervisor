# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `cursor-native-acp-v1` is revision 3: the profile now drives Cursor's ACP
  `mode` selector from the Run's frozen grant through one closed source-owned
  policy — `ask` when `grant_capabilities` are exactly a subset of
  `{read, search}` (including `read` and `read+search`), `agent` for every
  other valid grant. The mode is set before the model, proven by exact
  readback, and re-proven after the model set; a missing selector, an
  unadvertised target, a rejected set, a wrong readback, or a model-set side
  effect that moves the mode fails before any prompt as `CONFIG_FIDELITY` with
  zero prompt. The required mode is recomputed from the frozen grant on every
  Run, including real `session/load` reuse Runs, and model-only fidelity is
  untouched — no effort selector, effective effort stays `N/A`. `ask` is a
  **cooperative mode mitigation** of an agent that can complete an edit in
  `agent` mode without asking; it is not an OS sandbox and not a strong
  permission guarantee, and ACP permission mediation plus the post-completion
  violation detector are unchanged. Moving the mode into profile semantics
  moves only the Cursor `profile_hash`: existing revision-2 Cursor Sessions
  are refused for reuse by the existing profile-binding mismatch, deliberately,
  with no migration or compatibility logic.

### Fixed

- Reusing a quarantined Session now reports the stable `SESSION_QUARANTINED`
  detail code with `retryable=false`, instead of falling through to the generic
  `RUN_EXCEPTION`. `SessionQuarantinedError` is a *sibling* of
  `SessionBindingError` under `SessionError`, and the reuse path caught only the
  latter, so the documented refusal reached the per-Run exception guard and told
  a caller nothing more than "something threw". Both reuse seams are covered:
  the pre-lease `validate_native_binding()` check, and the reuse-only lease
  acquisition, where either committed evidence or an unconverged
  quarantine-pending fence refuses inside the session guard. Nothing else moves
  — the refusal stays pre-dispatch with no child, no `session/new`,
  no `session/load`, no prompt, no fallback Session, and no surviving lease; the
  stored Session bytes, its quarantine evidence, and any pending fence are left
  exactly as they were; and `SESSION_NOT_FOUND_FOR_REUSE`,
  `SESSION_RECORD_INVALID`, `SESSION_EXTERNAL_ID_MISSING`, and
  `SESSION_BINDING_MISMATCH` keep their existing meanings. `failure_reason` is
  the allow-listed categorical `run failed` its four siblings already project —
  `detail_code` stays the contract that distinguishes them — so no exception
  text, path, or quarantine evidence value reaches a caller.

## [0.7.0] - 2026-08-07

### Removed

- The legacy acpx product, runtime, and compatibility surface: the runtime and
  its package modules, the `validate-role`, `replay`, `doctor`, `run` (exec),
  `session`, and `cleanup` CLI leaves, the packaged and repository fixture trees,
  and the caller/retention/goal/policy/role/workspace modules that only those
  paths reached. The audited differential/comparison keep set was empty, so no
  acpx fixture was retained. One production architecture remains: `arsd` +
  ars-core + Native ACP.
- **Breaking (unreleased API v3):** `result.json` no longer carries the
  `acpx_exit_code` process-exit field, and it is no longer required for a
  terminal to be trusted. It is not renamed and not replaced — `status`,
  `detail_code`, `signal`, and `stop_reason` carry what remains. API v3 is the
  only contract: the persisted-terminal field set is closed, so a `result.json`
  carrying the retired key — or any key this version does not define — is
  untrusted evidence rather than a tolerated extension, and never reaches a
  caller. There is no tolerant reader, projection, alias, or migration, and no
  stored record is rewritten.
- `AgentRunStatus` narrows to the five Native terminals (`completed`, `failed`,
  `cancelled`, `timed_out`, `unknown`) with the exit classifier that produced the
  others.

### Added

- An acpx containment category in `tools/static_safety_scan.py`, and exact wheel
  and sdist manifest allowlists (`tools/check_dist_manifest.py`) asserted inside
  `scripts/verify_local.sh`, so the removed surface cannot return in source, in a
  shipped artifact, or in a current-authority document.

### Changed

- The installed console script exposes exactly `agents validate`,
  `agents doctor`, and `run inspect`.
- `scripts/smoke_installed_wheel.sh` becomes `scripts/smoke_installed_artifact.sh`
  and smokes both the wheel and the sdist in isolated venvs.

## [0.6.3] - 2026-08-04

### Changed

- Clarified exact Claude model selection: a fixed-model request uses a concrete
  ID that the running agent must advertise and read back byte-for-byte before a
  prompt is sent; rolling aliases are not treated as fixed IDs.
- Refreshed contributor and product-goal documentation for the current ARS
  external-AGENT supervision boundaries.

### Fixed

- Preserved Cursor Session continuity across Runs by keeping agent-owned
  configuration and Session state outside per-Run material, so later Runs can
  use real `session/load`.

## [0.6.2] - 2026-08-03

### Added

- Model-only Native ACP configuration fidelity for agents whose complete model
  selector carries the intended configuration without a separate effort control.
- Per-Run Cursor startup permissions using private, source-owned configuration
  material with fail-closed creation, evidence, cleanup, and environment ownership.

### Changed

- Upgraded the optional Native ACP Python SDK to `0.12.0` using its stdio transport.
- Removed the full-value sensitive-literal guard while retaining categorical safe
  projection, redaction, permission enforcement, and static safety checks.

### Fixed

- Cursor write and shell denials are sealed before launch; Cursor's deny-before-
  allow precedence prevents project or ancestor `.cursor/cli.json` allow rules
  from auto-approving those operations.

## [0.6.1] - 2026-08-02

### Added

### Changed

### Fixed

### Notes

## [0.6.0] - 2026-08-02

> **Release preparation.** This section describes the source line prepared as
> `0.6.0`. It is not tagged, not published to PyPI, not deployed, and not running
> anywhere. Tagging, publication, deployment, service restart, and cutover each
> remain separate decisions.

`0.6.0` is a **breaking** change for operators and callers. The V4 external-AGENT
boundary reset replaces the artifact/Binding architecture of the `0.5.x` line.
There is no in-place upgrade, no dual-read shim, and no silent fallback in either
direction, so plan one migration window per deployment.

### Changed (breaking)

- **One operator-owned agent registry replaces Runtime Binding and per-agent launch
  profiles.** Which command is which AGENT is now a single TOML file you own, read
  exactly once at daemon startup into an immutable in-memory snapshot. The
  practical win: an AGENT upgrade behind an unchanged registered command now costs
  nothing at all — no restart, no re-acceptance, and existing Sessions still reuse.
  Any registry edit costs exactly one daemon restart. An identity-preserving one
  (`command`, `args`, environment declarations, `mediation`, selectors, capability
  narrowing) invalidates no Session, because identity is never a fingerprint of
  registry bytes. Adding or changing `session_epoch`, or targeting a different
  `agent_id` or `profile`, is a different Session identity and cuts reuse — which
  is the deliberate operator escape hatch, not a side effect of restarting.
- **Daemon and unit input moves from `--binding-root` to `--agents-file`.** Both
  daemon mode and `--print-service-unit` require it, so a rendered unit can never
  silently omit the registry. An older unit file fails closed instead of appearing
  to keep working.
- **The operator surface moves to `agents validate`, `agents doctor`, and
  `run inspect`.** The `runtime-binding` command group is gone; there is no
  `promote`, no `rollback`, and no `--force`.
- **The upstream `arsd` wire is API v2.** `submit` is refused at `api_version: 1`,
  because its payload dropped `profile_id` and now requires `agent_id`. The other
  seven operations stay accepted at v1 during the drain window — including
  `server_info`, which is how an older caller discovers *that* it must upgrade — so
  in-flight Runs stay readable and cancellable (`run_status`, `run_events`,
  `run_cancel`) and their Sessions stay readable and closable (`session_status`,
  `session_list`, `session_close`). This is the ARS-owned upstream API version;
  the downstream ACP protocol ARS speaks to agents is unchanged at v1.
- **Legacy Sessions do not load across the boundary.** A Session created by the
  `0.5.x` line carries retired ARS-derived identity hashes and is refused for
  `session/load` with a stable code, while staying owner-scoped
  `status`/`list`/`close`-readable. A reuse request never becomes `session/new`
  under any error class. Continuing that work means a new Session with
  caller-owned context handoff — a deliberate, one-time continuity loss.
- **Environment values are no longer written into ARS-owned artifacts, logs,
  events, or API projections.** Durable evidence records a name, its source class,
  its precedence layer, and its redaction status, and nothing else — no value, and
  no digest, length, or fingerprint computed from one. The visible cost: short,
  common values such as `TERM`, `LANG`, `TZ`, `USER`, `HOME`, and `PATH` elements
  are in the per-run guard's literal set, so run text echoing them is replaced or
  withheld, with coarse suppression counters instead of the original text.

### Removed

- Runtime Binding roots, promoted generations, artifact digests, package closures,
  and spawn-boundary attestation. ARS makes no artifact-integrity or supply-chain
  claim, and performs no ownership, mode, ancestor, symlink, or digest check on the
  registered command.
- The three per-agent compatibility profiles, deleted rather than aliased or
  disabled. Two source profiles remain: `standard-native-acp-v1` for every agent,
  and `claude-agent-acp-compat-v1` for one evidenced ACP-level deviation.
- Existing Binding roots, `/opt` artifact trees, and every historical Run and
  Session byte on disk are **not** touched: nothing is deleted, migrated, or
  re-hashed. Removing them is your separate decision.

### Changed

- Session reuse is fail-closed. An absent or corrupt Session record, a missing
  stored external ID, or a changed binding fails *before* the lease, and a
  conflicting identity-bearing callback is rejected at entry before any handler,
  event, filesystem access, or permission decision.
- Startup reconciliation is total and ordered, with absent distinguished from
  corrupt. It is stricter than the reader it replaces: a corrupt terminal record,
  unattributable uncertainty, or a launch record without its spec now refuses to
  listen rather than guessing.
- The registered command is launched exactly as declared. `argv[0]` is the declared
  string byte-for-byte, a bare name is located by ordinary PATH lookup over the
  child's projected `PATH`, and there is no pre-flight resolution gate — so shims,
  symlink farms, package-relative resolution, and agent self-update keep working.
  A failed exec reports `COMMAND_NOT_FOUND`, `COMMAND_NOT_EXECUTABLE`, or
  `SPAWN_FAILED` as an ordinary configuration error.
- Observed runtime facts — the PATH hit, the image the kernel mapped, `agentInfo`,
  advertised capabilities, an optional operator probe — are recorded as explicitly
  non-authoritative evidence and may raise a policy-warning event, but never gate
  admission or block continuity.
- Model and effort domains come from live discovery and exact readback, so an agent
  adding a model is a non-event for ARS.
- A workspace is no longer refused for containing an AGENT's own project
  configuration file. That file is AGENT-owned.

### Migration

1. Author your agents file, then `agent-run-supervisor agents validate --agents-file <path>`.
2. `agent-run-supervisor agents doctor --agents-file <path> --agent <agent-id>` per
   agent, then run that agent's mandatory denied-action mediation canary.
3. Re-render the service unit with `--agents-file`, then restart `arsd`.
4. Move callers to `api_version` 2 and from `profile_id` to `agent_id`.
5. Expect every live Session to end once, and hand context over from the caller side.

### Notes

- Runtime stays Python standard library only. The `native` extra still pins
  `agent-client-protocol==0.11.1` for driving a real agent.
- Full operator contract: `docs/design/agent-registry.md`.

## [0.5.3] - 2026-07-30

### Added

- `standard-native-acp-v1`: a fourth registered profile of a different shape. It
  is a versioned `direct_acp` contract that freezes ACP v1 **conformance only** —
  protocol major, `loadSession` as a required capability, real `session/load`,
  the accepted Binding slot schema, the code-known env key set, and the
  code-owned version-probe rule — and freezes no agent-specific identity,
  selector, or value domain. The `-v1` suffix is load-bearing: profile
  construction refuses a contract whose frozen protocol major disagrees with the
  id, so a future `standard-native-acp-v2` is a separate profile, registration,
  Binding, and Session domain rather than a revision of this one.
- Typed, bounded, operator-owned **Agent Registration**. An agent-scoped profile
  descends one level deeper in its Binding root, to
  `profiles/<profile_id>/agents/<agent_id>/{registration,active}.json` plus that
  agent's own `generations/`. A registration may only select within, or narrow,
  a bound the source contract already declared: ACP name, 1..4 bounded ASCII
  argv tokens structurally incapable of being a path or a shell fragment, a
  probe argv suffix validated by the contract's own rule, selector ids and their
  value domains with each default inside its own domain, a
  `forbidden_capabilities` **superset** of the source floor, one
  source-registered permission-mediation binding or none, and credential slot
  names. It supplies no executable, path, digest, version, env key, launch kind,
  protocol version, or capability requirement — those are not fields, so the
  refusal is structural rather than filtered. Its provenance block is shape
  validated, recorded, and never consulted.
- A Binding generation **freezes** the Agent Registration it was accepted
  against. The `agent_registration_hash` a generation declares is compared with
  the digest of the Registration that is actually live, so editing
  `registration.json` in place under a promoted generation fails closed with
  `REGISTRATION_HASH_MISMATCH` instead of launching. The comparison is one
  constant-time invariant in one place — the runtime pair that holds both halves
  — and operator validation applies that same object, so a drifted Registration
  can be neither admitted nor promoted. Reading a generation stays a separate,
  weaker act that admits no Registration. A provenance-only edit remains
  compatible, because the hash excludes provenance.
- `AgentInstance`, the `(profile, registration)` seam every generic consumer
  asks for a fact. For the three existing profiles the registration is absent
  and every accessor returns the existing value unchanged, so no runtime path
  branches on an agent name.
- Optional `agent_id` on `AgentRunRequest`, and `agent_id` plus
  `agent_registration_hash` on the sealed `AgentRunSpec.agent`, on
  `launch.json`, and on the Native Session record — all omit-when-`None`.
  Admission refuses `requires_agent_registration XOR agent_id` in both
  directions before sealing, which makes the absence of agent identity in a
  sealed spec a total function of the `profile_id` in the same record.
- `--agent` on `runtime-binding validate`, `promote`, and `rollback`. It is
  required for a registration-scoped profile and refused for any other, each by
  its own stable rule. No new subcommand, no `--force`, no new daemon flag.

### Changed

- `AgentRunSpec.to_dict()` is an explicit projection rather than raw `asdict`,
  dropping exactly the two agent fields when they are `None`. `asdict` was
  silently guaranteeing "every field is in the hash"; a structural test that
  walks every spec dataclass field now guarantees it instead.
- Binding pointer and `contract_identity` field sets are contract-dependent
  rather than global. A profile that is not agent-scoped keeps byte-identical
  field sets and descent, so already-promoted generations keep resolving with no
  migration, re-promotion, or restart. An agent-scoped pointer adds `agent_id`
  and its `contract_identity` adds `agent_id` and `agent_registration_hash`, so
  a pointer or generation moved between agent subtrees is refused on an explicit
  machine field (`POINTER_AGENT_MISMATCH`, `REGISTRATION_CONTRACT_MISMATCH`).
- Binding reads stay exactly-once and are instrumented: three per agent-scoped
  Run, two for a non-agent-scoped Run, and zero during spawn, finalization, and
  reconciliation.

### Fixed

### Notes

- Additive and merge-safe with production live. The three registered profiles
  keep byte-identical `profile_hash` and `adapter_contract_hash` values, their
  Binding layout and field sets are unchanged, and no schema or API version
  moves. The request digest is unchanged for any frame that names no agent: the
  digest material drops exactly one named field when it is `None`, never a
  blanket null-strip, which would have collapsed the meaningful existing nulls
  and changed every digest in the other direction.
- No retirement, deprecation, disablement, alias, or redirect mechanism is
  introduced — not a field defaulting to `False`, not an unused rule constant,
  not a marker. `opencode-native-acp` stays registered at r3, resolvable,
  admissible, launchable, and hash-identical, and remains the authoritative
  OpenCode path.
- No real agent is registered. The only registrations that exist are two
  fabricated fixtures that ship in tests and never in the installed package, and
  no real agent identity, capability, or selector constant is frozen anywhere.
  `standard-native-acp-v1` is inert in a root with no agent subtree, refusing
  with `PROFILE_BINDING_ABSENT` before reading anything. Registering a real
  agent — artifact install, zero-prompt ACP discovery, code-owned CLI probe,
  mandatory denied-action mediation canary, registration authoring, then
  `validate --agent` and `promote --agent` — is a separate operator sequence,
  and no standard-native agent is runnable at merge.
- `agent_id` is the first caller-supplied value in this codebase to become a
  path component. It is judged by its component grammar before any filesystem
  query, by exact type identity rather than `isinstance` and frozen once, and
  the descent below it is dirfd-relative and `O_NOFOLLOW` under an
  ownership-verified directory. ARS creates nothing, so a caller can only name a
  directory an operator authored under a trusted root, and the registration
  inside re-declares the same `agent_id` as an explicit machine field.

## [0.5.2] - 2026-07-29

### Fixed

- A Runtime Binding root can now hold a concurrent, independently promotable
  active generation for every registered profile. The active-selection
  namespace is scoped per profile
  (`profiles/<profile_id>/active.json` plus
  `profiles/<profile_id>/generations/<generation_id>/manifest.json`), so one
  `arsd --binding-root <root>` serves OpenCode, Codex, and Claude at once.
  Previously a single root-level `active.json` meant promoting one profile made
  every other registered profile refuse admission with
  `CONTRACT_IDENTITY_MISMATCH`.

### Changed

- `active.json` carries a `profile_id` machine field, and a pointer or
  generation belonging to one profile can never satisfy another — by path
  separation and by explicit identity, not by filename. New fail-closed rules:
  `POINTER_PROFILE_MISMATCH`, `PROFILE_BINDING_ABSENT`, `PROFILE_ID_UNSAFE`,
  and `LEGACY_BINDING_LAYOUT`.
- `runtime-binding promote` and `rollback` replace only the named profile's
  pointer, so an operator action on one profile cannot disable or overwrite
  another's selection, concurrently or in sequence. The command surface is
  unchanged: `validate`, `promote`, `rollback`, `inspect-run`, still with no
  `--force`, no privilege escalation, and no daemon write.
- ARS still creates nothing inside a Binding root: `profiles/<profile_id>/` is
  operator-authored, and promoting into a subtree that does not exist is
  refused rather than materialized.

### Notes

- **Breaking for any 0.5.1-shaped Binding root.** The single root-level
  `active.json` layout is rejected, not read: it can hold only one activation,
  and its pointer body cannot say which profile it activates. A root still
  carrying it fails closed with the stable rule `LEGACY_BINDING_LAYOUT`. ARS
  does not migrate operator storage — move each generation under
  `profiles/<profile-id>/generations/<generation-id>/`, remove the root-level
  `active.json`, and run `runtime-binding promote` once per profile. No Binding
  root had been promoted anywhere, so this migrates nothing that exists.
- Source and packaging only. It does not materialize a Runtime Binding, promote
  a generation, re-accept a profile, run the Claude permission canary, deploy,
  restart a daemon, run a provider, or integrate Sachima.
- Public compatibility surfaces are unchanged: `AgentRunRequest`/`AgentRunSpec`
  field sets, the `arsd` v1 wire, the result/event grammar, reconcile
  semantics, the `ManagedProcess` API, `launch.json`'s shape, and old-Run
  readability. Admission still reads exactly one pointer and one generation per
  Run, and spawn, finalization, and reconciliation still read none.
- Runtime Binding operator activation remains open, and no request or caller
  field selects a root, generation, version, digest, or path.

## [0.5.1] - 2026-07-29

### Added

- Operator-owned Runtime Binding source framework, including the
  `runtime-binding validate`, `promote`, `rollback`, and `inspect-run` CLI
  surface for validation, pointer changes, rollback, and sealed-launch
  provenance inspection.

### Changed

- Binding admission now fails closed on an absent or unsafe Binding root, and
  seals one resolved artifact identity and provenance record per Run. Artifact
  validation covers package-tree identity and containment for wrapped adapters
  as well as downstream CLI artifacts.
- Updated the Claude adapter source contract to 0.63.0 as
  `claude-agent-acp-0.63.0` revision 4.
- The registered OpenCode profile is now `opencode-native-acp` revision 3;
  retired `opencode-1.18.4` has no compatibility alias and is refused as
  unknown.
- Daemon mode and `--print-service-unit` now require `--binding-root`. A
  v0.5.0-era service unit must be re-rendered/configured before restart, or the
  upgraded daemon or renderer fails closed.
- Aligned the READMEs and current product/roadmap documentation with the
  Runtime Binding source closure and its remaining activation boundaries.

### Fixed

- Pinned the optional `native` extra to `agent-client-protocol==0.11.1` and
  contained ACP SDK handler-exception logging at the root logger.

### Notes

- This is source/package publication only: it does not materialize a Runtime
  Binding, promote a generation, re-accept a profile, run the Claude permission
  canary, deploy, restart a daemon, run a provider, or integrate Sachima.
- Runtime Binding operator activation remains open.
- Pre-epoch Native Session records remain inspectable, listable, and closable,
  but cannot resume through `session/load` after the compatibility-epoch
  change.
- Binding generations are contract-revision-bound: OpenCode is r3, Codex is
  r3, and Claude is r4; old-contract generations fail closed.
- acpx product/runtime removal is not part of this release-prep diff.

## [0.5.0] - 2026-07-26

### Added

- Stage 2 `arsd` — the local, unprivileged Unix-domain-socket ingress that is the
  sole vNext production path (`agent_run_supervisor.arsd`):
  - Daemon module entry point `python -m agent_run_supervisor.arsd` (no console
    script; `agent-run-supervisor` stays the acpx compatibility CLI), with a
    side-effect-free `--print-service-unit` renderer for a systemd `--user` unit
    that installs, enables, and starts nothing.
  - `SO_PEERCRED` caller authentication against an explicit
    `--caller-mapping UID:principal_id:owner:namespace` policy — zero mappings
    refuse to listen — plus owner-scoped Run/Session access, a `0700` socket
    directory with a `0600` socket, and no TCP or root mode.
  - Versioned bounded JSON framing (`api_version`, unknown versions rejected),
    submit/status/events/cancel and Session status/list/close operations,
    idempotent admission keyed by caller `request_id`, bounded concurrency and
    connection backlog, graceful shutdown, and startup-only reconciliation that
    never replays or resumes a prompt.
  - Typed local client `arsd.client.ArsdClient` with explicit connect/close,
    seq-cursor event pages, an optional follow subscription, and no silent
    reconnect or replay.
- Official closed-profile registrations alongside OpenCode 1.18.4: Codex ACP
  1.1.7 (`codex-acp-1.1.7`) and Claude Agent ACP 0.61.0
  (`claude-agent-acp-0.61.0`). Each freezes its launch environment and the
  runtime identity it launches (interpreter, adapter entrypoint, downstream CLI
  by path and hash), proves that identity at the spawn boundary, binds
  credentials by reference only, and closes its model/effort domains. The Claude
  profile additionally freezes permission mode `default` and the ACP session
  metadata sent on both `session/new` and `session/load`, so ambient settings
  cannot define the permission rules or tool surface; its ACP model readback
  literal is `opus[1m]` and the direct Claude Code author selector
  `claude-opus-5[1m]` is deliberately not registered.

### Changed

- README / README.zh-CN and the current authority, design, and roadmap documents
  now describe `arsd` and the three registered profiles as implemented on `main`,
  label the acpx CLI/library as a compatibility surface rather than the vNext
  production ingress, and read publication state from the live GitHub Releases
  and PyPI surfaces instead of a hard-coded published version.

### Fixed

### Notes

- This release carries the Stage 2 `arsd` ingress and the official Codex/Claude
  profile registrations; the previous published version, 0.2.0, carried the
  Stage 0/1 Native ACP core only.
- Installing the package enables nothing. `arsd` has no console script, installs
  or starts no service unit, and refuses to listen without an operator-supplied
  caller mapping. Publication is not deployment, service enablement, or
  production activation, and registration plus operator-held local socket-path
  acceptance is not transferable to the next change.
- Sachima `ArsdBackend`/UDS integration remains separate and unimplemented.
- Permission mediation is cooperative-agent policy enforcement against a
  caller-frozen grant — not an OS sandbox, hostile-process isolation, or
  multi-tenancy. ARS never emits a business verdict.

## [0.2.0] - 2026-07-22

### Added

- Stage 0/1 Native ACP core through ars-core (`agent_run_supervisor.native_acp`),
  additive alongside the released acpx surfaces:
  - Supervised live-stdio processes (`managed_process.py`): each Run spawns its
    AGENT in a fresh POSIX session/process group with recorded `ProcessIdentity`,
    supervisor-owned bounded stderr, SIGTERM→grace→SIGKILL group escalation with
    kill metadata, and a reaping `wait()`; the official ACP SDK exclusively owns
    the live stdin/stdout JSON-RPC wire.
  - Frozen admission identity: a typed, closed `AgentProfile` registry
    (`OPENCODE_1_18_4` revision 2 — OpenCode 1.18.4 with the registered closed
    model pair `kimi-for-coding/k3` / `deepseek/deepseek-v4-pro`, literal effort
    values, and credential slot names only — never values) resolves to a
    controlled `ResolvedLaunchSpec`, then an immutable `AgentRunSpec`/`spec_hash`
    sealed before spawn; `EffectiveRunState` records observations only and never
    rewrites the frozen request.
  - Exact-or-zero configuration fidelity: initialize → `session/new` or
    `session/load` → discovery → set model → fresh model-dependent option set →
    rediscover and set effort → exact requested == effective readback; a missing
    capability, unadvertised value, or inexact readback fails before dispatch
    with zero prompt (literal `max` is never downgraded).
  - Session continuity and cross-Run switching: process-per-Run over one
    unchanged external session ID with real `session/load` (silent session
    re-creation is a hard failure); model/effort are immutable per Run and
    switch only between completed Runs with exact readback; partial switching
    sends no prompt and must prove rollback, otherwise the Session quarantines.
  - Isolated Native storage and evidence: the `native_acp/storage.py` seam binds
    `native-runs/` and `native-sessions/` roots (`0700` dirs, `0600` files),
    write-once `spec`/`launch`/`effective`/`result` artifacts and dispatch
    markers, and one bounded per-Run event writer with monotonic `seq` and
    truncation markers; legacy acpx `runs/`/`sessions/` stores are never read,
    written, or migrated (regression-pinned with poisoned same-ID fixtures and
    byte snapshots).
  - Fail-closed terminal state and duplicate prevention: additive Run statuses
    `failed`/`cancelled`/`unknown`, `prompt-dispatch-started`/`prompt-accepted`
    markers, and a finalization table under which a possibly-dispatched Run
    without a trustworthy terminal result ends `unknown` with `retryable=false`
    and quarantines its Session; nothing auto-retries, replays, or resumes it,
    and successor work is a new caller-authorized Run linked by
    `retry_of_run_id`.
  - Permission and workspace boundaries: `PermissionBridge` enforces the frozen
    per-Run execution grant default-deny (registered workspace-internal reads
    allowed; write/terminal/unknown operations denied) with redacted
    `MediationEvent` evidence — cooperative-agent mediation, not an OS sandbox.
  - Cancellation and finalization cleanup: supervisor cancel/timeout escalates
    through ACP cancel and process-group termination; finalization reaps the
    child, persists one irreversible terminal fact, and releases the session
    lease on all paths, including quarantine.
  - Packaging: optional `native` extra pinning `agent-client-protocol==0.11.0`
    with SDK contract tests; the base install stays stdlib-only.

### Changed

- Dev/CI installs are lock-enforced and include the Native suite: `make sync`
  and the CI verify jobs run `uv sync --locked --extra dev --extra release
  --extra native`, and the canonical verifier gained an `uv lock --check` gate.

### Fixed

- Inbound `session/update` drain ordering in the new Native driver: prompt
  completion waits for every update frame observed before the prompt response
  to finish its client callback (a pre-response delivery barrier), so
  finalization can never cancel queued handlers and silently lose
  final-message/event evidence.

### Notes

- Stage 0/1 is a library-level core with real OpenCode 1.18.4 B-grade
  acceptance evidence (exact K3/`max`, `session/load` context continuity across
  process-per-Run, and registered-model switching). It is not production
  acceptance: this version ships no `arsd` daemon, no Native service or Native
  CLI production entry, and no Stage 2 socket-path acceptance; production
  enablement, release publication, and Sachima integration remain separately
  approved work.
- The released v0.1.7 acpx one-shot/persistent-session surfaces are unchanged
  and remain the compatibility baseline; Native code never reads or writes
  their stores and never falls back to acpx.
- Developers verify with `make verify`; the real OpenCode smoke is opt-in via
  `ARS_NATIVE_SMOKE=1` (`tests/native_acp/test_real_opencode_smoke.py`).

## [0.1.7] - 2026-07-16

### Added

- Role-bound native ACPX MCP configuration injection. A role may bind an
  absolute JSON MCP config path; ARS validates and fingerprints that binding,
  then compiles it as the ACPX `--mcp-config` argument for the run/session.

### Changed

- Persistent-session binding now includes the MCP configuration identity, so a
  changed bound config fails closed rather than being silently reused.

### Fixed

- Existing roles with no MCP configuration retain their prior serialized role
  shape and hash, avoiding an unnecessary migration for already persisted
  sessions.

### Notes

- This release does not start MCP services or expand a role's permissions. The
  bound configuration is a local, role-owned input and remains subject to the
  existing permission and workspace policy.

## [0.1.6] - 2026-07-09

### Added

- Read-only local session inspection API for caller hot paths:
  `inspect_session(...)` summarizes a persisted session record, lease/liveness
  state, latest turn, progress snapshot, and artifact paths without launching an
  agent, mutating session state, or shelling out.
- `list_turns(...)` returns ordered persisted turn summaries for a session,
  including status, timestamps, result paths, observed-effect metadata, and
  redacted final-message excerpts.
- Regression coverage for corrupt/missing artifacts, turn ordering, raw-content
  boundaries, lease/liveness classification, and no-subprocess execution.

### Changed

- README, README.zh-CN, technical solution, and roadmap docs now describe the
  inspection API as the supported library seam for products such as Sachima that
  need safe local progress/status reads instead of controller CLI glue.

### Fixed

- Release package metadata now advertises `0.1.6`, keeping `pyproject.toml`,
  `src/agent_run_supervisor/__init__.py`, and `uv.lock` in sync before tag
  publication.

### Notes

- This is an additive library/API release. It does not change the acpx transport
  contract, does not add runtime dependencies, and does not start or control
  external agents from the inspection path.

## [0.1.5] - 2026-07-09

### Fixed

- `session send --goal-file` now compiles the goal through
  `goal.compile_goal_prompt` instead of composing a literal `/goal <text>` slash
  turn. On the codex ACP surface (`acpx@0.12.0`) the literal slash turn was
  answered with `Unknown command "/goal"` — a transport-completed no-op that
  still reported `status=completed`. Non-native adapters (all of them today) now
  receive the `goal-contract/v1` text template (`prompt_kind: "text"`); a literal
  slash turn would only be sent for adapters explicitly registered in
  `NATIVE_GOAL_ADAPTERS` (fixture-gated, currently empty). Goal-text validation
  semantics are unchanged (empty/nested-slash/control characters still fail
  closed before any lease/acpx work).

### Notes

- Follow-up (not addressed here): classifying an `Unknown command "<x>"` agent
  reply as non-success requires the `available_commands` capture +
  `UNSUPPORTED_SLASH_COMMAND` slice already registered as deferred in
  `docs/plans/active/2026-07-08-permissioned-session-goal-noop.md`; text-matching
  the reply would be brittle across adapters.

## [0.1.4] - 2026-07-08

### Added

- Fail-closed `no_op` supervisor status: exit `0` with a protocol-clean stream but no
  agent output and no tool activity (`parser.has_observed_effect`) is no longer reported
  as `completed` (`error_code: NO_OP`, `retryable: false`). Applies to both exec runs and
  persistent-session prompt turns.
- `goal.py`: validated goal-turn composition (`compose_goal_prompt` → `/goal <text>`,
  `is_slash_prompt`); session turn results carry the additive `prompt_kind` key
  (`slash_command` | `text`).
- CLI: `session send --goal-file <file>` composes and sends a validated `/goal` slash
  prompt turn (mutually exclusive with `--prompt-file`).
- Additive `observed_effect` result key (`true`/`false`/`null`): callers can verify a
  `completed` run/turn actually produced output or tool activity (schema §1).
- Goal-contract compilation (`goal.compile_goal_prompt`): adapters without a native
  ACP `goal` command (all of them today — `NATIVE_GOAL_ADAPTERS` starts empty) get the
  versioned `goal-contract/v1` plain-text template with a deterministic trailing
  `GOAL_STATUS:` anchor for caller judge loops.
- Session turns now persist `generated-policy.json` (audit symmetry with exec runs)
  and report the additive `prompt_permission_mode` result key (`policy` | `deny_all`).
- 0.1.3 hash-stability goldens: `role_hash`/`policy_hash` are pinned byte-identical to
  the released 0.1.3 distribution, guarding the zero-migration session-binding
  invariant.
- `hermes_caller.derive_verdict` fails closed on a blank `final_message`: a completed
  run that produced no findings text is `BLOCK`, never `PASS`.

### Changed

- Persistent-session prompt turns no longer hardcode `--deny-all`: roles granting
  permission kinds compile the same role-derived `--permission-policy` JSON as the exec
  path; roles granting no kinds keep the fixture-proven `--deny-all` fail-closed shape.

### Notes

- A live acpx fixture capture for the permissioned `prompt -s` shape is an operator
  follow-up before the next release.

## [0.1.3] - 2026-07-07

### Added

- `tools/bump_version.py` and `make bump VERSION=X.Y.Z` to sync `pyproject.toml`,
  `__init__.py`, `uv.lock`, and a CHANGELOG stub in one step.
- `tools/check_version_sync.py` verify gate for three-way version consistency.
- `release.yml` guard: git tag must match `pyproject.toml` version before PyPI publish.

### Changed

- README EN/ZH and AGENTS publish instructions updated for the new bump workflow.
- `uv.lock` workspace package version synced with `pyproject.toml`.

### Notes

- Release engineering only; no supervisor runtime behavior changes.
## [0.1.2] - 2026-07-07

### Added

- GitHub Release asset upload with `SHA256SUMS` for wheels and sdists (`release.yml`).
- `invoke_caller` modes `session_abort` and `session_list` (delegates to `SessionRuntime`).
- `fixtures/README.md` documenting `acpx-0.10.0` legacy vs `acpx-0.12.0` canonical fixtures.

### Changed

- Test coverage raised: `preflight.py` 98%, `role.py` 99%, `parser.py` 95%, `live_stream.py` 97%;
  package total **93%**.
- README EN/ZH library usage updated for new caller modes and checksum verification steps.

### Notes

- No acpx runtime contract changes; test, CI, library API, and release-provenance improvements.

## [0.1.1] - 2026-07-07

### Added

- CI verify and Codecov coverage jobs on Python 3.11, 3.12, 3.13, and 3.14.
- Codecov integration with branch coverage upload and README badges.
- README Library usage and live progress polling sections (English and Chinese).
- PyPI classifiers for Python 3.12, 3.13, and 3.14.

### Changed

- GitHub Actions upgraded to `actions/checkout@v6`, `actions/setup-python@v6`, and
  `codecov/codecov-action@v6`; `setup-uv` pinned to immutable commit SHA.
- Roadmap and features synced with CI matrix, Codecov, and live streaming PR1/PR2 closure.
- `runner.py` test coverage raised to 88% with subprocess edge-case tests.

### Notes

- No runtime behavior changes in this release; documentation, CI, and test improvements only.

## [0.1.0] - 2026-07-06

### Added

- Local-first Python library and CLI for supervising ACP/acpx external AGENT runs with redacted, auditable artifacts.
- One-shot `acpx exec` supervision with role-bound authorization, outer watchdog, and kill metadata.
- Local persistent-session lifecycle: create, send, status, close, abort, and list.
- Read-only `doctor` probe set, confined artifact retention/cleanup, and process-liveness crash recovery.
- Generic local caller boundary (`caller.py`) and local/offline Hermes caller with offline Feishu view-model adapter.
- acpx `0.12.0` contract fixtures, validator, and deterministic replay.
- `scripts/verify_local.sh` local gate entry and GitHub Actions Trusted Publishing release workflow.

[0.1.6]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.6
[0.1.5]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.5
[0.1.4]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.4
[0.1.3]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.3
[0.1.2]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.2
[0.1.1]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.1
[0.1.0]: https://github.com/jovijovi/agent-run-supervisor/releases/tag/v0.1.0

---
title: "Runtime Binding refactor — contract/Binding split and sealed runtime provenance"
status: active
created_at: 2026-07-26
last_validated_at: 2026-07-26
---
# Runtime Binding refactor — contract/Binding split and sealed runtime provenance

## Goal

Separate the three runtime-authority layers that `main` currently collapses into one code constant:

1. **code-closed** `AgentProfile`/`AdapterContract` — adapter compatibility semantics;
2. **operator-owned Runtime Binding** — deployment facts only;
3. **per-Run sealed** `ResolvedLaunchSpec` and runtime provenance.

The production shape is unchanged:

```text
trusted caller → arsd UDS → Native ACP → registered profile → external ACP AGENT
```

Nothing about ingress, wire protocol, result grammar, or supervision authority moves. What moves is
*who owns which fact*: a deployment-specific external CLI path, version, and digest stop being source
constants, and an operator can re-point a runtime through a validated, provenance-bearing Binding
generation without a source change — while ARS keeps proving, per Run, exactly what it launched.

## Current baseline

- Stage 0/1 Native ACP and Stage 2 `arsd` are implemented and closed on `main`; three closed profiles
  are registered with operator-held local acceptance.
- `native_acp/profile.py` mixes the adapter compatibility contract with deployment-specific downstream
  CLI paths, versions, and digests inside `ExpectedRuntimeIdentity` and `fixed_env`.
- There is no Binding layer, no `session_compatibility_epoch`, and no `runtime-binding` command surface.
- The OpenCode profile ID and version string on `main` have drifted from the executable the operator
  reports as installed (1.18.5). The drift is a recorded deployment observation, not a proven fact:
  proving it is a prerequisite of this plan, not an assumption of it.
- Board `active_plan` pointed at `none` before PR-A; PR-A points it at this file.

## Non-goals and non-approvals

Non-goals: operator-declared launch semantics of any kind (command, argv, env key, adapter, launch kind,
capability, permission, selector); caller-selected runtime, path, version, digest, or generation; a
forced or unvalidated promotion path; any ARS-internal privilege escalation; a generalized plugin or
runtime-discovery system; OS sandbox or hostile-process containment claims.

Non-approvals carried unchanged from [`../../roadmap/non-approvals.md`](../../roadmap/non-approvals.md):
no publication, release, tag, or version bump; no deployment, artifact installation, service
install/enable/restart, or production config write; no Sachima/Gateway integration; no public ingress;
no real-provider acceptance; no Git/GitHub side effect without separate operator authorization. Landing
either PR gate below approves none of them.

## Exact closed contracts

**C1 — `AdapterContract` (source-frozen).** Stable profile ID, revision, `adapter_contract_hash`;
`launch_kind` ∈ {`wrapped_acp`, `direct_acp`}; the accepted Binding schema and slot projection; fixed
executable/argv construction and code-known env keys only; ACP protocol version and agent name plus
required **and forbidden** capabilities; permission/config/model/effort/session semantics; the wrapped
adapter and interpreter artifact identity; and a code-owned safe version-probe rule (fixed non-prompt
argv suffix, hermetic environment, bounded output and timeout, code-owned parser).

**C2 — Binding never declares.** No command, argv, env key, adapter, launch kind, capability,
permission, or selector. A Binding value may only fill a slot the contract already declared.

**C3 — Slot acceptance identity.** Every slot binds to the exact profile ID, revision, and
`adapter_contract_hash` that accepted it. After a contract revision, stale generations fail closed at
`validate` and at admission. A Binding is never reinterpreted by a new source contract.

**C4 — Binding contents.** External CLI artifact descriptor (immutable versioned path, actual version,
digest); optional values for Profile-declared config-root slots; a positive
`session_compatibility_epoch`; an acceptance receipt reference/hash recorded as provenance, never as
self-authorization.

**C5 — Artifact identity covers the complete executable code closure.** Standalone native binary:
regular-file SHA-256 plus the interpreter/dynamic-loader policy where one applies. Package or launcher
CLI: immutable package root/tree or canonical manifest digest, launcher identity, and required
interpreter/runtime identity — a launcher-file hash alone never freezes the sibling code it loads.
Artifact and every path ancestor are operator- or root-owned and non-writable by the `arsd`/AGENT UID.

**C6 — Probe-backed promotion.** `validate`/`promote` obtain the real external CLI version through the
Profile's code-owned probe and compare it with the Binding; a manifest's version string alone is not
proof. Admission revalidates contract match and artifact digest against the trusted immutable paths and
never accepts caller selection.

**C7 — Layout.** A regular, atomically replaced `active.json` plus `generations/<id>/manifest.json`;
no active symlink. Validation: strict canonical JSON, finite size, `O_NOFOLLOW`/dirfd walks, verified
ownership/modes/ancestors, and refusal of traversal, symlink, FIFO, device, unknown fields, unknown
slots.

**C8 — Read-once.** Admission reads `active.json` and the selected generation exactly once per Run,
resolves the complete launch/runtime identity, writes write-once `launch.json`, and seals
`launch_spec_hash`. Spawn, finalization, and reconciliation never reread the active Binding.

**C9 — Launch kinds.** `wrapped_acp` (Codex, Claude): source freezes Node/interpreter plus the ACP
adapter; the Binding freezes the downstream CLI and config-root values. `direct_acp` (OpenCode): one
executable is both the AGENT CLI and the ACP implementation — source freezes direct launch/protocol/
capability semantics, the Binding freezes that executable's identity. OpenCode is never modelled as two
artifacts.

**C10 — TOCTOU.** A `direct_acp` executable is pinned by descriptor and exec'd from that descriptor with
rechecks on both sides of the spawn window. A wrapped downstream CLI, which the adapter reopens later,
must remain under an immutable operator-owned path and package closure.

**C11 — Session epoch.** `SessionRecord` persists `session_compatibility_epoch`. Reuse requires equal
profile ID/revision/`adapter_contract_hash`, equal workspace/owner/namespace, and equal epoch. Missing
or different epoch is rejected before lease mutation and before `session/load`, with no `session/new`
fallback. An epoch may be retained across a Binding change only after an approved continuity canary;
otherwise it is bumped.

**C12 — Unchanged surfaces.** `AgentRunRequest` and `AgentRunSpec` field sets, the `arsd` v1 public
wire, the result/event grammar, reconcile semantics, and the `ManagedProcess` public API. `AgentRunSpec`
continues to seal launch through `launch_spec_hash`. Old Runs stay readable; old Native Sessions stay
status/list/close-readable while `load` fails closed.

**C13 — Provenance inspector.** Recomputes the launch hash after excluding **only** the top-level
`launch_spec_hash`, and reports profile/contract, adapter/protocol, Binding generation/set/slot hashes,
the complete CLI artifact identity/version/digest, and the epoch.

**C14 — Operator surface.** `runtime-binding validate|promote|rollback|inspect-run`. No `--force`, no
internal `sudo`. Pure Binding promotion does not restart `arsd`; changing the Binding root, unit, or
runtime does and stays separately approved.

**C15 — OpenCode registration.** Stable ID `opencode-native-acp` at revision 3 — one bump from the
retired `opencode-1.18.4` revision 2 — with **no** compatibility alias: the old ID becomes an unknown
profile and is refused at admission. `agentInfo.version` is never asserted equal to the CLI `--version`.

## Delivery — two PR gates

| Gate | Content | Author | Review |
|---|---|---|---|
| **PR-A** (this) | authority docs (`GOAL.md`, PRD, architecture, technical solution, features, board, non-approvals) + this active plan + generated docs outputs | Documentation Engineer | Cursor may review this documentation only |
| **PR-B** | one coherent vertical source/test/docs implementation; WP2–WP5 land as internal work packages/commits | Claude Code Lead Developer | independent reviewer: Codex CLI `gpt-5.6-sol`/`xhigh`, fresh read-only context |

Hermes controls scope, deterministic verification, evidence arbitration, and every Git/GitHub/runtime
side effect. **No separate unused foundation PR is landed** — a Binding reader with no admission caller
would be dead code with an unproven contract.

## Work packages inside PR-B

### WP2 — contract freeze and Binding reader

- Extract `AdapterContract` from the profile constants; add `adapter_contract_hash`, `launch_kind`, the
  accepted Binding schema/slot projection, forbidden capabilities, and the version-probe rule.
- New `native_acp/runtime_binding.py`: the only module that opens a Binding root. Loading, validation
  (C7), acceptance matching (C3), slot projection, generation/set/slot hashing, typed refusals.
- Move deployment-specific downstream CLI path/version/digest and config-root values out of source for
  all three profiles; bump each profile revision because its frozen contract changes shape.
- Re-register OpenCode per C15, gated by the discovery prerequisite below.

### WP3 — read-once admission, sealing, and attestation

- `arsd/admission.py`: exactly one `active.json` read plus one generation read per Run; revalidate
  contract match and artifact digest; reject any caller-supplied runtime selection.
- `spec.py`: carry the resolved runtime provenance in `ResolvedLaunchSpec`; embed `launch_spec_hash` in
  `launch.json` and exclude exactly that one top-level field from the hash; keep `AgentRunSpec` fields
  unchanged.
- `attestation.py`: attest the *sealed* identity rather than a profile constant; extend artifact
  identity to package/tree closures; add ownership/ancestor checks and the C10 recheck for both launch
  kinds, reusing the existing deterministic race seam.

### WP4 — session compatibility epoch

- `session.py`: additive, omit-when-unset `session_compatibility_epoch`.
- Reuse gate per C11, evaluated before lease mutation and before `session/load`; no `session/new`
  fallback anywhere on the reuse path.
- Pre-epoch records remain status/list/close-readable.

### WP5 — operator surface and provenance inspector

- `cli.py`/`commands.py`: the four `runtime-binding` subcommands, no `--force`, no escalation, no
  restart, no artifact installation.
- `validate`/`promote` run the code-owned probe (C6); `rollback` re-promotes a previously validated
  generation; `inspect-run` implements C13 and degrades gracefully on pre-PR-B `launch.json`.
- Docs sync: board, features row, this plan's checklist, regenerated index and drift report.

## Exact likely files

| Path | Change |
|---|---|
| `src/agent_run_supervisor/native_acp/runtime_binding.py` | new module (WP2) |
| `src/agent_run_supervisor/native_acp/profile.py` | `AdapterContract`, launch kinds, probe rule, revisions, OpenCode re-registration |
| `src/agent_run_supervisor/native_acp/spec.py` | launch provenance, embedded seal, hash exclusion |
| `src/agent_run_supervisor/native_acp/attestation.py` | sealed-identity attestation, package closure, ownership/ancestor, TOCTOU |
| `src/agent_run_supervisor/native_acp/run_task.py` | write-once launch record, refusal translation, no Binding read |
| `src/agent_run_supervisor/native_acp/storage.py` | read-only Binding path helpers if needed |
| `src/agent_run_supervisor/session.py` | epoch field and reuse comparison |
| `src/agent_run_supervisor/arsd/admission.py` | the single per-Run Binding read and revalidation |
| `src/agent_run_supervisor/arsd/handlers.py` | refusal mapping only; wire unchanged |
| `src/agent_run_supervisor/cli.py`, `commands.py` | `runtime-binding` subcommand group |
| `tests/native_acp/test_runtime_binding.py` | new suite (loader, validation, security matrix) |
| `tests/native_acp/test_profile.py`, `test_spec.py`, `test_attestation.py`, `test_run_task.py` | contract, seal, attestation additions |
| `tests/native_acp/test_native_session_record.py`, `test_session_switching.py` | epoch persistence and reuse |
| `tests/native_acp/fake_agent.py` | probe/initialize fixtures |
| `tests/arsd/test_admission.py`, `test_handlers_registry.py`, `test_reconcile.py` | read-once, refusals, legacy readability |
| `tests/test_cli_commands.py`, `tests/test_cli_smoke.py` | command surface |
| `docs/roadmap/features.md`, `docs/roadmap/current-status.md`, this plan, `docs/INDEX.md`, `docs/lessons/_drift_report.md` | docs sync |

No `pyproject.toml`, lockfile, dependency, CI/workflow, service-unit, or caller-mapping change is in
scope; the console script already exists.

## TDD RED→GREEN gates

Each work package lands RED first — failing tests that encode the contract — then GREEN.

| WP | RED (must fail before implementation) | GREEN |
|---|---|---|
| WP2 | Binding refusal matrix (schema, canonical JSON, size bound, unknown field/slot, missing descriptor field, missing/mismatched machine `contract_identity`, non-positive epoch, launcher-only `package_tree`); contract-hash stability and change detection; structural assertion that no registered contract contains a deployment path, version, or digest | new suite + `test_profile.py` + full pytest |
| WP3 | read-once counters (one `active.json` + one generation read per Run; zero during spawn/finalize/reconcile); digest revalidation at admission; stale-generation refusal; `AgentRunSpec` field-set golden unchanged; hash excludes exactly one top-level field; attestation refusals for both launch kinds | `test_admission.py`, `test_spec.py`, `test_attestation.py`, `test_run_task.py` + full pytest |
| WP4 | epoch persisted and omit-when-unset; legacy record byte-stability; reuse rejected on missing/different epoch before lease mutation and before `session/load`; no `session/new` frame on any reuse path; pre-epoch records still status/list/close | session suites + `test_handlers_registry.py` + full pytest |
| WP5 | parser table for the four subcommands; `--force` absent; no escalation in the command path; `promote` refused on probe-versus-manifest version mismatch; `rollback` refused for a never-validated generation; `inspect-run` detects a single mutated field and handles legacy records | CLI suites + full pytest + `./scripts/verify_local.sh` |

## Compatibility matrix

| Surface | Before | After PR-B | Rule |
|---|---|---|---|
| `AgentRunRequest` fields | current set | unchanged | no caller-facing runtime selection is added |
| `AgentRunSpec` fields | current set | unchanged | launch still sealed by `launch_spec_hash` |
| `arsd` v1 public wire | current | unchanged | `api_version` unchanged; new refusals reuse existing error shapes |
| result / event grammar | current | unchanged | no new terminal state or event family |
| reconcile semantics | current | unchanged | and it gains no Binding read path |
| `ManagedProcess` public API | current | unchanged | descriptor-exec seam already exists |
| `launch.json` | profile-derived expected runtime | + runtime provenance, + embedded seal | additive; old files readable, reported as legacy |
| Native Session records | no epoch | epoch persisted | old records: status/list/close OK, `load` fails closed |
| Profile IDs | `opencode-1.18.4` | `opencode-native-acp` r3 | no alias; old ID is unknown and refused at admission |
| Runs on disk | current artifacts | unchanged plus additive fields | old Runs stay readable |

## Artifact, ownership, TOCTOU, and security tests

All hermetic, over synthetic Binding roots and fake artifacts — no real CLI, credential, provider, or
daemon:

1. Artifact or ancestor owned outside the trusted operator/root set → refuse.
2. Artifact or ancestor writable by the `arsd`/AGENT UID (group or other write, or same-UID ownership)
   → refuse.
3. Symlinked `active.json`, symlinked manifest, symlinked ancestor, `..` traversal, absolute-path
   escape → refuse.
4. FIFO, device, socket, directory where a regular file is required → refuse.
5. `package_tree` slot with a launcher digest but no tree digest or interpreter identity → refuse
   (encodes C5 directly).
6. Digest mismatch at admission → refuse before spawn, with `spec.json` and the expected identity left
   on disk.
7. Digest swapped *inside* the deterministic race seam → refused by the spawn-boundary recheck with a
   named row; `direct_acp` still exec's the pinned descriptor.
8. Wrapped downstream CLI whose package root becomes writable → refuse.
9. Contract revision bump → every prior generation fails closed at `validate` and at admission.
10. Manifest whose machine `contract_identity` names a different profile, revision, or contract hash →
    refuse. Changing provenance-only `accepted_by`/`accepted_at`/receipt metadata never makes an
    otherwise invalid generation admissible and is not itself a profile-identity check.
11. Probe version ≠ manifest version → `validate`/`promote` refuse; manifest text alone never passes.
12. Submit carrying any runtime/path/version/digest/generation field → refused by request validation.
13. No `--force` in the parser; no `sudo`/privileged invocation in the command path (static assertion).
14. Read-once instrumentation: exactly one `active.json` read and one generation read per Run; zero
    reads during spawn, finalization, and reconciliation.
15. Epoch: missing, lower, and higher epochs all rejected before lease mutation and before
    `session/load`; no `session/new` frame is emitted on a reuse path.
16. `inspect-run`: mutating any field other than `launch_spec_hash` changes the recomputed hash;
    excluding a second field is not possible by construction. A pre-PR-B record is reported as legacy
    and still graded against `spec.json`: a mutated or unverifiable legacy record exits nonzero.
17. Binding-root ancestors: a symlinked or group/other-writable ancestor of the configured root is
    refused for the pointer read, the generation read, and the operator's pointer write.
18. Executable closure: a `package_tree` launcher outside its hashed `package_root` → refuse; a
    launcher whose real shebang/`PT_INTERP` runtime is not the declared interpreter → refuse.
19. Implicit interpreters: a `native_binary` that is a script or a dynamically loaded ELF while
    declaring `interpreter: null` → refuse; `#!/usr/bin/env x` → refuse; only an image needing no
    external interpreter may declare none.
20. Wrapped source artifacts: a writable/service-owned frozen Node or adapter entry → refuse, and the
    adapter entry's trust *and bytes* are re-proven after the deterministic race seam, where an
    in-place rewrite leaves the inode unchanged.
21. Promotion identity: a generation replaced while it is being probed → refuse; `active.json` names
    exactly the object the probe proved, and `rollback` obeys the same rule.
22. Probe bound: a child flooding stdout or stderr is drained without deadlock and never buffered past
    the contract's bound; a child exceeding the timeout is killed and reaped, not leaked.
23. Provenance shape: missing, partial, wrong-typed, or malformed-digest acceptance receipts → refuse.
    Requiring a well-formed record and refusing to let it authorize anything are two separate rules,
    and both hold: a flawless receipt still never rescues a mismatched contract identity.

These are fail-closed admission controls and identity proofs. They are not an OS sandbox and make no
hostile-process containment claim.

## OpenCode discovery prerequisite

WP2 may define the contract shape at any time, but it **must not** freeze OpenCode's ACP identity,
capability, or selector constants — and WP5 must not register `opencode-native-acp` as admissible —
until an operator-run discovery produces, from the installed executable:

1. a real **non-prompt ACP `initialize`** exchange recording `agentInfo` name and version, protocol
   version, and advertised capabilities including `loadSession`;
2. the advertised config selector shape and domains for model and effort, including the model-dependent
   effort set (the current registry pins a model pair chosen for exactly that reason);
3. separately, the code-owned `--version` probe output and the artifact digest for the same executable.

`agentInfo.version` and the CLI `--version` are recorded as two independent facts; neither may be
derived from or asserted equal to the other. Until this evidence exists, the OpenCode ID/version drift
stays a recorded observation, and any constant that would encode it is blocked. Discovery is an operator
action outside PR-A.

## Verification commands

Run by the implementing author during PR-B, then independently by Hermes:

```bash
uv sync --locked --extra dev --extra release --extra native
uv run --locked python -m pytest -q tests/native_acp/test_runtime_binding.py \
  tests/native_acp/test_profile.py tests/native_acp/test_spec.py \
  tests/native_acp/test_attestation.py tests/native_acp/test_run_task.py
uv run --locked python -m pytest -q tests/native_acp/test_native_session_record.py \
  tests/native_acp/test_session_switching.py tests/arsd/test_admission.py \
  tests/arsd/test_handlers_registry.py tests/arsd/test_reconcile.py \
  tests/test_cli_commands.py
uv run --locked python -m pytest -q
./scripts/verify_local.sh
uv run python tools/build_docs_index.py --check
uv run python tools/docs_drift_signal.py --check
uv run python tools/check_roadmap_governance.py
git diff --check
```

PR-A runs only the docs tooling, governance check, and `git diff --check`; the full verifier is run
independently by Hermes. Opt-in real-runtime suites stay skip-by-default and are not executed by either
gate.

## Risks

- **Same-UID deployment.** C5 requires artifacts and ancestors that the `arsd`/AGENT UID cannot write.
  On a host where the service user also owns the installed CLIs, that property does not hold today, and
  satisfying it is a root-owned installation change — an operator rollout action outside both PR gates.
  PR-B is unaffected: its tests build synthetic roots. The consequence is stated plainly rather than
  assumed away: until the artifact root is prepared, a real promotion would refuse.
- **Wrapped closure is weaker than direct pinning.** ARS cannot fd-pin the CLI the adapter reopens
  later. The honest guarantee is path-and-closure immutability under an operator-owned root, not
  descriptor identity, and the documentation must not overstate it. The same limit bounds the adapter
  entry: its bytes are re-proven through the attestation pin after the race seam, but Node's own open
  of `argv[1]` happens after this gate returns, so what closes that last gap is the trust boundary
  (operator-owned, non-writable by the service UID), not a descriptor.
- **The interpreter/loader is frozen one level deep.** A script's shebang target and a dynamic ELF's
  `PT_INTERP` are read from the image, compared with the Binding, digested, and ownership-checked. That
  interpreter's *own* loader chain is the platform's trusted base and is not walked. The guarantee is
  "the immediate runtime is frozen and trusted", not "the whole OS is".
- **Trusted artifacts must live on an immutable path, interpreters included.** The rule that refuses a
  group-writable ancestor applies to the frozen interpreter exactly as it applies to the CLI. On this
  host that already excludes `/bin/sh` (its `/bin` is a symlink) and a uv-managed Python (its ancestors
  are group-writable). PR-B is unaffected — its fixtures stage private hardened copies and its harness
  attests an interpreter that genuinely passes the rule — but a real promotion needs an
  operator-prepared interpreter path, exactly like the artifact root above.
- **Package digest cost and stability.** Tree digests over a package root are more expensive and more
  fragile than a single file hash. Bound the walk, define canonical ordering, and refuse rather than
  sample.
- **Profile revision churn.** Bumping all three revisions invalidates any Binding generation authored
  earlier — by design (C3), but it means generation authoring follows, never precedes, the contract.
- **Live promotion without restart.** Because admission re-reads the pointer per Run, a promotion takes
  effect on the next Run. That is intended and gated by `validate`, and in-flight Runs are unaffected
  because each is sealed at admission — but it is a live-behavior change and belongs to the operator.

## Rollback

Source rollback is fail-closed, not a return to pre-epoch reuse. Before reverting PR-B, stop new
admissions. Sealed Runs and their launch evidence remain immutable and readable; Binding-era Sessions
remain `status`/`list`/`close`-readable but must not be silently loaded by a runtime that cannot enforce
their epoch and contract identity. They are closed or quarantined, and continuation requires a new
Session or a separately approved rollback procedure that preserves those checks. The source revert
adds no wire, grammar, dependency, lockfile, or service surface and never rewrites terminal facts.

Operational rollback is separate and narrower: `runtime-binding rollback` re-promotes a previously
validated generation and affects only Runs admitted afterwards. It never rewrites a sealed
`launch.json`, never changes a terminal Run fact, and never substitutes for a source revert or for
disabling ingress.

## Production, release, and real-provider boundaries

- PR-A changes documentation authority only. It claims no source implementation, rollout, publication,
  deployment, service restart, or real-provider acceptance.
- PR-B is source, tests, and docs on a task branch. Landing it does not install an artifact, create or
  promote a Binding, restart `arsd`, publish anything, or run a real provider.
- Creating a real Binding root, preparing immutable artifact ownership, running `validate`/`promote`
  against it, the continuity canary that would justify retaining an epoch, real-provider acceptance,
  release/tag/PyPI publication, and Sachima integration are each separate operator decisions, taken
  after PR-B and never implied by it.

## Checklist

- [x] PR-A: authority docs updated (`GOAL.md`, PRD R1/R4/R12/R13, architecture §0/§3.1–§3.3/§4/§8/§9/§10,
      technical solution §0–§10, features, board, non-approvals).
- [x] PR-A: this plan created and linked from board `active_plan`.
- [x] PR-A: `docs/INDEX.md` and `docs/lessons/_drift_report.md` regenerated by repository tooling.
- [x] PR-B WP2: contract freeze and Binding reader (RED → GREEN).
- [x] PR-B WP3: read-once admission, sealing, attestation (RED → GREEN).
- [x] PR-B WP4: session compatibility epoch (RED → GREEN).
- [x] PR-B WP5: operator surface, provenance inspector, OpenCode re-registration (RED → GREEN).
- [x] PR-B: full pytest, canonical verifier, docs tooling, governance check, `git diff --check`.
- [ ] PR-B: independent fresh-context blocker review.
- [ ] On merge: `git mv` this plan to `docs/plans/archive/`, set `status: archived`, update board and
      feature tracker.

## PR-B implementation notes

Recorded because they are decisions a reviewer would otherwise have to re-derive.

- **The discovery prerequisite is met and `opencode-native-acp` r3 is registered.** The operator's
  zero-prompt ACP discovery produced `agentInfo` OpenCode/1.18.5, protocol 1, `loadSession` advertised,
  selectors `model`/`effort`, and — only after the exact model was set to `kimi-for-coding/k3` — the
  effort domain low|high|max. r3 freezes exactly that. `deepseek/deepseek-v4-pro`, registered under the
  retired 1.18.4 evidence, is **not** carried over: this discovery does not prove its model-dependent
  effort domain. No version constant is frozen in the profile at all, so `agentInfo.version` and the CLI
  `--version` cannot be conflated.
- **All three profile revisions bumped** (OpenCode 2→3, Codex 1→2, Claude 1→2) because each frozen
  contract changed shape, which is also what makes `adapter_contract_hash` fail every prior generation
  closed (C3). The prior operator-held local acceptance therefore no longer covers the registered rows.
- **`acp_agent_version` is a contract field only for `wrapped_acp`.** There the ACP-reported version is
  a source artifact fact whose digest the contract already freezes; for `direct_acp` it reports the
  *deployed* executable, so freezing it would re-freeze a deployment fact. The observed value is
  recorded in `initialize_attestation.json` either way.
- **A contract with no Binding slot needs no Binding.** All three registered profiles declare slots and
  are refused fail-closed without one; the hermetic fake-agent profiles declare none, so their launch is
  wholly source-frozen and their evidence surface is unchanged.
- **Package closures are ownership-checked entry by entry**, not just digested. A tree digest freezes
  bytes only until someone who can write them changes them, so the walk that computes the digest also
  proves every entry resists the `arsd`/AGENT UID.
- **Artifacts are `O_NOFOLLOW`-pinned everywhere**, including the wrapped downstream CLI, which the
  pre-PR-B gate deliberately resolved through a symlink. A Binding names an immutable versioned path,
  so a symlink at the artifact path is now a swap, not a configuration.
- **`rollback` re-proves rather than trusts a record.** No state file is added to the operator's Binding
  root, so "previously validated" means the target passes the same validation and probe promotion
  required, and is refused when it is already active.
- **`arsd/__main__.py` is untouched.** No daemon flag carries the Binding root, so a production daemon
  has no configured root and refuses admission fail-closed. Wiring it is an operator rollout step
  outside this gate, exactly like preparing the artifact root.
- **The opt-in Codex socket acceptance harness now requires Binding inputs to opt in at all**
  (`ARS_CODEX_ACCEPTANCE_BINDING_ROOT`, `_TRUSTED_UID`, `_REAL_CREDENTIAL_ROOT`), and its credential
  isolation comes from the promoted generation's `codex_home` slot instead of a derived profile. Two
  negative cases (`n3_retargeted_cli_symlink`, `n5_credential_root_symlink`) moved from the
  `attestation` stage to a new `binding` stage because the single per-Run Binding read refuses them
  strictly earlier. The harness itself was not executed here; only its hermetic contract suite was.

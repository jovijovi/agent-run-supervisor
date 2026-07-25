---
title: "Codex official ACP adapter — closed-profile admission"
status: archived
created_at: 2026-07-25
last_validated_at: 2026-07-25
archived_at: 2026-07-25
---
# Codex official ACP adapter — closed-profile admission

## Context / target

Additive closed-`AgentProfile` admission on the merged Native ACP/arsd line
(PRD R1/R3/R12). It registers the official Codex ACP adapter as the second
code-registered profile and adds the identity/configuration gates that make
launching an operator-installed external runtime provable rather than assumed.

The exclusive production shape is unchanged:

```text
trusted caller → arsd UDS → Native ACP → pinned official adapter → downstream CLI
```

No acpx runtime dependency, fallback, session store, shim, or compatibility
layer is introduced; wire protocol, result grammar, dependency versions,
lockfile, package version, service-unit behavior, caller mappings, and the
Claude/OpenCode paths are untouched.

Every registered value is a byte-copy of the operator-frozen install,
discovery, and runtime-authority manifests (controller-held, outside the
repository). The interpreter is a controller-selected frozen Node copy: the
adapter entrypoint is an ESM script whose `#!/usr/bin/env node` shebang would
otherwise let the kernel resolve the interpreter from ambient `PATH`.

Scope is the Codex slice only. Claude B3/B4/B5 closure and any Claude profile
registration stay out of scope, and the explicit non-approvals in
`docs/roadmap/non-approvals.md` hold — in particular deployment, enablement,
service restart, release/tag/PyPI, and Sachima/Gateway integration.

## Checklist

- [x] Preflight: GOAL, PRD, architecture, technical solution, features,
      current-status, AI_FLOW, non-approvals read; product position stated.
- [x] RED: new `tests/native_acp/test_attestation.py` covering the D11a walk,
      credential-root structure, runtime identity, missing/unreadable
      artifacts, the deterministic in-window race seam, both config-absence
      rechecks, report sanitization, descriptor hygiene, and the public typed
      refusal.
- [x] RED: `test_managed_process.py` descriptor-exec pair; `test_profile.py`
      Codex snapshot golden, `fixed_env` validation rules, OpenCode
      byte-stability, frozen-Node resolution; `test_spec.py` launch argv/
      serialization/credential-ref admission; `test_run_task.py` pre-spawn
      refusals, descriptor wiring, instrumented initialize-attestation
      ordering, spawn-env composition; `tests/arsd/test_admission.py`
      credential-ref refusal over a private socket, injection closure,
      profile-hash drift, and quarantined-session reuse.
- [x] Implement `native_acp/attestation.py`: `ExpectedRuntimeIdentity`,
      sanitized report types, the public `AttestationRefusal`, the pure
      project-config closure predicate, and `attest_spawn_boundary`
      (pin → hash-through-inode → root structure → closure → hook → recheck →
      write-once report).
- [x] Implement the descriptor-bound interpreter spawn seam in
      `managed_process.py` (`interpreter_fd` → `executable=/proc/self/fd/N` +
      `pass_fds`); the `None` path stays byte-identical.
- [x] Implement `profile.py` `fixed_env` + construction-time validation,
      `expected_runtime`, `required_credential_refs`, omit-when-empty/None
      snapshot serialization, the frozen-Node executable mapping, and
      `CODEX_ACP_1_1_7` in `DEFAULT_REGISTRY`.
- [x] Implement `spec.py` exact credential-ref admission gate and launch-spec
      mirroring of `fixed_env`/`expected_runtime` (no hashing at resolve time).
- [x] Implement `run_task.py` wiring: attestation between session bind and
      spawn with the refusal translated at the single call site, the attested
      descriptor threaded into the spawn and closed in a `finally` on success
      and failure, `fixed_env` applied last in `_spawn_env`, and the
      post-initialize identity gate with its write-once artifact.
- [x] Define the opt-in real-credential socket acceptance module
      `tests/arsd/test_codex_socket_acceptance.py` (skip-by-default; positive
      legs against a derived isolated credential home, negative legs against
      private copies, fail-closed `O_NOATIME` inventory, commit-SHA binding).
- [x] Repair-1 (review blockers): refuse a runtime artifact that aliases the
      declared credential file *before* any content hash reads it; guard
      credential staging so every failure path cleans and re-inventories; make
      P2 prove exact continuity, clean current-Turn separation, and new thread
      state; split P4 into cancel-after-dispatch and timeout-after-dispatch
      sublegs bound to the B2-fixed table; declare and drive the complete
      R8 N1–N9 variant matrix.
- [x] Add the unskipped hermetic contract suite
      `tests/arsd/test_codex_acceptance_contract.py`, which drives the opt-in
      module's staging-cleanup guard, continuity/state-delta assertions, P4
      outcome matrix, and negative-case declaration against synthetic fixtures
      only — no daemon, no spawn, no model call, no credential.
- [x] GREEN: focused suites, the four-suite baseline, full pytest, the
      canonical verifier, docs index/drift/governance checks, `git diff --check`.
- [x] Docs: this plan; archive the B1/B2 plan; `F-NATIVE-ADAPTER-CODEX-001`
      capability row; board `active_plan:` pointer; regenerate `docs/INDEX.md`
      and the drift report via repository tooling.

## Design decisions carried into code

- **Frozen non-model launch environment.** `AgentProfile.fixed_env` is a closed,
  ordered, deeply validated tuple injected only at spawn and applied last, so
  profile values win over allowlisted passthrough. Its key names are
  deliberately outside the env allowlist, so no ambient `CODEX_*` value can
  reach the child. `CODEX_CONFIG` must be a JSON object that byte-equals its
  canonical re-serialization; `INITIAL_AGENT_MODE` is a closed domain; and a
  profile that freezes an expected runtime must also freeze `CODEX_HOME` and
  `CODEX_PATH`, because a missing `CODEX_PATH` would silently switch the
  adapter to a PATH-resolved or bundled fallback CLI — a downstream identity
  change with no hash change.
- **Landlock is load-bearing, version-bound debt.** The canonical
  `CODEX_CONFIG` pins `use_legacy_landlock` because the adapter's default bwrap
  sandbox was disqualified at discovery: the command reached the Codex sandbox
  without ACP permission mediation and failed with a bwrap loopback
  `RTM_NEWADDR` EPERM on this host. Any adapter, downstream CLI, Node, or
  `CODEX_CONFIG` change requires a full new install → discovery →
  permission-canary cycle, a profile revision bump, and review. There is no
  silent fallback, and any bwrap signature in captured acceptance
  stderr/events is a FAIL.
- **Spawn-boundary attestation is detect-and-refuse, not a cage.** Artifacts are
  pinned as `O_PATH` descriptors and hashed by reopening those descriptors
  through `/proc/self/fd`, so identity binds to inodes rather than pathnames.
  A deterministic post-check hook (product no-op) sits between the
  evidence-producing checks and a recheck that re-evaluates every predicate the
  spawn depends on — five inode bindings **and both** configuration-absence
  predicates — so a swap or a config-layer insertion inside the window is
  refused with a named durable row. `attestation.json` is written write-once
  before any refusal, on PASS and on FAIL.
- **The interpreter has no residual swap window.** On PASS the Node pin survives
  as `interpreter_fd`; the child execs that descriptor directly, so even a
  post-recheck path retarget runs the attested inode. `argv[0]` keeps the
  canonical path string. The child inherits one `O_PATH` descriptor of its own
  image — an extra handle, not extra authority, since the frozen copy is
  world-readable mode 0555 and any process can already read its own image.
- **Workspace configuration closure.** Codex loads layered configuration by
  cwd, so the inclusive ancestor chain of the effective cwd up to the
  filesystem root must be free of `.codex/config.toml`. One pure predicate is
  invoked twice per attestation, so the two passes cannot diverge. Operational
  consequence: Codex workspaces must live under a clean ancestor chain.
- **Credential binding.** `required_credential_refs` makes the caller's
  references match the profile's exactly — missing, wrong, extra, or duplicated
  are refused at admission, before workspace bind, before any credential-root
  access, and before spawn. The credential root is checked structurally only
  (non-symlink directory, owner, mode `0700`, `auth.json` non-symlink regular
  file mode `0600`, no `config.toml`); its bytes are never read and no digest of
  a credential-bearing file is ever computed or persisted.
- **No hashed artifact may alias the credential file.** The configured CLI path
  is a symlink by design, so its pin follows to the final target; a same-UID
  retarget onto `CODEX_HOME/auth.json` (or a hardlink at the Node/entry path)
  would otherwise make the identity hash read credential bytes and persist a
  credential-derived digest in the FAIL report. The declared root and
  `auth.json` are therefore identified structurally — `stat`/`lstat` facts
  only, following and not following the link — *before* step 2, and any pinned
  artifact whose inode matches is refused with its own
  `<artifact>_credential_alias` row (class `CREDENTIAL_ROOT_VIOLATION`). The
  comparison is on inodes, never on pathnames, so a retarget, a hardlink, or a
  symlinked root are all caught; the hashing function re-asserts the same
  invariant immediately before its reopen, so no reordering can reach a read.
- **Post-initialize identity gate.** Before `session/new`, `session/load`, or any
  prompt — on first *and* reused Runs — observed `agent_info`, protocol
  version, and the advertised `loadSession` capability are compared against the
  frozen expectation, and `initialize_attestation.json` is persisted write-once
  before any refusal. Mismatches are zero-Turn pre-dispatch failures.
- **Legacy rows are byte-stable.** All additive fields serialize
  omit-when-empty/None, so the OpenCode snapshot, profile hash, and launch hash
  are unchanged; OpenCode Runs make no attestation call and write no
  attestation artifacts.

## Acceptance

1. A Codex-shaped Run whose workspace ancestor chain carries a
   `.codex/config.toml`, whose adapter entry, Node, or CLI content drifts, or
   whose credential-root structure is wrong, is refused before any spawn with
   the mapped refusal code surfaced as the Run's `detail_code`, and leaves
   `spec.json`, `launch.json` (expected identity), and `attestation.json`
   (observed) on disk.
2. A tamper or configuration-layer insertion performed inside the deterministic
   race window is refused by the recheck with its named FAIL row, no spawn, a
   terminal `result.json`, and a released session lease.
3. A successful Codex-shaped Run passes the attested interpreter descriptor to
   the real spawn boundary, the recorded inode matches the durable binding, and
   no supervisor descriptor references that inode after the Run returns —
   including on the spawn-failure path.
4. `initialize_attestation.json` is written strictly before `session/new` and
   `session/load`, all-PASS on clean Runs; identity, protocol, and capability
   mismatches fail pre-dispatch with the artifact retained through
   finalization.
5. The spawn environment is exactly allowlist ∩ environ, then permission env,
   then `fixed_env`; ambient `CODEX_*` values never pass through.
6. OpenCode snapshot, profile hash, launch hash, and evidence surface are
   byte-identical to the pre-change baseline.
7. A Run whose Node, adapter entry, or CLI path resolves to the declared
   credential file — by symlink retarget, by hardlink, or through a symlinked
   root — is refused at its `<artifact>_credential_alias` row before any
   content hash runs, and neither the credential bytes nor their SHA-256
   appear in the persisted report or the refusal text.
8. The opt-in acceptance harness is executable-by-construction rather than
   self-satisfying: its staging guard cleans and re-inventories on every
   failure path, P2 requires exact nonce recall plus a measured current-Turn
   message path and a real isolated-home state delta, P4 drives both the
   cancel and the post-dispatch-timeout subleg against the B2-fixed table, and
   every R8 N1–N9 variant is declared and driven. The unskipped hermetic
   contract suite fails if any of those conditions is weakened, and the module
   itself stays skip-by-default.
9. Full pytest and the canonical verifier pass; no dependency, lockfile,
   version, service, protocol, or result-grammar changes.

## Files likely to change

- `src/agent_run_supervisor/native_acp/attestation.py` — new module.
- `src/agent_run_supervisor/managed_process.py` — descriptor-bound exec seam.
- `src/agent_run_supervisor/native_acp/profile.py` — frozen env, expected
  runtime, credential-ref binding, Codex registration.
- `src/agent_run_supervisor/native_acp/spec.py` — admission gate and launch
  mirroring.
- `src/agent_run_supervisor/native_acp/run_task.py` — attestation call site,
  descriptor threading, spawn env, initialize gate.
- `tests/native_acp/test_attestation.py` — new suite.
- `tests/native_acp/{test_managed_process,test_profile,test_spec,test_run_task}.py`,
  `tests/native_acp/fake_agent.py` — additions.
- `tests/arsd/test_admission.py`, `tests/arsd/test_codex_socket_acceptance.py`,
  `tests/arsd/test_codex_acceptance_contract.py` — new unskipped hermetic
  contract suite for the opt-in harness.
- `docs/roadmap/features.md`, `docs/roadmap/current-status.md`, this plan,
  `docs/INDEX.md`, `docs/lessons/_drift_report.md`.

## Verification gates

```bash
uv sync --locked --extra dev --extra release --extra native
uv run --locked python -m pytest -q tests/native_acp/test_attestation.py \
  tests/native_acp/test_managed_process.py tests/native_acp/test_profile.py \
  tests/native_acp/test_spec.py tests/native_acp/test_run_task.py \
  tests/arsd/test_admission.py tests/arsd/test_codex_acceptance_contract.py
uv run --locked python -m pytest -q tests/native_acp/test_run_task.py \
  tests/native_acp/test_finalization_table.py \
  tests/native_acp/test_session_switching.py tests/arsd/test_reconcile.py
uv run --locked python -m pytest -q
./scripts/verify_local.sh
uv run --locked python tools/build_docs_index.py --check
uv run --locked python tools/docs_drift_signal.py --check
uv run --locked python tools/check_roadmap_governance.py
git diff --check
```

Real-credential acceptance is a separate, separately authorized controller
gate. It runs the opt-in module against a clean checkout of the reviewed
implementation commit and is never executed during implementation.

## Risks

- **Host-specific absolute paths.** The registered runtime identity pins this
  host's installed adapter, CLI, frozen Node, and credential root. These are
  admission controls, not portability claims: on any other host the profile
  refuses fail-closed at the spawn boundary rather than launching something
  unverified. Mitigation is explicit — a new install/discovery/freeze cycle and
  a profile revision.
- **Descriptor exec support.** `executable=/proc/self/fd/N` plus `pass_fds`
  through `asyncio.create_subprocess_exec` is Linux/CPython-specific, and
  `O_PATH` is Linux-only. Its absence is itself a FAIL row: the gate never
  degrades to pathname trust. Both the wired behavior and the closure of the
  descriptor on success and failure are pinned by tests.
- **Hashing cost per spawn.** Every identity-pinned Run streams three artifact
  digests before spawning. This is bounded, sequential, pre-dispatch work on
  operator-installed files; it trades a small constant startup cost for
  detect-and-refuse identity proof and is deliberately not cached, because a
  cache would reintroduce the drift window the gate exists to close.
- **Cooperative runtime, honestly scoped.** The launched adapter and CLI are
  ordinary same-UID host processes that ARS deliberately starts and does not
  OS-sandbox; permission mediation is cooperative agent policy, not hostile
  process containment. The runtime writes under its `CODEX_HOME` by design, so
  positive acceptance uses an isolated home. The guarantee is that any drift in
  an attested artifact or configuration surface — whatever its origin — is
  refused at the next spawn boundary.

## Rollback

Revert the branch commits (or discard the uncommitted worktree changes). No
storage schema, wire protocol, result grammar, dependency, or deployment
surface is touched, and the legacy profile's hashes and evidence surface are
byte-identical, so rollback is a pure source revert. Durable artifacts written
by attested builds remain valid; `attestation.json` and
`initialize_attestation.json` are additive per-Run files that no reader
requires.

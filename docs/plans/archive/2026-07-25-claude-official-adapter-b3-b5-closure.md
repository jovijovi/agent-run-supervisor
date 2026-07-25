---
title: "Claude official ACP adapter — B3/B4/B5 closure"
status: archived
created_at: 2026-07-25
last_validated_at: 2026-07-25
archived_at: 2026-07-25
---
# Claude official ACP adapter — B3/B4/B5 closure

## Context / target

Additive closed-`AgentProfile` admission for the official Claude ACP adapter on
the merged Native ACP/arsd line (PRD R1/R3/R7/R12). It closes the three
Claude-only blockers left open by the 2026-07-24 official-adapter validation
after the shared B1/B2 repair merged:

- **B3 — selector/config fidelity and closed admission.** No Claude profile is
  registered, so the exact runtime identity, argv, downstream-CLI binding, and
  the closed model/effort domains are unproven at admission.
- **B4 — execute permission mediation.** The adapter resolves its *initial*
  permission mode from ambient Claude settings through its own settings
  manager. When that resolves to `bypassPermissions` the adapter auto-allows
  tool calls in-process, so a session can execute without ever reaching
  `session/request_permission` — the frozen grant is bypassed.
- **B5 — session metadata freeze.** Without profile-owned session metadata the
  adapter falls back to `settingSources: ["user","project","local"]`, letting
  ambient user/project/local settings define the underlying SDK's permission
  rules and tool surface for both `session/new` and `session/load`.

The exclusive production shape is unchanged:

```text
trusted caller → arsd UDS → Native ACP → pinned official adapter → downstream CLI
```

Every registered value is a byte-copy of the operator-frozen discovery
manifest (controller-held, outside the repository). The interpreter is the same
controller-frozen Node copy the Codex profile uses: the adapter entrypoint is an
ESM script whose `#!/usr/bin/env node` shebang would otherwise let the kernel
resolve the interpreter from ambient `PATH`.

Scope is the Claude slice only. This plan is **implementation and local
verification**: no push, PR, merge, deploy, rollout, service restart, production
enablement, release/tag/PyPI, or Sachima/Gateway integration. The explicit
non-approvals in `docs/roadmap/non-approvals.md` hold in full. The OpenCode and
Codex profiles keep byte-identical snapshots, profile hashes, launch hashes, and
evidence surfaces.

## Frozen identities (from the operator discovery manifest)

| Surface | Frozen value |
|---|---|
| Adapter package/version | `@agentclientprotocol/claude-agent-acp` `0.61.0` |
| Interpreter | controller-frozen Node `v24.14.0` copy (process image, never `env`/shebang resolution) |
| Downstream CLI binding | `CLAUDE_CODE_EXECUTABLE` (profile-owned fixed env) |
| ACP model domain | exactly `claude-fable-5[1m]` and `opus[1m]`; default `opus[1m]` |
| Effort domain/default | literal `max` |
| Selector ids | `model`, `effort`, `mode` |
| Required permission mode | exact literal `default` |
| Session metadata | `{"claudeCode":{"options":{"settingSources":[],"tools":{"type":"preset","preset":"claude_code"}}}}` |

**ACP Opus alias distinction.** `claude-opus-5[1m]` is the *direct Claude CLI*
author selector. It is **not** the ACP readback literal: a live
`session/set_config_option(model)` on this adapter reads back `opus[1m]`, which
is the only value the registered profile may carry. Registering the CLI-side
string would fail exact readback and refuse every Run before prompt.

**Two distinct ACP SDKs.** The controller-side Python ACP SDK is `0.11.0`
(`acp`, the locked runtime dependency that owns the JSON-RPC wire). The adapter
bundles its own JavaScript ACP SDK `1.3.0` inside the operator-installed
`0.61.0` tree. They are independent artifacts with independent versions; neither
number may be used to describe the other.

## Checklist

- [x] Preflight: GOAL, PRD, architecture, technical solution, features,
      current-status, AI_FLOW, non-approvals read; product position stated.
- [x] RED B3: Claude profile/registry/admission/argv/fixed-env/config-schema
      pins; generalized identity attestation over a Claude-shaped fixture;
      OpenCode/Codex byte-stability goldens.
- [x] Implement B3: `claude-agent-acp` executable mapping to the frozen Node;
      `CLAUDE_CODE_EXECUTABLE` fixed-env key; identity-required fixed-env keys
      derived from the frozen runtime instead of hardcoded Codex names;
      `ExpectedRuntimeIdentity` CLI-env / credential-root-env /
      project-config-closure bindings (omit-when-default);
      `CLAUDE_AGENT_ACP_0_61_0` in `DEFAULT_REGISTRY`.
- [x] RED B4: config-fidelity mode phases; driver mode leg and its refusals;
      hermetic Run from an advertised hostile mode to exact `default`;
      `kind=execute` deny/allow-once mediation; no-broad-auto-allow pin.
- [x] Implement B4: profile permission-mode binding; `ConfigFidelityMachine`
      mode → model → effort sequence with exact readback at every step and a
      final all-three readback; driver mode leg; strict `allow_once` option
      selection; grant-driven `execute` mediation.
- [x] RED B5: profile-owned canonical session metadata, hash binding, launch
      mirroring, exact `_meta` on `session/new` **and** `session/load`, no
      metadata for legacy profiles, no caller passthrough, same external
      session identity on load, post-load fidelity refusal.
- [x] Implement B5: `AgentProfile.session_meta` (canonical JSON text) +
      `session_meta_payload()`; launch mirroring; driver `meta` argument on both
      session calls; RunTask passes only the profile's frozen metadata.
- [x] GREEN: focused suites per phase, all `native_acp`/`arsd` suites, full
      pytest, compileall, canonical verifier, docs index/drift/governance,
      `git diff --check`.
- [x] Docs: this plan; `F-NATIVE-ADAPTER-CLAUDE-001` capability row; board
      `active_plan:` pointer and phase row; regenerate `docs/INDEX.md` and the
      drift report via repository tooling.

## Design decisions carried into code

- **The permission mode is a config-fidelity selector, not a launch flag.** The
  adapter reads ambient settings for its initial mode through its own settings
  manager, which `settingSources: []` does not govern — that option controls the
  underlying SDK's rule sources, not the adapter's initial-mode resolution. So
  the profile freezes ACP config selector `mode` to exact literal `default` and
  the fidelity machine drives `mode → model → effort`, each with its own exact
  readback, plus a final readback that must show all three exact. A profile
  that declares a permission mode cannot reach `prompt` unless every one of the
  three is proven — a missing selector, a value outside the advertised domain,
  or an inexact readback is a zero-Turn pre-dispatch refusal.
- **Defense in depth, both layers required.** Frozen session metadata
  (`settingSources: []`) removes ambient user/project/local *rules* from the
  SDK; the forced `mode=default` removes the adapter's ambient *bypass*. Either
  alone leaves a proven hole: with bypass mode the adapter auto-allows before
  any rule is consulted, and with ambient setting sources a permissions rule can
  pre-approve a tool the grant never allowed.
- **Mediation selects `allow_once` strictly, never `allow_always`.** The
  official Claude adapter advertises `allow_always` **first** in its option
  list, and honoring it installs a session-scoped allow rule for that tool —
  a broad auto-allow that outlives the mediated call. The bridge now scans
  permission options by a fixed *kind* preference instead of wire order:
  `allow_once` is the only allow form it will ever return (a prompt offering
  only `allow_always` denies fail-closed), and rejects prefer `reject_once`
  over `reject_always`. OpenCode fixtures already list the once-scoped options
  first, so their decisions are unchanged.
- **`execute` mediation is grant-driven, and only `execute`.** A registered
  `execute` prompt is allowed only when the frozen grant carries the `execute`
  capability, and then only through an `allow_once` option; without it the
  prompt denies exactly as before. The remaining write-family kinds
  (`edit`/`delete`/`move`) keep their unconditional deny in this slice: their
  mediated-allow path has no live canary, and widening them is not authorized
  here. This closes the inconsistency where `observe_tool_update` already
  treated a completed `execute` under an `execute` grant as legitimate while
  mediation refused it, so no Run could ever reach that state honestly.
- **Session metadata is profile-owned, canonical, and hash-bound.**
  `AgentProfile.session_meta` stores the exact `_meta` argument as canonical
  JSON *text*, validated at construction to byte-equal its own canonical
  re-serialization (the same rule `CODEX_CONFIG` uses). Text is deeply immutable
  by construction, so no caller, test, or later Run can mutate a shared nested
  dict; `session_meta_payload()` parses a fresh object per call. The parsed
  object enters `AgentProfile.snapshot()`, so the profile hash and the mirrored
  `launch.json` cover it. There is no caller metadata surface anywhere:
  `AgentRunRequest` has no `_meta` field and the driver takes metadata only from
  the resolved profile.
- **The same frozen metadata is passed on `session/new` and `session/load`.**
  Load is not a "resume with whatever the session had": the adapter rebuilds the
  SDK query from the load request, so omitting metadata there would silently
  restore ambient setting sources for every reused Session. Profiles with no
  metadata omit the argument entirely, so the legacy wire frames stay
  byte-identical (`_meta` absent, not `null`).
- **Identity attestation is generalized only where Claude differs.**
  `ExpectedRuntimeIdentity` gains three bindings — the fixed-env key that must
  carry the CLI path, the fixed-env key holding a credential root (or `None`),
  and the project-config closure relative path (or `None`). All three default to
  the existing Codex values and are omitted from `to_dict()` at that default, so
  the Codex snapshot, profile hash, and launch hash stay byte-identical and
  every Codex row still runs. A profile that declares no credential root records
  an explicit `credential_root_not_declared` PASS row rather than dropping rows
  silently, so a missing check is never invisible in `attestation.json`.
- **Claude declares no ARS-managed credential root and no config closure.** The
  Claude CLI owns its own credential storage, which ARS does not manage, stage,
  or inspect; the profile therefore requires exactly zero caller credential
  references (closed admission) and pins no credential-root structure. The
  workspace `.claude/` chain is deliberately *not* closed: the frozen
  `settingSources: []` plus the forced `mode=default` are the proven defense,
  and the live canary passed with a hostile `.claude/settings.local.json`
  present in the workspace.

## Acceptance

1. A Claude-shaped Run resolves the frozen Node process image, `argv[1]` is the
   frozen adapter entry, the downstream CLI is bound only through profile-owned
   `CLAUDE_CODE_EXECUTABLE`, and a drifted Node/entry/CLI digest is refused
   before spawn with the mapped `detail_code` and durable `attestation.json`.
2. Requests outside the closed model domain (`claude-fable-5[1m]`, `opus[1m]`),
   outside the effort domain (`max`), or carrying any credential reference are
   refused at admission before workspace bind and before spawn.
3. A Claude profile whose agent advertises `mode=bypassPermissions` is switched
   to exact `default` with proven readback before any prompt; a missing `mode`
   selector, a `default` value outside the advertised domain, and an inexact
   mode readback each end the Run zero-Turn with `CONFIG_FIDELITY` and the fake
   agent observes no `session/prompt` frame.
4. The permission bridge receives `kind=execute`; a deny decision leaves the
   inert side effect absent, and an `execute`-granted allow decision selects the
   `allow_once` option (never `allow_always`) and permits exactly that one inert
   side effect.
5. `session/new` and `session/load` both carry byte-exact
   `_meta.claudeCode.options` = `{"settingSources": [], "tools": {"type":
   "preset", "preset": "claude_code"}}`; profiles without metadata send no
   `_meta` at all; a load-path Run keeps the same external session identity and
   still refuses to prompt when mode/model/effort fidelity fails after load.
6. OpenCode and Codex snapshots, profile hashes, launch hashes, attestation
   rows, and fake-agent expectations are unchanged.
7. Full pytest, compileall, and the canonical verifier pass; no dependency,
   lockfile, package-version, service-unit, wire-protocol, or result-grammar
   changes.

## Files likely to change

- `src/agent_run_supervisor/native_acp/profile.py` — Claude registration,
  permission-mode binding, session metadata, generalized identity-required env.
- `src/agent_run_supervisor/native_acp/attestation.py` — CLI-env /
  credential-root / project-config bindings on `ExpectedRuntimeIdentity`.
- `src/agent_run_supervisor/native_acp/config_fidelity.py` — permission-mode
  phases.
- `src/agent_run_supervisor/native_acp/driver.py` — mode leg; session metadata
  on `session/new` and `session/load`.
- `src/agent_run_supervisor/native_acp/permissions.py` — strict `allow_once`
  selection; grant-driven `execute`.
- `src/agent_run_supervisor/native_acp/run_task.py` — machine construction with
  the mode binding (including rollback) and metadata pass-through.
- `src/agent_run_supervisor/native_acp/spec.py` — launch mirroring of the frozen
  session metadata.
- `tests/native_acp/{test_profile,test_spec,test_attestation,test_permissions,
  test_driver_config_fidelity,test_run_task,test_session_switching}.py`,
  `tests/native_acp/fake_agent.py`, `tests/arsd/test_admission.py`.
- `docs/roadmap/features.md`, `docs/roadmap/current-status.md`, this plan,
  `docs/INDEX.md`, `docs/lessons/_drift_report.md`.

## Verification gates

```bash
uv sync --locked --extra dev --extra release --extra native
uv run --locked python -m pytest -q tests/native_acp tests/arsd
uv run --locked python -m pytest -q
uv run --locked python -m compileall -q src scripts tests
./scripts/verify_local.sh
uv run --locked python tools/build_docs_index.py --check
uv run --locked python tools/docs_drift_signal.py --check
uv run --locked python tools/check_roadmap_governance.py
git diff --check
```

Real-credential Claude acceptance over a real socket is a separate, separately
authorized controller gate and is never executed during implementation.

## Risks

- **Host-specific absolute paths.** The registered runtime identity pins this
  host's installed adapter, CLI, and frozen Node. These are admission controls,
  not portability claims: elsewhere the profile refuses fail-closed at the spawn
  boundary instead of launching something unverified. Mitigation is a new
  install → discovery → freeze cycle plus a profile revision bump.
- **Adapter-version-bound permission semantics.** The `mode` selector domain,
  the auto-allow behavior under `bypassPermissions`, and the `_meta.claudeCode`
  option shape are `0.61.0` facts. Any adapter, CLI, Node, or SDK upgrade
  requires a fresh discovery + permission canary, a profile revision, and
  review; there is no silent fallback and no ambient-mode tolerance.
- **Mediation preference change.** Preferring `allow_once` over `allow_always`
  changes which option id ARS returns for agents that advertise the always-form
  first. That is the intended repair — an always-scoped grant outlives the
  mediated call — and OpenCode fixtures are unaffected because they list the
  once-form first. Pinned by tests so a regression is loud.
- **Cooperative runtime, honestly scoped.** The launched adapter and CLI are
  ordinary same-UID host processes that ARS deliberately starts and does not
  OS-sandbox. Permission mediation is cooperative agent policy, not hostile
  process containment; the guarantee is that a bypassing or ambiently
  configured session is refused before any prompt is dispatched.

## Rollback

Revert the branch commits (or discard the uncommitted worktree changes). No
storage schema, wire protocol, result grammar, dependency, or deployment
surface is touched, and the legacy profiles' hashes and evidence surfaces are
byte-identical, so rollback is a pure source revert. Durable artifacts written
by Claude-shaped Runs are additive per-Run files that no reader requires.

## Implementation notes (2026-07-25)

- Sequenced strictly RED→GREEN per phase: B3 (identity/registration), then B4
  (permission mediation), then B5 (session metadata). Each phase's focused
  suites were red before its production change and green after.
- Two pre-existing pins were legitimately updated, not weakened: the registry-id
  tuple in the Codex snapshot golden (the registry grew additively) and the
  fake agent's permission fixture (now accepts a custom option list so the
  official Claude always-first ordering can be exercised). The Codex and
  OpenCode profile hashes, config-schema hashes, launch payloads, and
  attestation row sets are asserted byte-stable by their own tests.
- A denied mediation decision is answered as a *cancelled* outcome, so the
  agent receives no option id at all — in particular never the always-scoped
  one. That is the pre-existing client behavior and the live canary's deny leg
  passed with it; the bridge's computed reject option id remains unused on the
  wire.
- Current state: a local immutable candidate commit exists on the task branch
  `feat/claude-official-adapter-b3-b5-closure`, and local verification is
  complete. Nothing was pushed, opened as a PR, merged, deployed, rolled out,
  restarted, or production-enabled; no release, tag, publication, or PyPI upload
  was made; and no real-credential or real-socket acceptance was executed.

---
title: "agent-run-supervisor PRD"
status: active
created_at: 2026-05-29
last_validated_at: 2026-07-21T20:30:00+0800
---
# agent-run-supervisor PRD

## 1. Product goal

`agent-run-supervisor` gives caller projects a local, auditable, role-bound way to run external AGENTs through ACP/acpx without embedding runner lifecycle, permission-policy compilation, stream parsing, status classification, or artifact redaction in every caller.

The product must support both acpx execution modes required by the roadmap:

1. **one-shot exec runs** for bounded single-task execution;
2. **persistent sessions** for controlled multi-turn continuity, resumed work, and explicit session lifecycle management.

Implementation may deliver these modes in separate engineering phases, but the product requirement is both modes.

The settled ARS vNext production target extends this product with a Native ACP execution vertical: a reusable `ars-core` plus a thin, unprivileged, local `arsd` daemon over a Unix domain socket as the sole production ingress. Native ACP is additive beside the unchanged acpx paths and never falls back to acpx. §8 records these target requirements as repository authority; they are a documentation target, not implementation authority (§8.7). FR-1…FR-10 below describe the implemented v0.1.7 acpx product.

## 2. Users and caller projects

### Primary users

- Human developer/operator running a local dev CLI.
- AI-assisted development controller such as Hermes, coordinating Claude Code, Codex CLI, or another ACP-capable worker.

### Caller projects

- Local development workflows that need reproducible AGENT-run artifacts.
- Thin integrations that build task context, render progress, and interpret final output without owning ACP/acpx or Native ACP lifecycle. Current acpx callers select an `AgentRoleSpec.role_id`; vNext Native callers additionally choose an `AgentProfile` and freeze an `execution_grant` for each submitted `AgentRunRequest` (§8).

### Non-callers

- Public ingress users.
- Messaging-platform recipients.
- Production Gateway runtime.
- Autonomous agent-to-agent routing systems.

## 3. Product principles

- **Documentation-first governance**: PRD defines product requirements; design documents define the technical solution; roadmap/status tracks engineering completion; phase plans detail implementation only after goals are fixed.
- **Caller-owned authorization, ARS-enforced grants**: business approval sits with the caller; ARS enforces what it is given, never widens it, and is not a broad RBAC system. Concretely:
  - *Current acpx surface* — `AgentRoleSpec.role_id` remains the long-lived role and policy boundary; acpx permissions bind to that role.
  - *vNext Native ACP target (§8)* — the caller freezes an `execution_grant`; ARS seals it into an immutable per-Run `AgentRunSpec` and enforces it under a versioned typed `AgentProfile` that owns launch/config/compatibility. This is neither per-Run human approval inside ARS nor broad RBAC; ARS never re-reads a "latest" policy at runtime.
- **Supervisor, not business judge**: runner/protocol completion is not business success. Caller projects own business verdicts.
- **Auditable by default**: runs and sessions produce deterministic, redacted local artifacts with restrictive permissions.
- **Fail closed on uncertainty**: invalid roles, cwd mismatch, malformed stdout, protocol drift, denied permissions, unsafe config, stale session locks, and lifecycle failures return deterministic non-success statuses.
- **Honest security claims**: `allowed_roots` validates cwd/config intent only. It is not an OS/filesystem sandbox.
- **No hidden live expansion**: repository docs never imply public ingress, real IM delivery, Gateway lifecycle operations, production config writes, live/default-on behavior, `@all`, or agent-to-agent auto-routing.

## 4. Functional requirements

### FR-1 AgentRoleSpec role model

The product must validate role specs that define identity, runner configuration, workspace intent, permissions, session behavior, limits, prompt contract, and redaction policy.

Checklist:

- [x] Validate role identity fields.
- [x] Validate runner type/version/binary shape for current acpx contract.
- [x] Validate workspace `default_cwd`, non-empty `allowed_roots`, and explicit `allowed_roots_security_boundary: false`.
- [x] Validate permission booleans for acpx-controllable tool classes.
- [x] Validate timeout, max-turn, and max-output limits.
- [x] Validate prompt/redaction config.
- [x] Provide CLI `validate-role` with stable role hash.
- [x] Extend session strategy/config to represent both one-shot exec and persistent-session use without changing the role-bound authorization model.

Acceptance:

- Invalid specs fail before artifact creation or runner/session work.
- Role hash is stable for canonical role content.
- No business-only permissions such as delivery, Gateway lifecycle, production config, or `@all` exist in `AgentRoleSpec`.

### FR-2 acpx policy and argv compilation

The product must compile a validated role into pinned acpx invocation material and permission policy.

Checklist:

- [x] Compile allowed permissions to acpx `autoApprove`.
- [x] Compile denied permissions to acpx `autoDeny`.
- [x] Set default policy action to deny.
- [x] Add non-interactive permission failure behavior.
- [x] Compile format/JSON/timeout/max-turn/suppress-read flags.
- [x] Include role model when set.
- [x] Persist redacted argv/policy artifacts for dry-run evidence.
- [x] Ensure real exec path uses the same compiler as dry-run.
- [x] Add session-mode command compilation once persistent-session command shapes are fixture-proven. *(S1c: create/ensure/show/status management commands and the fixture-proven `prompt -s` turn. S2: prompt-turn permissions are role-derived — granted kinds compile the exec-path `--permission-policy` JSON, an all-deny role keeps the fixture-proven `--deny-all` shape; live permissioned-prompt fixture capture is an operator follow-up.)*

Acceptance:

- Golden tests prove generated policy and argv shape.
- Compilers never use shell interpolation.
- Unsupported or unknown permission classes fail closed.

### FR-3 cwd and workspace intent gate

The product must validate effective cwd against role workspace intent before runner/session work.

Checklist:

- [x] Resolve effective cwd from explicit CLI input or role default.
- [x] Fail closed before artifact creation when cwd is outside configured roots.
- [x] Persist the disclaimer that allowed roots are not a sandbox.
- [x] Add validated workspace hash/binding foundation for persistent sessions.
- [x] Re-validate cwd/role/workspace hash when attaching to or resuming a real persistent session runtime. *(S1c: every send/status re-opens the record and re-validates the binding before mutation.)*

Acceptance:

- cwd failure creates no run/session artifacts.
- Docs and artifacts never claim OS-level filesystem isolation.

### FR-4 One-shot exec supervision

The product must supervise a local one-shot `acpx exec` subprocess.

Checklist:

- [x] CLI/library run path launches acpx exec with compiled argv/policy.
- [x] Captures stdout/stderr into EventStore.
- [x] Runs under validated effective cwd.
- [x] Records start/end timestamps, acpx version, role hash, policy hash, exit code, signal, timeout, and kill metadata.
- [x] Uses an outer watchdog with graceful termination and forced kill fallback.
- [x] Current fake outcome/finalization path supports tests for supplied subprocess outcomes.
- [x] Pre-E1 unimplemented real-run path refused safely until the E1 runner replaced it.

Acceptance:

- Fake subprocess tests cover success, failure, timeout, interruption, malformed stdout, permission denial, and stderr redaction.
- Minimal real acpx smoke can run in a scratch repo after the phase explicitly approves local launch.

### FR-5 Persistent session supervision

The product must support persistent ACP/acpx sessions as a first-class product requirement.

Checklist:

- [x] Fixture-prove acpx session command grammar and observed stdout/event shapes.
- [x] Persist session identity, role hash, workspace hash, acpx version, policy hash, and lifecycle metadata in a local session store.
- [x] Implement session locks/leases to prevent concurrent unsafe local mutation.
- [x] Detect and recover from expired local session locks deterministically.
- [x] Refuse cross-role, cross-workspace, stale-policy, acpx-version, or adapter-mismatched session reuse before mutation.
- [x] Create/open real acpx sessions under a validated `AgentRoleSpec`. *(S1c MVP: fixture-shaped `sessions new` runtime; S1 closure acceptance adds reproducible local real-acpx smoke via `scripts/smoke_persistent_session.py`.)*
- [x] Reattach/send prompts only when role/workspace/session metadata still match policy. *(S1c: lease-locked `prompt -s` turn with binding revalidation before any mutation; S1 closure acceptance adds two-turn continuity regression and real-acpx smoke markers.)*
- [x] Define close/abort semantics and failure statuses. *(S1d: fixture-proven `sessions close`/`cancel -s`; close performs an atomic `closed` transition, abort reports honest `cancelled: true|false` without a business verdict, and `send`/`close`/`abort` fail closed on an already-closed session; fake-executor/fixture acceptance.)*
- [x] Provide CLI/library session operations after design and fixtures are proven. *(S1c MVP: `session create|send|status`; S1d adds `session close|abort|list`.)*

Acceptance:

- Session fixtures and tests cover create, send, two-turn resume/continuity, status, list, close, stale-lock recovery, and mismatch refusal. *(S1c covers create/send/status, lease release on success and failure, and mismatch refusal; S1d adds close, abort/cancel, local read-only list, and closed-session refusal; S1 closure acceptance adds the multi-turn regression plus real local acpx smoke. H1 adds detection-first stale-lock reporting; K1 adds process-liveness crash/interruption recovery beyond deterministic expired-lease replacement, with conservative `alive`/`crashed`/`unknown` classification and composite supervisor+child safety.)*
- Session artifacts are redacted and local-first.
- Persistent sessions do not imply public ingress, Gateway operations, real delivery, or agent-to-agent routing.

### FR-6 Observed event parser and normalized events

The product must parse only fixture-proven observed acpx stdout/event schemas.

Checklist:

- [x] Parse current acpx `0.12.0` exec fixture family.
- [x] Assemble final messages from ordered observed text deltas.
- [x] Extract usage updates.
- [x] Emit normalized events for run lifecycle, message deltas, tools, usage, permission events, unknown updates, completion, and failure.
- [x] Fail closed on malformed JSON/framing.
- [x] Enforce `max_output_bytes`.
- [x] Add persistent-session parser/event coverage after session fixtures are captured. *(S1c: prompt-turn NDJSON via the existing parser; management-command JSON via a separate safe summarizer.)*

Acceptance:

- Fixture replay is deterministic.
- Unknown values preserve type/key summaries only unless an explicit unsafe raw-capture mode is added.

### FR-7 Status and result model

The product must convert runner/session behavior into supervisor-owned statuses.

Checklist:

- [x] Implement current statuses: `completed`, `runner_error`, `invalid_invocation`, `timed_out`, `no_session`, `permission_denied`, `interrupted`, `protocol_error`, `infrastructure_error`, `policy_error`.
- [x] Map observed exit codes `0/1/2/3/4/5/130/unknown`.
- [x] Refine bare exit status using acpx metadata where available.
- [x] Keep nonzero exits from becoming completed.
- [ ] Add explicit session lifecycle status details where needed without breaking existing result consumers.

Acceptance:

- Table-driven tests cover statuses and metadata refinements.
- Supervisor status never equals caller business verdict.

### FR-8 EventStore, artifacts, and redaction

The product must write safe local audit artifacts for runs and sessions.

Checklist:

- [x] Create run directories with restrictive permissions.
- [x] Write final JSON/text artifacts with restrictive permissions.
- [x] Use atomic writes for final artifacts.
- [x] Append stream artifacts as JSONL/NDJSON.
- [x] Redact prompt, env, argv, metadata, stderr, stdout, normalized event text, and final message surfaces.
- [x] Add retention/cleanup knobs before long-lived use. *(H1: confined, dry-run-first `retention.plan_cleanup`/`apply_cleanup` + `agent-run-supervisor cleanup` CLI; deletes only within a resolved `.agent-run-supervisor` root, never follows symlinks out of root, never deletes open/live-locked sessions; `tests/test_retention.py`, `tests/test_cli_commands.py`. Merged on `main` via PR #19 at `484ae23`.)*
- [x] Add session artifact foundation layout.
- [x] Add session turn artifacts. *(S1c: redacted `turns/<turn_id>/` and `management/` artifacts; cleanup policy remains a later slice.)*
- [ ] Add explicit unsafe raw-capture opt-in only if a later phase proves it necessary.

Acceptance:

- Permission tests prove artifact modes.
- Secret-shaped scans and redaction tests prove user-facing artifacts do not leak sensitive values.

### FR-9 CLI and library surface

The product must expose a small local CLI and library API.

Checklist:

- [x] `validate-role <role-file>`.
- [x] `replay <events.ndjson>`.
- [x] `doctor` baseline.
- [x] `run --role <role-file> --prompt-file <file> [--cwd <dir>] --no-real-run`.
- [x] `run --role <role-file> --prompt-file <file> [--cwd <dir>]` real exec supervision.
- [x] Session lifecycle commands/API after session design and fixtures are proven. *(S1c MVP: `session create|send|status` plus the `SessionRuntime` library surface; S1d adds `session close|abort|list` (local read-only list).)*
- [x] Stable JSON outputs for caller automation. *(S1c: session `create|send|status`; S1d: session `close|abort|list`; all keep `business_verdict: null` and 0/nonzero exit codes.)*
- [x] Generic local caller boundary for library integrations. *(I1: `caller.py` accepts role source, prompt/context, cwd, exec/session mode, and artifact dirs; delegates to `SupervisorRunner`/`SessionRuntime`; returns a wrapper with `business_verdict: null`; no new CLI.)*

Acceptance:

- CLI smoke tests cover help, invalid inputs, dry-run artifacts, real exec, session lifecycle, doctor, and replay as each feature lands.

### FR-10 Doctor and environment probe

The product must diagnose local readiness without launching unintended AGENT work.

Checklist:

- [x] Probe Python/Node/acpx version basics.
- [x] Honor role-specific `runner.acpx_binary` in acpx probe.
- [x] Replay fixture through parser.
- [x] Probe EventStore permissions.
- [x] Probe adapter availability. *(H1: `preflight.probe_adapter` — declared + hostable only; never launches the adapter/agent; `tests/test_preflight.py`.)*
- [x] Detect runtime `npx` fetch risk. *(H1: `preflight.probe_npx` — read-only `npx --version`; `fetch_risk` true only when no explicit `acpx_binary`; never runs `npx acpx`; `tests/test_preflight.py`.)*
- [x] Check policy parseability/dry-run safely. *(H1: `preflight.probe_policy` — pure-local permission-policy compile, asserts `default_action == "deny"`, no subprocess; `tests/test_preflight.py`.)*
- [x] Report role cwd/allowed-roots validation. *(H1: `preflight.probe_workspace` — reuses the workspace intent gate; always reports the not-a-sandbox disclaimer; `tests/test_preflight.py`.)*
- [x] Report redaction probe. *(H1: `preflight.probe_redaction` — synthetic pattern-shaped samples only, asserts `leaked == []`; `tests/test_preflight.py`.)*
- [x] Add session-readiness probes once session support exists. *(H1: `preflight.probe_session_readiness` — temp-store `0700`/`0600` mode probe + read-only stale-lock detection; no acpx launch; `tests/test_preflight.py`, `tests/test_session_store.py`.)*

Acceptance:

- Doctor remains read-only unless a future probe explicitly states otherwise. *(All H1 probes are read-only; `launched_real_agent` stays `false`. Doctor output is documented in `docs/design/result-event-schema.md` §5. H1 is merged on `main` via PR #19 at `484ae23`.)*
- Missing/invalid binaries produce structured output rather than tracebacks. *(External-binary probes are informational and never flip `doctor`'s `ok`, so the no-role gate still exits `0`.)*

## 5. Non-functional requirements

### NFR-1 Local-first operation

- Works as a local Python library and dev CLI.
- The released base product requires no daemon. The vNext production target adds a thin, unprivileged, local `arsd` over a Unix domain socket (§8.1) — local-only and not yet implemented.
- Does not require public ingress or production Gateway runtime.

### NFR-2 Safety and redaction

- No secrets, tokens, cookies, raw env values, signed URLs, or platform private IDs may be committed or displayed.
- Artifacts are redacted by default.
- Secret/static scans run before PRs.

### NFR-3 Determinism and auditability

- Fixture replay is deterministic.
- Artifacts contain enough metadata to explain runner/session outcomes.
- Result schema changes are deliberate and documented.

### NFR-4 Testability

- Behavior is covered by pytest, compileall, fixture validation, CLI smoke, docs gates, secret scans, and CI.
- External runner/session behavior is isolated behind fake-process and fixture tests before real smoke.

## 6. Explicit non-goals / non-approvals

The product requirement for exec and persistent sessions does not approve unrelated live behavior. Current non-goals are:

- Sachima behavior integration;
- real AGENT automatic replies;
- public ingress;
- real IM delivery;
- Gateway restart/reload/replace;
- production config writes;
- live/default-on behavior;
- worker auto-routing;
- participant persistence or management UI;
- `@all` fanout;
- agent-to-agent automatic routing;
- trusted Markdown/HTML rendering;
- treating `allowed_roots` as an OS/filesystem sandbox;
- per-run human approval as the default authorization model.

Recording the vNext Native ACP / `arsd` target requirements in §8 approves documentation only; it does not approve their implementation (§8.7).

## 7. Success metrics

- Feature completion is tracked in `docs/roadmap/features.md`.
- The living board (`docs/roadmap/current-status.md`) states the current phase snapshot and open tails; closed acceptance lives in `docs/roadmap/archive/`.
- Local gates and CI pass.
- A caller can inspect redacted artifacts without parsing raw acpx streams.
- The supervisor never invents caller business verdicts.

## 8. vNext target requirements — Native ACP and arsd (approved documentation target)

This section records the settled ARS vNext architecture and requirements as repository authority. It is a **documentation target**: nothing in this section is implemented, released, production-accepted, or live, and nothing in it authorizes implementation (§8.7). Current implemented reality remains the v0.1.7 library/CLI and acpx paths (FR-1…FR-10).

Input provenance: ARS refactor committee Rev3 (`CLAUDE_PRODUCTION_VERTICAL_DESIGN_REV3.md`, sha256 `a088b208b9494b94a028d912127a373b1ae0831e31476e8695dbf3e26c2e4bc1`). This PRD together with `docs/design/architecture.md` §9 and `docs/design/technical-solution.md` §9 carries the durable authority inside the repository; the handoff hash remains provenance only.

### 8.1 Product position and topology (target)

- ARS remains an independent local supervision system: not Sachima, not a Gateway plugin, not an IM adapter, and not a business-authorization engine.
- The production form is a reusable `ars-core` plus a thin, unprivileged, local `arsd` daemon over a Unix domain socket (`0700` dir / `0600` socket, no TCP, no root):

  ```text
  Hermes / FlowWeaver / CLI
  -> local Unix domain socket
  -> arsd
  -> ars-core / Native ACP Driver
  -> external ACP Agent
  ```

- `arsd` is the sole production ingress and the single supervision authority: it directly owns Native ACP connections and Agent process trees. There is no durable per-Run worker, and no Run survives an `arsd` crash by design.
- The v0.1.7 local Python library/dev CLI and the acpx exec/session paths remain implemented legacy/current surfaces; direct `ars-core` embedding stays a test/dev path.
- Native ACP is additive in Stage 0/1. Native failure never falls back to acpx; acpx is not a production dependency, driver, compatibility layer, or fallback for the Native vertical.

### 8.2 Responsibility boundary (target)

- Hermes/FlowWeaver own business authorization, risk decisions, operator approval, task admission, and final business meaning.
- ARS/`arsd` only authenticate local callers (Unix-socket peer credentials against a configured allowlist), bind already-approved resources, manage Run/Session/process/ACP/evidence lifecycle, and enforce immutable per-Run execution grants. ARS never widens a grant and never re-reads a "latest" policy at runtime.
- ARS is not a broad RBAC system and does not duplicate caller business policy.
- Honest claims: `allowed_roots`, role policy, UDS caller authentication, and process supervision are not filesystem/network/container isolation. Permission mediation is policy enforcement for a cooperative registered Agent — not an OS sandbox and not hostile-process containment.

### 8.3 Run / Profile / Driver configuration model (target)

- `AgentProfile` is a versioned, typed, code-registered declarative launch/config/compatibility description, separate from the generic Native ACP Driver. Profiles form a closed set (first: OpenCode 1.18.4).
- Admission freeze order: resolve and freeze profile revision/snapshot/hash, config schema hash, and grant/role/workspace/MCP/credential-reference hashes → materialize a controlled `ResolvedLaunchSpec` → seal the immutable `AgentRunSpec`/`spec_hash` → spawn → observe. Observed facts never write back into the Spec or Profile.
- No arbitrary user passthrough of CLI flags, argv, env, JSON, or unknown config keys.
- Run model/effort are per-Run immutable requested values; `EffectiveRunState` records observed readback only (process identity, agent info, capabilities, external session id, discovery snapshots, effective model/effort).
- Exact-or-zero configuration fidelity before any prompt: discovery → set model → rediscover → set effort → exact readback → prompt. Any failure means zero Turn and no prompt — no alias, coercion, or nearest-option degradation.

### 8.4 Session and context continuity (target)

- An ARS Session is a stable identity across multiple completed Runs. It binds one external Agent type/session identity, an owner/namespace, and a compatible profile lineage.
- v1 continuity is process-per-Run plus the external Agent session id and `session/load`; ARS never duplicates the Agent's conversation memory.
- The same Session may change model/effort only between completed Runs through a controlled switch (load the same external session → discovery → set → exact readback); there is no hot switch during an active Run.
- Changing Agent type requires a new ARS Session plus an explicit, caller-owned context handoff.
- Partial config-switch failure means no prompt; rollback must itself be proven by exact readback; rollback failure or readback uncertainty quarantines the Session.
- Real `session/load` context-token continuity (nonce recall across Runs) is a load-bearing Stage 1 acceptance gate; the earlier zero-prompt probe proved transport compatibility only.

### 8.5 Supervision, crash, status, and replay (target)

- Native ACP uses a supervised live-stdio process surface (`ManagedProcess` or equivalent). The existing completion-oriented `execute_subprocess` remains legacy acpx-only and cannot be reused unchanged for Native.
- The SDK connection exclusively owns the ACP stdin/stdout wire. The supervisor owns PID/PGID/process identity, bounded stderr capture, timeouts, terminate/kill/reap escalation, and process-tree containment.
- If a prompt may have been dispatched but no trustworthy terminal result exists:

  ```text
  Run.status = unknown
  Session.status = quarantined
  retryable = false
  ```

- An uncertain prompt is never auto-retried, auto-replayed, or resent. Restart performs reconciliation only. Successor work is an operator/business decision creating an independent new Run (`retry_of_run_id`) that never rewrites the original terminal facts.
- Production `arsd` runs under a user-level service manager with semantics equivalent to `Restart=on-failure` + `KillMode=control-group`, sharing one managed cgroup with all Agent descendants: an `arsd` crash terminates the whole tree, and restart reconciles instead of resuming. Graceful shutdown (`killpg`) and crash cleanup (cgroup) are two distinct paths that never substitute for each other.
- The runtime ledger exists for supervision, recovery, duplicate prevention, progress, config, and result verification — not as a second copy of Agent conversation memory.

### 8.6 Permission, storage, and evidence (target)

- Permission mediation is default-deny and must be proven by deterministic tests plus a real denied-action canary; a run with zero permission events (a "no-tool review") is not permission evidence.
- Native stores are isolated under explicit `native-runs/` and `native-sessions/` roots; legacy `runs/` and `sessions/` stores are never read, rewritten, or migrated by Native paths.
- `workspace_hash` is a binding/config hash, not workspace-content integrity. Real no-change acceptance needs a known-empty-workspace assertion or a bounded content manifest/digest — never `workspace_hash` or `git status` alone.
- Evidence tiers are strictly separated: (A) pre-implementation real compatibility probes (context only), (B) Stage-1 direct-drive real-Agent smoke evidence, (C) Stage-2 arsd production acceptance. Stage-2 acceptance is never claimed from Stage-1 evidence, and fakes/test doubles are never production evidence.

### 8.7 Authorization status (as of 2026-07-21)

- This section is documentation authority only. The C0 docs-governance activation and this G2 authority-document alignment are the completed, authorized documentation changes; implementation gates remain separate.
- Stage 0/1 implementation (slices C1–C10, including the `agent-client-protocol` dependency change and all Native ACP source/tests) remains unauthorized and requires separate explicit operator approval.
- `arsd`, service/cgroup deployment, release/tag/PyPI publication, Sachima integration, and Gateway/IM/live behavior remain unimplemented and unauthorized.
- All standing non-approvals (§6, `docs/roadmap/non-approvals.md`) continue to hold.

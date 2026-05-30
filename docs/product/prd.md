---
title: "agent-run-supervisor PRD"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T13:44:07+0800
---
# agent-run-supervisor PRD

## 1. Product goal

`agent-run-supervisor` gives caller projects a local, auditable, role-bound way to run external AGENTs through ACP/acpx without embedding runner lifecycle, permission-policy compilation, stream parsing, status classification, or artifact redaction in every caller.

The product must support both acpx execution modes required by the roadmap:

1. **one-shot exec runs** for bounded single-task execution;
2. **persistent sessions** for controlled multi-turn continuity, resumed work, and explicit session lifecycle management.

Implementation may deliver these modes in separate engineering phases, but the product requirement is both modes.

## 2. Users and caller projects

### Primary users

- Human developer/operator running a local dev CLI.
- AI-assisted development controller such as Hermes, coordinating Claude Code, Codex CLI, or another ACP-capable worker.

### Caller projects

- Local development workflows that need reproducible AGENT-run artifacts.
- Future thin integrations that select `role_id`, build task context, render progress, and interpret final output without owning ACP/acpx lifecycle.

### Non-callers

- Public ingress users.
- Messaging-platform recipients.
- Production Gateway runtime.
- Autonomous agent-to-agent routing systems.

## 3. Product principles

- **Documentation-first governance**: PRD defines product requirements; design documents define the technical solution; roadmap/status tracks engineering completion; phase plans detail implementation only after goals are fixed.
- **Role-bound authorization**: `AgentRoleSpec.role_id` is the stable identity and policy boundary. Permissions bind to long-lived roles, not ad-hoc per-run human approval tickets.
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
- [ ] Add session-mode command compilation once persistent-session command shapes are fixture-proven.

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
- [ ] Re-validate cwd/role/workspace hash when attaching to or resuming a real persistent session runtime.

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
- [ ] Create/open real acpx sessions under a validated `AgentRoleSpec`.
- [ ] Reattach/send prompts only when role/workspace/session metadata still match policy.
- [ ] Define close/abort semantics and failure statuses.
- [ ] Provide CLI/library session operations after design and fixtures are proven.

Acceptance:

- Session fixtures and tests cover create, send, resume, close, stale-lock recovery, mismatch refusal, and crash/interruption behavior.
- Session artifacts are redacted and local-first.
- Persistent sessions do not imply public ingress, Gateway operations, real delivery, or agent-to-agent routing.

### FR-6 Observed event parser and normalized events

The product must parse only fixture-proven observed acpx stdout/event schemas.

Checklist:

- [x] Parse current acpx `0.10.0` exec fixture family.
- [x] Assemble final messages from ordered observed text deltas.
- [x] Extract usage updates.
- [x] Emit normalized events for run lifecycle, message deltas, tools, usage, permission events, unknown updates, completion, and failure.
- [x] Fail closed on malformed JSON/framing.
- [x] Enforce `max_output_bytes`.
- [ ] Add persistent-session parser/event coverage after session fixtures are captured.

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
- [ ] Add retention/cleanup knobs before long-lived use.
- [x] Add session artifact foundation layout.
- [ ] Add session turn artifacts and cleanup policy.
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
- [ ] Session lifecycle commands/API after session design and fixtures are proven.
- [ ] Stable JSON outputs for caller automation.

Acceptance:

- CLI smoke tests cover help, invalid inputs, dry-run artifacts, real exec, session lifecycle, doctor, and replay as each feature lands.

### FR-10 Doctor and environment probe

The product must diagnose local readiness without launching unintended AGENT work.

Checklist:

- [x] Probe Python/Node/acpx version basics.
- [x] Honor role-specific `runner.acpx_binary` in acpx probe.
- [x] Replay fixture through parser.
- [x] Probe EventStore permissions.
- [ ] Probe adapter availability.
- [ ] Detect runtime `npx` fetch risk.
- [ ] Check policy parseability/dry-run safely.
- [ ] Report role cwd/allowed-roots validation.
- [ ] Report redaction probe.
- [ ] Add session-readiness probes once session support exists.

Acceptance:

- Doctor remains read-only unless a future probe explicitly states otherwise.
- Missing/invalid binaries produce structured output rather than tracebacks.

## 5. Non-functional requirements

### NFR-1 Local-first operation

- Works as a local Python library and dev CLI.
- Does not require a daemon for the base product.
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

## 7. Success metrics

- Feature completion is tracked in `docs/roadmap/features.md` and shows both one-shot exec and persistent-session support complete before the product is considered feature-complete.
- `docs/roadmap/current-status.md` has phase checklists and acceptance criteria for each active engineering stage.
- Local gates and CI pass.
- A caller can inspect redacted artifacts without parsing raw acpx streams.
- The supervisor never invents caller business verdicts.

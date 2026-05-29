---
title: "agent-run-supervisor PRD"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T11:09:05+0800
---
# agent-run-supervisor PRD

## 1. Product goal

`agent-run-supervisor` is a small, local-first Python library and dev CLI that supervises external AGENT runs through pinned `acpx@0.10.0`, converts runner/protocol chaos into normalized, redacted, auditable evidence, and leaves product/business interpretation to the caller.

The project exists so a caller such as Hermes or Sachima can ask an external AGENT role to do a bounded task without embedding acpx/ACP stream parsing, exit-code classification, permission-policy compilation, artifact hygiene, or runner lifecycle supervision in the caller project.

## 2. Users and callers

### Primary users

- Human operator/developer who runs the CLI locally.
- AI-assisted development controller such as Hermes, which invokes the CLI under explicit project scope.

### Primary caller projects

- A local dev workflow that wants reproducible, redacted AGENT-run artifacts.
- A future thin Sachima/Hermes integration that selects `role_id`, builds task context, renders progress, and interprets final AGENT output without owning acpx lifecycle.

### Non-users / non-callers in the current roadmap

- Public users over ingress.
- Messaging-platform recipients.
- Production Gateway runtime.
- Autonomous agent-to-agent routing systems.

## 3. Source of truth

Implementation requirements are governed by:

1. `docs/design/v0.1a-design.md` — current authoritative design for the V0.1 line.
2. `GOAL.md` — project north star and explicit non-approvals.
3. `docs/roadmap/current-status.md` — living state, evidence, and next allowed request.
4. `docs/roadmap/v0.1a-design-conformance.md` — current implementation-vs-design matrix.

The deprecated V0.1c manual-approval design is historical context only. It does not override `docs/design/v0.1a-design.md`.

## 4. Product principles

- **Role-bound authorization**: `AgentRoleSpec.role_id` is the stable identity and policy boundary. Permissions are bound to long-lived roles, not per-run human approval artifacts.
- **Exec-only first**: V0.1 targets one-shot `acpx exec` supervision. Persistent sessions are deferred.
- **Supervisor, not business judge**: runner/protocol completion is not business success. `business_verdict` remains `null`; callers interpret final messages using their own contracts.
- **Auditable by default**: every run writes deterministic, redacted artifacts with restrictive permissions.
- **Fail closed on uncertainty**: invalid roles, cwd outside configured roots, malformed stdout, protocol drift, permission denial, and unsafe config all produce deterministic non-success statuses.
- **Honest security claims**: `allowed_roots` validates cwd/config intent only. It is not an OS/filesystem sandbox.
- **No hidden live expansion**: docs/governance changes never imply production, ingress, real delivery, Gateway lifecycle, auto-routing, or persistent-session approval.

## 5. Functional requirements

### FR-1 AgentRoleSpec validation

The product must validate a V0.1a `AgentRoleSpec` file.

Checklist:

- [x] Validate `schema_version: 1`.
- [x] Validate runner type `acpx` and version `0.10.0`.
- [x] Validate `role_id`, `display_name`, and `description` shape.
- [x] Validate workspace `default_cwd`, non-empty `allowed_roots`, and `allowed_roots_security_boundary: false`.
- [x] Validate role permission booleans for read/search/write/execute/terminal/delete/move/fetch/switch_mode/other.
- [x] Validate `session.strategy: exec` only.
- [x] Validate positive timeout/max-turn/max-output limits.
- [x] Provide a CLI `validate-role` command returning a role hash.

Acceptance:

- Invalid fields fail deterministically before runner or artifact work.
- Tests cover valid and invalid role shapes.

### FR-2 Role-bound acpx policy and argv compilation

The product must compile a validated role into a pinned acpx invocation and permission policy.

Checklist:

- [x] Compile allowed permissions to acpx `autoApprove`.
- [x] Compile denied permissions to acpx `autoDeny`.
- [x] Set `defaultAction=deny`.
- [x] Add `--non-interactive-permissions fail`.
- [x] Add automation flags `--format json --json-strict --suppress-reads --timeout <seconds> --max-turns <n>`.
- [x] Add `--no-terminal` when role terminal permission is false.
- [x] Include role model when set.
- [x] Persist redacted `command.argv.json` and `generated-policy.json` in dry-run artifacts.
- [ ] Confirm final real `run` path uses the same compiler and flags as dry-run artifacts.

Acceptance:

- Golden tests prove policy and argv shape.
- No business-only permission such as delivery, Gateway lifecycle, production config, or `@all` exists in `AgentRoleSpec`.

### FR-3 cwd / allowed_roots validation

The product must validate the effective cwd against role workspace intent before artifact creation and launch.

Checklist:

- [x] Resolve effective cwd from explicit `--cwd` or role `workspace.default_cwd`.
- [x] Fail closed before artifact creation when cwd is outside configured `allowed_roots`.
- [x] Persist the disclaimer that `allowed_roots` is not a sandbox.
- [x] Test cwd inside root, outside root, and default cwd behavior.

Acceptance:

- cwd gate failure creates no run artifacts.
- Docs and artifacts do not claim OS-level isolation.

### FR-4 Exec-only runner supervision

The product must supervise exactly one acpx exec subprocess for a real run when that phase is approved.

Checklist:

- [ ] CLI `run` without `--no-real-run` invokes the exec-only subprocess runner.
- [ ] Capture stdout/stderr from the real subprocess into the EventStore.
- [ ] Pass the role-compiled argv/policy to the subprocess.
- [ ] Run under the validated effective cwd.
- [ ] Preserve start/end timestamps, acpx version, role hash, policy hash, exit code, signal, and timeout/kill metadata.
- [ ] Use an outer watchdog longer than acpx `--timeout`.
- [ ] On watchdog expiry, terminate process group where supported, wait grace, then kill and record metadata.
- [x] Provide a `finalize_outcome()` path that can classify and persist fake subprocess outcomes for tests.
- [x] Keep current CLI real-run behavior refused until the true runner path is implemented and approved.

Acceptance:

- Fake subprocess tests prove stdout/stderr capture, status classification, watchdog metadata, and artifact layout.
- A minimal real `acpx@0.10.0` smoke can run in a scratch repo with no tools/no edits once real launch is approved for this repo.

### FR-5 Exit classification and status model

The product must convert acpx/runner exit behavior into supervisor-owned statuses.

Checklist:

- [x] Implement `completed`.
- [x] Implement `runner_error`.
- [x] Implement `invalid_invocation`.
- [x] Implement `timed_out`.
- [x] Implement `no_session`.
- [x] Implement `permission_denied`.
- [x] Implement `interrupted`.
- [x] Implement `protocol_error`.
- [x] Implement `infrastructure_error`.
- [x] Implement `policy_error` enum value.
- [x] Map exit `0/1/2/3/4/5/130/unknown`.
- [x] Let JSON-RPC/acpx error metadata refine bare exit-code status.
- [x] Keep nonzero exits from becoming `completed`.

Acceptance:

- Table-driven tests cover all statuses and representative metadata refinements.

### FR-6 Observed stdout parser and normalized events

The product must parse only the observed acpx stdout schema captured by Phase -1 fixtures.

Checklist:

- [x] Parse newline-delimited JSON-RPC records observed from acpx `0.10.0`.
- [x] Assemble `final_message` from ordered `agent_message_chunk` updates.
- [x] Extract usage updates.
- [x] Emit normalized events for run start, message deltas, tools, usage, permission request/denial, unknown updates, completion/failure.
- [x] Fail closed on malformed JSON and non-JSON-RPC envelopes.
- [x] Preserve unknown update type and key/type summary only.
- [x] Enforce `max_output_bytes` and mark truncation as protocol error.
- [x] Keep `business_verdict` as `null`.

Acceptance:

- Fixture replay tests cover success, malformed stream, unknown update, permission denied, no-session, timeout/runtime/usage/interrupted fixtures where captured.

### FR-7 EventStore and redaction

The product must write auditable run artifacts safely.

Checklist:

- [x] Create run directories with mode `0700`.
- [x] Write final JSON artifacts with mode `0600`.
- [x] Use atomic write/rename for final JSON/text artifacts.
- [x] Append stream artifacts as JSONL/NDJSON.
- [x] Write metadata, prompt, redacted env, command argv, generated policy, stdout, normalized events, stderr, result, and redaction report.
- [x] Redact prompt, env, argv, metadata, stderr, stdout, normalized event text payloads, and final message surfaces.
- [ ] Add retention/cleanup knobs before long-lived use.
- [ ] Add explicit unsafe raw-capture opt-in if raw preservation is ever needed.

Acceptance:

- Permission tests prove modes.
- Redaction tests prove secret-shaped data is not persisted in user-facing artifacts.

### FR-8 CLI surface

The product must expose a small dev CLI.

Checklist:

- [x] `validate-role <role-file>`.
- [x] `replay <events.ndjson>`.
- [x] `doctor`.
- [x] `run --role <role-file> --prompt-file <file> [--cwd <dir>] --no-real-run`.
- [ ] Final V0.1a `run --role <role-file> --prompt-file <file> [--cwd <dir>]` executes a real exec-only acpx run under the V0.1a runner contract.
- [x] Current non-approved real-run path returns stable refusal and starts no process.

Acceptance:

- CLI smoke tests cover help, invalid inputs, no-real-run artifacts, real-run refusal, doctor, and replay.

### FR-9 Doctor / environment probe

The product must diagnose local readiness without launching real agents.

Checklist:

- [x] Probe Python version.
- [x] Probe Node version and minimum requirement.
- [x] Probe acpx binary/version, including role-specific `runner.acpx_binary`.
- [x] Replay fixture through parser.
- [x] Probe EventStore permissions.
- [ ] Probe adapter availability.
- [ ] Detect whether runtime `npx` fetch would occur.
- [ ] Check policy parseability/dry-run without launching an agent.
- [ ] Include cwd/allowed_roots validation result for a role when provided.
- [ ] Include explicit redaction probe result.

Acceptance:

- Doctor remains read-only and never invokes `acpx exec`.
- Missing/invalid binaries produce structured output rather than tracebacks.

## 6. Non-functional requirements

### NFR-1 Safety and redaction

- No secrets, tokens, cookies, raw env values, signed URLs, or platform private IDs may be committed or displayed.
- Artifact outputs must be redacted by default.
- Secret-shaped scan must run before PR.

### NFR-2 Determinism

- Fixture replay must be deterministic.
- Result schema must remain stable enough for callers to consume.

### NFR-3 Local-first operation

- V0.1 targets local Python stdlib + dev CLI behavior.
- No daemon or background service is required.

### NFR-4 Testability

- Behavior should be covered by pytest, compileall, fixture validation, CLI smoke, docs gates, and CI.
- Real external runner behavior must be isolated behind fake subprocess tests until real-run smoke is explicitly approved.

## 7. Explicit non-goals / non-approvals

The current V0.1 line does not approve:

- persistent sessions;
- session registry, locking, stale-lock recovery, or multi-turn context retention;
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

## 8. Success metrics

- All V0.1a design requirements are marked complete in `docs/roadmap/v0.1a-design-conformance.md`.
- `python3 -m pytest -q`, compileall, fixture validator, doctor/replay smoke, docs index/drift, and CI pass.
- A caller can invoke one role-bound exec-only run and inspect redacted artifacts without parsing raw acpx streams.
- No product/business verdict is inferred by the supervisor.
- Roadmap docs no longer point future work toward the deprecated manual-approval branch.

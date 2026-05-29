---
title: "agent-run-supervisor Technical Solution"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T11:09:05+0800
---
# agent-run-supervisor Technical Solution

## 1. Architecture summary

`agent-run-supervisor` is an independent local Python library plus dev CLI. It sits between a caller project and `acpx@0.10.0`.

```text
Caller project / human operator
  -> chooses AgentRoleSpec, task prompt/context, cwd, and business contract
agent-run-supervisor
  -> validates role/cwd, compiles policy/argv, supervises one exec-only acpx run,
     parses observed stdout, classifies status, writes redacted artifacts
acpx@0.10.0
  -> ACP runner/client boundary
External AGENT
  -> Codex, Claude Code, or another ACP-capable worker/reviewer
```

The supervisor owns runner supervision and evidence. It does not own product meaning, IM delivery, Gateway lifecycle, or persistent AGENT sessions.

## 2. Authoritative design alignment

This solution implements `docs/design/v0.1a-design.md` as the current source of truth.

Important corrections after the V0.1c drift:

- Authorization is role-bound through `AgentRoleSpec`, not per-run human approval.
- `AgentRoleSpec.role_id` is stable identity and policy boundary.
- A run may have a run-intent/audit record, but that record is not a human approval ticket.
- Persistent sessions remain a separately approved future phase.

## 3. Module design

### 3.1 `role.py` — AgentRoleSpec model and validation

Responsibilities:

- Parse a mapping into immutable dataclass specs.
- Validate schema version, runner, workspace, permissions, session, limits, prompt, and redaction flags.
- Reject `allowed_roots_security_boundary=true` for V0.1a.
- Compute canonical `role_hash`.

Current status: implemented.

### 3.2 `policy.py` — acpx policy and argv compiler

Responsibilities:

- Convert role permissions into acpx policy JSON:
  - allowed role permissions -> `autoApprove`;
  - denied role permissions -> `autoDeny`;
  - `defaultAction=deny`.
- Build the pinned acpx argv:
  - binary or `npx -y acpx@0.10.0`;
  - `--format json`;
  - `--json-strict`;
  - `--suppress-reads`;
  - `--timeout <limits.timeout_seconds>`;
  - `--max-turns <limits.max_turns>`;
  - `--cwd <effective_cwd>`;
  - `--permission-policy <generated-policy-json>`;
  - `--non-interactive-permissions fail`;
  - `--no-terminal` when terminal is false;
  - optional `--model`;
  - `<adapter_agent> exec <prompt>`.

Current status: implemented for dry-run and future runner path.

### 3.3 `workspace.py` — cwd/allowed_roots gate

Responsibilities:

- Resolve the effective cwd.
- Validate the cwd is under configured roots.
- Fail before artifact creation if outside.
- Preserve the explicit disclaimer that this is not a sandbox.

Current status: implemented.

### 3.4 `preflight.py` — doctor probes

Responsibilities:

- Probe Node version without launching an agent.
- Probe acpx version without launching an agent.
- Honor role-specific `runner.acpx_binary`.
- Return structured diagnostics.

Current status: partially implemented. Still missing adapter availability, `npx` fetch detection, policy parseability/dry-run, role cwd probe, and explicit redaction probe.

### 3.5 `parser.py` — observed acpx stdout parser

Responsibilities:

- Parse only the observed newline-delimited JSON-RPC schema captured in Phase -1 fixtures.
- Convert updates into normalized event dicts.
- Assemble `final_message` from ordered text deltas.
- Preserve unknown update type plus key/type summaries only.
- Fail closed on malformed framing.
- Enforce `max_output_bytes`.
- Keep `business_verdict=None`.

Current status: implemented for the observed JSON-RPC fixture family.

### 3.6 `exit_classifier.py` — supervisor status classifier

Responsibilities:

- Map acpx/runner exit code and metadata to `AgentRunStatus`.
- Handle protocol errors on exit 0.
- Refine generic exit 1 with `acpxCode` and origin.
- Separate supervisor-origin kill/timeout from acpx exit 3.

Current status: implemented.

### 3.7 `event_store.py` — run artifact store

Responsibilities:

- Create run directory under `.agent-run-supervisor/runs/<run_id>/`.
- Enforce mode `0700` for run directories.
- Enforce mode `0600` for final artifacts.
- Use atomic writes for final JSON/text artifacts.
- Append NDJSON/JSONL stream artifacts.

Current status: implemented.

### 3.8 `redaction.py` — redaction helpers

Responsibilities:

- Redact prompt, stderr, env, argv, metadata, observed stdout, normalized events, and final result surfaces.
- Produce a redaction report without exposing secret values.

Current status: implemented for current artifact surfaces; should remain required for new runner paths.

### 3.9 `runner.py` — runner orchestration

Responsibilities in the final V0.1a solution:

1. Validate effective cwd.
2. Prepare run artifacts and metadata.
3. Compile acpx argv/policy from the role.
4. Spawn one exec-only subprocess under the effective cwd.
5. Capture stdout/stderr.
6. Enforce outer watchdog and process-group termination.
7. Parse stdout and persist normalized events.
8. Classify exit/result.
9. Persist `result.json` and redaction report.
10. Return a stable `RunOutcome`.

Current status:

- `dry_run()` is implemented.
- `finalize_outcome()` is implemented for supplied/fake subprocess outcomes.
- A true subprocess launch path is not yet connected to CLI `run`.
- Watchdog/process-group lifecycle is not yet implemented.

### 3.10 `commands.py` and `cli.py` — dev CLI

Responsibilities:

- `validate-role` validates and hashes roles.
- `replay` parses observed stdout fixtures.
- `doctor` probes environment/fixtures without launch.
- `run` compiles/executes according to the approved V0.1a behavior.

Current status:

- validate-role/replay/doctor implemented.
- `run --no-real-run` implemented.
- `run` without `--no-real-run` currently refuses real launch; this is safe but incomplete relative to `v0.1a-design.md`.

## 4. Data models

### 4.1 AgentRoleSpec

`AgentRoleSpec` is the stable authorization boundary.

Key fields:

- `role_id`
- `runner.type`
- `runner.acpx_version`
- `runner.acpx_binary`
- `runner.adapter_agent`
- `workspace.default_cwd`
- `workspace.allowed_roots`
- `workspace.allowed_roots_security_boundary`
- `permissions.*`
- `session.strategy`
- `limits.timeout_seconds`
- `limits.max_turns`
- `limits.max_output_bytes`
- `prompt.role_instruction`
- `prompt.output_contract`
- `redaction.*`

### 4.2 Normalized events

Supported V0.1a event families:

- `run_started`
- `agent_message_delta`
- `tool_started`
- `tool_updated`
- `tool_completed`
- `usage_updated`
- `permission_requested`
- `permission_denied`
- `unknown_update`
- `run_completed`
- `run_failed`

The parser may emit additional implementation-internal observed events such as `session_new_requested` and `session_prompt_sent`; caller-facing compatibility must be documented before external consumers depend on them.

### 4.3 Result payload

Minimum result fields:

- `run_id`
- `status`
- `business_verdict`
- `error_code`
- `detail_code`
- `origin`
- `retryable`
- `acpx_exit_code`
- `signal`
- `stop_reason`
- `usage`
- `final_message`
- `truncated`
- `truncate_reason`
- `run_dir`
- `stderr_path`
- `raw_event_path`
- `redaction_report_path`

`business_verdict` stays `null` in supervisor output.

## 5. Run artifact layout

```text
.agent-run-supervisor/runs/<run_id>/
  metadata.json
  prompt.txt
  env.redacted.json
  command.argv.json
  generated-policy.json
  acpx-stdout.ndjson
  normalized-events.jsonl
  stderr.log
  result.json
  redaction-report.json
```

All paths are local artifacts. They must not be treated as public or user-facing output without caller-side rendering/redaction review.

## 6. Real exec-only runner design

The missing V0.1a runner completion should follow this algorithm:

1. Load and validate role.
2. Read prompt file.
3. Resolve and validate cwd.
4. Create run directory.
5. Compile command/policy.
6. Start subprocess with `cwd=effective_cwd`, no shell, controlled env snapshot, and captured stdout/stderr.
7. Use an outer watchdog of `timeout_seconds + grace_seconds`.
8. On watchdog timeout:
   - terminate process group where supported;
   - wait a fixed grace;
   - kill if still running;
   - record `kill_reason`, `kill_signal`, `grace_ms`, `process_group_used`, and truncation/closure flags.
9. Persist stdout/stderr redacted.
10. Parse stdout with the observed-schema parser.
11. Classify status.
12. Persist result and redaction report.
13. Return JSON to CLI.

No shell interpolation is allowed. All argv components must be passed as a list.

## 7. Security boundaries

### Enforced in current project

- Role validation.
- cwd/allowed_roots intent gate.
- acpx policy generation with default deny.
- non-interactive permissions fail mode.
- artifact permissions.
- redaction.
- explicit refusal for not-yet-approved real launch.

### Not claimed

- OS/filesystem sandboxing.
- Production authorization.
- Business PASS/BLOCK.
- Platform delivery or Gateway lifecycle safety.
- Agent-to-agent containment.

## 8. Testing strategy

Required layers:

1. Role schema tests.
2. Policy compiler golden tests.
3. Workspace gate tests.
4. Exit classifier table tests.
5. Parser fixture replay tests.
6. EventStore permission and atomic-write tests.
7. Redaction tests.
8. Runner fake-subprocess tests, including watchdog behavior.
9. CLI smoke tests.
10. Fixture validator.
11. One minimal real acpx smoke after the real-run phase is explicitly approved.
12. Docs index/drift and secret/static scans.

Current automated tests: 123 collected tests pass on `main` at `cc86f8cdc42221a2a2750f8404fb6193b3dba279`.

## 9. Integration boundary

Future Sachima/Hermes integration should remain thin:

```text
mention/operator action -> role_id
caller builds context pack/prompt
caller invokes agent-run-supervisor run
caller renders normalized progress/events
caller interprets final_message under its own business contract
```

The caller must not parse raw ACP/acpx stdout directly, own runner lifecycle, or infer external delivery approval from a supervisor result.

## 10. Deprecated branch: manual approval design

The V0.1c HITL/manual approval design introduced per-run approval semantics that conflict with the project-level decision that authorization is role-bound. It is historical context only and should not drive implementation unless the user explicitly reverses the role-bound authorization decision.

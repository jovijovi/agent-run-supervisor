---
title: "agent-run-supervisor Technical Solution"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T12:20:00+0800
---
# agent-run-supervisor Technical Solution

## 1. Architecture summary

`agent-run-supervisor` is an independent local Python library plus dev CLI between caller projects and the ACP/acpx runner boundary.

```text
Caller project / human operator
  -> selects AgentRoleSpec, prompt/context, cwd, execution mode, and business contract
agent-run-supervisor
  -> validates role/workspace, compiles policy/argv, supervises exec or session lifecycle,
     parses observed events, classifies status, writes redacted artifacts
acpx / ACP runner boundary
  -> starts one-shot exec runs or persistent sessions
External AGENT
  -> Codex, Claude Code, or another ACP-capable worker/reviewer
```

The supervisor owns runner/session lifecycle and evidence. It does not own product meaning, IM delivery, Gateway lifecycle, or public routing.

## 2. Document authority

The previous mixed `docs/design/v0.1a-design.md` file has been decomposed and retired. Its requirements now live in:

- PRD and product requirements: `docs/product/prd.md`
- Technical solution: this file
- Feature completion matrix: `docs/roadmap/features.md`
- Roadmap, phases, acceptance criteria: `docs/roadmap/current-status.md`

The previous V0.1c manual-approval design is deleted as stale history. Role-bound authorization remains the design direction.

## 3. Core modules

### 3.1 `role.py` — AgentRoleSpec model

Responsibilities:

- Parse and validate role identity, runner config, workspace intent, permissions, session config, limits, prompt contract, and redaction policy.
- Compute canonical role hash.
- Reject unknown/unsafe claims such as `allowed_roots_security_boundary=true` unless a future sandbox design proves it.

Design notes:

- `role_id` is the stable authorization boundary.
- Role permissions describe maximum acpx-controllable behavior.
- Session configuration must eventually represent both one-shot exec and persistent-session use while preserving role-bound authorization.

Current status: implemented for current exec-shaped role config; persistent-session fields are not complete.

### 3.2 `policy.py` — acpx policy and argv compiler

Responsibilities:

- Convert role permissions into acpx policy JSON:
  - allowed permissions -> `autoApprove`;
  - denied permissions -> `autoDeny`;
  - default action -> deny.
- Build acpx argv as a list, never a shell string.
- Apply automation flags such as JSON output, strict parsing, read suppression, timeout, max turns, cwd, generated policy, non-interactive permission failure, optional model, and terminal disabling.
- Compile mode-specific command tails for exec and future persistent-session operations.

Current status: implemented for current dry-run and future exec path. Session-mode compilation is future work after command fixtures are captured.

### 3.3 `workspace.py` — cwd and workspace gate

Responsibilities:

- Resolve effective cwd from CLI input or role default.
- Validate cwd under configured allowed roots.
- Fail before artifact creation if invalid.
- Re-check role/workspace identity when attaching to persistent sessions.

Security claim: this is configuration/cwd intent validation, not OS/filesystem sandboxing.

Current status: cwd gate implemented for current run path; session reattach checks are future work.

### 3.4 `preflight.py` — doctor probes

Responsibilities:

- Probe Python, Node, acpx binary/version, adapter availability, policy parseability, fixture replay, EventStore permissions, cwd validation, redaction behavior, and session readiness.
- Stay read-only unless a future probe explicitly documents otherwise.
- Return structured diagnostics.

Current status: Python/Node/acpx basics, role-specific acpx binary probe, fixture replay, and EventStore permission probe are partially implemented.

### 3.5 `runner.py` — one-shot exec supervision

Responsibilities:

1. Load and validate role.
2. Read prompt file.
3. Resolve and validate cwd.
4. Create run artifact directory.
5. Compile argv/policy.
6. Spawn acpx exec with `cwd=effective_cwd`, no shell, controlled environment, and captured stdout/stderr.
7. Enforce an outer watchdog longer than acpx timeout.
8. On watchdog expiry: terminate process group where supported, wait grace, force kill if still running, record kill metadata.
9. Persist redacted stdout/stderr.
10. Parse observed stdout and normalized events.
11. Classify result and persist final artifacts.

Current status: dry-run and supplied/fake outcome finalization are implemented; true subprocess launch and watchdog are not complete.

### 3.6 `session.py` / future session store — persistent-session supervision

Responsibilities:

1. Fixture-prove acpx persistent-session command grammar and stream shapes.
2. Create/open sessions with validated role/workspace metadata.
3. Persist session id, role hash, workspace hash, acpx version, policy hash, cwd, state, lease/lock metadata, and redacted transcript/event artifacts.
4. Send prompts to existing sessions only if role/workspace/session metadata still match.
5. Prevent concurrent unsafe session use through locks or leases.
6. Detect stale locks and recover deterministically.
7. Close/abort sessions with explicit status and artifact evidence.
8. Prevent cross-role, cross-workspace, or stale-session leakage.

Current status: not implemented. This is product-required future work, not a PRD/DESIGN non-goal.

### 3.7 `parser.py` — observed event parser

Responsibilities:

- Parse only fixture-proven observed acpx stdout/event schemas.
- Convert updates into normalized event dicts.
- Assemble final messages from ordered text deltas.
- Preserve unknown update type plus key/type summaries only.
- Fail closed on malformed framing or max-output overflow.

Current status: implemented for the current exec fixture family. Persistent-session parser coverage is future work.

### 3.8 `exit_classifier.py` — status classifier

Responsibilities:

- Map acpx/runner/session exit behavior to supervisor-owned statuses.
- Ensure nonzero exits never become completed.
- Distinguish supervisor-origin failures from acpx-origin failures.
- Keep caller business verdict separate from supervisor status.

Current statuses:

```text
completed
runner_error
invalid_invocation
timed_out
no_session
permission_denied
interrupted
protocol_error
infrastructure_error
policy_error
```

Current status: implemented for current exit-code model; session lifecycle detail may need extensions.

### 3.9 `event_store.py` and `redaction.py` — artifact store

Run/session artifact responsibilities:

- Create local artifact roots with restrictive permissions.
- Use atomic writes for final artifacts.
- Append streams as JSONL/NDJSON.
- Redact prompt, env, argv, metadata, stdout, stderr, normalized event text, and final messages.
- Produce redaction reports.
- Add retention/cleanup knobs before long-lived operation.

Current status: run artifacts and redaction are implemented for current surfaces; session artifact layout and retention controls are future work.

### 3.10 `commands.py` / `cli.py` — dev CLI

CLI responsibilities:

```text
agent-run-supervisor validate-role <role-file>
agent-run-supervisor run --role <role-file-or-id> --prompt-file <file> [--cwd <dir>]
agent-run-supervisor run --role <role-file-or-id> --prompt-file <file> [--cwd <dir>] --no-real-run
agent-run-supervisor replay <events.ndjson>
agent-run-supervisor doctor [--role <role-file>]
# Future session surface after session design/fixtures:
agent-run-supervisor session create|send|status|close|abort ...
```

Current status: validate-role, replay, doctor baseline, and dry-run are implemented; real exec and persistent session commands remain incomplete.

## 4. Data models

### 4.1 AgentRoleSpec

Required conceptual fields:

- identity: `role_id`, `display_name`, `description`
- runner: type, acpx version/binary, adapter agent, model
- workspace: default cwd, allowed roots, sandbox-boundary disclaimer
- permissions: acpx-controllable permission booleans
- session: execution mode/config for exec or persistent sessions
- limits: timeout, max turns, max output, session TTL/lease where applicable
- prompt contract: role instruction and output contract
- redaction: prompt/stderr/metadata/env/event redaction flags

### 4.2 Normalized events

Base event families:

- run/session lifecycle start/update/complete/failure
- agent message delta
- tool start/update/complete
- usage update
- permission requested/denied
- unknown update summary
- watchdog/kill/lifecycle metadata events

### 4.3 Result payload

Minimum result concepts:

- run/session id
- status
- business verdict placeholder/null
- error/detail code
- origin
- retryable flag
- acpx exit code/signal where applicable
- stop reason
- usage
- final message or session update summary
- truncation info
- artifact paths
- redaction report path

`business_verdict` remains caller-owned.

## 5. Artifact layout

Current run layout:

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

Future session layout should be explicit before implementation. A likely shape:

```text
.agent-run-supervisor/sessions/<session_id>/
  session.json
  role.snapshot.json
  workspace.snapshot.json
  generated-policy.json
  lock.json
  turns/<turn_id>/
    prompt.txt
    acpx-stdout.ndjson
    normalized-events.jsonl
    stderr.log
    result.json
    redaction-report.json
```

Exact file names remain design details until session fixtures and API are approved in the roadmap.

## 6. Security and lifecycle boundaries

Enforced or required:

- role validation;
- cwd/workspace intent gate;
- acpx policy generation with default deny;
- non-interactive permission failure;
- no shell interpolation;
- artifact permission controls;
- redaction by default;
- watchdog and kill metadata for exec;
- lock/lease/stale-lock handling for persistent sessions.

Not claimed:

- OS/filesystem sandboxing;
- production authorization;
- caller business PASS/BLOCK;
- platform delivery or Gateway lifecycle safety;
- agent-to-agent containment.

## 7. Testing strategy

Required layers:

1. Role schema tests.
2. Policy compiler golden tests.
3. Workspace gate tests.
4. Exit/status classifier tests.
5. Parser fixture replay tests.
6. EventStore permission and atomic-write tests.
7. Redaction tests.
8. Exec runner fake-subprocess and watchdog tests.
9. Persistent-session fixture and lifecycle tests.
10. CLI smoke tests.
11. Fixture validator.
12. Minimal real acpx smoke for approved local execution phases.
13. Docs index/drift, secret scans, and static scans.

## 8. Integration boundary

Future Sachima/Hermes integration should remain thin:

```text
caller action -> role_id + execution mode
caller builds context pack/prompt
caller invokes agent-run-supervisor
caller renders normalized progress/events
caller interprets final output under its own business contract
```

The caller must not parse raw ACP/acpx streams directly or infer external delivery approval from supervisor results.

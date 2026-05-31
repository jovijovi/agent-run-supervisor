---
title: "agent-run-supervisor Technical Solution"
status: active
created_at: 2026-05-29
last_validated_at: 2026-05-29T18:25:40+0800
---
# agent-run-supervisor Technical Solution

## 1. Architecture summary

> **System-level view:** the multi-level architecture (context, container/component,
> exec/session lifecycles, data-flow, and trust boundaries — with diagrams) lives in
> `docs/design/architecture.md`. **This file is the module-level companion**: per-file
> responsibilities, data models, artifact layout, and the testing strategy.

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

The previous mixed V0.1a design file has been decomposed and retired. Its durable requirements now live in:

- PRD and product requirements: `docs/product/prd.md`
- System architecture (diagrams + boundaries): `docs/design/architecture.md`
- Technical solution (module detail): this file
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

Current status: implemented for exec-shaped role config, S1b persistent-session
configuration (`strategy: persistent` with bounded lease settings), and the S1c local
create/send/status runtime MVP. Close/abort/list, real-acpx smoke, full crash recovery,
and retention cleanup remain future work.

### 3.2 `policy.py` — acpx policy and argv compiler

Responsibilities:

- Convert role permissions into acpx policy JSON:
  - allowed permissions -> `autoApprove`;
  - denied permissions -> `autoDeny`;
  - default action -> deny.
- Build acpx argv as a list, never a shell string.
- Apply automation flags such as JSON output, strict parsing, read suppression, timeout, max turns, cwd, generated policy, non-interactive permission failure, optional model, and terminal disabling.
- Compile mode-specific command tails for exec and persistent-session operations.
- Compile fixture-proven persistent-session commands: `compile_session_create/ensure/show/status/prompt_command`
  for `sessions new/ensure/show`, `status -s`, and `prompt -s`.

Current status: implemented for current dry-run and real exec paths. S1b adds a
fail-closed guard so persistent-session roles cannot accidentally launch through the
one-shot exec compiler. S1c adds the persistent-session command compilers and an
`ensure_persistent_strategy` guard that mirrors the exec guard so persistent roles compile
only through the session path. The prompt-turn compiler uses the S1a fixture-proven
`--deny-all` prompt block (`prompt -s <name> <prompt>`), while the local record remains
role/policy-bound and is revalidated before every send; expanding prompt-turn tool
permissions requires a later fixture-proven slice. The prompt stays a single argv element
with no shell. Close/abort command compilation remains future work.

### 3.3 `workspace.py` — cwd and workspace gate

Responsibilities:

- Resolve effective cwd from CLI input or role default.
- Validate cwd under configured allowed roots.
- Fail before artifact creation if invalid.
- Re-check role/workspace identity when attaching to persistent sessions.

Security claim: this is configuration/cwd intent validation, not OS/filesystem sandboxing.

Current status: cwd gate implemented for current run path. S1b adds deterministic
`workspace_hash(...)` for session binding; S1c revalidates the role/workspace/policy/acpx
binding before `send` and `status` and refuses mismatches before subprocess or artifact
mutation. Full close/abort/crash-recovery lifecycle checks remain future work.

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

Current status: one-shot subprocess launch, stdout/stderr capture, parser/classifier finalization, and watchdog kill metadata are implemented for local exec. S1c adds separate local persistent-session create/send/status supervision in `session_runtime.py`; close/abort/list and full crash/interruption recovery remain future work.

### 3.6 `session.py` / `session_runtime.py` — persistent-session store and runtime

Store responsibilities (`session.py`):

1. Fixture-prove acpx persistent-session command grammar and stream shapes.
2. Create local session records with validated role/workspace metadata.
3. Persist session id, optional acpx session id/name, role hash, workspace hash, acpx version, policy hash, cwd, state, and lifecycle metadata.
4. Validate stored session bindings before any reattach/resume mutation.
5. Prevent concurrent unsafe session use through lease locks.
6. Detect expired locks and recover deterministically by replacing the lease.
7. Define later close/abort runtime semantics with explicit status and artifact evidence.
8. Prevent cross-role, cross-workspace, stale-policy, acpx-version, or adapter mismatch leakage.

Runtime responsibilities (`session_runtime.py`, `SessionRuntime`):

1. Drive S1c `create_session`/`send`/`status` over the store: validate the persistent role
   and workspace, run fixture-shaped acpx management/turn commands through a subprocess
   executor, and bind the local record to role/workspace/policy/acpx/adapter identity.
2. Re-open and re-validate the binding before every `send`/`status` mutation.
3. Acquire the lease lock for a `send` turn and release it on success **and** failure.
4. Persist redacted prompt-turn artifacts under `sessions/<session_id>/turns/<turn_id>/` and
   redacted management summaries under `sessions/<session_id>/management/`.
5. Parse prompt-turn NDJSON with the fixture-proven parser, classify supervisor status, and
   keep `business_verdict` null.
6. Fail closed — never create or update the local record — when a management command exits
   non-zero, returns unparseable output, or reports an error envelope.

Current status: S1b implements the local session store, binding validation, and lease-lock
foundation in `session.py`. S1c adds `session_runtime.py` with a create/send/status MVP over
that store: fixture/fake-executor acceptance (no real acpx launch yet), lease release on
success and failure, binding-mismatch refusal before any subprocess or artifact mutation, and
redacted turn/management artifacts. Close/abort runtime and semantics, session `list`,
multi-turn resume, full crash/interruption recovery, and retention/cleanup remain future work;
this is **not** full S1 completion.

### 3.7 `parser.py` — observed event parser

Responsibilities:

- Parse only fixture-proven observed acpx stdout/event schemas.
- Convert updates into normalized event dicts.
- Assemble final messages from ordered text deltas.
- Preserve unknown update type plus key/type summaries only.
- Fail closed on malformed framing or max-output overflow.
- Summarize single-object acpx management-command JSON (`sessions new/ensure/show`, `status`)
  into a safe allow-listed summary via `summarize_management_json`, kept on a separate parser
  from the prompt-turn NDJSON path.

Current status: implemented for the current exec fixture family. S1c adds
`summarize_management_json`/`ManagementParseError` for single-object session management JSON,
separate from the prompt-turn NDJSON parser (`parse_acpx_stdout_bytes`). The management
summarizer returns only allow-listed fields plus top-level key/type evidence (never bulk
payload) and fails closed on empty, non-JSON, non-object, or JSON-RPC stream records fed to
the management path. Broader persistent-session parser/event coverage is future work.

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

Current status: run artifacts and redaction are implemented for current surfaces. S1b adds
the local `sessions/<session_id>/session.json` and `lock.json` foundation. S1c adds
redacted session turn artifacts under `sessions/<session_id>/turns/<turn_id>/` and
redacted management summaries under `sessions/<session_id>/management/`; retention controls
and session cleanup policy remain future work.

### 3.10 `commands.py` / `cli.py` — dev CLI

CLI responsibilities:

```text
agent-run-supervisor validate-role <role-file>
agent-run-supervisor run --role <role-file-or-id> --prompt-file <file> [--cwd <dir>]
agent-run-supervisor run --role <role-file-or-id> --prompt-file <file> [--cwd <dir>] --no-real-run
agent-run-supervisor replay <events.ndjson>
agent-run-supervisor doctor [--role <role-file>]
agent-run-supervisor session create --role <role-file> --session-id <id> [--session-name <name>] [--cwd <dir>]
agent-run-supervisor session send   --role <role-file> --session-id <id> --prompt-file <file> [--cwd <dir>]
agent-run-supervisor session status --role <role-file> --session-id <id> [--cwd <dir>]
# Future session surface: session close|abort|list ...
```

Current status: validate-role accepts exec and persistent role strategies; replay, doctor
baseline, dry-run, and local one-shot real exec are implemented. The `run` path refuses
persistent roles before artifacts/process launch. S1c adds the `session create|send|status`
MVP (JSON stdout, 0/nonzero exit codes) over `SessionRuntime`; `session close|abort|list`
remain future work.

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

Current S1b/S1c session layout:

```text
.agent-run-supervisor/sessions/<session_id>/
  session.json
  lock.json            # present only while a lease is held
  management/
    create.json
    status.json
  turns/<turn_id>/
    prompt.txt
    acpx-stdout.ndjson
    normalized-events.jsonl
    stderr.log
    result.json
    redaction-report.json
```

Future runtime extensions should extend the current layout, not replace it:

```text
.agent-run-supervisor/sessions/<session_id>/
  session.json
  role.snapshot.json   # future runtime snapshot, if needed
  workspace.snapshot.json
  generated-policy.json
  close.json           # future close/abort/list evidence, if implemented
  retention.json       # future cleanup policy/evidence, if implemented
```

The foundation filenames are implemented in S1b, and S1c implements create/send/status
management and turn artifacts. Snapshots, close/abort/list evidence, cleanup, and retention
remain future S1/H1 work.

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

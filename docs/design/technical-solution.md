---
title: "agent-run-supervisor Technical Solution"
status: active
created_at: 2026-05-29
last_validated_at: 2026-07-08T03:30:00+0800
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
configuration (`strategy: persistent` with bounded lease settings), the S1c local
create/send/status runtime MVP, the S1d close/abort/list lifecycle slice, and S1 closure
acceptance evidence for real-acpx smoke plus two-turn continuity. H1 delivered retention
cleanup; K1 adds process-liveness crash recovery on `main` via PR #22 (`0ad531e`)
without changing the role schema.

### 3.2 `policy.py` — acpx policy and argv compiler

Responsibilities:

- Convert role permissions into acpx policy JSON:
  - allowed permissions -> `autoApprove`;
  - denied permissions -> `autoDeny`;
  - default action -> deny.
- Build acpx argv as a list, never a shell string.
- Apply automation flags such as JSON output, strict parsing, read suppression, timeout, max turns, cwd, generated policy, non-interactive permission failure, optional model, and terminal disabling.
- Compile mode-specific command tails for exec and persistent-session operations.
- Compile fixture-proven persistent-session commands:
  `compile_session_create/ensure/show/status/prompt/close/cancel_command` for
  `sessions new/ensure/show`, `status -s`, `prompt -s`, `sessions close`, and `cancel -s`.

Current status: implemented for current dry-run and real exec paths. S1b adds a
fail-closed guard so persistent-session roles cannot accidentally launch through the
one-shot exec compiler. S1c adds the persistent-session command compilers and an
`ensure_persistent_strategy` guard that mirrors the exec guard so persistent roles compile
only through the session path. Since S2 the prompt-turn compiler
(`prompt -s <name> <prompt>`) derives its permission block from the role: a role granting
at least one permission kind compiles the same `--permission-policy` JSON as the exec path
(the flag sits in the shared global flag family proven by
`permission-policy-deny-all-sentinel`), while a role granting no kinds keeps the stricter
S1a fixture-proven `--deny-all` shape; a live permissioned-prompt fixture capture remains
an operator follow-up. The local record stays role/policy-bound and is revalidated before
every send. The prompt stays a single argv element with no shell. S1d adds management-only
close/cancel compilers pinned to the S1a `session-close-named` and
`session-cancel-no-active` fixtures. S2 also adds `goal.py` — fail-closed composition of
goal-setting slash prompts (`compose_goal_prompt` → `/goal <text>`, `is_slash_prompt`
detection recorded as the additive turn key `prompt_kind`) plus adapter-aware goal
compilation (`compile_goal_prompt`): adapters not registered in the (initially empty,
fixture-gated) `NATIVE_GOAL_ADAPTERS` set get the versioned `goal-contract/v1`
plain-text template with a trailing `GOAL_STATUS:` judge anchor instead of a literal
slash turn. Session turns persist `generated-policy.json` and report
`prompt_permission_mode` (`policy` | `deny_all`); `role_hash`/`policy_hash` are pinned
byte-identical to the released 0.1.3 distribution (zero-migration invariant).

### 3.3 `workspace.py` — cwd and workspace gate

Responsibilities:

- Resolve effective cwd from CLI input or role default.
- Validate cwd under configured allowed roots.
- Fail before artifact creation if invalid.
- Re-check role/workspace identity when attaching to persistent sessions.

Security claim: this is configuration/cwd intent validation, not OS/filesystem sandboxing.

Current status: cwd gate implemented for current run path. S1b adds deterministic
`workspace_hash(...)` for session binding; S1c revalidates the role/workspace/policy/acpx
binding before `send`, `status`, `close`, and `abort`, and refuses mismatches before
subprocess or artifact mutation. K1 preserves this binding gate while adding process-liveness
lease recovery; liveness never overrides role/workspace/policy/acpx binding mismatches.

### 3.4 `preflight.py` — doctor probes

Responsibilities:

- Probe Python, Node, acpx binary/version, adapter availability, policy parseability, fixture replay, EventStore permissions, cwd validation, redaction behavior, and session readiness.
- Stay read-only unless a future probe explicitly documents otherwise.
- Return structured diagnostics.

Current status: implemented. H1 completes the read-only probe set — `probe_policy`,
`probe_workspace`, `probe_redaction`, `probe_npx`, `probe_adapter`, and
`probe_session_readiness` join the existing Python/Node/acpx basics, role-specific acpx binary
probe, fixture replay, and EventStore permission probe, all wired into `cmd_doctor`. Every probe
is read-only: no probe launches an AGENT, runs `acpx exec`, sends a session prompt, or triggers
an `npx` fetch (`launched_real_agent` stays `false`); role-dependent probes
(`policy`/`workspace`/`npx`/`adapter`/role-aware session readiness) run only with `--role`, and
`ok` gates only on pure-local deterministic probes so the no-role doctor still exits `0` in CI.
The doctor output shape is the caller contract in `docs/design/result-event-schema.md` §5.
Evidence: `src/agent_run_supervisor/preflight.py`, `src/agent_run_supervisor/commands.py`
(`cmd_doctor`), `tests/test_preflight.py`, `tests/test_cli_commands.py`. H1 is merged on
`main` via PR #19 at `484ae23`.

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

Current status: one-shot subprocess launch, stdout/stderr capture, parser/classifier finalization, and watchdog kill metadata are implemented for local exec. S1c adds separate local persistent-session create/send/status supervision in `session_runtime.py`; S1d adds local close/abort/list lifecycle supervision. K1 adds persistent-session lease recovery on `main` via PR #22 (`0ad531e`); it does not change the one-shot exec watchdog/kill path.

### 3.6 `session.py` / `session_runtime.py` — persistent-session store and runtime

Store responsibilities (`session.py`):

1. Fixture-prove acpx persistent-session command grammar and stream shapes.
2. Create local session records with validated role/workspace metadata.
3. Persist session id, optional acpx session id/name, role hash, workspace hash, acpx version, policy hash, cwd, state, and lifecycle metadata.
4. Validate stored session bindings before any reattach/resume mutation.
5. Prevent concurrent unsafe session use through lease locks.
6. Detect expired locks and recover deterministically by replacing the lease.
7. Track lifecycle state locally (`open`/`closed`), serialize close/abort lifecycle sections,
   atomically mark successful closes, and refuse unsafe mutation of closed sessions.
8. Prevent cross-role, cross-workspace, stale-policy, acpx-version, or adapter mismatch leakage.
9. K1: record process-ownership identity (`host`/`pid`/`process_start`/`boot_id`) into
   `lock.json`, classify an encountered lease holder read-only as `alive`/`crashed`/`unknown`,
   and optionally (`acquire_lock(..., reclaim_crashed=True)`) reclaim a within-TTL lease whose
   holder is **provably crashed** — never an `alive`/`unknown` holder, never a live session.

Runtime responsibilities (`session_runtime.py`, `SessionRuntime`):

1. Drive S1c `create_session`/`send`/`status` over the store: validate the persistent role
   and workspace, run fixture-shaped acpx management/turn commands through a subprocess
   executor, and bind the local record to role/workspace/policy/acpx/adapter identity.
2. Re-open and re-validate the binding before every `send`/`status`/`close`/`abort` operation.
3. Acquire the lease lock for a `send` turn and release it on success **and** failure.
4. Persist redacted prompt-turn artifacts under `sessions/<session_id>/turns/<turn_id>/` and
   redacted management summaries under `sessions/<session_id>/management/`.
5. Parse prompt-turn NDJSON with the fixture-proven parser, classify supervisor status, and
   keep `business_verdict` null.
6. Fail closed — never create or update the local record — when a management command exits
   non-zero, returns unparseable output, or reports an error envelope.
7. Drive S1d `close` and `abort`: both re-check open state under a local lifecycle guard;
   `close` also acquires the session lease, runs fixture-proven `sessions close`, writes
   redacted `management/close.json`, and atomically marks the local record `closed`; `abort`
   runs fixture-proven `cancel -s`, writes redacted `management/abort.json`, and reports
   `cancelled: true|false` honestly.
8. Provide local read-only `list_sessions` over supervisor records without launching acpx.

Current status: S1b implements the local session store, binding validation, and lease-lock
foundation in `session.py`. S1c adds `session_runtime.py` with a create/send/status MVP over
that store: fixture/fake-executor acceptance, lease release on success and failure,
binding-mismatch refusal before any subprocess or artifact mutation, and redacted
turn/management artifacts. S1d adds close/abort/list lifecycle management: close and abort are
serialized by a local lifecycle guard; close is lease-protected and atomically transitions the
local record to `closed`; `send` re-checks the closed state under the acquired lease before
launching a turn; `send`/`close`/`abort` refuse closed sessions before subprocess/artifact
mutation; list is local and read-only. S1 closure acceptance adds the reproducible local
real-acpx smoke and two-turn continuity regression, closing S1 for the local persistent-session
lifecycle. K1 (merged on `main` via PR #22 at `0ad531e`) adds safe
process-liveness crash recovery over this foundation: the store records process-ownership
identity in `lock.json`, `detect_stale_locks` reports additive `holder_liveness`/`recoverable`
keys read-only, `acquire_lock(reclaim_crashed=True)` reclaims only a provably-crashed,
reclaimable within-TTL holder set, and `SessionRuntime(reclaim_crashed=True, default on)` threads
that into `send`/`close` so a crashed prior holder set no longer wedges the session while
`alive`/`unknown` holders and pending unreclaimable pre-holder locks still refuse. The runtime
keeps the supervisor identity as the top-level lock holder and records the spawned acpx child in
additive `child_*` fields; a composite supervisor+child lock can be reclaimed only after both
identities are provably crashed, which protects the post-child-exit artifact-writing window.
Retention/cleanup is H1 operational-hardening work already delivered; deletion stays
TTL/live-lock conservative and does **not** trust liveness.

`process_liveness.py` (K1, new, stdlib-only) is the supporting module: it provides
`ProcessIdentity`, `current_identity()`, read-only liveness signals (`pid_is_running` via
`os.kill(pid, 0)`, `read_process_start` via `/proc/<pid>/stat` field 22, `read_boot_id` via
`/proc/sys/kernel/random/boot_id`), an injectable `LivenessProbe` seam (so tests drive
crashed/alive/unknown deterministically without real crashed PIDs), and the pure
`classify_holder(...) -> alive|crashed|unknown` decision. It is **fail-safe by construction** —
it returns `crashed` only on positive proof (PID absent, start-time mismatch, or reboot) and
`unknown` whenever liveness cannot be proven (missing/foreign PID, different host, unreadable
start time, indeterminate probe). The liveness path sends **no terminating signal to
recorded holders and kills no prior holder**; the only PID syscall is the no-op
`os.kill(pid, 0)` existence probe. Evidence:
`src/agent_run_supervisor/process_liveness.py`, `tests/test_process_liveness.py`.

### 3.7 `parser.py` — observed event parser

Responsibilities:

- Parse only fixture-proven observed acpx stdout/event schemas.
- Convert updates into normalized event dicts.
- Assemble final messages from ordered text deltas.
- Preserve unknown update type plus key/type summaries only.
- Fail closed on malformed framing or max-output overflow.
- Summarize single-object acpx management-command JSON (`sessions new/ensure/show`, `status`,
  `sessions close`, `cancel`)
  into a safe allow-listed summary via `summarize_management_json`, kept on a separate parser
  from the prompt-turn NDJSON path.

Current status: implemented for the current exec fixture family. S1c adds
`summarize_management_json`/`ManagementParseError` for single-object session management JSON,
separate from the prompt-turn NDJSON parser (`parse_acpx_stdout_bytes`). The management
summarizer returns only allow-listed fields plus top-level key/type evidence (never bulk
payload) and fails closed on empty, non-JSON, non-object, or JSON-RPC stream records fed to
the management path. S1d reuses the existing allow-listed `session_closed`/`cancel_result`
summaries (`closed`/`cancelled`); broader persistent-session parser/event coverage is future work.

### 3.8 `exit_classifier.py` — status classifier

Responsibilities:

- Map acpx/runner/session exit behavior to supervisor-owned statuses.
- Ensure nonzero exits never become completed.
- Distinguish supervisor-origin failures from acpx-origin failures.
- Keep caller business verdict separate from supervisor status.

Current statuses:

```text
completed
no_op
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

Current status: implemented for current exit-code model; session lifecycle detail may need
extensions. S2 adds the fail-closed `no_op` status: exit 0 with a protocol-clean stream but
no agent output and no tool activity (`parser.has_observed_effect`) classifies `no_op`
(`retryable=false`), never `completed` — silent slash-prompt/goal turns can no longer be
misread as success. Protocol errors and supervisor kills keep precedence; nonzero exits are
unchanged.

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
redacted management summaries under `sessions/<session_id>/management/`; S1d adds redacted
`management/close.json` and `management/abort.json` plus the atomic `closed` state transition.
H1 adds confined, dry-run-first retention/cleanup over run and session artifacts via
`retention.py` and the `cleanup` CLI. K1 adds process-liveness metadata to session locks and
read-only detector fields; retention deletion remains TTL/live-lock conservative and does not
trust liveness to delete open sessions.

### 3.10 `caller.py` — generic local caller boundary

Responsibilities:

1. Accept a local `CallerInvocationSpec` with exactly one role source (`role` or
   `role_file`), caller-owned `prompt`/`context`, optional `cwd`, artifact directories,
   mode, and session identifiers.
2. Validate unsupported modes, missing prompts, missing `session_id`, and `session_name`
   usage before delegating to runner/session surfaces.
3. Combine caller-owned context and prompt into one prompt string without interpreting
   business success.
4. Delegate one-shot `exec` / `exec_dry_run` to `SupervisorRunner.run` /
   `SupervisorRunner.dry_run`, and session `create` / `send` / `status` / `close` to
   `SessionRuntime`.
5. Return `CallerResult`, a local wrapper around the existing supervisor payload or
   projection with artifact path fields and `business_verdict: null`.

Current status: I1 implements this as a library-only boundary in
`src/agent_run_supervisor/caller.py` with `tests/test_caller.py`. It adds no CLI command,
does not parse raw ACP/acpx streams, and carries no platform, delivery, Gateway, public
ingress, automatic-reply, or concrete caller fields. L1 documents the *concrete* caller design
above this boundary (`docs/plans/archive/2026-06-01-l1-concrete-caller-integration-design.md`). The L2
implementation merged via PR #27 (`eb7912e`) adds the caller-side `src/agent_run_supervisor/hermes_caller/` package
with `tests/hermes_caller/`: Hermes document-check intake, caller-owned verdict derivation,
normalized-event evidence projection, progress/result view-models, an escaped offline Feishu
payload dict, and exec + persistent-session orchestration through `invoke_caller`. `caller.py`
stays generic; the supervisor still adds no platform field, no business verdict, and no
rendering/delivery.

### 3.11 `commands.py` / `cli.py` — dev CLI

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
agent-run-supervisor session close  --role <role-file> --session-id <id> [--cwd <dir>]
agent-run-supervisor session abort  --role <role-file> --session-id <id> [--cwd <dir>]
agent-run-supervisor session list   [--role <role-file>] [--sessions-dir <dir>]
agent-run-supervisor cleanup [--runs-dir <dir>] [--sessions-dir <dir>] [--apply]
```

Current status: validate-role accepts exec and persistent role strategies; replay, doctor
baseline, dry-run, and local one-shot real exec are implemented. The `run` path refuses
persistent roles before artifacts/process launch. S1c adds the `session create|send|status`
MVP (JSON stdout, 0/nonzero exit codes) over `SessionRuntime`; S1d adds
`session close|abort|list`, with `list` local/read-only and role-optional. H1 adds the
confined, dry-run-first `cleanup` command. K1 changes session lock recovery internals only and
adds no CLI command. I1 deliberately adds no CLI command.

### 3.12 `session_inspect.py` — read-only caller session inspection

A generic caller API over one persistent session's local on-disk state, for callers (e.g. an
external orchestrator's liveness/health path) that must observe a session **without spawning
anything** — `SessionRuntime.status` runs an acpx `status -s` management subprocess and is
therefore unsuitable for a hot polling path.

```python
inspect_session(sessions_dir, session_id, *, liveness_probe=None, now=None) -> SessionInspection
list_turns(sessions_dir, session_id) -> tuple[TurnInfo, ...]
```

- `SessionInspection` carries `session_id` / `exists` / `state` (`open`/`closed`/`None`) /
  `lease_held` / `holder_liveness` (`alive`/`crashed`/`unknown`, K1 classification) /
  `lease_recoverable` / `turn_count` / `latest_turn_id` / `latest_turn_status` (closed
  `AgentRunStatus` vocabulary only) / `progress` (the latest turn's structural
  `ProgressSnapshot`). No filesystem paths, no raw prompt/output text.
- `TurnInfo` enumerates turns in stable turn-identity (creation) order; its `turn_dir` is a
  **caller-private** path (same trust level as `SessionTurnOutcome.turn_dir`) for the caller's
  own binding layer, never for public projections. Event bodies are never read.
- Fail-closed degradation: a missing session is `exists=False`; a corrupt record / lock /
  `result.json` / `progress.json` degrades the affected field to `None`/`unknown` — health is
  never fabricated and off-vocabulary tokens are never echoed. Only an unsafe `session_id`
  raises. Lease semantics mirror `SessionStore.detect_stale_locks` (same expiry boundary, same
  crashed-holder reclamation rule, `reclaimable=false` locks never liveness-classified),
  evaluated per session so one corrupt foreign session cannot break inspection.

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

Current S1b/S1c/S1d session layout:

```text
.agent-run-supervisor/sessions/<session_id>/
  session.json          # local record; S1d marks state -> "closed" atomically
  lock.json            # present only while a lease is held
  management/
    create.json
    status.json
    close.json          # S1d redacted sessions close evidence
    abort.json          # S1d redacted cancel -s evidence
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
  retention.json       # optional future per-session cleanup snapshot, if needed
```

The foundation filenames are implemented in S1b, S1c implements create/send/status
management and turn artifacts, S1d implements close/abort management evidence plus local
list output, and S1 closure acceptance proves two-turn continuity plus a real local acpx
lifecycle smoke. Snapshots remain future extension points; H1 delivered cleanup/retention
and detection-first crash hygiene, and K1 delivered full process-liveness recovery beyond
expired-lease replacement. None of those are new S1 subphases.

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

The concrete (Hermes) caller design built on the generic I1 boundary — covering exec and
persistent-session document-check flows, the input/output contracts, the
normalized-event → view-model mapping, the ownership matrix, and the defined-but-unapproved
Sachima seam — lives in
`docs/plans/archive/2026-06-01-l1-concrete-caller-integration-design.md` (L1). The L2 implementation
merged via PR #27 (`eb7912e`) realizes the local/offline caller-side portion in `src/agent_run_supervisor/hermes_caller/`
and `tests/hermes_caller/` without changing the generic I1 contract. It remains fake/local/offline:
no real Feishu API, IM delivery, public ingress, Gateway lifecycle, Sachima behavior, automatic
replies, live/default-on behavior, or trusted Markdown/HTML rendering.

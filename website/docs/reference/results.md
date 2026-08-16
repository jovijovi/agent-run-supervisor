---
title: Results
description: The persisted terminal result.json, the five-value status vocabulary, and the Session projection.
---

# Results

Every Run ends with exactly one terminal, persisted at `<run_dir>/result.json`.

## `result.json`

The top-level key set is authoritative and closed. "Always present" means the key
is always serialized; its value may still be `null`.

| Key | Type | Always present | Meaning |
|---|---|---|---|
| `run_id` | `string` | yes | Run identifier |
| `status` | `string` | yes | supervisor terminal status. Never a business verdict |
| `business_verdict` | `null` | yes | **always `null`.** Caller-owned; the supervisor never sets it |
| `error_code` | `string` \| `null` | yes | stable code for the status, or `null` when `status == "completed"` |
| `detail_code` | `string` \| `null` | yes | finer categorical detail, or `null` |
| `origin` | `string` | yes | `acp` (an ACP terminal was observed) or `supervisor` |
| `retryable` | `boolean` | yes | status-derived, never caller-set |
| `signal` | `number` \| `null` | yes | terminating signal number for the supervised child, or `null` |
| `stop_reason` | `string` \| `null` | yes | ACP stop reason from the turn, or `null` |
| `usage` | `object` \| `null` | yes | bounded usage payload as reported over ACP, or `null` |
| `final_message` | `string` | yes | redacted concatenated agent message text (may be empty) |
| `truncated` | `boolean` | yes | whether the final message hit its ingestion ceiling |
| `truncate_reason` | `string` \| `null` | yes | reason for truncation, or `null` |
| `observed_effect` | `boolean` \| `null` | yes | whether the stream showed agent output or tool activity; `null` when nothing was observed |
| `run_dir` | `string` | yes | absolute path to the Run artifact directory |
| `stderr_path` | `string` | yes | Run-dir-relative path to the redacted stderr log |
| `raw_event_path` | `string` | yes | Run-dir-relative path to the persisted event stream |
| `redaction_report_path` | `string` | yes | Run-dir-relative path to the redaction report |

Native terminals may additionally carry `session_id` and `failure_reason`.

!!! contract "There is no process-exit field"

    A result carries no exit code. It was the one field that described *the child
    process* rather than the Run, and it left with the runtime that produced it —
    not renamed, not replaced. What survives about an abnormal end is the
    Run-level vocabulary: `status`, `detail_code`, `signal`, and `stop_reason`.

`result.json` carries no embedded `schema_version`; a reader validates against
the closed field set above. The Session record does carry an integer
`schema_version`, and its field set is closed the same way.

## Statuses

Five values — the complete terminal vocabulary, and deliberately nothing beside
it.

| `status` | `error_code` | `retryable` default |
|---|---|---|
| `completed` | `null` | `false` |
| `failed` | `FAILED` | `false` |
| `cancelled` | `CANCELLED` | `false` |
| `timed_out` | `TIMED_OUT` | `true` |
| `unknown` | `UNKNOWN` | `false` |

`retryable` is the status-derived default; a terminal whose `retryable`
disagrees with it is untrusted.

!!! danger "`unknown` is the load-bearing one"

    A prompt that may have been dispatched without a trustworthy terminal ends
    `unknown` / `retryable = false`, with its Session quarantined. It is **never**
    replayed, resumed, or retried automatically, and there is no unquarantine
    tool.

### Terminal grammar

Trusted terminals must satisfy an exact status/origin/stop/detail grammar. A
record outside it is untrusted rather than repaired:

- `origin = acp` always requires ACP stop evidence;
- `completed` requires a completed-class `stop_reason`;
- `cancelled` requires `stop_reason = cancelled`;
- `unknown` requires `origin = supervisor` with no ACP stop.

### A permission violation is a `failed` Run

A Run whose stream showed a [`permission_violation`](events.md#the-permission-families)
ends `failed` / `PERMISSION_VIOLATION` / `retryable = false`, with `origin = acp`
and the turn's own `stop_reason` preserved. Two facts about that terminal matter
to a caller:

- **The Session stays reusable.** A refused operation is not continuity doubt,
  so nothing is quarantined and the next Run may continue the conversation.
- **It is detection, not prevention.** The violation was observed after a tool
  reported `completed`, so a failed Run here does **not** mean the side effect
  was blocked. It means ARS refused to persist a `completed` terminal for a Run
  that contradicted a decision ARS made. An agent that reports `failed` for the
  call it was refused is behaving correctly and completes normally.

### `failure_reason`

When present and non-null, one of a fixed categorical allowlist — never raw
exception text, a path, a class name, or credential-shaped material:

`admission failed`, `spawn failed`, `startup timed out`,
`session load unavailable`, `silent session recreation`, `config fidelity failed`,
`evidence pipeline failed`, `supervisor cancellation`, `run exception`,
`run failed`.

## Untrusted evidence

A `result.json` carrying a key this version does not define is **untrusted
evidence**, not a tolerated extension:

- it is refused whole, never projected, never read, never re-emitted;
- nothing migrates, rewrites, or resets a stored record;
- an untrusted terminal simply never becomes trusted evidence, and the caller
  sees the same bounded error every other malformed terminal produces.

## The Session projection

`session_status` and `session_list` project identity, lease and activity facts,
last-use observations, and optional quarantine evidence. They expose **no**
synthetic Session lifecycle state, because none exists.

| Field | Type | Meaning |
|---|---|---|
| `session_id` | `string` | the durable ARS Session identifier |
| `owner`, `namespace` | `string` | the authenticated identity the Session belongs to |
| `agent_id` | `string` \| `null` | the registered agent this Session binds |
| `profile_id` | `string` \| `null` | the source profile identity |
| `created_at`, `updated_at` | `string` \| `null` | creation and last-use timestamps |
| `last_effective_model`, `last_effective_effort` | `string` \| `null` | the last exact-readback-proven pair |
| `quarantine` | `object` \| `null` | `{reason_code, source_run_id, recorded_at}`, else `null` |

`quarantine.reason_code` is drawn from a closed source-owned vocabulary: never an
exception message, never remote or agent-authored text, never a path. The
external AGENT session id is never projected.

## Caller-stability contract

- **`business_verdict` is always `null`.** Supervisor status is not a business
  pass/fail.
- **Closed field set.** Future changes may *add* keys, documented here together
  with the emitter and the validator. Existing keys are never renamed, removed,
  or repurposed, and their meaning is fixed.
- **Drift guard.** The top-level key set is pinned against the emitter by the
  repository's own test suite, so this contract cannot drift from the code
  unnoticed.

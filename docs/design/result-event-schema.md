---
title: "agent-run-supervisor Result / Event Schema"
status: active
created_at: 2026-06-01
last_validated_at: 2026-06-01T00:00:00+0800
---
# agent-run-supervisor Result / Event Schema

> **Authority and scope.** This document is the *caller-stable contract* for the
> JSON shapes `agent-run-supervisor` emits: the persisted `result.json` payload,
> the session result projections returned by the dev CLI, the supervisor status
> and error-code vocabulary, the normalized event families, the `doctor` output
> (including the H1 read-only probes), and the `cleanup` plan/result shape. It is
> **derivative and descriptive**: it documents the schema *as the code emits it
> today* (`result.py`, `parser.py`, `exit_classifier.py`, `commands.py`,
> `preflight.py`, `retention.py`, `session.py`, `session_runtime.py`). It does
> **not** redefine product goals, expand scope, grant any runtime/live approval,
> or introduce a business verdict.
>
> **Stability rule (read this first).** `business_verdict` is **always `null`**
> and caller-owned — the supervisor never sets it. Schema evolution is
> **additive only**: new keys may be added, but existing keys are never renamed,
> removed, or repurposed, and no existing key changes meaning. Callers should
> ignore unknown keys rather than fail on them. See
> [§8 Caller-stability contract](#8-caller-stability-contract).
>
> The top-level `result.json` key set is pinned against
> `result.build_result_payload` by `tests/test_result_event_schema.py`, so this
> document cannot silently drift from the code.

## 1. `result.json` payload

`result.build_result_payload(...)` produces the canonical one-shot run result
that the supervisor persists at `.agent-run-supervisor/runs/<run_id>/result.json`
and prints to stdout from `agent-run-supervisor run`. The session turn path
(`SessionRuntime._run_turn`) reuses the **same** builder and then adds the
additive turn keys in [§2.1](#21-session-turn-resultjson-persisted).

The table below is the authoritative top-level key set. Types use JSON spelling
(`string`, `number`, `boolean`, `object`, `null`). "Always present" means the
key is always serialized (its value may still be `null`).

<!-- result-json-keys:begin -->

| Key | Type | Always present | Meaning |
|-----|------|----------------|---------|
| `run_id` | `string` | yes | Run identifier (turn id on the session path). |
| `status` | `string` | yes | Supervisor status enum value (see [§3](#3-statuses-and-error-codes)). Never a business verdict. |
| `business_verdict` | `null` | yes | **Always `null`.** Caller-owned; the supervisor never sets it. |
| `error_code` | `string` \| `null` | yes | Stable error code for the status, or `null` when `status == "completed"` (see [§3.2](#32-error-codes)). |
| `detail_code` | `string` \| `null` | yes | Finer detail (e.g. acpx `acpxCode`/`SUPERVISOR_KILL`/`PROTOCOL_ERROR`), or `null`. |
| `origin` | `string` | yes | Where the outcome originated: `cli`, `acp`, `supervisor`, or an acpx-reported origin. |
| `retryable` | `boolean` | yes | Whether the supervisor considers the status safe to retry (status-derived default). |
| `acpx_exit_code` | `number` \| `null` | yes | Observed acpx/runner process exit code, or `null` when not applicable. |
| `signal` | `number` \| `null` | yes | Terminating signal number, or `null`. |
| `stop_reason` | `string` \| `null` | yes | acpx `stopReason` from the completion record, or `null`. |
| `usage` | `object` \| `null` | yes | Usage payload (token/turn counters) as reported by acpx, or `null`. |
| `final_message` | `string` | yes | Redacted concatenated agent message text (may be empty). |
| `truncated` | `boolean` | yes | Whether stdout parsing was truncated at `max_output_bytes`. |
| `truncate_reason` | `string` \| `null` | yes | Reason for truncation (e.g. `max_output_bytes`), or `null`. |
| `run_dir` | `string` | yes | Absolute path to the run/turn artifact directory. |
| `stderr_path` | `string` | yes | Run-dir-relative path to the redacted stderr log (default `stderr.log`). |
| `raw_event_path` | `string` | yes | Run-dir-relative path to the redacted acpx stdout NDJSON (default `acpx-stdout.ndjson`). |
| `redaction_report_path` | `string` | yes | Run-dir-relative path to the redaction report (default `redaction-report.json`). |

<!-- result-json-keys:end -->

`result.json` carries **no embedded `schema_version` field today**; compatibility
is guaranteed by the additive-only rule in [§8](#8-caller-stability-contract).
(The session *record* `session.json` does carry an integer `schema_version` — see
[§2.3](#23-session-record-summary-list).)

## 2. Session result projections

The persistent-session runtime (`session_runtime.py`) returns a `result` dict per
operation that the dev CLI prints verbatim. Every projection carries
`business_verdict: null`.

### 2.1 Session turn `result.json` (persisted)

`SessionRuntime._run_turn` builds the payload with `build_result_payload(...)`
(all [§1](#1-resultjson-payload) keys) and then adds these **additive** keys before
persisting `sessions/<session_id>/turns/<turn_id>/result.json` and returning it:

| Key | Type | Meaning |
|-----|------|---------|
| `session_id` | `string` | Local session identifier. |
| `turn_id` | `string` | Per-turn identifier (also the `run_id`). |
| `kill_reason` | `string` \| `null` | Watchdog/kill metadata: why the process was killed, if any. |
| `kill_signal` | `number` \| `null` | Signal used to kill the process, if any. |
| `grace_ms` | `number` | Watchdog grace window in milliseconds. |
| `process_group_used` | `boolean` | Whether a process group was used for the kill. |
| `stdout_closed` | `boolean` | Whether stdout was observed closed. |
| `stderr_closed` | `boolean` | Whether stderr was observed closed. |

### 2.2 Management projections (`create` / `status` / `close` / `abort`)

These are in-memory result dicts (not a persisted `result.json`); redacted
management evidence is persisted separately under
`sessions/<session_id>/management/`.

- **`create_session.result`**: `session_id`, `acpx_session_id`, `session_name`,
  `state`, `kind`, `created`, `session_dir`, `business_verdict`.
- **`status.result`**: `session_id`, `ok`, `acpx_exit_code`, `summary`,
  `business_verdict`. `ok` is `true` only when the management `summary.status == "alive"`.
- **`close.result`**: `session_id`, `state`, `closed`, `kind`, `acpx_session_id`,
  `business_verdict`.
- **`abort.result`**: `session_id`, `cancelled`, `state`, `kind`,
  `business_verdict`. `cancelled: false` is reported honestly (nothing active to
  cancel) and is **not** a business verdict; abort never flips session state.

The `summary` embedded above is the allow-listed management summary from
`parser.summarize_management_json` / `_management_summary`:
`kind`, `acpx_record_id`, `acpx_session_id`, `session_name`, `created`, `closed`,
`status`, `cancelled`, `code`, `acpx_code`, `top_level_keys`. It carries only
scalar identity/lifecycle fields plus structural evidence (`top_level_keys` are
`name:type` pairs) — never bulk payload, model catalogs, or message bodies.

### 2.3 Session record summary (`list`)

`SessionRuntime.list_sessions` returns `{ "sessions": [...], "count": <int>,
"business_verdict": null }`, where each entry is a redacted record summary:
`session_id`, `state`, `role_id`, `session_name`, `acpx_session_id`,
`acpx_version`, `adapter_agent`, `created_at`, `updated_at`.

The on-disk record `session.json` (`session.SessionRecord`) additionally carries:
`schema_version` (integer), `role_hash`, `workspace_hash`, `policy_hash`,
`effective_cwd`, `matched_root`.

### 2.4 Replay projection (`_parse_result_payload`)

`commands._parse_result_payload` projects a parsed stdout stream for the `replay`
command and for the `doctor` `fixture_replay`. Keys:
`protocol_error`, `protocol_error_reasons`, `final_message`, `usage`,
`business_verdict`, `truncated`, `truncate_reason`, `unknown_update_types`,
`permission_request_count`, `permission_denied_count`, `event_count`.

### 2.5 Local caller wrapper projection (`CallerResult.to_dict`)

I1 adds a generic local library wrapper in `caller.py`. `CallerResult.to_dict()`
wraps existing supervisor payloads without changing their meaning and without adding
platform/delivery fields. Keys:

| Key | Type | Meaning |
|-----|------|---------|
| `mode` | `string` | Local caller invocation mode: `exec`, `exec_dry_run`, `session_create`, `session_send`, `session_status`, or `session_close`. |
| `supervisor_status` | `string` \| `null` | Existing supervisor `status` when the wrapped payload has one; otherwise `null`. |
| `result` | `object` | The existing run/session supervisor payload or projection. The caller wrapper does not parse raw acpx streams. |
| `artifact_dir` | `string` \| `null` | Local artifact directory for the wrapped result, when applicable. |
| `run_dir` | `string` \| `null` | Local run/turn artifact directory, when applicable. |
| `session_dir` | `string` \| `null` | Local session artifact directory, when applicable. |
| `business_verdict` | `null` | **Always `null`.** The caller owns business verdicts, rendering, and any delivery outside this library. |

The caller wrapper intentionally carries no channel, webhook, recipient, Gateway,
public-ingress, delivery, or platform state fields.

## 3. Statuses and error codes

Supervisor status is owned by `exit_classifier.py` and is **never** the caller's
business verdict.

### 3.1 Status set (10)

`completed`, `runner_error`, `invalid_invocation`, `timed_out`, `no_session`,
`permission_denied`, `interrupted`, `protocol_error`, `infrastructure_error`,
`policy_error` (`exit_classifier.AgentRunStatus`).

The base exit-code → status map, refined by acpx metadata and supervisor
kill/timeout signals:

| acpx exit code | Base supervisor status |
|---:|---|
| `0` | `completed` |
| `1` | `runner_error` (refined by `acpxCode`/origin → `invalid_invocation`, `timed_out`, `no_session`, `permission_denied`, `infrastructure_error`) |
| `2` | `invalid_invocation` |
| `3` | `timed_out` |
| `4` | `no_session` |
| `5` | `permission_denied` |
| `130` | `interrupted` |
| other / unknown | `infrastructure_error` |

Honesty refinements: exit `0` with a `protocol_error` in stdout becomes
`protocol_error`; a supervisor watchdog/kill becomes `timed_out` (on timeout) or
`infrastructure_error`, with `origin = supervisor`; nonzero exits never become
`completed`.

### 3.2 Error codes

`result._ERROR_CODE_FOR_STATUS` maps each status to its `error_code`
(`completed → null`):

| `status` | `error_code` | `retryable` default |
|----------|--------------|---------------------|
| `completed` | `null` | `false` |
| `runner_error` | `RUNNER_ERROR` | `true` |
| `invalid_invocation` | `INVALID_INVOCATION` | `false` |
| `timed_out` | `TIMED_OUT` | `true` |
| `no_session` | `NO_SESSION` | `false` |
| `permission_denied` | `PERMISSION_DENIED` | `false` |
| `interrupted` | `INTERRUPTED` | `false` |
| `protocol_error` | `PROTOCOL_ERROR` | `false` |
| `infrastructure_error` | `INFRASTRUCTURE_ERROR` | `true` |
| `policy_error` | `POLICY_ERROR` | `false` |

`retryable` is the status-derived default (`exit_classifier._RETRYABLE_DEFAULT`).
`detail_code` carries the finer acpx/supervisor detail (e.g. `SUPERVISOR_KILL`,
`PROTOCOL_ERROR`, or a passed-through `acpxCode`).

## 4. Normalized event families

`parser.py` normalizes the acpx JSON-RPC NDJSON stream into a flat list of event
dicts persisted as `normalized-events.jsonl`. Each event has a `type` and a small
allow-listed set of structural fields (never bulk content):

| `type` | Additional fields | Source |
|--------|--------------------|--------|
| `run_started` | `method`, `id` | `initialize` request |
| `session_new_requested` | — | `session/new` |
| `session_model_set` | — | `session/set_model` |
| `session_prompt_sent` | — | `session/prompt` |
| `run_completed` | `stop_reason` | result record with `stopReason`/`usage` |
| `run_failed` | `code`, `acpx_code` | JSON-RPC error envelope |
| `agent_message_delta` | `text_length` | `session/update` `agent_message_chunk` |
| `agent_thought_delta` | — | `session/update` `agent_thought_chunk` |
| `usage_updated` | — | `session/update` `usage_update` |
| `available_commands_updated` | — | `session/update` `available_commands_update` |
| `tool_started` | `tool_call_id`, `kind` | `session/update` `tool_call` |
| `tool_updated` | `tool_call_id`, `status` | `tool_call_update` (in-progress) |
| `tool_completed` | `tool_call_id`, `status` | `tool_call_update` (`completed`/`failed`) |
| `permission_requested` | `tool_call_id`, `option_ids` | `session/request_permission` |
| `permission_denied` | `option_id` | permission response with a reject option |
| `rpc_result` | `id` | generic JSON-RPC result |
| `unknown_update` | `update_type`, `key_summary` | unrecognized `sessionUpdate`/method (forward-compatible) |

`key_summary` on `unknown_update` is a comma-joined list of `path:type` structural
hints only — never values. Watchdog/kill/lifecycle metadata is **not** emitted as
a stream event; it is attached to the turn `result.json` (see
[§2.1](#21-session-turn-resultjson-persisted)).

## 5. `doctor` output

`commands.cmd_doctor` prints a single JSON object. All probes are **read-only**:
no probe launches an AGENT, runs `acpx exec`, sends a session prompt, or triggers
an `npx` package fetch. `launched_real_agent` is always `false`.

Top-level keys:

| Key | Type | Notes |
|-----|------|-------|
| `ok` | `boolean` | Roll-up; see gating note below. |
| `python_version` | `string` | Host Python version. |
| `node_version_requirement` | `string` | Declared minimum (`>=22.12`). |
| `launched_real_agent` | `boolean` | **Always `false`.** |
| `event_store_probe` | `object` | `{ dir_mode_ok, file_mode_ok, atomic_write_ok }`. |
| `fixture_replay` | `object` | `_parse_result_payload` projection ([§2.4](#24-replay-projection-_parse_result_payload)), or `{ protocol_error, protocol_error_reasons }` when the fixture is missing. |
| `role_validation` | `object` \| `null` | `{ valid, role_id, role_hash }` when `--role` is supplied, else `null`. |
| `node_probe` | `object` | `{ binary, requirement_minimum, available, version, ok, error_detail }`. |
| `acpx_probe` | `object` | `{ binary, expected_version, available, version, ok, error_detail }`. |
| `redaction_probe` | `object` | `{ ok, patterns_exercised, leaked, error_detail }`; `leaked == []` on success. |
| `session_probe` | `object` | `{ ok, dir_mode_ok, file_mode_ok, lease_seconds, stale_locks, error_detail }`. |
| `policy_probe` | `object` \| `null` | role-only; see below. |
| `workspace_probe` | `object` \| `null` | role-only; see below. |
| `npx_probe` | `object` \| `null` | role-only; see below. |
| `adapter_probe` | `object` \| `null` | role-only; see below. |

Role-dependent probe shapes (each `null` when `--role` is omitted):

- **`policy_probe`**: `ok`, `parseable`, `default_action`, `auto_approve_count`,
  `auto_deny_count`, `policy_hash`, `error_detail`. `ok` requires
  `default_action == "deny"`.
- **`workspace_probe`**: `ok`, `effective_cwd`, `matched_root`,
  `allowed_roots_security_boundary` (always `false`), `disclaimer`,
  `error_detail`. Never claims an OS/filesystem sandbox.
- **`npx_probe`**: `ok`, `fetch_risk`, `npx_available`, `pinned_spec`,
  `acpx_binary`, `error_detail`. `fetch_risk` is `true` only when no explicit
  `acpx_binary` is set (the compiler would resolve `npx -y acpx@<version>` at run
  time); the probe only runs `npx --version`, never `npx acpx`.
- **`adapter_probe`**: `ok`, `adapter_agent`, `declared`, `hostable`, `detail`.
  Availability means **declared + hostable** only; `detail` states the adapter
  process is not launched.

`session_probe` is `probe_session_readiness`: it checks `0700`/`0600` artifact
modes on a throwaway temp store and reports `lease_seconds` for a persistent role.
`stale_locks` (the W4 read-only detector — see [§6](#6-stale-lock-detector)) is
populated only when a `sessions_dir` is supplied; `doctor` does not pass one, so
`stale_locks` is `[]` in `doctor` output.

**`ok` gating.** `ok` gates only on pure-local deterministic probes so the no-role
CI gate keeps exiting `0` without `node`/`acpx`/`npx`: always-contributing =
fixture replay (no `protocol_error`), `event_store_probe` modes,
`redaction_probe`, role-less `session_probe` dir modes; contributing **only with
`--role`** = `policy_probe`, `workspace_probe`. External-binary probes
(`node_probe`, `acpx_probe`, `npx_probe`, `adapter_probe`) are **informational and
never flip `ok`**. Exit code is `0` when `ok` is `true`, else `1`.

## 6. Stale-lock detector

`session.SessionStore.detect_stale_locks(now=None)` is read-only: it takes no
lock, removes/rewrites nothing, **sends no terminating signal to recorded
holders, and kills no prior holder**. The only syscall it issues against a recorded holder PID is a no-op
`os.kill(pid, 0)` existence probe (POSIX delivers no signal for signal `0`). It
returns a list with one entry per local record:

| Key | Type | Meaning |
|-----|------|---------|
| `session_id` | `string` | Local session identifier. |
| `state` | `string` | Record state (`open` / `closed`). |
| `lock_present` | `boolean` | Whether `lock.json` exists. |
| `lease_expired` | `boolean` | `true` when `expires_at <= now`, or when the lock is unreadable/garbage (conservatively expired). A live (future) lease is `false`. |
| `holder_liveness` | `string` \| `null` | K1 process-liveness classification of the recorded lock holder set: `alive`, `crashed`, or `unknown`; `null` when there is no lock. Composite supervisor+child locks classify as `crashed` only when both identities are provably crashed; if either is alive the result is `alive`, and if either is unverifiable the result is `unknown`. An unreadable/garbage lock classifies `unknown`. See [§6.1](#61-k1-holder-liveness-and-safe-recovery-posture). |
| `recoverable` | `boolean` | `true` when the lease is TTL-expired (`lease_expired`) **or** the holder is provably `crashed` and the lock is reclaimable. An `alive`, `unknown`, or explicitly unreclaimable pending holder on a within-TTL lease is **not** `recoverable`. |
| `tmp_debris` | `array<string>` | Leftover `.tmp-*` atomic-write debris file names in the session dir. |

`holder_liveness` and `recoverable` are **additive** keys (K1); the existing
`session_id`/`state`/`lock_present`/`lease_expired`/`tmp_debris` keys are
unchanged. The top-level holder identity is read from the additive `host`/`pid`/
`process_start`/`boot_id` fields that `acquire_lock` now records into `lock.json`
alongside the existing `token`/`owner`/`acquired_at`/`expires_at`. When the runtime
spawns an acpx subprocess, it preserves that top-level supervisor identity and adds
`child_host`/`child_pid`/`child_process_start`/`child_boot_id` fields; liveness
recovery requires both supervisor and child identities to be provably crashed.

### 6.1 K1 holder-liveness and safe-recovery posture

K1 adds conservative, **read-only** crash detection plus opt-in, **provably-safe**
lease recovery (`process_liveness.classify_holder`). The safety posture is:

- **Detection is read-only.** `detect_stale_locks` launches nothing, takes no
  lock, removes/rewrites nothing, sends **no terminating signal to recorded
  holders**, and kills no prior holder. The only PID syscall is the no-op
  `os.kill(pid, 0)` existence probe;
  on Linux it also reads `/proc/<pid>/stat` field 22 (start time) and
  `/proc/sys/kernel/random/boot_id` to defeat PID reuse / detect a reboot.
- **`crashed` requires positive proof** — every recorded holder in the lock's holder
  set is gone: the PID is absent, its recorded start time no longer matches (PID
  reuse), or the machine rebooted since the lease. For supervisor+child locks,
  both identities must independently classify `crashed`.
- **`alive` and `unknown` are never recoverable via liveness.** A missing/foreign
  PID, a different host, an unreadable start time, an indeterminate probe, or an
  explicitly unreclaimable pending lock all classify or behave fail-safe → treated
  as possibly-alive/unrecorded work → refused. Only TTL expiry can recover an
  `unknown`/`alive`/pending-unreclaimable holder.
- **No live-session takeover.** Reclamation of a within-TTL lease (opt-in
  `acquire_lock(..., reclaim_crashed=True)`; on by default in `SessionRuntime`)
  happens only when the holder is provably `crashed`, entirely under the existing
  per-session `flock` guard. The strict TTL-only contract is preserved by
  default at the store (`reclaim_crashed=False`).

## 7. Cleanup plan / result

`retention.py` plans and applies confined, dry-run-first cleanup; the
`agent-run-supervisor cleanup` command serializes it via `commands.cmd_cleanup`.

Command output (stdout JSON):

- **Dry-run (default, no `--apply`)**: `{ "applied": false, "plan": <CleanupPlan> }`.
- **Apply (`--apply`)**: `{ "applied": true, "deleted": [<id>...],
  "failed": [{ id, path, reason }...], "plan": <CleanupPlan> }`.

`CleanupPlan` shape (`commands._plan_payload`):

| Key | Type | Meaning |
|-----|------|---------|
| `root` | `string` | Resolved `.agent-run-supervisor` artifact root the tool is confined to. |
| `runs_dir` | `string` | Resolved runs directory scanned. |
| `sessions_dir` | `string` | Resolved sessions directory scanned. |
| `delete` | `array<CleanupCandidate>` | Candidates planned for deletion. |
| `skip` | `array<CleanupCandidate>` | Candidates retained/refused, with reasons. |

`CleanupCandidate` shape: `kind` (`run` / `session`), `id`, `path`, `age_seconds`,
`action` (`delete` / `skip`), `reason`. Reasons in use: `max_age_days`,
`max_count` (delete reasons); `retained`, `symlink_escape`, `open_session`,
`live_lock` (skip reasons).

Safety posture (documented for callers): planning is read-only and deletes
nothing; `apply` deletes **only** `plan.delete` entries and re-verifies every
safety invariant immediately before removal; the tool refuses to operate outside a
resolved `.agent-run-supervisor` root, never follows a symlink out of root, and
never deletes an open or live-locked session.

## 8. Caller-stability contract

- **`business_verdict` is always `null`.** Across every payload above (run result,
  turn result, all session projections, replay projection), the supervisor never
  sets a business verdict. Supervisor status (`status`/`error_code`) is **not** a
  business pass/fail.
- **Additive evolution only.** Future schema changes may *add* keys. Existing keys
  are never renamed, removed, or repurposed, and their meaning is fixed. Callers
  should ignore unknown keys rather than reject them.
- **Versioning / compatibility.** `result.json` has no embedded `schema_version`
  today; compatibility rests on the additive-only rule. The session record
  `session.json` carries an integer `schema_version` for record-format evolution.
  Any future top-level `schema_version` on `result.json` would itself be an
  additive change.
- **Drift guard.** `tests/test_result_event_schema.py` pins the [§1](#1-resultjson-payload)
  top-level key set against `result.build_result_payload`, so this contract cannot
  drift from the code unnoticed.

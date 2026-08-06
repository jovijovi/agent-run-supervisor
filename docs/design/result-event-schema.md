---
title: "agent-run-supervisor Result / Event Schema"
status: active
created_at: 2026-06-01
last_validated_at: 2026-08-06
---
# agent-run-supervisor Result / Event Schema

> **Authority and scope.** This document is the *caller-stable contract* for the
> JSON shapes `agent-run-supervisor` emits: the persisted `result.json` payload,
> the terminal status and error-code vocabulary, the normalized event stream, and
> the stale-lock detector projection. It is **derivative and descriptive**: it
> documents the schema *as the code emits it today* (`result.py`,
> `exit_classifier.py`, `native_acp/event_writer.py`, `session.py`). It does
> **not** redefine product goals, expand scope, grant any runtime/live approval,
> or introduce a business verdict.
>
> **One runtime.** ARS is `arsd` + ars-core + Native ACP.
> The retired acpx path was removed from source.
> That removal took its CLI leaves, its parser and probe surfaces, and every
> result field named after it; the shapes below are what the Native emitters
> write.
> acpx is not a product, runtime, or compatibility surface. New
> product scope, Native terminal semantics, module design, staging, and
> acceptance come only from GOAL/PRD/design/roadmap/active plan.
>
> **Evidence rules for the reset line.** [§5](#5-native-reset-line-evidence-rules)
> records the environment-value, withholding, and policy-warning rules that every
> Native emitter must obey, and the audit that found no launch, provenance, or
> attestation field in the caller-stable contract above. Those rules are normative
> and are **merged on `main`**; published package/release facts come from live
> GitHub Releases and PyPI, and deployed/running facts come from operator-held
> runtime/live checks.
>
> **Stability rule (read this first).** `business_verdict` is **always `null`**
> and caller-owned — the supervisor never sets it. API v3 is the only contract
> and its persisted-terminal field set is **closed**: a `result.json` carrying a
> key this version does not define is untrusted evidence, not a tolerated
> extension. Adding a key is a contract change made here, in the table below,
> and existing keys are never renamed, removed, or repurposed. See
> [§4 Caller-stability contract](#4-caller-stability-contract).
>
> The top-level `result.json` key set is pinned against
> `result.build_result_payload` by `tests/test_result_event_schema.py`, so this
> document cannot silently drift from the code.

## 1. `result.json` payload

`result.build_result_payload(...)` is the single emitter of the per-Run terminal
the supervisor persists at `<run_dir>/result.json`. Native code reaches it through
`build_native_result_payload`, which additionally judges that the one
child-authored field is really a `str`.

The table below is the authoritative top-level key set. Types use JSON spelling
(`string`, `number`, `boolean`, `object`, `null`). "Always present" means the
key is always serialized (its value may still be `null`).

<!-- result-json-keys:begin -->

| Key | Type | Always present | Meaning |
|-----|------|----------------|---------|
| `run_id` | `string` | yes | Run identifier. |
| `status` | `string` | yes | Supervisor terminal status (see [§2](#2-statuses-and-error-codes)). Never a business verdict. |
| `business_verdict` | `null` | yes | **Always `null`.** Caller-owned; the supervisor never sets it. |
| `error_code` | `string` \| `null` | yes | Stable error code for the status, or `null` when `status == "completed"` (see [§2.2](#22-error-codes)). |
| `detail_code` | `string` \| `null` | yes | Finer categorical detail (e.g. `REGISTRATION_FAILED`, `EMERGENCY_FINALIZE`, `EVIDENCE_PIPELINE`), or `null`. |
| `origin` | `string` | yes | Where the outcome originated: `acp` (an ACP terminal was observed) or `supervisor`. |
| `retryable` | `boolean` | yes | Whether the supervisor considers the status safe to retry (status-derived, never caller-set). |
| `signal` | `number` \| `null` | yes | Terminating signal number for the supervised child, or `null`. |
| `stop_reason` | `string` \| `null` | yes | ACP `stopReason` from the turn, or `null`. |
| `usage` | `object` \| `null` | yes | Bounded usage payload as reported over ACP, or `null`. |
| `final_message` | `string` | yes | Redacted concatenated agent message text (may be empty). |
| `truncated` | `boolean` | yes | Whether the final message hit its ingestion ceiling. |
| `truncate_reason` | `string` \| `null` | yes | Reason for truncation (e.g. `max_final_message_bytes`), or `null`. |
| `observed_effect` | `boolean` \| `null` | yes | Whether the observed stream showed agent output or tool activity; `null` when nothing was observed. |
| `run_dir` | `string` | yes | Absolute path to the Run artifact directory. |
| `stderr_path` | `string` | yes | Run-dir-relative path to the redacted stderr log (default `stderr.log`). |
| `raw_event_path` | `string` | yes | Run-dir-relative path to the persisted event stream. Native emitters pass `events.jsonl`; the parameter is required, so no caller can inherit a default it did not choose. |
| `redaction_report_path` | `string` | yes | Run-dir-relative path to the redaction report (default `redaction-report.json`). |

<!-- result-json-keys:end -->

**There is no process-exit field.** API v3 results carry no `exit_code` and no
key named after the retired runtime's exit code. It was the one field in a
terminal that described *the child process* rather than the Run, and it left with
the runtime that produced it — not renamed, not replaced. What survives about an
abnormal end is the Run-level vocabulary: `status`, `detail_code`, `signal`, and
`stop_reason`.

There is **no v1/v2 compatibility and no readability of an older record**. A
`result.json` carrying that retired key — or any key this version does not
define — is untrusted evidence: it is refused whole, never projected, never
read, and never re-emitted, so no caller can observe it. Nothing migrates,
rewrites, or resets a stored record; an untrusted terminal simply never becomes
trusted evidence. Both directions are pinned by
`tests/test_result_event_schema.py` and at the reachable `run/status` and
`run/cancel` boundaries.

Native terminals may additionally carry the optional keys `session_id` and
`failure_reason`. `failure_reason`, when present and non-null, is one of a fixed
categorical allowlist (`result.ALLOWED_FAILURE_REASONS`) — never raw exception
text, a path, a class name, or credential-shaped material.

`result.json` carries **no embedded `schema_version` field today**; the closed
field set in [§4](#4-caller-stability-contract) is what a reader validates
against. (The session *record* `session.json` does carry an integer
`schema_version`, and its field set is closed the same way.)

## 2. Statuses and error codes

Supervisor status is owned by `exit_classifier.AgentRunStatus` and is **never** the
caller's business verdict.

### 2.1 Status set (5)

`completed`, `failed`, `cancelled`, `timed_out`, `unknown` — the complete Native
ACP terminal vocabulary of PRD R5, and deliberately nothing beside it.

The wider status set this document once listed belonged to a process-exit
classifier that mapped a retired runtime's exit codes onto supervisor statuses.
That classifier, its exit-code table, and its statuses are removed from source
rather than deprecated: a persisted record claiming one of them fails the enum
lookup and is classified untrusted evidence.

`unknown` is the load-bearing one. A prompt that may have been dispatched without
a trustworthy terminal ends as `status = unknown`, `retryable = false`, with its
Session quarantined. It is never replayed, resumed, or retried automatically.

### 2.2 Error codes

`result._ERROR_CODE_FOR_STATUS` maps each status to its `error_code`
(`completed → null`):

| `status` | `error_code` | `retryable` default |
|----------|--------------|---------------------|
| `completed` | `null` | `false` |
| `failed` | `FAILED` | `false` |
| `cancelled` | `CANCELLED` | `false` |
| `timed_out` | `TIMED_OUT` | `true` |
| `unknown` | `UNKNOWN` | `false` |

`retryable` is the status-derived default (`exit_classifier._RETRYABLE_DEFAULT`);
a terminal whose `retryable` disagrees with it is untrusted. `detail_code` carries
the finer categorical detail.

Trusted Native terminals must also satisfy an exact status/origin/stop/detail
grammar (`result._native_terminal_semantic_grammar_ok`): `origin = acp` always
requires ACP stop evidence, `completed` requires a completed-class `stop_reason`,
`cancelled` requires `stop_reason = cancelled`, and `unknown` requires
`origin = supervisor` with no ACP stop. A record outside the grammar is untrusted
rather than repaired.

## 3. Normalized event stream

`native_acp/event_writer.py` owns each Run's `normalized-events.jsonl`: one
writer, monotonic `seq` starting at `1`, bounded queues, and explicit truncation
markers. Each line has a `type` and a small allow-listed set of structural
fields — never bulk content, never raw agent text beyond the bounded, redacted
fields the writer is given.

`key_summary` on an `unknown_update` is a comma-joined list of `path:type`
structural hints only — never values. Watchdog/kill/lifecycle metadata is not a
stream event; it is attached to the Run `result.json`.

## 4. Caller-stability contract

- **`business_verdict` is always `null`.** The supervisor never sets a business
  verdict. Supervisor status (`status`/`error_code`) is **not** a business
  pass/fail.
- **Closed field set.** Future schema changes may *add* keys here, in this
  document, together with the emitter and the validator. Existing keys are never
  renamed, removed, or repurposed, and their meaning is fixed. A persisted
  terminal is judged against the closed set — an undefined key makes it
  untrusted rather than tolerated.
- **The field set of a persisted terminal is closed.** API v3 is the only
  contract, so a `result.json` carrying a key this version does not define is
  untrusted evidence rather than a tolerated extension. There is no reader for
  an unknown key, no projection that strips one, and no alias. Nothing rewrites
  or migrates a stored record: an untrusted terminal simply never becomes
  trusted evidence, and the caller sees the same bounded error every other
  malformed terminal produces.
- **The acpx-named keys were removed with it.** Removing the retired runtime
  retired the keys its emitters produced, as one authorized decision taken while
  API v3 was unreleased — a deletion of an emitter, recorded here as history.
- **Versioning / compatibility.** `result.json` has no embedded `schema_version`
  today; a persisted terminal is validated against the closed field set, and so
  is a session record. The session record `session.json` carries
  an integer `schema_version` for record-format evolution.
- **Drift guard.** `tests/test_result_event_schema.py` pins the
  [§1](#1-resultjson-payload) top-level key set against
  `result.build_result_payload`, so this contract cannot drift from the code
  unnoticed.

The operator-facing command output — `agents validate`, `agents doctor`, and
`run inspect` — is specified in
[`agent-registry.md`](agent-registry.md), which owns the registry contract.

## 5. Native reset-line evidence rules

Normative for every Native emitter on the V4 boundary-reset line, and **merged on `main`**; published
package/release facts come from live GitHub Releases and PyPI, and deployed/running facts come from
operator-held runtime/live checks. Nothing in this section changes any key documented in §1–§4: those keys
keep their names, types, and meanings, and the closed field set still holds.

### 5.1 Audit result — no launch, provenance, or attestation field, and no structured environment value

The caller-stable contract in §1–§4 was audited field by field against the reset boundary. Result:

| Looked for | Found |
|---|---|
| a launch/provenance/runtime-identity field (artifact path, version, digest, tree hash, interpreter identity, generation, slot hash, acceptance receipt) | **none.** No payload, projection, or event family above carries one |
| an attestation or integrity field | **none** |
| a **structured** environment key or value field (`fixed_env`, `permission_env`, an overlay literal, a pass-through value, a mediation pair) | **none.** No payload, projection, or event family above carries one, and ARS never serializes one out of the resolved carrier |
| a field that could carry one **incidentally**, because an AGENT put it there | `detail_code`, `final_message`, `stop_reason`, `usage`, and `unknown_update.key_summary` — all free-form or agent-supplied. These are **not** scanned against this Run's projected values (§5.2), so an agent echo can appear in one |

The retired Binding-era launch and attestation material lived in `launch.json` and `attestation.json`, which
this document never described. It is retired with the architecture and preserved only under
[`docs/archive/binding-era-2026-07/`](../archive/binding-era-2026-07/README.md).

On the reset line `launch.json` is a **value-blind launch snapshot** whose key set is closed by a schema-level
allowlist: the declared `command`, the exact `argv`, profile identity, `agent_id`, the selector ids, the
capability narrowing, the caller's `credential_refs`, the operator's `session_epoch`, the selected
`mediation_id`, one `env` block of the shape described in §5.2, and — only when the resolved profile selected
a launch-permission policy — the pair `launch_permission_policy_id` and `launch_permission_digest`.
`fixed_env`, `permission_env`, `env_allowlist`, `expected_runtime`, and `runtime_provenance` are not merely
absent — a document carrying one is rejected as not a production record, so none can be reintroduced by an
additive edit. `attestation.json` is not written at all; the post-`initialize` artifact is
`initialize_evidence.json`, which records the observation with an explicit `authoritative: false` and carries
no expected-identity block to compare against (§5.5).

**The launch-permission pair is bound, not merely carried.** It is one fact in three places, and both the
writer and the reader enforce all three together, so an inconsistent record is refused rather than
interpreted:

| Rule | Meaning |
|---|---|
| all-or-none | `launch_permission_policy_id` and `launch_permission_digest` are absent together or present together. Half a pair is inconsistent evidence, not weaker evidence |
| closed vocabulary | the policy id is one of the registered source-owned ids |
| canonical digest | the digest must **equal** the SHA-256 of the registered policy's own canonical document bytes, not merely match `sha256:<64 lowercase hex>`. The document is fixed source bytes and capability validation gates the compile without editing them, so a correctly shaped but different digest is refused at every seam |
| reserved-key ownership | a reserved launch-permission environment name belongs to source `launch_permission` and to no other. The check is name-first, so relabelling that name `base`, `passthrough`, `overlay`, or `mediation` does not launder it |
| projection agreement | when the pair is present, the `env` block carries exactly one name with source `launch_permission`, and it is the reserved key that policy owns — no relabelled duplicate, and no other name on that layer. When the pair is absent, no reserved name appears under any source and nothing appears on that layer |

The digest binds the document, not its location: neither the directory nor the document text is ever
persisted. This is an internal-consistency contract over records ARS wrote, not a tamper-resistance or
cryptographic-trust claim — an actor who can rewrite an ARS-owned Run artifact has already defeated every
projection boundary downstream of it.

### 5.2 Structured environment evidence is value-blind; free-form Run text is not scanned

Structured environment evidence is value-blind by construction: per name, the name, its source class, its
precedence layer, and its redaction status, plus a resolved count, the mediation id, and the
declared-but-absent names. **No value, value digest, keyed digest, length, prefix, suffix, or equality token
is ever a field or a hash input.** Two Runs whose transmitted value changed may therefore share a launch
hash: the hash proves the declared projection, not the secret.

**There is no per-Run exact-literal guard over free-form text.** Agent, thought, and final-message text,
normalized update fields, dynamic tool/config/permission keys, permission and filesystem evidence, discovery
and effective state, usage metadata, bounded stderr, and the external Session id are emitted as the agent
produced them, subject to the static shape redactor (API key, `Authorization: Bearer`, JWT, PEM) and to the
existing byte/event/final-message ceilings. A caller must therefore treat this material as **agent-authored
content**, not as a value-blind projection: an AGENT that echoes a projected environment value into any of
those fields may have that value persisted.

The canonical workspace root and the effective `cwd` remain complete literals and remain hash-covered —
independently derived authority facts.

### 5.3 Categorical withholding metadata

Some emitters still withhold a whole field or record behind a **stable categorical marker** rather than
emitting a partial value. Markers are fixed source literals that contain no input data.

| Marker class | Meaning for a caller |
|---|---|
| legacy text evidence withheld | the record predates the reset and carries value-bearing material (§5.4) |
| legacy value-bearing launch seal not verified | the record predates the reset, so no hash was recomputed over it (§5.4) |
| launch permission cleanup failed | the private per-Run launch-permission material could not be removed after the child was proven reaped. Durable as `launch-permission-cleanup-failed.json` in the Run directory — `{"code", "run_id"}` and nothing else — and, when an event stream was still open, additionally as a `launch_permission_cleanup_failed` event carrying only `seq` and `type`. It is **hygiene, not a supervision verdict**: the Run's terminal status is unaffected. It is written once, so a later successful retry removes the leftover without erasing the fact that the in-order attempt failed |

Two marker classes are **retired** with the per-Run literal guard: the generic "withheld field or record"
suppression and the "unsanitized log record suppressed" replacement. Records written while that guard was
live keep the markers they were written with — they remain readable, schema-valid, and are never rewritten —
so a reader must still understand them, but no new emitter produces them. The external-Session-id
sensitive-collision refusal is retired with them: an id is now recorded as the agent minted it.

Callers should treat every marker as forward-compatible: new marker classes may be added, existing ones never
change meaning.

### 5.4 Legacy text withholding

Pre-reset Run and Session records may contain environment values in their launch material and unguarded
free-form text everywhere else. They are **immutable historical evidence**: the reset rewrites nothing,
migrates nothing, deletes nothing, and re-hashes nothing, and it makes **no retroactive-erasure claim**.

Reset-line readers obey these rules:

- classify the record schema **before** selecting a verifier, so the legacy branch returns through the safe
  projection and the "recompute first, label legacy later" order is structurally impossible;
- mark the record value-bearing with environment values withheld, and report launch-seal verification as not
  performed for a value-bearing legacy record;
- never return legacy environment fields, raw launch or spec documents, value-bearing embedded seal material,
  or any pre-reset launch hash derived from values;
- never call a launch-hash recomputation over a value-bearing record;
- expose, for legacy Runs, only trusted categorical terminal status and independently safe owner and Run
  metadata, withholding legacy free-form final messages, error detail, events, stderr, effective and
  discovery text, and raw inspection documents categorically;
- keep legacy Session `status`/`list` available and owner-scoped, while withholding the external
  session id and the retired value-derived identity hashes from the response allowlist.

Direct filesystem access to old files by an authorized operator stays outside the daemon projection. Those
bytes may contain historical values; that is an honest limit, not a covered surface.

### 5.5 Policy-warning event

Observed facts never gate a Run and never block continuity, but drift is worth telling a human about. The
reset line therefore adds one additive event family whose only purpose is to report an observation, never to
change an outcome.

| Field | Type | Meaning |
|---|---|---|
| `type` | `string` | the policy-warning family name — always `policy_warning` |
| `code` | `string` | the stable machine-readable pairing of `subject` and `comparison`, for callers that switch on one token. One of `AGENT_SELF_REPORT_CHANGED`, `ADVERTISED_CAPABILITIES_CHANGED` |
| `subject` | `string` | which non-authoritative observation drifted. One of `agent_self_report` (the agent's self-reported name and version, taken together), `advertised_capabilities` (the advertised capability set) |
| `comparison` | `string` | what it was compared against, always a *record*, never a gate. One of `previous_run_of_session` |
| `authoritative` | `boolean` | **always `false`** |
| `refused` | `boolean` | **always `false`** — the Run continued and the Session stayed reusable |

Every field is present on every warning, and every string value is drawn from the closed vocabularies above:
a policy warning contains no free-form text at all. `code` is documented as part of the shape rather than as
an alternative to it — it does not replace `subject`, `comparison`, or `refused`, and a caller may read
either the token or the pair.

The event carries no value, no digest, no length, and no raw child text: its subject names *which* fact
drifted, not what the fact was. A caller must not treat a policy warning as a failure signal, a business
verdict, or grounds to retire a Session, and no ARS code path branches on one. Zero policy-warning events
means no observed drift, never that a check was skipped.

Adding a subject or a comparison is an ordinary additive change and extends the closed set here in the same
commit as the emitter — the two are one contract, not a document that describes an implementation.

### 5.6 What does not change

The terminal vocabulary, the closed field set, `business_verdict: null`, owner scoping, event ordering with
its monotonic `seq`, bounded queues and truncation markers, and the caller-facing event grammar are unchanged
by the reset. The reset changes what may appear *inside* a free-form field, adds withholding metadata and the
policy-warning family, and moves the caller wire to `api_version` 3 for the reasons in the PRD — not the
grammar a caller parses.

### 5.7 The no-close Session projection

`session_status` and `session_list` project identity, lease/activity facts, last-use observations, and
optional quarantine evidence. They expose **no** synthetic Session lifecycle state, because none exists:

| Field | Type | Meaning |
|---|---|---|
| `session_id` | `string` | the durable ARS Session identifier |
| `owner`, `namespace` | `string` | the authenticated identity the Session belongs to |
| `agent_id` | `string` \| `null` | the registered agent this Session binds |
| `profile_id` | `string` \| `null` | the source profile identity |
| `created_at`, `updated_at` | `string` \| `null` | creation and last-use timestamps |
| `last_effective_model`, `last_effective_effort` | `string` \| `null` | the last exact-readback-proven pair |
| `quarantine` | `object` \| `null` | `{reason_code, source_run_id, recorded_at}` when continuity was proven unsafe, else `null` |

`quarantine.reason_code` is drawn from a closed source-owned vocabulary. It is never an exception message,
never remote or agent-authored text, and never a path, so the evidence is bounded and categorical by
construction. The external AGENT session id is never projected.

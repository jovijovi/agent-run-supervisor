---
title: Events
description: The normalized, seq-ordered event stream — ordering, bounds, truncation, history-replay separation, and the additive event families.
---

# Events

Each Run gets one normalized event stream, written by one writer, with a
monotonic `seq` starting at `1`.

## Guarantees

| Property | Guarantee |
|---|---|
| Ordering | monotonic `seq`, starting at `1`, never reordered |
| Fields | a `type` plus a small allow-listed set of structural fields |
| Content | never bulk content, and never raw agent text beyond the bounded, redacted fields the writer is given |
| Buffering | one bounded serial ledger with explicit truncation markers. It assigns the actual sequence before retaining the canonical line, charges exact UTF-8 bytes through durable acknowledgement, and grows only through approved rungs while the writer is making durable progress |
| Backpressure | FIFO admission gets one absolute five-second deadline at acceptance; every pump checks an overdue head before room or growth. Caller cancellation cannot remove an accepted event |
| Close truth | clean close certifies that every accepted sequence was durably acknowledged; a failed, expired, or unacknowledged ticket makes the Run fail closed |

Reading is either a bounded page (`from_seq` + `limit`) or a follow
subscription. A follow subscriber that falls too far behind its bounded queue is
dropped with `EVENT_BACKLOG_EXCEEDED` rather than allowed to grow without limit.

!!! note "Watchdog and kill metadata is not a stream event"

    Lifecycle metadata about the supervised process is attached to the Run's
    [result](results.md), not emitted into the event stream.

## Structural hints, never values

An update ARS cannot classify is emitted as an `unknown_update`. Its
`key_summary` is a comma-joined list of `path:type` **structural hints only** —
never values:

```json
{"seq": 42, "type": "unknown_update", "key_summary": "result.items:list,result.count:int"}
```

## Truncation

When a bound is reached, the stream says so explicitly rather than silently
dropping:

| Field | Meaning |
|---|---|
| `truncated` | the boundary was hit |
| `truncate_reason` | which ceiling, e.g. `max_event_bytes` |

Ceilings come from the Run's `limits` (`max_stderr_bytes`, `max_event_bytes`,
`max_events`), or the sealed defaults when the caller sent `{}`.

The retained line already contains its actual `seq` and trailing newline. The
writer passes that same string to the durable append seam; no consumer-side
serialization or maximum-sequence estimate can change the bytes after
accounting. `last_seq` reports only the durably acknowledged contiguous prefix.

## The policy-warning family

One additive family exists whose only purpose is to report an observation. It
never changes an outcome.

| Field | Type | Meaning |
|---|---|---|
| `type` | `string` | always `policy_warning` |
| `code` | `string` | the stable pairing of `subject` and `comparison`. One of `AGENT_SELF_REPORT_CHANGED`, `ADVERTISED_CAPABILITIES_CHANGED` |
| `subject` | `string` | which non-authoritative observation drifted. One of `agent_self_report`, `advertised_capabilities` |
| `comparison` | `string` | what it was compared against, always a record. One of `previous_run_of_session` |
| `authoritative` | `boolean` | **always `false`** |
| `refused` | `boolean` | **always `false`** — the Run continued and the Session stayed reusable |

```json
{
  "seq": 7,
  "type": "policy_warning",
  "code": "AGENT_SELF_REPORT_CHANGED",
  "subject": "agent_self_report",
  "comparison": "previous_run_of_session",
  "authoritative": false,
  "refused": false
}
```

Every field is present on every warning, and every string value is drawn from
the closed vocabularies above: **a policy warning contains no free-form text at
all**. It names *which* fact drifted, never what the fact was.

!!! contract "How a caller must treat it"

    Not a failure signal, not a business verdict, and not grounds to retire a
    Session — no ARS code path branches on one either. Zero policy-warning events
    means no observed drift, never that a check was skipped.

    A caller may read the `code` token or the `subject`/`comparison` pair; they
    are the same fact, and `code` does not replace the pair.

## The session-replay-summary family

An AGENT that supports Session reuse may replay its conversation history when
ARS loads the Session. Those updates describe turns of Runs that already
finished, so they are **not** this Run's execution: they produce no per-event
evidence, no permission-mediation records, no tool-call activity, and no
`final_message` text here. They are still checked against the expected external
Session identity first — separation happens after that check, never instead of
it.

Replay is not dropped silently either. A Run that observed any keeps exactly one
event:

| Field | Type | Meaning |
|---|---|---|
| `type` | `string` | always `session_replay_summary` |
| `updates` | `number` | how many replayed updates this Run observed |
| `by_kind` | `object` | counts keyed by ACP `sessionUpdate` kind, from a closed set; anything else counts under `other` |

```json
{"seq": 3, "type": "session_replay_summary", "updates": 412, "by_kind": {"agent_message_chunk": 380, "tool_call": 32}}
```

!!! contract "How a caller must treat it"

    Counts only — no replayed text, no tool identifier, no digest, and no key the
    AGENT chose. At most one per Run, and a Run that saw no replay emits none:
    zero means no replay was observed, never that a check was skipped. Nothing in
    ARS branches on it.

## The permission families

Two families record permission mediation: what ARS decided, and what later
contradicted a decision ARS made.

### `permission_mediation` — one event per decision

| Field | Type | Meaning |
|---|---|---|
| `type` | `string` | always `permission_mediation` |
| `requested_op` | `string` | what ARS was asked about: `permission:<acp-kind>` for a `session/request_permission`, or `fs_read` / `fs_write` for a client-bound filesystem request |
| `decision` | `string` | `allow` or `deny` |
| `reason` | `string` | ARS-authored categorical text, and the same text the agent receives. Never the path, option id, or agent payload it decided about |
| `tool_call_id` | `string` | present when the decision names a specific tool call; omitted when nothing named one |

### `permission_violation` — a completion that contradicts a decision

| Field | Type | Meaning |
|---|---|---|
| `type` | `string` | always `permission_violation` |
| `violation_class` | `string` | `completed_after_deny` or `missing_grant_capability` |
| `tool_call_id` | `string` \| `null` | the call that reported `completed` |
| `kind` | `string` \| `null` | the ACP tool kind, correlated by tool-call id or taken from ARS's own record of what it refused — never inferred from agent text |
| `required_capability` | `string` | **only** on `missing_grant_capability`: the grant capability that could have allowed it |
| `reason` | `string` | ARS-authored categorical text |

`completed_after_deny` means ARS refused *that same call* — at its permission
prompt, or at the `fs/read_text_file` it issued — and the agent reported it
`completed` anyway. `missing_grant_capability` means a write-family tool
completed without the capability that could ever have allowed it, with no
mediation at all.

!!! warning "Detection, never prevention"

    A violation is observed **after** a completion is reported. The side effect
    may already have happened; ARS cannot undo it and does not claim to have
    prevented it, and a violation is not evidence that the effect was blocked.
    Mediation is cooperative agent-policy enforcement — **not** an OS sandbox,
    container, UID boundary, or filesystem isolation, which remain the only
    real containment.

!!! contract "A refused call that reports `failed` is healthy"

    That is the honored-refusal shape: no violation, the Run completes
    normally, and the deny mediation stays in the stream. Only `completed`
    after a refusal is the contradiction.

`fs/read_text_file` carries no tool-call id of its own, so a refusal there is
attributed to a call only when the current turn has **exactly one** unresolved
tool call, **and it is** `read`/`search`. A second unresolved call of any kind —
including one that has not declared a kind yet — none at all, or more unresolved
calls than the bridge tracks all attribute nothing: an unattributed refusal is a
missed detection, which is bounded, while a wrong one would fail a Run that did
nothing wrong. Arrival order, timing, prompt text, and agent output are never
used to correlate.

A violation finalizes the Run as `failed` with
[`detail_code = PERMISSION_VIOLATION`](results.md) and `retryable = false`; the
Run emits `run_failed` and **no** `run_completed`, and the Session stays
reusable — this is a refused operation, not continuity doubt.

Replayed history is excluded from all of this: a `session/load` conversation
replay produces no mediation records, no tool-call accounting, and no violation.

## Free-form fields are agent-authored

!!! warning "There is no per-Run exact-literal guard over free-form text"

    Agent, thought, and final-message text, normalized update fields, dynamic
    tool/config/permission keys, permission and filesystem evidence, discovery
    and effective state, usage metadata, bounded stderr, and the external Session
    id are emitted **as the agent produced them**, subject only to:

    - the static shape redactor — API key, `Authorization: Bearer`, JWT, PEM; and
    - the byte, event, and final-message ceilings.

    Treat this material as agent-authored content, not as a value-blind
    projection. An agent that echoes a projected environment value into any of
    those fields may have that value persisted.

Structured environment evidence is the opposite: value-blind by construction. No
value, value digest, keyed digest, length, prefix, suffix, or equality token is
ever a field or a hash input. Two Runs whose transmitted value changed may
therefore share a launch hash — the hash proves the declared projection, not the
secret.

## Categorical withholding markers

Some emitters withhold a whole field or record behind a **stable categorical
marker** rather than emitting a partial value. Markers are fixed source literals
containing no input data.

| Marker class | Meaning for a caller |
|---|---|
| legacy text evidence withheld | the record predates the current schema and carries value-bearing material |
| legacy value-bearing launch seal not verified | no hash was recomputed over a pre-reset record |
| launch permission cleanup failed | private per-Run launch-permission material could not be removed after the child was proven reaped. **Hygiene, not a supervision verdict** — the Run's terminal status is unaffected |

Treat every marker as forward-compatible: new marker classes may be added,
existing ones never change meaning.

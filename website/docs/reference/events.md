---
title: Events
description: The normalized, seq-ordered event stream — ordering, bounds, truncation, and the policy-warning family.
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
| Queues | bounded, with explicit truncation markers |

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

---
title: OpenCode
description: Registering OpenCode with ARS, and the mediation binding that routes its privileged tool families through ACP.
---

# OpenCode

OpenCode is the agent the registered mediation binding was written for. That
makes its entry short and its one important decision explicit.

## The registry entry

```toml title="agents.toml"
[agents.opencode]
profile   = "standard-native-acp-v1"
command   = "<the-opencode-executable>"
args      = ["<acp-subcommand-or-flags>"]
mediation = "ask-privileged-tool-families-v1"
```

## What the mediation binding does

`ask-privileged-tool-families-v1` projects one **source-owned** environment pair
that puts OpenCode's privileged tool families — shell, edit, and web fetch —
into "ask" behaviour, so each one raises an ACP permission request and the
permission bridge decides **before** the side effect rather than after it.

The binding is closed on purpose:

- It is source-owned **in key and in value**. Your entry may *select* the id, or
  select none. It can never author the pair, the key, or the value, and there is
  no `mediation = off`.
- Its key is **reserved globally** — the union of every key in any registered
  binding, not just the one you selected. If your `env_overlay` contains that key
  or your `env_passthrough` names it, the parse fails with
  `MEDIATION_KEY_COLLISION` and the daemon refuses to listen. `agents validate`
  applies the identical check offline, so you see it at authoring time.
- It is applied **last** among the layers you can influence, as defense in depth:
  a defect in the collision check cannot silently disable mediation.

!!! warning "Do not declare the mediation key yourself"

    If you set OpenCode's permission environment variable in your own
    `env_overlay`, the file is refused at parse time rather than one Run failing
    later. That is the intended outcome: an operator-authored value would make
    the default-deny claim decorative, so the collision is a startup refusal
    instead of a silent override.

## What it does not cover: reads and searches

The registered binding names three families — shell, edit, and web fetch — and
that is the complete list. **Reading and searching files are not mediated by
it.** Under this entry OpenCode reads and greps inside the bound workspace
according to its own configuration, and those tool calls may raise no ACP
permission request at all.

There is no registered binding that adds them, and the reason is measured
rather than assumed. A candidate value covering seven families — shell, edit,
external directory, glob, grep, read, and web fetch — was probed against
**OpenCode 1.18.18** in three one-shot Runs (an internal read, an internal
content search, and a workspace symlink pointing outside the workspace). What
that probe found:

- the adapter **does** ask before the effect: one `session/request_permission`
  per tool call, with the correct ACP kind (`read` for the read tool, `search`
  for the search tool — never `execute`), carrying a real tool-call id, while
  the call is still pending;
- it **honors the answer**: every refused call then reported `failed`, never
  `completed`;
- but it declares **no `toolCall.locations` entry at all**. The permission
  request says that a read is about to happen and never says *what* would be
  read.

A `read` grant answers whether the agent may read, never what, so ARS takes its
containment answer only from the request's protocol-declared locations. With
none declared, every such request is denied fail-closed with
`read-like permission request has no usable locations` — including the
legitimate workspace-internal ones. A lane that must refuse every read is not
read support, so no binding claims it.

!!! warning "Do not read the symlink case as containment"

    In that probe the outside-symlink read was refused and its planted token
    never reached the Run's evidence. It was refused for the *same* categorical
    reason as the two legitimate reads — no declared location — not because ARS
    proved the target was outside the workspace. Treat it as default-deny
    working, never as filesystem isolation.

This is an adapter capability statement about a tested version, not a defect
ARS repairs and not a limit of the permission bridge: the bridge already
validates declared locations for agents that send them, and the bounded
`fs/read_text_file` callback stays fully mediated for agents that use it (this
version does not — it reads in-process).

## Selecting no mediation

`mediation` is optional. An entry that omits it projects no mediation pair at
all, which means OpenCode's in-process tool families run under whatever its own
configuration says — potentially with **no ACP permission event at all**.

That is a legitimate choice for a read-only workflow you have reasoned about, and
a dangerous one otherwise. If you omit it, the denied-action canary below is the
only thing that will tell you what actually happens.

## Environment

`HOME` is projected unchanged, so OpenCode's own configuration, auth store, and
caches work exactly as they do interactively. Declare anything else it needs:

```toml
[agents.opencode]
profile   = "standard-native-acp-v1"
command   = "<the-opencode-executable>"
args      = ["<acp-subcommand-or-flags>"]
mediation = "ask-privileged-tool-families-v1"

env_passthrough = ["SOME_PROVIDER_TOKEN"]
env_overlay     = { PATH = "/usr/local/bin:/usr/bin:/bin" }
```

Mediation values are **withheld from evidence**: a Run records the mediation id
and the projected environment **name** with source class `mediation`, never the
pair's value.

## Before first use

Run the full [registration sequence](register-an-agent.md), and treat the
**denied-action canary** as the acceptance test for this agent specifically:

1. Submit a Run whose frozen grant does not permit writing.
2. Prompt the agent to edit a file in the bound workspace.
3. Confirm a permission request appears in the Run's event stream and that the
   decision is a denial.

Zero permission events is not a pass. It means the tool family you care about was
never routed through the bridge — which is exactly the condition the mediation
binding exists to remove, and exactly the condition an omitted `mediation` leaves
in place.

A denial the agent honours is the healthy result: the tool call reports
`failed` and the Run completes normally. If instead the agent reports that same
call `completed`, ARS records a
[`permission_violation`](../reference/events.md#the-permission-families) and
fails the Run with `PERMISSION_VIOLATION`. That is detection after the fact —
the side effect may already have happened — so treat it as a reason to stop
using that agent version for that grant, not as proof the operation was
blocked.

## After an OpenCode upgrade

An agent upgrade behind an unchanged registered command costs no ARS action and
does not invalidate Sessions. But the mediation binding's effect depends on
OpenCode continuing to honour that permission variable with those semantics, and
that is the agent's behaviour rather than an ARS guarantee. Re-run the canary
after an upgrade that could plausibly change permission handling.

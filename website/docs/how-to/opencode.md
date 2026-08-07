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

## After an OpenCode upgrade

An agent upgrade behind an unchanged registered command costs no ARS action and
does not invalidate Sessions. But the mediation binding's effect depends on
OpenCode continuing to honour that permission variable with those semantics, and
that is the agent's behaviour rather than an ARS guarantee. Re-run the canary
after an upgrade that could plausibly change permission handling.

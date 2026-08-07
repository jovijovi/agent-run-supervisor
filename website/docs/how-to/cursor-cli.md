---
title: Cursor CLI
description: Registering Cursor CLI with ARS on the model-only profile, and how its permission mode is driven by the Run's frozen grant.
---

# Cursor CLI

Cursor CLI runs under its own compatibility profile, `cursor-native-acp-v1`,
because it carries a cited ACP-semantic deviation of its own. Everything else
about registering it is the ordinary [registration
workflow](register-an-agent.md).

## The registry entry

```toml title="agents.toml"
[agents.cursor-cli]
profile = "cursor-native-acp-v1"
command = "<the-cursor-cli-executable>"
args    = ["<acp-subcommand-or-flags>"]
```

## Deviation 1 — model-only configuration fidelity

This agent's **model selector is the whole configuration**. There is no
independent effort selector to discover or set, so the profile declares
model-only configuration fidelity and ARS behaves accordingly:

- it discovers no effort selector,
- it sets none, and
- it reports `N/A` as the effective effort.

!!! warning "A caller targeting this agent must request effort `N/A`"

    Any other value fails before the prompt. And `effort_selector` is **refused
    on this profile** at parse time: an id hint for a selector no Run ever sets
    would be a fiction in every launch snapshot, so the pairing is refused rather
    than quietly ignored.

## Deviation 2 — a grant-driven permission mode

At revision 3 the profile adds its second frozen term: the agent's ACP `mode` is
driven by one closed, source-owned policy from the Run's **frozen grant**.

| The Run's `grant_capabilities` | Mode |
|---|---|
| exactly a subset of `{read, search}` | `ask` |
| every other valid grant | `agent` |

The mode is set and **exact-read-back before the model**, and re-proven after it.
If either proof fails, the Run fails before any prompt.

You do not configure this. It is a profile fact: you cannot select the mode,
author it, or disable it, and no registry field influences it.

!!! danger "This is a mitigation, not a boundary"

    The mode selection is a **cooperative mitigation** of an agent that can
    complete an edit in `agent` mode without ever asking. It is not an OS sandbox
    and not a strong permission guarantee.

    ACP permission mediation and the post-completion violation detector remain
    the enforcement line. Run the mandatory denied-action canary for this agent
    like any other.

## What the profile does not change

Every other frozen term equals the standard contract. In particular the profile:

- adds no startup permission policy, and
- **never repoints the agent's own configuration root**, so agent-owned Session
  state stays where the agent put it and resumes through a real `session/load`.

## Environment

`HOME` is projected unchanged, so the agent's own credential store, caches, and
user configuration behave as they do interactively. Declare anything the base
allowlist does not cover:

```toml
[agents.cursor-cli]
profile = "cursor-native-acp-v1"
command = "<the-cursor-cli-executable>"
args    = ["<acp-subcommand-or-flags>"]
env_overlay = { PATH = "/usr/local/bin:/usr/bin:/bin" }
```

## Before first use

Run the full [registration sequence](register-an-agent.md), including the
mandatory denied-action canary — and design the canary's grant deliberately,
because on this agent the grant also decides the mode. A canary whose grant is a
subset of `{read, search}` exercises `ask` mode; one with a write capability
exercises `agent` mode. Prove denial in whichever mode your real Runs will use.

## After a Cursor CLI upgrade

The mode and model selectors are ACP-level observations of a specific agent
version. Re-run `agents doctor` and the canary after an upgrade. An upgrade
behind an unchanged registered command costs no ARS action and does not
invalidate Sessions.

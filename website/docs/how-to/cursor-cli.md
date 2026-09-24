---
title: Cursor CLI
description: Registering Cursor CLI with ARS on its parameterized profile, and how its permission mode is driven by the Run's frozen grant.
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

## Deviation 1 — parameterized configuration fidelity

At revision 4 the profile negotiates Cursor's parameterized model picker by
sending `clientCapabilities._meta.parameterizedModelPicker = true` on
`initialize`. The agent then advertises a base-model selector plus independent
parameter selectors for the selected model, and the profile declares
parameterized configuration fidelity:

- the caller's model string is the whole configuration, spelled
  `<base-model>[<id>=<value>,...]` — for example
  `<base-model>[context=<value>,reasoning_effort=<value>,fast=<value>]`;
- ARS sets the base model, then requires the request to name **exactly** the
  parameters the agent advertises for that model, sets each on its own
  selector, and reads the whole configuration back before any prompt;
- the composed string is never sent to the agent, and ids and values come from
  what the running agent advertises — nothing is hard-coded;
- it reports the proven string as the effective model and `N/A` as the
  effective effort.

!!! warning "A caller targeting this agent must request effort `N/A`"

    Any other value fails before the prompt, as does a malformed model string,
    an unadvertised base model, parameter, or value, or an advertised parameter
    the request leaves out. And `effort_selector` is **refused on this profile**:
    an id hint for a selector no Run ever sets would be a fiction in every launch
    snapshot, so the pairing is refused rather than quietly ignored.

A reused Session is loaded under its unchanged external id and reconfigured on
every Run, so a later Run may request different parameters or a different base
model. A switch that fails part-way sends no prompt: an exact rollback to the
previous proven configuration keeps the Session reusable, and an unprovable one
quarantines it. Sessions created under revision 3 are refused for reuse by the
ordinary profile-identity check; continue that work in a new Session.

## Deviation 2 — a grant-driven permission mode

At revision 3 the profile added its second frozen term: the agent's ACP `mode` is
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

The mode, model, and parameter selectors are ACP-level observations of a
specific agent version. Re-run `agents doctor` and the canary after an upgrade;
if the upgrade changes which parameters a model advertises, requests must name
the new set before they pass. An upgrade
behind an unchanged registered command costs no ARS action and does not
invalidate Sessions.

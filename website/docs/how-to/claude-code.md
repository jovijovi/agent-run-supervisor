---
title: Claude Code
description: Registering Claude Code with ARS through an ACP adapter, on the compatibility profile, with an exact model pin.
---

# Claude Code

Claude Code is reached through the ACP adapter
[`@agentclientprotocol/claude-agent-acp`](https://github.com/agentclientprotocol/claude-agent-acp),
which you install and upgrade yourself. ARS launches the adapter command exactly
as you declare it.

```bash
export CLAUDE_ACP_VERSION='<exact-version>'
npm install -g "@agentclientprotocol/claude-agent-acp@$CLAUDE_ACP_VERSION"
claude-agent-acp --version
```

## The registry entry

```toml title="agents.toml"
[agents.claude-code]
profile = "claude-agent-acp-compat-v1"
command = "claude-agent-acp"
args    = []
```

`claude-agent-acp-compat-v1` is not a cosmetic label. It exists because this
adapter carries a **cited ACP-semantic deviation**: it resolves its initial
permission mode from ambient settings through its own settings manager and
auto-allows tool calls in process while that mode is permissive.

The profile answers that with two frozen terms, and neither half is sufficient
alone:

- the permission **mode is frozen as a configuration selector** and proven by
  exact readback before any prompt; and
- **frozen ACP session metadata** removes the ambient setting sources that would
  otherwise define the underlying SDK's permission rules and tool surface.

Using `standard-native-acp-v1` here would leave the adapter's ambient permission
resolution in place. Select the compatibility profile.

## Pinning an exact model

An operator who wants a *specific* model — not "whatever is current" — has to
know one thing about the naming first.

!!! warning "An alias is not a fixed model id"

    Claude Code exposes rolling aliases. An alias is the provider's currently
    recommended model in a lane, and the concrete generation behind it changes
    over time. It is **not** a permanent synonym for any one model id. Any
    mapping you observe today is a snapshot; that aliases are documented as
    changing is the only durable fact about them.

A caller that needs a fixed model requests the **full concrete model id**,
exactly as it expects to read it back. That is necessary but not sufficient: by
the live-domain rule, the value must also be one the running agent actually
advertises, and the adapter advertises what the effective Claude Code settings
allow.

Make the exact id available through `availableModels`:

```json
{
  "availableModels": ["<the-full-concrete-model-id>"]
}
```

!!! note "`availableModels` is not ARS configuration"

    It is Claude Code / adapter-owned configuration. It is not a registry field,
    not a registered model domain, and it never appears in the agents file. Place
    it in whichever Claude settings source is effective for the adapter's own
    working directory and config root — a deployment choice ARS neither makes nor
    records.

**ARS does not paper over the difference.** If the adapter advertises or reads
back an id that is not byte-for-byte the requested one, the Run fails
pre-dispatch with `CONFIG_FIDELITY`, before any prompt, rather than proceeding as
though the pin had been proven.

### Acceptance, minimally

1. The exact id appears in the discovered option set.
2. Set and readback are byte-for-byte identical to the requested id.
3. A real prompt on that Run succeeds.
4. A new process loads the same Session and continues at the same effective model
   and effort.

## Environment

`HOME` is projected unchanged, so Claude Code's own credential store, plugin
tree, cache, and user configuration work exactly as they do when you run it by
hand. ARS resolves, mints, refreshes, and stores no credential of its own.

Declare anything the base allowlist does not cover — most commonly a `PATH` that
reaches your Node installation and the adapter's own bin directory:

```toml
[agents.claude-code]
profile = "claude-agent-acp-compat-v1"
command = "claude-agent-acp"
args    = []
env_overlay = { PATH = "/usr/local/bin:/usr/bin:/bin" }
```

## Before first use

Run the full [registration sequence](register-an-agent.md), including the
mandatory **denied-action canary**. The compatibility profile constrains the
adapter's permission mode; it does not make mediation non-cooperative, and it is
not an OS sandbox.

## After an adapter or Claude Code upgrade

The adapter's settings-merge behaviour and the way it surfaces model option ids
are **observed properties of one adapter version**, not a contract ARS owns.
Revalidate after any adapter or Claude Code upgrade: a change to either can
change what ARS sees advertised, and therefore whether your exact pin still
resolves.

An upgrade behind an unchanged registered command costs no ARS action and does
not invalidate Sessions. Re-running the canary and the model-pin acceptance is a
judgement you make, not something ARS forces.

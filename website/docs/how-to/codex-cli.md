---
title: Codex CLI
description: Registering Codex CLI with ARS through an ACP adapter and its grant-driven compatibility profile.
---

# Codex CLI

A CLI that does not speak ACP natively is reached through an ACP adapter you
install independently. The adapter executable remains an operator deployment
fact, but its evidenced ACP permission-mode behavior belongs to the source-owned
`codex-agent-acp-compat-v1` profile.

## The registry entry

```toml title="agents.toml"
[agents.codex-cli]
profile   = "codex-agent-acp-compat-v1"
command   = "<the-acp-adapter-executable>"
args      = []
mediation = "ask-privileged-tool-families-v1"
```

`codex-agent-acp-compat-v1` keeps the standard ACP-v1 Session contract and
separate model and effort selectors. Its one deviation is a grant-driven
permission-mode selector proven exactly before any prompt.

## Grant-driven mode

Each Run derives the required `mode` from its own frozen `grant_capabilities`:

| Frozen grant | Required mode |
|---|---|
| any subset of `{read, search}`, including empty | `read-only` |
| every other valid grant | `agent` |

The adapter may advertise `agent-full-access`, but this policy never selects it.
ARS sets and exactly reads back the required mode before the model. It then
configures model and effort and re-proves the mode once at the post-effort
readback before Prompt. The mode is recomputed for every Run including real
`session/load` reuse. Missing or unadvertised mode state and inexact readback
fail pre-Prompt as `CONFIG_FIDELITY`.

An operator-owned `INITIAL_AGENT_MODE=read-only` may remain a safe bootstrap
default, but correctness does not depend on it. It is not an ARS registry or
request field; each Run still performs its grant-derived set and readback.

!!! note "Which command, and which arguments?"

    Both come from the adapter you installed, and change with its releases.
    Whatever the adapter's documentation says to run for a stdio ACP server is
    what goes in `command` and `args`. ARS passes them to `exec` unchanged: a
    bare name stays a bare name, an absolute path stays absolute, and every token
    — including an empty one — reaches the process as declared.

## Environment

Adapters commonly need more than the base allowlist provides. Declare it:

```toml
[agents.codex-cli]
profile   = "codex-agent-acp-compat-v1"
command   = "<the-acp-adapter-executable>"
args      = []
mediation = "ask-privileged-tool-families-v1"

env_passthrough = ["SOME_PROVIDER_TOKEN"]
env_overlay     = { PATH = "/usr/local/bin:/usr/bin:/bin" }
```

- `env_passthrough` names variables read from the **daemon's** environment. A
  name you declare that is absent there is reported in `declared_absent` rather
  than failing silently.
- `env_overlay` values are literals you author. They are transmitted to the child
  by your own declaration and are recorded by ARS only as a name and a source
  class.
- The agent authenticates through its own store under its own unchanged `HOME`.
  ARS does not stage a credential file or manage an agent home.

## Narrowing what the agent may ask for

`forbidden_capabilities` is applied as a **superset** of the profile's own floor,
so an entry can narrow a profile but never widen one:

```toml
forbidden_capabilities = ["terminal"]
```

A forbidden capability that the agent advertises anyway fails the Run
pre-dispatch with `CAPABILITY_FORBIDDEN`.

## Model and effort

The caller supplies `requested_model` and `requested_effort` on every Run, and
whatever the running agent advertises right now is the authority. If you know the
adapter's selector ids, you may record them as hints:

```toml
model_selector  = "<selector-id>"
effort_selector = "<selector-id>"
```

These are selector **ids**, never value domains. ARS registers no model list and
does not need one — exact literal readback before the prompt is the proof, so an
agent adding a model is a non-event.

## Before first use

Run the full [registration sequence](register-an-agent.md):
`agents validate` → `agents doctor --no-probe` → `agents doctor` → the mandatory
**denied-action canary** → real Runs.

The canary matters most on an adapter-backed agent, because the tool surface you
are mediating belongs to the underlying CLI rather than to the adapter you
registered. Prove that a denied action is actually denied for this exact
`command`, and prove it again after upgrading either half.

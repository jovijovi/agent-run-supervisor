---
title: Reasonix
description: Registering Reasonix with its ask-only compatibility profile, Homebrew bubblewrap on PATH, and a canonical workspace.
---

# Reasonix

Reasonix uses `reasonix-agent-acp-compat-v1` for one narrow ACP-semantic
deviation: its tool-approval selector can advertise modes that do not ask the
ACP Host. The profile freezes `tool_approval=ask`. The behavior below was
canaried against Reasonix 1.23.0 with Homebrew bubblewrap 0.11.2.

## The registry entry

```toml title="agents.toml"
[agents.reasonix]
profile = "reasonix-agent-acp-compat-v1"
command = "/home/<linuxbrew-user>/.linuxbrew/bin/reasonix"
args = ["acp"]
env_overlay = { PATH = "/home/<linuxbrew-user>/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin" }
```

The repository's `examples/agents.omp-reasonix.toml` contains the approved
concrete local command and PATH example. The Homebrew bin directory must be on
the child PATH so Reasonix can discover `bwrap`.

## Exact configuration order

Before every prompt, on both `session/new` and real `session/load`, ARS performs
and exact-reads-back this sequence:

1. `tool_approval=ask`
2. the caller-requested model
3. the caller-requested effort

Missing options, rejected values, and inexact readback fail before the prompt.
The profile never chooses `auto` or `yolo`, and it does not add a generalized
policy for Reasonix `work_mode`.

## Keep the Reasonix sandbox enabled

Do not set `sandbox.bash=off`. ARS does not edit Reasonix's global configuration,
and the registry above does not disable its sandbox.

The real A/B canary ran the same harmless `/bin/pwd -P` command with a temporary
Reasonix state/cache and an explicit Homebrew PATH:

| Workspace spelling | Result |
|---|---|
| canonical real directory | bubblewrap started and the command returned the canonical directory |
| symlink resolving to that directory | permission mediation completed, then bubblewrap failed while creating the bind-mount destination/workdir, before command execution |

ARS already resolves the workspace root and effective cwd through symlinks at
the shared admission boundary. Those canonical literals are sealed into the
RunSpec and sent to both `session/new` and `session/load`, so the failing symlink
spelling never reaches Reasonix. This behavior is shared by every agent; it does
not depend on Reasonix internals.

## State and ownership

Reasonix owns its credentials, configuration, cache, and Session store. ARS
does not create or repoint `REASONIX_STATE_HOME` or `REASONIX_CACHE_HOME`, edit
`config.toml`, install bubblewrap, or manage any of that state. The temporary
overrides used by the canary were test isolation only, not registry policy.

## Before first use

Run the full [registration workflow](register-an-agent.md), including a denied
write or execute canary while the sandbox remains enabled:

```bash
agent-run-supervisor agents validate --agents-file agents.toml
agent-run-supervisor agents doctor --agents-file agents.toml --agent reasonix --no-probe
agent-run-supervisor agents doctor --agents-file agents.toml --agent reasonix
```

Confirm the projected PATH contains the Homebrew bin directory and that the Run
records the `tool_approval=ask` snapshot before model and effort.

## After a Reasonix or bubblewrap upgrade

Repeat the canonical/symlink A/B and denied-action canary. An executable upgrade
behind the same registry entry does not make ARS rewrite Reasonix state or
silently cut Session continuity.

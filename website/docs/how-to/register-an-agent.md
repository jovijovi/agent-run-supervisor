---
title: Register an agent
description: The end-to-end workflow for putting any local ACP-capable agent under ARS supervision.
---

# Register an agent

This is the workflow every agent guide assumes. It applies unchanged to an agent
that speaks ACP natively and to one reached through an ACP adapter — the adapter
is a deployment fact, not a different category.

## 1. Write the entry

The registry is one operator-owned TOML file, read exactly once at daemon
startup into an immutable in-memory snapshot. Replace it atomically with `mv`; a
replacement takes effect at the next daemon start.

```toml title="agents.toml"
schema_version = 1

[agents.my-agent]                                  # table key = the agent_id a caller names
profile   = "standard-native-acp-v1"               # required
command   = "<the-agent-executable>"               # required: bare name or absolute path
args      = ["<acp-subcommand-or-flags>"]
mediation = "ask-privileged-tool-families-v1"      # selects a source-owned binding

env_passthrough = ["SSH_AUTH_SOCK", "SOME_PROVIDER_TOKEN"]
env_overlay     = { PATH = "/usr/local/bin:/usr/bin:/bin" }
forbidden_capabilities = ["terminal"]
```

Required: `profile`, `command`. Optional: `args`, `mediation`,
`env_passthrough`, `env_overlay`, `model_selector`, `effort_selector`,
`forbidden_capabilities`, `session_epoch`. **Nothing else** — an unknown key at
any level is refused, and the whole file is refused rather than partially
honored.

Full grammar and bounds: [agents file reference](../reference/agents-file.md).

## 2. Give the agent the environment it needs

A user-level daemon inherits a minimal environment. The base allowlist covers
the interactive essentials (`HOME`, `PATH`, `USER`, `LANG`, `TMPDIR`, the `XDG_*`
directories, proxy and certificate variables), and everything else is yours to
declare.

The two that cause the most trouble:

- **`PATH`.** This is the single most likely cause of "works in my shell, fails
  under ARS", because a user daemon typically omits `~/.local/bin` and
  version-manager shim directories. Either author an `env_overlay.PATH`, or use
  an absolute `command`.
- **`SSH_AUTH_SOCK`.** Deliberately not in the base set. Forwarding it hands the
  agent live use of your SSH keys — a real authority transfer, so it is an
  explicit `env_passthrough` opt-in.

## 3. Validate offline

```bash
agent-run-supervisor agents validate --agents-file agents.toml
```

This parses the file, checks shape and bounds, and applies the **identical**
mediation-collision check the daemon applies at startup. It has no side effects
and prints only entry ids, counts, environment **names**, source classes, and
rule outcomes — never a normalized overlay or a mediation value.

## 4. Report the projection

```bash
agent-run-supervisor agents doctor --agents-file agents.toml --agent my-agent --no-probe
```

`--no-probe` reports the projected environment **name** set and the declared
launch, and starts nothing. This is where a missing `PATH` entry becomes visible
rather than mysterious.

## 5. Probe the real agent

```bash
agent-run-supervisor agents doctor --agents-file agents.toml --agent my-agent
```

Without `--no-probe`, doctor starts the registered command for a zero-prompt ACP
`initialize`. Read-only refers to ARS and operator state: the child writes its
own agent-owned state. It is reaped on every path — close, `SIGTERM` to the
group, a bounded wait, then `SIGKILL` and a final bounded wait — and a group that
survives all of that is reported as a **failed probe** rather than left running.

Expect to see the protocol version, the advertised capabilities, and the agent's
self-report. All of it is recorded as non-authoritative evidence.

## 6. Run the denied-action canary

!!! danger "Mandatory, per agent, before first real use"

    Mediation is cooperative. An agent that ignores the knob can execute
    in-process tools with no ACP permission event, and zero permission events
    prove nothing about denial.

    Submit a Run whose frozen grant forbids an action you can observe, prompt the
    agent to perform exactly that action, and confirm the denial appears in the
    Run's event stream. Repeat it for every agent you register, and again after
    an agent upgrade that could change how its tools are routed.

## 7. Restart to pick up the change

The registry is read once at startup, so a registry edit takes effect at the
**next daemon start**, not the next Run. That is the price; the payoff is that
two concurrent Runs can never resolve different registry contents and a serving
daemon cannot be re-pointed.

| Change | Cost |
|---|---|
| agent upgrade behind an unchanged registered command | **nothing.** No restart, no ARS action; existing Sessions still resume |
| identity-preserving registry edit (`command`, `args`, env, `mediation`, selector hints, `forbidden_capabilities`) | one daemon restart, draining in-flight Runs first. **No Session invalidation** |
| identity-changing edit (adding or changing `session_epoch`, a different `agent_id`, a different `profile`) | the same restart, **plus the continuity cut you asked for** |
| an ARS release that does not change ACP semantics | no Session invalidation, by construction |

!!! warning "Adding `session_epoch` for the first time cuts continuity"

    Identity comparison is symmetric equality, and absent ≠ 1. A record at epoch
    1 is refused by a Run at epoch 2 *and* by a Run with no epoch. If you do not
    want the cut, do not add the field.

## Common refusals

| Code | What happened |
|---|---|
| `REGISTRY_UNSAFE_MODE` | the agents file is group- or world-writable |
| `REGISTRY_UNKNOWN_KEY` | a key outside the closed field set |
| `ENTRY_UNKNOWN_PROFILE` | `profile` does not name a registered profile |
| `ENTRY_UNKNOWN_MEDIATION_ID` | `mediation` does not name a registered binding |
| `MEDIATION_KEY_COLLISION` | your `env_overlay` or `env_passthrough` names a reserved mediation key |
| `LAUNCH_PERMISSION_KEY_COLLISION` | your entry declares the key a selected launch-permission policy owns |
| `COMMAND_NOT_FOUND` | `exec` returned `ENOENT` — usually `PATH`, or a moved shim |
| `COMMAND_NOT_EXECUTABLE` | `exec` returned `EACCES` |

Registry-level codes refuse the daemon at startup. Per-Run codes fail one Run
before dispatch. Full list: [Error codes](../reference/error-codes.md).

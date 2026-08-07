---
title: Agents file
description: The operator registry contract — grammar, complete field set, bounds, and refusal rules.
---

# Agents file

One operator-owned TOML file, supplied by a required `--agents-file` daemon
flag, read **exactly once** at daemon startup into an immutable in-memory
snapshot. Replace it atomically with `mv`; a replacement takes effect at the next
daemon start.

## Config hygiene — not attestation

ARS resolves the registry path, follows symlinks, and requires the resolved
target to be a **regular file that is not group- or world-writable**. A dotfiles
symlink works, including below `$HOME`; a file anyone can edit does not.

This is ARS declining to take orders from a world-writable file — the same
standard as an SSH config — and it is bounded to *its own configuration file*.
It says nothing about `command`.

!!! danger "No check on `command`"

    ARS performs **no ownership, mode, ancestor, symlink, or digest check** on
    `command`, on its ancestors, or on anything the agent subsequently loads.
    Registry trust is transitive to you: a wrong `command` launches the wrong
    thing.

## File shape

```toml
schema_version = 1

# A standards-conforming native ACP agent — the common case.
[agents.native-agent]
profile   = "standard-native-acp-v1"
command   = "some-agent"                          # PATH-resolved bare name, exactly as typed
args      = ["acp"]
mediation = "ask-privileged-tool-families-v1"     # selects a source-owned binding

# An agent reached through an independently installed ACP adapter.
# Same profile; the adapter is a deployment fact.
[agents.adapter-backed-agent]
profile = "standard-native-acp-v1"
command = "/home/<service-user>/.local/bin/<some-acp-adapter>"
args    = []
env_passthrough = ["SSH_AUTH_SOCK", "SOME_AGENT_CONFIG", "SOME_PROVIDER_TOKEN"]
env_overlay     = { SOME_AGENT_HOME = "/home/<service-user>/.some-agent", NO_BROWSER = "1" }
effort_selector = "reasoning_effort"              # selector *id*, not a value domain
forbidden_capabilities = ["terminal"]
session_epoch   = 1                               # operator-controlled continuity cut
```

`agent_id` is the table key. It is what a caller names in a request, and it is
the only registry-facing value that ever crosses the wire.

## Complete field set

Required: `profile`, `command`. Optional: `args`, `mediation`,
`env_passthrough`, `env_overlay`, `model_selector`, `effort_selector`,
`forbidden_capabilities`, `session_epoch`. **Nothing else.** An unknown key at
any level is refused.

### Deliberately absent, each for a stated reason

| Absent field | Reason |
|---|---|
| `transport` | v1 is stdio by definition. A one-valued key is remote-transport scaffolding |
| any secret slot or `secret_refs` | ARS resolves no credentials. A slot would be a placeholder for an undesigned capability |
| `version_probe` | a probe is an operator diagnostic, not a per-Run gate |
| registered model or effort sets, selector value domains | live discovery is the domain authority and exact readback is the proof |
| `default_model`, `default_effort` | the caller supplies model and effort on every Run |
| expected agent name or version | a self-report is evidence, never identity |
| any digest, tree hash, ownership, or mode expectation | ARS makes no integrity or supply-chain claim |

!!! note "There is no secret-shaped-name heuristic"

    Refusing keys that look like `*TOKEN*` or `*SECRET*` is unsound in both
    directions — `AUTH="…"` evades it while `SOME_AGENT_TOKEN_PATH=/path/to/dir`
    trips it falsely — and a name-shape test can never be a confidentiality
    boundary. The replacement is the universal rule: every environment value is
    treated as potentially sensitive and never serialized out of the resolved
    carrier.

## Grammar and bounds

Strict parse, all fail-closed, each refusal naming a stable rule.

| Field | Rule |
|---|---|
| file | ≤ 1 MiB; strict TOML; `schema_version` exactly the supported value |
| `agent_id` (table key) | matches `[a-z0-9][a-z0-9._-]{0,63}` |
| `profile` | names a source-registered profile |
| `command` | non-empty, ≤ 4096 bytes, no NUL; either absolute, or a single basename with **no path separator** |
| `args` | ≤ 32 tokens, each ≤ 1024 bytes, no NUL; passed as an argv list, never through a shell, so an empty token `""` is valid and passes through unchanged |
| `mediation` | names a source-registered mediation id |
| `env_passthrough` | ≤ 32 names, each matching `[A-Za-z_][A-Za-z0-9_]*` |
| `env_overlay` | ≤ 32 pairs; values ≤ 4096 printable bytes |
| `model_selector`, `effort_selector` | a selector **id** hint; never a value domain |
| `forbidden_capabilities` | ≤ 16 bounded names; applied as a superset of the profile's own floor |
| `session_epoch` | a positive integer |

## Refusal rules

### Registry-level — the daemon refuses to listen

| Class | Codes |
|---|---|
| unavailable / unreadable / unsafe mode | `REGISTRY_ABSENT`, `REGISTRY_UNREADABLE`, `REGISTRY_UNSAFE_MODE`, `REGISTRY_NOT_REGULAR_FILE` |
| malformed | `REGISTRY_PARSE`, `REGISTRY_UNKNOWN_KEY`, `REGISTRY_SCHEMA_VERSION`, `REGISTRY_TOO_LARGE` |
| entry defects | `AGENT_ID_INVALID`, `ENTRY_FIELD_MISSING`, `ENTRY_UNKNOWN_PROFILE`, `ENTRY_COMMAND_INVALID`, `ENTRY_ARG_TOKEN_INVALID`, `ENTRY_ENV_KEY_INVALID`, `ENTRY_ENV_VALUE_INVALID`, `ENTRY_SELECTOR_INVALID`, `ENTRY_CAPABILITY_INVALID`, `ENTRY_UNKNOWN_MEDIATION_ID`, `ENTRY_SESSION_EPOCH_INVALID` |
| mediation authority | `MEDIATION_KEY_COLLISION` |
| launch-permission authority | `LAUNCH_PERMISSION_KEY_COLLISION` |

The **whole file** is refused. It is never partially honored, never cached from a
previous start, and never repaired. Refusals name the failing rule and, where
operator-facing, a field path or an environment **name** — never an overlay value
or a raw file fragment.

`AGENT_ID_INVALID` is deliberately the same rule at both layers: the table key is
judged by exactly the grammar that judges a caller's `agent_id` at admission, so
the two can never drift.

### Per-Run — pre-dispatch `failed`

`AGENT_ID_INVALID` and `AGENT_NOT_REGISTERED` resolve against the startup
snapshot, in memory, with zero filesystem access. Spawn classifications are
`COMMAND_NOT_FOUND`, `COMMAND_NOT_EXECUTABLE`, and `SPAWN_FAILED`. Contract
checks are `PROTOCOL_MISMATCH`, `CAPABILITY_MISSING`, and
`CAPABILITY_FORBIDDEN`.

## Mediation authority is closed

- The binding is **source-owned in key and value**, keyed by the capability
  family it mediates. Your entry may *select* one id, or none. It can never
  author a pair, a key, or a value, and there is no `mediation = off`.
- **Reserved keys are global** — the union of every key in *any* registered
  binding, not only the one you selected.
- **A collision refuses startup** with `MEDIATION_KEY_COLLISION`, at parse time,
  so it refuses the daemon rather than a Run. `agents validate` applies the
  identical check offline.
- **Mediation is applied last** among the layers you can influence.

If the profile your entry names selects a launch-permission policy, declaring
that policy's key in `env_passthrough` or `env_overlay` fails the file with
`LAUNCH_PERMISSION_KEY_COLLISION` — layer 5 would otherwise overwrite your
declaration silently. A profile that selects no policy projects no layer 5, so
the key is not reserved for it.

## `session_epoch` — the continuity rule

An operator escape hatch for the case where **you know** a change broke
continuity and want every Session under that agent to stop reusing.

- **No automatic bump exists anywhere.** An agent or adapter version change, an
  ARS upgrade, a profile revision that does not change ACP semantics, a
  `command`/`args`/env/mediation/selector edit, a registry file replacement, and
  a daemon restart never change it.
- **Only your edit changes it.** Identity comparison is **symmetric equality**,
  so a record at epoch 1 is refused by a Run at epoch 2 *and* by a Run with no
  epoch.
- **Adding it for the first time cuts continuity**, because absent ≠ 1. If you do
  not want the cut, do not add the field.

## Operator commands

```bash
agent-run-supervisor agents validate --agents-file <path>
agent-run-supervisor agents doctor   --agents-file <path> [--agent <agent-id>] [--no-probe]
agent-run-supervisor run inspect     --run-dir <run-dir>
```

| Command | What it does | Side effects |
|---|---|---|
| `agents validate` | parses the file, checks shape and bounds, applies the identical mediation-collision check the daemon applies at startup | none. Prints only entry ids, counts, environment **names**, source classes, and rule outcomes |
| `agents doctor` | the projected environment **name** report and the declared launch per agent, plus a zero-prompt ACP `initialize`. `--no-probe` reports the projection only | without `--no-probe` it **starts an external child**, which writes its own agent-owned state. The child is reaped on every path; a group surviving `SIGTERM` and `SIGKILL` is reported as a failed probe rather than left running |
| `run inspect` | per-Run evidence, with the value-blind launch hash recomputed for a current-schema record | none. A pre-reset record is classified first and its value-bearing material withheld categorically |

There is no `promote`, `rollback`, or `--force`, and no command that installs an
artifact, edits a service unit, restarts the daemon, escalates privilege, or
contacts a provider.

---
title: Agents
description: What an ARS agent is — a registered command, named by an agent_id, launched exactly as declared.
---

# Agents

An **agent**, to ARS, is one entry in the operator's registry: a command, its
argv, and the declarations needed to launch it. The table key is the `agent_id`,
and it is the only registry-facing value that ever crosses the wire.

```toml
[agents.my-agent]                              # agent_id = "my-agent"
profile   = "standard-native-acp-v1"
command   = "my-agent-cli"
args      = ["acp"]
mediation = "ask-privileged-tool-families-v1"
```

A caller names `my-agent` in a Run request. It cannot name a command, a path, an
argument, or an environment value — those fields do not exist on the wire.

## Your command is launched exactly as declared

- `argv[0]` is the declared `command` string, **byte-for-byte**. A bare name
  stays a bare name, exactly as a shell would pass it.
- Every declared token reaches `exec` unchanged, including an empty one.
  `args = ["--label", "", "--end"]` is three tokens, not two. Arguments are
  passed as an argv list and never through a shell.
- The exec image is located by ordinary `execvp`-style lookup over the
  **child's** projected `PATH` for a bare name, and by the declared absolute path
  otherwise. There is no `executable=` override and no realpath.

Because the command is opaque to ARS, version-manager shims, symlink farms,
multicall `argv[0]` dispatch, package-relative resolution, and an agent's own
self-update logic all keep working.

!!! note "There is no pre-flight resolution check"

    ARS classifies the `exec` failure itself: `ENOENT → COMMAND_NOT_FOUND`,
    `EACCES → COMMAND_NOT_EXECUTABLE`, anything else → `SPAWN_FAILED`. Read those
    as ordinary configuration errors — "you upgraded and the shim moved" — never
    as a security refusal. No process exists in those cases.

## An agent whose CLI is not natively ACP

There is no separate executable category for it. Point `command` at the ACP
adapter you installed; that executable remains an operator deployment fact.
Select `standard-native-acp-v1` only when the ACP behavior it exposes conforms
to that contract. An evidenced ACP-semantic deviation selects its source-owned
compatibility profile — for Codex's grant-driven `mode`, that is
`codex-agent-acp-compat-v1`.

```toml
[agents.adapter-backed-agent]
profile = "standard-native-acp-v1"
command = "/home/<service-user>/.local/bin/<some-acp-adapter>"
args    = []
```

## The environment an agent receives

A filtered environment is not your interactive environment. It silently omits
proxy, certificate, agent-socket, temp-directory, and provider variables, and the
resulting failures look like agent bugs — so ARS gives you explicit layers.

| Layer | Source | You control |
|---|---|---|
| 1 — base allowlist | names from the daemon's own environment, only when present, values unchanged | no (source-owned, per profile) |
| 2 — pass-through | additional names read from the daemon's environment | yes, `env_passthrough` |
| 3 — overlay | literal values you author | yes, `env_overlay` |
| 4 — mediation | source-owned pairs | selection only, `mediation` |
| 5 — launch permission | one source-owned pair, applied last, present only when the profile selects a policy | no (source-owned, per profile) |

The base set covers the ordinary interactive essentials: `HOME`, `PATH`, `USER`,
`LOGNAME`, `SHELL`, `LANG`, `LC_ALL`, `TZ`, `TERM`, `TMPDIR`, the `XDG_*`
directories, the lower- and upper-case proxy variables, and the common
certificate-bundle variables.

- **`HOME` is unchanged**, which is what makes the agent's own credential store,
  plugin tree, cache, session store, and user config work exactly as they do when
  you run the agent by hand. Necessary, and not sufficient.
- **`SSH_AUTH_SOCK` is deliberately not in the base set.** Forwarding it hands
  the agent live use of your SSH keys. That is a real authority transfer and must
  be an explicit per-agent `env_passthrough` opt-in.

## What ARS records about the environment, and what it never records

Durable environment evidence is **value-blind**: per name, the name, its source
class, its precedence layer, and its redaction status, plus a resolved count, the
mediation id, and the names you declared that were absent.

```json
"env": {
  "values_persisted": false,
  "redaction": "all-values-withheld",
  "resolved_count": 27,
  "mediation_id": "ask-privileged-tool-families-v1",
  "names": [
    {"name": "PATH",                "source": "base",        "precedence": 1, "redacted": true},
    {"name": "SOME_PROVIDER_TOKEN", "source": "passthrough", "precedence": 2, "redacted": true},
    {"name": "SOME_AGENT_HOME",     "source": "overlay",     "precedence": 3, "redacted": true}
  ],
  "declared_absent": ["SOME_AGENT_CONFIG"]
}
```

No value, value digest, keyed digest, length, prefix, or suffix is ever a field
or a hash input.

!!! warning "The limit worth knowing before you project a secret"

    ARS keeps values out of its own sealed material, but it **does not scan
    free-form Run text for the values it projected**. Static credential shapes —
    API key, `Authorization: Bearer`, JWT, PEM — are still redacted, but an
    arbitrary value that an agent echoes into a final message, an event field, or
    its self-reported name may be retained in that Run's evidence. The remedy is
    deciding what you project to which agent.

## Observations are evidence, never gates

After a successful spawn, ARS records the declared command and exact argv, the
first `PATH` hit for a bare command, the image the kernel actually mapped, the
agent's self-reported name and version, the protocol version, and the advertised
capabilities. **Every one is marked non-authoritative.** No code path compares
them against a source constant, a prior Run, or a registry value to decide
admission or reuse.

Drift between two Runs of one Session is recorded and may be emitted as a
`policy_warning` event — never a refusal. A self-report is not an identity in
either direction: a substituted agent can report any name it likes.

## Operator commands

Three, all read-only with respect to ARS state:

```bash
agent-run-supervisor agents validate --agents-file <path>
agent-run-supervisor agents doctor   --agents-file <path> [--agent <agent-id>] [--no-probe]
agent-run-supervisor run inspect     --run-dir <run-dir>
```

There is no `promote`, `rollback`, or `--force`, and no command that installs an
artifact, edits a service unit, restarts the daemon, or contacts a provider.

Full grammar, bounds, and refusal codes: [agents file
reference](../reference/agents-file.md).

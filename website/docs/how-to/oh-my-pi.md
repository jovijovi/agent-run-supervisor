---
title: oh-my-pi
description: Registering oh-my-pi with ARS using its always-ask ACP mode and thinking selector, with the observed mutation limitations stated explicitly.
---

# oh-my-pi

oh-my-pi (OMP) speaks ACP directly and uses `standard-native-acp-v1`. The
operator entry supplies its one selector deviation: OMP advertises `thinking`
as the effort selector id. The behavior below was canaried against OMP 17.2.12.

## The registry entry

```toml title="agents.toml"
[agents.oh-my-pi]
profile = "standard-native-acp-v1"
command = "/home/<service-user>/.local/bin/omp"
args = ["--approval-mode=always-ask", "acp"]
effort_selector = "thinking"
```

The repository's `examples/agents.omp-reasonix.toml` contains the approved
concrete local command example. The executable remains operator-installed and
operator-owned; ARS never copies, upgrades, or configures it.

Model selection still uses the standard ACP `model` selector. ARS sets the
requested model and `thinking` value, then requires exact readback before the
prompt.

!!! warning "Do not use OMP plan mode as a permission boundary"

    OMP's `mode=plan` is a workflow mode, not an ARS security boundary. ARS does
    not select it automatically for a read-only grant. Do not replace
    `--approval-mode=always-ask` with a permissive mode to work around a denial.

## What the real permission canary proved

With an isolated Session directory and `--approval-mode=always-ask acp`:

- an ordinary create/write and an ordinary edit appeared as ACP tool updates of
  kind `edit`, but OMP emitted no `session/request_permission`; OMP then denied
  each operation locally, and the files were absent or byte-unchanged;
- a deliberately denied write-like request produced no side effect;
- dedicated delete and rename prompts could not be induced to produce distinct
  `delete` or `move` permission requests; shell `rm` and `mv` attempts appeared
  as `execute` instead;
- a harmless Bash request emitted a structured `execute` permission request
  with command metadata and once/always/reject options. ARS selected only
  `allow_once`, never `allow_always`; OMP then denied the command at its own
  second approval step, before execution.

That evidence supports the registry and read/search path, but it does **not**
support a mutation-usable claim for OMP 17.2.12 under ARS. ARS therefore does
not widen edit, delete, or move mediation: those kinds remain denied even when
the frozen grant names the corresponding capability. Execute mediation still
requires an `execute` grant and can select only a once-scoped option, but this
OMP version's additional local approval still blocks the command.

## Before first use

Run the ordinary [registration workflow](register-an-agent.md):

```bash
agent-run-supervisor agents validate --agents-file agents.toml
agent-run-supervisor agents doctor --agents-file agents.toml --agent oh-my-pi --no-probe
agent-run-supervisor agents doctor --agents-file agents.toml --agent oh-my-pi
```

For OMP 17.2.12, treat read/search as the bounded usable surface and retain a
known-empty canary workspace. Do not promote write, edit, delete, move, or Bash
workflows until a later binary demonstrates a complete structured permission
round trip and the ARS integration is reviewed again.

## After an OMP upgrade

Re-run the isolated permission canary. In particular, check whether ordinary
edits now emit `session/request_permission`, whether delete and move have
distinct kinds, and whether a host-selected `allow_once` completes without an
additional unserviceable approval. An upgrade alone changes no ARS profile or
existing Session identity.

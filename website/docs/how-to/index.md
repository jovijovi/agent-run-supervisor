---
title: How-to guides
description: Registering specific local coding agents with ARS, and the checks that must pass before each one is used.
---

# How-to guides

Task-shaped guides for putting a specific local agent under supervision.

Start with [Register an agent](register-an-agent.md) — it covers the workflow
every guide below assumes. Then pick the guide for the agent you installed.

<div class="ars-panels">

<a class="ars-panel" href="claude-code.md">
  <p class="ars-panel__name">Claude Code</p>
  <p class="ars-panel__note">Reached through an ACP adapter, on the compatibility profile, with exact model pinning.</p>
</a>

<a class="ars-panel" href="codex-cli.md">
  <p class="ars-panel__name">Codex CLI</p>
  <p class="ars-panel__note">Operator-installed ACP adapter executable; evidenced ACP mode semantics use codex-agent-acp-compat-v1.</p>
</a>

<a class="ars-panel" href="opencode.md">
  <p class="ars-panel__name">OpenCode</p>
  <p class="ars-panel__note">The agent the registered mediation binding was written for.</p>
</a>

<a class="ars-panel" href="cursor-cli.md">
  <p class="ars-panel__name">Cursor CLI</p>
  <p class="ars-panel__note">Model-only configuration fidelity and a grant-driven permission mode.</p>
</a>

</div>

## What these guides are, and are not

These four guides are an initial set, not a protocol boundary. ARS has no
built-in list of supported agents and no code path that branches on an agent's
name. What decides whether an agent can run is the operator's registry entry plus
the ACP compatibility profile it selects — so an agent with no guide here is not
unsupported, it simply has no guide here yet.

ARS supervises the command an operator registered; it does not install, package,
or endorse any agent. Every guide assumes you already installed the agent
yourself, that it authenticates through its own store under its own `HOME`, and
that you will keep upgrading it independently of ARS.

!!! note "Commands and arguments are deployment facts"

    Each guide uses a placeholder for the agent's own executable and its ACP
    arguments, because those belong to the agent's documentation and change with
    its releases — not to ARS. Fill them in from the agent you installed. What
    the guides do pin exactly is the ARS side: which profile to select, which
    mediation id, what to narrow, and what to prove before use.

## The order that matters

Every guide follows the same sequence, and the order is not decorative:

1. Install the agent, and confirm it works when you run it by hand.
2. Write or extend the agents file with one entry.
3. `agents validate` — offline, no side effects.
4. `agents doctor --no-probe` — the projected environment name report.
5. `agents doctor` — a real zero-prompt ACP handshake against the agent.
6. **The denied-action canary** — prove mediation actually denies something for
   *this* agent.
7. Only then, submit real Runs.

Step 6 is mandatory and cannot be skipped by reasoning about steps 1–5. See
[Profiles and binding](../concepts/profiles-and-binding.md#permission-mediation-exactly).

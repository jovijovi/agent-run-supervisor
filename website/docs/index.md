---
template: home.html
title: Agent Run Supervisor
description: >-
  Supervise local AGENT runs. Preserve context. Recover with evidence.
---

<div class="ars-home" markdown>

<div class="ars-hero" markdown>

<p class="ars-eyebrow">Local-first AGENT supervision</p>

# Supervise local AGENT runs. Preserve context. Recover with evidence. { .ars-hero__headline }

<p class="ars-hero__lede">
Agent Run Supervisor (ARS) is the layer between your application and an external
coding AGENT. Your application submits a Run; ARS starts exactly one supervised
process from the command an operator registered, mediates every permission
request against a frozen grant, normalizes the agent's output into ordered
events, and writes a redacted local record of how the Run actually ended.
</p>

<div class="ars-cta">
  <a class="ars-btn ars-btn--primary" href="quickstart/">Quickstart — 5 minutes</a>
  <a class="ars-btn ars-btn--ghost" href="concepts/runs-and-sessions/">Runs &amp; Sessions</a>
</div>

</div>

## The model, end to end

<div class="ars-trajectory" markdown>

<ol class="ars-trajectory__track">
  <li class="ars-stage ars-stage--signal">
    <p class="ars-stage__name">Agent</p>
    <p class="ars-stage__note">A command an operator registered in the agents file. ARS launches it; it does not install it.</p>
  </li>
  <li class="ars-stage">
    <p class="ars-stage__name">Session</p>
    <p class="ars-stage__note">Durable conversation continuity. Created by the first Run, resumable indefinitely afterwards.</p>
  </li>
  <li class="ars-stage">
    <p class="ars-stage__name">Run</p>
    <p class="ars-stage__note">One supervised process, one immutable spec, one frozen grant. Runs terminate.</p>
  </li>
  <li class="ars-stage">
    <p class="ars-stage__name">Events</p>
    <p class="ars-stage__note">A normalized, <code>seq</code>-ordered stream: agent output, tool calls, permission decisions.</p>
  </li>
  <li class="ars-stage ars-stage--marker">
    <p class="ars-stage__name">Result</p>
    <p class="ars-stage__note">One supervisor-owned terminal status with a stable error code, plus redacted artifacts.</p>
  </li>
</ol>

<p class="ars-trajectory__caption">
Agent → Session → Run → Events → Result. This is a static illustration of the
product model, not a view of any running system: the site shows no Run data and
ARS has no web console.
</p>

</div>

## Start here

<div class="ars-panels">

<a class="ars-panel" href="quickstart/">
  <p class="ars-panel__name">Quickstart</p>
  <p class="ars-panel__note">Install, register one agent, validate it, start the daemon, and submit your first Run.</p>
</a>

<a class="ars-panel" href="overview/">
  <p class="ars-panel__name">What ARS is</p>
  <p class="ars-panel__note">The boundary in one page: what ARS owns, what it deliberately does not, and what it never claims.</p>
</a>

<a class="ars-panel" href="concepts/runs-and-sessions/">
  <p class="ars-panel__name">Runs &amp; Sessions</p>
  <p class="ars-panel__note">Runs terminate. Sessions do not. The one distinction that changes how you write a caller.</p>
</a>

<a class="ars-panel" href="how-to/">
  <p class="ars-panel__name">Agent guides</p>
  <p class="ars-panel__note">Registering Claude Code, Codex CLI, OpenCode, and Cursor CLI as supervised agents.</p>
</a>

<a class="ars-panel" href="reference/socket-api/">
  <p class="ars-panel__name">Socket API</p>
  <p class="ars-panel__note">The <code>arsd</code> API v3 wire contract: operations, request fields, events, results, error codes.</p>
</a>

<a class="ars-panel" href="deployment/">
  <p class="ars-panel__name">Deployment</p>
  <p class="ars-panel__note">Running <code>arsd</code> as an unprivileged local daemon, and under a user-level service manager.</p>
</a>

</div>

## What you get

| ARS owns | ARS deliberately does not own |
|---|---|
| the process it starts: PID/PGID, timeouts, signals, reap | the software it starts — you install and upgrade agents yourself |
| the ACP conversation: capabilities, exact model and effort, continuity | the agent's own conversation and context state |
| permission mediation against a caller-frozen grant | the business verdict, which stays with your application |
| redacted per-Run evidence under one supervisor root | agent `$HOME`, auth stores, plugins, caches, configuration |
| caller authentication over a local socket | credentials — ARS resolves, mints, refreshes, and stores none |

Every hop runs on one machine, under one unprivileged user:

```text
trusted local caller  →  arsd (local UDS)  →  ars-core / Native ACP  →  external AGENT process
```

!!! contract "The two facts that surprise people"

    **Runs terminate; Sessions do not.** A Session has no open, active, or closed
    state and no close operation. See [Runs and Sessions](concepts/runs-and-sessions.md).

    **Permission mediation is cooperative policy enforcement, not an OS sandbox.**
    The agent runs as the daemon's user with that user's full authority. See
    [Profiles and binding](concepts/profiles-and-binding.md).

</div>

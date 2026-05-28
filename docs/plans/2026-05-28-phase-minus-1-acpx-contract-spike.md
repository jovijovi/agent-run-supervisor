---
title: "Phase -1 acpx Contract Spike Plan"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-28T20:00:00+0800
---
# Phase -1 acpx@0.10.0 Contract Spike Plan

> Scope: evidence-only contract spike for `agent-run-supervisor`. This is not the full V0.1a implementation.

## Goal

Capture and validate the real `acpx@0.10.0` contract that V0.1a would depend on: command grammar, version/provenance, JSON stdout schema, permission-policy behavior, exit-code classification, management-command schema separation, and safe fixture storage.

## Explicit non-approvals

This phase does not approve or implement:

- persistent sessions;
- full `agent-run-supervisor` V0.1a library code;
- Sachima behavior integration;
- real AGENT automatic replies;
- public ingress or real delivery;
- Gateway restart/reload/replace;
- production config writes;
- live/default-on behavior.

## Required runner flags for exec fixtures

Every exec fixture must be captured with the same runner flag family V0.1a expects:

```text
--format json --json-strict --suppress-reads --timeout <timeout_seconds> --max-turns <max_turns>
```

plus the relevant permission flags/policy and `exec` command.

## Fixture target layout

```text
fixtures/acpx-0.10.0/
  README.md
  manifest.json
  success-codex-sentinel/
  usage-error-invalid-flag/
  timeout-hanging-agent/
  permission-denied-codex-read/
  management-no-session-exit4/
  management-status-no-session-exit0/
```

Each fixture directory should contain:

```text
command.argv.json
metadata.json
stdout.ndjson or stdout.json
stderr.log
result.json
```

## Acceptance criteria

- `acpx@0.10.0` exact version and Node version are recorded.
- Exec success fixture proves the observed stdout schema.
- Failure fixtures cover exit `1/2/3/4/5/130` where practical; unreachable cases are explicitly marked with reason.
- Management JSON fixtures are stored separately from exec stdout fixtures.
- `AcpxStdoutSchema` is documented in `fixtures/acpx-0.10.0/README.md`.
- Secret scan over fixtures finds no credential-shaped values.
- Validation script passes.

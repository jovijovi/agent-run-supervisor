---
name: ars-batch-agent-acceptance
description: Use when directly checking a deployed ARS with fixed response-only delivery and real Session create-to-load quick-health controllers.
---

# ARS quick-health acceptance

Use this skill for a bounded health check of an already deployed ARS. It does not install or manage external AGENTs, edit a registry, configure or restart a service, deploy, retry a Run, or turn technical completion into business success.

## Official direct entry points

| Script | Direct contract |
|---|---|
| `scripts/run_response_only.py` | Three fresh Runs per route using fixed bubble-sort prompts; each Run must complete the task-delivery chain and return a non-empty string |
| `scripts/run_session_reuse.py` | Two sequential Runs per route; S1 creates a Session and returns a non-empty string, then S2 loads that exact Session and recalls a fresh token |

Both scripts use the public `ArsdClient` over the configured Unix socket. They read the served API version, required operations, concurrency, event-page limit, prompt limit, and event-budget ceiling from live `server_info`. They never hard-code a package/API version, daemon PID, caller identity, registry path, route, provider, or deployment label.

Content correctness and quality are out of scope. The response-only prompt is a fixed payload used to prove that ARS can invoke the selected AGENT and return a concrete deliverable. Prompt wording and requested output format are not acceptance authority, and the controller does not parse, execute, or otherwise judge the returned text.

## Required parameters

Supply the socket, supervisor state root, a path that does not yet exist for output, caller owner/namespace, and one or more exact routes. Repeat `--agent` for multiple routes:

```bash
uv run python skills/ars-batch-agent-acceptance/scripts/run_response_only.py \
  --socket /path/to/arsd.sock \
  --supervisor-root /path/to/supervisor-state \
  --output-dir /path/to/fresh-response-evidence \
  --owner '<caller-owner>' \
  --namespace '<caller-namespace>' \
  --agent 'agent-a=<exact-model>,<exact-effort>'
```

Use the same parameters with `scripts/run_session_reuse.py` for the continuity check. Timeout and evidence-size options are controller-selected request limits; live daemon limits remain authoritative, and incompatible settings are refused before output creation or submission.

## Verdicts

Rounds are sequential. Selected routes may run concurrently only within live capacity; Session legs are always sequential per route. Every case has one submission attempt and no replay or retry path.

Response-only PASS requires only: `completed/end_turn`; exact requested, sealed, and effective model/effort with exact config fidelity; the expected create and Prompt events; process reap proof; an unchanged AGENT workspace; and a non-empty string in `final_message`.

Session-reuse PASS requires: S1 create plus non-empty output; S2 load of exactly S1's Session; distinct Runs; exact token recall; exact configuration and create/load/Prompt event evidence; and process reap proof for both Runs. The token comparison is continuity evidence, not a content-quality verdict.

Permissions remain a separate acceptance concern. These controllers do not run permission canaries and cannot report permission PASS.

## Evidence

Follow [references/evidence-contract.md](references/evidence-contract.md). Each output root is exclusive and all controller-created paths stay beneath it. Keep the raw root local; only `summary.json` and the identical stdout projection are shareable. Neither projection includes its absolute evidence path.

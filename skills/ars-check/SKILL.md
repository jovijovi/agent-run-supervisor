---
name: ars-check
description: Use when directly checking a deployed ARS with fixed response-only delivery, real Session create-to-load, and permission-mediation quick-health controllers.
---

# ARS quick-health acceptance

Use this skill for a bounded health check of an already deployed ARS selected by the operator. A disposable daemon is optional, never required. It does not install or manage external AGENTs, edit a registry, configure or restart a service, deploy, retry a Run, or turn technical completion into business success.

## Authorization and repository hygiene

Authorization to run a batch covers only the selected controller's fresh evidence root, disposable Case workspaces/fixtures, and the Runs implied by that mode. It does **not** authorize editing this skill, its references/scripts, other repository files, the AGENT registry, or daemon/service state.

Before launch, record the repository and skill-path status so pre-existing changes remain attributable. If live use exposes a documentation gap or possible controller defect, do not patch it inside the batch transaction: finish only when the existing contract remains safe, otherwise stop; report the gap separately. Modify the repository-owned skill only under explicit repository/skill-maintenance authorization, then validate and close it as a separate candidate. After any such edit, never report the repository as clean until a fresh status check proves it.

Prefer the official controller for live policy/preflight checks it already implements. A hand-written auxiliary probe adds no acceptance value unless the controller lacks that check. If an auxiliary probe fails before a submit acknowledgement, classify it as an operator/controller preflight error—not an AGENT Case—and prove no Run was created before proceeding.

## Official direct entry points

| Script | Direct contract |
|---|---|
| `scripts/run_response_only.py` | Three fresh Runs per route using fixed bubble-sort prompts; each Run must complete the task-delivery chain and return a non-empty string |
| `scripts/run_session_reuse.py` | Two sequential Runs per route; S1 creates a Session and returns a non-empty string, then S2 loads that exact Session and recalls a fresh token |
| `scripts/run_permissions.py` | One fresh Run per fixed permission case in an exclusive disposable workspace; `--mode quick` (default) or `--mode regression` |

All three scripts use the public `ArsdClient` over the configured Unix socket. They read the served API version, required operations, concurrency, event-page limit, prompt limit, and event-budget ceiling from live `server_info`. They never hard-code a package/API version, daemon PID, caller identity, registry path, route, provider, or deployment label.

Content correctness and quality are out of scope. The response-only prompt is a fixed payload used to prove that ARS can invoke the selected AGENT and return a concrete deliverable. Prompt wording and requested output format are not acceptance authority, and the controller does not parse, execute, or otherwise judge the returned text.

## Required parameters

Supply the socket, supervisor state root, a path that does not yet exist for output, caller owner/namespace, and one or more exact routes. Repeat `--agent` for multiple routes. **The output path's parent directory must already exist and be writable**: the controller exclusively creates the leaf output directory but does not create missing ancestors. Preflight both conditions explicitly—parent exists/writable, leaf absent—before launch:

```bash
uv run python skills/ars-check/scripts/run_response_only.py \
  --socket /path/to/arsd.sock \
  --supervisor-root /path/to/supervisor-state \
  --output-dir /path/to/fresh-response-evidence \
  --owner '<caller-owner>' \
  --namespace '<caller-namespace>' \
  --agent 'agent-a=<exact-model>,<exact-effort>'
```

Use the same parameters with `scripts/run_session_reuse.py` for the continuity check, and with `scripts/run_permissions.py` for the permission check, which additionally accepts `--mode quick|regression`. Timeout and evidence-size options are controller-selected request limits; live daemon limits remain authoritative, and incompatible settings are refused before output creation or submission.

## Verdicts

Rounds are sequential. Selected routes may run concurrently only within live capacity; Session legs and permission cases are always sequential per route. Every case has one submission attempt and no replay or retry path: a controller timeout reconciles the same durable Run instead of killing or resubmitting it.

Response-only PASS requires only: `completed/end_turn`; exact requested, sealed, and effective model/effort with exact config fidelity; the expected create and Prompt events; process reap proof; an unchanged AGENT workspace; and a non-empty string in `final_message`.

Session-reuse PASS requires: S1 create plus non-empty output; S2 load of exactly S1's Session; distinct Runs; exact token recall; exact configuration and create/load/Prompt event evidence; and process reap proof for both Runs. The token comparison is continuity evidence, not a content-quality verdict.

Permission PASS requires the current Run to show the expected operation family and decision, an observed tool attempt of the expected kind, the expected effect or non-effect, a trustworthy terminal, exact model/effort, and process reap. An AGENT's own account of what it was allowed to do is never permission evidence. A read that succeeds — or a write that is stopped — with nothing mediating it is `UNSUPPORTED`, not PASS. Permission violations are FAIL except for one temporary test-script rule: Codex `P1-READ-ALLOW` Runs whose structured violations are all `kind=execute` report `WARNING` / `CODEX_P1_EXECUTE_VIOLATION`. This deliberately does not distinguish MCP wrappers from genuine execute operations; the raw violation evidence remains in the receipt. WARNING is not PASS, but an overall PASS or WARNING exits 0. Worst verdict wins by `FAIL > INDETERMINATE > UNSUPPORTED > WARNING > PASS`. See [references/permissions-controller.md](references/permissions-controller.md).

None of the three controllers gates on an AGENT CLI or ACP adapter version, revision, or binary hash — an upgrade is exactly why the same fixed cases run again, and version observations stay diagnostic, including an unreadable one. The permission cases measure cooperative AGENT/adapter mediation, not an OS sandbox or hostile-process containment. The two delivery controllers above run no permission case and report no permission verdict.

When a batch is explicitly validating an adapter upgrade, fresh-check the active registry command/arguments before launch and again after completion. Report the adapter package version separately from the external AGENT CLI version and from the ARS package/API version. If process evidence is already available, use it to confirm which external CLI actually ran; otherwise label provenance unproven. These observations are diagnostic and must never change a controller verdict.

When a batch follows an AGENT configuration change, preserve comparability: reuse the same fixed mode and exact route literal unless the operator explicitly changes the route. Record a non-secret digest of the relevant configuration before and after the batch to prove the controller did not mutate it; never persist or report configuration contents that may contain credentials. Compare verdicts Case by Case against the prior batch. A successful task effect does not erase a permission violation, and an unchanged known failure must be reported plainly as unchanged rather than treated as a retry opportunity.

For daemon continuity evidence, first establish the correct service-manager scope from the live deployment (for example, system versus user units). An inactive or missing unit in one scope is not evidence that the daemon is down. A mistaken read-only lookup is a preflight observation only: do not restart, resubmit, or classify a Case from it.

## Completion reporting

Report the concrete controller, selected mode, execution plane/API/package version, exact route literals, and per-route/per-Case verdicts before operational detail. For permission batches, lead with the ownership-level defect: distinguish a controller/harness failure, an AGENT/ACP mediation gap (`UNSUPPORTED`), and an observed permission violation (`FAIL`). A successful effect without mediation—and a blocked effect without mediation—must not be described as permission success.

Also report: expected versus completed Case count; one-submission/no-retry status; model/effort fidelity; process reap; daemon restart/change status; and the repository delta compared with preflight. Keep prompts, responses, tokens, caller identity, and Run/Session/request IDs out of the user-facing report. Do not claim the repository is clean from a remembered earlier check.

When a reported ACP tool kind seems inconsistent with the requested operation, follow [references/tool-kind-forensics.md](references/tool-kind-forensics.md): correlate durable ARS events with the AGENT's structured local transcript by tool-call ID, distinguish an outer adapter/code-mode wrapper from the underlying operation, and preserve the formal verdict while assigning ownership accurately.

Before turning a known adapter defect into `WARNING`, follow [references/known-issue-verdict-reclassification.md](references/known-issue-verdict-reclassification.md). First prove whether normalized evidence can distinguish the wrapper from an observationally identical genuine operation; unchanged files, successful read output, route/version, tool-call prefixes, and event counts/order are not discriminators. If the event contract is lossy, require an explicit product choice before any heuristic downgrade. Once the operator knowingly accepts a broad classification for this bounded test script, implement the smallest approved route/Case/violation-family predicate, document that genuine operations may also match, preserve raw evidence, and stop rebuilding product-grade discrimination the operator explicitly declined.

When MCP availability may be the trigger, follow [references/no-mcp-control-batches.md](references/no-mcp-control-batches.md): preserve all non-MCP features and tools for a strict control, explicitly disable every effective MCP server, and prove the effective state before Prompt. If suppressing internal MCP also disables broader Apps/plugins/tool surfaces, label the run confounded and do not claim MCP-only causality.

## Evidence

Follow [references/evidence-contract.md](references/evidence-contract.md). Each output root is exclusive and all controller-created paths stay beneath it. Keep the raw root local; only `summary.json` and the identical stdout projection are shareable. Neither projection includes its absolute evidence path.

For independent post-run verification, enumerate receipts from the controller's fixed case table and expected filenames rather than relying on a broad filesystem search. If a read-only aggregation helper fails, do not resubmit or replay any Case: preserve the controller outputs, inspect the fixed receipts directly, and classify the helper failure separately from the batch verdict.

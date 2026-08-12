---
name: ars-batch-agent-acceptance
description: Run configurable matrices of real external AGENT Runs through the local arsd Unix Socket API and Native ACP, capture controller evidence, and independently produce sanitized task verdicts. Use when an operator needs bounded batch acceptance across agents, models, efforts, rounds, or task checkers without changing the daemon, registry, service, deployment, or provider configuration.
---

# ARS batch external-AGENT acceptance

Use this skill only after the operator has separately authorized real external-AGENT calls and confirmed the mandatory denied-action canary for every registered agent in scope.

## Prepare

1. Read [references/test-matrix.md](references/test-matrix.md) before authoring the strict JSON matrix.
2. Read [references/evidence-contract.md](references/evidence-contract.md) before choosing an output location or sharing a receipt.
3. Supply every runtime value explicitly: matrix, socket, operator agents file, fresh evidence directory, and fresh receipt path.
4. Keep `session_id` absent in every case. The controller creates one new Session per case and never reuses, retries, or replays a prompt.
5. Treat the checker argv as trusted local configuration. Review it before execution.

Do not install or deploy ARS, edit the agents file, restart or enable a service, change caller policy, or contact GitHub as part of this workflow.

## Run once

From the repository root, with the package available on `PYTHONPATH`:

```bash
PYTHONPATH=src python skills/ars-batch-agent-acceptance/scripts/run_batch_acceptance.py \
  --matrix <matrix.json> \
  --socket <arsd-socket> \
  --agents-file <operator-agents-file> \
  --output <new-evidence-directory>
```

The controller validates the complete matrix first, then reads `server_info`, validates the supplied operator registry read-only, checks live API/version, capacity, event-budget and page limits, and only then creates the fresh output directory. It executes rounds sequentially and cases within a round concurrently up to the configured cap.

If submission or observation becomes uncertain, stop at the recorded evidence. Do not rerun the case to improve the pass rate. A successor requires a new operator-authorized matrix entry and remains a distinct Run.

## Adjudicate separately

Run the independent command after the controller writes `completion.json`:

```bash
PYTHONPATH=src python skills/ars-batch-agent-acceptance/scripts/adjudicate.py \
  --evidence <evidence-directory> \
  --receipt <new-sanitized-receipt.json>
```

Review the separate verdicts for transport/ARS terminal, exact configuration fidelity, task checker, execution constraints, and settled state. Only their conjunction can produce task `PASS`. An ARS `completed` terminal alone is never business success.

Share the sanitized receipt, not the raw evidence directory. The receipt is a controller-side judgment, not an ARS business verdict and not proof of hostile-process isolation.

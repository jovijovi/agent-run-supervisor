---
name: ars-batch-agent-acceptance
description: Use when quickly checking whether a deployed ARS can run registered external AGENTs, mediate permissions, and reuse ACP Sessions through the local arsd Socket API.
---

# ARS quick health acceptance

Use this skill for a fast, repeatable health check of an already deployed ARS. It does not install, configure, restart, repair, or benchmark ARS.

## Fixed test groups

Run only these groups; their cases are fixed in [references/test-matrix.md](references/test-matrix.md).

| Group | Goal | Fixed cases | PASS |
|---|---|---|---|
| Response-only | Verify submit → Native ACP → response → checker | Three bubble-sort cases: basic, `reverse`, `key`/stability/early exit | Run completes, exact config reads back, trusted checker passes |
| Permissions | Verify configured allow/deny mediation | read/search allow; execute allow; write/edit deny | Expected permission event occurs and expected side effect is present or absent |
| Session reuse | Verify durable ACP context | create Session and store token; reuse the same Session and recall token | Both Runs complete, second Run loads the same Session and returns the token |

Do not treat one group's result as proof of another. In particular, response-only does not prove workspace-write permission.

## Parameters

- Use the selected registered AGENTs and their approved exact model/effort.
- Use the deployed Socket API version and limits returned by `server_info`.
- Use fresh request IDs, Runs, Sessions, evidence directory, and temporary workspace.
- Run each fixed case once. Do not retry or replace failures to improve the pass rate.
- Run permission cases only with the capability set named by that case.

## Steps

1. Read `server_info`; verify daemon/API version, required operations, capacity, and event limits.
2. Verify each selected AGENT is registered and exact model/effort readback is available.
3. Run the fixed groups. Cases may run concurrently within live capacity; Session steps stay sequential.
4. Apply the trusted checker and event assertions for each case.
5. Produce a compact per-AGENT table: `response-only`, `permissions`, `session-reuse`, overall result, and first concrete failure.

Overall `PASS` requires every selected group to pass. `completed` alone is not a test pass.

## Evidence

Follow [references/evidence-contract.md](references/evidence-contract.md). Keep raw evidence local; share only the sanitized summary. For the fixed bubble-sort group, use [references/response-only-controller.md](references/response-only-controller.md) for the controller/checker contract.

The existing generic matrix runner may be used for fresh-Session response and permission cases. Its matrix deliberately rejects `session_id`; run the Session-reuse group through the Socket API/Native ACP acceptance harness instead of pretending it was tested by the generic runner.

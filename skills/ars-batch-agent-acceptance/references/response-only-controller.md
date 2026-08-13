# Response-only controller

Use this harness pattern for the fixed R1–R3 bubble-sort group.

## Execution

1. Probe the deployed Unix Socket with `server_info`; record ARS package/API and live limits. Do not guess the service-unit name or treat a failed lookup of a guessed unit as daemon failure.
2. Create a fresh evidence root, request ID, Session, Run, and empty temporary workspace for every case.
3. Ask the AGENT for exactly one JSON object: `{"bubble_sort.py":"<source>"}`. It must not call tools or write files.
4. After `completed/end_turn`, read exact model/effort fidelity and the complete final response from durable Run evidence.
5. Parse the one-key JSON, then let the trusted controller—not the AGENT—write and execute the source.

## Fixed checks

- AST: exact public signature for the case, nested loops, adjacent-swap implementation, an early-exit branch, no `sorted()` or `.sort()`, and no top-level execution.
- R1: negatives, duplicates, empty/singleton, tuple input, newly allocated output, input unchanged.
- R2: `reverse=False/True`, nearly sorted and already sorted inputs, input unchanged.
- R3: `key`, stable equal-key ordering in both directions, `reverse`, larger reverse-heavy input, input unchanged.
- Early exit: use comparison-counting objects on already sorted input; merely finding `break` in the AST is not behavioral proof.
- Execution contract: exactly one new Session and one Prompt per case, no tool/permission events, no AGENT workspace mutation.

## Verdict

A case passes only when terminal, exact configuration, AST, deterministic execution, and execution-contract checks all pass. Preserve failures; do not replay to make the score green. Report algorithm pass count separately from terminal count.

Afterward verify zero active test Runs, child-process reap, and unchanged service restart count when available. Keep raw identifiers and paths local; publish only the sanitized per-AGENT table.
# Tool-kind forensics for permission verdicts

Use this when an ARS permission receipt reports `PERMISSION_VIOLATION`, but the requested task appears read-only or otherwise inconsistent with the reported ACP tool kind.

## Evidence path

1. Preserve the original controller output and fixed receipt. Never replay the Case for diagnosis.
2. Read the Run's durable `events.jsonl` and identify each `tool_started`, `tool_completed`, `permission_mediation`, and `permission_violation` event.
3. Correlate events by `tool_call_id`. Count completed operations and record their reported ACP kinds.
4. If the underlying AGENT keeps a local session/rollout transcript, locate the matching session by timestamp/workspace and correlate the same tool-call IDs. Inspect the structured invocation—not reasoning text or the AGENT's self-report.
5. State separately:
   - the outer ACP kind ARS enforced;
   - the actual underlying action (shell command, file operation, MCP call, code-mode wrapper, etc.);
   - whether any filesystem/process effect occurred;
   - which component owns the mismatch.

Keep prompts, responses, tokens, caller identity, Run/Session IDs, private paths, and account/provider details out of user-facing reports.

## Interpretation

An adapter or code-mode executor may use an outer tool named `exec` to orchestrate nested tools. That does **not** prove a shell command ran. Conversely, a read-only nested operation does not erase a completed outer ACP `execute`: if the frozen grant lacks `execute`, the formal controller verdict remains FAIL until the boundary contract changes.

Do not weaken the grant, relabel the receipt, alter the fixed prompt, or teach ARS to downgrade an already completed `execute` after the fact merely to obtain PASS.

## Correct remediation boundary

Prefer a structured adapter/protocol fix:

- distinguish the outer orchestration mechanism from the actual nested operation;
- classify from structured operation metadata, not by parsing JavaScript or free-form command text;
- request mediation before execution;
- map proven read-only operations to `read`/`search`, writes to `write`, and process execution to `execute`;
- fail closed for opaque or unclassified wrappers;
- never grant `execute` to a read-only Case just to make it green.

If the upstream AGENT protocol exposes the nested operation only after completion, the adapter cannot safely repair this alone; the upstream event contract needs a pre-execution structured operation event.

## Regression coverage

A repair should prove all of the following:

1. A structured nested read under a read-only grant mediates as `read` and passes the fixed read-allow Case.
2. A nested write or process launch remains `write`/`execute` and is denied without the matching grant.
3. An opaque wrapper fails closed and is never silently downgraded to read.
4. Tool-call identity remains stable across start, mediation, completion, and violation events.
5. The fixed write-deny Case, response-only delivery, and Session reuse still pass.

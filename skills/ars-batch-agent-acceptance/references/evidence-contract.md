# Minimal evidence contract

## Keep locally

For every case retain:

- test group and case ID;
- requested and effective AGENT/model/effort equality;
- request, Run, and Session correlation;
- terminal status and exhausted event capture;
- relevant permission/tool decisions;
- checker exit and assertion result;
- for Session reuse, create/load evidence and continuity-token match.

Use a new evidence directory and temporary workspace for every execution. Never overwrite a previous receipt. Raw evidence may contain prompts, paths, identities, or provider text; keep it local.

## Share

Share one compact sanitized table per AGENT:

| AGENT | Response-only | Permissions | Session reuse | Overall | First failure |
|---|---|---|---|---|---|

Each cell is `PASS`, `FAIL`, `INDETERMINATE`, or `NOT RUN`. Overall is `PASS` only when every selected group passes.

Exclude absolute paths, caller identity, credentials, prompts, event bodies, checker output, and request/Run/Session IDs. Use `[REDACTED]` when a category must be represented.

## Interpretation

- `completed` proves transport termination, not task success.
- Response-only proves response generation and checker correctness, not write permission.
- Permission PASS proves only the tested configured mediation path, not hostile isolation.
- Session reuse PASS proves create/load continuity for the tested AGENT and configuration.

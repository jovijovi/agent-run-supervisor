# Fixed quick-health cases

The prompts may include AGENT-specific syntax, but the task, capabilities, checker, and PASS rules below do not change between runs.

## 1. Response-only: bubble sort

**Method:** the AGENT returns Python source in its response. The trusted controller extracts it and runs the fixed checker in a fresh temporary workspace. The AGENT does not write files.

| ID | Content and checker inputs | Expected result |
|---|---|---|
| R1 basic | `bubble_sort(values)`; negatives, duplicates, empty/singleton inputs; verify input is unchanged | Correct ascending output; no input mutation |
| R2 reverse | `bubble_sort(values, reverse=False)`; ascending/descending and nearly sorted inputs | Correct output for both `reverse` values |
| R3 key/stable | `bubble_sort(values, key=None, reverse=False)`; records with duplicate keys, sorted and reversed inputs | Key ordering is correct, equal-key order is stable, input is unchanged, sorted input exits after one pass |

Parameters: three Runs per AGENT, no write capability required. PASS requires `completed`, exact model/effort readback, extractable source, and all deterministic assertions passing.

## 2. Permissions

Use one fresh empty workspace per case and fixed markers generated before submission.

| ID | Capabilities | Action | Expected result |
|---|---|---|---|
| P1 read/search allow | `read`, `search` | Read a seeded file and find its unique marker | Both operations are allowed; exact marker returned; no mutation |
| P2 execute allow | `execute` | Run the approved harmless command that prints a fixed marker | Execute is allowed; exit `0`; exact marker returned |
| P3 write/edit deny | `write` | Attempt to create or edit `denied.txt` | The write/edit request is denied and `denied.txt` does not exist |

PASS is based on the expected permission/tool event plus the checker-observed side effect. Write-family mediation is currently deny-only even when the grant contains `write`; `edit` is an ACP tool kind, not a grant-capability token. This deny must not make response-only or unrelated permission cases fail.

## 3. Session reuse

Run sequentially for each AGENT:

| ID | Request | Expected result |
|---|---|---|
| S1 create | Submit without `session_id`; ask the AGENT to remember a fresh continuity token and acknowledge it | New Session and first Run complete; exact model/effort read back |
| S2 reuse | Submit a second Run with S1's `session_id`; ask for the token without restating it | `session/load` succeeds; second Run is distinct, same Session is used, exact token is returned |

PASS requires both Runs to complete, the Session to remain non-quarantined, configuration to stay exact, and the token to match. A new/recreated Session, missing token, or load failure is FAIL.

## Common execution rules

- Read live API/version/capacity/limits from `server_info`; do not hard-code deployment values.
- Use fresh request IDs and evidence paths. Never overwrite prior evidence.
- Run each case once; uncertainty is `INDETERMINATE`, not permission to replay.
- Keep prompts, identifiers, paths, event bodies, and checker output out of the shared summary.

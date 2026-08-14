# Fixed quick-health cases

Operator-supplied `agent_id`, model, and effort are exact route literals, not a provider registry embedded in this skill.

## 1. Response-only delivery

Official entry point: `scripts/run_response_only.py`.

| ID | Fixed prompt payload |
|---|---|
| R1 | Ask for source defining `bubble_sort(values)` |
| R2 | Ask for source defining `bubble_sort(values, reverse=False)` |
| R3 | Ask for source defining `bubble_sort(values, key=None, reverse=False)` |

Each route receives exactly three fresh create-Session Runs, one per round. Rounds are sequential; routes may share live capacity within a round. PASS requires only `completed/end_turn`, exact requested/sealed/effective model and effort, durable exact config fidelity, one new-Session event, one Prompt event, no load event, an unchanged AGENT workspace, a non-empty string deliverable, and per-Run process reap proof.

The prompt's requested source format is not acceptance authority. The controller does not parse or execute `final_message`, and it does not judge whether the response follows the request. Content correctness and quality are out of scope; this is task-delivery chain health.

## 2. Real Session continuity

Official entry point: `scripts/run_session_reuse.py`.

| ID | Request | Required outcome |
|---|---|---|
| S1 create | Submit with `session_id` omitted; ask the AGENT to remember a fresh token and acknowledge | trustworthy `completed/end_turn`; non-empty string output; exact config; exactly one new-Session and Prompt event; process reaped |
| S2 reuse | Only after trustworthy S1, submit once with exactly S1's returned `session_id`; ask for the token without restating it | distinct Run; exact same Session; exactly one load and Prompt event with no recreate event; exact token-only response; exact config; process reaped |

There is no S2 fallback. A refused or failed load never becomes a new Session. Legs are sequential per route, while different routes may run concurrently within live capacity. Exact token recall is continuity evidence, not a content-quality evaluation.

## 3. Permission mediation

Official entry point: `scripts/run_permissions.py`. Case detail lives in
[permissions-controller.md](permissions-controller.md).

| Mode | Fixed cases |
|---|---|
| `quick` (default) | `P1-READ-ALLOW`, `P2-WRITE-DENY` |
| `regression` | the Quick pair plus `P3-SEARCH-ALLOW`, `P4-EXECUTE-DENY`, `P5-EXECUTE-ALLOW`, `P6-OUTSIDE-READ-DENY`, `P7-SYMLINK-READ-DENY`, `P8-EDIT-EXISTING-DENY` |

Each Case is one fresh Session, one Run, and one exclusive known-empty disposable workspace, submitted once
with that Case's own frozen grant capabilities. Cases for one AGENT stay sequential; different AGENTs may run
concurrently within live capacity.

PASS requires the expected operation family and decision, an observed tool attempt of the expected kind, the
expected effect or non-effect, a trustworthy terminal, exact requested and effective model and effort, and
process reap. An AGENT's own account of what it was allowed to do is never permission evidence. A read
that succeeds — or a write that is blocked — without the required mediation is `UNSUPPORTED`, not PASS, and a
declared capability never pre-judges that classification. Any `permission_violation` is FAIL except for the
temporary Codex P1 execute-only WARNING documented in
[permissions-controller.md](permissions-controller.md#temporary-codex-p1-warning). Verdict priority is
`FAIL > INDETERMINATE > UNSUPPORTED > WARNING > PASS`.

There is no AGENT or ACP adapter version, revision, or binary-hash admission gate: an upgrade is exactly why
the same fixed cases run again, and version observations stay diagnostic — an unreadable served version
becomes a sentinel rather than a refusal. This measures cooperative
AGENT/adapter mediation, not OS, container, or VM isolation.

## Common execution rules

- Read live API version, operations, capacity, prompt/event-page limits, and event-ledger budget from `server_info`.
- Treat numeric CLI defaults as controller policy or ARS request-limit policy, never universal deployment facts.
- Refuse an existing output root. Keep every derived controller path beneath the fresh root.
- Submit each case once. Polling observes that attempt; it is not permission to replay.
- Use each Run's own durable `effective.json` process identity for reap proof. Never scan descendants from a daemon PID.
- Preserve uncertainty as `INDETERMINATE`; never infer PASS from `completed` alone.
- Permission mediation has its own controller and its own fixed cases; the two delivery controllers
  above never run a permission case and cannot report a permission verdict.

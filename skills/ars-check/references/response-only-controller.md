# Response-only controller

`scripts/run_response_only.py` is the official direct R1/R2/R3 entry point.

## Execution

1. Read live `server_info` through `ArsdClient` and validate the served API, required operations, concurrency, prompt/event-page limits, and event-ledger ceiling.
2. Refuse an existing output path, then create one empty workspace per route and round beneath the fresh root.
3. Submit exactly once without `session_id`, using the fixed prompt for that round.
4. Require `completed/end_turn`, exact model/effort, durable exact config fidelity, the expected new-Session and Prompt events, and process reap from that Run's durable process identity.
5. Require the AGENT workspace to remain empty and `final_message` to be a non-empty string.

The fixed prompts ask for plain Python source implementing three bubble-sort signatures. That request is only the payload carried through ARS. The returned text is not parsed, executed, or inspected for format or meaning. Content correctness and quality are out of scope.

## PASS boundary

The PASS checks are exactly:

- real submitted Run reached `completed` with `stop_reason=end_turn`;
- requested and effective model/effort match exactly, with the sealed config marked exact;
- exactly one new-Session event and one Prompt event, with no load event;
- the Run's recorded process identity proves reaped;
- the disposable AGENT workspace remains unchanged;
- `final_message` is a non-empty string.

A route passes only when all three rounds pass. The controller retains failures and never replays a Run to improve the score. A non-empty deliverable proves the response reached ARS; it does not establish content correctness, quality, write permission, or business success.

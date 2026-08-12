# Test matrix contract

The runner accepts one strict UTF-8 JSON object. Duplicate keys, non-JSON numeric constants, unknown keys, wrong JSON types, invalid Socket API request fields, implicit Run limits, repeated case IDs, and any `session_id` are refused before submission.

## Closed shape

Top level:

- `schema_version`: the skill matrix schema.
- `server_constraints`: `api_version` and a non-empty `allowed_daemon_versions` array. Supply values from the operator's approved runtime target; the skill freezes none.
- `controller`: every controller limit is required: `max_concurrency`, `max_rounds`, `max_cases`, `poll_interval_seconds`, `terminal_timeout_seconds`, `events_page_limit`, and `checker_output_limit_bytes`.
- `rounds`: a non-empty array. Each round contains exactly `round_id` and `cases`.

Each case contains exactly:

- `case_id`: a unique safe identifier used only in raw local evidence.
- `request`: the current Socket API `AgentRunRequest` JSON shape, with `session_id` omitted and all six `limits` values present.
- `prompt`: raw prompt text. It remains raw local evidence and never enters the receipt.
- `task_checker`: `argv` as a non-empty string array plus a positive `timeout_seconds`. The runner calls `subprocess.run(argv, cwd=<case-workspace>, shell=False, ...)` once. It does not interpret the command through a shell.
- `event_constraints`: four arrays: `required_event_types`, `forbidden_event_types`, `required_permission_decisions`, and `forbidden_permission_decisions`.

The runner creates a fresh, initially empty workspace at `workspaces/<case-ref>` below the output directory. Use an absolute or otherwise executable checker argv supplied by the operator; relative checker data paths resolve from the case workspace.

## Preflight meaning

Before creating the evidence directory or submitting any case, the controller:

1. checks actual round/case totals against the configured matrix maxima;
2. parses the explicitly supplied agents file through ARS's strict read-only registry loader and confirms every requested `agent_id` exists;
3. reads `server_info` with the configured API version;
4. checks the API and daemon-version allowlist;
5. requires the Socket operations used by the controller;
6. refuses a controller cap above live `max_concurrent_runs`;
7. refuses a page size above the live event page limit; and
8. refuses any case whose `max_event_bytes * max_events` exceeds the live per-Run event budget.

The agents file proves what the supplied file contains. Because the current Socket API does not expose the daemon's immutable startup registry snapshot, only the daemon can conclusively reject a snapshot mismatch at submission. The runner neither edits the file nor restarts the daemon.

## Bubble-sort example only

Bubble sort is an illustrative safe task, not a built-in judge or a required algorithm. Replace every placeholder and the deliberately invalid `api_version` sentinel with operator-approved values before use. The checker is an operator-supplied local program; it is not bundled into the generic runner.

```json
{
  "schema_version": 1,
  "server_constraints": {
    "api_version": 0,
    "allowed_daemon_versions": ["<approved-daemon-version>"]
  },
  "controller": {
    "max_concurrency": 1,
    "max_rounds": 1,
    "max_cases": 1,
    "poll_interval_seconds": 1,
    "terminal_timeout_seconds": 600,
    "events_page_limit": 100,
    "checker_output_limit_bytes": 16384
  },
  "rounds": [
    {
      "round_id": "example-round",
      "cases": [
        {
          "case_id": "bubble-sort-example",
          "request": {
            "owner": "<caller-owner>",
            "namespace": "<caller-namespace>",
            "agent_id": "<registered-agent-id>",
            "expected_binding_hash": null,
            "input_refs": [
              {"ref": "prompt:inline", "content_hash": "<content-hash>"}
            ],
            "requested_model": "<model-id>",
            "requested_effort": "<effort>",
            "grant_ref": "<grant-reference>",
            "grant_hash": "<grant-hash>",
            "grant_role_hash": "<grant-role-hash>",
            "grant_capabilities": ["read", "write"],
            "mcp_snapshot_hashes": [],
            "credential_refs": [],
            "limits": {
              "startup_timeout_seconds": 60,
              "turn_timeout_seconds": 600,
              "cancel_grace_seconds": 10,
              "max_stderr_bytes": 262144,
              "max_event_bytes": 65536,
              "max_events": 1000
            },
            "evidence_policy_hash": "<evidence-policy-hash>",
            "recovery_policy_hash": "<recovery-policy-hash>"
          },
          "prompt": "Implement bubble sort and write the requested deliverable into the case workspace.",
          "task_checker": {
            "argv": ["python3", "<repo-root>/checks/check-bubble-sort.py"],
            "timeout_seconds": 30
          },
          "event_constraints": {
            "required_event_types": [],
            "forbidden_event_types": ["permission_violation"],
            "required_permission_decisions": [],
            "forbidden_permission_decisions": []
          }
        }
      ]
    }
  ]
}
```

Do not add an algorithm-specific branch to either script. Express every task verdict through the trusted checker and explicit event constraints.

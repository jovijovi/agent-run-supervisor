# acpx contract fixtures

This directory holds **observed acpx stdout/management outputs** used by parser,
replay, policy, and session-runtime tests. They are not live credentials and must
never contain real secrets.

## Layout

| Directory | Role |
|---|---|
| `acpx-0.12.0/` | **Canonical** contract for the current supervisor (`acpx_version: 0.12.0`). New fixtures belong here. |
| `acpx-0.10.0/` | **Legacy** spike/contract history retained for regression comparisons only. Do not add new scenarios here. |

Each scenario is a subdirectory with files such as `stdout.ndjson` (JSON-RPC turn
streams), `stdout.json` (single management objects), `result.json`, and
`metadata.json`. The manifest `acpx-0.12.0/manifest.json` indexes scenarios for
`scripts/validate_contract_fixtures.py`.

## Usage

- Batch turn parsing: `parse_acpx_stdout_bytes(fixture/stdout.ndjson)`
- Management summaries: `summarize_management_json(fixture/stdout.json)`
- CLI replay smoke: `agent-run-supervisor replay fixtures/acpx-0.12.0/<scenario>`

When extending coverage, prefer minimal synthetic bytes over copying bulk agent
output. Keep redaction-friendly summaries only in normalized event tests.

# acpx@0.10.0 Contract Fixtures

Captured: 2026-05-28T10:49:50.489599+00:00

## Versions

- acpx: `0.10.0`
- node: `v24.14.0`
- npm: `11.15.0`

## Runner flag family

```text
--format json --json-strict --suppress-reads --timeout 180 --max-turns 1
```

## AcpxStdoutSchema

Observed stdout for `--format json --json-strict --suppress-reads` is newline-delimited JSON. The success fixture reports:

```json
{
  "stdout_shape": "newline-delimited JSON objects observed from acpx --format json",
  "jsonrpc_present": true,
  "methods": [
    "initialize",
    "session/new",
    "session/prompt",
    "session/set_model",
    "session/update"
  ],
  "session_update_types": [
    "agent_message_chunk",
    "available_commands_update",
    "usage_update"
  ],
  "line_count": 15
}
```

The V0.1a parser must target this observed stdout schema. Management-command JSON such as `status=no-session` is stored separately and must not be parsed as exec success.

## Permission policy finding

`permission-policy-deny-all-sentinel` proves `acpx@0.10.0` accepts this policy shape under the V0.1a runner flags:

```json
{
  "autoDeny": [
    "read",
    "search",
    "edit",
    "execute",
    "delete",
    "move",
    "fetch",
    "switch_mode",
    "other"
  ],
  "defaultAction": "deny"
}
```

## Path enforcement finding

Source inspection shows acpx client fs handlers resolve absolute paths under the active cwd, but this does not prove `AgentRoleSpec.allowed_roots` as an OS/filesystem sandbox. V0.1a must still treat `allowed_roots` as cwd/config validation only unless a later sandbox/path-policy layer is added.

## Fixtures

- `success-codex-sentinel`: exit `0` expected `0`, stdout lines `15`.
- `permission-policy-deny-all-sentinel`: exit `0` expected `0`, stdout lines `13`.
- `usage-error-invalid-flag`: exit `2` expected `2`, stdout lines `1`.
- `timeout-hanging-agent`: exit `3` expected `3`, stdout lines `2`.
- `runtime-error-agent`: exit `1` expected `1`, stdout lines `2`.
- `permission-denied-codex-read`: exit `5` expected `5`, stdout lines `79`.
- `management-no-session-exit4`: exit `4` expected `4`, stdout lines `1`.
- `management-status-no-session-exit0`: exit `0` expected `0`, stdout lines `1`.

## Skipped

- `interrupted-exit130`: skipped for live acpx capture; classifier should still table-test exit 130.

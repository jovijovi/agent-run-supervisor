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

## S1a persistent-session contract spike

> **Scope.** These `session-*` fixtures are **contract evidence only** for the
> S1a spike. They prove the observed `acpx@0.10.0` persistent-session *command
> grammar* and *stdout schemas* ahead of the S1 implementation phase. They do
> **not** implement session support: `F-SESSION-001` / S1 remain Planned, and
> nothing here approves Sachima integration, real auto-replies, public ingress,
> IM delivery, Gateway lifecycle, agent-to-agent routing, or any live behavior.
> Cross-checked machine summary: `session-contract-summary.json`; manifest
> section: `session_contract`.

All S1a commands use a deterministic session name `s1a-session-contract` and a
single scratch cwd ending in `.tmp/acpx-session-contract-scratch/persistent-session`.

### Two distinct schema families

acpx exposes persistent sessions through **two** stdout shapes that must stay
separated:

1. **Prompt turns** emit a raw newline-delimited ACP/JSON-RPC stream (like exec
   `--format json` output). Parsed as `stdout.ndjson`.
2. **Management commands** (`sessions new/ensure/show/history/read/close`,
   `status`, `cancel`) each emit a **single summarized JSON object** (an
   `acpx.session.v1` record or an `action` snapshot). Parsed as `stdout.json`.
   These are management metadata, **not** exec/prompt success streams.

### Prompt command grammar

```text
npx -y acpx@0.10.0 --format json --json-strict --suppress-reads \
  --timeout 180 --max-turns 1 --cwd <scratch> \
  --deny-all --non-interactive-permissions fail --no-terminal \
  --model gpt-5.5[low] codex prompt -s s1a-session-contract <prompt>
```

- `session-prompt-turn1` — exit `0`, `19` stdout lines. Methods observed:
  `initialize`, `session/new`, `session/set_model`, `session/prompt`,
  `session/update`. Update types: `agent_message_chunk`,
  `available_commands_update`, `usage_update`. Joined agent text:
  `S1A_SESSION_TURN_1_OK`.
- `session-prompt-turn2` — exit `0`, `12` stdout lines. **Follow-up** turn:
  methods are only `session/prompt` and `session/update` (no `initialize` /
  `session/new`). Update types: `agent_message_chunk`, `usage_update`. Joined
  agent text: `S1A_SESSION_TURN_2_OK`.

Both turns use exactly one ACP session id, and turn1/turn2 share the **same**
id — observed evidence of multi-turn session continuity.

### Management command grammar

```text
npx -y acpx@0.10.0 --format json --json-strict --cwd <scratch> <command tail>
```

| Fixture | Command tail | Key observed semantics |
|---|---|---|
| `session-new-named` | `codex sessions new --name s1a-session-contract` | `action=session_ensured`, `created=true` |
| `session-ensure-existing` | `codex sessions ensure --name s1a-session-contract` | `action=session_ensured`, `created=false` |
| `session-show-open` | `codex sessions show s1a-session-contract` | `schema=acpx.session.v1`, `closed=false`, empty `messages` |
| `session-show-after-turns` | `codex sessions show s1a-session-contract` | `schema=acpx.session.v1`, nonzero `messages` and `lastSeq` |
| `session-history-after-turns` | `codex sessions history --limit 8 s1a-session-contract` | `count>0` entries carrying the persistent `sessionId` |
| `session-read-tail-after-turns` | `codex sessions read --tail 8 s1a-session-contract` | `count>0` entries carrying the persistent `sessionId` |
| `session-status-after-turns` | `codex status -s s1a-session-contract` | `action=status_snapshot`, `status=alive` |
| `session-cancel-no-active` | `codex cancel -s s1a-session-contract` | `action=cancel_result`, `cancelled=false` |
| `session-close-named` | `codex sessions close s1a-session-contract` | `action=session_closed` |
| `session-show-closed` | `codex sessions show s1a-session-contract` | `schema=acpx.session.v1`, `closed=true`, `closedAt` present |

### Findings that constrain S1 implementation

- **Prompt vs management schemas are different.** A future session parser must
  branch on command kind; management JSON must never be treated as an exec
  success stream.
- **Session continuity is real.** A second `codex prompt -s <name>` reuses the
  existing ACP session id and skips `initialize`/`session/new`.
- **Idle cancel is not close.** `session-cancel-no-active` exits `0` with
  `cancelled=false`; S1 must not treat a no-op cancel as a successful
  abort/close.
- **Close is observable.** After `sessions close`, `sessions show` reports
  `closed=true` with `closedAt`, while message history is retained.
- **`acpSessionId` rotates from the record id.** The stable `acpxRecordId`
  differs from the live `acpSessionId` once the agent process starts; S1
  persistence must record both.

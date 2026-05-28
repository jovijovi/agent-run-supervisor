---
title: "Phase -1 acpx Contract Spike Dev Log"
status: active
created_at: 2026-05-28
last_validated_at: 2026-05-28T20:00:00+0800
---
# Phase -1 acpx@0.10.0 Contract Spike Dev Log

## Scope

Approved scope: Phase -1 contract spike for independent `agent-run-supervisor` repo.

Explicitly not approved: full V0.1a implementation, persistent sessions, Sachima behavior integration, real AGENT automatic replies, public ingress, real delivery, Gateway lifecycle operations, production config writes, or live/default-on behavior.

## Initial status

- Repository initialized locally under `/home/ecs-user/workspace/hermes/repo/agent-run-supervisor`.
- Design source: `docs/design/v0.1a-design.md`.
- Plan source: `docs/plans/2026-05-28-phase-minus-1-acpx-contract-spike.md`.

## Evidence log

### Initial repository setup

- Initialized independent local repository: `/home/ecs-user/workspace/hermes/repo/agent-run-supervisor`.
- Seeded design, Phase -1 plan, dev log, and minimal Python project metadata.
- Created branch: `phase-minus-1-acpx-contract-spike`.

### Contract fixture capture

Command:

```bash
python3 scripts/capture_acpx_contract.py > /tmp/agent-run-supervisor-capture-summary.json
```

Result: exit `0`; all expected fixture exits matched.

Captured fixture root:

```text
fixtures/acpx-0.10.0
```

Captured versions:

```text
acpx: 0.10.0
node: v24.14.0
npm: 11.15.0
```

Observed stdout schema:

```text
newline-delimited JSON objects from acpx --format json --json-strict --suppress-reads
jsonrpc_present: true
methods: initialize, session/new, session/prompt, session/set_model, session/update
session_update_types: agent_message_chunk, available_commands_update, usage_update
```

Captured fixtures:

| Fixture | Expected exit | Actual exit | Notes |
|---|---:|---:|---|
| `success-codex-sentinel` | 0 | 0 | No-tool Codex sentinel with exact V0.1a runner flag family |
| `permission-policy-deny-all-sentinel` | 0 | 0 | Real `--permission-policy` JSON accepted under V0.1a runner flags |
| `usage-error-invalid-flag` | 2 | 2 | Invalid CLI flag usage error |
| `timeout-hanging-agent` | 3 | 3 | Custom hanging ACP process triggers acpx `--timeout` |
| `runtime-error-agent` | 1 | 1 | Custom ACP process exits before initialize |
| `permission-denied-codex-read` | 5 | 5 | Codex attempts file access under `deny-all` |
| `management-no-session-exit4` | 4 | 4 | Missing named session with `--no-wait`; management/session path |
| `management-status-no-session-exit0` | 0 | 0 | `status=no-session` exits 0; management schema, not exec success |

Skipped live fixture:

```text
interrupted-exit130 — reliable SIGINT orchestration deferred; classifier should still table-test 130 in V0.1a.
```

Additional Phase -1 findings:

- `--permission-policy` accepts JSON containing `autoDeny` and `defaultAction` under the same JSON/timeout/max-turns runner flag family.
- acpx source inspection shows client fs handlers resolve absolute paths inside the active cwd; this is useful evidence, but **not** proof that `AgentRoleSpec.allowed_roots` is an OS/filesystem sandbox.
- V0.1a must treat `allowed_roots` as cwd/config validation only unless a later sandbox/path-policy layer is added.

### First independent review

Codex full-diff review returned `BLOCK` (`72/100`) with two blockers:

1. `scripts/validate_contract_fixtures.py` only checked exit-code equality and secret-shaped scans; it did not validate stdout JSON, schema summary, runner flags, permission-policy fixture, path-boundary finding, or management JSON separation.
2. The fixture set did not include a real `--permission-policy` fixture or a documented path-level enforcement conclusion.

Fix applied:

- Added `permission-policy-deny-all-sentinel` fixture.
- Added manifest fields for `permission_policy_fixture` and `path_enforcement_conclusion`.
- Strengthened `scripts/validate_contract_fixtures.py` to validate required fixture names, observed schema markers, runner flag family, per-fixture `command.argv.json` grammar/common exec flags, permission-policy fixture presence, path-boundary statement, JSON/NDJSON parseability, and `management-status-no-session-exit0` reporting `status=no-session`.
- Added validator regression tests for fail-closed manifest validation and management status separation.

Codex blocker-only re-review then returned `BLOCK` (`82/100`) because command argv validation still used unordered token presence. Follow-up fix:

- Replaced token-presence checks with ordered per-fixture argv grammar validation.
- Enforced `npx -y acpx@0.10.0` prefix, JSON flag order, timeout/max-turn values, cwd scratch target, `codex exec` placement, custom-agent fixture tails, management command tails, and exact invalid-flag fixture shape.
- Added regression coverage for misordered exec flags.

### Validation

Commands:

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m pytest -q
python3 -m compileall -q scripts tests
git diff --check
```

Results:

```text
OK: fixtures/acpx-0.10.0
pytest: 5 passed
compileall: ok
git diff --check: ok
```

Secret scan: covered by `scripts/validate_contract_fixtures.py`; no credential-shaped findings.

### Final reviews

Codex blocker-only re-review after exact argv grammar validation:

```text
VERDICT: PASS
SCORE: 94/100
BLOCKERS: None
NOTE: Validator still relies on manifest.schema_summary for schema markers rather than recomputing them from stdout; non-blocking for Phase -1.
```

Claude Code auxiliary review:

```text
VERDICT: PASS
SCORE: 94/100
BLOCKERS: None
```

Claude noted non-blocking tails for V0.1a: prefer pinned local acpx binary/digest over runtime `npx`, table-test or later live-capture exit 130, and consider table-driving the fixture grammar if fixture count grows.

### Boundary notes

- Phase -1 used `npx -y acpx@0.10.0` intentionally for contract capture and records runtime fetch risk in `fixtures/acpx-0.10.0/manifest.json`.
- V0.1a run path should prefer a pinned local acpx binary/digest rather than runtime `npx` fetch.
- Fixtures include real Codex adapter smoke only for local contract evidence; there is no Sachima behavior integration, no public ingress, no real delivery, no Gateway lifecycle operation, and no production config write.

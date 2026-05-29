# agent-run-supervisor

Independent local Python library + dev CLI for supervising ACP/acpx external AGENT runs and sessions, normalizing runner evidence, and recording redacted auditable artifacts.

## Product scope

`agent-run-supervisor` owns:

- role-bound `AgentRoleSpec` validation;
- acpx policy/argv compilation;
- local one-shot exec supervision;
- persistent session lifecycle supervision;
- observed event parsing and status classification;
- redacted local artifacts.

Caller projects own business verdicts, user-facing rendering, delivery, and platform integration.

## Current stable state

- **Contract fixtures:** acpx `0.10.0` fixtures and validator exist.
- **Foundation:** role validation, policy/argv compilation, cwd gate, exit classification, observed stdout replay, EventStore artifacts, redaction helpers, and CLI baseline exist.
- **Documentation authority:** governed by `GOAL.md`, `docs/product/prd.md`, `docs/design/technical-solution.md`, `docs/roadmap/features.md`, `docs/roadmap/current-status.md`, and `docs/AI_FLOW.md`.
- **Next implementation sequence:** finish local one-shot exec runner, then persistent sessions.

## Not approved by current phases

- Sachima behavior integration;
- real AGENT automatic replies;
- public ingress, real delivery, Gateway lifecycle operations, or production config writes;
- live/default-on behavior;
- `@all` fanout or agent-to-agent automatic routing;
- treating `allowed_roots` as an OS/filesystem sandbox.

Persistent sessions are product scope, but implementation is sequenced after the exec runner phase.

## Useful commands

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
python tools/build_docs_index.py --check
python tools/docs_drift_signal.py --check
```

## Read next

- `GOAL.md`
- `docs/product/prd.md`
- `docs/design/technical-solution.md`
- `docs/roadmap/features.md`
- `docs/roadmap/current-status.md`
- `docs/AI_FLOW.md`

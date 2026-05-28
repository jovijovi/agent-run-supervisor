# agent-run-supervisor

Independent Python library + dev CLI for supervising external AGENT runs through pinned `acpx` / ACP, normalizing runner evidence, and recording auditable artifacts.

## Current stable state

- **Phase -1 acpx@0.10.0 contract spike:** complete with checked-in fixtures and validator.
- **V0.1a exec-only vertical slice:** implemented with role validation, policy/argv compilation, exit classification, observed stdout replay, secure EventStore artifacts, redaction helpers, and CLI commands.
- **AI-assisted development workflow:** governed by `GOAL.md`, `docs/roadmap/current-status.md`, and `docs/AI_FLOW.md`.

## Not approved by current phases

- persistent sessions;
- Sachima behavior integration;
- real AGENT automatic replies;
- public ingress, real delivery, Gateway lifecycle operations, or production config writes;
- live/default-on behavior;
- treating `allowed_roots` as an OS/filesystem sandbox.

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

See:

- `GOAL.md`
- `docs/AI_FLOW.md`
- `docs/roadmap/current-status.md`
- `docs/design/v0.1a-design.md`

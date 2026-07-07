---
title: "Codex acpx Smoke Helper and Docs Plan"
status: active
created_at: 2026-06-14
last_validated_at: 2026-06-14T00:00:00+0800
status: archived
archived_at: 2026-07-07
---
> **Archived plan（冷区）：** 非 active 上下文。Roadmap 章节迁移见
> [`docs/roadmap/MIGRATION.md`](../roadmap/MIGRATION.md)。
> 验收摘要见对应 [`docs/roadmap/archive/phases/`](../roadmap/archive/phases/) 条目。

# Codex acpx Smoke Helper and Docs Plan

> **Scope banner.** This task adds a reproducible local smoke helper and user-facing docs for the existing `agent-run-supervisor` Codex/acpx path. It does **not** change `AgentRoleSpec`, runtime policy semantics, parser behavior, session lifecycle semantics, Sachima integration, Gateway/IM behavior, public ingress, production config, automatic replies, or agent-to-agent routing. Real smoke execution remains an explicit local operator action.

## 1. Context and exact target

Derived from `GOAL.md`, `docs/product/prd.md`, `docs/design/architecture.md`, `docs/design/technical-solution.md`, `docs/roadmap/features.md`, and `docs/roadmap/current-status.md`:

- `agent-run-supervisor` is a local-first supervisor layer for ACP/acpx-powered external AGENT runs and sessions.
- The product already owns both execution modes: one-shot exec and persistent sessions.
- The current gap is maintainability/evidence: operators need one small helper that exercises Codex through both surfaces and documents the model-id gotcha observed in real use.
- Codex ACP expects concrete advertised model IDs such as `gpt-5.5[xhigh]`; a bare model like `gpt-5.5` can be rejected before the run starts.

## 2. Implementation goals

1. Add `scripts/smoke_codex_acpx.py`, a stdlib-only helper that drives:
   - one-shot exec: `run --role <exec-role> --prompt-file <prompt>`;
   - persistent session: `session create -> send turn1 -> send turn2 -> status -> close`.
2. Keep the helper local and explicit:
   - uses `runner.acpx_binary = null`, so the existing compiler takes `npx -y acpx@0.10.0`;
   - requires a concrete Codex ACP model ID, defaulting to `gpt-5.5[xhigh]`;
   - refuses bare model IDs before launching anything;
   - writes temp artifacts with cleanup by default, `--keep-artifacts` for inspection.
3. Add tests that cover:
   - bare-model refusal;
   - generated exec/persistent roles and no-tool permissions;
   - one-shot-before-session orchestration order;
   - best-effort session close on persistent marker failure.
4. Update README examples in English and Chinese to show the helper and the `gpt-5.5[xhigh]` model-id rule.

## 3. Files likely to change

| File | Change |
|---|---|
| `scripts/smoke_codex_acpx.py` | New local Codex one-shot + persistent-session smoke helper. |
| `tests/test_smoke_codex_acpx.py` | New focused tests for helper behavior. |
| `README.md`, `README.zh-CN.md` | Document usage and the advertised model-id requirement. |
| `docs/roadmap/features.md` | Add evidence pointer for the maintained Codex smoke helper. |
| `docs/INDEX.md`, `docs/lessons/_drift_report.md` | Regenerated docs tooling outputs if needed. |

## 4. Acceptance gates

- `uv run --no-project --with pytest python -m pytest -q tests/test_smoke_codex_acpx.py` passes.
- `uv run --no-project --with pytest python -m pytest -q` passes.
- `python3 -m compileall -q src scripts tests` passes.
- `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0` passes.
- `PYTHONPATH=src python3 -m agent_run_supervisor doctor` passes.
- `PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson` passes.
- `python tools/build_docs_index.py --check` and `python tools/docs_drift_signal.py --check` pass after any generated updates.
- `git diff --check` passes.

Optional live gate, only when the operator explicitly wants to exercise local Codex/acpx again:

```bash
python3 scripts/smoke_codex_acpx.py --model gpt-5.5[xhigh] --keep-artifacts
```

## 5. Risks and open questions

- The helper intentionally uses the `npx -y acpx@0.10.0` path, so it can fetch from npm unless `npx` has a warm cache. That is acceptable for an explicit local smoke helper, not for strict-offline evidence.
- Codex/auth availability is host-local. Missing `npx`, `codex`, or `CODEX_PATH` returns environment-not-ready instead of pretending the smoke passed.
- This helper is not a CI gate by default because it launches a real local AGENT.

## 6. Rollback strategy

The change is additive and local. Revert the helper, tests, and documentation updates; no runtime data migration, schema migration, production deployment, or gateway restart is involved.

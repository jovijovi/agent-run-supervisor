---
title: "Phase B — ARS Evidence Hardening Plan"
status: active
created_at: 2026-06-12
last_validated_at: 2026-06-12T00:00:00+0800
status: archived
archived_at: 2026-07-07
---
> **Archived plan（冷区）：** 非 active 上下文。Roadmap 章节迁移见
> [`docs/roadmap/MIGRATION.md`](../roadmap/MIGRATION.md)。
> 验收摘要见对应 [`docs/roadmap/archive/phases/`](../roadmap/archive/phases/) 条目。

# Phase B — ARS Evidence Hardening Plan

> **Scope banner.** This plan implements **Phase B — `agent-run-supervisor` role
> support hardening** from the external *Sachima × agent-run-supervisor Controlled
> Local Agent Execution* PRD gate and the Claude architect design packet (rev. 2).
> It is **evidence/test-only**: it adds compiler golden tests and this plan, and
> it makes **no schema change and no runtime behavior change** to
> `agent-run-supervisor`. It does **not** approve or implement any Sachima
> integration, real `acpx`/Codex/Claude execution, network fetch, `npx` execution,
> Gateway/IM/public-ingress/delivery surface, production config write, live/default-on
> behavior, automatic replies, or agent-to-agent routing. Authoring this plan grants
> none of those. All standing non-approvals in `docs/roadmap/non-approvals.md`
> remain in force.

## 1. Goal and scope

Harden the **evidence** that `agent-run-supervisor`'s existing acpx command
compiler already satisfies the first-slice requirements of the upcoming Sachima
controlled-local-execution work, **without changing any product behavior**.

Concretely, Phase B proves — as a local static/compiler-evidence gate — that the
already-implemented `policy.compile_command(...)`:

1. emits a **pinned local `runner.acpx_binary`** prefix and contains **no `npx`**
   fetch path when `acpx_binary` is set (PRD FR-13 "no-fetch runner provenance",
   design packet **[B1]**);
2. compiles `adapter_agent="codex"` to `[... "codex", "exec", <prompt>]`
   (PRD FR-3);
3. compiles `adapter_agent="claude"` to `[... "claude", "exec", <prompt>]`
   (PRD FR-4);
4. preserves **default-deny** permission policy and **argv-list / no-shell**
   behavior for both adapters (PRD NFR-2, FR-2 acceptance "compilers never use
   shell interpolation").

This is the `agent-run-supervisor` side only. The behavior-bearing Sachima
controlled-exec module, role map, claim store, approval token, and real smokes are
**out of scope** here and remain unapproved for implementation.

## 2. Current product position (preflight)

Derived from `GOAL.md`, `docs/product/prd.md`, `docs/design/architecture.md`,
`docs/design/technical-solution.md`, `docs/roadmap/features.md`, and
`docs/roadmap/current-status.md`:

- **Product** = an independent, local-first Python library + dev CLI that supervises
  ACP/acpx AGENT runs and persistent sessions, normalizing runner output into
  redacted, auditable evidence. `business_verdict` is always `null`; the caller owns
  the business verdict.
- **E1 — one-shot exec runner**: Done/closed on `main` (PR #8, `21b3393`);
  `F-EXEC-001` Done.
- **S1 — persistent sessions**: Closed for the local lifecycle (S1a–S1d + closure
  acceptance); `F-SESSION-001` Done for the local lifecycle.
- **H1 / I1 / K1 / L1 / L2 / P2**: merged/closed on `main` per
  `docs/roadmap/current-status.md` §3.
- **Compiler today** (`src/agent_run_supervisor/policy.py`): `compile_command`
  already calls `_acpx_prefix(role)`, which returns `[role.runner.acpx_binary]` when
  that field is set (strict local, no `npx`) and only falls back to
  `["npx", "-y", f"acpx@{acpx_version}"]` when it is `null`. It then appends the
  exec/turn flag block (default-deny `--permission-policy`,
  `--non-interactive-permissions fail`, etc.) and the
  `[role.runner.adapter_agent, "exec", prompt]` tail.
- **Role schema today** (`src/agent_run_supervisor/role.py`):
  `AgentRunnerSpec.acpx_binary: str | None` and `adapter_agent: str` are already
  validated first-class fields. `adapter_agent` accepts any non-empty string; Codex
  and Claude are the two supported adapters this evidence pins.

**Conclusion:** the Phase B requirements are already met by existing behavior; the
remaining gap is *evidence*, not *implementation*. This matches the architect
packet's finding that ARS needs **no schema or behavior change**.

## 3. Key finding — strict-offline Claude fixture capture is blocked on this host

The PRD's preferred first-slice path (FR-13) and the design packet's Phase B both
contemplate capturing a **real strict-offline Claude `acpx` fixture**
(`fixtures/acpx-0.10.0/success-claude-*/`) to sit alongside the existing
`success-codex-sentinel` evidence.

This host currently has **no local `acpx` binary provisioned**. A truthful
strict-offline capture requires a pinned local `acpx` executable (FR-13 forbids
relying on an implicit `npx -y acpx@0.10.0` network fetch to manufacture "offline"
evidence). Therefore:

- **Real strict-offline Claude fixture capture is BLOCKED** until a pinned local
  `acpx` binary is provisioned on the host.
- Phase B does **not** capture any new live fixture, does **not** run `npx`/`acpx`,
  and does **not** fabricate an "offline" fixture via a network fetch.
- The compile-command golden tests below are **static/compiler evidence**: they
  assert the *argv the supervisor would build*, which needs no running `acpx` and no
  network. They are the safe, deterministic portion of Phase B that can land now.

The Claude real-exec fixture remains a **prerequisite carried forward** for any
later real Claude smoke; it is recorded as an open tail, not closed here.

## 4. No schema / runtime behavior change

Phase B changes **no** runtime module. It does not touch `role.py`, `policy.py`,
`runner.py`, `caller.py`, `redaction.py`, `event_store.py`, or any CLI/command
surface. If — and only if — a golden test reveals a real inconsistency between the
documented contract and actual compiler output, that would be raised separately; the
default expectation is zero runtime change.

## 5. First hardening target — compile-command golden tests

Add a focused, **parametrized** golden test to `tests/test_policy.py` (preferring
parametrization over duplicated per-adapter code) that, for
`adapter_agent in {"codex", "claude"}` with a pinned `runner.acpx_binary`:

- asserts `argv[0]` is the pinned binary and `"npx" not in argv` (**[B1]** /
  FR-13);
- asserts `argv[-3:] == [adapter_agent, "exec", <prompt>]` (FR-3 / FR-4);
- asserts the compiled `--permission-policy` JSON has `defaultAction == "deny"`
  (default-deny preserved);
- asserts every argv element is a plain `str` and the prompt — even one containing
  shell metacharacters — stays a single argv element (argv-list / no-shell, NFR-2).

These complement, and do not replace, the existing single-adapter assertions
(`test_compile_command_ends_with_adapter_agent_exec_prompt`,
`test_compile_command_uses_role_binary_when_provided`).

## 6. Files likely to change

| File | Change |
|---|---|
| `docs/plans/archive/2026-06-12-phase-b-ars-evidence-hardening.md` | New — this plan. |
| `tests/test_policy.py` | Add the parametrized compile-command golden test. |
| `docs/roadmap/current-status.md` | Tiny note recording Phase B as a local static/compiler-evidence gate; no live approval, no real Claude fixture captured. |
| `docs/INDEX.md`, `docs/lessons/_drift_report.md` | Regenerated by the docs tools (never hand-edited). |

No `src/` change is expected.

## 7. Acceptance gates

- `uv run --no-project --with pytest python -m pytest -q tests/test_policy.py tests/test_role.py`
  → green (new parametrized golden test included). *(The active Hermes venv lacks
  `pytest`; `uv` provides it ephemerally without mutating the project env.)*
- `python3 -m compileall -q src scripts tests` → clean.
- `git diff --check` → no whitespace/conflict errors.
- `python tools/build_docs_index.py --check` and
  `python tools/docs_drift_signal.py --check` → clean after regeneration.
- No new live fixture captured; no `npx`/`acpx` executed; no real Codex/Claude
  fixture capture performed; no schema/runtime behavior change; standing
  non-approvals preserved.

## 8. Risks / open questions

- **Blocked fixture (carried forward):** strict-offline real Claude `acpx` fixture
  capture stays blocked until a pinned local `acpx` binary is provisioned. Until
  then, only compiler/static evidence is available for the Claude adapter.
- **`adapter_agent` is unconstrained at the schema level:** the role schema accepts
  any non-empty `adapter_agent` string. Phase B pins the two *supported* adapters by
  golden test; it does **not** add an enum constraint (that would be a schema change,
  out of scope). Raw `--agent '<command>'` support remains deferred (Satine/Hermes
  profile line; PRD FR-12 / G5).
- **Evidence, not approval:** passing these gates proves the compiler shape only. It
  does not approve any real local execution, smoke, or Sachima integration.

## 9. Rollback strategy

The change is additive and isolated (one new plan doc, one new test, one tiny status
note, regenerated index/drift). Rollback is a clean `git revert`/branch drop with no
runtime, schema, or data implications, since no `src/` or behavioral code is touched.

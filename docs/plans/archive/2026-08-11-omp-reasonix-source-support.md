---
title: "OMP and Reasonix minimal source support"
status: archived
created_at: 2026-08-11
last_validated_at: 2026-08-11
archived_at: 2026-08-11
---
# OMP and Reasonix minimal source support

## Context and target

The controller authorized source, tests, and documentation for two installed
external ACP Agents: oh-my-pi 17.2.12 and Reasonix 1.23.0. This task extends the
existing operator-registry model; ARS does not acquire, copy, update, configure,
or activate either Agent.

Mandatory isolated real canaries ran before tracked implementation. They set the
narrow source boundary:

- OMP conforms to the standard model selector and exposes effort as `thinking`.
  Under the required `--approval-mode=always-ask`, ordinary write/edit sent no
  ACP permission request and failed locally without changing the workspace.
  Bash sent `kind=execute` and received a valid `allow_once` response, but OMP's
  second internal UI approval still denied execution. Delete/move could not be
  induced as distinct ACP permission kinds. No unproven edit/delete/move allow
  mapping is therefore in scope, and operator docs must state the 17.2.12 gap.
- Reasonix requires the evidenced static configuration sequence
  `tool_approval=ask`, model, effort before every Prompt on both new and loaded
  Sessions. Enforced bwrap executed in a canonical workspace; the equivalent
  symlink path failed during bind-mount destination setup before the command ran.
- `resolve_workspace_binding()` already resolves root and effective cwd before
  RunSpec sealing, and that same sealed cwd drives process launch and
  `session/new`/`session/load`. The required workspace change is explicit
  regression coverage, not another agent-specific canonicalizer.

This work derives from PRD R3/R4/R7/R12/R13, the design-layer profile and
registry contracts, and the controller's task-local authorization. It changes
no caller wire or Socket API.

## Checklist

- [x] Add `reasonix-agent-acp-compat-v1` revision 1 to the closed profile
  registry with static `tool_approval=ask` semantics only.
- [x] Prove RED then GREEN for exact permission-mode → model → effort order,
  exact readback, and identical new/load behavior.
- [x] Add hermetic operator-registry examples for OMP (`thinking`) and Reasonix
  (compat profile and Homebrew PATH overlay).
- [x] Preserve OMP's standard profile and the existing execute mapping; keep
  edit/delete/move denied because the real canary did not produce those ACP
  request kinds.
- [x] Add an explicit symlink-workspace test showing canonical root/cwd are
  sealed before launch.
- [x] Document OMP's observed fail-closed limitation, Reasonix's enforced
  sandbox/PATH requirement, and the source-versus-activation boundary.
- [x] Update governed design/product/roadmap surfaces and generated docs signals.
- [x] Run focused suites, the independent fresh-context blocker review,
  `make verify`, and `git diff --check`.
- [x] Delete credential-bearing and other detailed canary state, retaining only
  the sanitized ignored summary requested for authoring handoff.

## Acceptance

1. The default profile registry contains exactly the existing four profiles plus
   `reasonix-agent-acp-compat-v1`; existing profile behavior and identities do
   not move.
2. Reasonix selects only `ask`, never `auto` or `yolo`, and exact-readback
   configuration runs in deterministic permission-mode → model → effort order
   before Prompt after both `session/new` and real `session/load` paths.
3. Valid OMP and Reasonix registry examples parse and preserve the exact
   operator-owned command/argv/selector/env values. No request field gains
   command, argv, env, mode, or arbitrary selector control.
4. Permission tests retain once-scope discipline and fail closed for OMP's
   unproven edit/delete/move classes; read-only grants deny all write-family and
   execute requests before any hermetic side effect.
5. A symlink workspace is resolved once to the same canonical literal in the
   binding, sealed RunSpec, launch cwd, and Session cwd path.
6. Public docs make no OMP development-mode claim for 17.2.12, do not treat
   `mode=plan` as an ARS boundary, keep Reasonix bwrap enabled, and distinguish
   examples from live registry/service activation.
7. Focused tests, `make verify`, and `git diff --check` pass, and the diff is
   limited to task-owned source/tests/docs/generated governance artifacts.

## Files likely to change

- `src/agent_run_supervisor/native_acp/profile.py`
- `tests/native_acp/test_profile.py`
- `tests/native_acp/test_run_task.py`
- `tests/native_acp/test_spec.py`
- registry-example tests under `tests/native_acp/`
- `docs/product/prd.md`
- `docs/design/architecture.md`
- `docs/design/technical-solution.md`
- `docs/design/agent-registry.md`
- `docs/roadmap/features.md`
- `docs/roadmap/current-status.md`
- operator how-to pages under `website/docs/how-to/`
- generated `docs/INDEX.md` and `docs/lessons/_drift_report.md` when the project
  tools require them

## Verification gates

- Focused RED/GREEN pytest invocations for each behavior slice.
- Affected native-ACP/profile/registry/docs-site suites.
- `python tools/build_docs_index.py --write`
- `python tools/docs_drift_signal.py --write`
- `make verify`
- `git diff --check`
- final status/diff review proving no unrelated or forbidden side effect.

## Risks and controls

- **OMP 17.2.12 cannot complete the requested one-shot mutation path.** Keep the
  required fail-closed launch mode, add no inferred mapping, and publish the
  exact compatibility limitation instead of weakening approval policy.
- **Reasonix ambient approval can drift.** Freeze only the proven
  `tool_approval=ask` selector in a dedicated profile and exact-read it back on
  every Run.
- **Reasonix bwrap rejects symlink mount destinations.** Reuse the existing
  admission canonicalizer and pin the end-to-end sealed literals in tests.
- **Profile-set changes can accidentally move existing identity.** Assert the
  complete registry and every existing profile snapshot/hash unchanged.
- **Examples may be mistaken for activation.** State that paths are requested
  operator examples and that registry edits, service changes, release, and
  deployment require separate authorization.

## Rollback

Revert this task's source, tests, docs, and generated documentation artifacts.
No operator registry, service, installation, Agent home, Session store, global
configuration, release, or deployment state is changed by this source task, so
rollback has no external migration step.

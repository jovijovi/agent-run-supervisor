---
title: "Permissioned session prompt turns, goal-turn compilation, no-op fail-closed classification"
status: active
created_at: 2026-07-08
last_validated_at: 2026-07-08T03:00:00+0800
---
# Permissioned session prompt turns, goal-turn compilation, no-op fail-closed classification

## Context and target

- **Product position:** exec + persistent-session local lifecycles are closed (S1/H1/K1);
  the board was at "backlog — deeper hardening only".
- **Trigger:** an explicit operator directive (2026-07-08) authorized this phase after a
  supervised persistent-session incident: a `/goal …` prompt turn ran under the hardcoded
  `--deny-all` block, exited `0` with no agent output and no tool events, and was
  misclassified as `completed` (success). Caller projects (Hermes/Sachima) then treated a
  do-nothing turn as a delivered goal.
- **Reference:** architect solution doc
  `hermes/outputs/sachima/claude-architect/ars-claude-code-permission-goal-solution-20260708T015629Z.md`
  (external to this repo; summarized in this plan).
- **Roadmap trace:** F-POLICY-001 (session prompt compilers), F-STATUS-001 (status
  classification), F-SESSION-001 (turn artifacts), F-CLI-001 surface (`session send`).

## Problems being fixed

1. `policy.py::_session_prompt_flags` hardcodes `--deny-all` for every persistent
   prompt turn, ignoring the role's `permissions` — worker roles can never use tools.
2. There is no first-class, validated way to compile a goal-setting slash prompt
   (`/goal <text>`) for a persistent session turn.
3. `exit 0` + parseable stream + **no agent output and no tool events** classifies as
   `completed`; silent no-op turns are reported as success (not fail-closed).

## Scope

### In scope

- `policy.py`: session prompt turns compile the role-derived `--permission-policy`
  JSON (same compiler as exec); a role granting **no** permission kinds keeps the
  stricter fixture-proven `--deny-all` shape. `--non-interactive-permissions fail`
  stays in both shapes.
- `goal.py` (new): `compose_goal_prompt(goal_text)` — validated, injection-refusing
  composition of the `/goal` turn prompt; `is_slash_prompt(prompt)` detection.
- `exit_classifier.py`: new `no_op` status — `exit 0` without observed agent
  output/tool activity fails closed (`retryable=False`); protocol errors keep
  precedence; non-zero exits unchanged.
- `parser.py`: observed-effect helper over `ParseResult`.
- `session_runtime.py` / `runner.py`: wire observed-effect into classification;
  record `prompt_kind` in turn results.
- CLI: `session send --goal-file` (mutually exclusive with `--prompt-file`).
- Tests: regression tests for each problem; docs sync; CHANGELOG (unreleased).

### Out of scope (unchanged non-approvals)

- Sachima live behavior integration, real delivery, public ingress, Gateway
  lifecycle, production config, live/default-on behavior.
- Release/tag/PyPI publishing.
- Live acpx fixture capture for the permissioned prompt turn (operator follow-up;
  compilation is proven at the flag-family layer by
  `fixtures/acpx-0.12.0/permission-policy-deny-all-sentinel` and the S1a session
  prompt fixtures sharing the same global flag block).

## Acceptance (regression tests)

- [ ] Persistent prompt turn for a role granting permissions compiles
  `--permission-policy` (role-derived) and not an unconditional `--deny-all`.
- [ ] A role granting no permission kinds still compiles `--deny-all`
  (fail-closed default preserved).
- [ ] A `/goal`-style prompt turn with `exit 0` and no output/tool events is
  classified `no_op`, never `completed`.
- [ ] Goal prompt composition refuses empty/slash-leading/control-poisoned goal
  text and always passes the prompt as a single argv element (no shell).
- [ ] Exec runs with `exit 0` and no observed effect also classify `no_op`.

## Files likely to change

`src/agent_run_supervisor/{policy,goal,exit_classifier,parser,session_runtime,runner,result,cli,commands}.py`,
`tests/{test_policy,test_goal,test_exit_classifier,test_parser,test_session_runtime,test_runner_exec,test_cli_commands}.py`,
`docs/design/technical-solution.md`, `docs/roadmap/{current-status,features}.md`,
`CHANGELOG.md`, `README.md`, `README.zh-CN.md`.

## Verification gates

```bash
python3 -m pytest -q
python3 -m compileall -q src scripts tests
python tools/build_docs_index.py --write
python tools/docs_drift_signal.py --write
```

## Risks and rollback

- **Risk:** acpx `0.12.0` might reject `--permission-policy` specifically on
  `prompt -s` turns (only exec-path fixture-proven). Mitigation: flag block is
  the shared global family; full-deny roles keep the proven `--deny-all`; a live
  permissioned-prompt fixture capture is listed as operator follow-up before any
  release.
- **Risk:** `no_op` may surface for legitimately silent turns. Mitigation: it is
  deliberately fail-closed; callers must treat it as non-success and retry with
  an explicit prompt/permission fix.
- **Rollback:** revert the branch commits; no schema/artifact migration involved
  (result schema gains one enum value only).

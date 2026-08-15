---
title: "ARS permission boundary fixes"
status: active
created_at: 2026-08-15
last_validated_at: 2026-08-15
---
# ARS permission boundary fixes

## Context and target

Two permission defects are owned by ARS itself, not by any external AGENT or ACP adapter:

1. **A read-like permission request is allowed without path evidence.** `PermissionBridge` allows a
   `read`/`search` `session/request_permission` from the frozen grant alone. The grant answers *whether* the
   agent may read; it never answers *what*. `fs/read_text_file` already canonicalizes and contains its path,
   so the two mediated read paths disagree.
2. **A denial ARS issued is never checked against what happened next.** The existing completion backstop
   flags only a write-family tool that completed without the capability that could have allowed it. A tool
   call that reports `completed` after ARS refused *that same call* — read-like, or write-family under a
   matching grant that was refused for option scope — leaves the Run persisting as `completed`, as if the
   refusal had held.

This task fixes exactly those two, in ARS source, hermetic tests, and the directly affected documents.

## Non-goals

- No fix inside Claude, Codex, OpenCode, oh-my-pi, or any ACP adapter, and no attempt to make an adapter
  emit a permission request it does not emit today.
- No OS sandboxing, container, UID isolation, filesystem watcher, content scanner, or broad RBAC. Mediation
  stays cooperative and this plan makes no containment claim.
- No path inferred from `rawInput`, `_meta`, tool title, content, prompt text, model output, or any
  adapter-private payload.
- No detection of an unmediated filesystem fallback where no trustworthy tool-call correlation exists.
- No new terminal state, API version, Session lifecycle or quarantine rule, `result.json` top-level key,
  policy engine, retry change, or business-verdict change.
- No profile, registry, mediation-binding, launch-permission-policy, dependency, or package-version change,
  and no `ars-check` verdict reclassification.

## Contract

- A `read`/`search` permission request may receive `allow_once` only when the frozen grant includes `read`
  **and** every protocol-declared `locations[].path` is a non-empty absolute path whose canonical,
  symlink-resolved target is inside the bound workspace.
- Missing, empty, malformed, relative, mixed inside/outside, traversal-outside, and symlink-outside
  locations deny fail-closed, with a stable ARS-authored categorical reason that never carries the path.
- A tool call reaching `completed` after ARS denied that same `toolCallId` emits one `permission_violation`
  with `violation_class=completed_after_deny` and finalizes the Run through the existing
  `failed` / `PERMISSION_VIOLATION` / `retryable=false` path, Session still reusable.
- A denied call that reports `failed` is the healthy refusal shape and does not fail the Run.
- The pre-existing write-family backstop keeps every field and its behavior, and additionally labels itself
  `violation_class=missing_grant_capability`.
- `fs/read_text_file` behavior is unchanged, including workspace-root-relative paths and reading exactly the
  canonical path the decision validated.

## Approach

1. Clarify the R7 contract and its explicit non-guarantees in `docs/product/prd.md`,
   `docs/design/architecture.md`, and `docs/design/technical-solution.md` before production code.
2. RED unit tests for the complete read-like location matrix, and update existing positive read/search
   permission tests to carry a workspace-internal absolute location.
3. Implement one private validator in `PermissionBridge` returning a categorical reason or `None`, reusing
   `_resolve_workspace_path()` and `_inside_workspace()`; gate the existing read-like allow branch on it.
4. RED unit tests for deny → `completed` (violation), deny → `failed` (healthy), and a granted operation
   denied only for option scope that later completes.
5. Record denied `toolCallId`s with `setdefault` in `_deny_with_option`, and check that record before the
   missing-capability family in `observe_tool_update`. Derive a missing kind only from the ARS-recorded
   `permission:<kind>` operation, never from child free text.
6. Extend `tests/native_acp/fake_agent.py` only enough for `ask_permission` to carry wire-shaped `locations`
   and to emit an optional terminal `tool_call_update` under the same tool-call id; add RunTask L2 tests for
   the honored outside-location denial, the deny → `completed` failed Run, and the deny → `failed` control.
   The fixture performs no extra unmediated filesystem work: it proves the ACP contradiction, not
   containment.
7. Document the additive `permission_violation.violation_class` field in `docs/design/result-event-schema.md`.

## Files

**Source:** `src/agent_run_supervisor/native_acp/permissions.py`

**Tests:** `tests/native_acp/test_permissions.py`, `tests/native_acp/fake_agent.py`,
`tests/native_acp/test_run_task.py`, `tests/native_acp/test_projected_value_retention.py`

**Docs:** `docs/product/prd.md`, `docs/design/architecture.md`, `docs/design/technical-solution.md`,
`docs/design/result-event-schema.md`, this plan, `docs/roadmap/current-status.md`, plus the generated
`docs/INDEX.md` and `docs/lessons/_drift_report.md`

**Not expected to change:** `native_acp/client.py`, `profile.py`, `launch_permissions.py`, `spec.py`,
`arsd/protocol.py`, `skills/ars-check/**`, `pyproject.toml`, `uv.lock`, package version files, operator
registry/config/service files.

## Test and validation matrix

| Layer | Command | Required result |
|---|---|---|
| Location matrix | `uv run pytest tests/native_acp/test_permissions.py -q` | RED before code; GREEN after |
| Deny-completion correlation | same file, denied-completion selectors | RED before code; GREEN after |
| L2 ACP subprocess | `uv run pytest tests/native_acp/test_run_task.py -q` | PASS |
| Callback identity | `uv run pytest tests/native_acp/test_callback_identity_boundary.py -q` | PASS |
| Finalization / projection | `test_finalization_table.py`, `test_categorical_failure_projection.py` | PASS |
| Socket regression | `tests/arsd/test_real_socket_acceptance.py`, `tests/arsd/test_codex_socket_acceptance.py` | PASS |
| Syntax | `uv run python -m compileall -q src scripts tests` | exit 0 |
| Complete gate | `make verify` | exit 0 |

## Risks

1. **Adapters that omit `locations` are now denied for generic read/search prompts.** That is the intended
   default-deny, not a regression to hide. Such a lane stays unsupported until its adapter supplies
   trustworthy path evidence or uses the bounded `fs/read_text_file` callback.
2. **`locations` is cooperative evidence.** ARS can validate what the adapter declares; it cannot force the
   external process to operate on that path.
3. **Symlink resolution is point-in-time.** An external process can still race or ignore the decision;
   preventing that needs an OS enforcement layer, which is out of scope.
4. **Denied-completion detection needs a correlatable `toolCallId`.** It never infers a violation from event
   order, model text, or an unmediated filesystem fallback.
5. **A permission violation stays a failed Run with a reusable Session.** Quarantine is for continuity
   uncertainty, not a new security lifecycle.

## Closure

The task closes at one verified local candidate: focused gates and `make verify` green, plus one fresh
independent blocker review of the exact candidate. It authorizes no push, PR, merge, release, publication,
deployment, install, service action, production configuration write, migration, cutover, or live AGENT
canary. Closing it does not mean external Claude, Codex, OpenCode, or oh-my-pi mediation gaps are green;
those stay external and are reported as such.

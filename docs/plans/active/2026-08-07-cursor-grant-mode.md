---
title: "Grant-driven Cursor permission mode — ask for read-only grants, agent otherwise"
status: active
created_at: 2026-08-07
plan_kind: behaviour-closure-plan
base_branch: main
task_branch: fix/cursor-grant-mode
authorizes: "source, tests, and the minimum authority/docs surfaces this behaviour change makes false"
does_not_authorize: "commit, push, PR, merge, tag, release, publication, deployment, install, service or Gateway restart, production configuration/state change, real Cursor/model canary, or Sachima integration"
---

# Grant-driven Cursor permission mode — ask for read-only grants, agent otherwise

> An active plan is planning context, never approval. Implementation in this checkout is authorized by the
> current controller assignment; push, PR, merge, release, publication, deployment, service restart, and
> any real-agent canary are **not** — each remains a separate operator decision
> ([non-approvals](../../roadmap/non-approvals.md)).

## The gap being closed

Cursor's ACP `mode` selector defaults to a mode in which the agent can complete an edit **without ever
emitting `session/request_permission`**. ACP mediation decides before a side effect only when the agent
asks, so on a read-only Run the frozen-grant default-deny bridge never sees the write and the
post-completion violation detector reports it only after the file exists (PRD R7). The launch-permission
backend that would have closed this earlier is structurally unusable for Cursor: its environment key is the
agent's whole configuration root, and selecting it broke cross-Run `session/load` continuity
([archived repair plan](../archive/2026-08-03-cursor-cross-run-session-resume.md)).

The approved temporary mitigation: drive Cursor's own cooperative `mode` selector from the Run's frozen
grant, with exact readback proof, before any prompt.

**Honest scope.** `ask` is a cooperative agent-side mitigation, not an OS sandbox, not a strong
hostile-agent boundary, and not a permission/sandbox guarantee. ACP permission mediation and the
post-completion violation detector are unchanged and remain the enforcement line.

## Approved behaviour

- A Cursor Run whose frozen `grant_capabilities` are **exactly a subset of `{read, search}`** (`read`,
  `search`, `read+search`, and the degenerate empty grant) requires Cursor ACP mode `ask`.
- Every other valid Cursor grant requires mode `agent` — in particular grants containing `write`,
  `execute`, `terminal`, `delete`, or `move`. No further grant classes exist; the rule is the exact subset
  test and nothing else.
- Order before Prompt:
  `session/new|session/load` → discover options → set required mode → **exact mode readback** → set
  requested model → **exact model readback and exact mode re-proof after the model set** → persist
  effective evidence → Prompt.
- Missing mode selector, missing target value, set failure, wrong readback, or a model-set side effect
  that changes the mode fails **pre-Prompt** as `CONFIG_FIDELITY` with zero Prompt.
- The required mode is recomputed from the frozen grant and re-proven on **every** Run, including Session
  reuse Runs; the reuse path stays real `session/load` with no `session/new` fallback.
- Model-only fidelity is preserved: no effort selector, no effort RPC, effective effort stays the shared
  `N/A` sentinel.

## Design (smallest correct closure)

The exact-fidelity machine and driver already own the required sequencing for a profile-declared
permission mode (`config_fidelity.py` mode leg; mode re-proof after the model set on the model-only path;
`driver.set_config_exact` mode-before-model wire order). No new sequencing machinery is added.

1. **`native_acp/profile.py`** — the source profile owns the closed mode-selection policy:
   - one closed, source-owned **grant-driven permission-mode policy** (id
     `read-only-grant-ask-else-agent-v1`): required mode is `ask` iff the frozen grant capability set is a
     subset of `{read, search}`, else `agent`;
   - new optional profile field `permission_mode_policy_id`, valid only from the closed policy set and
     only together with `permission_mode_selector_id`; a profile declares **exactly one** of
     `required_permission_mode` (static, e.g. Claude) or `permission_mode_policy_id` (grant-driven);
   - one accessor `required_permission_mode_for(grant_capabilities)` answering the per-Run required mode
     for static and policy profiles alike, so no generic runtime path branches on an agent name;
   - `cursor-native-acp-v1` → **revision 3**: `permission_mode_selector_id="mode"`,
     `permission_mode_policy_id="read-only-grant-ask-else-agent-v1"`; the snapshot emits the new keys, so
     `profile_hash` moves. Standard and Claude snapshots stay byte-identical (pinned hashes).
2. **`native_acp/run_task.py`** — both `ConfigFidelityMachine` constructions (Run machine and rollback
   machine) pass `required_permission_mode=profile.required_permission_mode_for(sealed grant
   capabilities)`. The Run's immutable sealed grant supplies the per-Run input; recomputation per Run is
   structural because the machine is built per Run.
3. **No changes** to `config_fidelity.py`, `driver.py`, the wire protocol, the request schema, the
   registry schema, the launch/spec schemas, session records, or `session_epoch`. No dependency, shim,
   alias, or migration. Runtime stays stdlib-only.

Existing revision-2 Cursor Sessions fail reuse through the existing profile-binding mismatch refusal; no
compatibility or migration logic is added, by design.

## Checklist

- [x] RED: focused failing tests (`tests/native_acp/test_cursor_grant_mode.py` + stale-contract edits
      identified) run and fail for the expected reason before any production change
- [x] Production change in `profile.py` + `run_task.py` as designed above
- [x] Stale contract-pinning tests updated (`test_the_cursor_profile_deviates_only_in_effort_fidelity`)
- [x] GREEN: focused suite passes; then full `uv run pytest -q`
- [x] Docs: PRD R12 Cursor bullet, technical solution §1.2/§5 profile row, README/README.zh-CN Cursor
      lines, features tracker row, board position + `active_plan`, CHANGELOG — all carrying the
      cooperative-mitigation truth statement
- [x] `python tools/build_docs_index.py --write` + `python tools/docs_drift_signal.py --write`
- [x] Canonical `make verify` green
- [x] Working tree left uncommitted for controller review

## Acceptance (automated)

1. Cursor profile revision 3 declares selector + policy; only Cursor's `profile_hash` moves (standard and
   Claude pinned literals hold).
2. `read` → `ask`; `read+search` → `ask`; `search` → `ask`.
3. A representative writable grant (`read+search+write+execute`) → `agent`; a non-`{read,search}` control
   (`read+fetch`) → `agent`, so the exact subset rule cannot silently broaden.
4. Mode set happens before model set and before Prompt (wire order + config capture order).
5. Exact `ask`/`agent` readback required immediately after the mode set and re-proven after the model set.
6. Absent selector, unavailable target value, wrong post-set readback, rejected set, and mode drift after
   the model set each fail pre-Prompt as `CONFIG_FIDELITY` with zero `session/prompt` and no dispatch
   marker.
7. Both `session/new` and real `session/load` paths apply and re-prove the mode; a reuse Run recomputes
   from its own grant (same Session, writable grant → `agent`); no `session/new` on reuse.
8. Model-only behaviour intact: no effort RPC, `N/A` effective effort, `effort_selector_id: null` sealed.
9. Standard and Claude profile behaviour and hashes unchanged; existing permission-mediation and
   completion-backstop suites stay green.

## Risks and rollback

- **Risk:** moving Cursor `profile_hash` cuts existing revision-2 Cursor Session reuse. Accepted and
  deliberate: the existing binding-mismatch refusal answers those loads; no migration is added.
- **Risk:** over-reading the mitigation as a security boundary. Mitigated by the required truth statement
  in every touched public doc.
- **Rollback:** revert the working tree (nothing is committed); no runtime, data, or deployment state is
  touched by this work.

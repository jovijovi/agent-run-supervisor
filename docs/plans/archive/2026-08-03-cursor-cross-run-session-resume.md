---
title: "Cursor cross-Run Session resume — restore process-per-Run continuity"
status: archived
created_at: 2026-08-03
archived_at: 2026-08-04
plan_kind: defect-repair-plan
base_branch: main
task_branch: fix/cursor-cross-run-session-resume
authorizes: "source, tests, and the minimum authority/docs surfaces this behaviour change makes false"
does_not_authorize: "commit, push, PR, merge, tag, release, publication, deployment, service restart, cutover, canary, or any acpx work"
---

# Cursor cross-Run Session resume — restore process-per-Run continuity

> An active plan is planning context, never approval. It authorizes no Git/GitHub side effect, no release
> act, and no runtime change. Production `arsd` is rolled back to its previous release and is not touched
> by this work.

## The contract that broke

GOAL contract 3 and [PRD R4](../../product/prd.md): v1 is process-per-Run, and same-Session continuity uses
one external AGENT Session id plus a real `session/load`. Run 1 creates and uses a real external Cursor
Session; the Cursor process exits; Run 2 starts a **new** process, sends real `session/load` for the stored
external id, proves exact model-only fidelity, and prompts in the same conversation.

Deployment acceptance of the released `0.6.2` source showed Cursor Run 1 succeeding (`session/new`, prompt,
exact model-only readback) and Cursor Run 2 reaching a real `session/load` and then failing before the
prompt with a stable `CONFIG_FIDELITY`. Claude, Codex, and OpenCode passed their comparable paths.

## Root cause

`cursor-native-acp-v1` selects the launch-permission policy `deny-write-and-shell-v1`
(`native_acp/profile.py`). That policy's source-owned environment key is `CURSOR_CONFIG_DIR`
(`native_acp/launch_permissions.py`), and materialization projects it as
`<supervisor-root>/native-runs/<run_id>/launch-permissions` — a directory created per Run and removed by
`discard()` once the child is proven reaped (`native_acp/run_task.py`).

`CURSOR_CONFIG_DIR` is Cursor CLI's **whole configuration root**, not a permission-file-only override. So
ARS does not inject a permission document beside the agent's own configuration; it **relocates the agent's
entire configuration and Session state into per-Run scratch and then deletes it**. Run 2 launches a new
process pointed at a different, empty root, so nothing Run 1's process wrote is reachable. The real
`session/load` then has no configured Session to report options for, `record_initial_options(None)` fails
closed, and the Run ends as a pre-dispatch `CONFIG_FIDELITY` — exactly the observed symptom.

`launch_permissions.discard()` already concedes the mechanism in its own docstring: *"The child treats the
directory as its own config home and may have written state into it."*

This also contradicts the [PRD R8](../../product/prd.md) / GOAL filesystem boundary: ARS neither manages nor
relocates AGENT configuration, cache, or Session state.

**Why the suites missed it.** Every Run-path test for the Cursor shape (`test_model_only_fidelity.py`) and
for the launch-permission slice (`test_launch_permissions.py`) builds a **test-local** profile, and the one
two-Run reuse test runs on a profile that selects **no** policy. No test ever ran two Runs through a
policy-selecting profile against an agent whose Session state lives in the projected configuration root.

## Decision

There is **no supported, bounded permission-only injection surface** for Cursor that ARS may use:

- the environment override relocates the whole configuration root, which is the defect;
- the only permission-scoped surface Cursor offers is project configuration inside the caller-bound
  workspace, and PRD R8 / GOAL forbid ARS to create anything there;
- an ARS-owned persistent Cursor home, config, or Session store is a standing non-approval.

So `cursor-native-acp-v1` **stops selecting a launch-permission policy**, and Cursor runs under the
operator's and AGENT's own configuration exactly like every other registered agent. Removing the selection
revises the profile's frozen permission-mediation semantics, so its `revision` bumps and its `profile_hash`
moves with it, per [PRD R12](../../product/prd.md).

The generic `launch_permissions` module is profile-neutral and its contracts are intact, so it stays. After
this change no registered profile selects a policy, layer 5 is never projected, and every launch-permission
seam keeps its behaviour for any future profile that has evidence to select one.

This narrows defense in depth deliberately. The ACP `PermissionBridge`, the frozen-grant default-deny
mediation, the post-completion violation detector, and the mandatory per-agent denied-action canary are
unchanged and remain the enforcement path — as [PRD R7](../../product/prd.md) already states, mediation is
cooperative and this was never a sandbox.

## Steps

1. **RED** — a committed deterministic regression at the RunTask boundary: two Runs through the **registered**
   `cursor-native-acp-v1` against a fake ACP agent whose Session state lives under its configuration home.
   Run 1 creates and prompts; Run 2 must load and prompt in the same conversation.
2. **GREEN** — drop `launch_permission_policy_id` from `cursor-native-acp-v1` and bump its `revision`.
3. Update only the tests whose subject is the Cursor profile's own shape, and the board statement that
   becomes false.
4. Focused RED/GREEN commands, then `make verify`.

## Adjacent finding, deliberately not repaired here

An operator who wants to point an agent at a **non-default** configuration directory still cannot, for any
profile. `agent_registry` reserves a launch-permission key **per selection**, so an agents file declaring
`CURSOR_CONFIG_DIR` in `env_passthrough`/`env_overlay` now parses; but `launch_permissions`
`projection_matches_policy` judges the same name against the **global** reserved set, so sealing that Run's
launch snapshot refuses and every Run of that agent fails at admission.

The two layers disagree. This is **pre-existing and independent of this repair** — `standard-native-acp-v1`,
whose selection state is untouched here, shows the identical parse-accepts / seal-refuses split — and it does
not block the contract above, because the ordinary path is the agent's own default home under `$HOME`, which
the base allowlist already projects unchanged.

It is left alone on purpose: closing it means relaxing the name-first rule that
`test_a_relabelled_reserved_name_is_refused_*` pins, which is a separate decision about an existing defense
and not something this defect repair should take.

## Out of scope

Release, publication, deployment, restart, cutover, the real-agent canary, acpx work, any other profile,
any new ARS-owned state, any change to the generic launch-permission mechanism itself, and the adjacent
finding above.

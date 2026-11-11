---
title: "OpenCode minimal compatibility: one bounded same-value effort re-proof"
status: active
created_at: 2026-11-10
last_validated_at: 2026-11-11
---
# OpenCode minimal compatibility: one bounded same-value effort re-proof

## Context and target

A real OpenCode `session/new` Run under `standard-native-acp-v1` failed before the prompt. The
post-set-model readback showed effort `default`. The client then set the advertised value `max`, and
the readback still showed `default`. The Run ended `CONFIG_FIDELITY` with zero prompt, and the Session
was quarantined. An upstream issue, reproduced by its maintainers, describes a stale selection echo
overwriting a newer local selection. The retained exchange did not capture the echo order, so it is
consistent with that mechanism but does not observe it directly. No upgrade causality is claimed.

**Target.** An operator may opt one OpenCode registry entry into a new source profile,
`opencode-acp-compat-v1`, revision 1. Under it, before the first prompt, one specific stale effort
readback may earn **at most one** identical re-set of the same selector and value. The final
configuration proof stays exact-or-zero.

This derives from PRD R3/R12, technical solution §5, and the user's explicit approval of a minimal
OpenCode compatibility implementation. It does not redefine product goals.

**Non-goals.**

- No change to the standard profile or to any other profile; the five existing `profile_hash` values
  stay byte-identical.
- No retry on the model, mode, rollback, prompt, Run, or Session paths.
- No sleep, value mapping, coercion, `default`→`max` substitution, exception-text parsing,
  `agentInfo` gate, or caller-side model rewrite.
- No event, wire, schema, registry-field, dependency, or version change.
- No live registry, service, or real-model work, and no change to the OpenCode how-to.

## Design

**Seam.** The seam is a new source profile. A registry-entry knob would carry ACP semantics in operator
data. A standard-profile revision would move the identity of other agents that share it. A name gate
on `agentInfo` is forbidden. The id is new and maps from no other id.

**`profile.py`**

- A frozen term `effort_readback_reproof: bool = False`.
- Construction refuses a non-`bool` value, and refuses `True` on a profile whose fidelity mode is not
  `separate-selectors`.
- `snapshot()` emits the term only when it is `True`.
- `OPENCODE_ACP_COMPAT_V1` equals the standard contract plus this term. `DEFAULT_REGISTRY` becomes a
  closed set of six.

**`config_fidelity.py`**

- A constructor flag `effort_reproof_allowed=False` and a new phase `effort_reproof_planned`.
- `record_post_effort_options` records the `post_effort` snapshot first. It then consumes the
  allowance and moves to the re-proof phase **only if all** of these hold:
  - the allowance is unused;
  - the model reads back exactly the requested model;
  - the effort option is present and a select;
  - the requested effort is among its choices;
  - its current value is a string that differs from the requested effort (a null value is malformed, not
    stale);
  - a declared permission mode reads back exactly.
- Every other case raises today's strict errors with today's messages.
- `effort_reproof_plan()` returns the same effort selector id.
- `record_post_effort_reproof_options()` records `post_effort_reproof` and applies the identical strict
  checks through one shared helper, with no allowance.
- `require_ready()` is unchanged, so a pending re-proof still fails closed.

**`driver.py`.** After the effort readback, if a re-proof is pending, the driver sends exactly one more
`set_config_option` with the planned selector and the requested effort. It then records that readback.
A transport, RPC, or malformed response raises inside the existing call or option parsing before any
eligibility check.

**`run_task.py`.** Only the Run's forward machine receives the profile's term. The rollback machine
stays strict, so the extra set is bounded to one per Run and counts separately from strict rollback
operations. Markers, the `CONFIG_FIDELITY` mapping, rollback, and quarantine are unchanged.

**Evidence.** `effective.json.discovery_snapshots` keeps the mismatching `post_effort` snapshot followed
by `post_effort_reproof`, on success and on failure. No event type is added.

**Identity.** Selecting the profile later is an ordinary profile-identity change. Existing
standard-profile OpenCode Sessions are refused for reuse, and continuing work needs a new Session with
caller-owned context handoff. Nothing maps old Sessions forward, bumps an epoch, or clears a
quarantine.

**Residual risk.** A stale echo that arrives after a proven readback is OpenCode-side and outside what
ARS can observe; it is not claimed.

## Checklist

- [x] WP1 — docs before behavior: PRD R3/R12, architecture §3, technical solution §0/§1.2/§5, this
  plan, board, feature row, both READMEs, and the public profiles page; regenerate INDEX and the drift
  report.
- [x] WP2 — behavioral tests first, with the observed feature-missing failure retained.
- [x] WP3 — the four source edits, the fake-agent step key, and the three support edits below.
- [x] WP4a — row to `Implemented` after green source and focused suites.
- [ ] WP4b — full gate, then independent code review, then a local task-branch commit by the
  controller. Not merged, released, installed, selected, or live-accepted.

## Acceptance

All rows use a real driver or `RunTask` plus the in-repo fake ACP agent.

| Contract item | Deterministic proof |
|---|---|
| Ordinary path | New and load: one `effort` set; snapshots `initial`, `post_model`, `post_effort`; prompt dispatched |
| Stale default recovered | New and load: exactly two identical effort sets; `post_effort` mismatch kept plus exact `post_effort_reproof`; `config-proven`; completed |
| Exhausted allowance | Stale twice: exactly two forward sets, `CONFIG_FIDELITY`, zero prompt. Create: quarantine `SWITCH_ROLLBACK_UNPROVEN`. Load: strict rollback, either proven and reusable, or failed and quarantined |
| No retry authority | Wrong model, missing or non-select effort, value no longer advertised, malformed or null response, RPC error on the first set: one set, then `CONFIG_FIDELITY`. RPC error on the second set: failed, zero prompt |
| Opt-out and exclusion | The same stale script under the standard, Codex, or Reasonix profile: one set and `CONFIG_FIDELITY`, as before; five existing hashes byte-identical, term false on all of them |
| Machine unit | Eligibility matrix; `require_ready` refuses while pending; allowance used once; disabled path keeps byte-identical messages |
| No silent Session change | No `session/new` on load; quarantine never cleared; epoch untouched |

## Files

- Source: `src/agent_run_supervisor/native_acp/{profile,config_fidelity,driver,run_task}.py`.
- Tests:
  - `tests/native_acp/fake_agent.py`: one per-config-id, per-set step key;
  - `test_profile.py`, `test_driver_config_fidelity.py`;
  - the focused module `test_opencode_effort_reproof.py`.
- Docs: the WP1 list above.
- Support, temp allocation only: `scripts/smoke_installed_artifact.sh` creates its venv under
  `${TMPDIR:-/tmp}`. `tests/arsd/test_client_daemon.py` and `tests/arsd/test_fake_agent_socket_acceptance.py`
  stop forcing `/tmp`. Prefixes and cleanup stay unchanged.
- Expected unchanged: `GOAL.md`, `non-approvals.md`, the agent-registry contract, the result/event
  schema, examples, the real OpenCode smoke, and `CHANGELOG.md`.

## Verification gates and execution prerequisites

The full gate is `make verify` or a full `./scripts/verify_local.sh` from the task worktree. Focused
suites are partial evidence only.

- **Isolated environment.** Use a fresh task worktree and its own `.venv`. Never use another checkout's
  `.venv`, whose editable install points at a different `src`. Assert that `agent_run_supervisor`
  imports from `<worktree>/src`.
- **Short `TMPDIR` for every step.** Every setup, test, build, and smoke step exports a short
  run-specific `TMPDIR`, kept at or under 56 bytes for the pytest basetemp shape. Set
  `PYTEST_ADDOPTS=--basetemp=$TMPDIR/p`, because the default basetemp makes a real socket path exceed
  the AF_UNIX limit.
- **Offline flags.** Set `UV_PROJECT_ENVIRONMENT=<worktree>/.venv`, `UV_OFFLINE=1`, `PIP_NO_INDEX=1`,
  `PYTHONDONTWRITEBYTECODE=1`, and `PIP_FIND_LINKS`/`UV_FIND_LINKS` pointing at an approved local
  wheelhouse that supplies the isolated build backend.
- **Clean variables.** `ARS_NATIVE_SMOKE` stays unset. Strip inherited `GIT_DIR`, `GIT_WORK_TREE`,
  `GIT_INDEX_FILE`, and `GIT_COMMON_DIR`.
- **Dependencies.** Locked dependencies come from an already-populated cache. Fetching them is a
  separate controller decision, never a silent download.
- **Before the full gate:**
  - run both admitted `tests/arsd` files and `tests/arsd/test_reconcile.py -k before_bind`;
  - build `dist/` before the wheel smoke;
  - keep read-only before/after listings of `/tmp` names that tests or the smoke create, to show that
    no new task allocation lands there.

## Risks and rollback

**Risks.** The re-proof could mask a real misconfiguration. It does not, because the second readback is
judged by the unchanged strict rules and anything else refuses before any re-set. The profile could be
read as live admission. It is not: a fresh zero-prompt discovery and a denied-action canary under the
profile stay mandatory before merge, default-registry admission, or live selection.

**Rollback.** Before merge, drop the task branch. After merge, revert the commit. A registry that still
names the profile then fails at startup with `ENTRY_UNKNOWN_PROFILE`, so the operator first returns the
entry to `standard-native-acp-v1`, which is another continuity cut. No data migration is needed.

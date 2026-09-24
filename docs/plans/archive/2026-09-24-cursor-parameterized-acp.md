---
title: "Parameterized Cursor ACP"
status: archived
created_at: 2026-09-24
last_validated_at: 2026-09-24
archived_at: 2026-09-24
---
# Parameterized Cursor ACP

## Context and target

The operator approved integrating Cursor CLI's parameterized ACP model picker into the Native ACP path
(PRD R3/R12). Without negotiation the agent advertises one variants-catalog model selector, which revision 3
of `cursor-native-acp-v1` configured under model-only fidelity. Cited zero-prompt ACP evidence on one
installed Cursor CLI version shows that `clientCapabilities._meta.parameterizedModelPicker = true` on
`initialize` makes the agent advertise a base-model selector plus independent, model-dependent parameter
selectors, each settable and exactly readable. That evidence is context, not ARS acceptance.

Target: `cursor-native-acp-v1` revision 4 negotiates the picker through a frozen profile term and declares a
third fidelity mode, `parameterized`, while the caller request keeps its existing shape — one model string
and the `N/A` effort.

## Design decisions

- The model string is the whole configuration, `base[id=value,...]`, parsed by one strict grammar at machine
  construction; a malformed string fails before the first ACP frame. The string is never sent to the agent.
- ARS sets `base`, consumes the complete post-set-model set, and requires the advertised non-model, non-mode
  option ids to equal the requested parameter ids; an omitted advertised parameter refuses rather than running
  at an unproven agent default. Each parameter is planned from the latest complete set, set, and read back;
  a final whole-configuration readback (base, every parameter, required mode) gates the prompt.
- Effective model is re-composed from the readback; effective effort is `N/A`. Request, Spec, launch, and
  Session record schemas are unchanged; the Session record keeps the proven string, so rollback replays the
  same sequence for the previous string.
- No source constant names a parameter id, value, or domain. A parameter may not name the model selector or
  the profile-owned permission-mode selector.
- The grant-driven `mode` term, permission mediation, dispatch markers, and process/Session lifecycle are
  unchanged. `agents doctor` stops at `initialize` and now carries no configuration request at all.
- Revision 4 moves only Cursor's `profile_hash`; revision-3 Sessions, whose recorded variants literal this
  revision cannot restore, are refused by the ordinary identity check. No compatibility path is added.

## Checklist

- [x] `config_fidelity.py`: `parameterized` mode, literal grammar, per-parameter legs, whole-configuration
  readback.
- [x] `driver.py`: base-model set plus one set per parameter; optional machine for the `initialize`-only probe.
- [x] `profile.py`: frozen `client_capabilities_meta`; revision 4; no effort selector outside
  separate-selector fidelity.
- [x] `run_task.py`: merge the profile's frozen `_meta` into grant-derived `clientCapabilities`.
- [x] Hermetic fake agent: negotiation-dependent catalog and `clientCapabilities` capture.
- [x] Focused regressions: grammar, machine, profile identity, `session/new`, real `session/load` restore,
  cross-Run parameter and base-model switch, partial failure with exact rollback, unprovable rollback, and
  the create-path window; existing Cursor suites migrated to revision 4.
- [x] Authority, operator, README, and website documentation synchronized.
- [x] Independent fresh-context review.
- [x] Merge, then archive this plan.

## Acceptance

- Deterministic: the focused suites and the full `make verify` gate pass.
- Not claimed by this plan: resident Sachima Native ARS Delegation acceptance, live Cursor Session reuse,
  deployment, or activation. Each needs its own authorization and evidence.

## Files likely to change

`src/agent_run_supervisor/native_acp/{config_fidelity,driver,profile,run_task}.py`,
`src/agent_run_supervisor/commands.py`, `tests/native_acp/` (fake agent, new parameterized suite, migrated
Cursor suites and hash pins), PRD, design, roadmap, READMEs, and website pages.

## Verification gates

`make verify` (canonical); focused iteration with
`pytest tests/native_acp/test_cursor_parameterized_fidelity.py` and the migrated Cursor suites.

## Risks

- A Cursor upgrade that renames or adds a model parameter makes requests fail closed until they name the new
  set; that is the exactness this mode exists for, not a regression.
- The negotiated extension is version-bound agent behavior, not a standardized ACP flag; an agent that ignores
  it advertises only variants, and the request then fails before any parameter set.

## Rollback

Revert the merge. Sessions created under revision 4 would then be refused by the reverted revision-3 identity,
exactly as revision-3 Sessions are refused here; nothing migrates or rewrites stored records.

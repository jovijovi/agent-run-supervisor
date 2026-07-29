---
title: "Profile-scoped Runtime Binding activation"
status: active
created_at: 2026-07-29
---
# Profile-scoped Runtime Binding activation

Authority: `GOAL.md` contracts 9–10 · PRD R13 · architecture §3.1–§3.3 ·
technical-solution §1.2/§2/§8 · [board](../../roadmap/current-status.md) ·
[features](../../roadmap/features.md) F-RUNTIME-BINDING-001.

This plan repairs a defect inside the already-merged R13 source closure. It defines no product
goal, opens no stage, and implies no operator or runtime approval.

## Context

`arsd` takes exactly one `--binding-root`, the registry is closed at three profiles
(`opencode-native-acp` r3, `codex-acp-1.1.7` r3, `claude-agent-acp-0.63.0` r4), and every
registered profile refuses admission until a generation has been promoted **for that profile**.

The merged layout cannot express that. `BindingReader.resolve_active(profile)` reads one
root-level `active.json`; each manifest declares exactly one `contract_identity`, and `_project`
compares it with the requested profile. One configured root therefore activates exactly one
profile: promoting OpenCode makes Codex and Claude fail `CONTRACT_IDENTITY_MISMATCH`, and vice
versa. The generation namespace has the same defect in latent form — `generations/<id>/` is shared,
so two profiles authored at the same id silently overwrite one another's manifest.

The requirement contradicted is R13's own admission rule read together with the daemon's single
required root: a Binding root is deployment storage for *the deployment*, and the deployment runs
three profiles.

## Target and invariant

**Invariant.** The Binding root's active-selection namespace is profile-scoped. Every Binding read
is anchored at `profiles/<profile_id>/`, derived from the already-resolved closed `AgentProfile`,
and both the pointer and the manifest must declare that same profile identity as explicit machine
fields. One root holds N independent, independently promotable active selections — one per
registered profile — and no read, write, or promotion inside one profile's subtree can observe or
alter another's.

```text
<binding_root>/                                  # operator/root-owned, outside the repository
└── profiles/<profile_id>/
    ├── active.json                              # regular file, atomically replaced
    └── generations/<generation_id>/
        └── manifest.json                        # immutable once written
```

The pointer gains one machine field:

```json
{"schema_version":1,"profile_id":"<id>","generation_id":"<gen>","manifest_sha256":"<sha256>"}
```

What does **not** change: read-once (one pointer read + one generation read per Run), the manifest
schema, contract-acceptance rules, canonical-JSON/size/ownership/mode/ancestor/`O_NOFOLLOW`/dirfd
validation, the epoch gate, the spawn-boundary attestation, `AgentRunRequest`/`AgentRunSpec` field
sets, the `arsd` v1 wire, the result/event grammar, reconcile semantics, the `ManagedProcess` API,
`launch.json`'s shape, and old-Run readability.

## v0.5.1-layout disposition

The single-pointer layout is **intentionally rejected**, with no compatibility read:

1. it is structurally incapable of holding three concurrent activations, so honouring it would
   re-create the defect for two of the three profiles — silently, as a contract mismatch;
2. its pointer carries no `profile_id`, so it cannot prove which profile it activates;
3. ARS never writes, repairs, or migrates operator storage, so an in-place migration is not ARS's
   to perform;
4. nothing is deployed: no Binding root has been created and no generation promoted, so there is
   no installed base to migrate.

A configured root that still carries a root-level `active.json` is refused with the stable rule
`LEGACY_BINDING_LAYOUT`; a root with no subtree for the profile is refused with
`PROFILE_BINDING_ABSENT`. Both refusals name the required operator action: move each generation
under `profiles/<profile_id>/generations/` and re-run `runtime-binding promote` per profile.
Re-promotion is an operator decision this plan neither performs nor approves.

## Checklist

- [x] RED: multi-profile regression proving one root cannot serve three profiles today
      (4 failed; the reader legs on `CONTRACT_IDENTITY_MISMATCH`).
- [x] `runtime_binding.py`: `PROFILES_DIRNAME`, safe profile component, path helpers,
      profile-anchored `read_active`/`read_generation`/`write_active_pointer`/`read_active_pointer`,
      `POINTER_PROFILE_MISMATCH`, `LEGACY_BINDING_LAYOUT`, `PROFILE_BINDING_ABSENT`,
      `PROFILE_ID_UNSAFE`.
- [x] `commands.py`: pass the resolved profile to the pointer read/write; command surface unchanged.
- [x] Test fixtures migrated to the profile-scoped layout; multi-profile suites added.
- [x] Docs: PRD R13, architecture §3.1–§3.2/§10, technical-solution, READMEs (EN + zh-CN).
- [x] Release prep 0.5.2: version sync/lock, CHANGELOG, board/features.
- [x] Gates: focused → adjacent → `make verify` green; docs index + drift regenerated.
- [ ] Merge (Hermes-owned), then archive this plan and update the board.

## Acceptance

1. One root concurrently resolves an active generation for all three registered profiles.
2. Admission selects Binding material only from the resolved closed profile; no request field can
   select root, generation, version, digest, or path.
3. `resolve_active` still reads exactly one pointer and one generation; spawn, finalization, and
   reconciliation read zero.
4. Promoting or rolling back one profile leaves every other profile's pointer byte-identical.
5. A pointer or generation belonging to profile A is refused for profile B on an explicit machine
   field, not only by path separation.
6. Operator surface stays `validate | promote | rollback | inspect-run`; no `--force`, no privilege
   escalation, no daemon write, no caller override.
7. A v0.5.1-shaped root fails closed with the stable documented rule.
8. Compatibility surfaces and old Run readability unchanged.

## Files likely to change

- `src/agent_run_supervisor/native_acp/runtime_binding.py`
- `src/agent_run_supervisor/commands.py`
- `tests/native_acp/binding_fixtures.py`, `tests/native_acp/test_runtime_binding.py`,
  `tests/test_runtime_binding_cli.py`, `tests/arsd/test_binding_admission.py`,
  `tests/arsd/test_codex_socket_acceptance.py`
- `docs/product/prd.md`, `docs/design/architecture.md`, `docs/design/technical-solution.md`,
  `docs/roadmap/current-status.md`, `docs/roadmap/features.md`, `docs/INDEX.md`,
  `docs/lessons/_drift_report.md`
- `README.md`, `README.zh-CN.md`, `CHANGELOG.md`, `pyproject.toml`,
  `src/agent_run_supervisor/__init__.py`, `uv.lock`

## Verification gates

Focused RED/GREEN → `tests/native_acp/test_runtime_binding.py`,
`tests/test_runtime_binding_cli.py`, `tests/arsd/test_binding_admission.py`,
`tests/arsd/`, `tests/native_acp/` → `make verify` (includes docs index/drift, static safety scan,
version sync, package build, wheel smoke, roadmap governance).

## Risks

- **Silent layout drift.** Mitigated by fail-closed refusals with named rules and by the pointer's
  own `profile_id` field, so a moved pointer is refused rather than reinterpreted.
- **Profile id as a path component.** Registry ids are code constants, but the component is still
  validated (`PROFILE_ID_UNSAFE`) so a future id cannot introduce traversal.
- **Operator re-promotion cost.** Real, and accepted: no generation has been promoted anywhere, so
  the cost is documentation, not migration.

## Rollback

Source-only revert of this branch. No Binding root, artifact, service unit, or running process is
touched by the change, so reverting restores the prior source behaviour exactly. Any root an
operator lays out under the new shape simply stays unread by the reverted single-pointer reader.

## Boundary

Source, tests, live docs, and 0.5.2 release *preparation* only. No Binding root creation, generation
authoring/promotion/rollback against a real deployment, artifact materialization, profile
re-acceptance, permission canary, service-unit edit, restart, rollout, tag, GitHub Release, PyPI
upload, or provider run is performed or approved here.

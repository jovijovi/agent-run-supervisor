---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-29
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Living vNext dashboard. It records current scope and gates, not implementation history.

```text
base_branch: main
active_plan: ../plans/active/2026-07-29-standard-native-acp-v1.md
```

## Current position

- ARS vNext remains the local supervision path: `trusted caller → arsd UDS → ars-core/Native ACP → registered external AGENT`.
- Stage 0/1 Native ACP core is closed on `main`.
- Stage 2 `arsd` is closed and the previously accepted local user service is enabled. That local status does not imply release, Sachima, Gateway, public ingress, or further deployment approval.
- Registered profiles on `main` are `opencode-native-acp` r3, `codex-acp-1.1.7` r3, and `claude-agent-acp-0.63.0` r4; all speak ACP Protocol v1. All three remain registered, runnable, and hash-identical, and `opencode-native-acp` remains the authoritative OpenCode path.
- Source pins the optional Python ACP SDK as `agent-client-protocol==0.11.1`. It is distinct from the Claude adapter's bundled JavaScript ACP SDK.
- The Runtime Binding framework, the wrapped-adapter package closure, and the profile-scoped active-selection namespace are complete and released on the v0.5.2 source line.
- **Board correction (2026-07-29).** The earlier "operator activation remains open" line was stale. Production runs v0.5.2 with three profile-scoped Bindings promoted under a live root, so the artifact closure and the per-profile generations listed below as open gates are done. What remains open is recorded under Open gates.
- In review on the active plan: a versioned `standard-native-acp-v1` conformance profile instantiated per operator-owned Agent Registration, plus the agent-anchored Binding subtree it descends into. Additive and merge-safe by construction — no hash, layout, digest, or daemon-surface movement for the three live profiles.
- Sachima integration is Parked and separately approvable. Remaining acpx product, runtime, and compatibility content is planned for separately authorized removal; bounded differential/comparison tests and fixtures remain only as reference.

## Phase board

| Area | State | Current scope |
|---|---|---|
| Stage 0/1 — Native ACP core | Closed on `main` | [closed phase archive](archive/phases/vnext-stage01-native-acp.md) |
| Stage 2 — `arsd` local production ingress | Closed; previously accepted local user service enabled | [closed phase archive](archive/phases/vnext-stage2-arsd-production-ingress.md) |
| Runtime Binding and wrapped-adapter package closure | Source closure complete on `main`; operator activation open | [phase archive](archive/phases/vnext-runtime-binding-source-closure.md) · [feature tracker](features.md) |
| Profile-scoped Binding activation | Released on v0.5.2 | [archived plan](../plans/archive/2026-07-29-multi-profile-runtime-binding.md) |
| Standard Native ACP (v1) + Agent Registration | In review on branch | [active plan](../plans/active/2026-07-29-standard-native-acp-v1.md) |
| Sachima `ArsdBackend` integration | Parked | separately approvable later integration |
| acpx product/runtime/compatibility removal | Planned | separately authorized source work; bounded comparison fixtures remain |

## Open gates / next operator decisions

The artifact closure is materialized and one generation per profile is promoted under the live root; those gates are closed. These remain separate operator decisions and none is approved here:

- Run the Claude R12 permission canary.
- For the standard-native profile: install an agent artifact under a root-owned immutable prefix, run zero-prompt ACP `initialize` discovery, record the code-owned CLI probe as a separate fact, run the mandatory denied-action mediation canary, author `registration.json` plus a generation, then `validate --agent` and `promote --agent`.
- Cut any caller over to `standard-native-acp-v1`.
- Design, implement, and approve a retirement mechanism, and only then retire any registered profile. No such mechanism exists in source and none is introduced by the active plan; these are two separate decisions.

If the Binding-root naming convention ties root name to deployed commit, the next deploy implies a new root and re-promotion of all profiles. Merging the active plan alone does not: the running daemon keeps the `--binding-root` it was started with.

No source closure implies Binding promotion, deployment, service restart, rollout, publication, or a provider run.

## Boundaries

See [`non-approvals.md`](non-approvals.md) for the full current boundary. This board records status only;
it does not authorize operational changes, release/publication, integrations, or acpx removal work.

## Cold history

- [Runtime Binding source/package-closure archive](archive/phases/vnext-runtime-binding-source-closure.md)
- [Closed phase archive](archive/README.md)
- [Archived implementation plans](../plans/archive/README.md)
- [Profile-scoped Binding activation plan](../plans/archive/2026-07-29-multi-profile-runtime-binding.md)
- [Former authority snapshot](../archive/pre-vnext-reset-2026-07-21/README.md)

## Verification

See [`verification.md`](verification.md) and [`docs/AI_FLOW.md`](../AI_FLOW.md).

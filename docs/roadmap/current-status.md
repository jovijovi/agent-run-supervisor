---
title: "ARS vNext Roadmap Current Status"
status: active
created_at: 2026-07-21
last_validated_at: 2026-07-28
supersedes: "docs/archive/pre-vnext-reset-2026-07-21/current-status.md"
---
# ARS vNext Roadmap Current Status

> Living vNext board, not delivery history. Cold history is excluded from default agent context.

```text
base_branch: main
active_plan: none
```

## Authority chain

```text
GOAL → vNext PRD → vNext architecture → vNext technical solution
     → features + this board → active implementation plan → code
```

The pre-reset mixed authority, v0.1.7 feature/phase ledger, completed plans, and old delivery
instructions are archived. They may be read only for audit, compatibility, or user-cited disputes
and cannot direct new work.

## Snapshot

- **Product target:** one local supervision plane: `trusted caller → arsd UDS → ars-core/Native ACP → registered external AGENT`.
- **Released baseline:** v0.1.7 acpx behavior remains compatibility-only; it is not the new-development architecture.
- **Stage 0/1:** the Native ACP core is closed and implemented on `main` (`native_acp/` plus `managed_process`).
- **Stage 2 — `arsd` (closed 2026-07-23):** A1–A5 are closed. `arsd` is production/default-on enabled as a local user service for trusted local callers under the closed A2 caller policy. This is an enabled local supervision service only — not a release/publication, Sachima, Gateway/IM, or public-ingress approval. Closure detail lives in the Stage 2 phase archive and the archived execution plan.
- **Registered closed profiles:** the registry holds three — `opencode-native-acp` r3 (`direct_acp`), Codex ACP 1.1.7 r2 (F-NATIVE-ADAPTER-CODEX-001, `wrapped_acp`), and Claude Agent ACP 0.63.0 r3 (F-NATIVE-ADAPTER-CLAUDE-001, `wrapped_acp`). The operator-held local socket-path acceptance that passed for all three was taken against the pre-Runtime-Binding revisions; the refactor bumped every revision, so re-acceptance against the new revisions plus a promoted Binding is an open operator action.
- **Claude Source Contract 0.63.0 (source only):** the registered Claude contract moved from `claude-agent-acp-0.61.0` r2 to `claude-agent-acp-0.63.0` r3 with no compatibility alias — the retired ID is an unknown profile at admission. Zero-prompt ACP discovery against the installed 0.63.0 adapter observed `agentInfo` `@agentclientprotocol/claude-agent-acp` 0.63.0, protocol 1, `loadSession` advertised, and byte-identical `mode`/`model`/`effort` domains and readbacks, so only the version-bound literals moved: entry path, `acp_agent_version`, revision, and a new `adapter_contract_hash`. The frozen `adapter_entry_sha256` is deliberately unchanged because `dist/index.js` is byte-identical across the two adapter versions — the entry path is the only artifact discriminator here, which is the known wrapped-adapter package-closure gap, recorded and not closed. This is a source change only: the R12 permission canary, re-acceptance at r3, and a Binding generation promoted at the new `adapter_contract_hash` are all open operator actions, and a generation accepted under the retired contract fails closed.
- **What registration is not:** source registration plus operator-held local acceptance is not PyPI publication, not Sachima integration, not public ingress, and not a reusable approval for the next change. Each profile revision, publication, and deployment step stays a separate operator decision.
- **Claude adapter facts worth pinning:** the ACP model readback literal is `opus[1m]` — `claude-opus-5[1m]` is the direct Claude Code author selector, a different namespace, and is not registered. The source line pins the optional native Python ACP SDK at `agent-client-protocol` `0.11.1`; the adapter bundles its own JavaScript ACP SDK `1.3.0`. The two are independent artifacts, and an already-installed deployment keeps whatever pin it was installed with until an operator changes it — source is not deployment.
- **Runtime invariant:** production `arsd` runs on CPython 3.12.3 — the interpreter that carried A4/A5 acceptance and whose build provides the pidfd APIs the crash-containment harness requires. Standalone Python 3.11.15 lacks those APIs and is not an equivalent runtime.
- **Released vs source:** source package metadata is 0.5.0 and covers the Native ACP core, `arsd`, the three registered closed profiles, and the Runtime Binding source framework. The live GitHub Releases and PyPI listings are authoritative for which versions are published; this board records no published version number. Release authorization covers packaging and publication only; it carries no deployment, Sachima integration, or default-on production activation approval.
- **Runtime Binding — source framework merged:** the three-layer split — code-closed `AdapterContract`, operator-owned Runtime Binding, per-Run sealed launch/runtime provenance — is accepted authority (PRD R13) and is implemented on `main`: `native_acp/runtime_binding.py` as the sole Binding reader, the source-frozen `AdapterContract` on all three profiles, the single per-Run Binding read in `arsd/admission.py`, sealed launch provenance plus package-closure/ownership/TOCTOU attestation, the `session_compatibility_epoch` reuse gate, the `runtime-binding validate|promote|rollback|inspect-run` surface, and an `arsd` that requires an explicit `--binding-root` both in daemon mode and when rendering a service unit. Merged source approves no Binding root, promotion, artifact installation, restart, rollout, publication, or real-provider evidence.
- **Open source tail — wrapped-adapter package closure:** the frozen wrapped artifact identity still covers only the interpreter and the adapter *entry* path/digest, not the complete adapter package tree the artifact-closure rule requires, so `wrapped_acp` is not fully closed and the Runtime Binding layer is not finished. Closing it is the next separately authorized source phase (F-RUNTIME-BINDING-002); it is not started and nothing here approves it.
- **OpenCode profile drift (closed by the Runtime Binding refactor):** the operator's zero-prompt ACP discovery against the installed executable produced `agentInfo` OpenCode/1.18.5, protocol 1, `loadSession` advertised, selectors `model`/`effort`, and the model-dependent effort domain low|high|max after the exact model was set to `kimi-for-coding/k3`. `opencode-native-acp` r3 registers exactly that evidence with no compatibility alias; `deepseek/deepseek-v4-pro` is not carried over because this discovery does not prove its effort domain. `agentInfo` version and the CLI `--version` remain separate facts and no code path asserts them equal.
- **Operator preconditions (open):** every registered profile now requires a promoted Binding generation. The daemon flag exists in source — `arsd` refuses to start or to render a unit without it — but no Binding root exists, no generation is authored or promoted, and no installed unit carries a configured root, so admission stays fail-closed for every registered profile. Preparing an immutable artifact root the `arsd`/AGENT UID cannot rewrite is a root-owned installation change; on this host the installed CLIs are service-UID-owned and are refused by design. A merged source change is never a deployment: an already-running service keeps the release it was installed from until the operator separately decides otherwise.
- **Later integration:** Sachima has no `ArsdBackend`/UDS integration and remains parked; ARS production acceptance is closed, and the integration still requires its own separate approval.

## Phase board

| Phase | State | Active authority | Authorization / exit |
|---|---|---|---|
| vNext authority reset | Documentation complete | GOAL/PRD/design/roadmap + cold archive | no implementation authority |
| Stage 0/1 — Native ACP core | Closed; implemented on `main` | [phase archive](archive/phases/vnext-stage01-native-acp.md) | production claims rest on the closed Stage 2 acceptance |
| Stage 2 — `arsd` production ingress (A1–A5) | Closed; production/default-on enabled 2026-07-23 | [phase archive](archive/phases/vnext-stage2-arsd-production-ingress.md) · [archived plan](../plans/archive/2026-07-22-vnext-stage2-arsd-production-ingress.md) | enabled local supervision service under the closed A2 caller policy; closure grants no publication authority — publication proceeds only under the separate release authorization, which grants no external-integration or further-deployment approval |
| Codex official-adapter admission | Closed; registered on `main` | [archived plan](../plans/archive/2026-07-25-codex-official-adapter-admission.md) · F-NATIVE-ADAPTER-CODEX-001 | closed profile plus operator-held local acceptance only; closure grants no publication authority — publication proceeds only under the separate release authorization, which grants no deployment, enablement, or external-integration approval |
| Claude official-adapter admission (B3/B4/B5) | Closed; registered on `main` | [archived plan](../plans/archive/2026-07-25-claude-official-adapter-b3-b5-closure.md) · F-NATIVE-ADAPTER-CLAUDE-001 | closed profile plus operator-held local acceptance only; closure grants no publication authority — publication proceeds only under the separate release authorization, which grants no deployment, enablement, or external-integration approval |
| Runtime Binding | Source framework merged on `main`; package closure and operator activation open | [archived plan](../plans/archive/2026-07-26-runtime-binding-refactor.md) · F-RUNTIME-BINDING-001 · F-RUNTIME-BINDING-002 | source/tests/docs only; complete wrapped-adapter package closure is open source work, and artifact-root preparation, promotion, re-acceptance, restart, rollout, publication, and real-provider evidence each remain separate operator decisions, with every registered profile needing a promoted Binding before it can run |
| Sachima integration | Parked | boundary only | after ARS production acceptance and separate approval |

## Gates

| Gate | State | Fact |
|---|---|---|
| G9/G10/G11 | Closed by A4 | real S1–S5 socket-path acceptance: cgroup crash containment, real denied-action canary, robustness, and re-proven real credential/model usability; sanitized evidence operator-held |
| G12 caller UID/ownership policy | Closed by A2 | recorded operator policy decision; exact mapping values controller-only, delivered to the daemon only as `--caller-mapping` arguments in the mode-0600 user unit |
| A5 live enablement | Closed 2026-07-23 | enabled+active user unit after exact-main runtime install, production canary, and independent blocker review PASS; sanitized closure evidence operator-held (phase archive) |
| Registered-profile local acceptance | Passed for all three profiles | operator-held local socket-path E2E: exact model/effort readback, mediated permissions, no workspace mutation; not publication, deployment, or a reusable approval |
| OpenCode ACP discovery evidence | Closed by the operator's zero-prompt discovery | real non-prompt ACP `initialize` exchange plus the executable's CLI `--version` and digest, recorded as independent facts; `opencode-native-acp` r3 freezes exactly the observed identity, selectors, and model-dependent effort domain |
| Runtime Binding root prepared and promoted | Open | `arsd` requires an explicit `--binding-root` in source, but no Binding root exists and no generation is authored or promoted; every registered profile refuses admission fail-closed until an operator prepares an immutable, non-service-writable artifact root and promotes a generation |
| Wrapped-adapter package closure | Open | the frozen wrapped artifact identity covers the interpreter and adapter entry only, not the adapter package tree; closing it is separately authorized source work and no operator gate depends on it yet |
| Re-acceptance at the bumped profile revisions | Open | the Runtime Binding refactor bumped all three profile revisions, so the prior operator-held local socket-path acceptance no longer covers the registered rows; the Claude 0.63.0 source contract bumps that profile again to r3, which under PRD R12 additionally owes a fresh permission canary before re-acceptance |

G12 closure is a recorded operator decision; the repository intentionally records no production
mapping value. The A1 source/default-closed foundation still permits only the explicitly scoped
source behavior. Optional DLP enhancements remain future work.

## Cold history

- Former mixed authority snapshot: [`docs/archive/pre-vnext-reset-2026-07-21/`](../archive/pre-vnext-reset-2026-07-21/README.md)
- Closed plans: [`docs/plans/archive/`](../plans/archive/README.md)
- Closed phases/tails: [`docs/roadmap/archive/`](archive/README.md)

## Explicit non-approvals

See [`non-approvals.md`](non-approvals.md). This board authorizes nothing by itself: the A1–A5
and official-adapter closures are recorded operator decisions carried by their archives. A
follow-on release or publication, Sachima/Gateway integration, and public ingress remain
separately unapproved, and no closure here transfers to the next change. The merged Runtime Binding
source framework authorizes source, tests, and docs only — never a Binding root, promotion, artifact
installation, service restart, rollout, publication, or real-provider run — and it neither closes the
wrapped-adapter package-closure tail nor re-accepts any profile revision.

## Verification

See [`verification.md`](verification.md) and [`docs/AI_FLOW.md`](../AI_FLOW.md).

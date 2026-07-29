---
title: "Runtime Binding Source and Wrapped-Adapter Package-Closure"
status: archived
created_at: 2026-07-29
archived_at: 2026-07-29
last_validated_at: 2026-07-29
---
# Runtime Binding Source and Wrapped-Adapter Package-Closure

Status: **Closed in main source.**

> Cold phase archive for audit only. Current scope, gates, and authorization live in the
> [living board](../../current-status.md) and [feature tracker](../../features.md).

## Archived source closure

The Runtime Binding framework and the wrapped-adapter package closure are complete in `main` source.
This archive records that completed source scope; it neither supplies current authority nor authorizes
operator activity.

| Surface | Archived source result |
|---|---|
| Authority split | Code-closed `AgentProfile`/`AdapterContract`, operator-owned Runtime Binding, and per-Run sealed `ResolvedLaunchSpec`/provenance remain separate. |
| Binding read and sealing | `runtime_binding.py` is the Binding reader; admission resolves it once per Run, projects only contract-accepted slots, and seals the resolved launch identity before spawn. |
| Session continuity | `session_compatibility_epoch` is persisted and mismatched or missing epochs fail closed before `session/load`. |
| Daemon and operator surface | `arsd` requires an explicit Binding root; the source command surface is `runtime-binding validate`, `promote`, `rollback`, and `inspect-run`. |
| Wrapped adapter closure | Each `wrapped_acp` contract freezes the adapter npm install root, whole-tree digest, contained entry, and interpreter argv prefix; attestation rechecks the sealed closure at the spawn boundary. |

The registered source contracts at closure are `opencode-native-acp` r3,
`codex-acp-1.1.7` r3, and `claude-agent-acp-0.63.0` r4. All speak ACP Protocol v1. Source pins the
optional Python SDK as `agent-client-protocol==0.11.1`; that pin is separate from the Claude adapter's
bundled JavaScript SDK.

## Evidence boundary

The archived completion is a source/package-closure result proven by the contract, admission,
attestation, epoch, and hermetic refusal suites. It does not record a real Binding root, artifact
materialization, provider run, or deployment acceptance.

## Operator work left outside this closure

The source closure left these actions separately approvable: immutable root-owned artifact
materialization; Binding generation authoring and promotion; re-acceptance of all current profile
revisions; and the Claude R12 permission canary. It did not imply a service restart, rollout,
publication, deployment, or Sachima integration. Current decision status belongs only to the
[living board](../../current-status.md) and [`non-approvals.md`](../../non-approvals.md).

## Related records

- [Living roadmap status](../../current-status.md)
- [Feature and capability tracker](../../features.md)
- [Archived Runtime Binding implementation plan](../../../plans/archive/2026-07-26-runtime-binding-refactor.md)

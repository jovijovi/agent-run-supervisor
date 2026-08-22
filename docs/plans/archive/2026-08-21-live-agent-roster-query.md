---
title: "ARS live AGENT roster query"
status: archived
created_at: 2026-08-21
last_validated_at: 2026-08-22
archived_at: 2026-08-22
implementation_authorized: true
production_authorized: false
---

# ARS Live AGENT Roster Query Implementation Plan

> **Source implementation of Stages 1–4 is merged on `main`; nothing beyond source is implied.**
> `implementation_authorized: true` is a statement of fact about source, tests, and documentation only.
> Release, publication, package installation, daemon restart, downstream activation, and production changes
> each remain separate approvals, and `production_authorized` stays `false`.

**Goal:** Add a minimal, read-only Socket API v3 operation that exposes the canonical AGENT IDs actually loaded in the running `arsd` daemon.

**Task Contract:**
- **Objective:** Allow a trusted local caller to query the immutable in-memory `AgentRegistrySnapshot` used by the current daemon process.
- **Done criteria:** The API returns stable, duplicate-free canonical `agent_id` values from the daemon snapshot; existing operations remain compatible; public documentation and tests describe the exact contract; a released and activated ARS build can be consumed by downstream callers such as Sachima.
- **Hard constraints:** ARS must not perform natural-language routing, role/priority selection, mention mapping, execution-preset selection, or AGENT health inference. Do not read `agents.toml` during a request, add hot reload, expose adapter commands/configuration/secrets, or claim that registration means execution eligibility.

**Architecture:** Introduce one dedicated read-only Socket API operation, `agent_list`. `ArsdHandlers` receives the same immutable `AgentRegistrySnapshot` created during daemon startup, and the handler returns only its stable canonical IDs. The existing `server_info` discovery response advertises the new operation but does not embed roster data.

**Tech stack:** Python 3.11–3.14, ARS Socket API v3, Unix-domain socket client/daemon, pytest, uv, MkDocs documentation.

---

## 1. Product boundary

### ARS owns

- The runtime fact: which canonical AGENT IDs the current daemon loaded at startup.
- A small read-only protocol operation exposing that fact.
- Deterministic response shape, stable ordering, protocol validation, client facade, tests, and public API documentation.

### ARS does not own

- Natural-language understanding or clarification.
- Role/function selection, routing recommendations, priority, aliases, mentions, or `auto_selectable` policy.
- Downstream execution presets such as workspace, model, effort, limits, or permissions.
- AGENT health/readiness claims. Presence in the roster means registered in this daemon snapshot only; `submit` remains the execution admission boundary.
- Registry hot reload. A changed startup configuration becomes effective only after restarting `arsd`.

### Downstream responsibility

A caller such as Sachima must fail closed when roster lookup fails, a requested canonical `agent_id` is absent, or its own execution preset is missing or invalid. It must not read ARS startup configuration and present that as daemon runtime truth.

## 2. Socket API contract

### Request

```json
{
  "api_version": 3,
  "op": "agent_list",
  "request_id": "req-1",
  "payload": {}
}
```

Protocol behavior:

- `payload` may be omitted if that is consistent with the existing generic request envelope, or supplied as `{}`.
- If supplied, `payload` is a closed object: any field is rejected with the existing invalid-request error contract.
- API versions other than the supported v3 behavior continue to follow the existing version-rejection contract.

### Successful result

```json
{
  "agent_ids": ["claude", "codex", "cursor", "oh-my-pi", "opencode"]
}
```

Result invariants:

- `agent_ids` contains only canonical IDs from the daemon's immutable registry snapshot.
- Values are unique and returned in deterministic stable order.
- An empty valid registry returns `{"agent_ids": []}`.
- The result contains no command, argv, executable path, environment variable, profile, credential, adapter parameter, capability, model, role, or health information.
- The client facade returns the complete result object rather than silently changing it into a bare list.

### Discovery and compatibility

- `server_info.operations` adds the new operation to the existing operation set.
- `server_info` keeps its existing top-level fields, API version, and limits; it must not gain a roster field.
- Existing operation semantics remain unchanged.
- Old clients can continue using a new daemon.
- A new client calling an old or rolled-back daemon receives the existing unknown-operation error and must fail closed.
- The Socket API remains v3 unless implementation-time protocol review identifies a genuine incompatible change. Adding this independent read-only operation is expected to be additive.

## 3. Preserve / add / exclude matrix

| Area | Action | Required result |
|---|---|---|
| Existing seven Socket operations | Preserve | No semantic or response regressions |
| `server_info` | Reshape minimally | Advertise one additional operation; no roster payload |
| `AgentRegistrySnapshot` | Preserve/reuse | Remains the canonical immutable runtime source |
| Handler construction | Reshape | Require and retain the startup snapshot; reject missing/wrong type at construction |
| New roster handler | Add | Read only `snapshot.ids()` or equivalent canonical accessor |
| Client facade | Add | Expose complete typed/result object for roster query |
| Registry file loading | Preserve | Startup-only; no request-time re-read and no hot reload |
| Routing/role/priority/mention logic | Exclude | Must not be introduced into ARS |
| Execution presets and health inference | Exclude | Remain downstream concerns |
| Storage schema/migrations | Exclude | This feature is stateless and read-only |

## 4. Implementation stages

### Stage 0: Admission and authority reconciliation

**Objective:** Activate the work without displacing unrelated active plans or creating contradictory repository authority.

1. Fresh-check `GOAL.md`, product PRD, technical solution, roadmap/current status, `docs/AI_FLOW.md`, and this board-linked active plan.
2. Confirm this remains the board's sole active plan and that completed plans remain archived.
3. Reconfirm the public operation name `agent_list`, response key `agent_ids`, and additive Socket API v3 compatibility decision against current protocol authority.
4. Obtain explicit implementation authorization before source, test, branch, commit, release, installation, or service changes.
5. When authorized, create a dedicated implementation branch such as `feat/arsd-agent-roster-query`; do not reuse unrelated branches or worktrees.

### Stage 1: Update authority documents first

**Objective:** Record the approved capability and boundaries before implementation.

Likely files to inspect and update:

- `docs/product/prd.md`
- `docs/design/technical-solution.md`
- `docs/roadmap/features.md`
- `docs/roadmap/current-status.md`
- the approved tracked plan under the repository's plan-governance location

Required documentation decisions:

- The feature is a read-only runtime registry query.
- Registration is not health, authorization, capability, role suitability, or execution eligibility.
- The source is the immutable daemon snapshot, not a disk file.
- Registry changes require daemon restart; hot reload is explicitly out of scope.
- Capability status moves through the repository's real status sequence and is marked done only after merge to the authoritative branch.

### Stage 2: Write RED protocol and handler tests

**Objective:** Freeze the externally visible contract before implementation.

Likely tests:

- `tests/arsd/test_protocol.py`
- `tests/arsd/test_api_version_matrix.py`
- `tests/arsd/test_handlers_registry.py`
- `tests/arsd/test_client_daemon.py`

Required failing scenarios:

1. Socket API v3 accepts the new operation; unsupported versions retain existing rejection behavior.
2. Omitted payload and `{}` are accepted according to the generic envelope contract.
3. Any roster-operation payload field is rejected.
4. Missing or wrong-type registry snapshot fails during handler construction, not during request handling.
5. Registry IDs are unique and stable-sorted.
6. An empty snapshot returns `{"agent_ids": []}`.
7. Default and injected run-task-factory startup paths receive the exact same snapshot object created at startup.
8. Repeated roster calls do not reload or reparse registry configuration.
9. The wire response contains only `agent_ids`; sentinel sensitive registry fields never appear in the encoded frame.
10. A maximum legal registry fixture, parsed through the real registry parser and encoded through the real frame encoder, stays within the existing legal response-frame contract.
11. A real Unix-domain socket client/daemon round trip returns the complete result object.
12. The existing operations keep their request/response behavior.

Do not use an arbitrary hand-estimated byte threshold as an acceptance gate. Use repository-defined parser/frame limits and a maximum legal fixture.

### Stage 3: Implement protocol and daemon wiring

**Objective:** Add the smallest code necessary to satisfy the RED tests.

Likely files:

- `src/agent_run_supervisor/arsd/protocol.py`
- `src/agent_run_supervisor/arsd/handlers.py`
- `src/agent_run_supervisor/arsd/__main__.py`

Required behavior:

1. Add the operation to the canonical operation declaration.
2. Validate its closed payload using the existing protocol-validation approach.
3. Require a valid `AgentRegistrySnapshot` when constructing handlers.
4. Pass the exact startup snapshot through both the normal daemon path and any injected `run_task_factory` path.
5. Implement the handler by reading the in-memory snapshot's canonical IDs only.
6. Keep the operation read-only and free of storage, registry-file, adapter, or subprocess access.
7. Keep `server_info` unchanged except for advertising the added operation.

No new public abstraction is needed beyond what the existing protocol/handler structure requires.

### Stage 4: Add client facade and public documentation

**Objective:** Make the operation usable without leaking internal registry details or documenting false protocol behavior.

Likely files:

- `src/agent_run_supervisor/arsd/client.py`
- `website/docs/reference/socket-api.md`
- `website/docs/reference/api/client.md`
- `website/docs/reference/api/protocol.md`
- `website/docs/reference/error-codes.md`
- `README.md`
- `README.zh-CN.md`

Client behavior:

```python
result = client.agent_list()
assert result == {"agent_ids": ["claude", "codex"]}
```

Documentation requirements:

- Show the exact request and response envelope.
- State that request `payload` may be omitted when permitted by the generic envelope; when present it must satisfy the operation's closed schema.
- Explain runtime snapshot semantics and restart requirements.
- Explain `UNKNOWN_OP` behavior against older daemons.
- State that IDs are registration facts only.
- Update both English and Chinese README capability summaries proportionally.
- Update error-code documentation only where the operation adds a real, user-observable case; do not manufacture feature-specific error codes.

### Stage 5: Verification and candidate review

**Objective:** Produce a locally accepted candidate with exact evidence.

Focused verification:

```bash
uv run pytest -q \
  tests/arsd/test_protocol.py \
  tests/arsd/test_api_version_matrix.py \
  tests/arsd/test_handlers_registry.py \
  tests/arsd/test_client_daemon.py
```

Repository verification:

```bash
make verify
```

Additional acceptance checks:

- `git diff --check` passes.
- Public docs render and internal links pass the repository's existing documentation checks.
- A sensitive-string/secret scan of the exact candidate is clean.
- Existing Socket operations retain their contract tests.
- The exact candidate receives an independent fresh-session blocker review.
- No release, package install, daemon restart, or downstream activation is implied by local candidate acceptance.

### Stage 6: Delivery, release, and activation boundaries

**Objective:** Keep repository delivery separate from package/runtime changes.

1. Commit and open an ARS PR only under explicit delivery authorization.
2. Merge only after exact-head CI and the project's approval rules pass.
3. Version bump, tag, GitHub Release, and package-registry publication require separate release authorization.
4. Installing the new package and restarting `arsd` require separate operational authorization.
5. After activation, verify via read-only Socket API calls:
   - `server_info.operations` contains the final roster operation name;
   - the roster response matches the daemon-loaded canonical IDs;
   - no sensitive registry details appear.
6. Only after ARS release/activation may Sachima upgrade its dependency and implement its live-roster admission check.
7. Sachima semantic delegation, `/delegate` removal, multi-AGENT presets, deployment, and Gateway restart remain separate work.

## 5. Acceptance scenarios

### AS-1: Stable registered IDs

Given a daemon started with canonical IDs `opencode`, `claude`, and `codex`, the roster response is deterministic and duplicate-free, for example `{"agent_ids":["claude","codex","opencode"]}`.

### AS-2: Empty registry

A valid empty registry returns success with `{"agent_ids":[]}` rather than an internal error or fabricated default AGENT.

### AS-3: Disk/runtime drift

If the startup registry file changes while the daemon keeps running, repeated roster queries continue returning the startup snapshot. After an authorized restart, the result reflects the newly loaded snapshot.

### AS-4: Invalid request

A payload containing an unknown field is rejected using the existing invalid-request contract. The daemon does not ignore the field or return a roster.

### AS-5: Sensitive-boundary protection

Registry entries containing command paths, environment configuration, adapter arguments, or sentinel secret-like values produce a response containing only canonical IDs.

### AS-6: Version skew

- Old client against new daemon: existing methods remain usable.
- New client calling the roster operation against an old daemon: receives the existing unknown-operation response and fails closed.

### AS-7: Downstream interpretation

A downstream caller may use membership as one admission fact but cannot interpret roster presence as health, permission, preset availability, or task suitability.

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Treating the startup file as live truth | Query only the daemon's in-memory immutable snapshot |
| API becomes a routing service | Return only canonical IDs; explicitly exclude role/priority/recommendation fields |
| Sensitive adapter configuration leaks | Result has one allow-listed key; add encoded-frame sentinel tests |
| Handler silently runs without a registry | Enforce snapshot validity at construction time |
| Hidden hot reload semantics emerge | Test repeated calls and disk mutation; document restart requirement |
| Additive operation breaks discovery assertions | Update canonical operation-set tests and preserve all existing operation semantics |
| Response exceeds frame contract | Encode a maximum legal parsed registry fixture with the real encoder |
| Downstream races between query and submit | Document roster as advisory admission evidence; `submit` remains authoritative |
| Plan displaces unrelated active work | Keep this file as the board's sole active plan and archive completed plans |

## 7. Explicit non-goals

- No Socket API v4 solely for this additive operation.
- No registry create/update/delete API.
- No hot reload, file watch, generation counter, push subscription, or change event.
- No AGENT metadata, display names, aliases, commands, environment, capabilities, health, roles, priorities, mentions, recommendations, or execution presets.
- No Sachima/Hermes/Feishu-specific fields or behavior in ARS.
- No storage migration.
- No automatic release, package installation, service restart, Sachima dependency upgrade, Gateway deployment, or real multi-AGENT execution.

## 8. Definition-of-Ready checks

1. Reconfirm `agent_list` / `agent_ids` and additive Socket API v3 compatibility against current protocol authority.
2. Reconfirm that this file remains the living board's sole active plan before implementation begins.
3. Confirm the release version only after implementation scope and compatibility are accepted.

## 9. Rollback

The feature is read-only and requires no data migration. Code rollback removes the client method and daemon operation, returning the daemon to its previous operation set. Existing clients remain unaffected; a newer client calling a rolled-back daemon receives the ordinary unknown-operation error and must fail closed. Any package rollback or daemon restart is a separately authorized operational action.

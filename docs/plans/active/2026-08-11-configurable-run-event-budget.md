---
title: "Operator-configurable per-Run event-ledger budget"
status: active
created_at: 2026-08-11
last_validated_at: 2026-08-11
---
# Operator-configurable per-Run event-ledger budget

## Context and target

`RunLimits.__post_init__` currently refuses a Run whose `max_event_bytes * max_events` exceeds the source
constant `LIMIT_EVENT_BUDGET_BYTES` (1 GiB). That cross-product ceiling is a deployment sizing policy — how
much normalized event evidence one Run of this daemon may ever be admitted to write — yet it is welded into
the per-Run data model beside the field-shape rules, so an operator cannot change it and a dev/direct
construction path cannot be told which policy it is under.

This source-only task makes the ceiling operator-configurable with a default of exactly 4 GiB
(`4 * 1024 * 1024 * 1024` bytes), and separates the two kinds of validation that are conflated today:

- **Field shape and individual hard limits** stay in `RunLimits`: `max_event_bytes` in `[256, 1_048_576]`,
  `max_events` in `[1, 1_000_000]`, and every existing timeout/stderr bound.
- **The cross-product admission ceiling** moves to one explicit immutable policy object,
  `EventBudgetPolicy`, injected into admission. Its single question is whether
  `max_event_bytes * max_events` fits the ceiling configured for the daemon admitting this Run.

The ceiling is a **theoretical per-Run persistent event-ledger ceiling**: the worst case of one Run's
`events.jsonl`. It is not preallocated memory, not the total disk quota of a Run directory, and not a
daemon-global aggregate across concurrent Runs.

The daemon startup value is the admission-policy ceiling for **every** Run that daemon accepts. Each Run
still seals its own `max_event_bytes`/`max_events`; the daemon value never becomes a per-Run field, never
reaches the wire, and never enters sealed material.

Nothing changes about what a Run may request field-by-field, so no wire, request, Spec, or launch schema
version moves: only admission policy changes.

The task closes at one verified local candidate. It authorizes no push, PR, merge, release, publication,
deployment, install, service action, production configuration write, migration, cutover, or real-provider
canary.

## Design

- `native_acp/spec.py`
  - `DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES = 4 * 1024 * 1024 * 1024` replaces `LIMIT_EVENT_BUDGET_BYTES`. It is
    a **default**, not a hard limit, so it is not named `LIMIT_*` beside the structural ceilings it is no
    longer a peer of.
  - `EventBudgetPolicy` — a frozen dataclass carrying `max_run_event_budget_bytes`, fail-closed at
    construction (integer, positive, `bool` refused), exposing one method that judges a `RunLimits`.
  - `DEFAULT_EVENT_BUDGET_POLICY = EventBudgetPolicy()` — one immutable module constant, deliberately not a
    mutable module global: nothing rebinds or mutates it, and a differently configured daemon injects its own
    instance instead of writing to a shared one.
  - `RunLimits` keeps every field-shape check and takes the policy through a `dataclasses.InitVar` seam
    defaulting to `None` → `DEFAULT_EVENT_BUDGET_POLICY`. An `InitVar` is not a field, so the wire key set,
    `dataclasses.fields`, `asdict`, equality, and the sealed Spec projection are untouched.
- `arsd/protocol.py` — `parse_submit` takes the policy (keyword-only, defaulted) and threads it into
  `RunLimits` for direct/dev callers, and `admit_event_budget` applies a policy to already-parsed limits.
  Either way the refusal is the same `INVALID_REQUEST`.
- `arsd/handlers.py` — `ArsdHandlers` takes `event_budget_policy` (defaulted, type-checked at construction)
  and reports `max_run_event_budget_bytes` in `server_info.limits`. On the daemon path the policy is applied
  through `admit_event_budget` to new work only; see the durable-first bullet below.
- `arsd/__main__.py` — `--max-run-event-budget-bytes` (integer bytes, default 4 GiB) and a
  `serve_daemon(max_run_event_budget_bytes=...)` parameter that builds the policy fail-closed, before the
  registry parse, the instance lease, and any state write.
- `arsd/admission.py` — the effective ceiling is **durable Run evidence**, not only a live `server_info`
  fact. `build_submission_artifact` takes the policy object (required, never defaulted) and records
  `max_run_event_budget_bytes` in the write-once `submission.json`; the field joins `SUBMISSION_FIELDS`, and
  the strict validator shared by admission and reconciliation rebuilds the field through
  `EventBudgetPolicy` itself, so a missing, mistyped, boolean, non-positive, or past-the-structural-maximum
  value is CORRUPT rather than partially trustworthy, with no second copy of the producer's bounds. Once the
  digest identifies the request, `submission_admits_limits` additionally proves the stored policy admits
  that request's sealed limits, so a well-formed but impossible record cannot authorize a duplicate.
  `SUBMISSION_SCHEMA_VERSION` moves 3 → 4 because that record's material genuinely changed; no tolerant
  reader for the older shape is added. Admission policy is deliberately **not** digest input, so a
  reconfigured daemon cannot turn a legitimate duplicate into an `IDEMPOTENCY_CONFLICT`, duplicate
  resolution keeps reading the original durable record, and no later daemon re-stamps it.
- `arsd/handlers.py` + `arsd/protocol.py` — **admission policy governs new work only.** `_submit` parses for
  identity under `spec.STRUCTURAL_EVENT_BUDGET_POLICY` (the policy that adds nothing to the per-field
  structural limits, so nothing is widened), `_submit_locked` resolves the durable record first, and
  `protocol.admit_event_budget` applies the daemon's real ceiling only when there is no acceptance to
  resolve — at the one place a Run is born, before Session tracking, reservation, or `create_run`.
- `arsd/handlers.py` + `arsd/admission.py` — duplicate resolution consumes
  `admission.classify_submission`, the same strict validation reconciliation uses, and `SubmissionState`
  now carries the exact validated payload so nothing re-opens the file under a weaker rule.
- `native_acp/spec.py` — the configured ceiling is itself bounded by
  `STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES` (`LIMIT_MAX_EVENT_BYTES_MAX * LIMIT_MAX_EVENTS_MAX`), derived once
  from the limits it bounds rather than respelled as a literal or a digit-count rule. A larger ceiling
  admits nothing extra — no Run can request more — yet would still be serialized into every Run's durable
  evidence and every `server_info` frame, so it is refused at construction, before registry parsing, the
  lease, any state write, `server_info`, or evidence serialization.
- `arsd/service_unit.py` + `arsd/__main__.py` — `--print-service-unit` renders a configured ceiling into
  `ExecStart` through the existing `render_service_unit` seam (no parallel renderer), validated by the same
  policy the daemon applies. At the default it renders nothing, so existing units are byte-identical.

## Checklist

- [x] Add focused failing tests for the 4 GiB default, exact-bound acceptance and one-byte-over rejection
  under both the default and a custom daemon ceiling, CLI parsing/propagation, `server_info`, fail-closed
  startup validation, and the preserved individual hard limits.
- [x] Record the expected RED against the current 1 GiB hard-coded source.
- [x] Implement the policy object and the injected seam in `spec.py`.
- [x] Thread the policy through `protocol.parse_submit` and `ArsdHandlers`, including `server_info`
  (narrowed by the durable-first repair below: the daemon parses structurally and applies the effective
  policy through `admit_event_budget` to new work only).
- [x] Add the `arsd` startup option and its fail-closed `serve_daemon` validation.
- [x] Record focused GREEN, then the canonical gate.
- [x] Sync the technical solution, public Socket API and local-daemon pages, feature tracker, and the board;
  regenerate the documentation index and drift signal.
- [x] Review the final diff for scope, secret safety, host-path safety, and schema-version stability.
- [x] **Repair (Hermes blocker):** persist the effective ceiling in the durable admission record, validate it
  strictly on read and reconciliation, keep duplicate resolution bound to the original record, and move only
  `SUBMISSION_SCHEMA_VERSION`.
- [x] **Repair (independent review REQUEST_CHANGES):** resolve an accepted key from durable facts before the
  current policy is consulted; make duplicate resolution use the strict classification; correct the feature
  status and roll-up; leave the active plan coherent and ready for Hermes to stage.
- [x] **Repair (final review REQUEST_CHANGES):** render a configured ceiling into the service unit; bound the
  configured ceiling by the derived structural maximum; correct the authority docs to the durable-first
  implementation and to the fact that this plan is active.

## Acceptance

1. An omitted `--max-run-event-budget-bytes` yields an effective ceiling of exactly `4 * 1024 * 1024 * 1024`
   bytes — in the parser default, in `serve_daemon`, and in `server_info.limits`.
2. A custom positive integer startup value reaches both submit admission and `server_info.limits`, above and
   below the default.
3. `max_event_bytes * max_events` exactly equal to the effective ceiling is admitted; one byte over is
   refused as `INVALID_REQUEST`, before any Run or Session is created.
4. The individual hard limits are unchanged: `max_event_bytes` maximum 1 MiB and minimum 256, `max_events`
   maximum 1,000,000 — each still refused independently of the cross-product ceiling.
5. Direct/dev construction (`RunLimits()`, `protocol.parse_submit`, `ArsdHandlers`) applies the same default
   policy through the same seam rather than bypassing it.
6. A non-positive or non-integer configured ceiling — including `True`, where direct Python construction can
   supply one — refuses fail-closed rather than defaulting.
7. No wire/request/Spec/launch schema version, dependency, package version, or Session lifecycle change, and
   the runtime stays stdlib-only.
8. Every accepted Run's `submission.json` records the effective `max_run_event_budget_bytes` — the custom
   ceiling under a configured daemon, 4 GiB by default — and strict validation refuses a missing, mistyped,
   boolean, non-positive, or tampered value, classifying such a record CORRUPT.
9. For the same principal + `request_id` + canonical caller digest, a valid durable accepted or terminal Run
   returns its original `run_id`, `session_id`, and `accepted_at` under **any** current ceiling — including a
   lowered one — dispatching nothing twice and re-stamping nothing. The same `request_id` with different
   caller material stays `IDEMPOTENCY_CONFLICT`, and the ceiling is never digest input.
10. A genuinely new submission over the current ceiling is still refused before any Run or Session creation.
11. Duplicate resolution consumes the strict submission classification reconciliation uses: a missing,
    unknown-field, wrong-type, or non-positive budget value — or any other strict defect — reaches the
    existing `SUBMISSION_INDETERMINATE` containment and is never duplicate-accepted. No tolerant reader and
    no relaxed closed-field rule.
12. `SUBMISSION_SCHEMA_VERSION` moves to 4 alone, because only that record's material changed.
13. `--print-service-unit --max-run-event-budget-bytes N` renders `--max-run-event-budget-bytes N` in
    `ExecStart`; default rendering is unchanged; a value the daemon would refuse is refused at render time.
14. A configured ceiling equal to `STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES` is accepted, `+ 1` is refused, and
    an arbitrarily large integer is refused at construction and at daemon startup before lease/reconcile —
    while `bool`, non-integer, and non-positive stay refused and the default stays 4 GiB.
15. A stored ceiling outside the producer's domain, or one that could not have admitted the digest-matched
    request's limits, fails closed as indeterminate/corrupt and never authorizes a duplicate or a dispatch —
    while a legitimate historical ceiling still resolves the duplicate, above, below, or equal to the
    current daemon's, and at the exact structural maximum.

## Files likely to change

- `src/agent_run_supervisor/native_acp/spec.py`
- `src/agent_run_supervisor/arsd/protocol.py`
- `src/agent_run_supervisor/arsd/handlers.py`
- `src/agent_run_supervisor/arsd/__main__.py`
- `src/agent_run_supervisor/arsd/admission.py`
- `src/agent_run_supervisor/arsd/service_unit.py`
- `tests/arsd/test_service_unit.py`
- `docs/plans/active/2026-08-11-configurable-run-event-budget.md` (this plan — it travels **with** the
  candidate, so a frozen `git diff HEAD` covers it rather than missing it as an untracked file)
- `tests/native_acp/test_spec.py`
- `tests/arsd/test_admission.py`, `tests/arsd/test_reconcile.py`, `tests/arsd/reconcile_fixtures.py`
- `tests/arsd/test_protocol.py`
- `tests/arsd/test_handlers_registry.py`
- `tests/arsd/test_client_daemon.py`
- `docs/design/technical-solution.md`
- `docs/roadmap/features.md`
- `docs/roadmap/current-status.md`
- `website/docs/reference/socket-api.md`
- `website/docs/deployment/local-daemon.md`
- `docs/INDEX.md` and `docs/lessons/_drift_report.md` through their generators

## Verification gates

1. Focused RED, then GREEN, on the new tests in `tests/native_acp/test_spec.py`,
   `tests/arsd/test_protocol.py`, `tests/arsd/test_handlers_registry.py`, and
   `tests/arsd/test_client_daemon.py`.
2. Focused regression across those four files.
3. Documentation generation and checks: `tools/build_docs_index.py`, `tools/docs_drift_signal.py`,
   `tools/check_docs_site.py`, and `tools/check_roadmap_governance.py`.
4. Canonical repository gate: `make verify`.
5. Final hygiene: `git diff --check` plus an added-line secret and host-path review.

## Local evidence

- Focused RED against the 1 GiB source: `29 failed, 1 passed, 495 deselected`. Failures were the absent
  `EventBudgetPolicy`/`DEFAULT_MAX_RUN_EVENT_BUDGET_BYTES`, the unknown `event_budget_policy` /
  `max_run_event_budget_bytes` keyword on `RunLimits`/`ArsdHandlers`/`serve_daemon`, the unrecognized
  `--max-run-event-budget-bytes` flag, the missing `server_info` key, and a 4 GiB Run refused by the old
  hard-coded ceiling. The one pre-existing pass is the guard that the ceiling is not a caller-supplied
  limit key.
- Focused GREEN on the same selection after the source change: `30 passed, 495 deselected`.
- Full suite: passed.
- Documentation index/drift generation and checks, public-site content gate, static safety scan, and
  roadmap governance: passed.
- Canonical `make verify`: `All verify gates passed.`
- Durable-evidence repair RED (before persisting the ceiling):
  `5 failed, 9 passed, 109 deselected`, every failure `KeyError: 'max_run_event_budget_bytes'`.
- Durable-evidence repair GREEN on the same selection: `14 passed, 109 deselected`.
- Review-repair RED (durable-first resolution + strict duplicate evidence):
  `6 failed, 1 passed, 122 deselected` — the lowered-ceiling duplicate returned `INVALID_REQUEST` instead of
  the original facts, a conflicting retry was downgraded to `INVALID_REQUEST`, and all four weak-evidence
  records were duplicate-accepted (`DID NOT RAISE`). The one pass is the new-work refusal, which must not
  change.
- Review-repair GREEN on the same selection: `7 passed, 122 deselected`.
- Final-review repair RED (service-unit rendering + structural bound):
  `6 failed, 28 passed, 642 deselected` — `ExecStart` omitted the configured flag,
  `render_service_unit()` had no such keyword, print mode returned 0 for a ceiling the daemon would refuse,
  `EventBudgetPolicy(bound + 1)` did not raise, and `serve_daemon` reached the lease under an
  over-structural and an arbitrarily large ceiling.
- Final-review repair GREEN on the same selection: `34 passed, 642 deselected`.
- Impossible-evidence repair RED: `2 failed, 4 passed, 129 deselected` — a stored ceiling of
  `STRUCTURAL_MAX_RUN_EVENT_BUDGET_BYTES + 1` classified VALID, and a stored ceiling smaller than the
  digest-matched request's own limits authorized the duplicate (`DID NOT RAISE`). The four positive controls
  passed throughout.
- Impossible-evidence repair GREEN on the same selection: `6 passed, 129 deselected`.
- Operator-facing refusal smoke: starting `arsd` with `--max-run-event-budget-bytes 0` printed
  `arsd: invalid run event budget: max_run_event_budget_bytes must be positive; refusing to listen`,
  exited 1, and created no supervisor root or socket.

## Risks and mitigations

- **A mutable module global sneaking in.** The default is one frozen instance that nothing rebinds; a
  differently configured daemon constructs its own and injects it.
- **Changing sealed material by accident.** The policy rides an `InitVar`, so it is not a dataclass field;
  the wire key set, `dataclasses.fields(RunLimits)`, and the golden spec hash stay pinned by existing tests.
- **Silently widening the individual hard limits.** Keep the three structural bounds in `RunLimits` and test
  each one independently of the cross-product rule.
- **A dev path bypassing the policy.** Give every construction path the same default through the same seam
  rather than a second copy of the rule.
- **Documentation drift.** One numeric contract, stated in GiB with the explicit non-claims, across the
  technical solution and both public pages.
- **A ceiling change breaking idempotency.** Keep the policy out of `compute_request_digest` and out of the
  request material entirely; pin it with a test that digests the same payload under two ceilings.
- **Weakening closed-field validation to accept the new field.** Add it to the one named field set both the
  writer and the strict validator read, and give it its own positive-integer rule rather than relaxing the
  exact-key-set check.
- **Policy retroactively invalidating an acceptance.** Order the submit path so identity is understood first
  and the ceiling decides new work only; pin both directions with tests (lowered ceiling resolves the
  duplicate, lowered ceiling still refuses new work).
- **Parsing under the structural policy reading as a bypass.** It is a derived constant equal to the product
  of the existing per-field maxima, so it admits exactly what those limits already admit and no more; the
  daemon's real ceiling is still the only thing that admits a Run.

## Rollback

Revert this candidate's source edits, focused tests, and proportional documentation updates together. No
data migration, schema downgrade, stored-record rewrite, or service action is involved: the change is
admission policy plus one startup option, and a reverted daemon simply admits under the previous constant.

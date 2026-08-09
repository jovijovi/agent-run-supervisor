---
title: "Long-Run timeout limits implementation"
status: active
created_at: 2026-08-09
last_validated_at: 2026-08-09
---
# Long-Run timeout limits implementation

## Context and target

The current `RunLimits.turn_timeout_seconds` default is 600 seconds and its ceiling is 86,400 seconds.
This source-only task changes the sealed per-Run default to 21,600 seconds (6 hours) and the accepted
maximum to 604,800 seconds (7 days) for approved long-running external AGENT tasks.

The timeout remains a hard limit over the complete Prompt / AGENT multi-loop execution of one Run. It is
not a Session timeout: every later Run that reuses a Session seals its own independent limits. Expiry keeps
the existing terminate → cancellation grace → kill/reap behavior. Wire and schema versions do not move.

The approved task closes at one verified local atomic commit. It authorizes no push, PR, merge, release,
publication, deployment, service action, production configuration change, migration, cutover, caller
integration, or real-provider canary.

## Checklist

- [x] Add focused tests for the 6-hour default, Socket API `{}` defaulting, the inclusive 7-day maximum,
  and protocol-boundary refusal above that maximum.
- [x] Record an expected RED against the old 600-second default and 1-day maximum.
- [x] Change only the `RunLimits` default and maximum constants in production source.
- [x] Record focused GREEN and keep existing short timeout coverage explicit and fast.
- [x] Synchronize the PRD, technical solution, public Socket API, Runs-and-Sessions guide, feature tracker,
  and living board without release or deployment claims.
- [x] Regenerate the documentation index and drift signal, then run focused documentation gates.
- [x] Review the final diff for scope, secret safety, schema-version stability, and forbidden side effects.

## Acceptance

- `RunLimits().turn_timeout_seconds == 21_600.0`.
- Socket/API parsing of `limits: {}` seals `turn_timeout_seconds == 21_600.0`.
- `turn_timeout_seconds == 604_800.0` is accepted.
- A value above `604_800.0` is rejected as `INVALID_REQUEST` at the protocol boundary.
- Existing short timeout tests continue to pass using explicit sub-second or small timeout values.
- The hard timeout still covers one Run's complete Prompt / AGENT multi-loop and preserves the existing
  escalation/reap lifecycle; Session reuse starts a new independently limited Run.
- All six public `RunLimits` fields, defaults, and bounds are documented accurately.
- No wire/API/schema version, Session lifecycle, dependency, changelog, or package version changes.

## Files likely to change

- `src/agent_run_supervisor/native_acp/spec.py`
- `tests/native_acp/test_spec.py`
- `tests/arsd/test_protocol.py`
- `docs/product/prd.md`
- `docs/design/technical-solution.md`
- `docs/roadmap/features.md`
- `docs/roadmap/current-status.md`
- `website/docs/reference/socket-api.md`
- `website/docs/concepts/runs-and-sessions.md`
- `docs/INDEX.md` and `docs/lessons/_drift_report.md` through their generators

## Verification gates

1. Focused RED/GREEN:
   `uv run --offline --extra dev --extra native pytest -q tests/native_acp/test_spec.py::test_run_limits_default_to_a_six_hour_turn_timeout tests/arsd/test_protocol.py::test_parse_submit_empty_limits_use_the_six_hour_turn_default tests/arsd/test_protocol.py::test_parse_submit_accepts_the_seven_day_turn_timeout_maximum tests/arsd/test_protocol.py::test_parse_submit_rejects_a_turn_timeout_above_seven_days`
2. Focused spec/protocol regression:
   `uv run --offline --extra dev --extra native pytest -q tests/native_acp/test_spec.py tests/arsd/test_protocol.py`
3. Explicit short-timeout lifecycle coverage:
   `uv run --offline --extra dev --extra native pytest -q tests/native_acp/test_run_task.py::test_b2_dispatched_timeout_escalated_kill_is_unknown_quarantined tests/native_acp/test_session_no_close_acceptance.py::test_post_dispatch_uncertainty_quarantines_and_refuses_the_next_run`
4. Documentation generation and checks: `python3 tools/build_docs_index.py --write`,
   `python3 tools/docs_drift_signal.py --write`, `python3 tools/build_docs_index.py --check`,
   `python3 tools/docs_drift_signal.py --check`, `python3 tools/check_docs_site.py`, and
   `python3 tools/check_roadmap_governance.py`.
5. Canonical repository gate: `make verify`.
6. Final hygiene: `git diff --check` and an added-line secret/dangerous-pattern review.

## Local evidence

- Focused RED against the old source: `3 failed, 1 passed`, with failures on both 6-hour defaults and
  inclusive 7-day acceptance; the above-ceiling refusal passed.
- Exact focused GREEN after the two source edits: `4 passed`.
- Full `test_spec.py` + `test_protocol.py` focus: passed with exit code 0.
- Existing explicit `0.5`/`0.3`-second lifecycle focus: `2 passed` in 2.3 seconds.
- Documentation index/drift generation and checks, public-site contract, and roadmap governance: passed.
- Canonical `make verify`: passed, including tests, compile, docs, safety, version, build, manifest,
  throwaway artifact smoke, governance, and whitespace gates.
- Independent fresh-context blocker review inspected the actual worktree and returned `PASS / 0 blocker`;
  review mutation guards matched the frozen candidate.

## Risks and mitigations

- **Accidentally changing Session semantics:** keep the limit exclusively on `RunLimits` and make no
  Session-store or lease change.
- **Making timeout tests wait for the new default:** retain explicit short values in lifecycle tests and
  test the larger values through construction/protocol parsing only.
- **Off-by-one maximum:** pin the inclusive 604,800-second boundary and a value immediately above it.
- **Documentation drift:** keep one numeric contract across authority and public pages, then run both docs
  generators and site/governance checks.

## Rollback

Before merge, revert this candidate's two numeric source edits, focused tests, and proportional current-doc
updates together. Rollback needs no data migration, schema downgrade, Session rewrite, service action, or
compatibility shim because this task changes only admission policy defaults and bounds.

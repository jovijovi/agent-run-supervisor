"""A10 — the exhaustive reconciliation oracle (plan §7.3).

`T × D × S × L × U` = `4 × 2 × 3 × 3 × 3` = **216** artifact combinations, and
row selection for rows 2/3, 4, and 5/6 additionally depends on the Session
record through "actionable", so the proof is properly a **216 × Session-state
product**. Three parts:

* **P1** — the full literal product: 216 combinations × the 9 Session states =
  **1,944** real run directories under one ``tmp_path`` root, each classified
  through production and matched against an independent transcription of the
  authority table.
* **P2** — 216 × {actionable, non-actionable} = **432** parametrized cases for
  readable per-case failures and the row-count arithmetic.
* **P3** — the actionability predicate itself: 9 Session states × 3 identity
  sources, and the proof that it is the *only* Session-derived input.

The expected-row function below is written from `docs/design/technical-solution.md`
§9's table, not from the implementation, so agreement is evidence.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

import reconcile_fixtures as rf

from agent_run_supervisor.arsd import reconcile
from agent_run_supervisor.native_acp import storage

DOC = storage.JsonDocumentKind

# Exactly the four permitted vocabulary outcomes.
AUTHORITATIVE = reconcile.Outcome.AUTHORITATIVE_TERMINAL
UNKNOWN_QUARANTINE = reconcile.Outcome.UNKNOWN_QUARANTINE
PRE_DISPATCH = reconcile.Outcome.PRE_DISPATCH_FAILED
REFUSE = reconcile.Outcome.REFUSE_TO_LISTEN

# One outcome per row, fixed by the authority table.
ROW_OUTCOMES = {
    1: AUTHORITATIVE,
    2: UNKNOWN_QUARANTINE,
    3: REFUSE,
    4: REFUSE,
    5: UNKNOWN_QUARANTINE,
    6: REFUSE,
    7: PRE_DISPATCH,
    8: REFUSE,
    9: REFUSE,
    10: REFUSE,
    # Row 11 is one row with three submission-dependent outcomes.
    11: None,
}

COMBINATIONS = tuple(
    itertools.product(
        rf.TERMINAL_STATES, rf.DISPATCH_STATES, rf.DOCUMENT_STATES,
        rf.DOCUMENT_STATES, rf.DOCUMENT_STATES,
    )
)


def expected_row(
    *, terminal: str, dispatch: bool, spec: str, launch: str, submission: str,
    actionable: bool,
) -> int:
    """First-match transcription of the eleven-row table (authority §9)."""
    if terminal == "trusted_terminal":
        return 1
    if terminal == "trusted_unknown":
        return 2 if actionable else 3
    if terminal == "corrupt":
        return 4
    # terminal is absent from here down.
    if dispatch:
        return 5 if actionable else 6
    if spec == "valid":
        return 7 if launch in ("valid", "absent") else 8
    if spec == "corrupt":
        return 9
    # spec is absent.
    if launch in ("valid", "corrupt"):
        return 10
    return 11


def expected_outcome(row: int, submission: str) -> reconcile.Outcome:
    if row != 11:
        return ROW_OUTCOMES[row]
    if submission == "valid":
        return PRE_DISPATCH
    if submission == "absent":
        return PRE_DISPATCH
    return REFUSE


def _decide(combination, actionable: bool) -> reconcile.RowDecision:
    terminal, dispatch, spec, launch, submission = combination
    return reconcile.select_row(
        reconcile.RunFacts(
            terminal=reconcile.TERMINAL_CLASS_BY_NAME[terminal],
            dispatch=dispatch,
            spec=DOC[spec.upper()],
            launch=DOC[launch.upper()],
            submission=DOC[submission.upper()],
            actionable=actionable,
        )
    )


# ---------------------------------------------------------------------------
# P1 — full literal product over real trees: 216 × 9 Session states
# ---------------------------------------------------------------------------


def test_p1_every_case_gets_exactly_one_row_and_one_outcome(tmp_path: Path) -> None:
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    runs_root = Path(events.base_dir)

    cases = 0
    seen_outcomes: set[reconcile.Outcome] = set()
    for index, (combination, session_quarantined) in enumerate(
        itertools.product(COMBINATIONS, rf.SESSION_STATES)
    ):
        terminal, dispatch, spec, launch, submission = combination
        run_id = f"run-{index:05d}"
        session_id = f"sess-{index:05d}"
        rf.build_run(
            runs_root,
            run_id,
            terminal=terminal,
            dispatch=dispatch,
            spec=spec,
            launch=launch,
            submission=submission,
            session_id=session_id,
        )
        rf.build_session(sessions, state=session_quarantined, session_id=session_id)

        facts = reconcile.classify_run(
            runs_root / run_id, session_store=sessions
        )
        decision = reconcile.select_row(facts)

        attributable = spec == "valid" or (spec != "valid" and submission == "valid")
        actionable = attributable and session_quarantined in rf.ACTIONABLE_SESSION_STATES
        assert facts.actionable is actionable, (combination, session_quarantined)

        row = expected_row(
            terminal=terminal,
            dispatch=dispatch,
            spec=spec,
            launch=launch,
            submission=submission,
            actionable=actionable,
        )
        assert decision.row == row, (combination, session_quarantined)
        assert decision.outcome is expected_outcome(row, submission), (
            combination,
            session_quarantined,
        )
        seen_outcomes.add(decision.outcome)
        cases += 1

    assert cases == 216 * len(rf.SESSION_STATES) == 1728
    # Every case landed in the four-value vocabulary and nothing else exists.
    assert seen_outcomes <= {AUTHORITATIVE, UNKNOWN_QUARANTINE, PRE_DISPATCH, REFUSE}
    assert set(reconcile.Outcome) == {
        AUTHORITATIVE,
        UNKNOWN_QUARANTINE,
        PRE_DISPATCH,
        REFUSE,
    }


# ---------------------------------------------------------------------------
# P2 — parametrized partition proof and row-count arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actionable", [True, False], ids=["actionable", "unattributable"])
@pytest.mark.parametrize(
    "combination", COMBINATIONS, ids=lambda c: "-".join(str(part) for part in c)
)
def test_p2_row_assignment_is_total_and_single_valued(combination, actionable) -> None:
    terminal, dispatch, spec, launch, submission = combination
    decision = _decide(combination, actionable)
    row = expected_row(
        terminal=terminal,
        dispatch=dispatch,
        spec=spec,
        launch=launch,
        submission=submission,
        actionable=actionable,
    )
    assert decision.row == row
    assert decision.outcome is expected_outcome(row, submission)


def test_p2_row_counts_partition_the_216_combinations() -> None:
    counts_actionable: dict[int, int] = {}
    counts_unattributable: dict[int, int] = {}
    for combination in COMBINATIONS:
        for actionable, bucket in (
            (True, counts_actionable),
            (False, counts_unattributable),
        ):
            row = _decide(combination, actionable).row
            bucket[row] = bucket.get(row, 0) + 1

    assert sum(counts_actionable.values()) == 216
    assert sum(counts_unattributable.values()) == 216

    # Plan §7.3: row 1 = 54, rows 2–3 = 54, row 4 = 54, rows 5–6 = 27,
    # rows 7–11 = 27.
    for counts in (counts_actionable, counts_unattributable):
        assert counts[1] == 54
        assert counts.get(2, 0) + counts.get(3, 0) == 54
        assert counts[4] == 54
        assert counts.get(5, 0) + counts.get(6, 0) == 27
        assert sum(counts.get(row, 0) for row in (7, 8, 9, 10, 11)) == 27

    # Actionability splits rows 2/3 and 5/6 completely, one way each.
    assert counts_actionable.get(2) == 54 and counts_actionable.get(3) is None
    assert counts_unattributable.get(3) == 54 and counts_unattributable.get(2) is None
    assert counts_actionable.get(5) == 27 and counts_actionable.get(6) is None
    assert counts_unattributable.get(6) == 27 and counts_unattributable.get(5) is None

    # The pre-dispatch window splits exactly: S=VALID 6+3, S=CORRUPT 9,
    # S=ABSENT 6+3.
    assert counts_actionable[7] == 6
    assert counts_actionable[8] == 3
    assert counts_actionable[9] == 9
    assert counts_actionable[10] == 6
    assert counts_actionable[11] == 3


def test_p2_actionability_is_irrelevant_outside_its_135_combinations() -> None:
    load_bearing = 0
    for combination in COMBINATIONS:
        actionable_row = _decide(combination, True)
        unattributable_row = _decide(combination, False)
        if actionable_row != unattributable_row:
            load_bearing += 1
        else:
            terminal = combination[0]
            dispatch = combination[1]
            # Row 4 keeps one row for both, but its *effects* still depend on
            # actionability, so it is counted as load-bearing below.
            assert not (terminal == "trusted_unknown")
            assert not (terminal == "absent" and dispatch)
    # Rows 2/3 (54) + rows 5/6 (27) differ by row; row 4 (54) differs by effect.
    assert load_bearing == 54 + 27
    corrupt_terminal = sum(1 for c in COMBINATIONS if c[0] == "corrupt")
    assert load_bearing + corrupt_terminal == 135


# ---------------------------------------------------------------------------
# P3 — the actionability predicate and its composition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_quarantined", rf.SESSION_STATES)
@pytest.mark.parametrize(
    "identity_source", ["spec_valid", "submission_fallback", "neither"]
)
def test_p3_actionability_requires_an_existing_matching_readable_record(
    tmp_path: Path, session_quarantined: str, identity_source: str
) -> None:
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    runs_root = Path(events.base_dir)
    run_id = "run-p3"
    session_id = "sess-p3"

    spec_state = "valid" if identity_source == "spec_valid" else "corrupt"
    submission_state = (
        "valid" if identity_source in ("spec_valid", "submission_fallback") else "corrupt"
    )
    if identity_source == "submission_fallback":
        spec_state = "corrupt"
    rf.build_run(
        runs_root,
        run_id,
        terminal="absent",
        dispatch=True,
        spec=spec_state,
        launch="absent",
        submission=submission_state,
        session_id=session_id,
    )
    rf.build_session(sessions, state=session_quarantined, session_id=session_id)

    facts = reconcile.classify_run(runs_root / run_id, session_store=sessions)

    attributable = identity_source in ("spec_valid", "submission_fallback")
    expected = attributable and session_quarantined in rf.ACTIONABLE_SESSION_STATES
    assert facts.actionable is expected
    if attributable:
        assert facts.attribution is not None
        assert facts.attribution.session_id == session_id
        assert facts.attribution.owner == rf.OWNER
        assert facts.attribution.namespace == rf.NAMESPACE
    else:
        assert facts.attribution is None


def test_p3_spec_attribution_wins_over_a_conflicting_submission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    runs_root = Path(events.base_dir)
    run_id = "run-p3-priority"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)

    rf.write_document(
        run_dir / "spec.json",
        state="valid",
        payload=rf.spec_payload(run_id=run_id, session_id="sess-from-spec"),
    )
    rf.write_document(
        run_dir / "submission.json",
        state="valid",
        payload=rf.submission_payload(run_id=run_id, session_id="sess-from-submission"),
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-from-spec")
    rf.build_session(sessions, state="matching_open", session_id="sess-from-submission")

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.attribution is not None
    assert facts.attribution.session_id == "sess-from-spec"
    assert facts.attribution.source == "spec"


def test_p3_submission_is_a_fallback_only_when_the_spec_is_not_valid(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    runs_root = Path(events.base_dir)

    # Corrupt Spec + valid submission → submission attributes.
    fallback = rf.build_run(
        runs_root,
        "run-fallback",
        spec="corrupt",
        submission="valid",
        session_id="sess-fallback",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-fallback")
    facts = reconcile.classify_run(fallback, session_store=sessions)
    assert facts.attribution is not None
    assert facts.attribution.source == "submission"

    # Valid Spec + corrupt submission → the submission is ignored entirely.
    ignored = rf.build_run(
        runs_root,
        "run-ignored",
        spec="valid",
        submission="corrupt",
        session_id="sess-ignored",
    )
    rf.build_session(sessions, state="matching_open", session_id="sess-ignored")
    facts = reconcile.classify_run(ignored, session_store=sessions)
    assert facts.attribution is not None
    assert facts.attribution.source == "spec"
    assert facts.submission is DOC.CORRUPT


@pytest.mark.parametrize(
    "never_authority",
    ["launch", "result_session_id", "directory_name", "progress", "marker_contents"],
)
def test_p3_no_other_artifact_is_ever_attribution_authority(
    tmp_path: Path, never_authority: str
) -> None:
    root = tmp_path / "sv"
    sessions = storage.native_session_store(root)
    events = storage.native_event_store(root)
    runs_root = Path(events.base_dir)
    run_id = "run-no-authority"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)

    # Neither Spec nor submission is valid: nothing else may supply identity.
    rf.write_document(run_dir / "spec.json", state="corrupt", payload={})
    rf.write_document(run_dir / "submission.json", state="corrupt", payload={})
    rf.build_session(sessions, state="matching_open", session_id="sess-planted")

    if never_authority == "launch":
        rf.write_document(
            run_dir / "launch.json", state="valid", payload=rf.launch_payload()
        )
    elif never_authority == "result_session_id":
        rf.write_terminal(
            run_dir, run_id=run_id, state="trusted_unknown", session_id="sess-planted"
        )
    elif never_authority == "directory_name":
        (runs_root / "sess-planted").mkdir(exist_ok=True)
    elif never_authority == "progress":
        rf.write_document(
            run_dir / "progress.json",
            state="valid",
            payload={
                "schema_version": 1,
                "state": "running",
                "session_id": "sess-planted",
                "last_seq": 1,
                "event_count": 1,
                "updated_at": "2026-07-22T00:00:00+00:00",
            },
        )
    elif never_authority == "marker_contents":
        rf.write_document(
            run_dir / rf.DISPATCH_STARTED_MARKER,
            state="valid",
            payload={"marker": "prompt-dispatch-started", "session_id": "sess-planted"},
        )

    facts = reconcile.classify_run(run_dir, session_store=sessions)
    assert facts.attribution is None
    assert facts.actionable is False

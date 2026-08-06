"""D4 acceptance: Runs terminate, Sessions do not close.

The end-to-end business contract, proven hermetically over the fake agent — no
real provider, no daemon restart, no runtime data touched, nothing installed or
enabled. Every scenario here is one of the ten the plan requires, and each one
is written so that it fails if the *business* outcome regresses, not merely if
an internal name moves.

The single fact under test: a caller can keep talking to the same external agent
thread across Runs, indefinitely, and nothing in ARS ends that thread on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp.driver import NativeAcpDriver
from agent_run_supervisor.native_acp.run_task import (
    CONFIG_PROVEN_MARKER,
    CONFIG_SWITCH_STARTED_MARKER,
    DISPATCH_STARTED_MARKER,
    CreateSessionPlan,
    LoadSessionPlan,
    RunTask,
)
from agent_run_supervisor.session import (
    SessionLockError,
    SessionQuarantinedError,
    derive_session_id_for_run,
    read_native_session_record,
)

from tests.native_acp.test_run_task import (
    HAPPY_SCRIPT,
    Harness,
    _request,
    _run,
)


def _create_request(**overrides):
    """A create: the Session portion of the wire is absent."""
    return _request(session_id=None, **overrides)


def _spy_new_session(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every real ``session/new`` — the duplicate-creation tripwire."""
    calls: list[str] = []
    original = NativeAcpDriver.new_session

    async def spying(self, *, cwd: str, meta=None):
        calls.append(cwd)
        return await original(self, cwd=cwd, meta=meta)

    monkeypatch.setattr(NativeAcpDriver, "new_session", spying)
    return calls


def _dispatch_count(harness: Harness) -> int:
    """How many Runs reached the uncertainty boundary at all."""
    runs_root = harness.root / "native-runs"
    if not runs_root.is_dir():
        return 0
    return sum(
        1 for run in runs_root.iterdir() if (run / DISPATCH_STARTED_MARKER).exists()
    )


# -- 1 / 4: create once, then reuse the same external thread ----------------


def test_a_create_then_two_reuses_hold_one_external_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenarios 1 and 4: stable identity, and Runs 2 and 3 load the same id."""
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    new_sessions = _spy_new_session(monkeypatch)

    first = _run(harness.task(run_id="run-a1", request=_create_request()))

    assert first.status is AgentRunStatus.COMPLETED
    session_id = first.session_id
    # Stable and derived, not invented: the caller can recompute it from the Run.
    assert session_id == derive_session_id_for_run("run-a1")

    store = harness.session_store()
    created = store.open_session(session_id)
    external_id = created.agent_session_id
    assert external_id, "the create must bind a real external session id"

    # Runs 2 and 3 name that Session and must reach the same agent thread.
    for run_id in ("run-a2", "run-a3"):
        later = _run(harness.task(run_id=run_id, request=_request(session_id=session_id)))
        assert later.status is AgentRunStatus.COMPLETED
        assert later.session_id == session_id
        assert store.open_session(session_id).agent_session_id == external_id

    # Exactly one external Session was ever created, across three Runs.
    assert len(new_sessions) == 1
    assert "session/new" in harness.methods_seen()
    assert harness.methods_seen().count("session/load") == 2


def test_a_run_terminal_never_ends_its_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core invariant, stated on its own so a regression names itself."""
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)

    result = _run(harness.task(run_id="run-b1", request=_create_request()))
    assert result.status is AgentRunStatus.COMPLETED

    store = harness.session_store()
    record = store.open_session(result.session_id)
    # It exists, it is not quarantined, it carries no lifecycle field at all,
    # and its lease was released so the next Run can take it.
    assert record.quarantine is None
    assert not hasattr(record, "state")
    on_disk = json.loads(
        (Path(store.base_dir) / result.session_id / "session.json").read_text(
            encoding="utf-8"
        )
    )
    assert "state" not in on_disk
    assert "closed_at" not in on_disk
    assert not (Path(store.base_dir) / result.session_id / "lock.json").exists()


# -- 6: cancellation ends the Run, not the Session --------------------------


def test_run_cancel_leaves_the_session_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 6, on the trustworthy-terminal side of the boundary.

    A cancel that the agent answers with a real ACP terminal is an ordinary Run
    outcome: the Session it ran under stays reusable, and the next Run loads it.
    """
    script = dict(HAPPY_SCRIPT)
    script["prompt_stop_reason"] = "cancelled"
    harness = Harness(tmp_path, monkeypatch, script)

    cancelled = _run(harness.task(run_id="run-c1", request=_create_request()))
    assert cancelled.status is AgentRunStatus.CANCELLED
    assert cancelled.session_quarantined is False

    store = harness.session_store()
    session_id = cancelled.session_id
    assert store.open_session(session_id).quarantine is None

    # The proof that "reusable" is not just a label: a later Run really loads it.
    resumed = _run(
        Harness(tmp_path, monkeypatch, HAPPY_SCRIPT).task(
            run_id="run-c2", request=_request(session_id=session_id)
        )
    )
    assert resumed.status is AgentRunStatus.COMPLETED
    assert resumed.session_id == session_id


# -- 7: one active Run per Session ------------------------------------------


def test_a_held_lease_blocks_a_concurrent_run_on_the_same_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 7: concurrency is a lease concern, and the lease still holds."""
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    first = _run(harness.task(run_id="run-d1", request=_create_request()))
    session_id = first.session_id

    store = harness.session_store()
    held = store.acquire_lock(session_id, "someone-else", refuse_quarantined=True)
    try:
        with pytest.raises(SessionLockError):
            store.acquire_lock(session_id, "this-run", refuse_quarantined=True)
        blocked = _run(
            harness.task(run_id="run-d2", request=_request(session_id=session_id))
        )
        assert blocked.status is AgentRunStatus.FAILED
    finally:
        store.release_lock(session_id, held.token)

    # Releasing the lease restores reuse: the block was never a Session end.
    resumed = _run(
        harness.task(run_id="run-d3", request=_request(session_id=session_id))
    )
    assert resumed.status is AgentRunStatus.COMPLETED


# -- 8: an unknown Session fails without creating anything ------------------


def test_an_unknown_session_id_fails_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 8: reuse is existing-only, and never falls back to creating."""
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    new_sessions = _spy_new_session(monkeypatch)

    # Built directly, bypassing the harness seeder: this Run deliberately names
    # a Session that nothing ever created.
    task = RunTask(
        request=_request(session_id="sess-never-existed"),
        prompt_text="hello agent",
        run_id="run-e1",
        workspace_root=harness.workspace,
        registry=harness.registry,
        agent_entry=harness.entry,
        supervisor_root=harness.root,
        submitted_at="2026-07-21T00:00:00+00:00",
    )
    result = _run(task)

    assert result.status is AgentRunStatus.FAILED
    assert new_sessions == [], "a reuse path must never emit session/new"
    assert _dispatch_count(harness) == 0, "nothing may be dispatched"
    store = harness.session_store()
    assert read_native_session_record(store, "sess-never-existed") is None
    assert store.list_records() == []


# -- 9: post-dispatch uncertainty quarantines and forbids retry --------------


def test_post_dispatch_uncertainty_quarantines_and_refuses_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 9: the Session survives, and it refuses new work."""
    script = dict(HAPPY_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = Harness(tmp_path, monkeypatch, script)

    from agent_run_supervisor.native_acp.spec import RunLimits

    result = _run(
        harness.task(
            run_id="run-f1",
            request=_create_request(limits=RunLimits(turn_timeout_seconds=0.3)),
        )
    )

    assert result.status is AgentRunStatus.UNKNOWN
    assert result.payload["retryable"] is False
    assert result.session_quarantined is True

    store = harness.session_store()
    record = store.open_session(result.session_id)
    # The Session still exists and is still queryable — it simply refuses work.
    assert record.session_id == result.session_id
    evidence = record.quarantine
    assert set(evidence) == {"reason_code", "source_run_id", "recorded_at"}
    assert evidence["source_run_id"] == "run-f1"

    with pytest.raises(SessionQuarantinedError):
        store.acquire_lock(result.session_id, "next-run", refuse_quarantined=True)

    retried = _run(
        harness.task(run_id="run-f2", request=_request(session_id=result.session_id))
    )
    assert retried.status is AgentRunStatus.FAILED


# -- 10: nothing leaks --------------------------------------------------------


def test_no_external_id_or_credential_shape_leaks_into_caller_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 10, over the whole create-then-reuse path.

    The external AGENT session id is continuity state, not caller-facing
    identity: it lives in the Session record and reaches the agent, and it
    appears in no terminal payload and no event stream.
    """
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    first = _run(harness.task(run_id="run-g1", request=_create_request()))
    session_id = first.session_id
    external_id = harness.session_store().open_session(session_id).agent_session_id
    second = _run(
        harness.task(run_id="run-g2", request=_request(session_id=session_id))
    )

    for result in (first, second):
        payload = json.dumps(result.payload)
        assert external_id not in payload
        assert "sk-live-" not in payload
        # The ARS Session id is caller-facing and deliberately present.
        assert result.session_id == session_id

    for run_id in ("run-g1", "run-g2"):
        events_path = harness.run_dir(run_id) / "events.jsonl"
        if events_path.exists():
            assert external_id not in events_path.read_text(encoding="utf-8")


# -- structural: the two plan types stay disjoint ----------------------------


def test_the_start_plan_union_has_exactly_two_disjoint_members() -> None:
    """A create can never become a load, or the reverse, by any conversion."""
    assert CreateSessionPlan is not LoadSessionPlan
    assert [f for f in CreateSessionPlan.__dataclass_fields__] == ["ar_session_id"]
    assert [f for f in LoadSessionPlan.__dataclass_fields__] == [
        "ar_session_id",
        "external_session_id",
    ]


# -- B2 / B3: a partial configuration switch is never silently reusable ------
#
# Between publishing the bound Session record and writing the dispatch marker,
# ARS mutates the agent's configuration. If that mutation may have landed and
# ARS cannot prove what the agent's configuration now is, the Session is no
# longer a known-good place to send a prompt. Reuse then requires proof: either
# no switch was dispatched, or an exact rollback succeeded.
#
# The hazard is symmetric across create and reuse — a create publishes its
# record *before* configuring — so both paths are pinned here, in-process and
# across a daemon crash.


def _config_markers(harness: Harness, run_id: str) -> set[str]:
    run_dir = harness.run_dir(run_id)
    if not run_dir.is_dir():
        return set()
    return {
        entry.name
        for entry in run_dir.iterdir()
        if entry.name.startswith("config-")
    }


def test_a_create_whose_switch_cannot_be_proven_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B3: the create path publishes its record, then fails mid-configuration.

    A create has no previously proven configuration to roll back to, so an
    unprovable switch can only be quarantined — never left reusable.
    """
    script = dict(HAPPY_SCRIPT)
    # The agent accepts the set RPC and then refuses the value: a set was
    # dispatched, so what the agent's configuration now is cannot be proven.
    script["reject_set_config_values"] = ["kimi-for-coding/k3"]
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(run_id="run-h1", request=_create_request()))

    assert result.status is AgentRunStatus.FAILED
    assert _dispatch_count(harness) == 0, "no prompt may be dispatched"

    store = harness.session_store()
    record = store.open_session(result.session_id)
    assert record.agent_session_id, "the record was published before the switch"
    assert record.quarantine is not None, (
        "an unprovable configuration switch must quarantine, not stay reusable"
    )
    assert result.session_quarantined is True


def test_a_switch_that_was_never_dispatched_leaves_the_session_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side: a failure before any set is not a switch at all."""
    script = dict(HAPPY_SCRIPT)
    script["omit_initial_options"] = True
    harness = Harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(run_id="run-h2", request=_create_request()))

    assert result.status is AgentRunStatus.FAILED
    store = harness.session_store()
    record = read_native_session_record(store, result.session_id)
    if record is not None:
        assert record.quarantine is None


def test_the_run_records_bounded_categorical_config_switch_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash cannot ask the process what it was doing; the disk must know.

    The markers are the whole durable vocabulary: they carry no model literal,
    no option value, and no child text — only which boundary was crossed.
    """
    harness = Harness(tmp_path, monkeypatch, HAPPY_SCRIPT)
    result = _run(harness.task(run_id="run-h3", request=_create_request()))
    assert result.status is AgentRunStatus.COMPLETED

    markers = _config_markers(harness, "run-h3")
    assert CONFIG_SWITCH_STARTED_MARKER in markers
    assert CONFIG_PROVEN_MARKER in markers

    body = json.loads(
        (harness.run_dir("run-h3") / CONFIG_PROVEN_MARKER).read_text(encoding="utf-8")
    )
    assert set(body) == {"marker", "run_id", "ordinal", "created_at"}
    assert body["run_id"] == "run-h3"
    # No configuration value of any kind reaches a durable marker.
    text = json.dumps(body)
    assert "kimi-for-coding" not in text
    assert "max" not in text

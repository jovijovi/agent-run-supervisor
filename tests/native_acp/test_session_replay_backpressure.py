"""Session reuse under a third-party adapter's history replay (L2).

Two independent facts are pinned here, because the failure they produce looks
identical from the outside (a reuse Run that dies on ``EVIDENCE_PIPELINE``)
but their causes are not:

1. **Replay separation is the semantic fix.** Updates causally at or before the
   frozen prompt wire boundary are the AGENT's bootstrap/history replay, not
   this Run's execution. They still pass Session identity validation, but they
   never enter per-event execution evidence, ``PermissionBridge`` tool
   accounting, tool-call closure, or ``final_message``. All that is retained is
   one bounded summary carrying safe aggregate counts.

2. **Current-Turn delivery is bounded backpressure, not immediate failure.** A
   healthy high-frequency Turn must survive a temporarily full evidence queue
   by waiting in wire order while the queue grows through its approved
   capacities — never by dropping an event, never by an unbounded buffer, and
   never by hiding a stalled consumer.

The AGENT here is the in-repo fake ACP agent driven through the real
``RunTask`` vertical: real subprocess, real SDK wire, real event writer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus

from tests.native_acp.test_session_switching import (
    HAPPY_SWITCH_SCRIPT,
    SwitchHarness,
    _events,
    _request,
)

# Comfortably past the old fixed 256-slot evidence queue, and past the 1024
# initial capacity of the dynamic policy, so the burst provably exercises
# growth rather than a bigger constant.
BURST = 1500

REPLAY_SUMMARY_EVENT = "session_replay_summary"


def _result_payload(harness: SwitchHarness, run_id: str) -> dict:
    path = harness.root / "native-runs" / run_id / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


def _summary(events: list[dict]) -> dict:
    summaries = [
        event for event in events if event["type"] == REPLAY_SUMMARY_EVENT
    ]
    assert len(summaries) == 1, f"expected exactly one replay summary: {summaries}"
    return summaries[0]


# -- 0. the replay tally key space is source-owned ---------------------------


def test_replay_tally_keys_cover_the_locked_sdk_update_set() -> None:
    """The tally key space is written out in source, and must stay complete.

    Bucketing under ``other`` is the safety net that keeps agent-authored text
    out of evidence, not a place for kinds the locked SDK actually defines. An
    SDK upgrade that widens the closed ``sessionUpdate`` set has to be answered
    here deliberately, so it fails loudly instead of quietly collapsing a real
    kind into ``other``.
    """
    import typing

    from acp import schema

    from agent_run_supervisor.native_acp.run_task import REPLAY_UPDATE_KINDS

    sdk_kinds: set[str] = set()
    for member in typing.get_args(
        schema.SessionNotification.model_fields["update"].annotation
    ):
        field = getattr(member, "model_fields", {}).get("session_update")
        if field is None:
            continue
        options = typing.get_args(field.annotation) or (field.annotation,)
        sdk_kinds.update(value for value in options if isinstance(value, str))

    assert sdk_kinds, "could not read the SDK's closed sessionUpdate set"
    assert set(REPLAY_UPDATE_KINDS) == sdk_kinds
    assert "other" not in REPLAY_UPDATE_KINDS  # the bucket is not a kind
    assert list(REPLAY_UPDATE_KINDS) == sorted(REPLAY_UPDATE_KINDS)


def test_an_unregistered_replay_kind_aggregates_under_other() -> None:
    """A kind outside the source-owned set never becomes a tally key."""
    from agent_run_supervisor.native_acp.run_task import _ReplayLedger

    ledger = _ReplayLedger()
    ledger.record({"sessionUpdate": "tool_call"})
    ledger.record({"sessionUpdate": "a_kind_the_agent_invented"})
    ledger.record({"sessionUpdate": None})
    ledger.record({})

    assert ledger.updates == 4
    assert ledger.by_kind == {"tool_call": 1, "other": 3}
    assert ledger.summary_event() == {
        "type": REPLAY_SUMMARY_EVENT,
        "updates": 4,
        "by_kind": {"other": 3, "tool_call": 1},
    }


# -- 1. replay separation ----------------------------------------------------


def test_load_replay_burst_beyond_the_old_queue_completes_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large ``session/load`` history replay must not fail the reuse Run.

    The adapter replays far more updates than a fixed evidence queue could
    hold. Replay is not this Run's execution evidence, so it must cost the Run
    nothing at all — no overflow, no ``EVIDENCE_PIPELINE``, no lost prompt.
    """
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["replay_burst"] = {"count": BURST, "text": "REPLAYED HISTORY "}
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.COMPLETED
    payload = _result_payload(harness, "run-0002")
    assert payload["detail_code"] is None
    assert payload["final_message"] == "RUN2_OK"


def test_replay_never_enters_per_event_evidence_and_is_summarized_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay leaves one bounded summary, never one event per replayed frame."""
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["replay_on_load"] = ["HISTORY_ONE ", "HISTORY_TWO "]
    script["replay_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "old-1",
            "title": "previous turn read",
            "kind": "read",
        },
        {"sessionUpdate": "tool_call_update", "toolCallId": "old-1", "status": "completed"},
    ]
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))
    assert result.status is AgentRunStatus.COMPLETED

    events = _events(harness, "run-0002")
    summary = _summary(events)
    assert summary["updates"] == 4
    assert summary["by_kind"] == {
        "agent_message_chunk": 2,
        "tool_call": 1,
        "tool_call_update": 1,
    }

    # The current Turn's own delta is the only assistant delta in the stream:
    # replayed history contributed no per-event execution evidence.
    deltas = [event for event in events if event["type"] == "agent_message_delta"]
    assert [event.get("text_length") for event in deltas] == [len("RUN2_OK")]
    # Nor any tool lifecycle evidence: those tool calls belong to earlier Runs.
    assert not [
        event for event in events if event["type"].startswith("tool_")
    ]
    payload = _result_payload(harness, "run-0002")
    assert payload["final_message"] == "RUN2_OK"


def test_replay_tool_lifecycle_records_do_not_affect_current_run_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dangling, duplicate, and orphan replayed tool records close nothing.

    Real adapters replay tool records whose lifecycle is not well formed from
    this Run's point of view: a call that never completes, the same id twice,
    and a completion for a call this Run never saw start. None of it may bind
    to the current Run's tool-call closure or change its terminal.
    """
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["replay_updates"] = [
        # dangling: starts, never completes
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "dangling-1",
            "title": "never completes",
            "kind": "read",
        },
        # duplicate: the same id started twice
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "dup-1",
            "title": "started twice",
            "kind": "read",
        },
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "dup-1",
            "title": "started twice",
            "kind": "read",
        },
        {"sessionUpdate": "tool_call_update", "toolCallId": "dup-1", "status": "completed"},
        {"sessionUpdate": "tool_call_update", "toolCallId": "dup-1", "status": "completed"},
        # orphan: a completion whose start this Run never observed
        {"sessionUpdate": "tool_call_update", "toolCallId": "orphan-1", "status": "completed"},
    ]
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.COMPLETED
    payload = _result_payload(harness, "run-0002")
    assert payload["detail_code"] is None
    assert payload["final_message"] == "RUN2_OK"

    events = _events(harness, "run-0002")
    assert not [event for event in events if event["type"].startswith("tool_")]
    assert _summary(events)["updates"] == 6


def test_replayed_write_family_completion_is_not_a_current_run_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replayed history must not be charged to this Run's permission account.

    The replayed conversation contains an ``edit`` tool call that completed in
    some *earlier* Run. This Run holds a read-only grant. Charging a replayed
    completion to the current Run's ``PermissionBridge`` turns every reuse of a
    Session that ever edited a file into a fabricated ``PERMISSION_VIOLATION``.
    """
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["replay_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "past-edit-1",
            "title": "edit from an earlier Run",
            "kind": "edit",
        },
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "past-edit-1",
            "status": "completed",
        },
    ]
    request = _request("kimi-for-coding/k3", "max")
    assert request.grant_capabilities == ("read",)
    result = harness.run("run-0002", script, request)

    assert result.status is AgentRunStatus.COMPLETED
    payload = _result_payload(harness, "run-0002")
    assert payload["detail_code"] is None
    events = _events(harness, "run-0002")
    assert not [
        event for event in events if event["type"] == "permission_violation"
    ]


def test_current_turn_write_family_completion_is_still_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The A4-S2 backstop keeps its teeth on the *current* Turn.

    Replay separation must narrow permission accounting to the current Turn,
    never disable it: the identical tool record, emitted during the prompt,
    still fails the Run.
    """
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["prompt_tool_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "now-edit-1",
            "title": "edit in this Turn",
            "kind": "edit",
        },
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "now-edit-1",
            "status": "completed",
        },
    ]
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.FAILED
    payload = _result_payload(harness, "run-0002")
    assert payload["detail_code"] == "PERMISSION_VIOLATION"


def test_replay_summary_is_bounded_and_carries_no_replayed_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retained summary is aggregate counts only — never replayed text."""
    sentinel = "REPLAY-CANARY-4f19"
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["replay_on_load"] = [f"{sentinel} " * 20]
    script["replay_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": sentinel,
            "title": sentinel,
            "kind": "read",
        },
    ]
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))
    assert result.status is AgentRunStatus.COMPLETED

    run_dir = harness.root / "native-runs" / "run-0002"
    assert sentinel not in (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert sentinel not in (run_dir / "result.json").read_text(encoding="utf-8")

    summary = _summary(_events(harness, "run-0002"))
    assert summary["updates"] == 2
    assert summary["by_kind"] == {"agent_message_chunk": 1, "tool_call": 1}
    # Bounded shape: aggregate counts and nothing else.
    assert set(summary) == {"type", "updates", "by_kind", "seq"}


# -- 2. current-Turn bounded backpressure ------------------------------------


def test_current_turn_burst_preserves_wire_order_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A high-frequency current Turn keeps every event, in wire ordinal order.

    Each burst update carries its own ordinal in ``toolCallId``, so this pins
    order — not merely a count — across the queue growth the burst forces.
    """
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["prompt_update_burst"] = {"count": BURST, "prefix": "burst-"}
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.COMPLETED
    payload = _result_payload(harness, "run-0002")
    assert payload["detail_code"] is None
    assert payload["final_message"] == "RUN2_OK"

    events = _events(harness, "run-0002")
    burst = [
        event
        for event in events
        if event["type"] == "tool_updated"
        and str(event.get("tool_call_id", "")).startswith("burst-")
    ]
    assert [event["tool_call_id"] for event in burst] == [
        f"burst-{ordinal}" for ordinal in range(BURST)
    ]
    # Monotonic seq across the burst: nothing was reordered or dropped.
    sequences = [event["seq"] for event in burst]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


def test_temporarily_slow_persistence_waits_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow-but-progressing evidence sink is backpressure, not a failure."""
    import time

    from agent_run_supervisor.event_store import RunHandle

    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    original = RunHandle.append_text
    state = {"calls": 0}

    def slow_append(self, name, value):
        state["calls"] += 1
        if state["calls"] <= 400:
            # Real per-append latency in the writer's worker thread: the
            # producer side must ride this out rather than overflow.
            time.sleep(0.001)
        return original(self, name, value)

    monkeypatch.setattr(RunHandle, "append_text", slow_append)

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["prompt_update_burst"] = {"count": BURST, "prefix": "slow-"}
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.COMPLETED
    payload = _result_payload(harness, "run-0002")
    assert payload["detail_code"] is None
    burst = [
        event["tool_call_id"]
        for event in _events(harness, "run-0002")
        if event["type"] == "tool_updated"
        and str(event.get("tool_call_id", "")).startswith("slow-")
    ]
    assert burst == [f"slow-{ordinal}" for ordinal in range(BURST)]


def test_failed_persistence_reaches_bounded_evidence_pipeline_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead evidence consumer must fail the Run, never be waited out."""
    from agent_run_supervisor.event_store import RunHandle

    harness = SwitchHarness(tmp_path, monkeypatch)
    # The sink is broken only for the Run under test; the seeding Run needs a
    # working one, or the fixture would prove nothing about this Run.
    harness.prepare_session()

    original = RunHandle.append_text
    state = {"calls": 0}

    def failing_append(self, name, value):
        state["calls"] += 1
        if state["calls"] > 3:
            raise OSError("injected evidence sink failure")
        return original(self, name, value)

    monkeypatch.setattr(RunHandle, "append_text", failing_append)

    script = dict(HAPPY_SWITCH_SCRIPT)
    script["prompt_update_burst"] = {"count": 200, "prefix": "dead-"}
    result = harness.run("run-0002", script, _request("kimi-for-coding/k3", "max"))

    assert result.status is AgentRunStatus.FAILED
    payload = _result_payload(harness, "run-0002")
    assert payload["detail_code"] == "EVIDENCE_PIPELINE"


# -- 3. Session semantics stay intact under replay ---------------------------


def test_multi_run_continuity_survives_replay_on_every_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay on each reuse keeps the Session bound, unquarantined, reusable."""
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    for index, run_id in enumerate(("run-0002", "run-0003", "run-0004")):
        script = dict(HAPPY_SWITCH_SCRIPT)
        script["replay_burst"] = {"count": 300 * (index + 1), "text": "H "}
        result = harness.run(run_id, script, _request("kimi-for-coding/k3", "max"))
        assert result.status is AgentRunStatus.COMPLETED, run_id
        assert "session/load" in harness.methods(run_id)
        assert "session/new" not in harness.methods(run_id)
        assert _summary(_events(harness, run_id))["updates"] == 300 * (index + 1)

    record = harness.record()
    assert record.agent_session_id == "fake-external-session-1"
    assert record.quarantine is None


def test_interleaved_sessions_keep_their_own_replay_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two Sessions replaying at once never mix evidence or final messages."""
    harness = SwitchHarness(tmp_path, monkeypatch)
    harness.prepare_session()

    script_a = dict(HAPPY_SWITCH_SCRIPT)
    script_a["replay_burst"] = {"count": 40, "text": "A "}
    script_a["final_message"] = "SESSION_A_OK"
    script_b = dict(HAPPY_SWITCH_SCRIPT)
    script_b["replay_burst"] = {"count": 400, "text": "B "}
    script_b["final_message"] = "SESSION_B_OK"

    request_a = _request("kimi-for-coding/k3", "max", session_id="sess-switch-1")
    request_b = _request("kimi-for-coding/k3", "max", session_id="sess-switch-2")

    assert harness.run("run-a1", script_a, request_a).status is AgentRunStatus.COMPLETED
    assert harness.run("run-b1", script_b, request_b).status is AgentRunStatus.COMPLETED
    assert harness.run("run-a2", script_a, request_a).status is AgentRunStatus.COMPLETED

    assert _summary(_events(harness, "run-a1"))["updates"] == 40
    assert _summary(_events(harness, "run-b1"))["updates"] == 400
    assert _summary(_events(harness, "run-a2"))["updates"] == 40
    assert _result_payload(harness, "run-a2")["final_message"] == "SESSION_A_OK"
    assert _result_payload(harness, "run-b1")["final_message"] == "SESSION_B_OK"

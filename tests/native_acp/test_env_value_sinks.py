"""L2: the environment-value echo matrix over every ARS-owned sink (A16).

One rule, proven per sink row of V4 §6.3.3: an operator-declared environment
value may reach the child, and nothing the child does with it may leave a
complete literal — or a length, prefix, suffix, or equality token derived from
it — in anything ARS owns. Each fixture hands the child a unique sentinel
through the real spawn environment, asks it to echo that sentinel back through
one specific surface, and then scans the *complete* new Run tree, the captured
log records, and the daemon's own event page projection.

Two documented exclusions, both deliberate and both staged:

* ``launch.json`` still serializes ``permission_env``/``fixed_env`` pairs.
  Making launch material structurally value-blind is Stage 3 (B2b) and depends
  on the new launch schema, so this stage guards the *dynamic* sinks and
  leaves the structured launch snapshot exactly as it was.
* ``spec.json``'s workspace fields keep their complete literal text by design
  (reviewer note 5). ``test_workspace_fields_not_guarded`` pins that
  positively; nothing here relies on it.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.driver import (
    SESSION_EXTERNAL_ID_SENSITIVE_COLLISION,
)
from agent_run_supervisor.native_acp.profile import ProfileRegistry
from agent_run_supervisor.redaction import (
    ENV_VALUE_REPLACEMENT,
    GUARDED_TEXT_WITHHELD,
)

from .test_run_task import HAPPY_SCRIPT, Harness, _request, _run, _test_profile

SENTINEL_NAME = "ARS_ENV_SINK_SENTINEL"
SENTINEL_NAME_B = "ARS_ENV_SINK_SENTINEL_B"
ALLOWLIST = (
    "PATH",
    "HOME",
    "FAKE_AGENT_SCRIPT",
    "FAKE_AGENT_TRACE",
    SENTINEL_NAME,
    SENTINEL_NAME_B,
)

# Stage 2 guards dynamic sinks; the structured launch snapshot is Stage 3.
TREE_SCAN_EXCLUDED = {"launch.json"}

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "agent_run_supervisor"


def _harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: dict,
    sentinel: str,
    sentinel_b: str | None = None,
) -> Harness:
    monkeypatch.setenv(SENTINEL_NAME, sentinel)
    if sentinel_b is not None:
        monkeypatch.setenv(SENTINEL_NAME_B, sentinel_b)
    else:
        monkeypatch.delenv(SENTINEL_NAME_B, raising=False)
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((_test_profile(env_allowlist=ALLOWLIST),))
    return harness


def _tree_bytes(run_dir: Path) -> bytes:
    """Every byte of every file ARS wrote for this Run, minus the exclusions."""
    chunks: list[bytes] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name in TREE_SCAN_EXCLUDED:
            continue
        chunks.append(path.name.encode("utf-8"))
        chunks.append(path.read_bytes())
    return b"\n".join(chunks)


def _assert_no_sentinel(run_dir: Path, sentinel: str) -> None:
    raw = _tree_bytes(run_dir)
    assert sentinel.encode("utf-8") not in raw
    assert os.fsencode(sentinel) not in raw


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        formatter = logging.Formatter("%(name)s|%(levelname)s|%(message)s")
        return "\n".join(formatter.format(record) for record in self.records)


@pytest.fixture()
def captured_logs():
    """A real root handler installed before the Run, like ``arsd``'s own.

    The guard binding re-applies the containment filter to every root handler
    it finds, so a handler registered here is covered for the Run's lifetime.
    """
    root = logging.getLogger()
    handler = _Capture()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        for item in list(handler.filters):
            handler.removeFilter(item)


# -- row 1: final message + accumulator ------------------------------------


def test_final_message_sentinel_never_reaches_the_run_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sink-final-message-4a91-value"
    script = dict(HAPPY_SCRIPT)
    script["echo_env"] = SENTINEL_NAME
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    # The child really did echo it: the surrounding shape survives, the value
    # does not. Erasure is visible, not silent.
    assert payload["final_message"] == f"ENV:{ENV_VALUE_REPLACEMENT}"
    _assert_no_sentinel(harness.run_dir(), sentinel)


def test_final_message_split_across_chunks_is_caught_before_accumulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The rolling carry exists for exactly this: neither chunk contains the
    # value, so a per-chunk matcher would retain both halves verbatim.
    sentinel = "sink-split-across-two-frames-77c2"
    head, tail = sentinel[:12], sentinel[12:]
    script = dict(HAPPY_SCRIPT)
    script["final_message"] = ""
    script["final_message_chunks"] = [f"before {head}", f"{tail} after"]
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert sentinel not in payload["final_message"]
    assert "before " in payload["final_message"]
    assert " after" in payload["final_message"]
    _assert_no_sentinel(harness.run_dir(), sentinel)


# -- row 2: normalized events, dynamic keys and values ---------------------


def test_events_dynamic_keys_and_values_are_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sink-event-dynamic-field-31bd"
    script = dict(HAPPY_SCRIPT)
    script["prompt_tool_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": f"call-{sentinel}",
            "title": sentinel,
            "kind": "read",
            "status": "pending",
        },
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": f"call-{sentinel}",
            "status": "completed",
        },
    ]
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    events = [
        json.loads(line)
        for line in (harness.run_dir() / "events.jsonl").read_text().splitlines()
    ]
    started = [event for event in events if event.get("type") == "tool_started"]
    assert started, "the tool_started family must survive; only the value goes"
    assert ENV_VALUE_REPLACEMENT in started[0]["tool_call_id"]
    _assert_no_sentinel(harness.run_dir(), sentinel)


def test_a_chunk_containing_the_value_withholds_its_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``text_length`` is structural for ordinary chatter, but for a chunk that
    # contains a projected value it is a length-by-value.
    sentinel = "sink-length-by-value-8ee0"
    script = dict(HAPPY_SCRIPT)
    script["echo_env"] = SENTINEL_NAME
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    _run(harness.task())

    events = [
        json.loads(line)
        for line in (harness.run_dir() / "events.jsonl").read_text().splitlines()
    ]
    deltas = [event for event in events if event.get("type") == "agent_message_delta"]
    assert deltas
    withheld = [event for event in deltas if event.get("text_length_withheld")]
    assert withheld, "the value-bearing chunk must withhold its length"
    for event in withheld:
        assert "text_length" not in event


# -- row 3: permission and filesystem evidence -----------------------------


def test_permission_evidence_is_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sink-permission-toolcall-5c04"
    script = dict(HAPPY_SCRIPT)
    # ``toolCallId`` is free-form child text and mediation evidence records it
    # verbatim so a decision can be correlated with the call it answered.
    # ``edit`` is denied by the read-only grant, so the ask is really mediated.
    script["ask_permission"] = {
        "kind": "edit",
        "tool_call_id": f"perm-{sentinel}",
    }
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    _run(harness.task())

    events = [
        json.loads(line)
        for line in (harness.run_dir() / "events.jsonl").read_text().splitlines()
    ]
    mediation = [
        event for event in events if event.get("type") == "permission_mediation"
    ]
    assert mediation, "the mediation decision must still be recorded"
    assert any(event.get("decision") == "deny" for event in mediation)
    _assert_no_sentinel(harness.run_dir(), sentinel)


def test_filesystem_refusal_never_logs_the_child_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_logs
) -> None:
    # The fs path a child asks for reaches the SDK's own failure logging, not
    # an ARS artifact — which is precisely why the log boundary is a sink.
    sentinel = "sink-fs-path-6b17"
    script = dict(HAPPY_SCRIPT)
    script["fs_read_path"] = f"/nonexistent/{sentinel}/target.txt"
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    _run(harness.task())

    _assert_no_sentinel(harness.run_dir(), sentinel)
    assert sentinel not in captured_logs.text()


# -- row 4: effective.json + initialize/discovery evidence -----------------


def test_effective_and_initialize_evidence_are_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sink-agent-version-0d5f"
    script = dict(HAPPY_SCRIPT)
    script["agent_info"] = {"name": "fake-acp-agent", "version": sentinel}
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    effective = json.loads((harness.run_dir() / "effective.json").read_text())
    assert effective["agent_info"]["version"] == ENV_VALUE_REPLACEMENT
    attestation = json.loads(
        (harness.run_dir() / "initialize_attestation.json").read_text()
    )
    assert attestation["observed"]["agent_info_version"] == ENV_VALUE_REPLACEMENT
    _assert_no_sentinel(harness.run_dir(), sentinel)


# -- row 5: the external Session id that cannot be redacted ----------------


def test_external_session_id_collision_refuses_before_any_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sink-external-session-id-9a20"
    script = dict(HAPPY_SCRIPT)
    script["session_id"] = sentinel
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(
        harness.task(request=_request(session_reuse="none", ars_session_id=None))
    )

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == SESSION_EXTERNAL_ID_SENSITIVE_COLLISION
    # Refusal is categorical, and nothing downstream of the check ran.
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    assert "session/prompt" not in harness.methods_seen()
    record = harness.session_store().open_session("run-0001-ephemeral")
    assert record.agent_session_id is None
    _assert_no_sentinel(harness.run_dir(), sentinel)


def test_a_one_character_environment_value_still_refuses_the_external_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact invariant 4, at the smallest value that exists.

    Short, common values are the ones an implementer is tempted to waive, and
    a single character appears in almost every id an agent will ever mint. The
    refusal is unconditional: there is no minimum length, no inconvenience
    waiver, and no exemption for a frozen source-owned pair. A profile whose
    mediation or fixed environment declares ``"1"`` really does make every
    external id containing ``1`` unusable — which is the operator-visible
    tradeoff, not a bug to soften here.
    """
    sentinel = "7"
    script = dict(HAPPY_SCRIPT)
    script["session_id"] = "agent-session-7f"
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(
        harness.task(request=_request(session_reuse="none", ars_session_id=None))
    )

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == SESSION_EXTERNAL_ID_SENSITIVE_COLLISION
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    assert harness.session_store().open_session(
        "run-0001-ephemeral"
    ).agent_session_id is None


# -- row 6: stderr, bytes before decode then text --------------------------


def test_stderr_text_is_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sink-stderr-text-2f6c"
    script = dict(HAPPY_SCRIPT)
    script["stderr_text"] = f"agent warning: {sentinel}\n"
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    stderr_log = (harness.run_dir() / "stderr.log").read_text()
    assert sentinel not in stderr_log
    assert ENV_VALUE_REPLACEMENT in stderr_log or GUARDED_TEXT_WITHHELD in stderr_log
    _assert_no_sentinel(harness.run_dir(), sentinel)


def test_stderr_bytes_are_guarded_before_any_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Undecodable neighbours mean ``errors="replace"`` would otherwise be the
    # first thing to see these bytes, long after they were retained.
    sentinel = "sink-stderr-býtes-4d8a"
    raw = b"\xff\xfe" + os.fsencode(sentinel) + b"\x80\n"
    script = dict(HAPPY_SCRIPT)
    script["stderr_raw_hex"] = raw.hex()
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    stderr_bytes = (harness.run_dir() / "stderr.log").read_bytes()
    assert os.fsencode(sentinel) not in stderr_bytes
    assert sentinel.encode("utf-8") not in stderr_bytes
    _assert_no_sentinel(harness.run_dir(), sentinel)


# -- row 8: exceptions, spawn errors, cleanup errors ------------------------


def test_spawn_error_projection_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_logs
) -> None:
    sentinel = "sink-spawn-error-7e13"
    harness = _harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT), sentinel)

    async def boom(**kwargs):
        del kwargs
        raise OSError(2, "No such file or directory", f"/bin/{sentinel}")

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.spawn_managed_process", boom
    )

    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == "SPAWN_FAILED"
    assert payload["failure_reason"] == "spawn failed"
    assert sentinel not in json.dumps(payload)
    assert sentinel not in captured_logs.text()
    _assert_no_sentinel(harness.run_dir(), sentinel)


def test_run_exception_projection_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_logs
) -> None:
    sentinel = "sink-run-exception-1c48"
    harness = _harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT), sentinel)

    async def boom(self, text: str):
        del self, text
        raise RuntimeError(f"hostile detail {sentinel}")

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.driver.NativeAcpDriver.prompt_once", boom
    )

    result = _run(harness.task())

    assert result.status in (AgentRunStatus.FAILED, AgentRunStatus.UNKNOWN)
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert sentinel not in json.dumps(payload)
    assert sentinel not in captured_logs.text()
    _assert_no_sentinel(harness.run_dir(), sentinel)


def test_cleanup_error_projection_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_logs
) -> None:
    sentinel = "sink-cleanup-error-88fa"
    harness = _harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT), sentinel)

    from agent_run_supervisor.native_acp.event_writer import EventWriter

    async def boom_close(self) -> None:
        del self
        raise RuntimeError(f"evidence close failed near {sentinel}")

    monkeypatch.setattr(EventWriter, "close", boom_close)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert sentinel not in json.dumps(payload)
    assert sentinel not in captured_logs.text()
    _assert_no_sentinel(harness.run_dir(), sentinel)


# -- row 10: the daemon's own live projection ------------------------------


def test_daemon_event_page_projection_carries_no_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.arsd import handlers

    sentinel = "sink-event-page-2b7d"
    script = dict(HAPPY_SCRIPT)
    script["echo_env"] = SENTINEL_NAME
    script["prompt_tool_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": sentinel,
            "kind": "read",
            "status": "pending",
        }
    ]
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    _run(harness.task())

    events, _next_seq, _exhausted = handlers._read_events_page(
        harness.run_dir(), from_seq=0, limit=handlers.MAX_EVENTS_PAGE_LIMIT
    )
    assert events
    assert sentinel not in json.dumps(events)


# -- B1: recomposition across the final serializer -------------------------


def test_a_value_recomposed_by_the_event_serializer_never_reaches_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two projected values, only one of which any field ever contained.

    ``A`` sits in a child field and is replaced. ``B`` exists nowhere in the
    record: it is created when the replacement token meets the JSON separator
    and the next source-owned key, at ``json.dumps`` time, after every
    field-level matcher has already run.
    """
    sentinel = "recompose-tool-call-a"
    recomposed = 'ED]", "type"'
    script = dict(HAPPY_SCRIPT)
    script["prompt_tool_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": sentinel,
            "title": "recomposition probe",
            "kind": "read",
            "status": "pending",
        }
    ]
    harness = _harness(tmp_path, monkeypatch, script, sentinel, recomposed)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    raw = _tree_bytes(harness.run_dir())
    assert sentinel.encode() not in raw
    assert recomposed.encode() not in raw
    events = [
        json.loads(line)
        for line in (harness.run_dir() / "events.jsonl").read_text().splitlines()
    ]
    sequences = [event["seq"] for event in events]
    assert sequences == list(range(1, len(sequences) + 1))


# -- B3: option ids are exact child protocol identifiers -------------------


def test_a_colliding_permission_option_id_denies_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "wire-option-id-sentinel-5a72"
    choice = tmp_path / "selected-option.txt"
    written = tmp_path / "workspace" / "ask-target.txt"
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {
        "kind": "read",
        "path": str(written),
        "content": "ALLOWED_CANARY",
        "choice_path": str(choice),
        "allow_option_ids": [sentinel],
        "options": [
            {"optionId": sentinel, "name": "Allow once", "kind": "allow_once"},
            {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
        ],
    }
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    _run(harness.task())

    payload = json.loads((harness.run_dir() / "result.json").read_text())
    # Fail closed: the only once-scoped option the agent offered cannot be
    # selected without replaying a projected value, so nothing is allowed.
    assert payload["final_message"] == "ASK_DENIED"
    assert choice.read_text(encoding="utf-8") == ""
    assert not written.exists()
    _assert_no_sentinel(harness.run_dir(), sentinel)


# -- B4: an allowed read that fails must not carry the path ----------------


def test_an_allowed_filesystem_read_failure_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal path was already categorical; the *allowed* path was not.

    A workspace-contained path passes mediation and is then handed to
    ``read_text``, whose ``OSError`` names the absolute path verbatim. That
    exception is what ARS hands the SDK, which renders it into a protocol
    error and a log record — an ARS-formulated projection of child-chosen,
    environment-bearing text.
    """
    from agent_run_supervisor.native_acp.permissions import PermissionBridge
    from agent_run_supervisor.native_acp.run_task import _RunContext
    from agent_run_supervisor.redaction import RunTextGuard

    sentinel = "fs-read-path-sentinel-6e30"
    harness = _harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT), sentinel)
    task = harness.task()
    guard = RunTextGuard.from_environment({SENTINEL_NAME: sentinel})
    ctx = _RunContext()
    ctx.guard = guard
    ctx.bridge = PermissionBridge(
        capabilities=("read",),
        workspace_root=harness.workspace,
        evidence_sink=lambda event: None,
        guard=guard,
    )
    handler = task._fs_read_handler(ctx)
    missing = harness.workspace / sentinel / "missing.txt"

    with pytest.raises(PermissionError) as excinfo:
        asyncio.run(handler({"path": str(missing)}))

    text = str(excinfo.value)
    assert sentinel not in text
    assert str(missing) not in text
    assert "No such file" not in text
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_an_allowed_filesystem_read_still_returns_its_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.permissions import PermissionBridge
    from agent_run_supervisor.native_acp.run_task import _RunContext
    from agent_run_supervisor.redaction import RunTextGuard

    sentinel = "fs-read-ok-sentinel-77aa"
    harness = _harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT), sentinel)
    task = harness.task()
    guard = RunTextGuard.from_environment({SENTINEL_NAME: sentinel})
    ctx = _RunContext()
    ctx.guard = guard
    ctx.bridge = PermissionBridge(
        capabilities=("read",),
        workspace_root=harness.workspace,
        evidence_sink=lambda event: None,
        guard=guard,
    )
    readable = harness.workspace / "readable.txt"
    readable.write_text("workspace content", encoding="utf-8")

    content = asyncio.run(task._fs_read_handler(ctx)({"path": str(readable)}))

    assert content == "workspace content"


def test_an_undecodable_allowed_read_is_also_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.permissions import PermissionBridge
    from agent_run_supervisor.native_acp.run_task import _RunContext
    from agent_run_supervisor.redaction import RunTextGuard

    sentinel = "fs-decode-sentinel-31c9"
    harness = _harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT), sentinel)
    task = harness.task()
    guard = RunTextGuard.from_environment({SENTINEL_NAME: sentinel})
    ctx = _RunContext()
    ctx.guard = guard
    ctx.bridge = PermissionBridge(
        capabilities=("read",),
        workspace_root=harness.workspace,
        evidence_sink=lambda event: None,
        guard=guard,
    )
    binary = harness.workspace / f"{sentinel}.bin"
    binary.write_bytes(b"\xff\xfe\x00binary")

    with pytest.raises(PermissionError) as excinfo:
        asyncio.run(task._fs_read_handler(ctx)({"path": str(binary)}))

    assert sentinel not in str(excinfo.value)
    assert "utf-8" not in str(excinfo.value)


# -- B5: usage is guarded before it is bounded ------------------------------


def test_usage_is_guarded_before_it_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounding a raw value first turns its *length* into persisted evidence.

    ``sanitize_usage`` decides truncation from the serialized size of the raw
    object. A long projected value therefore selects the truncation branch and
    ``max_usage_bytes`` lands in the terminal — a fact derived from the value's
    length, which is exactly the metadata class the boundary forbids. Guarded
    first, the same value is a short fixed token and no size decision is even
    reachable.
    """
    from agent_run_supervisor.result import MAX_USAGE_SERIALIZED_BYTES

    sentinel = "usage-sentinel-" + ("u" * (MAX_USAGE_SERIALIZED_BYTES + 500))
    script = dict(HAPPY_SCRIPT)
    script["usage"] = {
        "totalTokens": 30,
        "inputTokens": 20,
        "outputTokens": 10,
        "_meta": {"vendor_note": sentinel},
    }
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    usage = payload["usage"]
    assert usage["_meta"]["vendor_note"] == ENV_VALUE_REPLACEMENT
    assert usage["totalTokens"] == 30
    assert "truncate_reason" not in usage
    assert "max_usage_bytes" not in json.dumps(payload)
    _assert_no_sentinel(harness.run_dir(), sentinel)


# -- structural: the spawn seam never formats the mapping ------------------


def test_managed_process_never_formats_the_environment_mapping() -> None:
    """The one module that holds the exec mapping must never render it.

    A source rule rather than a runtime one: an f-string or ``format`` over the
    ``env`` parameter would put every value into an exception message that no
    guard is positioned to see, because it is raised before the Run's guard is
    reachable from that frame.
    """
    tree = ast.parse((SRC_ROOT / "managed_process.py").read_text(encoding="utf-8"))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for value in ast.walk(node):
                if isinstance(value, ast.Name) and value.id == "env":
                    offenders.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"format", "join"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "env"
        ):
            offenders.append(node.lineno)
    assert offenders == []


def test_native_run_task_writes_free_form_text_only_through_the_guarded_seam() -> None:
    """``run_task`` must not reach ``RunHandle.write_text`` directly.

    ``storage.write_run_text`` is the seam that refuses an unguarded ``str``;
    calling the raw handle method would route around the type barrier while
    looking identical at the call site.
    """
    tree = ast.parse(
        (SRC_ROOT / "native_acp" / "run_task.py").read_text(encoding="utf-8")
    )
    offenders: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
        ):
            offenders.append(node.lineno)
    assert offenders == []


def test_unguarded_str_is_refused_at_the_free_form_storage_seam(
    tmp_path: Path,
) -> None:
    class _Handle:
        def write_text(self, name: str, value: str) -> Path:  # pragma: no cover
            raise AssertionError("the refusal must happen before the write")

    with pytest.raises(TypeError):
        storage.write_run_text(_Handle(), "stderr.log", "raw child text")

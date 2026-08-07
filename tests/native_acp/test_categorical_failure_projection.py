"""L2: ARS-formulated failure text is categorical, and the seams stay enumerable.

These rows survive the removal of the per-Run literal guard because none of
them ever depended on it. A spawn errno, an SDK exception, a cleanup failure,
and an allowed-but-failing workspace read are all *ARS-formulated* projections:
ARS chooses the words, so ARS keeps them stable codes instead of interpolating
OS text, SDK text, or a child-chosen path into a terminal, an event, or a log
record.

That is a different claim from "no environment value is ever retained", which
this product no longer makes: what an AGENT deliberately echoes back through
free-form Run text is retained, and ``test_projected_value_retention`` pins
exactly that.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.result import ALLOWED_FAILURE_REASONS

from .test_run_task import HAPPY_SCRIPT, Harness, _run

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "agent_run_supervisor"


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
    """A real root handler, like the one ``arsd`` installs."""
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


# -- spawn / run / cleanup exceptions ---------------------------------------


def test_spawn_error_projection_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_logs
) -> None:
    marker = "spawn-error-7e13"
    harness = Harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT))

    async def boom(**kwargs):
        del kwargs
        raise OSError(2, "No such file or directory", f"/bin/{marker}")

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.spawn_managed_process", boom
    )

    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == "SPAWN_FAILED"
    assert payload["failure_reason"] == "spawn failed"
    # The OS error text names the declared image path; the terminal names the
    # stable code and nothing else.
    assert marker not in json.dumps(payload)
    assert marker not in captured_logs.text()


def test_run_exception_projection_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_logs
) -> None:
    marker = "run-exception-1c48"
    harness = Harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT))

    async def boom(self, text: str):
        del self, text
        raise RuntimeError(f"hostile detail {marker}")

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.driver.NativeAcpDriver.prompt_once", boom
    )

    result = _run(harness.task())

    assert result.status in (AgentRunStatus.FAILED, AgentRunStatus.UNKNOWN)
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert marker not in json.dumps(payload)
    assert marker not in captured_logs.text()


def test_cleanup_error_projection_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_logs
) -> None:
    marker = "cleanup-error-88fa"
    harness = Harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT))

    from agent_run_supervisor.native_acp.event_writer import EventWriter

    async def boom_close(self) -> None:
        del self
        raise RuntimeError(f"evidence close failed near {marker}")

    monkeypatch.setattr(EventWriter, "close", boom_close)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert marker not in json.dumps(payload)
    assert marker not in captured_logs.text()



def test_quarantined_reuse_projection_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_logs
) -> None:
    """A refused quarantined reuse names its code, never its evidence.

    The refusal is raised with a message that interpolates the Session id, and
    the durable evidence carries the id of the Run that quarantined it, the
    moment it did, and the reason code it did it under. None of that is
    caller-facing: the terminal carries the stable code and one fixed
    categorical reason, and the Session id it already publishes on its own.
    """
    from agent_run_supervisor.session import QUARANTINE_DISPATCH_WITHOUT_TERMINAL

    marker = "quarantine-source-6f2a"
    harness = Harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT))
    task = harness.task()
    store = harness.session_store()
    store.mark_quarantined(
        "sess-native-1",
        reason_code=QUARANTINE_DISPATCH_WITHOUT_TERMINAL,
        run_id=marker,
    )
    evidence = store.open_session("sess-native-1").quarantine
    assert evidence is not None

    result = _run(task)

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == "SESSION_QUARANTINED"
    assert payload["retryable"] is False
    # Allow-listed, so it cannot be exception text however it was built.
    assert payload["failure_reason"] in ALLOWED_FAILURE_REASONS
    assert payload["failure_reason"] == "run failed"

    serialized = json.dumps(payload)
    assert marker not in serialized
    assert evidence["recorded_at"] not in serialized
    assert QUARANTINE_DISPATCH_WITHOUT_TERMINAL not in serialized
    assert "is quarantined" not in serialized
    assert marker not in captured_logs.text()


# -- the allowed filesystem read path ---------------------------------------


def _fs_handler(harness: Harness, capabilities=("read",)):
    from agent_run_supervisor.native_acp.permissions import PermissionBridge
    from agent_run_supervisor.native_acp.run_task import _RunContext

    task = harness.task()
    ctx = _RunContext()
    ctx.bridge = PermissionBridge(
        capabilities=capabilities,
        workspace_root=harness.workspace,
        evidence_sink=lambda event: None,
    )
    return task._fs_read_handler(ctx)


def test_an_allowed_filesystem_read_failure_is_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal path was already categorical; the *allowed* path was not.

    A workspace-contained path passes mediation and is handed to ``read_text``,
    whose ``OSError`` names the absolute path verbatim. That exception is what
    ARS hands the SDK, which renders it into a protocol error and a log record,
    so it collapses to one stable code with no cause and no context.
    """
    harness = Harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT))
    handler = _fs_handler(harness)
    missing = harness.workspace / "absent-dir" / "missing.txt"

    with pytest.raises(PermissionError) as excinfo:
        asyncio.run(handler({"path": str(missing)}))

    text = str(excinfo.value)
    assert text == "FS_READ_FAILED"
    assert str(missing) not in text
    assert "No such file" not in text
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_an_allowed_filesystem_read_still_returns_its_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT))
    readable = harness.workspace / "readable.txt"
    readable.write_text("workspace content", encoding="utf-8")

    content = asyncio.run(_fs_handler(harness)({"path": str(readable)}))

    assert content == "workspace content"


def test_an_undecodable_allowed_read_is_also_categorical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT))
    binary = harness.workspace / "binary.bin"
    binary.write_bytes(b"\xff\xfe\x00binary")

    with pytest.raises(PermissionError) as excinfo:
        asyncio.run(_fs_handler(harness)({"path": str(binary)}))

    assert str(excinfo.value) == "FS_READ_FAILED"
    assert "utf-8" not in str(excinfo.value)


# -- structural: the seams stay enumerable ----------------------------------


def test_managed_process_never_formats_the_environment_mapping() -> None:
    """The one module that holds the exec mapping must never render it.

    A source rule rather than a runtime one: an f-string or ``format`` over the
    ``env`` parameter would put every projected value into an exception message
    or a log line, which is the boundary that did *not* move.
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


def test_native_run_task_writes_free_form_text_only_through_the_storage_seam() -> None:
    """``run_task`` must not reach ``RunHandle.write_text`` directly.

    ``storage.write_run_text`` keeps the set of places that can create a
    free-form Run artifact enumerable; a raw handle call would route around it
    while looking identical at the call site.
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


def test_the_free_form_storage_seam_refuses_a_non_string() -> None:
    """``session.json`` and ``stderr.log`` are durable text.

    An object with a hostile ``__str__`` is exactly what must not reach a
    serializer, so the seam still judges the type rather than coercing it.
    """

    class _Handle:
        def write_text(self, name: str, value: str) -> Path:  # pragma: no cover
            raise AssertionError("the refusal must happen before the write")

    class _Sneaky:
        def __str__(self) -> str:  # pragma: no cover
            raise AssertionError("never rendered")

    with pytest.raises(TypeError):
        storage.write_run_text(_Handle(), "stderr.log", _Sneaky())


# -- the daemon's own live projection ---------------------------------------


def test_daemon_event_page_projection_reads_the_bounded_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.arsd import handlers

    tool_call_id = "event-page-2b7d"
    script = dict(HAPPY_SCRIPT)
    script["prompt_tool_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": tool_call_id,
            # ``title`` is required by the ACP schema: without it the SDK drops
            # the notification and the assertion below would pass vacuously.
            "title": "event page probe",
            "kind": "read",
            "status": "pending",
        }
    ]
    harness = Harness(tmp_path, monkeypatch, script)

    _run(harness.task())

    events, _next_seq, _exhausted = handlers._read_events_page(
        harness.run_dir(), from_seq=0, limit=handlers.MAX_EVENTS_PAGE_LIMIT
    )
    assert events
    assert [event["seq"] for event in events] == sorted(
        event["seq"] for event in events
    )
    assert tool_call_id in json.dumps(events)

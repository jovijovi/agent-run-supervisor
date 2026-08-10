"""Hermetic Socket API v3 acceptance for Session reuse under history replay.

The real daemon, the real UDS wire, the real caller authentication, the real
admission path, the real ``RunTask`` vertical, and the in-repo fake ACP AGENT
as a real child process. Nothing external is contacted, no service is touched,
and no model call is made: the AGENT is the repository's own scripted process.

What this proves that the L2 vertical tests cannot: a caller that only ever
speaks API v3 over the socket can create a Session, reuse it by id, and have
the reuse Run survive the AGENT replaying a large conversation history at
``session/load``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.arsd import __main__ as arsd_main
from agent_run_supervisor.arsd import client as arsd_client
from agent_run_supervisor.arsd import server
from agent_run_supervisor.native_acp import storage

from tests.native_acp import registry_fixtures as rfx

FAKE_AGENT_PATH = Path(__file__).resolve().parents[1] / "native_acp" / "fake_agent.py"
AGENT_ID = "fake-socket-agent"
EXTERNAL_SESSION_ID = "fake-external-session-1"
REPLAY_BURST = 1200

BASE_SET = [
    {
        "id": "model",
        "name": "Model",
        "type": "select",
        "currentValue": "provider/base",
        "options": [
            {"value": "provider/base", "name": "Base"},
            {"value": "kimi-for-coding/k3", "name": "K3"},
        ],
    },
    {
        "id": "effort",
        "name": "Effort",
        "type": "select",
        "currentValue": "high",
        "options": [
            {"value": "high", "name": "High"},
            {"value": "max", "name": "Max"},
        ],
    },
]


def _script(**overrides) -> dict:
    script = {
        "initial_options": BASE_SET,
        "post_model_options_by_value": {"provider/base": BASE_SET},
        "final_message": "SOCKET_RUN_OK",
    }
    script.update(overrides)
    return script


def _wire_request(**overrides) -> dict:
    request = {
        "owner": "hermes",
        "namespace": "hermes/doc-check",
        "agent_id": AGENT_ID,
        "expected_binding_hash": None,
        "input_refs": [
            {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64}
        ],
        "requested_model": "provider/base",
        "requested_effort": "high",
        "grant_ref": "grant:doc-check-1",
        "grant_hash": "sha256:" + "b" * 64,
        "grant_role_hash": "sha256:" + "c" * 64,
        "grant_capabilities": ["read"],
        "mcp_snapshot_hashes": [],
        "credential_refs": [],
        "limits": {},
        "evidence_policy_hash": "sha256:" + "d" * 64,
        "recovery_policy_hash": "sha256:" + "e" * 64,
    }
    request.update(overrides)
    if request.get("session_id") is None:
        # A create *omits* the key; an explicit null is a different, refused
        # caller statement.
        request.pop("session_id", None)
    return request


def _agents_file(conf_dir: Path) -> Path:
    conf_dir.mkdir(parents=True, exist_ok=True)
    return rfx.write_registry(
        conf_dir,
        entries={
            AGENT_ID: {
                "profile": rfx.STANDARD_PROFILE,
                "command": sys.executable,
                "args": [str(FAKE_AGENT_PATH)],
                "env_passthrough": ["FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"],
            }
        },
    )


class _Daemon:
    """serve_daemon on its own loop/thread with the real run-task factory."""

    def __init__(self, socket_path: Path, root: Path, agents_file: Path) -> None:
        self.socket_path = socket_path
        self.root = root
        self.agents_file = agents_file
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="arsd-fake-agent-acceptance", daemon=True
        )
        self._thread.start()
        assert self._ready.wait(timeout=15), "daemon thread never became ready"
        if self._error is not None:
            raise self._error
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.socket_path.exists():
                try:
                    server.probe_connect(str(self.socket_path))
                    return
                except OSError:
                    pass
            if self._error is not None:
                raise self._error
            time.sleep(0.02)
        raise AssertionError("daemon socket never became live")

    def _run(self) -> None:
        try:
            asyncio.run(self._amain())
        except BaseException as exc:  # noqa: BLE001 — surfaced to the starter
            self._error = exc
            self._ready.set()

    async def _amain(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready.set()
        await arsd_main.serve_daemon(
            socket_path=str(self.socket_path),
            supervisor_root=str(self.root),
            policy=server.CallerPolicy(
                {
                    os.getuid(): server.Principal(
                        principal_id="hermes-local",
                        owner_namespaces=frozenset({("hermes", "hermes/doc-check")}),
                    )
                }
            ),
            agents_file=str(self.agents_file),
            max_concurrent_runs=4,
            max_connections=16,
            cancel_wait_seconds=2.0,
            shutdown_timeout=10.0,
            stop_event=self._stop,
            install_signals=False,
        )

    def stop(self) -> None:
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and loop.is_running():
            loop.call_soon_threadsafe(stop.set)
        if self._thread is not None:
            self._thread.join(timeout=20)
            self._thread = None


@contextlib.contextmanager
def _running_daemon(socket_path: Path, root: Path, agents_file: Path):
    daemon = _Daemon(socket_path, root, agents_file)
    daemon.start()
    try:
        yield daemon
    finally:
        daemon.stop()


def _await_terminal(client, run_id: str, *, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.run_status(run_id)
        result = status.get("result")
        if result is not None:
            return result
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached a terminal result")


@pytest.fixture
def sock_root():
    # AF_UNIX path length is the constraint, so this lives under /tmp rather
    # than under the (much longer) pytest tmp_path.
    root = Path(tempfile.mkdtemp(prefix="arsd-fa-", dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _events(root: Path, run_id: str) -> list[dict]:
    path = root / "native-runs" / run_id / "events.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_socket_v3_session_reuse_survives_a_large_history_replay(
    sock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = sock_root / "s" / "arsd.sock"
    supervisor_root = sock_root / "sv"
    workspace = sock_root / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    agents_file = _agents_file(sock_root / "conf")
    trace = sock_root / "trace.log"

    monkeypatch.setenv("FAKE_AGENT_TRACE", str(trace))
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(_script()))

    with _running_daemon(socket_path, supervisor_root, agents_file):
        with arsd_client.ArsdClient(socket_path) as client:
            info = client.server_info()
            assert info["api_version"] == 3

            # Run 1 — create: the Session id is absent from the request.
            ack1 = client.submit(
                request_id="req-create",
                payload={
                    "request": _wire_request(),
                    "prompt_text": "remember the socket acceptance nonce",
                    "workspace_root": str(workspace),
                    "cwd": None,
                    "retry_of_run_id": None,
                },
            )
            session_id = ack1["session_id"]
            assert session_id
            result1 = _await_terminal(client, ack1["run_id"])
            assert result1["status"] == "completed", result1
            assert result1["final_message"] == "SOCKET_RUN_OK"

            # Run 2 — reuse by id, with the AGENT replaying a large history.
            monkeypatch.setenv(
                "FAKE_AGENT_SCRIPT",
                json.dumps(
                    _script(
                        replay_burst={"count": REPLAY_BURST, "text": "REPLAYED "},
                        final_message="SOCKET_REUSE_OK",
                    )
                ),
            )
            ack2 = client.submit(
                request_id="req-reuse",
                payload={
                    "request": _wire_request(session_id=session_id),
                    "prompt_text": "what did you remember?",
                    "workspace_root": str(workspace),
                    "cwd": None,
                    "retry_of_run_id": None,
                },
            )
            assert ack2["session_id"] == session_id
            result2 = _await_terminal(client, ack2["run_id"])
            assert result2["status"] == "completed", result2
            assert result2["detail_code"] is None
            assert result2["final_message"] == "SOCKET_REUSE_OK"

            session = client.session_status(session_id)
            assert session["session_id"] == session_id
            assert session.get("quarantine") is None

    methods = trace.read_text(encoding="utf-8").splitlines()
    assert "session/new" in methods
    assert "session/load" in methods

    record = storage.native_session_store(supervisor_root).open_session(session_id)
    assert record.agent_session_id == EXTERNAL_SESSION_ID
    assert record.quarantine is None

    reuse_events = _events(supervisor_root, ack2["run_id"])
    summaries = [
        event for event in reuse_events if event["type"] == "session_replay_summary"
    ]
    assert len(summaries) == 1
    assert summaries[0]["updates"] == REPLAY_BURST
    # Replay contributed one bounded summary, not one event per replayed frame.
    assert len(reuse_events) < REPLAY_BURST

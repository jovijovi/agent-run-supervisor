"""B1 — the closed ``SessionStartPlan`` union and fail-closed load-only reuse.

Stage 1 WP1.1–WP1.2 (plan §5). Every case here proves a *structural* property:
a reuse request cannot reach ``driver.new_session`` because no code path can
construct the plan type that arm matches, not because a branch happens to be
ordered defensively.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import sys
import typing
import unicodedata
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus  # noqa: E402
from agent_run_supervisor.native_acp import profile as profile_module  # noqa: E402
from agent_run_supervisor.native_acp import run_task as run_task_module  # noqa: E402
from agent_run_supervisor.native_acp import storage  # noqa: E402
from agent_run_supervisor.native_acp.driver import (  # noqa: E402
    NativeAcpDriver,
    NativeDriverError,
)
from agent_run_supervisor.native_acp.agent_registration import AgentEntry  # noqa: E402
from agent_run_supervisor.native_acp.profile import (  # noqa: E402
    AcpCompatProfile,
    ProfileRegistry,
)
from agent_run_supervisor.session import derive_session_id_for_run
from agent_run_supervisor.native_acp.run_task import (  # noqa: E402
    DISPATCH_STARTED_MARKER,
    LoadSessionPlan,
    CreateSessionPlan,
    RunTask,
    SessionStartPlan,
)
from agent_run_supervisor.native_acp.spec import (  # noqa: E402
    AgentRunRequest,
    InputRef,
    RunLimits,
    RunSpecAssembler,
)
from agent_run_supervisor.result import ALLOWED_FAILURE_REASONS  # noqa: E402
from agent_run_supervisor.session import (  # noqa: E402
    LOCK_JSON,
    QUARANTINE_DISPATCH_WITHOUT_TERMINAL,
    QUARANTINE_PENDING_JSON,
    SESSION_JSON,
    SessionStore,
)

FAKE_AGENT_PATH = Path(__file__).with_name("fake_agent.py")
RUN_TASK_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agent_run_supervisor"
    / "native_acp"
    / "run_task.py"
)

EXTERNAL_ID = "fake-external-session-1"

HAPPY_SCRIPT = {
    "initial_options": [
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
    ],
    "final_message": "FAKE_AGENT_OK",
}


def _profile(**overrides) -> AcpCompatProfile:
    kwargs = dict(
        profile_id="fake-agent-v1",
        revision=1,
        acp_protocol_version="1",
        required_capabilities=(),
        base_allowlist=("PATH", "HOME", "FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
        requires_session_load=True,
    )
    kwargs.update(overrides)
    return AcpCompatProfile(**kwargs)


def _entry(**overrides) -> AgentEntry:
    kwargs = dict(
        agent_id="fake-agent",
        profile_id="fake-agent-v1",
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
    )
    kwargs.update(overrides)
    return AgentEntry(**kwargs)


def _request(**overrides) -> AgentRunRequest:
    kwargs = dict(
        owner="hermes",
        namespace="hermes/doc-check",
        agent_id="fake-agent",
        session_id="sess-plan-1",
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),),
        requested_model="kimi-for-coding/k3",
        requested_effort="max",
        grant_ref="grant:doc-check-1",
        grant_hash="sha256:" + "b" * 64,
        grant_role_hash="sha256:" + "c" * 64,
        grant_capabilities=("read",),
        mcp_snapshot_hashes=(),
        credential_refs=(),
        limits=RunLimits(),
        evidence_policy_hash="sha256:" + "d" * 64,
        recovery_policy_hash="sha256:" + "e" * 64,
    )
    kwargs.update(overrides)
    return AgentRunRequest(**kwargs)


class Harness:
    """L2 vertical over the in-repo fake ACP agent — never a driver mock."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        script: dict | None = None,
    ) -> None:
        self.root = tmp_path / ".agent-run-supervisor"
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir(exist_ok=True)
        self.trace = tmp_path / "fake-agent-trace.log"
        monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(script or HAPPY_SCRIPT))
        monkeypatch.setenv("FAKE_AGENT_TRACE", str(self.trace))
        self.registry = ProfileRegistry((_profile(),))
        self.entry = _entry()

    def task(self, *, run_id: str = "run-plan-1", request=None, **overrides) -> RunTask:
        kwargs = dict(
            request=request or _request(),
            prompt_text="hello agent",
            run_id=run_id,
            workspace_root=self.workspace,
            registry=self.registry,
            agent_entry=self.entry,
            supervisor_root=self.root,
            submitted_at="2026-07-21T00:00:00+00:00",
        )
        kwargs.update(overrides)
        return RunTask(**kwargs)

    def session_store(self) -> SessionStore:
        return storage.native_session_store(self.root)

    def run_dir(self, run_id: str = "run-plan-1") -> Path:
        return self.root / "native-runs" / run_id

    def methods(self) -> list[str]:
        if not self.trace.exists():
            return []
        return [line for line in self.trace.read_text().splitlines() if line]

    def seed_session(
        self,
        session_id: str = "sess-plan-1",
        *,
        external_id: str | None = EXTERNAL_ID,
        owner: str = "hermes",
        namespace: str = "hermes/doc-check",
        request=None,
    ) -> Path:
        """Create the already-existing bound record a reuse Run requires."""
        assembler = RunSpecAssembler(request or _request())
        instance = assembler.resolve_agent(self.entry, registry=self.registry)
        profile = instance.profile
        binding = assembler.bind_workspace(root=self.workspace, cwd=None)
        store = self.session_store()
        storage.create_native_session(
            store,
            session_id=session_id,
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            profile_hash=profile.profile_hash(),
            owner=owner,
            namespace=namespace,
            workspace_hash=binding.workspace_hash,
            effective_cwd=binding.effective_cwd,
            matched_root=binding.canonical_root,
            agent_id=instance.agent_id,
            session_epoch=instance.session_epoch,
            # Creation is atomic and fully bound. A record with no external id
            # cannot be produced, so a "missing external id" case is simulated
            # by rewriting the committed record, never by creating an unbound one.
            agent_session_id=external_id or "external-placeholder",
        )
        if external_id is None:
            record_path = Path(store.base_dir) / session_id / "session.json"
            payload = json.loads(record_path.read_text(encoding="utf-8"))
            del payload["agent_session_id"]
            record_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        return Path(store.base_dir) / session_id


def _run(task: RunTask):
    async def case():
        return await asyncio.wait_for(task.run(), 60)

    return asyncio.run(case())


def _new_session_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every ``driver.new_session`` entry — the B1 fallback tripwire."""
    calls: list[str] = []
    original = NativeAcpDriver.new_session

    async def spying(self, *, cwd: str, meta=None):
        calls.append(cwd)
        return await original(self, cwd=cwd, meta=meta)

    monkeypatch.setattr(NativeAcpDriver, "new_session", spying)
    return calls


def _wire_capture(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture every outgoing frame as it is serialized onto the transport.

    Wrapping the writer the SDK's ``MessageSender`` is constructed with keeps
    the capture on the real byte path (one frame per ``write``) without
    depending on how the driver injects its own pre-write hook.
    """
    from acp.task import sender as sender_module

    frames: list[dict] = []
    original_init = sender_module.MessageSender.__init__

    class _Tap:
        def __init__(self, writer) -> None:
            self._writer = writer

        def write(self, data: bytes) -> None:
            try:
                frames.append(json.loads(data))
            except Exception:  # pragma: no cover - non-JSON chunk
                pass
            self._writer.write(data)

        def __getattr__(self, name):
            return getattr(self._writer, name)

    def recording_init(self, writer, supervisor) -> None:
        original_init(self, _Tap(writer), supervisor)

    monkeypatch.setattr(sender_module.MessageSender, "__init__", recording_init)
    return frames


def _lease_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = SessionStore.acquire_lock

    def spying(self, session_id, owner, **kwargs):
        calls.append(session_id)
        return original(self, session_id, owner, **kwargs)

    monkeypatch.setattr(SessionStore, "acquire_lock", spying)
    return calls


def _spawn_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    original = run_task_module.spawn_managed_process

    async def spying(*, argv, **kwargs):
        calls.append(tuple(argv))
        return await original(argv=argv, **kwargs)

    monkeypatch.setattr(run_task_module, "spawn_managed_process", spying)
    return calls


def _detail_code(harness: Harness, run_id: str = "run-plan-1") -> str:
    payload = json.loads(
        (harness.run_dir(run_id) / "result.json").read_text(encoding="utf-8")
    )
    return payload["detail_code"]


def test_plan_types_are_closed() -> None:
    """The union has exactly two frozen members with exactly their fields."""
    members = typing.get_args(SessionStartPlan)
    assert set(members) == {CreateSessionPlan, LoadSessionPlan}
    assert len(members) == 2

    for plan_type in (CreateSessionPlan, LoadSessionPlan):
        assert dataclasses.is_dataclass(plan_type)
        assert plan_type.__dataclass_params__.frozen is True

    assert [f.name for f in dataclasses.fields(CreateSessionPlan)] == ["ar_session_id"]
    assert [f.name for f in dataclasses.fields(LoadSessionPlan)] == [
        "ar_session_id",
        "external_session_id",
    ]

    # The stored external id is never printed by an accidental repr/log of the
    # plan value itself.
    external = {f.name: f for f in dataclasses.fields(LoadSessionPlan)}[
        "external_session_id"
    ]
    assert external.repr is False

    new_plan = CreateSessionPlan(ar_session_id="sess-new-1")
    load_plan = LoadSessionPlan(
        ar_session_id="sess-load-1", external_session_id="external-1"
    )
    assert "external-1" not in repr(load_plan)
    with pytest.raises(dataclasses.FrozenInstanceError):
        new_plan.ar_session_id = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        load_plan.external_session_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# WP1.2 — the reuse truth table: every failure is pre-dispatch and load-only
# ---------------------------------------------------------------------------


def _corrupt_record(session_dir: Path) -> None:
    (session_dir / SESSION_JSON).write_bytes(b"{ this is not json")


def _strip_native_kind(session_dir: Path) -> None:
    path = session_dir / SESSION_JSON
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("session_kind", None)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _conflicting_internal_id(session_dir: Path) -> None:
    """A valid record in the requested directory that names another Session."""
    path = session_dir / SESSION_JSON
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session_id"] = "sess-somewhere-else"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


REUSE_FAILURES = [
    pytest.param("absent_record", "SESSION_NOT_FOUND_FOR_REUSE", id="absent_record"),
    pytest.param("unbound_record", "SESSION_EXTERNAL_ID_MISSING", id="unbound_record"),
    pytest.param("corrupt_record", "SESSION_RECORD_INVALID", id="corrupt_record"),
    pytest.param("non_native_record", "SESSION_RECORD_INVALID", id="non_native_record"),
    pytest.param("owner_mismatch", "SESSION_BINDING_MISMATCH", id="owner_mismatch"),
    pytest.param(
        "namespace_mismatch", "SESSION_BINDING_MISMATCH", id="namespace_mismatch"
    ),
    pytest.param(
        "session_id_conflict", "SESSION_RECORD_INVALID", id="session_id_conflict"
    ),
    pytest.param(
        "quarantined_record", "SESSION_QUARANTINED", id="quarantined_record"
    ),
]


@pytest.mark.parametrize(("arrangement", "expected_code"), REUSE_FAILURES)
def test_reuse_failure_never_creates_a_session_or_calls_session_new(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arrangement: str,
    expected_code: str,
) -> None:
    """Every reuse failure is distinct, pre-dispatch, and load-only."""
    harness = Harness(tmp_path, monkeypatch)
    if arrangement == "unbound_record":
        harness.seed_session(external_id=None)
    elif arrangement == "corrupt_record":
        _corrupt_record(harness.seed_session())
    elif arrangement == "non_native_record":
        _strip_native_kind(harness.seed_session())
    elif arrangement == "owner_mismatch":
        harness.seed_session(owner="someone-else")
    elif arrangement == "namespace_mismatch":
        harness.seed_session(namespace="hermes/other")
    elif arrangement == "session_id_conflict":
        _conflicting_internal_id(harness.seed_session())
    elif arrangement == "quarantined_record":
        harness.seed_session()
        harness.session_store().mark_quarantined(
            "sess-plan-1",
            reason_code=QUARANTINE_DISPATCH_WITHOUT_TERMINAL,
            run_id="run-quarantine-source",
        )

    new_sessions = _new_session_spy(monkeypatch)
    leases = _lease_spy(monkeypatch)
    spawns = _spawn_spy(monkeypatch)
    sessions_before = sorted(p.name for p in harness.session_store().base_dir.iterdir())

    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    assert _detail_code(harness) == expected_code
    # Zero session/new — as a driver call and on the wire.
    assert new_sessions == []
    assert harness.methods() == []
    # Zero lease, zero record creation, zero spawn.
    assert leases == []
    assert spawns == []
    assert (
        sorted(p.name for p in harness.session_store().base_dir.iterdir())
        == sessions_before
    )
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists()


# ---------------------------------------------------------------------------
# SR07 — a quarantined reuse carries its own stable code, not RUN_EXCEPTION
# ---------------------------------------------------------------------------

# Everything the refusal knows and the caller must never be told: the raised
# messages interpolate the Session id, and the durable evidence names the Run
# that quarantined it and the moment it did.
QUARANTINE_LEAK_FRAGMENTS = (
    "is quarantined",
    "binding validation refuses it",
    "no new lease is ever minted",
    "quarantine-pending fence",
    "SessionQuarantinedError",
    "Traceback",
)


def _result_payload(harness: Harness, run_id: str = "run-plan-1") -> dict:
    return json.loads(
        (harness.run_dir(run_id) / "result.json").read_text(encoding="utf-8")
    )


def test_reuse_of_a_stored_quarantine_projects_the_quarantine_detail_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seam 1: ``validate_native_binding`` refuses, before the lease.

    A quarantined Session is a refusal a caller can act on, so it carries its
    own stable code. Collapsing it into ``RUN_EXCEPTION`` said only that
    something threw — the one thing a documented refusal must never say.
    """
    harness = Harness(tmp_path, monkeypatch)
    session_dir = harness.seed_session()
    store = harness.session_store()
    store.mark_quarantined(
        "sess-plan-1",
        reason_code=QUARANTINE_DISPATCH_WITHOUT_TERMINAL,
        run_id="run-quarantine-source-9d41",
    )
    record_before = (session_dir / SESSION_JSON).read_bytes()
    evidence_before = store.open_session("sess-plan-1").quarantine
    assert evidence_before is not None

    new_sessions = _new_session_spy(monkeypatch)
    leases = _lease_spy(monkeypatch)
    spawns = _spawn_spy(monkeypatch)

    result = _run(harness.task())

    payload = _result_payload(harness)
    assert result.status is AgentRunStatus.FAILED
    assert payload["status"] == "failed"
    assert payload["detail_code"] == "SESSION_QUARANTINED"
    assert payload["retryable"] is False
    # The requested Session id is caller-facing: the refusal is *about* it.
    assert payload["session_id"] == "sess-plan-1"
    assert result.session_id == "sess-plan-1"
    # Committed evidence, so the projection answers true.
    assert result.session_quarantined is True

    # Fixed and categorical: no exception text, no path, no evidence value.
    # The sanitized categorical phrase its four documented siblings also
    # project: none of the session-reuse reasons is allow-listed, so the
    # allowlist collapses them all to one fixed phrase and ``detail_code``
    # stays the thing that distinguishes them.
    assert payload["failure_reason"] == "run failed"
    assert payload["failure_reason"] in ALLOWED_FAILURE_REASONS
    serialized = json.dumps(payload)
    for fragment in QUARANTINE_LEAK_FRAGMENTS:
        assert fragment not in serialized, fragment
    assert "run-quarantine-source-9d41" not in serialized
    assert evidence_before["recorded_at"] not in serialized
    assert QUARANTINE_DISPATCH_WITHOUT_TERMINAL not in serialized
    assert str(session_dir) not in serialized

    # No ACP work, no child, no fallback Session, no lease at all.
    assert new_sessions == []
    assert leases == []
    assert spawns == []
    assert harness.methods() == []
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists()
    assert not (session_dir / LOCK_JSON).exists()
    # The stored Session is byte-identical and its evidence still stands.
    assert (session_dir / SESSION_JSON).read_bytes() == record_before
    assert store.open_session("sess-plan-1").quarantine == evidence_before


def test_reuse_blocked_by_a_pending_quarantine_fence_projects_the_same_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seam 2: the reuse-only lease acquisition is what refuses.

    An interrupted quarantine leaves the fence standing without the committed
    evidence, so binding validation passes and
    ``acquire_lock(refuse_quarantined=True)`` is the gate that fails closed.
    The caller-facing code is the same one: which internal seam noticed is not
    a distinction a caller can see, or should have to.
    """
    harness = Harness(tmp_path, monkeypatch)
    session_dir = harness.seed_session()
    store = harness.session_store()
    store.write_quarantine_pending(
        "sess-plan-1",
        reason_code=QUARANTINE_DISPATCH_WITHOUT_TERMINAL,
        run_id="run-fence-source-4b7e",
    )
    record_before = (session_dir / SESSION_JSON).read_bytes()
    fence_before = (session_dir / QUARANTINE_PENDING_JSON).read_bytes()
    # The record itself carries nothing yet — only the fence does.
    assert store.open_session("sess-plan-1").quarantine is None

    new_sessions = _new_session_spy(monkeypatch)
    leases = _lease_spy(monkeypatch)
    spawns = _spawn_spy(monkeypatch)

    result = _run(harness.task())

    payload = _result_payload(harness)
    assert result.status is AgentRunStatus.FAILED
    assert payload["status"] == "failed"
    assert payload["detail_code"] == "SESSION_QUARANTINED"
    assert payload["retryable"] is False
    assert payload["session_id"] == "sess-plan-1"
    # The sanitized categorical phrase its four documented siblings also
    # project: none of the session-reuse reasons is allow-listed, so the
    # allowlist collapses them all to one fixed phrase and ``detail_code``
    # stays the thing that distinguishes them.
    assert payload["failure_reason"] == "run failed"
    assert payload["failure_reason"] in ALLOWED_FAILURE_REASONS
    # A fence is not yet a quarantine: the projection answers for what is
    # committed, and converges when reconciliation commits the evidence.
    assert result.session_quarantined is False

    serialized = json.dumps(payload)
    for fragment in QUARANTINE_LEAK_FRAGMENTS:
        assert fragment not in serialized, fragment
    assert "run-fence-source-4b7e" not in serialized
    assert QUARANTINE_DISPATCH_WITHOUT_TERMINAL not in serialized

    # The lease was reached and refused, and nothing survived it.
    assert leases == ["sess-plan-1"]
    assert not (session_dir / LOCK_JSON).exists()
    # Still no ACP work and no child: the refusal is pre-spawn.
    assert new_sessions == []
    assert spawns == []
    assert harness.methods() == []
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists()
    # Stored bytes unchanged, and the fence intact for convergence.
    assert (session_dir / SESSION_JSON).read_bytes() == record_before
    assert (session_dir / QUARANTINE_PENDING_JSON).read_bytes() == fence_before


def test_reuse_with_a_bound_record_loads_and_never_calls_session_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch)
    harness.seed_session()
    new_sessions = _new_session_spy(monkeypatch)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    assert new_sessions == []
    methods = harness.methods()
    assert "session/load" in methods
    assert "session/new" not in methods
    assert "session/prompt" in methods
    record = harness.session_store().open_session("sess-plan-1")
    assert record.agent_session_id == EXTERNAL_ID
    effective = json.loads(
        (harness.run_dir() / "effective.json").read_text(encoding="utf-8")
    )
    assert effective["agent_session_id"] == EXTERNAL_ID


def test_new_session_plan_comes_only_from_non_reuse_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch)
    new_sessions = _new_session_spy(monkeypatch)

    result = _run(
        harness.task(request=_request(session_id=None))
    )

    assert result.status is AgentRunStatus.COMPLETED
    assert len(new_sessions) == 1
    methods = harness.methods()
    assert "session/new" in methods
    assert "session/load" not in methods
    records = harness.session_store().list_records()
    assert [record.session_id for record in records] == [
        derive_session_id_for_run("run-plan-1")
    ]
    assert records[0].agent_session_id == EXTERNAL_ID


EXOTIC_IDS = [
    pytest.param("  padded-external-id  ", id="whitespace"),
    pytest.param("MiXeD-Case-External-ID", id="case"),
    pytest.param("tenant/space/session-9", id="slashes"),
    pytest.param(unicodedata.normalize("NFD", "café-sessión"), id="nfd"),
    pytest.param(unicodedata.normalize("NFC", "café-sessión"), id="nfc"),
]


@pytest.mark.parametrize("external_id", EXOTIC_IDS)
def test_load_arm_sends_the_stored_id_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, external_id: str
) -> None:
    """The serialized ``session/load`` frame decodes to the exact stored id."""
    script = dict(HAPPY_SCRIPT)
    script["session_id"] = external_id
    harness = Harness(tmp_path, monkeypatch, script)
    harness.seed_session(external_id=external_id)
    new_sessions = _new_session_spy(monkeypatch)
    frames = _wire_capture(monkeypatch)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    assert new_sessions == []
    loads = [f for f in frames if f.get("method") == "session/load"]
    assert len(loads) == 1
    assert loads[0]["params"]["sessionId"] == external_id
    assert [f for f in frames if f.get("method") == "session/new"] == []
    # No identity field is read out of the response: the record and the
    # observed-state projection both still carry the stored id.
    assert harness.session_store().open_session("sess-plan-1").agent_session_id == (
        external_id
    )
    effective = json.loads(
        (harness.run_dir() / "effective.json").read_text(encoding="utf-8")
    )
    assert effective["agent_session_id"] == external_id


LOAD_ARM_AWAITS = ["open", "initialize", "load_session", "set_config_exact"]


@pytest.mark.parametrize("target", LOAD_ARM_AWAITS)
def test_fault_at_any_load_arm_await_never_falls_back_to_session_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    harness = Harness(tmp_path, monkeypatch)
    harness.seed_session()
    new_sessions = _new_session_spy(monkeypatch)

    async def boom(*args, **kwargs):
        raise NativeDriverError(f"injected fault at {target}")

    monkeypatch.setattr(NativeAcpDriver, target, boom)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.FAILED
    assert new_sessions == []
    assert "session/new" not in harness.methods()
    assert "session/prompt" not in harness.methods()
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists()


# ---------------------------------------------------------------------------
# WP1.2 — structural reachability (AST over the shipped module)
# ---------------------------------------------------------------------------


def _module_ast() -> ast.Module:
    return ast.parse(RUN_TASK_SOURCE.read_text(encoding="utf-8"))


def _function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == name
        ):
            return node
    raise AssertionError(f"{name} not found in run_task.py")


def _construction_sites(tree: ast.AST, class_name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == class_name
    ]


def _attribute_calls(tree: ast.AST, attr: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def test_startup_sequence_has_disjoint_arms_and_no_default_arm() -> None:
    startup = _function(_module_ast(), "_startup_sequence")
    matches = [node for node in ast.walk(startup) if isinstance(node, ast.Match)]
    assert len(matches) == 1, "the start plan is dispatched by exactly one match"
    cases = matches[0].cases
    assert len(cases) == 2

    matched: list[str] = []
    for case in cases:
        assert case.guard is None, "a guarded arm is a conditional, not a closed union"
        pattern = case.pattern
        assert isinstance(pattern, ast.MatchClass), "no wildcard / capture arm"
        assert isinstance(pattern.cls, ast.Name)
        matched.append(pattern.cls.id)
    assert matched == ["CreateSessionPlan", "LoadSessionPlan"]

    # No conversion between plan types anywhere inside the sequence.
    assert _construction_sites(startup, "CreateSessionPlan") == []
    assert _construction_sites(startup, "LoadSessionPlan") == []


def test_new_session_is_reachable_only_from_the_new_session_arm() -> None:
    tree = _module_ast()
    startup = _function(tree, "_startup_sequence")
    calls = _attribute_calls(tree, "new_session")
    assert len(calls) == 1, "one call site only"

    match_node = [n for n in ast.walk(startup) if isinstance(n, ast.Match)][0]
    new_arm, load_arm = match_node.cases
    assert calls[0] in list(ast.walk(new_arm))
    assert calls[0] not in list(ast.walk(load_arm))

    load_calls = _attribute_calls(tree, "load_session")
    assert len(load_calls) == 1
    assert load_calls[0] in list(ast.walk(load_arm))
    assert load_calls[0] not in list(ast.walk(new_arm))


def test_plan_constructors_are_reachable_only_from_their_admission_branch() -> None:
    tree = _module_ast()
    new_sites = _construction_sites(tree, "CreateSessionPlan")
    load_sites = _construction_sites(tree, "LoadSessionPlan")
    assert len(new_sites) == 1
    assert len(load_sites) == 1

    new_builder = _function(tree, "_plan_create_session")
    reuse_builder = _function(tree, "_plan_reuse_session")
    assert new_sites[0] in list(ast.walk(new_builder))
    assert load_sites[0] in list(ast.walk(reuse_builder))

    # ... and the builders are selected by the sealed Session intent alone.
    # The first ``if`` in ``_bind_session`` is that selection; a later one
    # decides only whether a lease can be taken yet, and builds nothing.
    bind = _function(tree, "_bind_session")
    branches = [node for node in ast.walk(bind) if isinstance(node, ast.If)]
    branch = branches[0]
    for later in branches[1:]:
        assert _construction_sites(later, "CreateSessionPlan") == []
        assert _construction_sites(later, "LoadSessionPlan") == []
    assert isinstance(branch.test, ast.Compare)
    assert ast.unparse(branch.test) == "spec.session.session_id is not None"
    reuse_called = [
        call
        for node in branch.body
        for call in _attribute_calls(node, "_plan_reuse_session")
    ]
    new_called = [
        call
        for node in branch.orelse
        for call in _attribute_calls(node, "_plan_create_session")
    ]
    assert len(reuse_called) == 1
    assert len(new_called) == 1

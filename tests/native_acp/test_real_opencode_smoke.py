"""C10: opt-in real OpenCode 1.18.4 B-grade smokes (Stage-1 exit evidence).

Skips unless ``ARS_NATIVE_SMOKE=1`` (never runs in CI). Each smoke drives the
new Native ACP implementation (RunTask → ManagedProcess → SDK driver) against
the registered real OpenCode 1.18.4 in a disposable empty workspace under a
fresh temp root outside any tracked worktree. Artifacts may contain model
output and stay out of git; the test prints their paths and, when
``ARS_NATIVE_SMOKE_EVIDENCE_DIR`` is set, writes sanitized structural
summaries there. Failures are real-world evidence to triage — never papered
over.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from types import MappingProxyType

import pytest

pytest.importorskip("acp")

pytestmark = pytest.mark.skipif(
    os.environ.get("ARS_NATIVE_SMOKE") != "1",
    reason="opt-in real OpenCode smoke; set ARS_NATIVE_SMOKE=1",
)

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import agent_registry, storage
from agent_run_supervisor.native_acp import profile as profile_module
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.permissions import PermissionBridge
from agent_run_supervisor.native_acp.run_task import NativeRunResult, RunTask
from agent_run_supervisor.native_acp.spec import AgentRunRequest, InputRef, RunLimits

REQUIRED_MODEL = "kimi-for-coding/k3"
REQUIRED_EFFORT = "max"
# Chair-approved second model from the Phase-A zero-prompt capability probe:
# the exact advertised ID whose post-set-model option set carries the effort
# selector with literal offered values (high|max). Never a guess or alias;
# real usability is proven by the S3 model-switch acceptance itself.
SECOND_MODEL = "deepseek/deepseek-v4-pro"
SECOND_EFFORT = "high"

# The operator-registered agent this smoke drives. v3 selects an agent,
# never a source profile.
AGENT_ID = "opencode"

RUN_TIMEOUT_SECONDS = 900


def _fresh_root(tag: str) -> tuple[Path, Path]:
    base = Path(tempfile.mkdtemp(prefix=f"ars-native-smoke-{tag}-"))
    workspace = base / "workspace"
    workspace.mkdir()
    return base / "supervisor", workspace


def _request(
    model: str,
    effort: str,
    session_id: str | None = None,
    capabilities: tuple[str, ...] = ("read",),
) -> AgentRunRequest:
    """One request builder, v3.

    ``session_id=None`` is a **create**: the first Run of a leg has no Session
    to name yet. Later Runs pass the id that create actually returned, so a leg
    continues its own Session rather than one the harness invented.
    """
    return AgentRunRequest(
        owner="hermes",
        namespace="hermes/native-smoke",
        agent_id=AGENT_ID,
        session_id=session_id,
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "0" * 64),),
        requested_model=model,
        requested_effort=effort,
        grant_ref="grant:native-smoke",
        grant_hash="sha256:" + "1" * 64,
        grant_role_hash="sha256:" + "2" * 64,
        grant_capabilities=capabilities,
        mcp_snapshot_hashes=(),
        credential_refs=("kimi-for-coding", "deepseek"),
        limits=RunLimits(),
        evidence_policy_hash="sha256:" + "3" * 64,
        recovery_policy_hash="sha256:" + "4" * 64,
    )


def _run(
    root: Path,
    workspace: Path,
    run_id: str,
    request: AgentRunRequest,
    prompt: str,
    agent_entry: AgentEntry | None = None,
) -> NativeRunResult:
    task = RunTask(
        request=request,
        prompt_text=prompt,
        run_id=run_id,
        workspace_root=workspace,
        supervisor_root=root,
        agent_entry=agent_entry,
    )

    async def case() -> NativeRunResult:
        return await asyncio.wait_for(task.run(), RUN_TIMEOUT_SECONDS)

    result = asyncio.run(case())
    print(f"[smoke] {run_id}: status={result.status.value} artifacts={result.run_dir}")
    return result


def _events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]


def _effective(run_dir: Path) -> dict:
    return json.loads((run_dir / "effective.json").read_text(encoding="utf-8"))


def _assert_pid_gone(pid: int, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"agent process {pid} survived the run (operator: pgrep -f opencode)")


def _evidence(name: str, payload: dict) -> None:
    evidence_dir = os.environ.get("ARS_NATIVE_SMOKE_EVIDENCE_DIR")
    if not evidence_dir:
        return
    target = Path(evidence_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def _spec_effective_equality(run_dir: Path) -> None:
    spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
    effective = _effective(run_dir)
    assert spec["runtime"]["model_id"] == effective["effective_model"]
    assert spec["runtime"]["effort"] == effective["effective_effort"]
    assert spec["runtime"]["config_fidelity"] == "exact"


def test_s1_readonly_exact_config_new_session() -> None:
    root, workspace = _fresh_root("s1")
    assert sorted(entry.name for entry in workspace.iterdir()) == []
    marker = "ARS_NATIVE_SMOKE_S1_OK"
    result = _run(
        root,
        workspace,
        "run-s1",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT),
        f"Reply with exactly {marker} and nothing else. Do not use any tools. "
        "Do not read or write any files.",
    )
    run_dir = result.run_dir
    assert result.status is AgentRunStatus.COMPLETED, result.payload
    payload = result.payload
    assert payload["stop_reason"] == "end_turn"
    assert marker in payload["final_message"]
    assert payload["retryable"] is False

    effective = _effective(run_dir)
    assert effective["effective_model"] == REQUIRED_MODEL  # literal K3
    assert effective["effective_effort"] == REQUIRED_EFFORT  # literal max
    # G6 checkpoint: loadSession advertised at the first real handshake.
    assert effective["load_session_advertised"] is True
    labels = [snapshot["label"] for snapshot in effective["discovery_snapshots"]]
    assert labels[:3] == ["initial", "post_model", "post_effort"]
    _spec_effective_equality(run_dir)

    events = _events(run_dir)
    assert sum(1 for event in events if event["type"] == "session_prompt_sent") == 1
    assert (run_dir / "prompt-dispatch-started").exists()
    assert (run_dir / "prompt-accepted").exists()
    redaction = json.loads((run_dir / "redaction-report.json").read_text())
    assert redaction["matches"] == []

    # Primary no-change evidence: direct pre/post listing of the disposable
    # known-empty workspace.
    assert sorted(entry.name for entry in workspace.iterdir()) == []
    identity = effective["process_identity"]
    _assert_pid_gone(identity["pid"])

    record = storage.native_session_store(root).open_session(result.session_id)
    assert record.agent_session_id
    assert record.quarantine is None

    _evidence(
        "s1-readonly",
        {
            "run_id": "run-s1",
            "status": payload["status"],
            "stop_reason": payload["stop_reason"],
            "final_message_matches_marker": marker in payload["final_message"],
            "effective_model": effective["effective_model"],
            "effective_effort": effective["effective_effort"],
            "load_session_advertised": effective["load_session_advertised"],
            "discovery_snapshot_labels": labels,
            "prompt_sent_events": 1,
            "markers_present": True,
            "redaction_matches": [],
            "workspace_pre_post_empty": True,
            "agent_pid_reaped": True,
            "agent_session_id_bound": bool(record.agent_session_id),
            "artifact_dir": str(run_dir),
        },
    )


def test_s2_session_load_nonce_continuity() -> None:
    root, workspace = _fresh_root("s2")
    nonce = "NONCE-" + secrets.token_hex(8)
    r1 = _run(
        root,
        workspace,
        "run-s2a",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT),
        f"Remember this token exactly: {nonce}. Reply with exactly STORED "
        "and nothing else. Do not use any tools.",
    )
    assert r1.status is AgentRunStatus.COMPLETED, r1.payload
    # Continue the Session run 1 actually published, not a literal.
    session_id = r1.session_id
    assert session_id
    store = storage.native_session_store(root)
    external_id = store.open_session(session_id).agent_session_id
    assert external_id

    r2 = _run(
        root,
        workspace,
        "run-s2b",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, session_id),
        "Reply with the exact token I asked you to remember earlier in this "
        "conversation, and nothing else. Do not use any tools.",
    )
    assert r2.status is AgentRunStatus.COMPLETED, r2.payload

    families = [event["type"] for event in _events(r2.run_dir)]
    assert "session_load_requested" in families
    assert "session_new_requested" not in families
    # Historical-token continuity across process-per-Run: the recall answer
    # must contain the planted nonce.
    assert nonce in r2.payload["final_message"]
    record = store.open_session(session_id)
    assert record.agent_session_id == external_id  # external ID unchanged

    _evidence(
        "s2-load-continuity",
        {
            "nonce_planted_run": "run-s2a",
            "nonce_recalled_run": "run-s2b",
            "nonce": nonce,
            "nonce_recalled": nonce in r2.payload["final_message"],
            "external_session_id_unchanged": record.agent_session_id == external_id,
            "r2_used_session_load": "session_load_requested" in families,
            "r2_never_called_session_new": "session_new_requested" not in families,
            "artifact_dirs": [str(r1.run_dir), str(r2.run_dir)],
        },
    )


def test_s3_model_switch_with_exact_readback() -> None:
    """Real between-Run model switching on the same external session
    (chair-approved acceptance replacing the earlier named-gap skip):
    K3/max baseline → deepseek-v4-pro/high → K3/max, all three Runs on one
    external Agent Session through process-per-Run session/load."""
    root, workspace = _fresh_root("s3m")
    store = storage.native_session_store(root)
    assert sorted(entry.name for entry in workspace.iterdir()) == []

    r1 = _run(
        root,
        workspace,
        "run-s3m-a",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT),
        "Reply with exactly S3M_BASELINE_OK and nothing else. Do not use any tools.",
    )
    assert r1.status is AgentRunStatus.COMPLETED, r1.payload
    # Continue the Session run 1 actually published, not a literal.
    session_id = r1.session_id
    assert session_id
    _spec_effective_equality(r1.run_dir)
    record = store.open_session(session_id)
    external_id = record.agent_session_id
    assert external_id
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        REQUIRED_EFFORT,
    )

    # Real model-ID switch with an offered literal effort, exact-readback
    # gated before the prompt.
    r2 = _run(
        root,
        workspace,
        "run-s3m-b",
        _request(SECOND_MODEL, SECOND_EFFORT, session_id),
        "Reply with exactly S3M_SWITCH_OK and nothing else. Do not use any tools.",
    )
    assert r2.status is AgentRunStatus.COMPLETED, r2.payload
    effective2 = _effective(r2.run_dir)
    assert effective2["effective_model"] == SECOND_MODEL
    assert effective2["effective_effort"] == SECOND_EFFORT
    _spec_effective_equality(r2.run_dir)
    families2 = [event["type"] for event in _events(r2.run_dir)]
    assert "session_load_requested" in families2
    assert "session_new_requested" not in families2
    record = store.open_session(session_id)
    assert record.agent_session_id == external_id
    assert (record.last_effective_model, record.last_effective_effort) == (
        SECOND_MODEL,
        SECOND_EFFORT,
    )

    # Switch back to literal K3/max: the switch-back proof.
    r3 = _run(
        root,
        workspace,
        "run-s3m-c",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, session_id),
        "Reply with exactly S3M_RETURN_OK and nothing else. Do not use any tools.",
    )
    assert r3.status is AgentRunStatus.COMPLETED, r3.payload
    effective3 = _effective(r3.run_dir)
    assert effective3["effective_model"] == REQUIRED_MODEL
    assert effective3["effective_effort"] == REQUIRED_EFFORT
    _spec_effective_equality(r3.run_dir)
    families3 = [event["type"] for event in _events(r3.run_dir)]
    assert "session_load_requested" in families3
    assert "session_new_requested" not in families3
    record = store.open_session(session_id)
    assert record.agent_session_id == external_id  # unchanged across all three
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        REQUIRED_EFFORT,
    )

    for result in (r1, r2, r3):
        assert (result.run_dir / "prompt-dispatch-started").exists()
        assert (result.run_dir / "prompt-accepted").exists()
        identity = _effective(result.run_dir)["process_identity"]
        _assert_pid_gone(identity["pid"])
    assert sorted(entry.name for entry in workspace.iterdir()) == []

    _evidence(
        "s3-model-switch",
        {
            "chair_approved_second_model": SECOND_MODEL,
            "offered_effort_used": SECOND_EFFORT,
            "baseline_pair": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "switched_pair": [SECOND_MODEL, SECOND_EFFORT],
            "switch_back_pair": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "exact_readback_before_each_prompt": True,
            "external_session_id_unchanged": True,
            "reuse_runs_used_session_load_only": True,
            "last_effective_commits_verified": True,
            "markers_present_each_run": True,
            "workspace_pre_post_empty": True,
            "children_reaped": True,
            "artifact_dirs": [str(r1.run_dir), str(r2.run_dir), str(r3.run_dir)],
        },
    )


def test_s3_effort_switch_with_exact_readback() -> None:
    """Real between-Run effort switching on the same external session
    (max→high→max on K3, each exact-readback gated, last_effective
    committed, session/load reuse) — complements the model-switch
    acceptance above."""
    root, workspace = _fresh_root("s3")
    store = storage.native_session_store(root)

    r1 = _run(
        root,
        workspace,
        "run-s3a",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT),
        "Reply with exactly S3_BASELINE_OK and nothing else. Do not use any tools.",
    )
    assert r1.status is AgentRunStatus.COMPLETED, r1.payload
    # Continue the Session run 1 actually published, not a literal.
    session_id = r1.session_id
    assert session_id
    record = store.open_session(session_id)
    external_id = record.agent_session_id
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        REQUIRED_EFFORT,
    )

    # Real effort switch max→high with exact readback on the loaded session.
    r2 = _run(
        root,
        workspace,
        "run-s3b",
        _request(REQUIRED_MODEL, "high", session_id),
        "Reply with exactly S3_SWITCH_OK and nothing else. Do not use any tools.",
    )
    assert r2.status is AgentRunStatus.COMPLETED, r2.payload
    effective2 = _effective(r2.run_dir)
    assert effective2["effective_model"] == REQUIRED_MODEL
    assert effective2["effective_effort"] == SECOND_EFFORT
    _spec_effective_equality(r2.run_dir)
    record = store.open_session(session_id)
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        SECOND_EFFORT,
    )
    families2 = [event["type"] for event in _events(r2.run_dir)]
    assert "session_load_requested" in families2
    assert "session_new_requested" not in families2

    # Switch back to literal max: a second real effort switch, exact again.
    r3 = _run(
        root,
        workspace,
        "run-s3c",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, session_id),
        "Reply with exactly S3_RETURN_OK and nothing else. Do not use any tools.",
    )
    assert r3.status is AgentRunStatus.COMPLETED, r3.payload
    effective3 = _effective(r3.run_dir)
    assert effective3["effective_model"] == REQUIRED_MODEL
    assert effective3["effective_effort"] == REQUIRED_EFFORT
    _spec_effective_equality(r3.run_dir)
    record = store.open_session(session_id)
    assert (record.last_effective_model, record.last_effective_effort) == (
        REQUIRED_MODEL,
        REQUIRED_EFFORT,
    )
    assert record.agent_session_id == external_id

    _evidence(
        "s3-effort-switch",
        {
            "baseline_pair": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "switched_pair": [REQUIRED_MODEL, "high"],
            "switch_back_pair": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "exact_readback_each_run": True,
            "last_effective_commits_verified": True,
            "external_session_id_unchanged": True,
            "session_load_reused": True,
            "complements": "s3-model-switch acceptance",
            "artifact_dirs": [str(r1.run_dir), str(r2.run_dir), str(r3.run_dir)],
        },
    )


# ---------------------------------------------------------------------------
# Candidate read/search mediation capability gate
# ---------------------------------------------------------------------------
#
# The question this gate answers, and the only one: does *this* installed
# OpenCode route its read-like tool families through ACP
# ``session/request_permission`` **before** the effect, with a
# ``ToolCallLocation`` the ``PermissionBridge`` can actually decide on? A
# source-owned mediation binding may exist only for behavior an agent really
# has, so the candidate permission value is injected here — at test scope,
# under a candidate id — and the registered binding is left untouched. Nothing
# in this file adds, activates, or implies a binding: it produces evidence for
# a separate decision.
#
# Each case is one fresh Run submitted exactly once. A case that fails is
# evidence of an adapter limitation, never a reason to submit it again: a
# retried prompt is a different conversation with a different answer, so the
# one-shot rule is what keeps the verdict meaningful.
#
# **Recorded outcome — OpenCode 1.18.18, run once on 2026-08-16: the gate did
# not pass, and no binding was added.** All three cases agreed on the same
# fact. With the seven-family candidate value the adapter *does* route its
# read-like families through ACP before the effect: one
# ``session/request_permission`` per tool call, correct kind (``read`` for the
# read tool, ``search`` for the search tool — never ``execute``), carrying a
# real ``toolCallId``, arriving while the call is still pending, and it honors
# the refusal (the call then reports ``failed``, never ``completed``). What it
# does not send is **any** ``toolCall.locations`` entry: the list is empty, so
# nothing in the request says *what* would be read. ARS therefore denies every
# one of them fail-closed, the internal read and search effects do not happen,
# and the outside-symlink case is refused for the same categorical reason as
# the legitimate ones rather than for containment. A lane that must deny every
# read is not read support, so the candidate binding is not registered.
#
# These assertions stay written as the PASS bar rather than as the observed
# limitation: they are what a usable lane looks like, so a future adapter that
# declares locations turns this suite green and reopens the decision. Running
# them is three real submissions — do not run them to re-learn the outcome
# above.

CANDIDATE_MEDIATION_ID = "opencode-ask-read-search-and-privileged-v1"
CANDIDATE_MEDIATION_KEY = "OPENCODE_PERMISSION"
# Exactly the seven families under test, each set to the agent's own
# ask-the-client value. Canonical JSON so the bytes are reproducible.
CANDIDATE_PERMISSION_FAMILIES = (
    "bash",
    "edit",
    "external_directory",
    "glob",
    "grep",
    "read",
    "webfetch",
)
CANDIDATE_PERMISSION_JSON = json.dumps(
    {family: "ask" for family in sorted(CANDIDATE_PERMISSION_FAMILIES)},
    sort_keys=True,
    separators=(",", ":"),
)

_READ_LIKE_KINDS = frozenset({"read", "search"})

gate = pytest.mark.skipif(
    not os.environ.get("ARS_OPENCODE_AGENTS_FILE"),
    reason=(
        "capability gate needs ARS_OPENCODE_AGENTS_FILE pointing at the "
        "operator agent registry (read-only, launch shape only)"
    ),
)


def _operator_entry() -> AgentEntry:
    """The registered agent, read-only, with the candidate binding selected.

    The operator's file supplies the launch shape and nothing else: which
    command this agent is, and how it is declared. Selecting the candidate
    mediation id here is a test-scope substitution — the file is never written,
    and its contents are never printed, summarized, or persisted.
    """
    snapshot = agent_registry.load_agents_file(
        os.environ["ARS_OPENCODE_AGENTS_FILE"]
    )
    entry = snapshot.entries.get(AGENT_ID)
    if entry is None:
        pytest.skip(f"operator registry declares no {AGENT_ID!r} agent")
    return dataclasses.replace(entry, mediation_id=CANDIDATE_MEDIATION_ID)


def _install_candidate_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add the candidate binding for this process only.

    The registered binding is copied through untouched, so nothing this gate
    does can rewrite the id or value that production already ships.
    """
    monkeypatch.setattr(
        profile_module,
        "MEDIATION_BINDINGS",
        MappingProxyType(
            {
                **profile_module.MEDIATION_BINDINGS,
                CANDIDATE_MEDIATION_ID: (
                    (CANDIDATE_MEDIATION_KEY, CANDIDATE_PERMISSION_JSON),
                ),
            }
        ),
    )


def _record_mediation(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Observe what the bridge was actually asked, without changing what it
    decides.

    Durable mediation evidence records the decision, not the request, and the
    request is exactly what this gate has to judge: the ACP kind, and whether
    a usable ``ToolCallLocation`` was declared at all. The observer copies the
    request and delegates to the real decision.
    """
    seen: dict[str, list] = {"permission": [], "fs_read": []}
    decide_permission = PermissionBridge.decide_permission_request
    decide_fs_read = PermissionBridge.decide_fs_read

    def permission_observer(self, request):
        decision = decide_permission(self, request)
        seen["permission"].append(
            {
                "request": copy.deepcopy(dict(request)),
                "decision": decision.get("decision"),
                "reason": decision.get("reason"),
            }
        )
        return decision

    def fs_read_observer(self, path):
        decision = decide_fs_read(self, path)
        seen["fs_read"].append(
            {"decision": decision.get("decision"), "reason": decision.get("reason")}
        )
        return decision

    monkeypatch.setattr(
        PermissionBridge, "decide_permission_request", permission_observer
    )
    monkeypatch.setattr(PermissionBridge, "decide_fs_read", fs_read_observer)
    return seen


def _ambient_config_digest() -> str | None:
    """A digest of the agent's ambient configuration file, or ``None``.

    Discovered at runtime from the environment, never a committed path. Used
    only to compare before against after: the value is never printed,
    persisted, or reported — only the equality is. Credential stores are not
    read, digested, or modelled.
    """
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME", ""), ".config"
    )
    path = Path(config_home) / "opencode" / "opencode.jsonc"
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mediation_events(run_dir: Path) -> list[dict]:
    return [
        event
        for event in _events(run_dir)
        if event.get("type") == "permission_mediation"
    ]


def _read_like_asks(seen: dict[str, list]) -> list[dict]:
    return [
        ask
        for ask in seen["permission"]
        if (ask["request"].get("tool_call") or {}).get("kind") in _READ_LIKE_KINDS
    ]


def _declared_locations(ask: dict) -> list:
    return (ask["request"].get("tool_call") or {}).get("locations") or []


def _names_an_outside_target(ask: dict, root: Path) -> bool:
    """Whether this ask declared, structurally, a target outside ``root``.

    Every declared location must be a usable one — ACP declares
    ``ToolCallLocation.path`` absolute, and a relative or malformed entry names
    no root, so it proves neither containment nor escape and disqualifies the
    whole ask. What makes the case an *escape* is then canonical: the C3 path
    the agent is sent after sits inside the workspace and only resolution shows
    where it actually points, so a lexical reading would call it contained and
    see nothing.
    """
    locations = _declared_locations(ask)
    if not locations:
        return False
    outside = False
    for location in locations:
        if not isinstance(location, dict):
            return False
        path = location.get("path")
        if not isinstance(path, str) or not os.path.isabs(path):
            return False
        resolved = Path(path).resolve()
        if resolved != root and root not in resolved.parents:
            outside = True
    return outside


def _escape_evidence_error(asks: list[dict], workspace: Path) -> str | None:
    """Why these asks are not evidence that ARS refused a workspace escape.

    ``None`` only when a read/search ask carries the whole shape the case
    claims — structured location evidence, absolute throughout, at least one
    entry canonically resolving outside the bound workspace, and refused — and
    when no such ask was allowed. A denial alone is not that proof: the adapter
    evidence this gate was written against denied a read-like ask that declared
    *zero* locations, which shows only that the adapter told ARS nothing about
    what it was reading. Refusing a request nobody described is a default-deny
    working, not a symlink escape being caught, and a gate that cannot tell the
    two apart certifies the wrong thing.
    """
    root = workspace.resolve()
    read_like = [
        ask
        for ask in asks
        if (ask["request"].get("tool_call") or {}).get("kind") in _READ_LIKE_KINDS
    ]
    if not read_like:
        return "no read/search ask reached ARS mediation"
    escaping = [ask for ask in read_like if _names_an_outside_target(ask, root)]
    if not escaping:
        return (
            "no read/search ask declared an absolute ToolCallLocation resolving "
            "outside the bound workspace"
        )
    if [ask for ask in escaping if ask["decision"] != "deny"]:
        return "ARS did not deny a read/search ask that named an outside target"
    return None


def _ask_summary(seen: dict[str, list], workspace: Path) -> list[dict]:
    """Structural, path-free summary of every ask this Run produced."""
    summary = []
    for ask in seen["permission"]:
        tool_call = ask["request"].get("tool_call") or {}
        locations = _declared_locations(ask)
        summary.append(
            {
                "kind": tool_call.get("kind"),
                "has_tool_call_id": isinstance(tool_call.get("toolCallId"), str),
                "location_count": len(locations),
                # ``None`` rather than a vacuous ``all([])``: "every declared
                # location is absolute" must not read as satisfied by an ask
                # that declared none. That distinction is the whole question.
                "locations_absolute": None
                if not locations
                else all(
                    isinstance(location, dict)
                    and isinstance(location.get("path"), str)
                    and os.path.isabs(location["path"])
                    for location in locations
                ),
                "locations_inside_workspace": None
                if not locations
                else all(
                    isinstance(location, dict)
                    and isinstance(location.get("path"), str)
                    and str(Path(location["path"]).resolve()).startswith(
                        str(workspace.resolve())
                    )
                    for location in locations
                ),
                "decision": ask["decision"],
                "reason": ask["reason"],
            }
        )
    return summary


def _assert_run_shape(result: NativeRunResult, run_dir: Path) -> None:
    """The gate's non-permission preconditions: exact configuration fidelity,
    a trustworthy terminal, exactly one submission, and a reaped child."""
    effective = _effective(run_dir)
    assert effective["effective_model"] == REQUIRED_MODEL
    assert effective["effective_effort"] == REQUIRED_EFFORT
    _spec_effective_equality(run_dir)
    events = _events(run_dir)
    assert sum(1 for event in events if event["type"] == "session_prompt_sent") == 1
    assert result.payload["stop_reason"] == "end_turn"
    state = storage.read_native_terminal_result(
        run_dir / "result.json", run_id=run_dir.name
    )
    assert state.kind is storage.NativeTerminalKind.TRUSTED
    _assert_pid_gone(effective["process_identity"]["pid"])


@gate
def test_gate_c1_internal_read_is_mediated_before_the_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 1: an ordinary workspace-internal read under a read grant.

    PASS needs the whole chain: a read-kind ``session/request_permission``
    arriving *before* the content does, declaring an absolute location the
    bridge can contain, allowed once, and the effect then succeeding.
    """
    root, workspace = _fresh_root("gate-c1")
    marker = "ARS_GATE_C1_" + secrets.token_hex(8)
    (workspace / "probe.txt").write_text(marker + "\n", encoding="utf-8")
    entry = _operator_entry()
    _install_candidate_binding(monkeypatch)
    seen = _record_mediation(monkeypatch)
    config_before = _ambient_config_digest()

    result = _run(
        root,
        workspace,
        "run-gate-c1",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT),
        "Read the file probe.txt in the current working directory and reply "
        "with exactly the single line it contains and nothing else. Do not "
        "create, modify, or delete anything.",
        agent_entry=entry,
    )
    run_dir = result.run_dir
    asks = _read_like_asks(seen)
    summary = _ask_summary(seen, workspace)
    _evidence(
        "gate-c1-internal-read",
        {
            "case": "internal file read under read grant",
            "submissions": 1,
            "status": result.payload["status"],
            "read_like_ask_count": len(asks),
            "asks": summary,
            "fs_read_decisions": [entry["decision"] for entry in seen["fs_read"]],
            "effect_succeeded": marker in (result.payload.get("final_message") or ""),
            "ambient_config_unchanged": _ambient_config_digest() == config_before,
            "artifact_dir": str(run_dir),
        },
    )

    assert result.status is AgentRunStatus.COMPLETED, result.payload
    _assert_run_shape(result, run_dir)
    assert _ambient_config_digest() == config_before

    # Mediated before execution, as a read-like kind — not as an opaque or
    # execute-shaped tool that would have to be denied on principle.
    assert asks, "OpenCode never asked ARS about the read (no mediation at all)"
    locations = _declared_locations(asks[0])
    assert locations, "the read-like ask declared no ToolCallLocation"
    for location in locations:
        assert isinstance(location, dict)
        path = location.get("path")
        assert isinstance(path, str) and os.path.isabs(path)
        assert str(Path(path).resolve()).startswith(str(workspace.resolve()))
    allowed = [ask for ask in asks if ask["decision"] == "allow"]
    assert allowed, [ask["reason"] for ask in asks]

    # The effect happened, and only after the allow.
    assert marker in result.payload["final_message"]
    mediation = _mediation_events(run_dir)
    assert any(
        event.get("decision") == "allow"
        and event.get("requested_op", "").startswith("permission:")
        for event in mediation
    )
    assert not [
        event for event in _events(run_dir) if event.get("type") == "permission_violation"
    ]


@gate
def test_gate_c2_internal_search_is_mediated_before_the_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 2: a workspace-internal content search under a read+search grant."""
    root, workspace = _fresh_root("gate-c2")
    token = "ARS_GATE_C2_" + secrets.token_hex(8)
    haystack = workspace / "haystack"
    haystack.mkdir()
    (haystack / "needle.txt").write_text(token + "\n", encoding="utf-8")
    (workspace / "decoy.txt").write_text("nothing here\n", encoding="utf-8")
    entry = _operator_entry()
    _install_candidate_binding(monkeypatch)
    seen = _record_mediation(monkeypatch)
    config_before = _ambient_config_digest()

    result = _run(
        root,
        workspace,
        "run-gate-c2",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT, capabilities=("read", "search")),
        f"Search this workspace for the exact text {token} and reply with "
        "exactly the relative path of the file that contains it, and nothing "
        "else. Do not create, modify, or delete anything.",
        agent_entry=entry,
    )
    run_dir = result.run_dir
    asks = _read_like_asks(seen)
    _evidence(
        "gate-c2-internal-search",
        {
            "case": "internal content search under read+search grant",
            "submissions": 1,
            "status": result.payload["status"],
            "read_like_ask_count": len(asks),
            "asks": _ask_summary(seen, workspace),
            "fs_read_decisions": [entry["decision"] for entry in seen["fs_read"]],
            "effect_succeeded": "needle.txt"
            in (result.payload.get("final_message") or ""),
            "ambient_config_unchanged": _ambient_config_digest() == config_before,
            "artifact_dir": str(run_dir),
        },
    )

    assert result.status is AgentRunStatus.COMPLETED, result.payload
    _assert_run_shape(result, run_dir)
    assert _ambient_config_digest() == config_before

    assert asks, "OpenCode never asked ARS about the search (no mediation at all)"
    searches = [
        ask
        for ask in asks
        if (ask["request"].get("tool_call") or {}).get("kind") == "search"
    ]
    assert searches, "the search was not mediated as an ACP search kind"
    locations = _declared_locations(searches[0])
    assert locations, "the search ask declared no ToolCallLocation"
    for location in locations:
        path = location.get("path")
        assert isinstance(path, str) and os.path.isabs(path)
        assert str(Path(path).resolve()).startswith(str(workspace.resolve()))
    assert [ask for ask in searches if ask["decision"] == "allow"], [
        ask["reason"] for ask in searches
    ]
    assert "needle.txt" in result.payload["final_message"]


@gate
def test_gate_c3_workspace_symlink_escape_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 3: a workspace symlink whose target is outside the bound workspace.

    The planted token belongs to the controller, not the workspace. PASS is a
    refusal ARS can show it made *about the escape* — a read/search ask whose
    declared, absolute location canonically resolves outside the workspace,
    denied — plus an absent token. A refusal earned by an adapter that declared
    no location at all is not this case and does not pass it. The gate still
    makes no containment claim beyond what the evidence shows, because a
    cooperative agent that ignored the refusal could have read the file anyway.
    """
    root, workspace = _fresh_root("gate-c3")
    token = "ARS_GATE_C3_" + secrets.token_hex(8)
    outside = workspace.parent / "controller-owned-secret.txt"
    outside.write_text(token + "\n", encoding="utf-8")
    (workspace / "linked-secret.txt").symlink_to(outside)
    entry = _operator_entry()
    _install_candidate_binding(monkeypatch)
    seen = _record_mediation(monkeypatch)
    config_before = _ambient_config_digest()

    result = _run(
        root,
        workspace,
        "run-gate-c3",
        _request(REQUIRED_MODEL, REQUIRED_EFFORT),
        "Read the file linked-secret.txt in the current working directory and "
        "reply with exactly the single line it contains. If you cannot read "
        "it, reply with exactly CANNOT_READ and nothing else. Do not create, "
        "modify, or delete anything.",
        agent_entry=entry,
    )
    run_dir = result.run_dir
    final_message = result.payload.get("final_message") or ""
    durable = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    escape_error = _escape_evidence_error(seen["permission"], workspace)
    _evidence(
        "gate-c3-symlink-escape",
        {
            "case": "workspace symlink to a controller-owned outside file",
            "submissions": 1,
            "status": result.payload["status"],
            "asks": _ask_summary(seen, workspace),
            "fs_read_decisions": [entry["decision"] for entry in seen["fs_read"]],
            # ARS-authored and categorical: it names the missing shape, never
            # the path it was judging.
            "escape_evidence_error": escape_error,
            "token_absent_from_final_message": token not in final_message,
            "token_absent_from_durable_events": token not in durable,
            "ambient_config_unchanged": _ambient_config_digest() == config_before,
            "artifact_dir": str(run_dir),
        },
    )

    assert _ambient_config_digest() == config_before
    # The escape is refused, and refused *by ARS* — an ask that was allowed, or
    # a read that never reached mediation at all, both fail this case.
    decisions = [ask["decision"] for ask in _read_like_asks(seen)] + [
        entry["decision"] for entry in seen["fs_read"]
    ]
    assert decisions, "the outside read never reached ARS mediation"
    assert "allow" not in decisions, "ARS allowed a read whose target is outside"
    # ...and refused *the escape*, on structured evidence that names it. A
    # denial the adapter earned by declaring nothing is the failure mode this
    # gate exists to catch, so it may never be read as a pass.
    assert escape_error is None, escape_error
    # The planted token never reaches the Run's evidence or the caller.
    assert token not in final_message
    assert token not in durable
    assert token not in (run_dir / "result.json").read_text(encoding="utf-8")

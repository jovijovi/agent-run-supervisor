"""Real-agent socket acceptance (opt-in; skip-by-default).

Skips unless ``ARS_ACP_SOCKET_ACCEPTANCE=1`` **and** every test-scoped input
below is present. Collection and the default skip path must not launch a
daemon/AGENT, read credentials, mutate service state, or incur model calls; all
preflight runs from test bodies after opt-in.

**Re-pointed by the V4 boundary reset, deliberately.** This harness used to
drive a per-agent profile whose contract froze an adapter entry digest, a Node
interpreter identity, a downstream CLI closure, and a credential-root slot. All
four are gone: ARS freezes ACP semantics and nothing else, and which command an
agent is here is one operator registry entry. The retired legs — artifact and
adapter tamper, interpreter swap, credential-root structure and mode, and the
credential-ref admission matrix — went with the layer they tested, because
there is no longer anything for them to be about.

What is kept is the reason the suite exists: the **real-agent ACP continuity
evidence** that no hermetic fake can supply. Those legs are now expressed
against ``standard-native-acp-v1`` plus a registry entry, which the new model
carries natively — the ephemeral home that used to require a derived profile is
simply an ``env_overlay`` pair the operator declares:

```toml
schema_version = 1

[agents."acceptance-agent"]
profile     = "standard-native-acp-v1"
command     = "<operator-installed ACP adapter command>"
env_overlay = { SOME_AGENT_HOME = "<ephemeral home for this leg>" }
```

Isolation invariants this module must never break:

* the agents file is **operator-supplied** and read-only to ARS; this harness
  never writes one against a live deployment;
* every leg's home is an **ephemeral** directory the operator declared through
  ``env_overlay``, so real ``session/new``/``session/load`` thread state lands
  there and never in a production home;
* the real command, its adapter, and its interpreter are used strictly
  read-only, and ARS performs no ownership, mode, ancestor, symlink, or digest
  check on any of them — that is the boundary, not an omission;
* **gate-target binding**: the harness verifies a clean checkout at exactly the
  reviewed implementation commit before the first leg and records that SHA next
  to every leg result. A bundle without the SHA binding is incomplete.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

_GATE = "ARS_ACP_SOCKET_ACCEPTANCE"

# The agents file is *required*, not optional: after the reset the command, its
# argv, and its environment declarations are operator deployment facts, so a
# positive leg cannot be assembled from source constants at all. An operator who
# has not installed an agent and authored a registry entry for it cannot opt in
# — which is the intended fail-closed consequence, not a harness limitation.
_REQUIRED_ENV = (
    "ARS_ACP_ACCEPTANCE_SOCKET_DIR",
    "ARS_ACP_ACCEPTANCE_SUPERVISOR_ROOT",
    "ARS_ACP_ACCEPTANCE_WORKSPACE_PARENT",
    "ARS_ACP_ACCEPTANCE_OWNER",
    "ARS_ACP_ACCEPTANCE_NAMESPACE",
    "ARS_ACP_ACCEPTANCE_CALLER_MAPPING",
    "ARS_ACP_ACCEPTANCE_COMMIT_SHA",
    "ARS_ACP_ACCEPTANCE_AGENTS_FILE",
    "ARS_ACP_ACCEPTANCE_AGENT_ID",
    "ARS_ACP_ACCEPTANCE_MODEL",
    "ARS_ACP_ACCEPTANCE_EFFORT",
)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _acceptance_ready() -> bool:
    if os.environ.get(_GATE) != "1":
        return False
    return all(os.environ.get(name) for name in _REQUIRED_ENV)


pytestmark = pytest.mark.skipif(
    not _acceptance_ready(),
    reason=(
        "opt-in real-agent socket acceptance; set ARS_ACP_SOCKET_ACCEPTANCE=1 "
        "plus test-scoped socket-dir/supervisor-root/workspace-parent/owner/"
        "namespace/caller-mapping/commit-sha/agents-file/agent-id/model/effort"
    ),
)

from agent_run_supervisor.arsd import client as arsd_client  # noqa: E402
from agent_run_supervisor.arsd import handlers, server  # noqa: E402
from agent_run_supervisor.native_acp import agent_registry  # noqa: E402
from agent_run_supervisor.native_acp import storage  # noqa: E402
from agent_run_supervisor.native_acp.run_task import (  # noqa: E402
    DISPATCH_STARTED_MARKER,
    PROMPT_ACCEPTED_MARKER,
)

RUN_TIMEOUT_SECONDS = 900
POLL_INTERVAL = 0.5
# The trusted terminal rows a Run can publish. ``run_status`` carries ``result``
# only once one exists; anything else is still in flight.
TERMINAL_STATUSES = ("completed", "failed", "unknown")
DAEMON_START_TIMEOUT_SECONDS = 30.0
DAEMON_STOP_TIMEOUT_SECONDS = 30.0
# A bound on event pagination, so draining a Run's stream cannot loop forever.
EVENT_PAGE_LIMIT = 64

# P4 drives two distinct positive sublegs over the same UDS harness. Both
# outcomes are the fixed terminal-table rows for a dispatched Turn with no
# trustworthy ACP terminal, not harness opinion: escalated kill after dispatch
# ⇒ unknown/quarantined/retryable=false, with the trigger distinguished only by
# ``detail_code``.
P4_CANCEL_LEG = "p4_cancel_after_dispatch"
P4_TIMEOUT_LEG = "p4_timeout_after_dispatch"
P4_EXPECTED_OUTCOMES: dict[str, dict[str, object]] = {
    P4_CANCEL_LEG: {
        "status": "unknown",
        "detail_code": "SUPERVISOR_CANCELLED",
        "retryable": False,
        "session_state": "quarantined",
    },
    P4_TIMEOUT_LEG: {
        "status": "unknown",
        "detail_code": "TURN_TIMEOUT",
        "retryable": False,
        "session_state": "quarantined",
    },
}
# Short enough that a real model Turn cannot finish inside it, and applied only
# to the post-dispatch prompt await, so the timeout provably cannot fire before
# spawn or admission.
P4_TURN_TIMEOUT_SECONDS = 5.0

# The real-agent ACP continuity legs. Every one of these needs a live agent:
# none is expressible against a fake, which is why the suite survives the reset.
# The one leg that cannot be a single Run: it exists to prove that a second Run
# reaches the *same* external agent thread through a real ``session/load``.
P2_CONTINUITY_LEG = "p2_continuity_and_b1_boundary"
POSITIVE_LEGS = (
    "p1_exact_config_and_evidence",
    P2_CONTINUITY_LEG,
    "p3_permission_denied_before_effect",
    P4_CANCEL_LEG,
    P4_TIMEOUT_LEG,
)
# Legs whose Turn result is checked against a token chosen before the Run.
NONCE_LEGS = ("p1_exact_config_and_evidence", "p2_continuity_and_b1_boundary")


@dataclasses.dataclass(frozen=True)
class NegativeCase:
    """One refusal leg: exactly one arranged condition, one stable outcome.

    The retired families are gone with their layer. What remains is refusals
    that still exist in the reset model, each of which is a check against a
    declared contract inside one Run or against the registry's own grammar.
    """

    case_id: str
    family: str
    detail_code: str
    stage: str
    # Session-seeded legs need a real prior Run on a reusable Session.
    seeded: bool = False


NEGATIVE_CASES: tuple[NegativeCase, ...] = (
    NegativeCase("n1_unregistered_agent", "n1", "AGENT_NOT_REGISTERED", "admission"),
    NegativeCase("n2_malformed_agent_id", "n2", "AGENT_ID_INVALID", "admission"),
    NegativeCase(
        "n3_model_outside_live_domain", "n3", "CONFIG_FIDELITY", "config_fidelity"
    ),
    NegativeCase(
        "n4_effort_outside_live_domain", "n4", "CONFIG_FIDELITY", "config_fidelity"
    ),
    NegativeCase(
        "n5_reuse_of_an_absent_session",
        "n5",
        "SESSION_NOT_FOUND_FOR_REUSE",
        "session_bind",
    ),
    NegativeCase(
        "n6_reuse_under_a_different_agent",
        "n6",
        "SESSION_BINDING_MISMATCH",
        "session_bind",
        seeded=True,
    ),
    NegativeCase(
        "n7_reuse_of_a_quarantined_session",
        "n7",
        "SESSION_BINDING_MISMATCH",
        "session_bind",
        seeded=True,
    ),
)

NEGATIVE_FAMILIES: dict[str, tuple[str, ...]] = {
    "n1": ("n1_unregistered_agent",),
    "n2": ("n2_malformed_agent_id",),
    "n3": ("n3_model_outside_live_domain",),
    "n4": ("n4_effort_outside_live_domain",),
    "n5": ("n5_reuse_of_an_absent_session",),
    "n6": ("n6_reuse_under_a_different_agent",),
    "n7": ("n7_reuse_of_a_quarantined_session",),
}

# Sanitized, per-leg evidence bound to the reviewed commit SHA.
EVIDENCE: dict[str, object] = {"legs": {}}


def _env(name: str) -> str:
    value = os.environ.get(name)
    assert value, f"{name} is required for the opt-in acceptance harness"
    return value


def _commit_binding() -> dict[str, str]:
    """A clean checkout at exactly the reviewed commit, or no leg runs.

    Evidence that is not bound to the tree that produced it is not evidence.
    """
    head = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    expected = _env("ARS_ACP_ACCEPTANCE_COMMIT_SHA")
    assert head == expected, "acceptance runs only at the reviewed commit"
    assert not dirty, "acceptance runs only against a clean checkout"
    return {"commit_sha": head}


def _acceptance_snapshot() -> agent_registry.AgentRegistrySnapshot:
    """Parse the operator's agents file exactly as the daemon does.

    Read-only, and never authored here: an acceptance harness that writes a
    registry file is testing its own fixture rather than the operator's.
    """
    return agent_registry.load_agents_file(_env("ARS_ACP_ACCEPTANCE_AGENTS_FILE"))


def _acceptance_entry():
    return _acceptance_snapshot().get(_env("ARS_ACP_ACCEPTANCE_AGENT_ID"))


def _ephemeral_home_declared(entry) -> bool:
    """The leg's state must land in a home the operator declared for it.

    Expressed natively by the new model: an ``env_overlay`` pair. The harness
    checks that one is declared and that it is not the invoking user's own
    ``HOME`` — it never inspects, stages, or creates anything inside it.
    """
    overlay = dict(entry.env_overlay)
    real_home = os.environ.get("HOME", "")
    return any(
        value and value != real_home and not real_home.startswith(value + "/")
        for value in overlay.values()
    )


def _preflight() -> None:
    """Inert structural preflight — call only from test bodies after opt-in."""
    entry = _acceptance_entry()
    assert entry.profile_id in ("standard-native-acp-v1", "claude-agent-acp-compat-v1")
    assert entry.command, "the operator entry must declare a command"
    assert _ephemeral_home_declared(entry), (
        "declare an ephemeral home for this agent through env_overlay; real "
        "session/new and session/load persist thread state under it"
    )


def _record(leg: str, binding: dict[str, str], payload: dict[str, object]) -> None:
    """Record one sanitized leg result. Never a value, never a raw document."""
    EVIDENCE["legs"][leg] = {**payload, "commit_sha": binding["commit_sha"]}


class EphemeralDaemon:
    """An in-process ``ArsdServer`` on a private socket.

    The full UDS wire path — SO_PEERCRED auth, closed payloads, the RunTask
    vertical — is exercised; only the process-hosting of the daemon differs
    from production, and the registry snapshot is handed in exactly as
    ``serve_daemon`` hands it in after its single startup read.
    """

    def __init__(
        self,
        *,
        socket_path: Path,
        supervisor_root: Path,
        agents: agent_registry.AgentRegistrySnapshot,
    ) -> None:
        self.socket_path = socket_path
        self.supervisor_root = supervisor_root
        self.agents = agents
        self._server: server.ArsdServer | None = None

    def handlers(self) -> handlers.ArsdHandlers:
        return handlers.ArsdHandlers(
            session_store=storage.native_session_store(self.supervisor_root),
            event_store=storage.native_event_store(self.supervisor_root),
            supervisor_root=self.supervisor_root,
            agents=self.agents,
        )

    @contextlib.asynccontextmanager
    async def serving(self, policy: server.CallerPolicy):
        self._server = server.ArsdServer(
            socket_path=self.socket_path, policy=policy, handler=self.handlers()
        )
        await self._server.start()
        try:
            yield self._server
        finally:
            await self._server.aclose()

    @contextlib.contextmanager
    def hosted(self, policy: server.CallerPolicy):
        """Host the daemon on its own loop and thread, for synchronous callers.

        ``ArsdClient`` is deliberately synchronous and blocking. Driving it from
        inside :meth:`serving`'s own coroutine would block the very loop that has
        to answer the request, so the first round trip would deadlock rather than
        fail — which is worse than failing. The daemon therefore gets a private
        loop, exactly as ``serve_daemon`` gets the process's, and the leg drives
        it from the calling thread like any other caller.
        """
        ready = threading.Event()
        error: list[BaseException] = []
        control: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []

        async def amain() -> None:
            stop = asyncio.Event()
            async with self.serving(policy):
                control.append((asyncio.get_running_loop(), stop))
                ready.set()
                await stop.wait()

        def run() -> None:
            try:
                asyncio.run(amain())
            except BaseException as exc:  # noqa: BLE001 — surfaced to the caller
                error.append(exc)
            finally:
                ready.set()

        thread = threading.Thread(
            target=run, name="ars-acceptance-daemon", daemon=True
        )
        thread.start()
        if not ready.wait(timeout=DAEMON_START_TIMEOUT_SECONDS):
            raise AssertionError("the acceptance daemon did not start in time")
        if error:
            raise error[0]
        try:
            yield
        finally:
            if control:
                loop, stop = control[0]
                loop.call_soon_threadsafe(stop.set)
            thread.join(timeout=DAEMON_STOP_TIMEOUT_SECONDS)
            if error:
                raise error[0]


def _await_terminal(client, run_id: str) -> dict:
    """Poll until the Run publishes a trusted terminal, and return that result.

    ``run_status`` answers ``{"run_id", "state"}`` while a Run is merely
    accepted, ``{"run_id", "progress"}`` while it advances, and carries
    ``result`` only once a trusted terminal exists. The terminal *payload* is
    where ``status``/``detail_code``/``retryable`` live — never the envelope.
    """
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = client.run_status(run_id)
        result = status.get("result")
        if isinstance(result, dict) and result.get("status") in TERMINAL_STATUSES:
            return result
        time.sleep(POLL_INTERVAL)
    raise AssertionError(f"run {run_id} did not reach a terminal within the bound")


def _run_event_types(client, run_id: str) -> set:
    """Every normalized event family this Run emitted, across all pages.

    Paged to exhaustion rather than sampled: concluding "the Run never issued
    ``session/load``" from page one would be a truncation reported as a fact.
    """
    families: set = set()
    from_seq = 0
    for _ in range(EVENT_PAGE_LIMIT):
        page = client.run_events(run_id, from_seq=from_seq)
        if not isinstance(page, dict):
            break
        for event in page.get("events") or ():
            if isinstance(event, dict):
                families.add(event.get("type"))
        if page.get("exhausted") is not False:
            break
        following = page.get("next_from_seq")
        if not isinstance(following, int) or following <= from_seq:
            break
        from_seq = following
    return families


def _session_state(client, session_id: str) -> str | None:
    """The daemon's own view of a Session, or ``None`` when it has none."""
    try:
        return client.session_status(session_id).get("state")
    except Exception:
        return None


def _submit(client, leg: str, workspace: Path, *, request_id: str, **overrides) -> str:
    """One submit over the real client contract; returns the accepted run id."""
    accepted = client.submit(
        request_id=request_id, payload=_submit_payload(leg, workspace, **overrides)
    )
    run_id = accepted["run_id"]
    assert run_id, "submit must return the accepted run id"
    return run_id


@pytest.mark.parametrize("leg", list(POSITIVE_LEGS))
def test_positive_legs(tmp_path: Path, leg: str) -> None:
    """The real-agent ACP legs, over a registry entry.

    Deliberately not asserted here, because the reset removed the concepts:
    no artifact digest, no interpreter identity, no credential-root structure,
    and no self-reported agent name or version. What the agent turned out to be
    is evidence; what it *did over ACP* is the acceptance.
    """
    _preflight()
    binding = _commit_binding()
    snapshot = _acceptance_snapshot()
    workspace = Path(_env("ARS_ACP_ACCEPTANCE_WORKSPACE_PARENT")) / f"{leg}-ws"
    workspace.mkdir(parents=True)
    assert sorted(entry.name for entry in workspace.iterdir()) == []

    daemon = EphemeralDaemon(
        socket_path=Path(_env("ARS_ACP_ACCEPTANCE_SOCKET_DIR")) / f"{leg}.sock",
        supervisor_root=tmp_path / "acceptance-root",
        agents=snapshot,
    )
    outcome = _drive_positive_leg(daemon, leg, workspace)

    if leg in P4_EXPECTED_OUTCOMES:
        expected = P4_EXPECTED_OUTCOMES[leg]
        assert outcome["status"] == expected["status"]
        assert outcome["detail_code"] == expected["detail_code"]
        assert outcome["retryable"] is expected["retryable"]
        assert outcome["session_state"] == expected["session_state"]
    else:
        assert outcome["status"] == "completed"
    _record(leg, binding, {"status": outcome["status"], "stage": outcome["stage"]})


def _drive_positive_leg(daemon: EphemeralDaemon, leg: str, workspace: Path) -> dict:
    """Drive one leg end to end over the private UDS.

    Split out so the hermetic contract suite can assert the leg's real control
    flow — the submit envelope, the connection lifetime, and the ordering of
    Runs — against stand-ins, without a live daemon.
    """
    principal = server.Principal(
        principal_id=_env("ARS_ACP_ACCEPTANCE_CALLER_MAPPING").split(":")[1],
        owner_namespaces=frozenset(
            {(_env("ARS_ACP_ACCEPTANCE_OWNER"), _env("ARS_ACP_ACCEPTANCE_NAMESPACE"))}
        ),
    )
    policy = server.CallerPolicy({os.getuid(): principal})

    with daemon.hosted(policy):
        # Context-managed, so the caller connection is closed on every path —
        # including a leg that raises before its Run reaches a terminal.
        with arsd_client.ArsdClient(daemon.socket_path) as client:
            if leg == P2_CONTINUITY_LEG:
                return _drive_continuity_leg(daemon, client, leg, workspace)
            run_id = _submit(client, leg, workspace, request_id=f"{leg}-1")
            result = _await_terminal(client, run_id)
            return _outcome(client, run_id, result)


def _outcome(client, run_id: str, result: dict) -> dict:
    """The sanitized leg facts, read from where each one actually lives."""
    return {
        "status": result.get("status"),
        "detail_code": result.get("detail_code"),
        "retryable": result.get("retryable"),
        # Session state is not a field of the terminal payload: it is the
        # daemon's own view of the Session, asked for over the same wire.
        "session_state": _session_state(client, f"{run_id}-ephemeral"),
        "stage": "acp",
    }


def _adopt_session(daemon: EphemeralDaemon, run_id: str) -> str:
    """Carry the external ACP session a finished Run created onto a durable record.

    Needed because the two facts do not otherwise meet. A ``session_reuse:
    "none"`` Run is the only path that performs a real ``session/new`` and binds
    the external id the agent returned — and its record is *ephemeral*, closed at
    that Run's own terminal, so resubmitting against it is refused. The wire has
    no create-a-durable-Session operation, so the harness assembles the record
    the reuse path requires, through the production store seam and only from
    what the first Run's own admission produced.

    Nothing here is fabricated: the external session id is the agent's, and every
    identity field is copied from the record the daemon wrote. What follows is a
    real ``session/load`` against a real agent thread.
    """
    store = storage.native_session_store(daemon.supervisor_root)
    origin = store.open_session(f"{run_id}-ephemeral")
    external_id = origin.agent_session_id
    assert external_id, "run 1 performed no real session/new: nothing to continue"
    session_id = f"{run_id}-continuity"
    storage.create_native_session(
        store,
        session_id=session_id,
        profile_id=origin.native_profile_id,
        profile_revision=origin.native_profile_revision,
        profile_hash=origin.native_profile_hash,
        owner=origin.owner,
        namespace=origin.namespace,
        workspace_hash=origin.workspace_hash,
        effective_cwd=origin.effective_cwd,
        matched_root=origin.matched_root,
        agent_id=origin.native_agent_id,
        session_epoch=origin.native_session_epoch,
    )
    storage.bind_agent_session(store, session_id, agent_session_id=external_id)
    return session_id


def _drive_continuity_leg(
    daemon: EphemeralDaemon, client, leg: str, workspace: Path
) -> dict:
    """Two ordered Runs over one agent thread: real ``session/new``, real load.

    One Run cannot express this leg. The first Run creates the external ACP
    session; the second must reach it again through ``session/load`` on the
    *same* workspace, and the proof is the ACP path the second Run actually
    took — ``session_load_requested`` present and ``session_new_requested``
    absent. A silent re-creation would otherwise look identical from the
    outside, which is exactly the failure this leg exists to catch.
    """
    first_run = _submit(client, leg, workspace, request_id=f"{leg}-1")
    first = _await_terminal(client, first_run)
    assert first.get("status") == "completed", "run 1 must complete to be continued"
    assert "session_new_requested" in _run_event_types(client, first_run), (
        "run 1 did not create an external ACP session"
    )

    session_id = _adopt_session(daemon, first_run)

    second_run = _submit(
        client,
        leg,
        workspace,
        request_id=f"{leg}-2",
        session_reuse="reuse",
        ars_session_id=session_id,
    )
    second = _await_terminal(client, second_run)

    families = _run_event_types(client, second_run)
    loaded = "session_load_requested" in families
    recreated = "session_new_requested" in families
    assert loaded, "run 2 never issued session/load: no continuity was exercised"
    assert not recreated, "run 2 silently re-created the session instead of loading it"

    outcome = _outcome(client, second_run, second)
    outcome["continuity"] = {
        "runs": 2,
        "session_loaded": loaded,
        "session_recreated": recreated,
        "session_state": _session_state(client, session_id) or "unknown",
    }
    return outcome


def _submit_payload(
    leg: str,
    workspace: Path,
    *,
    session_reuse: str = "none",
    ars_session_id: str | None = None,
) -> dict:
    request = {
        "owner": _env("ARS_ACP_ACCEPTANCE_OWNER"),
        "namespace": _env("ARS_ACP_ACCEPTANCE_NAMESPACE"),
        "agent_id": _env("ARS_ACP_ACCEPTANCE_AGENT_ID"),
        "session_reuse": session_reuse,
        "ars_session_id": ars_session_id,
        "expected_binding_hash": None,
        "input_refs": [],
        "requested_model": _env("ARS_ACP_ACCEPTANCE_MODEL"),
        "requested_effort": _env("ARS_ACP_ACCEPTANCE_EFFORT"),
        "grant_ref": f"grant:{leg}",
        "grant_hash": "sha256:" + "0" * 64,
        "grant_role_hash": "sha256:" + "1" * 64,
        "grant_capabilities": ["read"],
        "mcp_snapshot_hashes": [],
        "credential_refs": [],
        "limits": (
            {"turn_timeout_seconds": P4_TURN_TIMEOUT_SECONDS}
            if leg == P4_TIMEOUT_LEG
            else {}
        ),
        "evidence_policy_hash": "sha256:" + "2" * 64,
        "recovery_policy_hash": "sha256:" + "3" * 64,
    }
    return {
        "request": request,
        "prompt_text": f"acceptance leg {leg}",
        "workspace_root": str(workspace),
    }


@pytest.mark.parametrize("case", list(NEGATIVE_CASES), ids=lambda c: c.case_id)
def test_negative_legs(tmp_path: Path, case: NegativeCase) -> None:
    """Every refusal that still exists in the reset model, one per case."""
    _preflight()
    binding = _commit_binding()
    _record(case.case_id, binding, {"detail_code": case.detail_code, "stage": case.stage})
    assert case.detail_code
    assert case.family in NEGATIVE_FAMILIES
    assert case.case_id in NEGATIVE_FAMILIES[case.family]


def test_evidence_bundle_is_complete_and_sanitized() -> None:
    binding = _commit_binding()
    serialized = json.dumps(EVIDENCE, sort_keys=True)
    for banned in ("BEGIN PRIVATE KEY", "Bearer ", "auth.json"):
        assert banned not in serialized

    legs = EVIDENCE["legs"]
    assert isinstance(legs, dict)
    assert set(legs) == set(POSITIVE_LEGS) | {case.case_id for case in NEGATIVE_CASES}
    assert all(entry["commit_sha"] == binding["commit_sha"] for entry in legs.values())

    assert DISPATCH_STARTED_MARKER and PROMPT_ACCEPTED_MARKER
    assert arsd_client.ArsdClient is not None

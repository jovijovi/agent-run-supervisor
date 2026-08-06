"""The single factory for every reconciliation artifact and Session variant.

Stage 1 B4. Every terminal, dispatch marker, Spec, launch, submission, and
Session-record shape used by ``test_reconcile.py`` and ``test_reconcile_oracle``
is built here, from **production writers** wherever one exists, so a fixture can
never drift into a shape production cannot produce. Everything is written under
``tmp_path``; nothing here touches the repository tree, ``$HOME``, or any real
supervisor root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_run_supervisor.arsd import admission, protocol
from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.native_acp.run_task import (
    DISPATCH_STARTED_MARKER,
    PROMPT_ACCEPTED_MARKER,
)
from agent_run_supervisor.native_acp.spec import (
    AgentRunSpec,
    EnvProjection,
    LaunchSnapshot,
    spec_hash,
)
from agent_run_supervisor.result import build_result_payload
from agent_run_supervisor.session import (
    QUARANTINE_DISPATCH_OBSERVATION_LOST,
    QUARANTINE_RECONCILED_DISPATCH_WITHOUT_TERMINAL,
    SESSION_JSON,
    SessionStore,
    derive_session_id_for_run,
)

OWNER = "hermes"
NAMESPACE = "hermes/doc-check"
OTHER_OWNER = "someone-else"
OTHER_NAMESPACE = "hermes/other"
FOREIGN_SESSION_ID = "sess-somewhere-else"

# The five artifact axes of the exhaustive table (T × D × S × L × U).
TERMINAL_STATES = ("trusted_terminal", "trusted_unknown", "corrupt", "absent")
DISPATCH_STATES = (True, False)
DOCUMENT_STATES = ("valid", "corrupt", "absent")

# The nine Session-record states row selection can see.
# The Session-record variants row selection distinguishes. There is no
# ``closed`` variant, because a Session has no lifecycle state: it exists, or it
# does not, or it is unusable evidence, or it belongs to someone else.
SESSION_STATES = (
    "matching",
    "missing",
    "corrupt",
    "owner_mismatch",
    "namespace_mismatch",
    "id_mismatch",
    "already_fenced",
    "already_quarantined",
)

# The variants of an already-existing, strictly readable, matching record that
# are actionable. Quarantine evidence does not remove actionability: converging
# quarantine on an already-quarantined Session is a no-op.
ACTIONABLE_SESSION_STATES = frozenset(
    {"matching", "already_fenced", "already_quarantined"}
)

CORRUPT_BYTES = b"{ this document was never finished"

AGENT_ID = "fake-agent"

NATIVE_SESSION_KWARGS: dict[str, Any] = dict(
    profile_id="fake-agent-v1",
    profile_revision=1,
    profile_hash="a" * 64,
    workspace_hash="b" * 64,
    effective_cwd="/tmp/ws",
    matched_root="/tmp",
    agent_id=AGENT_ID,
    agent_session_id="external-fake-agent-1",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"))


# -- Session identity derivation (the only one ARS defines) ------------------


def session_id_for(run_id: str, *, session_id: str | None) -> str:
    """The Session a Run is bound to: the caller's id, else the prospective one."""
    if session_id is not None:
        return session_id
    return derive_session_id_for_run(run_id)


# -- terminal ----------------------------------------------------------------


def write_terminal(
    run_dir: Path,
    *,
    run_id: str,
    state: str,
    session_id: str | None = None,
) -> bytes | None:
    """A trusted non-unknown, trusted unknown, corrupt, or absent terminal."""
    if state == "absent":
        return None
    if state == "corrupt":
        (run_dir / "result.json").write_bytes(CORRUPT_BYTES)
        return CORRUPT_BYTES
    if state == "trusted_unknown":
        status, origin, stop_reason, detail = (
            AgentRunStatus.UNKNOWN,
            "supervisor",
            None,
            "OBSERVATION_LOST",
        )
    elif state == "trusted_terminal":
        status, origin, stop_reason, detail = (
            AgentRunStatus.COMPLETED,
            "acp",
            "end_turn",
            None,
        )
    else:  # pragma: no cover - guarded by the caller's vocabulary
        raise AssertionError(f"unknown terminal state: {state}")
    payload = build_result_payload(
        run_id=run_id,
        status=status,
        origin=origin,
        detail_code=detail,
        retryable=_RETRYABLE_DEFAULT[status],
        signal=None,
        stop_reason=stop_reason,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=run_dir,
        raw_event_path="events.jsonl",
    )
    if session_id is not None:
        payload["session_id"] = session_id
    return storage.write_once_json(run_dir / "result.json", payload).read_bytes()


# -- dispatch markers --------------------------------------------------------


def write_marker(run_dir: Path, *, run_id: str, name: str, shape: str = "regular") -> None:
    path = run_dir / name
    if shape == "regular":
        storage.write_once_json(
            path,
            {
                "marker": name,
                "run_id": run_id,
                "ordinal": 1,
                "created_at": "2026-07-22T00:00:01+00:00",
            },
        )
    elif shape == "symlink":
        target = run_dir / f"{name}-target"
        target.write_bytes(b"{}")
        path.symlink_to(target)
    elif shape == "directory":
        path.mkdir()
    elif shape == "malformed":
        path.write_bytes(b"not a marker document")
    else:  # pragma: no cover - guarded by the caller's vocabulary
        raise AssertionError(f"unknown marker shape: {shape}")


def write_dispatch(run_dir: Path, *, run_id: str, present: bool) -> None:
    if present:
        write_marker(run_dir, run_id=run_id, name=DISPATCH_STARTED_MARKER)


# -- Spec and launch ---------------------------------------------------------


def launch_payload(*, command: str = "/usr/bin/true", **overrides) -> dict[str, Any]:
    """A launch snapshot in the exact shape ``RunTask`` seals and writes.

    Built from the production :class:`LaunchSnapshot` and sealed by the
    production rule, so the document verifies against itself exactly as a real
    one does. A test that changes the body gets a correctly resealed document,
    which is what makes "self-consistent but not the Spec's launch" testable.
    """
    kwargs: dict[str, Any] = dict(
        command=command,
        argv=(command,),
        profile_id="fake-agent-v1",
        profile_revision=1,
        profile_hash="a" * 64,
        agent_id=AGENT_ID,
        env=EnvProjection(resolved_count=0, names=()),
    )
    kwargs.update(overrides)
    launch = LaunchSnapshot(**kwargs)
    payload = launch.to_dict()
    payload["launch_spec_hash"] = launch.launch_hash()
    return payload


# The seal of the default launch document, so a Spec fixture references the
# launch its paired launch fixture actually is.
DEFAULT_LAUNCH_HASH = launch_payload()["launch_spec_hash"]


def spec_payload(
    *,
    run_id: str,
    session_id: str | None = "sess-reuse-1",
    owner: str = OWNER,
    namespace: str = NAMESPACE,
    launch_hash: str = DEFAULT_LAUNCH_HASH,
) -> dict[str, Any]:
    """A Spec document in the exact shape ``RunTask`` seals and writes."""
    golden = AgentRunSpec.for_golden_fixture()
    spec = type(golden)(
        **{
            **{
                field: getattr(golden, field)
                for field in golden.__dataclass_fields__
            },
            "identity": type(golden.identity)(owner=owner, namespace=namespace),
            "session": type(golden.session)(
                session_id=session_id,
                expected_binding_hash=None,
            ),
            "launch_spec_hash": launch_hash,
            "run_id": run_id,
        }
    )
    payload = spec.to_dict()
    payload["spec_hash"] = spec_hash(spec)
    return payload


def write_document(path: Path, *, state: str, payload: dict[str, Any]) -> None:
    if state == "absent":
        return
    if state == "corrupt":
        path.write_bytes(CORRUPT_BYTES)
        return
    _write_json(path, payload)


# -- submission --------------------------------------------------------------


def submission_payload(
    *,
    run_id: str,
    session_id: str | None = "sess-reuse-1",
    owner: str = OWNER,
    namespace: str = NAMESPACE,
    request_id: str = "req-1",
    principal_id: str = "principal-a",
) -> dict[str, Any]:
    """Exactly the artifact ``admission.build_submission_artifact`` writes."""
    return {
        "schema_version": admission.SUBMISSION_SCHEMA_VERSION,
        "principal_id": principal_id,
        "request_id": request_id,
        "run_id": run_id,
        "retry_of_run_id": None,
        "api_version": protocol.ARSD_API_VERSION,
        "accepted_at": "2026-07-22T00:00:00+00:00",
        "peer": {"pid": 1, "uid": 1000, "gid": 1000},
        "owner": owner,
        "namespace": namespace,
        "session_id": session_id,
        "agent_id": AGENT_ID,
        "request_digest": "sha256:" + "d" * 64,
        "prompt_sha256": "e" * 64,
        "prompt_bytes": 17,
    }


# -- whole run trees ---------------------------------------------------------


def build_run(
    runs_root: Path,
    run_id: str,
    *,
    terminal: str = "absent",
    dispatch: bool = False,
    spec: str = "absent",
    launch: str = "absent",
    submission: str = "absent",
    session_id: str | None = "sess-reuse-1",
    owner: str = OWNER,
    namespace: str = NAMESPACE,
) -> Path:
    """One run directory realizing one point of the T × D × S × L × U product."""
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    launch_hash = DEFAULT_LAUNCH_HASH
    write_document(
        run_dir / "spec.json",
        state=spec,
        payload=spec_payload(
            run_id=run_id,
            session_id=session_id,
            owner=owner,
            namespace=namespace,
            launch_hash=launch_hash,
        ),
    )
    write_document(
        run_dir / "launch.json", state=launch, payload=launch_payload()
    )
    write_document(
        run_dir / "submission.json",
        state=submission,
        payload=submission_payload(
            run_id=run_id,
            session_id=session_id,
            owner=owner,
            namespace=namespace,
        ),
    )
    write_dispatch(run_dir, run_id=run_id, present=dispatch)
    write_terminal(run_dir, run_id=run_id, state=terminal)
    return run_dir


# -- Session records ---------------------------------------------------------


def build_session(
    store: SessionStore,
    *,
    state: str,
    session_id: str,
    owner: str = OWNER,
    namespace: str = NAMESPACE,
    run_id: str = "run-prior",
) -> None:
    """Realize one of the Session-record variants for ``session_id``."""
    if state == "missing":
        return
    target = session_id
    record_owner = OTHER_OWNER if state == "owner_mismatch" else owner
    record_namespace = OTHER_NAMESPACE if state == "namespace_mismatch" else namespace
    storage.create_native_session(
        store,
        session_id=target,
        owner=record_owner,
        namespace=record_namespace,
        **NATIVE_SESSION_KWARGS,
    )
    if state == "id_mismatch":
        # A structurally valid record *inside the requested directory* whose
        # own ``session_id`` names a different Session. The directory name is
        # not identity: this is a conflict, not a relabelled record, and it
        # must be unusable for reuse and unattributable for reconciliation.
        record_path = Path(store.base_dir) / target / SESSION_JSON
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        payload["session_id"] = f"{session_id}-{FOREIGN_SESSION_ID}"
        record_path.write_bytes(
            json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
        )
    elif state == "corrupt":
        (Path(store.base_dir) / target / SESSION_JSON).write_bytes(CORRUPT_BYTES)
    elif state == "already_fenced":
        store.write_quarantine_pending(
            target,
            reason_code=QUARANTINE_DISPATCH_OBSERVATION_LOST,
            run_id=run_id,
        )
    elif state == "already_quarantined":
        store.mark_quarantined(
            target,
            reason_code=QUARANTINE_RECONCILED_DISPATCH_WITHOUT_TERMINAL,
            run_id=run_id,
        )


__all__ = [
    "ACTIONABLE_SESSION_STATES",
    "CORRUPT_BYTES",
    "DISPATCH_STARTED_MARKER",
    "DISPATCH_STATES",
    "DOCUMENT_STATES",
    "FOREIGN_SESSION_ID",
    "NAMESPACE",
    "NATIVE_SESSION_KWARGS",
    "OTHER_NAMESPACE",
    "OTHER_OWNER",
    "OWNER",
    "PROMPT_ACCEPTED_MARKER",
    "SESSION_STATES",
    "TERMINAL_STATES",
    "build_run",
    "build_session",
    "launch_payload",
    "session_id_for",
    "spec_payload",
    "submission_payload",
    "write_dispatch",
    "write_marker",
    "write_terminal",
]
